"""
Test the current (frozen) strategies on the newest data, in four steps:

  1. download: NSE bhavcopy (lot sizes, real expiries) + Dhan 1-min ATM+-10 options with OI
     from --start to --end into data/latest/raw/ (data/final/ is never touched)
  2. build:    data/latest/nifty_opt_1min.parquet (backtest-ready), check it against
     data/final/ on the overlapping days, and write data/latest/nifty_opt_1min_extended.parquet
     (= data/final before --start + the new download)
  3. backtest: results_latest/new_days/      only days after the last tested day (2026-09-25)
               results_latest/extended_year/ the test year 2025-09-30 -> newest day
  4. register: print both results and add them to test_done.txt

  python run_latest.py                         # token from DHAN_ACCESS_TOKEN or .env, else prompted
  python run_latest.py --token <token>         # pass it directly (add --save-token to write it to .env)
  python run_latest.py --skip-download         # rerun steps 2-4 on what is already downloaded
  python run_latest.py --robustness            # also run the robustness grid on the extended year (~2 min)

Dhan access tokens expire; a 401 / DH-901 error means a new one is needed.
"""
import argparse
import getpass
import os
import re
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from build_dhan_backtest_data import build

PY = sys.executable
ENV = Path(".env")
FINAL = Path("data/final/nifty_opt_1min.parquet")
LAST_TESTED = pd.Timestamp("2026-09-25")      # last day of the submitted test year (T10)
TEST_START = "2025-09-30"                     # first day of the submitted test year
REGISTER = Path("test_done.txt")


def token_from(arg: str | None) -> str:
    """The Dhan token: --token, then the environment, then .env, then a hidden prompt."""
    if arg:
        return arg
    if os.getenv("DHAN_ACCESS_TOKEN"):
        return os.environ["DHAN_ACCESS_TOKEN"]
    if ENV.exists():
        m = re.search(r'^DHAN_ACCESS_TOKEN\s*=\s*"?([^"\s]+)"?\s*$', ENV.read_text(), re.M)
        if m:
            return m.group(1)
    tok = getpass.getpass("Dhan access token: ").strip()
    if not tok:
        sys.exit("No token given.")
    return tok


def save_token(tok: str) -> None:
    """Write the token into .env, replacing any existing DHAN_ACCESS_TOKEN line."""
    text = ENV.read_text() if ENV.exists() else ""
    line = f'DHAN_ACCESS_TOKEN="{tok}"'
    text, n = re.subn(r"^DHAN_ACCESS_TOKEN\s*=.*$", line, text, flags=re.M)
    ENV.write_text(text if n else text.rstrip("\n") + ("\n" if text else "") + line + "\n")
    print(f"token saved to {ENV}")


def sh(*args: str, env: dict | None = None) -> None:
    """Run a project script with this interpreter; stop on failure."""
    print(f"\n$ python {' '.join(args)}")
    r = subprocess.run([PY, *args], env=env)
    if r.returncode:
        sys.exit(f"failed: python {' '.join(args)} (exit {r.returncode})")


def download(start: date, end: date, out: Path, tok: str) -> None:
    """Step 1: bhavcopy for lot sizes and expiries, then the Dhan 1-minute options."""
    print("=" * 70 + "\nSTEP 1  download")
    sh("data.py", "bhavcopy", "--days", str((date.today() - start).days + 5))   # skips files already there
    sh("read_parquet.py", "data/bhavcopy", "--rows", "0", "--out", "data/nifty_opt_bhavcopy.parquet")
    # Dhan chunks are cached by start date, so a chunk from an earlier, shorter run would be reused
    # and miss the newer days: always start from an empty raw folder (only ~42 calls).
    raw = out / "raw"
    if raw.exists():
        shutil.rmtree(raw)
    sh("dhan_nifty_options_1y.py", "--start", f"{start}", "--end", f"{end}", "--strikes", "10",
       "--out", str(out), env=os.environ | {"DHAN_ACCESS_TOKEN": tok})


def check_overlap(new: pd.DataFrame) -> None:
    """Compare the new download with data/final on the days both cover."""
    old = pd.read_parquet(FINAL)
    days = sorted(set(old["date"]) & set(new["date"]))
    if not days:
        print("overlap check: no common days with data/final")
        return
    key = ["ts", "strike", "opt_type"]
    m = old[old["date"].isin(days)].merge(new[new["date"].isin(days)], on=key, suffixes=("_old", "_new"))
    n_old = old["date"].isin(days).sum()
    diff = (m["close_new"] / m["close_old"] - 1).abs()
    print(f"overlap check vs {FINAL} on {', '.join(str(d.date()) for d in days)}:")
    print(f"  {len(m):,} of {n_old:,} old bars matched on (ts, strike, type)")
    print(f"  close: median |diff| {diff.median():.2%}, 99th pct {diff.quantile(.99):.2%}, "
          f"identical {(diff == 0).mean():.1%}")
    print(f"  OI identical {(m['oi_old'] == m['oi_new']).mean():.1%}, "
          f"spot identical {(m['spot_old'] == m['spot_new']).mean():.1%}")


