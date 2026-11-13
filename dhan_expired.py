"""
Download 1-minute expired NIFTY options data from DhanHQ v2 (/charts/rollingoption).

Dhan stores expired options "rolling": each series is a strike offset from ATM
(ATM, ATM+1, ATM-1, ...) and the actual strike is returned per bar, so one series
switches contracts as spot moves. Up to ATM+-10 for index options, 5 years back,
30 days per call, 5 data calls/sec, 100k/day.

  set DHAN_CLIENT_ID=...  &  set DHAN_ACCESS_TOKEN=...     (PowerShell: $env:DHAN_CLIENT_ID="...")

  python dhan_expired.py check                      # which expiryCode is the nearest expiry? (vs bhavcopy)
  python dhan_expired.py check --date 2026-09-22
  python dhan_expired.py download --years 5         # ATM+-10, CE+PE, 1-min, one parquet per 30-day window
  python dhan_expired.py download --start 2026-01-01 --strikes 5 --expiry-code 1
  python dhan_expired.py combine                    # -> data/nifty_opt_1min.parquet, with inferred expiry

Downloads resume: finished windows in data/dhan/ are skipped.
"""
import argparse
import logging
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

URL = "https://api.dhan.co/v2/charts/rollingoption"
NIFTY_ID = 13                    # NIFTY 50 index security id
OUT = Path("data/dhan")
COMBINED = Path("data/nifty_opt_1min.parquet")
BHAVCOPY = Path("data/nifty_opt_bhavcopy.parquet")
FIELDS = ["open", "high", "low", "close", "volume", "oi", "iv", "strike", "spot"]
WINDOW = 30                      # max days per call; toDate is exclusive
MIN_GAP = 0.25                   # seconds between calls (limit is 5/sec)
# NIFTY weekly expiry moved from Thursday to Tuesday for contracts after this date
THURSDAY_LAST = pd.Timestamp("2025-08-28")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("dhan")


class DhanError(Exception):
    """Raised when a Dhan API call fails in a way retrying won't fix."""
    pass


class Client:
    """Minimal Dhan API client with rate limiting and retries (needs DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN)."""
    def __init__(self):
        cid, tok = os.environ.get("DHAN_CLIENT_ID"), os.environ.get("DHAN_ACCESS_TOKEN")
        if not cid or not tok:
            sys.exit("set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN environment variables")
        self.s = requests.Session()
        self.s.headers.update({"access-token": tok, "client-id": cid,
                               "Content-Type": "application/json", "Accept": "application/json"})
        self.cid = cid
        self.last_call = 0.0

    def rolling(self, start: date, end: date, offset: int, opt: str, flag: str,
                code: int, interval: int) -> pd.DataFrame:
        """One series: bars in [start, end) for strike ATM+offset, opt 'CALL'/'PUT'."""
        body = {
            "dhanClientId": self.cid, "exchangeSegment": "NSE_FNO", "securityId": NIFTY_ID,
            "instrument": "OPTIDX", "expiryFlag": flag, "expiryCode": code,
            "strike": "ATM" if offset == 0 else f"ATM{offset:+d}", "drvOptionType": opt,
            "requiredData": FIELDS, "interval": interval,
            "fromDate": f"{start:%Y-%m-%d}", "toDate": f"{end:%Y-%m-%d}",
        }
        for attempt in range(6):
            time.sleep(max(0.0, self.last_call + MIN_GAP - time.time()))
            self.last_call = time.time()
            try:
                r = self.s.post(URL, json=body, timeout=60)
            except requests.RequestException as e:
                log.warning("network error %s, retrying", e)
                time.sleep(2 ** attempt)
                continue
            j = r.json() if r.content else {}
            err = str(j.get("errorCode") or j.get("status") or "")
            if r.status_code == 429 or err in ("DH-904", "805"):
                log.warning("rate limited, backing off")
                time.sleep(5 * 2 ** attempt)
                continue
            if r.status_code >= 500 or err in ("DH-908", "DH-909", "800"):
                time.sleep(2 ** attempt)
                continue
            if err == "DH-907":                       # no data (holidays, before history starts)
                return pd.DataFrame()
            if r.status_code != 200:
                raise DhanError(f"HTTP {r.status_code}: {j or r.text[:300]}")
            return _frame(j, offset, opt)
        raise DhanError(f"gave up after retries: {body}")


