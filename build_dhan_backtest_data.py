"""
Wire the completed ATM+-10 Dhan download (data/final/raw/, built by
dhan_nifty_options_1y.py) into the backtest, by adding the expiry info it lacks.

That raw data has no expiry field at all (dhan_nifty_options_1y.py's rollingoption
calls didn't request one). This script reuses dhan_expired.py's infer_expiry() --
the same weekday-rule + bhavcopy-validated logic dhan_expired.py combine already
uses -- to assign each trading day its nearest weekly expiry, then writes a file
backtest/data.py's load_days() can read directly: real strike per bar plus an
`offset` column (ATM-relative, parsed from strike_label), same shape as
dhan_expired.py's own output.

This is a separate output from combine_dhan_options.py's file (which drops
strike_label/offset for the real-strike lookup/snapshot use case) -- that one is
for humans looking things up, this one is for the backtest.

  python build_dhan_backtest_data.py                       # -> data/final/nifty_opt_1min.parquet
  python run_backtest.py --data data/final/nifty_opt_1min.parquet --out results_dhan

For dates before the bhavcopy starts, --calendar takes per-date expiry and lot size from
another file instead (any parquet with date, expiry, lot_size columns, e.g. the converted
tradeable universe); days missing from it are dropped:

  python build_dhan_backtest_data.py --raw data/dhan_2023_2025/raw --out data/dhan_2023_2025/nifty_opt_1min.parquet
      --calendar data/universe/nifty_opt_1min_universe.parquet   (one command)
"""
import argparse
import re
from pathlib import Path

import pandas as pd

from dhan_expired import THURSDAY_LAST, _validate_expiry, infer_expiry
from backtest.metrics import inr

RAW = Path("data/final/raw")
OUT = Path("data/final/nifty_opt_1min.parquet")
BHAVCOPY = Path("data/nifty_opt_bhavcopy.parquet")


def _offset(label: str) -> int:
    if label == "ATM":
        return 0
    m = re.match(r"ATM([+-]\d+)$", label)
    if not m:
        raise ValueError(f"unrecognised strike label: {label!r}")
    return int(m.group(1))


def build(raw: Path = RAW, out: Path = OUT, calendar: Path | None = None) -> None:
    """Combine the raw Dhan chunks, add offset/expiry/dte, and write the backtest input file."""
    files = sorted(raw.glob("*.parquet"))
    if not files:
        raise SystemExit(f"no chunks in {raw} -- run dhan_nifty_options_1y.py first")
    parts = [pd.read_parquet(f, columns=["ts", "strike", "strike_label", "type", "open", "close", "spot", "oi"])
             for f in files]
    parts = [p for p in parts if not p.empty]
    df = (pd.concat(parts, ignore_index=True)
          .drop_duplicates(["ts", "strike_label", "type"])
          .rename(columns={"type": "opt_type"}))
    df["ts"] = df["ts"].dt.tz_localize(None)
    df["offset"] = df["strike_label"].map(_offset)
    df = df.drop(columns=["strike_label"])
    df.insert(1, "date", df["ts"].dt.normalize())
    print(f"{len(files)} chunks -> {inr(len(df))} rows, {df['date'].nunique()} trading days")

    if calendar is not None:
        cal = (pd.read_parquet(calendar, columns=["date", "expiry", "lot_size"])
               .groupby("date").agg(expiry=("expiry", "min"), lot_size=("lot_size", "first")))
        cal.index = pd.to_datetime(cal.index)
        cal["expiry"] = pd.to_datetime(cal["expiry"])
        missing = sorted(set(df["date"].unique()) - set(cal.index))
        for d in missing:
            print(f"drop {pd.Timestamp(d).date()}: not in {calendar}")
        df = df[df["date"].isin(cal.index)]
        df["expiry"] = df["date"].map(cal["expiry"])
        df["lot_size"] = df["date"].map(cal["lot_size"]).astype(int)
        df["dte"] = (df["expiry"] - df["date"]).dt.days
        print(f"expiry and lot size from {calendar}: {df['date'].nunique()} days kept, {len(missing)} dropped")
        _write(df, out)
        return

    days = pd.DatetimeIndex(df["date"].unique())
    true = pd.Series(dtype="datetime64[ns]")
    if BHAVCOPY.exists():
        true = pd.read_parquet(BHAVCOPY, columns=["date", "expiry"]).groupby("date")["expiry"].min()
        days = days.union(true.index)
    exp = infer_expiry(days)
    if len(true):
        _validate_expiry(exp, true)
        exp.update(true)
    df["expiry"] = df["date"].map(exp)
    df["dte"] = (df["expiry"] - df["date"]).dt.days
    n_true = df["date"].isin(true.index).groupby(df["date"]).first().sum()
    print(f"expiry: {n_true} days from bhavcopy, {df['date'].nunique() - n_true} from weekday rule "
          f"(THURSDAY_LAST={THURSDAY_LAST.date()})")

    _write(df, out)


def _write(df: pd.DataFrame, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    print(f"wrote {inr(len(df))} rows -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", type=Path, default=RAW)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--calendar", type=Path, help="parquet with date, expiry, lot_size (overrides bhavcopy/weekday rule)")
    a = ap.parse_args()
    build(a.raw, a.out, a.calendar)
