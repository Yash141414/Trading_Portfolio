"""
Charts and the Markdown report. Everything here is computed from the run's
results; the written analysis is kept in a separate file (ANALYSIS.md) so that
re-running the backtest never overwrites it.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from .metrics import drawdown, inr, table  # noqa: E402

COLORS = {"Mean Reversion": "#2a78b5", "Directional": "#d9822b", "Semi-Directional": "#3a9e5c",
          "Combined": "#222222"}


def plot_equity(curves: dict[str, pd.DataFrame], capitals: dict[str, float], out: Path) -> None:
    """All strategies and the combined portfolio as % return, plus combined drawdown."""
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    for name, eq in curves.items():
        ret = eq["equity"] / capitals[name] - 1
        ax.plot(ret.index, ret * 100, label=name, color=COLORS.get(name),
                lw=2.4 if name == "Combined" else 1.4)
    ax.axhline(0, color="#999", lw=0.8)
    ax.set_ylabel("Return on capital (%)")
    ax.set_title("Equity curves - individual strategies and combined portfolio")
    ax.legend(loc="upper left", frameon=False)
    ax.grid(alpha=0.3)
    dd = drawdown(curves["Combined"]["equity"]) * 100
    ax2.fill_between(dd.index, dd, 0, color="#c0392b", alpha=0.4)
    ax2.set_ylabel("Combined DD (%)")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)


def plot_single(name: str, eq: pd.DataFrame, capital: float, out: Path) -> None:
    """One strategy's equity (Rs lakh) and drawdown chart, saved to `out`."""
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(10, 5.5), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    ax.plot(eq.index, eq["equity"] / 1e5, color=COLORS.get(name), lw=1.6)
    ax.axhline(capital / 1e5, color="#999", lw=0.8)
    ax.set_ylabel("Equity (Rs lakh)")
    ax.set_title(f"{name} - equity and drawdown")
    ax.grid(alpha=0.3)
    dd = drawdown(eq["equity"]) * 100
    ax2.fill_between(dd.index, dd, 0, color="#c0392b", alpha=0.4)
    ax2.set_ylabel("DD (%)")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)


def plot_before_after(eqs: dict[str, pd.DataFrame], capital: float, out: Path, name: str) -> None:
    """A strategy's previous vs current version, % return and drawdown."""
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(10, 5.5), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    for (label, eq), color in zip(eqs.items(), ("#999999", COLORS.get(name, "#222222"))):
        ax.plot(eq.index, (eq["equity"] / capital - 1) * 100, label=label, color=color, lw=1.6)
        ax2.plot(eq.index, drawdown(eq["equity"]) * 100, color=color, lw=1.2)
    ax.axhline(0, color="#999", lw=0.8)
    ax.set_ylabel("Return on capital (%)")
    ax.set_title(f"{name} - before vs after the change")
    ax.legend(loc="upper left", frameon=False)
    ax.grid(alpha=0.3)
    ax2.set_ylabel("DD (%)")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)


