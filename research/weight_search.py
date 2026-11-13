"""
Risk-multiplier search on the DEVELOPMENT period for a rule set (research/specs/*.json).

For every combination of per-day-type risk multipliers (far days fixed at their spec value), compute the
combined Calmar from the search grid's P&L (linear in risk, no compounding), keeping only combinations
where each strategy is profitable in every calendar year and both halves. Ranks by the Calmar averaged
over the combination and its one-step neighbours, so a plateau beats a single peak.

  python research/weight_search.py research/specs/v2.json
"""
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from portfolio import CAP, SEARCH, daily, rule_pnl  # noqa: E402
from score_rules import SPLIT  # noqa: E402

W = [0.25, 0.5, 1.0, 1.5, 2.0, 3.0]


def calmar(p, cap):
    eq = cap + np.cumsum(p)
    cagr = (eq[-1] / cap) ** (245 / len(p)) - 1
    mdd = (eq / np.maximum.accumulate(np.maximum(eq, cap)) - 1).min()
    return (cagr / -mdd if mdd < 0 else 99.0), cagr, mdd


def main(path: Path, top: int = 5) -> None:
    df = rule_pnl(SEARCH)
    spec = json.loads(path.read_text())
    cols, base = {}, {}
    for s, sp in spec.items():
        for dt, rules in sp.items():
            cols[(s, dt)] = daily(df, {dt: [r[:-1] + [1.0] for r in rules]})
            base[(s, dt)] = rules[0][-1]
    X = pd.DataFrame(cols).fillna(0.0).sort_index()
    yrs, h1 = X.index.year.values, X.index < SPLIT
    buckets = [c for c in X.columns if c[1] != "far"]
    far = [c for c in X.columns if c[1] == "far"]
    A = X[buckets].values
    F = X[far].values * np.array([base[f] for f in far])
    res = {}
    for w in itertools.product(range(len(W)), repeat=len(buckets)):
        P = A * np.array([W[i] for i in w])
        ok = True
        for s in spec:
            ps = P[:, [i for i, b in enumerate(buckets) if b[0] == s]].sum(1) + \
                 F[:, [j for j, f in enumerate(far) if f[0] == s]].sum(1)
            if min(ps[yrs == y].sum() for y in np.unique(yrs)) <= 0 or ps[h1].sum() <= 0 or ps[~h1].sum() <= 0:
                ok = False
                break
        c, g, m = calmar(P.sum(1) + F.sum(1), CAP * len(spec))
        res[w] = (c if ok else -1.0, g, m)

    def nb(w):
        out = [w]
        for i in range(len(w)):
            for d in (-1, 1):
                if 0 <= w[i] + d < len(W):
                    out.append(w[:i] + (w[i] + d,) + w[i + 1:])
        return out
    sm = {w: np.mean([res[v][0] for v in nb(w)]) for w in res if res[w][0] > 0}
    for w in sorted(sm, key=sm.get, reverse=True)[:top]:
        print("  ".join(f"{b[0][:4]}-{b[1]}:{W[i]}" for b, i in zip(buckets, w)),
              f"| calmar {res[w][0]:.2f}  smoothed {sm[w]:.2f}  cagr {res[w][1]*100:.1f}%  mdd {res[w][2]*100:.1f}%")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