def _frame(j: dict, offset: int, opt: str) -> pd.DataFrame:
    d = (j.get("data") or {}).get("ce" if opt == "CALL" else "pe") or {}
    if not d.get("timestamp"):
        return pd.DataFrame()
    df = pd.DataFrame({k: d[k] for k in ["timestamp", *FIELDS] if d.get(k)})
    df.insert(0, "ts", pd.to_datetime(df.pop("timestamp"), unit="s", utc=True)
              .dt.tz_convert("Asia/Kolkata").dt.tz_localize(None))
    df.insert(1, "offset", offset)
    df.insert(2, "opt_type", "CE" if opt == "CALL" else "PE")
    return df


def windows(start: date, end: date):
    """Split [start, end) into windows of at most WINDOW days."""
    while start < end:
        stop = min(start + timedelta(days=WINDOW), end)
        yield start, stop
        start = stop


# ---- download ---------------------------------------------------------------

def run_download(a) -> None:
    """`download` command: fetch every window for ATM+-N, CE and PE, one parquet per window."""
    c = Client()
    OUT.mkdir(parents=True, exist_ok=True)
    today = date.today()
    start = pd.Timestamp(a.start).date() if a.start else today - timedelta(days=int(365.25 * a.years))
    end = pd.Timestamp(a.end).date() if a.end else today + timedelta(days=1)
    offsets = range(-a.strikes, a.strikes + 1)
    tag = f"nifty_{a.expiry_flag.lower()}{a.expiry_code}_{a.interval}m"
    todo = list(windows(start, end))
    log.info("%d windows x %d series = %d calls", len(todo), 2 * len(offsets), len(todo) * 2 * len(offsets))

    for w0, w1 in todo:
        f = OUT / f"{tag}_{w0:%Y%m%d}.parquet"
        complete = w1 <= today                    # windows reaching today get refetched next run
        if f.exists() and complete:
            continue
        frames = []
        for opt in ("CALL", "PUT"):
            for off in offsets:
                frames.append(c.rolling(w0, w1, off, opt, a.expiry_flag, a.expiry_code, a.interval))
        df = pd.concat([x for x in frames if len(x)], ignore_index=True) if any(len(x) for x in frames) else None
        if df is None:
            log.info("%s -> %s: no data", w0, w1)
            continue
        df.to_parquet(f, index=False)
        log.info("%s -> %s: %d rows, %d days -> %s", w0, w1, len(df), df["ts"].dt.date.nunique(), f.name)


# ---- combine ----------------------------------------------------------------

def infer_expiry(days: pd.DatetimeIndex) -> pd.Series:
    """Nearest weekly expiry for each trading day.

    Scheduled weekday is Thursday up to THURSDAY_LAST, Tuesday after. If that day
    is a holiday (not in `days`) the expiry moves back to the previous trading day.
    Days after the last known trading day can't be holiday-checked and stay as scheduled.
    """
    days = pd.DatetimeIndex(sorted(set(days)))
    out = {}
    for d in days:
        thu = d + pd.Timedelta(days=(3 - d.weekday()) % 7)
        e = thu if thu <= THURSDAY_LAST else d + pd.Timedelta(days=(1 - d.weekday()) % 7)
        if e <= days[-1]:
            e = days[days <= e][-1]
        out[d] = e
    return pd.Series(out)


