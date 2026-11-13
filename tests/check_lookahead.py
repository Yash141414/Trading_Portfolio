"""
Look-ahead check: run the current strategies on synthetic data where no edge is possible.

Spot is a random walk and options are fairly priced (tests/make_synthetic.py), and all
costs and slippage are set to zero. If the engine or a strategy can see the future,
mean P&L per trade comes out consistently positive. Pass: |t| < 2 for every strategy on
(nearly) every seed, and the sign varies across seeds. One |t| slightly above 2 in ~15
strategy-seed tests is what chance produces; a strategy that is positive on every seed
is not.

  python tests/check_lookahead.py                      # seeds 1 2 3 4, synthetic data in a temp folder
  python tests/check_lookahead.py --seeds 1 2 3 4 5 6

Synthetic data has no open interest, so Directional's put/call-ratio signal falls back to
the move since the open there: this check does not cover the OI path. For that, compute the
ratio a few minutes stale and confirm results barely change (see manual.md).
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest.config import Config, Costs  # noqa: E402
from backtest.data import load_days  # noqa: E402
from backtest.engine import run  # noqa: E402
from backtest.metrics import trades_frame  # noqa: E402
from backtest.strategies import default_strategies  # noqa: E402

ZERO = Config(costs=Costs(brokerage_per_order=0, stt_sell=0, exchange_txn=0, sebi_per_crore=0,
                          stamp_buy=0, gst=0, slippage_pts=0, slippage_pct=0))


def main():
    """Generate synthetic data per seed and print mean P&L per trade and its t-stat for each strategy."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3, 4])
    a = ap.parse_args()
    flagged = []
    with tempfile.TemporaryDirectory() as tmp:
        for seed in a.seeds:
            path = Path(tmp) / f"synthetic_{seed}.parquet"
            subprocess.run([sys.executable, str(ROOT / "tests" / "make_synthetic.py"), "--out", str(path),
                            "--seed", str(seed)], check=True, capture_output=True)
            days = load_days(path)
            print(f"seed {seed}: {len(days)} synthetic days, zero costs")
            for strat in default_strategies():
                pnl = trades_frame(run(strat, days, ZERO))["pnl"]
                t = pnl.mean() / (pnl.std(ddof=1) / np.sqrt(len(pnl))) if len(pnl) > 1 else float("nan")
                mark = "  <-- |t| >= 2" if abs(t) >= 2 else ""
                print(f"  {strat.name:17s} trades {len(pnl):4d}  mean Rs {pnl.mean():9.2f}  t {t:6.2f}{mark}")
                if abs(t) >= 2:
                    flagged.append((seed, strat.name, t))
    n = len(a.seeds) * len(default_strategies())
    print(f"\n{len(flagged)} of {n} strategy-seed tests with |t| >= 2 (about {0.05 * n:.1f} expected by chance)")


if __name__ == "__main__":
    main()
