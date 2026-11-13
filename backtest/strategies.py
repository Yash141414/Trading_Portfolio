"""
The three intraday strategies. Each decides on a bar's close, trades the nearest
weekly expiry at the next bar's open, is flat by 15:15, and trades every day.

DEVELOPMENT / TEST: the current rules (2026-10-02 redesign) were selected on Dhan data for
2023-01-02 -> 2025-09-29 only, to make all three strategies profitable in every year and both
halves and reach a combined Calmar >= 5 there (research/rule_search.py, score_rules.py,
weight_search.py). 2025-09-30 -> 2026-09-25 is the test year. It is not perfectly clean: the
previous rules (kept as *Fit2026) were fitted on it, so it had been looked at before.

Each strategy is a set of rules, one per day type, because what paid differed
sharply by days to expiry:
  far    3+ days to expiry: little edge in anything; small mandatory trade
  dte1   the day before expiry: fading the move since the previous close paid well
  exp    expiry day: fast decay; fades and credit spreads paid

A rule is: take a view at `sig_t` from either spot's move since a reference
(`ref`: "open" = the day's first bar, "pc" = the previous session's close) or, with
`ref="pcr"`, the put/call open-interest ratio over the 11 strikes around ATM
(>= 1 -> up). `mode` fade trades against that view, follow trades with it; express
it with `kind`; exit at `exit_t`, at a spot stop beyond the morning range (`stop`,
long-premium kinds only), or at 15:15. Data without OI (e.g. synthetic) makes
"pcr" fall back to the move since the open.

  kinds    long        buy the ATM option in the view's direction
           long_otm1   buy the option one strike OTM in the view's direction
           debitW      buy ATM, sell W strikes further out (same type)
           creditW     sell ATM on the side against the view, buy W strikes further out
           otmcreditW  sell 2 strikes OTM against the view, buy W strikes beyond that

Sizing: lots so the worst-case loss (premium paid, or spread width minus credit,
plus round-trip slippage) is `risk_per_trade` x the rule's `risk` multiplier of the
sleeve's equity, with a 1-lot floor while one lot's worst case is under 5% of equity.
"""
from dataclasses import dataclass
from datetime import time

import numpy as np

from .config import Config
from .data import Day
from .engine import Leg, Position, Session, Trade, lots_for, slip

MAX_PREMIUM_FRAC = 0.20   # never spend more than 20% of sleeve equity on premium in one trade
MAX_ONE_LOT_RISK = 0.05   # the 1-lot floor applies only if one lot's worst case is under 5% of equity


@dataclass(frozen=True)
class Rule:
    """One trading rule for a day type; see the module docstring for what each field means."""
    ref: str            # "open" (day's first bar), "pc" (previous close) or "pcr" (put/call OI ratio)
    mode: str           # "fade" or "follow"
    sig_t: time         # decide on the close of the bar before this time, fill at its open
    kind: str
    exit_t: time
    stop: float = 0.0   # 0 = no spot stop; m = stop at the morning range's far edge +- m x range
    risk: float = 1.0   # multiplier on cfg.risk_per_trade


def day_type(day: Day) -> str:
    """'exp' on expiry day, 'dte1' the day before, 'far' when expiry is 3+ days away."""
    return "exp" if day.dte == 0 else ("dte1" if day.dte == 1 else "far")


def legs_for(kind: str, sign: int, k: float, step: float) -> list[Leg]:
    """Legs expressing a view `sign` (+1 up, -1 down) around ATM strike k."""
    up, dn = ("CE", "PE") if sign > 0 else ("PE", "CE")
    if kind == "long":
        return [Leg(up, k, +1)]
    if kind == "long_otm1":
        return [Leg(up, k + sign * step, +1)]
    if kind.startswith("debit"):
        w = int(kind[5:])
        return [Leg(up, k, +1), Leg(up, k + sign * w * step, -1)]
    if kind.startswith("otmcredit"):
        w = int(kind[9:])
        return [Leg(dn, k - sign * 2 * step, -1), Leg(dn, k - sign * (2 + w) * step, +1)]
    if kind.startswith("credit"):
        w = int(kind[6:])
        return [Leg(dn, k, -1), Leg(dn, k - sign * w * step, +1)]
    raise ValueError(f"unknown kind {kind!r}")


