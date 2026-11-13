"""
Download 1 year of 1-minute Nifty option data (ATM-10 .. ATM+10, CE + PE)
from Dhan's Expired Options Data API (POST /v2/charts/rollingoption).

Requires an active Dhan Data API subscription.
  export DHAN_ACCESS_TOKEN=...      # regenerate daily; this is the only credential this endpoint needs
  pip install requests pandas pyarrow
  python dhan_nifty_options_1y.py --days 365 --expiry WEEK

DhanHQ's docs (https://dhanhq.co/docs/v2/expired-options-data/) list only the access-token header
for POST /v2/charts/rollingoption -- client-id/dhanClientId is not part of this endpoint's request;
it's only required for a few unrelated endpoints (token renewal, IP whitelisting).

Output (--out, default data/dhan):
  <out>/raw/<side>_<strike>_<from>.parquet   (one per API call, resumable)
  <out>/nifty_options_1min.parquet           (combined)
"""
import argparse
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
from backtest.metrics import inr

URL = "https://api.dhan.co/v2/charts/rollingoption"
NIFTY_SECURITY_ID = "13"
FIELDS = ["open", "high", "low", "close", "volume", "oi", "iv", "strike", "spot"]
CHUNK_DAYS = 28            # API allows up to 30; toDate is non-inclusive
    

def strike_labels(n: int = 10):
    """Dhan strike labels from ATM-n to ATM+n, e.g. ['ATM-1', 'ATM', 'ATM+1']."""
    return [f"ATM-{i}" for i in range(n, 0, -1)] + ["ATM"] + [f"ATM+{i}" for i in range(1, n + 1)]


def date_chunks(start: date, end: date):
    """Split [start, end) into windows of at most CHUNK_DAYS (the API's per-call limit)."""
    d = start
    while d < end:
        e = min(d + timedelta(days=CHUNK_DAYS), end)
        yield d, e
        d = e


def fetch(sess, strike, side, frm, to, expiry_flag, expiry_code, retries=4):
    """POST one rolling-option request; retry with backoff; exit on auth errors; None if all retries fail."""
    body = {
        "exchangeSegment": "NSE_FNO",
        "interval": "1",
        "securityId": NIFTY_SECURITY_ID,
        "instrument": "OPTIDX",
        "expiryFlag": expiry_flag,
        "expiryCode": expiry_code,
        "strike": strike,
        "drvOptionType": side,
        "requiredData": FIELDS,
        "fromDate": frm.isoformat(),
        "toDate": to.isoformat(),
    }
    for attempt in range(retries):
        try:
            r = sess.post(URL, json=body, timeout=60)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (401, 403):
                sys.exit(f"Auth failed ({r.status_code}): token expired or no Data API plan. {r.text[:200]}")
            print(f"  HTTP {r.status_code}: {r.text[:200]}")
        except requests.RequestException as e:
            print(f"  error: {e}")
        time.sleep(2 ** attempt)
    return None


def to_frame(payload, strike_label, side):
    """API JSON for one strike label and side -> DataFrame with an IST timestamp column `ts`."""
    key = "ce" if side == "CALL" else "pe"
    block = (payload or {}).get("data", {}).get(key)
    if not block or not block.get("timestamp"):
        return pd.DataFrame()
    df = pd.DataFrame({k: v for k, v in block.items() if isinstance(v, list) and v})
    df["ts"] = pd.to_datetime(df.pop("timestamp"), unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    df["strike_label"] = strike_label
    df["type"] = "CE" if side == "CALL" else "PE"
    return df


def main():
    """Download every (strike label, side, window) not already on disk, then combine and check completeness."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--expiry", choices=["WEEK", "MONTH"], default="WEEK")
    ap.add_argument("--expiry-code", type=int, default=1, help="1 = nearest expiry")
    ap.add_argument("--strikes", type=int, default=10, help="ATM +/- N (max 10 for index near expiry)")
    ap.add_argument("--sleep", type=float, default=0.3)
    ap.add_argument("--out", type=Path, default=Path("data/dhan"))
    ap.add_argument("--start", help="YYYY-MM-DD; with --end, overrides --days")
    ap.add_argument("--end", help="YYYY-MM-DD, exclusive (default: today)")
    a = ap.parse_args()
    out, raw = a.out, a.out / "raw"

    tok = os.getenv("DHAN_ACCESS_TOKEN")
    if not tok:
        sys.exit("Set DHAN_ACCESS_TOKEN.")
    sess = requests.Session()
    sess.headers.update({"access-token": tok,
                         "Content-Type": "application/json", "Accept": "application/json"})

    raw.mkdir(parents=True, exist_ok=True)
    end = date.fromisoformat(a.end) if a.end else date.today()
    start = date.fromisoformat(a.start) if a.start else end - timedelta(days=a.days)
    jobs = [(s, side, f, t) for s in strike_labels(a.strikes) for side in ("CALL", "PUT")
            for f, t in date_chunks(start, end)]
    print(f"{len(jobs)} requests")

    for i, (strike, side, frm, to) in enumerate(jobs, 1):
        f = raw / f"{side}_{strike}_{frm:%Y%m%d}.parquet"
        if f.exists():
            continue
        df = to_frame(fetch(sess, strike, side, frm, to, a.expiry, a.expiry_code), strike, side)
        df.to_parquet(f, index=False)   # write even if empty so reruns skip it
        print(f"[{i}/{len(jobs)}] {side} {strike} {frm}->{to}: {len(df)} rows")
        time.sleep(a.sleep)

    parts = [pd.read_parquet(p) for p in raw.glob("*.parquet")]
    parts = [p for p in parts if not p.empty]
    if not parts:
        sys.exit("No data returned.")
    full = (pd.concat(parts).drop_duplicates(["ts", "strike_label", "type"])
            .sort_values(["ts", "type", "strike_label"]))
    full.to_parquet(out / "nifty_options_1min.parquet", index=False)

    # Completeness check: a full NSE session is 375 one-minute bars
    per_day = full.groupby([full["ts"].dt.date, "strike_label", "type"]).size()
    short = per_day[per_day < 370]
    print(f"\nSaved {inr(len(full))} rows across {full['ts'].dt.date.nunique()} trading days.")
    print(f"Series-days with <370 bars: {len(short)} of {len(per_day)}")
    if len(short):
        short.rename("bars").reset_index().to_csv(out / "incomplete_days.csv", index=False)
        print(f"See {out}/incomplete_days.csv — refetch or treat as gaps.")


if __name__ == "__main__":
    main()