def run_combine(a) -> None:
    """`combine` command: merge downloaded windows and add expiry (bhavcopy, else weekday rule) and dte."""
    files = sorted(OUT.glob(f"nifty_{a.expiry_flag.lower()}{a.expiry_code}_{a.interval}m_*.parquet"))
    if not files:
        sys.exit(f"no files in {OUT} - run download first")
    df = (pd.concat(map(pd.read_parquet, files), ignore_index=True)
          .drop_duplicates(["ts", "offset", "opt_type"], keep="last")
          .sort_values(["ts", "opt_type", "offset"], ignore_index=True))
    df.insert(1, "date", df["ts"].dt.normalize())

    if a.expiry_flag == "WEEK":
        days = pd.DatetimeIndex(df["date"].unique())
        true = pd.Series(dtype="datetime64[ns]")
        if BHAVCOPY.exists():                     # real expiries where we have them
            true = pd.read_parquet(BHAVCOPY, columns=["date", "expiry"]).groupby("date")["expiry"].min()
            days = days.union(true.index)
        exp = infer_expiry(days)
        if len(true):
            _validate_expiry(exp, true)
            exp.update(true)
        df["expiry"] = df["date"].map(exp)
        df["dte"] = (df["expiry"] - df["date"]).dt.days
        n_true = df["date"].isin(true.index).groupby(df["date"]).first().sum()
        log.info("expiry: %d days from bhavcopy, %d from weekday rule", n_true, df["date"].nunique() - n_true)

    df.to_parquet(COMBINED, index=False)
    log.info("%d files -> %s: %d rows, %d days (%s -> %s)", len(files), COMBINED, len(df),
             df["date"].nunique(), df["date"].min().date(), df["date"].max().date())


def _validate_expiry(exp: pd.Series, true: pd.Series) -> None:
    """How well the weekday rule (used before the bhavcopy starts) matches real expiries."""
    bad = true.index[exp.reindex(true.index).values != true.values]
    log.info("weekday rule vs bhavcopy: %d/%d days match (bhavcopy used where available)",
             len(true) - len(bad), len(true))
    for d in bad[:10]:
        log.info("  %s rule %s, actual %s", d.date(), exp[d].date(), true[d].date())


# ---- check ------------------------------------------------------------------

def run_check(a) -> None:
    """Fetch ATM CALL for one day with expiryCode 0/1/2 and match to bhavcopy contracts."""
    c = Client()
    b = pd.read_parquet(BHAVCOPY) if BHAVCOPY.exists() else None
    d = pd.Timestamp(a.date) if a.date else None
    if d is None:
        if b is None:
            sys.exit("pass --date")
        near = b.groupby("date")["expiry"].min()
        d = near[(near - near.index).dt.days >= 2].index[-1]     # a recent non-expiry day
    log.info("checking %s", d.date())

    for code in (0, 1, 2):
        try:
            x = c.rolling(d.date(), d.date() + timedelta(days=1), 0, "CALL", "WEEK", code, 1)
        except DhanError as e:
            print(f"expiryCode {code}: error {e}")
            continue
        if x.empty:
            print(f"expiryCode {code}: no data")
            continue
        last = x.iloc[-1]
        print(f"\nexpiryCode {code}: {len(x)} bars {x['ts'].min():%H:%M}-{x['ts'].max():%H:%M}, "
              f"last bar strike {last['strike']:.0f} close {last['close']:.2f} spot {last['spot']:.2f}")
        if b is not None:
            m = b[(b["date"] == d) & (b["strike"] == last["strike"]) & (b["opt_type"] == "CE")]
            m = m.assign(diff=(m["ltp"] - last["close"]).abs()).sort_values("expiry")
            print("  bhavcopy CE at that strike:")
            print(m[["expiry", "ltp", "close", "diff"]].head(4).to_string(index=False))
    print("\nThe expiryCode whose last close matches the NEAREST expiry's ltp is the one to download.")


def main():
    """Command-line entry point: check / download / combine."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("download", "combine"):
        p = sub.add_parser(name)
        p.add_argument("--expiry-flag", default="WEEK", choices=["WEEK", "MONTH"])
        p.add_argument("--expiry-code", type=int, default=1, choices=[0, 1, 2],
                       help="run `check` to confirm which code is the nearest expiry")
        p.add_argument("--interval", type=int, default=1, choices=[1, 5, 15, 25, 60])
        if name == "download":
            p.add_argument("--years", type=float, default=5)
            p.add_argument("--start", help="YYYY-MM-DD (overrides --years)")
            p.add_argument("--end", help="YYYY-MM-DD, exclusive (default: today)")
            p.add_argument("--strikes", type=int, default=10, help="ATM +- N (max 10)")
    p = sub.add_parser("check")
    p.add_argument("--date", help="YYYY-MM-DD inside the bhavcopy range")
    a = ap.parse_args()
    if a.cmd == "download" and not 0 <= a.strikes <= 10:
        ap.error("--strikes must be 0..10")
    {"download": run_download, "combine": run_combine, "check": run_check}[a.cmd](a)


if __name__ == "__main__":
    main()
