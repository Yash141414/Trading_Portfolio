"""
Load 1-minute NIFTY option bars into per-day arrays.

The default source is build_dhan_backtest_data.py's output (data/final/nifty_opt_1min.parquet):
Dhan's expired-options data, which is "rolling" - each series is a strike offset from
ATM (ATM, ATM+1, ...) with the real strike given on every bar. It is pivoted back to
real contracts here, so a position in a fixed strike can be marked every minute while
spot moves around it. The 'offset' column also gives an ATM-by-offset lookup
(Day.strike). Sources without an 'offset' column (build_strategy_data.py) work too:
ATM then comes from rounding spot. Open interest, when present, is kept for the
Directional strategy's put/call ratio (Day.pcr).

Lot sizes come from the NSE bhavcopy (data/nifty_opt_bhavcopy.parquet): the nearest
expiry's lot size on each date. Days without one are skipped, never guessed.

Day arrays are indexed [bar, strike column]. Prices are forward-filled within the
day: a strike with no bar in a minute keeps its last close (stale, never future).
"""
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path("data/final/nifty_opt_1min.parquet")   # Dhan, built by build_dhan_backtest_data.py
BHAVCOPY = Path("data/nifty_opt_bhavcopy.parquet")
MIN_BARS = 300          # skip days with badly incomplete data (a full session is 375)


@dataclass
class Day:
    """One trading day of the nearest weekly expiry: bar times, spot, and per-strike prices.

    Price arrays are indexed [bar, strike column]; use `px` rather than indexing them
    directly, since it maps a strike to its column and returns NaN for strikes the day
    never traded.
    """
    date: pd.Timestamp
    expiry: pd.Timestamp
    dte: int
    lot: int
    ts: pd.DatetimeIndex            # bar timestamps
    spot: np.ndarray                # spot per bar
    strikes: dict                   # opt_type -> np.ndarray of strikes (columns)
    close: dict                     # opt_type -> [bars, strikes] close, ffilled
    open: dict                      # opt_type -> [bars, strikes] open, falls back to prior close
    offsets: np.ndarray             # offset values, e.g. -10..10
    offset_strike: dict             # opt_type -> [bars, offsets] strike at that offset
    oi: dict | None = None          # opt_type -> [bars, strikes] open interest, ffilled (if the data has it)

    @property
    def n(self) -> int:
        """Number of 1-minute bars in the day."""
        return len(self.ts)

    def pcr(self, i: int, k: float, n: int = 5, step: int = 50) -> float:
        """Put/call open-interest ratio over strikes k-n*step .. k+n*step at bar i's close."""
        if self.oi is None:
            return np.nan
        tot = {}
        for opt in ("CE", "PE"):
            cols = [self.col(opt, k + j * step) for j in range(-n, n + 1)]
            v = np.array([self.oi[opt][i, c] for c in cols if c >= 0], dtype=float)
            tot[opt] = np.nansum(v)
        return tot["PE"] / tot["CE"] if tot["CE"] > 0 else np.nan

    def bar_at(self, t) -> int:
        """First bar at or after clock time t (datetime.time); n if none."""
        return int(np.searchsorted(self.ts.time, t)) if len(self.ts) else 0

    def strike(self, i: int, opt: str, offset: int, step: int = 50) -> float:
        """Strike at `offset` from ATM on bar i; falls back to rounding spot."""
        k = np.where(self.offsets == offset)[0]
        if len(k):
            s = self.offset_strike[opt][i, k[0]]
            if not np.isnan(s):
                return float(s)
        return float(round(self.spot[i] / step) * step + offset * step)

    def col(self, opt: str, strike: float) -> int:
        """Column index of `strike` in this day's arrays for CE/PE `opt`; -1 if absent."""
        j = np.where(self.strikes[opt] == strike)[0]
        return int(j[0]) if len(j) else -1

    def px(self, i: int, opt: str, strike: float, field: str = "close") -> float:
        """Option price at bar i: its close (default) or open (`field="open"`); NaN if not traded."""
        j = self.col(opt, strike)
        if j < 0:
            return np.nan
        return float((self.close if field == "close" else self.open)[opt][i, j])


def _lot_sizes(bars: pd.DataFrame) -> pd.Series:
    """Lot size per date for the nearest expiry."""
    if "lot_size" in bars:
        return bars.groupby("date")["lot_size"].first()
    if not BHAVCOPY.exists():
        raise FileNotFoundError(f"{BHAVCOPY} needed for lot sizes")
    b = pd.read_parquet(BHAVCOPY, columns=["date", "expiry", "lot_size"])
    near = b[b["expiry"] == b.groupby("date")["expiry"].transform("min")]
    return near.groupby("date")["lot_size"].agg(lambda s: s.mode().iat[0])


def load_days(path: Path = DATA, start=None, end=None) -> list[Day]:
    """Read the 1-minute file and return one Day per trading date in [start, end].

    Days with no bhavcopy lot size, or fewer than MIN_BARS bars (e.g. a shortened
    session), are skipped with a printed note.
    """
    bars = pd.read_parquet(path)
    if start:
        bars = bars[bars["date"] >= pd.Timestamp(start)]
    if end:
        bars = bars[bars["date"] <= pd.Timestamp(end)]
    if bars.empty:
        raise ValueError("no bars in the requested range")
    lots = _lot_sizes(bars)
    has_offset = "offset" in bars.columns
    offsets = np.sort(bars["offset"].unique()) if has_offset else np.array([], dtype=float)
    days = []
    for d, g in bars.groupby("date", sort=True):
        if d not in lots.index:
            print(f"skip {d.date()}: no lot size")
            continue
        ts = pd.DatetimeIndex(np.sort(g["ts"].unique()))
        if len(ts) < MIN_BARS:
            print(f"skip {d.date()}: only {len(ts)} bars")
            continue
        spot = g.groupby("ts")["spot"].median().reindex(ts).ffill().bfill().to_numpy()
        strikes, close, open_, off_k, oi = {}, {}, {}, {}, {}
        for opt in ("CE", "PE"):
            o = g[g["opt_type"] == opt]
            c = o.pivot_table(index="ts", columns="strike", values="close", aggfunc="last").reindex(ts).ffill()
            op = o.pivot_table(index="ts", columns="strike", values="open", aggfunc="last").reindex(ts)
            op = op.fillna(c.shift(1)).fillna(c)          # no bar this minute -> last known close
            strikes[opt] = c.columns.to_numpy(dtype=float)
            close[opt] = c.to_numpy()
            open_[opt] = op[c.columns].to_numpy()
            if "oi" in o:
                oi[opt] = (o.pivot_table(index="ts", columns="strike", values="oi", aggfunc="last")
                           .reindex(index=ts, columns=c.columns).ffill().to_numpy(dtype=float))
            if has_offset:
                off_k[opt] = (o.pivot_table(index="ts", columns="offset", values="strike", aggfunc="last")
                              .reindex(index=ts, columns=offsets).to_numpy(dtype=float))
            else:
                off_k[opt] = np.empty((len(ts), 0), dtype=float)
        days.append(Day(date=d, expiry=g["expiry"].iat[0], dte=int(g["dte"].iat[0]), lot=int(lots[d]),
                        ts=ts, spot=spot, strikes=strikes, close=close, open=open_,
                        offsets=offsets, offset_strike=off_k, oi=oi or None))
    return days
