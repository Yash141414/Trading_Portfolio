"""
Known-answer tests for fills, slippage and charges.  python tests/test_engine.py

Expected numbers were worked out by hand from the rates in backtest/config.py.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backtest.config import DEFAULT  # noqa: E402
from backtest.data import Day  # noqa: E402
from backtest.engine import Leg, Position  # noqa: E402


def tiny_day() -> Day:
    """A 5-bar day with one strike and known open/close prices, for hand-checkable fills."""
    ts = pd.date_range("2026-01-06 09:15", periods=5, freq="min")
    opens = np.array([[98.0], [100.0], [110.0], [120.0], [125.0]])
    closes = np.array([[99.0], [105.0], [115.0], [122.0], [126.0]])
    return Day(date=pd.Timestamp("2026-01-06"), expiry=pd.Timestamp("2026-01-13"), dte=7, lot=65, ts=ts,
               spot=np.full(5, 25000.0), strikes={"CE": np.array([25000.0]), "PE": np.array([25000.0])},
               close={"CE": closes, "PE": closes}, open={"CE": opens, "PE": opens},
               offsets=np.array([0]), offset_strike={"CE": np.full((5, 1), 25000.0), "PE": np.full((5, 1), 25000.0)})


def close_to(a, b, tol=1e-3):
    """True if a and b agree within tol."""
    assert abs(a - b) < tol, f"{a} != {b}"


def test_long_call_round_trip():
    """Buy one lot, sell it later: fill prices, slippage, charges and net P&L match hand calculations."""
    pos = Position(tiny_day(), [Leg("CE", 25000.0, +1)], 1, 1, DEFAULT, "t", "long")
    close_to(pos.entry_px[0], 100.5)             # open of bar 1 (not its close) + 0.5% slippage
    t = pos.close(3, "test")
    close_to(t.exit_net, 119.4)                  # open of bar 3 - 0.5% slippage
    close_to(t.gross_pnl, (119.4 - 100.5) * 65)  # 1228.5
    # buy 6532.50 turnover: 20 + 2.28834 exch + 0.00653 sebi + 0.19598 stamp + 4.01308 gst = 26.50393
    # sell 7761.00 turnover: 20 + 2.71868 exch + 0.00776 sebi + 7.76100 stt + 4.09076 gst = 34.57820
    close_to(t.costs, 26.50393 + 34.57820)
    close_to(t.pnl, 1228.5 - 61.08213)
    assert t.bars_held == 2


def test_short_leg_signs():
    """A short leg receives a credit at entry, loses when the option rises, and charges reduce its P&L."""
    pos = Position(tiny_day(), [Leg("CE", 25000.0, -1)], 2, 1, DEFAULT, "t", "short")
    close_to(pos.entry_net, -99.5)               # credit received: 100 - 0.5
    t = pos.close(3, "test")
    close_to(t.gross_pnl, (-120.6 + 99.5) * 130) # bought back at 120 + 0.6, 2 lots
    assert t.pnl < t.gross_pnl


def test_min_slippage_floor():
    """On a cheap option, slippage uses the fixed points floor, not the % of premium."""
    d = tiny_day()
    d.open["CE"][:] = 10.0
    pos = Position(d, [Leg("CE", 25000.0, +1)], 1, 1, DEFAULT, "t", "long")
    close_to(pos.entry_px[0], 10.25)             # 0.5% of 10 = 0.05 < 0.25 floor


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
