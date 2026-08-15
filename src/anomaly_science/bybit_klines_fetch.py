"""Bybit linear-perp klines fetcher (1d + 1h) -> cross-venue validation of the Binance setups.

Bybit v5 REST (/v5/market/kline). Writes one parquet per symbol per interval into
  .output/market/bybit/um_futures/klines_{1d,1h}/{SYMBOL}.parquet
with the same columns the pipeline expects (timestamp, open, high, low, close, volume,
quote_volume, trade_count, taker_buy_base_volume, taker_buy_quote_volume) -- Bybit gives no
taker/trade_count so those are NaN (core setups only need OHLCV+turnover). Ranks symbols by
24h turnover and keeps the top --limit. Run:
  python -m anomaly_science.bybit_klines_fetch --intervals 60,D --start 2020-01 --limit 200 --workers 8
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
COLS = ["timestamp", "open", "high", "low", "close", "volume", "quote_volume",
        "trade_count", "taker_buy_base_volume", "taker_buy_quote_volume"]


def top_symbols(limit):
    r = requests.get(f"{BASE}/v5/market/tickers", params={"category": "linear"}, timeout=30).json()
    rows = [(t["symbol"], float(t.get("turnover24h", 0) or 0)) for t in r["result"]["list"]
            if t["symbol"].endswith("USDT")]
    rows.sort(key=lambda x: -x[1])
    return [s for s, _ in rows[:limit]]


def fetch_symbol(sym, interval, start_ms, out_dir, resume):
    out_path = out_dir / f"{sym}.parquet"
    if resume and out_path.exists():
        return sym, 0
    step = (60 if interval == "60" else 1440) * 60 * 1000
    now = int(time.time() * 1000)
    all_rows = []; cur = start_ms
    while cur < now:
        try:
            r = requests.get(f"{BASE}/v5/market/kline", params={"category": "linear", "symbol": sym,
                             "interval": interval, "start": cur, "limit": 1000}, timeout=30).json()
            lst = r.get("result", {}).get("list", [])
        except Exception:
            time.sleep(1.0); continue
        if not lst:
            break
        lst = sorted(lst, key=lambda x: int(x[0]))       # oldest-first
        for k in lst:
            all_rows.append((int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), float(k[6])))
        last = int(lst[-1][0])
        if last + step <= cur:
            break
        cur = last + step
        time.sleep(0.05)
    if not all_rows:
        return sym, 0
    df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume", "quote_volume"])
    df = df.drop_duplicates("timestamp").sort_values("timestamp")
    for c in ["trade_count", "taker_buy_base_volume", "taker_buy_quote_volume"]:
        df[c] = float("nan")
    df = df[COLS]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    return sym, len(df)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--intervals", default="60,D")
    ap.add_argument("--start", default="2020-01")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args()
    start_ms = int(pd.Timestamp(args.start + "-01", tz="UTC").timestamp() * 1000)
    syms = top_symbols(args.limit)
    print(f"top {len(syms)} bybit linear perps by 24h turnover; start {args.start}")
    for interval in args.intervals.split(","):
        out_dir = OUT / ("klines_1h" if interval == "60" else "klines_1d")
        t0 = time.time(); done = rows = 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(fetch_symbol, s, interval, start_ms, out_dir, not args.no_resume): s for s in syms}
            for f in as_completed(futs):
                s, n = f.result(); done += 1; rows += max(n, 0)
                if done % 25 == 0:
                    print(f"  [{interval}] {done}/{len(syms)} rows={rows:,} {time.time()-t0:.0f}s")
        print(f"[{interval}] DONE {done} symbols, {rows:,} rows -> {out_dir} ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
