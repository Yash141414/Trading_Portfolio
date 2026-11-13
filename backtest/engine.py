"""
Execution model: fills, slippage, charges, marking and position sizing.

Timing rule (no look-ahead): a strategy decides on the CLOSE of bar i, and the
order fills at the OPEN of bar i+1. Every position is squared off at the open of
the square-off bar (15:15) at the latest.
"""
from dataclasses import dataclass

import numpy as np

from .config import Config, Costs
from .data import Day


@dataclass
class Leg:
    """One option leg of a position: CE/PE, strike, and +1 (long) or -1 (short)."""
    opt: str          # "CE" / "PE"
    strike: float
    side: int         # +1 long, -1 short

    def __str__(self):
        return f"{'+' if self.side > 0 else '-'}{self.strike:.0f}{self.opt}"


@dataclass
class Trade:
    """A closed position, as recorded in trades.csv: timing, prices, P&L after slippage and charges."""
    strategy: str
    date: object
    dte: int
    direction: str
    legs: str
    lots: int
    qty: int                       # contracts per leg = lots * lot size
    entry_ts: object
    exit_ts: object
    bars_held: int
    entry_net: float               # net premium per unit at entry, + = debit paid
    exit_net: float
    gross_pnl: float               # Rs, after slippage, before charges
    costs: float                   # Rs, all statutory + brokerage
    pnl: float                     # Rs, net
    reason: str


def slip(px: float, c: Costs) -> float:
    """Slippage per unit per fill: the larger of a fixed points floor and a % of premium."""
    return max(c.slippage_pts, c.slippage_pct * px)


def fill_price(px: float, side: int, c: Costs) -> float:
    """Price actually paid (buy) or received (sell) after slippage."""
    return px + slip(px, c) if side > 0 else max(0.05, px - slip(px, c))


def charges(px: float, qty: int, is_buy: bool, c: Costs) -> float:
    """Charges for one executed order of `qty` contracts at premium `px`."""
    turnover = px * qty
    exch = turnover * c.exchange_txn
    sebi = turnover * c.sebi_per_crore / 1e7
    tax = (c.stamp_buy if is_buy else c.stt_sell) * turnover
    return c.brokerage_per_order + exch + sebi + tax + c.gst * (c.brokerage_per_order + exch + sebi)


class Position:
    """A multi-leg option position opened at the open of bar `i_fill`."""

    def __init__(self, day: Day, legs: list[Leg], lots: int, i_fill: int, cfg: Config,
                 strategy: str, direction: str):
        self.day, self.legs, self.lots, self.cfg = day, legs, lots, cfg
        self.strategy, self.direction = strategy, direction
        self.qty = lots * day.lot
        self.i_entry = i_fill
        self.entry_px = [fill_price(day.px(i_fill, l.opt, l.strike, "open"), l.side, cfg.costs) for l in legs]
        self.cost = sum(charges(p, self.qty, l.side > 0, cfg.costs) for p, l in zip(self.entry_px, legs))
        self.entry_net = sum(l.side * p for l, p in zip(legs, self.entry_px))

    @property
    def valid(self) -> bool:
        """True if every leg had a price at the fill bar and at least one contract was bought."""
        return all(np.isfinite(self.entry_px)) and self.qty > 0

    def net(self, i: int) -> float:
        """Net premium per unit at the close of bar i (what the position is worth)."""
        return sum(l.side * self.day.px(i, l.opt, l.strike) for l in self.legs)

    def upnl_pts(self, i: int) -> float:
        """Unrealised P&L per unit at bar i's close, before exit slippage and charges."""
        return self.net(i) - self.entry_net

    def close(self, i_fill: int, reason: str) -> Trade:
        """Exit every leg at the open of bar i_fill (with slippage and charges) and return the Trade."""
        d, c = self.day, self.cfg.costs
        exit_px = [fill_price(d.px(i_fill, l.opt, l.strike, "open"), -l.side, c) for l in self.legs]
        cost = self.cost + sum(charges(p, self.qty, l.side < 0, c) for p, l in zip(exit_px, self.legs))
        exit_net = sum(l.side * p for l, p in zip(self.legs, exit_px))
        gross = (exit_net - self.entry_net) * self.qty
        return Trade(self.strategy, d.date, d.dte, self.direction, " ".join(map(str, self.legs)),
                     self.lots, self.qty, d.ts[self.i_entry], d.ts[i_fill], i_fill - self.i_entry,
                     self.entry_net, exit_net, gross, cost, gross - cost, reason)


def lots_for(risk_per_lot: float, equity: float, cfg: Config) -> int:
    """Lots such that hitting the planned stop loses about cfg.risk_per_trade of equity."""
    if not np.isfinite(risk_per_lot) or risk_per_lot <= 0:
        return 0
    return int(min(cfg.max_lots, cfg.risk_per_trade * equity // risk_per_lot))


@dataclass
class Session:
    """Bar indices that bound trading on a day."""
    first: int
    last_entry: int
    square_off: int

    @classmethod
    def of(cls, day: Day, cfg: Config, first_time=None) -> "Session":
        """Bar indices for the day's first allowed bar, last entry (14:30) and square-off (15:15)."""
        sq = min(day.bar_at(cfg.square_off), day.n - 1)
        return cls(first=day.bar_at(first_time or cfg.session_open),
                   last_entry=min(day.bar_at(cfg.last_entry), sq - 1), square_off=sq)


def run(strategy, days: list[Day], cfg: Config) -> list[Trade]:
    """Run one strategy over all days, sizing each day off the sleeve's equity so far."""
    equity, trades = cfg.capital_per_strategy, []
    for day in days:
        t = strategy.run_day(day, equity, cfg)
        trades += t
        equity += sum(x.pnl for x in t)
    return trades
