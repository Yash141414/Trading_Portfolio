"""
Rule search for the 2026-10-02 redesign: score every candidate rule on the DEVELOPMENT period only.

The data file passed in must contain only development days (data/dhan_2023_2025/nifty_opt_1min.parquet,
2023-01-02 -> 2025-09-29). The test year (data/final/, 2025-09-30 on) is never read here.

  simulate   for every day, signal time, option structure, exit time and stop, simulate the position
             in BOTH directions with the backtest's own code (strategies.simulate) at a fixed Rs 10 lakh
             equity and 1x risk; also record each signal's view (open / pc / pcr) at that time.
             -> <out>/grid.parquet, <out>/views.parquet
  (scoring is done by research/score_rules.py on those two files)

  python research/rule_search.py simulate
"""
import argparse
import pickle
import sys
import time as clock
from concurrent.futures import ProcessPoolExecutor
from datetime import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backtest.config import DEFAULT as CFG                    # noqa: E402
from backtest.data import load_days                           # noqa: E402
from backtest.engine import Session                           # noqa: E402
from backtest.strategies import day_type, simulate, view      # noqa: E402

DATA = Path("data/dhan_2023_2025/nifty_opt_1min.parquet")
OUT = Path("data/dhan_2023_2025/search")
LAST_DEV_DAY = pd.Timestamp("2025-09-29")

SIGS = [time(9, 20), time(9, 30), time(9, 45), time(10, 0), time(10, 15), time(10, 30), time(10, 45),
        time(11, 0), time(11, 30), time(12, 0), time(13, 0)]
DEBIT_KINDS = ["long", "long_otm1", "debit4", "debit6"]       # the spot stop applies to these only
CREDIT_KINDS = ["credit3", "credit5", "otmcredit3"]
EXITS = [time(12, 0), time(13, 30), time(15, 15)]
STOPS = [0.0, 1.0]
EQUITY = CFG.capital_per_strategy


def _work(chunk_file: Path) -> tuple[list, list]:
    days, prevs = pickle.loads(chunk_file.read_bytes())
    rows, views = [], []
    for day, prev in zip(days, prevs):
        s = Session.of(day, CFG)
        for sig in SIGS:
            i = day.bar_at(sig) - 1
            if i < 0 or i >= s.last_entry:
                continue
            views.append((day.date, sig, view(day, i, "open", prev, CFG), view(day, i, "pc", prev, CFG),
                          view(day, i, "pcr", prev, CFG)))
            for kind in DEBIT_KINDS + CREDIT_KINDS:
                for ex in EXITS:
                    if ex <= sig:
                        continue
                    for stop in (STOPS if kind in DEBIT_KINDS else [0.0]):
                        for sign in (1, -1):
                            t = simulate(day, i, sign, kind, ex, stop, 1.0, EQUITY, CFG, "search")
                            if t:
                                rows.append((day.date, day.dte, day_type(day), sig, kind, ex, stop, sign, t[0].pnl))
    return rows, views


def cmd_simulate(a) -> None:
    t0 = clock.time()
    days = load_days(a.data)
    if days[-1].date > LAST_DEV_DAY:
        raise SystemExit(f"{a.data} contains days after {LAST_DEV_DAY.date()} (test year) -- refusing")
    prevs = [None] + [d.spot[-1] for d in days[:-1]]
    print(f"{len(days)} development days loaded in {clock.time() - t0:.0f}s")
    cache = a.out / "chunks"
    cache.mkdir(parents=True, exist_ok=True)
    n = a.workers * 3
    files = []
    for c in range(n):
        f = cache / f"days_{c:02d}.pkl"
        f.write_bytes(pickle.dumps((days[c::n], prevs[c::n])))
        files.append(f)
    del days
    rows, views = [], []
    with ProcessPoolExecutor(a.workers) as ex:
        for r, v in ex.map(_work, files):
            rows += r
            views += v
    grid = pd.DataFrame(rows, columns=["date", "dte", "dtype", "sig", "kind", "exit", "stop", "sign", "pnl"])
    vw = pd.DataFrame(views, columns=["date", "sig", "open", "pc", "pcr"])
    for df in (grid, vw):
        for c in ("sig", "exit"):
            if c in df:
                df[c] = df[c].astype(str)
    grid.to_parquet(a.out / "grid.parquet", index=False)
    vw.to_parquet(a.out / "views.parquet", index=False)
    for f in files:
        f.unlink()
    print(f"{len(grid):,} simulated positions, {len(vw):,} views -> {a.out}  ({clock.time() - t0:.0f}s)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["simulate"])
    ap.add_argument("--data", type=Path, default=DATA)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--workers", type=int, default=10)
    a = ap.parse_args()
    cmd_simulate(a)