def build_files(out: Path) -> tuple[Path, Path]:
    """Step 2: backtest-ready new file, overlap check, and the extended test-year file."""
    print("=" * 70 + "\nSTEP 2  build")
    new_path, ext_path = out / "nifty_opt_1min.parquet", out / "nifty_opt_1min_extended.parquet"
    build(out / "raw", new_path)
    new = pd.read_parquet(new_path)
    bars = new.groupby(["date", "offset", "opt_type"]).size()
    short = bars[bars < 370]
    print(f"new data: {new['date'].min().date()} -> {new['date'].max().date()}, {new['date'].nunique()} days, "
          f"{len(short)} of {len(bars)} series-days with < 370 bars")
    if len(short):
        print(short.groupby(level="date").size().rename("short series").to_string())
    check_overlap(new)

    old = pd.read_parquet(FINAL)
    ext = pd.concat([old[old["date"] < new["date"].min()], new[old.columns]], ignore_index=True)
    ext.to_parquet(ext_path, index=False)
    print(f"extended file: {ext['date'].min().date()} -> {ext['date'].max().date()}, "
          f"{ext['date'].nunique()} days -> {ext_path}")
    return new_path, ext_path


def backtest(new_path: Path, ext_path: Path, results: Path, robustness: bool) -> dict[str, Path]:
    """Step 3: unchanged strategies on the new days alone and on the extended test year."""
    print("=" * 70 + "\nSTEP 3  backtest")
    first_new = (LAST_TESTED + timedelta(days=1)).date()
    runs = {"new_days": ["--data", str(new_path), "--start", f"{first_new}"],
            "extended_year": ["--data", str(ext_path), "--start", TEST_START] + (["--robustness"] * robustness)}
    out = {}
    for name, args in runs.items():
        out[name] = results / name
        sh("run_backtest.py", *args, "--out", str(out[name]))
    return out


def stat(folder: Path) -> pd.DataFrame:
    return pd.read_csv(folder / "stats.csv", index_col=0)


def register(out: dict[str, Path]) -> None:
    """Step 4: print the results and add one row per run to test_done.txt."""
    print("=" * 70 + "\nSTEP 4  results")
    rows = (("Sharpe", "Sharpe"), ("Sharpe 95% CI low", "CI lo"), ("Sharpe 95% CI high", "CI hi"),
            ("Total return", "Return"), ("CAGR", "CAGR"), ("Max drawdown", "MDD"), ("Calmar", "Calmar"),
            ("Trades", "Trades"), ("Win rate", "Win"))
    lines = []
    for name, folder in out.items():
        s = stat(folder)
        print(f"\n{name}  ({s.loc['Start'].iat[0]} -> {s.loc['End'].iat[0]}, {s.loc['Trading days'].iat[0]} days)")
        print(s.loc[[r for r, _ in rows]].rename(index=dict(rows)).to_string())
        c = s["Combined"]
        lines.append((folder, name, c))

    if not REGISTER.exists():
        return
    text = REGISTER.read_text(encoding="utf-8")
    ids = [int(i) for i in re.findall(r"^T(\d+)\s", text, re.M)]
    nxt = max(ids, default=0) + 1
    today = date.today().isoformat()
    new_rows = []
    for k, (folder, name, c) in enumerate(lines):
        kind = ("NEW DAYS ONLY, OUT-OF-SAMPLE" if name == "new_days"
                else "test year extended, OUT-OF-SAMPLE")
        period = f"{c['Start']} -> {c['End']}, {c['Trading days']}d"
        res = f"Return {c['Total return']}, MDD {c['Max drawdown']}, Calmar {c['Calmar']}, Sharpe {c['Sharpe']}"
        new_rows.append(f"T{nxt + k:<3d} {today}  {folder.as_posix() + '/':<45s} R4     DHAN-LATEST "
                        f"{kind}: {period}\n{'':<92s}{res}")
    anchor = "\nPer-strategy results for the main tests:"
    if anchor in text and f"{out['new_days'].as_posix()}/" not in text:
        text = text.replace(anchor, "\n" + "\n".join(new_rows) + "\n" + anchor, 1)
        text = text.replace("Data files:\n", "Data files:\n  DHAN-LATEST data/latest/ (run_latest.py), "
                            "Dhan download after the submitted test year, has OI\n", 1)
        REGISTER.write_text(text, encoding="utf-8")
        print(f"\nadded T{nxt}-T{nxt + len(new_rows) - 1} to {REGISTER}")
    else:
        print(f"\n{REGISTER} not updated (already lists these folders, or its layout changed)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2026-09-28",
                    help="first day to download (default 2026-09-28: 28/29 Sep were in data/final but untested)")
    ap.add_argument("--end", default=str(date.today()), help="exclusive (default: today, so a session still trading is left out)")
    ap.add_argument("--out", type=Path, default=Path("data/latest"))
    ap.add_argument("--results", type=Path, default=Path("results_latest"))
    ap.add_argument("--token", help="Dhan access token (default: DHAN_ACCESS_TOKEN, then .env, then prompt)")
    ap.add_argument("--save-token", action="store_true", help="write --token / the prompted token into .env")
    ap.add_argument("--skip-download", action="store_true")
    ap.add_argument("--robustness", action="store_true")
    a = ap.parse_args()

    if not a.skip_download:
        tok = token_from(a.token)
        if a.save_token:
            save_token(tok)
        download(date.fromisoformat(a.start), date.fromisoformat(a.end), a.out, tok)
    new_path, ext_path = build_files(a.out)
    register(backtest(new_path, ext_path, a.results, a.robustness))


if __name__ == "__main__":
    main()
