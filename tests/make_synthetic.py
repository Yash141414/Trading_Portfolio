"""
Synthetic 1-minute data in the same format as dhan_expired.py's combined file.

Spot is a driftless random walk and every option is priced by Black-Scholes at
the SAME volatility that drives spot, so options are fairly priced and no
strategy has a real edge. Backtesting on it should give ~zero gross P&L and a
loss equal to charges + slippage. A clearly positive result would mean the
engine is leaking future information.

  python tests/make_synthetic.py --out <path>/synthetic_1min.parquet
"""
import argparse

import numpy as np
import pandas as pd
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backtest.metrics import inr  # noqa: E402

VOL = 0.13            # annual, per 252 trading days
BARS = 375            # 09:15 .. 15:29
OFFSETS = np.arange(-10, 11)
STEP = 50


def norm_cdf(x):
    """Standard normal CDF (Abramowitz & Stegun approximation, no scipy needed)."""
    # Abramowitz & Stegun 7.1.26, |error| < 1.5e-7
    t = 1 / (1 + 0.3275911 * np.abs(x) / np.sqrt(2))
    poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))))
    erf = 1 - poly * np.exp(-(x ** 2) / 2)
    return 0.5 * (1 + np.sign(x) * erf)


def bs(spot, k, t, call):
    """Black-Scholes price of a call (call=True) or put, zero rates, at the true volatility VOL."""
    t = np.maximum(t, 1e-9)
    sd = VOL * np.sqrt(t)
    d1 = (np.log(spot / k) + 0.5 * sd ** 2) / sd
    d2 = d1 - sd
    c = spot * norm_cdf(d1) - k * norm_cdf(d2)
    return np.where(call, c, c - spot + k)          # put via parity, r = 0


def main():
    """Write a year of synthetic 1-minute data in the backtest's input format."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)

    b = pd.read_parquet("data/nifty_opt_bhavcopy.parquet", columns=["date", "expiry", "lot_size"])
    near = b[b["expiry"] == b.groupby("date")["expiry"].transform("min")]
    cal = near.groupby("date").agg(expiry=("expiry", "first"), lot_size=("lot_size", "first"))
    days = cal.index
    sigma_min = VOL / np.sqrt(252 * BARS)
    spot0, frames = 24500.0, []
    for d, row in cal.iterrows():
        # trading days left after today up to expiry, plus the rest of today
        left = ((days > d) & (days <= row["expiry"])).sum()
        path = spot0 * np.exp(np.cumsum(rng.normal(-0.5 * sigma_min ** 2, sigma_min, BARS + 1)))
        spot0 = path[-1] * np.exp(rng.normal(0, VOL / np.sqrt(252) * 0.3))  # overnight gap
        s_open, s_close = path[:-1], path[1:]
        t_open = (left + (BARS - np.arange(BARS)) / BARS) / 252
        t_close = (left + (BARS - 1 - np.arange(BARS)) / BARS) / 252
        ts = d + pd.Timedelta(hours=9, minutes=15) + pd.to_timedelta(np.arange(BARS), unit="min")
        atm = np.round(s_close / STEP) * STEP
        for opt in ("CE", "PE"):
            call = opt == "CE"
            k = atm[:, None] + OFFSETS[None, :] * STEP                    # [bars, offsets]
            o = bs(s_open[:, None], k, t_open[:, None], call)
            c = bs(s_close[:, None], k, t_close[:, None], call)
            hi, lo = np.maximum(o, c) * 1.002, np.minimum(o, c) * 0.998
            n = k.size
            frames.append(pd.DataFrame({
                "ts": np.repeat(ts, len(OFFSETS)), "date": d, "offset": np.tile(OFFSETS, BARS),
                "opt_type": opt, "open": o.ravel().round(2), "high": hi.ravel().round(2),
                "low": lo.ravel().round(2), "close": c.ravel().round(2),
                "volume": rng.integers(100, 10000, n), "oi": 0.0, "iv": VOL * 100,
                "strike": k.ravel(), "spot": np.repeat(s_close, len(OFFSETS)).round(2),
                "expiry": row["expiry"], "dte": (row["expiry"] - d).days, "lot_size": row["lot_size"]}))
    df = pd.concat(frames, ignore_index=True)
    df.to_parquet(a.out, index=False)
    print(f"{inr(len(df))} rows, {len(days)} days -> {a.out}")


if __name__ == "__main__":
    main()
