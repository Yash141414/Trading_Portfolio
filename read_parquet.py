"""
Read parquet files produced by data.py (or any parquet file/folder).
With no path it reads the combined file data/nifty_opt_bhavcopy.parquet.

  python read_parquet.py                                            # combined file
  python read_parquet.py --start 2026-01-01 --end 2026-03-31        # combined file, date range
  python read_parquet.py data/bhavcopy/nifty_opt_20250929.parquet   # one daily file
  python read_parquet.py data/bhavcopy --raw --rows 20              # daily files, NSE column names

Rebuild the combined file (and CSV) after new daily files arrive:
  python read_parquet.py data/bhavcopy --rows 0 --out data/nifty_opt_bhavcopy.parquet
  python read_parquet.py data/bhavcopy --rows 0 --out data/nifty_opt_bhavcopy.csv

From Python:
  from read_parquet import load
  df = load(start="2026-01-01")
"""
import argparse
import re
from datetime import date
from pathlib import Path

import pandas as pd
from backtest.metrics import inr

# NSE UDiFF bhavcopy columns -> readable names. Columns not listed keep their name.
RENAME = {
    "TradDt": "date", "TckrSymb": "symbol", "XpryDt": "expiry",
    "StrkPric": "strike", "OptnTp": "opt_type",
    "OpnPric": "open", "HghPric": "high", "LwPric": "low", "ClsPric": "close",
    "LastPric": "ltp", "PrvsClsgPric": "prev_close", "SttlmPric": "settle",
    "UndrlygPric": "spot", "OpnIntrst": "oi", "ChngInOpnIntrst": "oi_chg",
    "TtlTradgVol": "volume", "TtlTrfVal": "turnover",
    "TtlNbOfTxsExctd": "trades", "NewBrdLotQty": "lot_size",
    "FinInstrmNm": "instrument",
}
COMBINED = Path("data/nifty_opt_bhavcopy.parquet")
DATE_COLS = ["date", "expiry", "BizDt", "FininstrmActlXpryDt"]
FILE_DATE = re.compile(r"(\d{8})")


def _file_date(p: Path) -> date | None:
    m = FILE_DATE.search(p.stem)
    return pd.to_datetime(m.group(1), format="%Y%m%d").date() if m else None


def list_files(path: str | Path, start=None, end=None) -> list[Path]:
    """Parquet files under `path` (file, folder, or glob), filtered by the YYYYMMDD in the name."""
    path = Path(path)
    if path.is_file():
        files = [path]
    elif path.is_dir():
        files = sorted(path.glob("*.parquet"))
    else:
        files = sorted(Path().glob(str(path)))
    start = pd.Timestamp(start).date() if start else None
    end = pd.Timestamp(end).date() if end else None
    if start or end:
        def keep(f):
            d = _file_date(f)
            return d is None or ((not start or d >= start) and (not end or d <= end))
        files = [f for f in files if keep(f)]
    return files


def load(path: str | Path = COMBINED, start=None, end=None, raw: bool = False,
         columns: list[str] | None = None) -> pd.DataFrame:
    """Load one or many parquet files into a single DataFrame.

    raw=False drops all-empty columns, renames NSE columns and parses dates.
    start/end skip daily files by filename, then filter rows on `date`.
    `columns` limits what is read from disk (NSE names for daily files,
    readable names for the combined file).
    """
    files = list_files(path, start, end)
    if not files:
        raise FileNotFoundError(f"no parquet files matched {path}")
    df = pd.concat((pd.read_parquet(f, columns=columns) for f in files), ignore_index=True)
    if raw:
        return df
    df = df.dropna(axis=1, how="all").rename(columns=RENAME)
    for c in DATE_COLS:
        if c in df:
            df[c] = pd.to_datetime(df[c])
    if "date" in df and (start or end):   # combined file has no date in its name
        df = df[df["date"].between(pd.Timestamp(start or df["date"].min()),
                                   pd.Timestamp(end or df["date"].max()))]
        df = df.reset_index(drop=True)
    return df


def main():
    """Command-line entry point: load, preview and optionally save the combined bhavcopy."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", default=str(COMBINED),
                    help=f"parquet file, folder, or glob (default: {COMBINED})")
    ap.add_argument("--start", help="first date (YYYY-MM-DD)")
    ap.add_argument("--end", help="last date (YYYY-MM-DD)")
    ap.add_argument("--raw", action="store_true", help="keep original columns/names")
    ap.add_argument("--rows", type=int, default=10, help="rows to preview")
    ap.add_argument("--out", help="write combined frame to .parquet or .csv")
    a = ap.parse_args()

    files = list_files(a.path, a.start, a.end)
    df = load(a.path, a.start, a.end, raw=a.raw)

    pd.set_option("display.width", 200, "display.max_columns", 30)
    print(f"{len(files)} file(s), {inr(len(df))} rows x {df.shape[1]} cols, "
          f"{df.memory_usage(deep=True).sum() / 1e6:.1f} MB in memory\n")
    print(df.dtypes.to_string(), "\n")
    if "date" in df:
        print(f"dates: {df['date'].min():%Y-%m-%d} -> {df['date'].max():%Y-%m-%d} "
              f"({df['date'].nunique()} trading days)\n")
    print(df.head(a.rows).to_string())

    if a.out:
        out = Path(a.out)
        df.to_csv(out, index=False) if out.suffix == ".csv" else df.to_parquet(out, index=False)
        print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
