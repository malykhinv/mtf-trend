"""Bybit funding-rate + open-interest history fetcher (all linear USDT perps).

Writes per-symbol parquet:
  .output/market/bybit/um_futures/funding_v1/{SYMBOL}.parquet   (funding_time_ms, funding_rate)
  .output/market/bybit/um_futures/oi_{1h,1d}/{SYMBOL}.parquet   (timestamp_ms, open_interest, oi_value)
Funding ~2020-07+, OI ~2021-09+ (Bybit limits). Paginate backward via endTime.
Run: python -m anomaly_science.bybit_funding_oi_fetch --what funding,oi1d,oi1h --workers 8
"""

from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

BASE = "https://api.bybit.com"
OUT = Path(".output/market/bybit/um_futures")
START_MS = int(pd.Timestamp("2020-01-01", tz="UTC").timestamp() * 1000)


def all_symbols():
    r = requests.get(f"{BASE}/v5/market/tickers", params={"category": "linear"}, timeout=30).json()
    return sorted([t["symbol"] for t in r["result"]["list"] if t["symbol"].endswith("USDT")])


def fetch_funding(sym, out_dir, resume):
    out = out_dir / f"{sym}.parquet"
    if resume and out.exists():
        return sym, 0
    rows = []; end = int(time.time() * 1000)
    while end > START_MS:
        try:
            lst = requests.get(f"{BASE}/v5/market/funding/history", params={"category": "linear", "symbol": sym,
                               "endTime": end, "limit": 200}, timeout=30).json()["result"]["list"]
        except Exception:
            time.sleep(1); continue
        if not lst:
            break
        for k in lst:
            rows.append((int(k["fundingRateTimestamp"]), float(k["fundingRate"])))
        oldest = min(int(k["fundingRateTimestamp"]) for k in lst)
        if oldest >= end:
            break
        end = oldest - 1; time.sleep(0.05)
    if not rows:
        return sym, 0
    df = pd.DataFrame(rows, columns=["funding_time", "funding_rate"]).drop_duplicates("funding_time").sort_values("funding_time")
    out.parent.mkdir(parents=True, exist_ok=True); df.to_parquet(out, index=False)
    return sym, len(df)


def fetch_oi(sym, interval, out_dir, resume):
    out = out_dir / f"{sym}.parquet"
    if resume and out.exists():
        return sym, 0
    rows = []; end = int(time.time() * 1000)
    while end > START_MS:
        try:
            res = requests.get(f"{BASE}/v5/market/open-interest", params={"category": "linear", "symbol": sym,
                               "intervalTime": interval, "endTime": end, "limit": 200}, timeout=30).json()["result"]
            lst = res["list"]
        except Exception:
            time.sleep(1); continue
        if not lst:
            break
        for k in lst:
            rows.append((int(k["timestamp"]), float(k["openInterest"])))
        oldest = min(int(k["timestamp"]) for k in lst)
        if oldest >= end:
            break
        end = oldest - 1; time.sleep(0.05)
    if not rows:
        return sym, 0
    df = pd.DataFrame(rows, columns=["timestamp", "open_interest"]).drop_duplicates("timestamp").sort_values("timestamp")
    out.parent.mkdir(parents=True, exist_ok=True); df.to_parquet(out, index=False)
    return sym, len(df)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--what", default="funding,oi1d,oi1h")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args()
    syms = all_symbols(); resume = not args.no_resume
    print(f"bybit all linear USDT perps: {len(syms)}")
    jobs = {"funding": (fetch_funding, OUT / "funding_v1", None),
            "oi1d": (fetch_oi, OUT / "oi_1d", "1d"), "oi1h": (fetch_oi, OUT / "oi_1h", "60")}
    for what in args.what.split(","):
        fn, out_dir, iv = jobs[what]; t0 = time.time(); done = rows = 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            if iv:
                futs = {ex.submit(fn, s, iv, out_dir, resume): s for s in syms}
            else:
                futs = {ex.submit(fn, s, out_dir, resume): s for s in syms}
            for f in as_completed(futs):
                s, n = f.result(); done += 1; rows += max(n, 0)
                if done % 50 == 0:
                    print(f"  [{what}] {done}/{len(syms)} rows={rows:,} {time.time()-t0:.0f}s")
        print(f"[{what}] DONE {done}, {rows:,} rows -> {out_dir} ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
