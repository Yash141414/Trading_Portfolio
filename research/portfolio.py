"""
Quick portfolio check on the DEVELOPMENT period from the rule search's simulated P&L.

Approximation: each rule's P&L is taken from the search grid (fixed Rs 10 lakh equity, 1x risk) and scaled
linearly by its risk multiplier -- no compounding, lot floors or caps. Good for comparing candidate rule
sets quickly; the final numbers always come from run_backtest.py with the real engine.

  python research/portfolio.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from score_rules import KEY, SEARCH, rule_pnl  # noqa: E402

CAP = 1_000_000.0


def daily(df: pd.DataFrame, spec: dict) -> pd.Series:
    """spec: {dtype: [(ref, mode, sig, kind, exit, stop, risk), ...]} -> daily P&L series."""
    parts = []
    for dt, rules in spec.items():
        for ref, mode, sig, kind, ex, stop, risk in rules:
            m = ((df.dtype == dt) & (df.ref == ref) & (df["mode"] == mode) & (df.sig == sig)
                 & (df.kind == kind) & (df.exit == ex) & (df.stop == stop))
            x = df.loc[m, ["date", "pnl"]]
            if x.empty:
                raise ValueError(f"no such rule: {dt} {ref} {mode} {sig} {kind} {ex} {stop}")
            parts.append(x.assign(pnl=x.pnl * risk))
    return pd.concat(parts).groupby("date")["pnl"].sum()


def stats(pnl: pd.Series, cap: float) -> dict:
    eq = cap + pnl.cumsum()
    years = len(pnl) / 245
    cagr = (eq.iat[-1] / cap) ** (1 / years) - 1
    mdd = (eq / eq.cummax().clip(lower=cap) - 1).min()
    ret = pnl / eq.shift(1).fillna(cap)
    by_year = pnl.groupby(pnl.index.year).sum() / cap
    return dict(cagr=round(cagr * 100, 1), mdd=round(mdd * 100, 1), calmar=round(cagr / -mdd, 2) if mdd < 0 else np.inf,
                sharpe=round(ret.mean() / ret.std() * np.sqrt(245), 2),
                **{f"y{y}%": round(v * 100, 1) for y, v in by_year.items()})


def report(df: pd.DataFrame, specs: dict) -> None:
    cols = {}
    for name, spec in specs.items():
        cols[name] = daily(df, spec)
    dev = pd.DataFrame(cols).fillna(0.0)
    rows = {n: stats(dev[n], CAP) for n in dev}
    rows["Combined"] = stats(dev.sum(axis=1), CAP * len(dev.columns))
    print(pd.DataFrame(rows).T.to_string())
    print("correlation:\n", dev.corr().round(2).to_string())


if __name__ == "__main__":
    import json
    df = rule_pnl(SEARCH)
    specs = json.loads(Path(sys.argv[1]).read_text())
    report(df, specs)