def _md(df: pd.DataFrame) -> str:
    cols = [str(df.index.name or "")] + [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for idx, row in df.iterrows():
        lines.append("| " + " | ".join([str(idx)] + [str(v) for v in row]) + " |")
    return "\n".join(lines)


def monthly_returns(curves: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Monthly compounded return of each curve, formatted as +x.x% strings."""
    m = pd.DataFrame({n: (1 + eq["ret"]).resample("ME").prod() - 1 for n, eq in curves.items()})
    m.index = m.index.strftime("%Y-%m")
    m.index.name = "Month"
    return m.map(lambda v: f"{v:+.1%}")


def breakdown(trades: pd.DataFrame, by: str) -> pd.DataFrame:
    """Trades, win rate, average and total P&L per strategy, grouped by column `by`."""
    g = trades.groupby(["strategy", by])["pnl"]
    df = pd.DataFrame({"trades": g.size(), "win rate": g.apply(lambda p: f"{(p > 0).mean():.0%}"),
                       "avg P&L (Rs)": g.mean().map(inr),
                       "total P&L (Rs)": g.sum().map(inr)})
    df.index = [f"{s} / {k}" for s, k in df.index]
    df.index.name = f"strategy / {by}"
    return df


def write_report(out: Path, meta: dict, stats: dict, halves: dict, curves: dict, trades: pd.DataFrame,
                 corr: pd.DataFrame, robust: pd.DataFrame | None, compare: list | None = None) -> Path:
    """Write results/report.md: setup, charts, stats, halves, correlations, robustness, before/after, breakdowns."""
    s = []
    s.append("# NIFTY Intraday Options - Three-Strategy Backtest\n")
    s.append(f"Data: {meta['data']} | Period: {meta['start']} to {meta['end']} "
             f"({meta['days']} trading days) | Generated: {meta['generated']}\n")
    if meta.get("synthetic"):
        s.append("> **SYNTHETIC DATA - pipeline test only. These numbers say nothing about real performance.**\n")
    s.append("## Setup\n")
    s.append(meta["setup"] + "\n")
    s.append("## Equity curves\n\n![Equity curves](equity_curves.png)\n")
    for name in stats:
        if name != "Combined":
            s.append(f"![{name}]({name.lower().replace(' ', '_').replace('-', '_')}.png)\n")
    s.append("## Performance statistics\n")
    s.append(_md(table(stats)) + "\n")
    c = stats["Combined"]
    target = "met" if c["calmar"] >= 5 else "**not met**"
    s.append(f"Combined Calmar = {c['calmar']:.2f} (target >= 5: {target}). "
             f"Combined Sharpe {c['sharpe']:.2f}, 95% CI [{c['sharpe_lo']:.2f}, {c['sharpe_hi']:.2f}].\n")
    s.append("## First half vs second half\n")
    s.append("Same parameters throughout. The rules were selected on 2 Jan 2023 - 29 Sep 2025 (the development "
             "period, which required every strategy to be profitable in both of its halves): a half inside it "
             "is in-sample, a half after it is out-of-sample.\n")
    s.append(_md(halves) + "\n")
    s.append("## Correlation of daily returns\n")
    s.append(_md(corr.map(lambda v: f"{v:.2f}")) + "\n")
    if robust is not None:
        s.append("## Parameter robustness\n")
        s.append("Each strategy re-run with its key parameters moved around the defaults. "
                 "Results that collapse when a parameter moves one notch are a sign of overfitting.\n")
        s.append(_md(robust) + "\n")
    for name, why, tbl, png in compare or []:
        s.append(f"## {name}: before vs after\n")
        s.append(why + " Both versions are re-run here through the same engine, costs and sizing on every "
                 "backtest. The robustness grid shifts every signal time by +-10 minutes and scales risk by "
                 "0.5x-1.5x; the stress test (with --robustness) moves one thing at a time: signal time "
                 "+-10/20 min, exit time +-30/60 min, stop width, option structure. Both use this run's period.\n")
        s.append(_md(tbl) + "\n")
        s.append(f"![{name} before vs after]({png})\n")
    s.append("## Breakdown by exit reason\n")
    s.append(_md(breakdown(trades, "reason")) + "\n")
    s.append("## Breakdown by days to expiry\n")
    s.append(_md(breakdown(trades, "dte")) + "\n")
    s.append("## Breakdown by direction\n")
    s.append(_md(breakdown(trades, "direction")) + "\n")
    s.append("## Monthly returns\n")
    s.append(_md(monthly_returns(curves)) + "\n")
    s.append("## Analysis\n\nSee [ANALYSIS.md](ANALYSIS.md) for the written review of each strategy's "
             "strengths and weaknesses.\n")
    path = out / "report.md"
    path.write_text("\n".join(s), encoding="utf-8")
    return path
