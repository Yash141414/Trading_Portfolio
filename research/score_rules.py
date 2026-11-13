"""
Score the candidate rules simulated by research/rule_search.py (DEVELOPMENT period only).

A rule = (day type, ref, mode, signal time, structure, exit, stop). Its daily P&L is the simulated
position in the direction its view gives that day (fixed Rs 10 lakh equity, 1x risk). For each
strategy family it ranks rules per day type, keeping only rules that make money in every calendar
year of the development period and whose neighbours (signal time one step earlier/later) also make
money overall -- the same robustness test as before: a plateau, not a peak.

  families   Mean Reversion     fade, long-premium structures (long / long_otm1 / debit)
             Directional        follow, long-premium structures
             Semi-Directional   credit spreads, fade or follow

  python research/score_rules.py [--top 8]
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

SEARCH = Path("data/dhan_2023_2025/search")
SPLIT = pd.Timestamp("2024-05-01")          # development halves
FAMILIES = {
    "Mean Reversion": dict(modes=["fade"], kinds=["long", "long_otm1", "debit4", "debit6"]),
    "Directional": dict(modes=["follow"], kinds=["long", "long_otm1", "debit4", "debit6"]),
    "Semi-Directional": dict(modes=["fade", "follow"], kinds=["credit3", "credit5", "otmcredit3"]),
}
KEY = ["dtype", "ref", "mode", "sig", "kind", "exit", "stop"]


def rule_pnl(search: Path = SEARCH) -> pd.DataFrame:
    """One row per (rule, day): the P&L of the position in the rule's direction that day."""
    grid = pd.read_parquet(search / "grid.parquet")
    vw = pd.read_parquet(search / "views.parquet").melt(["date", "sig"], var_name="ref", value_name="view")
    m = grid.merge(vw, on=["date", "sig"])
    out = []
    for mode, mult in (("follow", 1), ("fade", -1)):
        x = m[m["sign"] == mult * m["view"]].copy()
        x["mode"] = mode
        out.append(x)
    df = pd.concat(out, ignore_index=True).drop(columns=["sign", "view"])
    df["year"] = df["date"].dt.year
    df["half"] = np.where(df["date"] < SPLIT, "H1", "H2")
    return df


def score(df: pd.DataFrame) -> pd.DataFrame:
    """Per-rule totals, per-year and per-half totals, t-stat, worst trade and neighbour minimum."""
    g = df.groupby(KEY)["pnl"]
    s = pd.DataFrame({"n": g.size(), "total": g.sum(), "mean": g.mean(), "sd": g.std(), "worst": g.min()})
    s["t"] = s["mean"] / (s["sd"] / np.sqrt(s["n"]))
    yr = df.pivot_table(index=KEY, columns="year", values="pnl", aggfunc="sum")
    yr.columns = [f"y{c}" for c in yr.columns]
    hf = df.pivot_table(index=KEY, columns="half", values="pnl", aggfunc="sum")
    s = s.join(yr).join(hf)
    s["min_year"] = yr.min(axis=1)
    # neighbour robustness: the same rule with the signal time one step earlier / later
    sigs = sorted(df["sig"].unique())
    pos = {v: i for i, v in enumerate(sigs)}
    tot = s["total"]
    nb = []
    for k in s.index:
        d = dict(zip(KEY, k))
        vals = []
        for step in (-1, 1):
            j = pos[d["sig"]] + step
            if 0 <= j < len(sigs):
                kk = tuple(sigs[j] if c == "sig" else d[c] for c in KEY)
                if kk in tot.index:
                    vals.append(tot[kk])
        nb.append(min(vals) if vals else np.nan)
    s["nb_min"] = nb
    return s.reset_index()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--search", type=Path, default=SEARCH)
    ap.add_argument("--top", type=int, default=8)
    a = ap.parse_args()
    s = score(rule_pnl(a.search))
    s.to_csv(a.search / "scores.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    cols = ["ref", "mode", "sig", "kind", "exit", "stop", "n", "total", "t", "worst", "y2023", "y2024", "y2025",
            "H1", "H2", "nb_min"]
    for fam, f in FAMILIES.items():
        fs = s[s["mode"].isin(f["modes"]) & s["kind"].isin(f["kinds"])]
        for dt in ("far", "dte1", "exp"):
            x = fs[fs["dtype"] == dt]
            ok = x[(x["min_year"] > 0) & (x["nb_min"] > 0)].sort_values("t", ascending=False)
            print(f"\n=== {fam} / {dt}: {len(ok)} of {len(x)} rules positive in every year with positive neighbours")
            print((ok if len(ok) else x.sort_values("t", ascending=False))[cols].head(a.top)
                  .round({"t": 2}).to_string(index=False, float_format=lambda v: f"{v:,.0f}"))


if __name__ == "__main__":
    main()
