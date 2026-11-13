"""
NSE Nifty options data collector.

  python data.py snapshot                 # log live chain every 60s (run daily via cron/Task Scheduler)
  python data.py snapshot --expiries 3    # also poll the 3 nearest expiries (default 2)
  python data.py bhavcopy --days 365      # backfill EOD Nifty options from NSE archives

NOTE: NSE does not serve historical intraday option-chain data. `snapshot` builds
it forward in time. Endpoints below are unofficial and change without notice.

pip install requests pandas pyarrow tzdata
"""
import argparse
import io
import logging
import random
import sys
import time
import zipfile
from datetime import date, datetime, timedelta, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

IST = ZoneInfo("Asia/Kolkata")
OUT = Path("data")
BASE = "https://www.nseindia.com"
# The old /api/option-chain-indices endpoint now 404s. v3 returns {} unless an
# expiry (e.g. 29-Sep-2026) is given; the expiry list comes from contract-info.
EXPIRY_URL = BASE + "/api/option-chain-contract-info?symbol=NIFTY"
CHAIN_URL = BASE + "/api/option-chain-v3?type=Indices&symbol=NIFTY&expiry={expiry}"
# UDiFF bhavcopy format (NSE switched to this in 2024)
BHAV_URL = ("https://nsearchives.nseindia.com/content/fo/"
            "BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip")
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE + "/option-chain",
}
MARKET_OPEN, MARKET_CLOSE = dtime(9, 15), dtime(15, 30)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("nse")


def new_session() -> requests.Session:
    """Requests session with browser-like headers and NSE cookies (NSE rejects bare clients)."""
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get(BASE, timeout=15)                 # sets Akamai cookies
    s.get(BASE + "/option-chain", timeout=15)
    return s


def fetch_expiries(s: requests.Session) -> list[str]:
    """Current NIFTY option expiry dates from NSE's live option-chain endpoint."""
    r = s.get(EXPIRY_URL, timeout=15)
    r.raise_for_status()
    return r.json()["expiryDates"]


def fetch_chain(s: requests.Session, expiry: str) -> dict:
    """Live NIFTY option chain (JSON) for one expiry."""
    url = CHAIN_URL.format(expiry=expiry)
    r = s.get(url, timeout=15)
    if r.status_code == 200 and r.text.strip().startswith("{"):
        j = r.json()
        if j.get("records", {}).get("data"):
            return j
    raise RuntimeError(f"{url} -> HTTP {r.status_code}, body {r.text[:80]!r}")


def flatten(j: dict, polled_at: datetime) -> pd.DataFrame:
    """Option-chain JSON -> one row per strike and option type, stamped with the poll time."""
    rec = j["records"]
    rows = []
    for item in rec["data"]:
        for side in ("CE", "PE"):
            leg = item.get(side)
            if not leg:
                continue
            rows.append({
                "polled_at": polled_at,
                "nse_timestamp": rec.get("timestamp"),
                "underlying": rec.get("underlyingValue"),
                "expiry": leg.get("expiryDate") or item.get("expiryDate"),
                "strike": leg.get("strikePrice"),
                "type": side,
                "ltp": leg.get("lastPrice"),
                "iv": leg.get("impliedVolatility"),
                "oi": leg.get("openInterest"),
                "chg_oi": leg.get("changeinOpenInterest"),
                "volume": leg.get("totalTradedVolume"),   # cumulative for the day
                "bid": leg.get("buyPrice1"),
                "bid_qty": leg.get("buyQuantity1"),
                "ask": leg.get("sellPrice1"),
                "ask_qty": leg.get("sellQuantity1"),
            })
    return pd.DataFrame(rows)


def run_snapshot(interval: int = 60, n_expiries: int = 2) -> None:
    """Poll the live chain every `interval` seconds during market hours; save the day to one parquet (checkpointed every ~10 polls)."""
    today = datetime.now(IST).date()
    if today.weekday() >= 5:
        log.info("Weekend, exiting."); return
    out_dir = OUT / "snapshots"; out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"nifty_chain_{today:%Y%m%d}.parquet"
    frames, s, fails = [], new_session(), 0

    while True:
        now = datetime.now(IST)
        if now.time() < MARKET_OPEN:
            time.sleep(min(30, (datetime.combine(today, MARKET_OPEN, IST) - now).total_seconds())); continue
        if now.time() > MARKET_CLOSE:
            break
        try:
            expiries = fetch_expiries(s)[:n_expiries]
            df = pd.concat([flatten(fetch_chain(s, e), now) for e in expiries], ignore_index=True)
            frames.append(df); fails = 0
            log.info("rows=%d expiries=%s nse_ts=%s", len(df), expiries, df["nse_timestamp"].iat[0])
            if len(frames) % 10 == 0:          # checkpoint every ~10 polls
                pd.concat(frames).to_parquet(out_file, index=False)
        except Exception as e:
            fails += 1
            log.warning("fetch failed (%d): %s", fails, e)
            time.sleep(min(300, 5 * 2 ** fails))
            try:
                s = new_session()
            except requests.RequestException:
                pass
            continue
        # align to the next interval boundary with a little jitter
        time.sleep(interval - time.time() % interval + random.uniform(0, 3))

    if frames:
        pd.concat(frames).to_parquet(out_file, index=False)
        log.info("Saved %s", out_file)


def run_bhavcopy(days: int) -> None:
    """Download the last `days` of NSE F&O bhavcopies and keep NIFTY options, one parquet per day."""
    out_dir = OUT / "bhavcopy"; out_dir.mkdir(parents=True, exist_ok=True)
    s = new_session()
    end = datetime.now(IST).date()
    d = end - timedelta(days=days)
    while d <= end:
        f = out_dir / f"nifty_opt_{d:%Y%m%d}.parquet"
        if d.weekday() < 5 and not f.exists():
            try:
                r = s.get(BHAV_URL.format(d=d), timeout=30)
                if r.status_code == 200:
                    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                        df = pd.read_csv(z.open(z.namelist()[0]))
                    # UDiFF columns: TckrSymb, FinInstrmTp (IDO = index option), XpryDt, StrkPric, OptnTp ...
                    df = df[(df["TckrSymb"] == "NIFTY") & (df["FinInstrmTp"] == "IDO")]
                    df.to_parquet(f, index=False)
                    log.info("%s rows=%d", d, len(df))
                else:
                    log.info("%s no file (HTTP %d, likely holiday)", d, r.status_code)
            except Exception as e:
                log.warning("%s failed: %s", d, e)
            time.sleep(random.uniform(1, 2.5))  # be polite
        d += timedelta(days=1)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["snapshot", "bhavcopy"])
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--expiries", type=int, default=2)
    a = p.parse_args()
    run_snapshot(a.interval, a.expiries) if a.mode == "snapshot" else run_bhavcopy(a.days)