def worst_case(day: Day, i: int, legs: list[Leg], cfg: Config) -> tuple[float, float]:
    """(worst-case loss per unit incl. round-trip slippage, premium paid per unit) at bar i."""
    px = [day.px(i, l.opt, l.strike) for l in legs]
    if not all(np.isfinite(px)):
        return np.nan, np.nan
    net = sum(l.side * p for l, p in zip(legs, px))            # + = debit paid
    slippage = sum(2 * slip(p, cfg.costs) for p in px)
    if net > 0:
        return net + slippage, net
    width = max(abs(a.strike - b.strike) for a in legs for b in legs if a.opt == b.opt) if len(legs) > 1 else np.inf
    return width + net + slippage, 0.0


def size(day: Day, max_loss: float, debit: float, equity: float, cfg: Config, risk: float) -> int:
    """Lots so the worst case loses risk x cfg.risk_per_trade of equity; premium and max_lots capped, 1-lot floor."""
    if not np.isfinite(max_loss) or max_loss <= 0:
        return 0
    lots = int(min(cfg.max_lots, risk * cfg.risk_per_trade * equity // (max_loss * day.lot)))
    if debit > 0:
        lots = min(lots, int(MAX_PREMIUM_FRAC * equity // (debit * day.lot)))
    if lots == 0 and max_loss * day.lot <= MAX_ONE_LOT_RISK * equity:
        lots = 1
    return lots


def rules_of(spec) -> tuple[Rule, ...]:
    """A day type's spec is one Rule or a tuple of Rules (tranches)."""
    return spec if isinstance(spec, tuple) else (spec,)


class RuleStrategy:
    """Runs the rule(s) for each day type. A tuple of rules means tranches: each is an
    independent position (entered at its own signal time, sized with its own risk)."""
    name = "Rule strategy"
    rules: dict[str, Rule | tuple[Rule, ...]] = {}

    def __init__(self, **overrides):
        self.rules = {**type(self).rules, **overrides}
        self.prev_close = None

    def run_day(self, day: Day, equity: float, cfg: Config) -> list[Trade]:
        """Run this day type's rule(s) and return the day's trades (one per tranche)."""
        prev, self.prev_close = self.prev_close, day.spot[-1]
        trades = []
        for r in rules_of(self.rules[day_type(day)]):
            trades += self._run_rule(day, r, prev, equity, cfg)
        return trades

    def _run_rule(self, day: Day, r: Rule, prev, equity: float, cfg: Config) -> list[Trade]:
        s = Session.of(day, cfg)
        i = day.bar_at(r.sig_t) - 1
        if i < 0 or i >= s.last_entry:      # i == 0 is fine: some days' data starts a few minutes late
            return []
        move = view(day, i, r.ref, prev, cfg)
        sign = move if r.mode == "follow" else -move
        return simulate(day, i, sign, r.kind, r.exit_t, r.stop, r.risk, equity, cfg, self.name)


def view(day: Day, i: int, ref: str, prev, cfg: Config) -> int:
    """+1 (up) / -1 (down) at bar i's close from `ref`: the move since the open ("open"), since the
    previous close ("pc"), or the put/call OI ratio around ATM ("pcr", falls back to "open" without OI)."""
    if ref == "pcr":
        p = day.pcr(i, day.strike(i, "CE", 0, cfg.strike_step))
        if np.isfinite(p):
            return 1 if p >= 1 else -1          # more put OI than call OI near the money -> bullish
    base = day.spot[0] if ref in ("open", "pcr") or prev is None else prev
    return 1 if day.spot[i] >= base else -1


def simulate(day: Day, i: int, sign: int, kind: str, exit_t, stop: float, risk: float, equity: float,
             cfg: Config, name: str) -> list[Trade]:
    """Open `kind` in direction `sign` at bar i+1's open, sized off `equity`, and close it at `exit_t`,
    the spot stop, or 15:15. Returns [] if it can't be filled."""
    s = Session.of(day, cfg)
    step = cfg.strike_step
    k = day.strike(i, "CE", 0, step)
    legs = legs_for(kind, sign, k, step)
    max_loss, debit = worst_case(day, i, legs, cfg)
    lots = size(day, max_loss, debit, equity, cfg, risk)
    pos = Position(day, legs, lots, i + 1, cfg, name, "long" if sign > 0 else "short")
    if not pos.valid:
        return []
    i_exit = min(day.bar_at(exit_t), s.square_off)
    if stop > 0 and debit > 0:
        hi, lo = day.spot[:i + 1].max(), day.spot[:i + 1].min()
        rng = max(hi - lo, 1.0)
        level = lo - stop * rng if sign > 0 else hi + stop * rng
        for j in range(pos.i_entry, i_exit):
            if sign * (day.spot[j] - level) <= 0:
                return [pos.close(min(j + 1, i_exit), "stop")]
    return [pos.close(i_exit, "square-off" if i_exit == s.square_off else "time exit")]

T = time


class MeanReversion(RuleStrategy):
    """Fade the move since the previous close: bet that it partly reverses near expiry.

    Designed 2026-10-02 on 2023-01-02 -> 2025-09-29 only (research/, rule set research/specs/v2.json).
    Every tranche's signal time was profitable in each of 2023, 2024 and 2025 on its own; risk multipliers
    were chosen by a neighbour-averaged Calmar search. Far days have no robust edge, so their trade is small.

    far   10:00, fade the move since the open, buy ATM, exit 12:00, quarter risk
    dte1  10:45 / 13:00 tranches: fade the move since the previous close, buy 1 strike OTM, stop 1x morning
          range beyond it, hold to 15:15, 1.5x risk each
    exp   09:30 / 10:30 tranches: fade the move since the previous close, buy ATM, exit 12:00, 1x risk each
    """
    name = "Mean Reversion"
    rules = {
        "far": Rule("open", "fade", T(10, 0), "long", T(12, 0), 0.0, 0.25),
        "dte1": tuple(Rule("pc", "fade", t, "long_otm1", T(15, 15), 1.0, 1.5) for t in (T(10, 45), T(13, 0))),
        "exp": tuple(Rule("pc", "fade", t, "long", T(12, 0), 0.0, 1.0) for t in (T(9, 30), T(10, 30))),
    }


class Directional(RuleStrategy):
    """Take the direction option writers are positioned for: the put/call open-interest ratio over the 11
    strikes around ATM (>= 1 -> bullish, else bearish). Far days follow the move since the previous close.

    Designed 2026-10-02 on 2023-01-02 -> 2025-09-29 only (see MeanReversion). The OI ratio is held for
    short windows (to 12:00 on dte1, to 13:30 on expiry day), where it worked across signal times.

    far   11:00, follow the move since the previous close, buy ATM, exit 13:30, quarter risk
    dte1  10:15 / 11:00 tranches: follow the OI ratio, buy ATM, exit 12:00, 1x risk each
    exp   10:00 / 10:30 tranches: follow the OI ratio, 4-strike debit spread, stop 1x morning range,
          exit 13:30, quarter risk each
    """
    name = "Directional"
    rules = {
        "far": Rule("pc", "follow", T(11, 0), "long", T(13, 30), 0.0, 0.25),
        "dte1": tuple(Rule("pcr", "follow", t, "long", T(12, 0), 0.0, 1.0) for t in (T(10, 15), T(11, 0))),
        "exp": tuple(Rule("pcr", "follow", t, "debit4", T(13, 30), 1.0, 0.25) for t in (T(10, 0), T(10, 30))),
    }


class SemiDirectional(RuleStrategy):
    """Premium selling with a directional tilt: an ATM credit spread on the side the view expects price to
    move away from, so it earns if price goes sideways or in the view's direction.

    Designed 2026-10-02 on 2023-01-02 -> 2025-09-29 only (see MeanReversion).

    far   10:45, follow the move since the previous close: sell ATM spread (5 wide), exit 13:30, quarter risk
    dte1  10:30 / 10:45 tranches: fade the move since the previous close, sell ATM spread (3 wide), hold to
          15:15, quarter risk each
    exp   09:30 / 10:15 tranches: fade the move since the previous close, sell ATM spread (5 wide), exit 12:00,
          3x risk each
    """
    name = "Semi-Directional"
    rules = {
        "far": Rule("pc", "follow", T(10, 45), "credit5", T(13, 30), 0.0, 0.25),
        "dte1": tuple(Rule("pc", "fade", t, "credit3", T(15, 15), 0.0, 0.25) for t in (T(10, 30), T(10, 45))),
        "exp": tuple(Rule("pc", "fade", t, "credit5", T(12, 0), 0.0, 3.0) for t in (T(9, 30), T(10, 15))),
    }


class MeanReversionFit2026(RuleStrategy):
    """Saved: Mean Reversion as fitted on 2025-09-30 -> 2026-09-25 (not traded since the 2026-10-02
    redesign; it failed out of sample on 2023-2025). Fade the day's move: bet that the move since the reference point partly reverses.

    v2 (robustness change). The day-before-expiry rule carries most of the profit and in v1
    was a single 10:30 entry whose result depended on the exact minute (a 10:10 entry cut
    Sharpe from 2.19 to 1.15). It is now three equal tranches at 10:20 / 10:35 / 10:50, all
    inside the range of times that worked, with the same 3x total risk. The far-day rule buys
    ATM instead of 1-strike OTM (better in every stress variant). Worst Sharpe across 42
    one-at-a-time stress variants rose from 1.15 to 1.54. The previous version is kept as
    MeanReversionV1.

    far   10:00, fade the move since the open, buy ATM, hold to 15:15, half risk
    dte1  10:20 / 10:35 / 10:50 tranches: fade the move since the previous close, buy ATM,
          stop 1x morning range beyond it, hold to 15:15, 1x risk each (3x total)
    exp   10:00, fade the move since the open, 6-strike debit spread, exit 12:00 or stop 1x range
    """
    name = "Mean Reversion (fit 2025-26)"
    rules = {
        "far": Rule("open", "fade", T(10, 0), "long", T(15, 15), 0.0, 0.5),
        "dte1": tuple(Rule("pc", "fade", t, "long", T(15, 15), 1.0, 1.0) for t in (T(10, 20), T(10, 35), T(10, 50))),
        "exp": Rule("open", "fade", T(10, 0), "debit6", T(12, 0), 1.0, 1.0),
    }


class DirectionalFit2026(RuleStrategy):
    """Saved: Directional as fitted on 2025-09-30 -> 2026-09-25 (not traded since the 2026-10-02 redesign).
    Take the direction option writers are positioned for: the put/call open-interest ratio
    over the 11 strikes around ATM. More put OI than call OI (ratio >= 1) means writers are
    defending lower levels -> bullish; otherwise bearish.

    Chosen for robustness, not peak return: price-follow rules had no signal time at which a
    +-10 minute shift stayed profitable, while the OI-ratio signal was profitable in both halves
    at every decision time from 09:45 to 11:00. Risk multipliers are moderate, not optimised.

    far   10:00, buy ATM in the OI-ratio direction, hold to 15:15, 1x risk
    dte1  10:15, buy ATM in the OI-ratio direction, stop 1x morning range beyond it, hold to 15:15, 2x risk
    exp   09:55, sell an ATM 5-wide credit spread against the OI-ratio direction, hold to 15:15, half risk
    """
    name = "Directional (fit 2025-26)"
    rules = {
        "far": Rule("pcr", "follow", T(10, 0), "long", T(15, 15), 0.0, 1.0),
        "dte1": Rule("pcr", "follow", T(10, 15), "long", T(15, 15), 1.0, 2.0),
        "exp": Rule("pcr", "follow", T(9, 55), "credit5", T(15, 15), 0.0, 0.5),
    }


class SemiDirectionalFit2026(RuleStrategy):
    """Saved: Semi-Directional as fitted on 2025-09-30 -> 2026-09-25 (not traded since the 2026-10-02
    redesign). Premium selling with a directional tilt: a credit spread on the side the view expects
    price to move away from, so it earns if price goes sideways or in the view's direction.

    far   09:30, follow the gap: sell 2-strike-OTM spread (3 wide) against it, exit 12:00, half risk
    dte1  10:30, fade the move since the previous close: sell ATM spread (5 wide), hold to 15:15, 3x risk
    exp   10:00, fade the move since the open: sell ATM spread (5 wide), exit 12:00, 3x risk
    """
    name = "Semi-Directional (fit 2025-26)"
    rules = {
        "far": Rule("pc", "follow", T(9, 30), "otmcredit3", T(12, 0), 0.0, 0.5),
        "dte1": Rule("pc", "fade", T(10, 30), "credit5", T(15, 15), 0.0, 3.0),
        "exp": Rule("open", "fade", T(10, 0), "credit5", T(12, 0), 0.0, 3.0),
    }


class MeanReversionV1(RuleStrategy):
    """Saved specification of Mean Reversion as of 2026-09-29, before the robustness change.
    Not traded. To revert, set MeanReversion.rules = MeanReversionV1.rules (results snapshot:
    results/archive/2026-09-29_mean_reversion_v1/).

    far   10:00, fade the move since the open, buy the 1-strike-OTM option, hold to 15:15, half risk
    dte1  10:30, fade the move since the previous close, buy ATM, stop 1x morning range beyond it, 3x risk
    exp   10:00, fade the move since the open, 6-strike debit spread, exit 12:00 or stop 1x range
    """
    name = "Mean Reversion (v1)"
    rules = {
        "far": Rule("open", "fade", T(10, 0), "long_otm1", T(15, 15), 0.0, 0.5),
        "dte1": Rule("pc", "fade", T(10, 30), "long", T(15, 15), 1.0, 3.0),
        "exp": Rule("open", "fade", T(10, 0), "debit6", T(12, 0), 1.0, 1.0),
    }


class DirectionalPriceFollow(RuleStrategy):
    """The previous Directional (kept for the report's before/after comparison only, not traded):
    follow the day's price move. It was fragile - every +-10 minute shift of its signal times lost.

    far   09:20, follow the gap (move since the previous close), buy ATM, exit 12:00, 2x risk
    dte1  09:25, follow the move since the open, 6-strike debit spread, stop 1x range, hold to 15:15, 2x risk
    exp   11:00, follow the move since the open, buy ATM, exit 13:30 or stop 0.5x range, half risk
    """
    name = "Directional (before)"
    rules = {
        "far": Rule("pc", "follow", T(9, 20), "long", T(12, 0), 0.0, 2.0),
        "dte1": Rule("open", "follow", T(9, 25), "debit6", T(15, 15), 1.0, 2.0),
        "exp": Rule("open", "follow", T(11, 0), "long", T(13, 30), 0.5, 0.5),
    }


def default_strategies():
    """The three strategies that are traded and reported, in report order."""
    return [MeanReversion(), Directional(), SemiDirectional()]
