"""Standalone Binance Vision USD-M futures klines fetcher (1d + 1h).

Purpose (2026-08-04): the cross-sectional momentum/quality study only had a
7-month all-bear IS window. To test the long side and the regime-adaptive logic
across bull/bear/sideways we need more history. The strategy is daily and every
validated signal is computable from plain klines, so we pull only 1d (decisions)
and 1h (a cheap lower-TF for intraday-shape features) — NOT 1m.

Writes one parquet per symbol per interval into separate folders:
  .output/market/binance_vision/um_futures/klines_1d/{SYMBOL}.parquet
  .output/market/binance_vision/um_futures/klines_1h/{SYMBOL}.parquet

Includes delisted symbols (survivorship, protocol §5.1): symbol discovery is the
S3 prefix listing, which retains every symbol that ever had data.

Run:
  python -m anomaly_science.klines_multitf_fetch --intervals 1d --start 2023-01
  python -m anomaly_science.klines_multitf_fetch --intervals 1h --start 2023-01 --workers 12
"""

from __future__ import annotations

import argparse
import io
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

BASE = "https://data.binance.vision"
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
MONTHLY_KLINES_PREFIX = "data/futures/um/monthly/klines/"
OUT_ROOT = Path(".output/market/binance_vision/um_futures")

RAW_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
            "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]
OUT_COLS = ["timestamp", "open", "high", "low", "close", "volume", "quote_volume",
            "trade_count", "taker_buy_base_volume", "taker_buy_quote_volume"]


def _get(url: str, retries: int = 6, timeout: int = 45) -> requests.Response | None:
    for i in range(retries):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return None
        except requests.RequestException:
            pass
        time.sleep(min(1.5 * (i + 1), 8.0))
    return None


def _s3_list(prefix: str, delimiter: str = "") -> list[str]:
    """Marker-paginated S3 listing. Returns CommonPrefixes if delimiter set, else Keys."""
    out: list[str] = []
    marker = ""
    while True:
        url = f"{S3}?prefix={prefix}"
        if delimiter:
            url += f"&delimiter={delimiter}"
        if marker:
            url += f"&marker={requests.utils.quote(marker, safe='')}"
        r = _get(url)
        if r is None:
            break
        text = r.text
        items = (re.findall(r"<Prefix>([^<]+)</Prefix>", text) if delimiter
                 else re.findall(r"<Key>([^<]+)</Key>", text))
        # first <Prefix> in a delimiter listing echoes the request prefix; drop it
        items = [x for x in items if x != prefix]
        out.extend(items)
        if "<IsTruncated>true</IsTruncated>" not in text:
            break
        marker = items[-1] if items else ""
        if not marker:
            break
    return out


def list_symbols(quotes: tuple[str, ...]) -> list[str]:
    prefixes = _s3_list(MONTHLY_KLINES_PREFIX, delimiter="/")
    syms = [p.split("/")[-2] for p in prefixes if p.endswith("/")]
    syms = [s for s in syms if any(s.endswith(q) for q in quotes)]
    return sorted(set(syms))


def list_month_labels(symbol: str, interval: str, start_label: str) -> list[str]:
    prefix = f"{MONTHLY_KLINES_PREFIX}{symbol}/{interval}/"
    keys = _s3_list(prefix)
    labels = []
    for k in keys:
        m = re.search(rf"{symbol}-{interval}-(\d{{4}}-\d{{2}})\.zip$", k)
        if m and m.group(1) >= start_label:
            labels.append(m.group(1))
    return sorted(set(labels))


def _parse_zip(content: bytes) -> pd.DataFrame:
    zf = zipfile.ZipFile(io.BytesIO(content))
    name = zf.namelist()[0]
    raw = zf.read(name)
    first = raw[:20].decode(errors="ignore")
    header = 0 if first.startswith("open_time") else None
    df = pd.read_csv(io.BytesIO(raw), header=header,
                     names=None if header == 0 else RAW_COLS)
    df = df.rename(columns={
        "open_time": "timestamp", "count": "trade_count",
        "taker_buy_volume": "taker_buy_base_volume",
    })
    for c in OUT_COLS:
        if c not in df.columns:
            df[c] = pd.NA
    return df[OUT_COLS]


def build_symbol(symbol: str, interval: str, start_label: str, out_dir: Path, resume: bool) -> tuple[str, int]:
    out_path = out_dir / f"{symbol}.parquet"
    if resume and out_path.exists():
        return symbol, -1
    labels = list_month_labels(symbol, interval, start_label)
    frames = []
    for lab in labels:
        url = f"{BASE}/data/futures/um/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{lab}.zip"
        r = _get(url)
        if r is None:
            continue
        try:
            frames.append(_parse_zip(r.content))
        except (zipfile.BadZipFile, pd.errors.ParserError):
            continue
    if not frames:
        return symbol, 0
    df = pd.concat(frames, ignore_index=True)
    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).drop_duplicates("timestamp").sort_values("timestamp")
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    return symbol, len(df)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--intervals", default="1d", help="comma list, e.g. 1d,1h")
    ap.add_argument("--start", default="2023-01", help="first monthly label YYYY-MM")
    ap.add_argument("--quotes", default="USDT,USDC")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="0 = all symbols")
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args()

    quotes = tuple(q.strip().upper() for q in args.quotes.split(","))
    intervals = [i.strip() for i in args.intervals.split(",")]
    print(f"listing symbols (quotes={quotes}) ...")
    symbols = list_symbols(quotes)
    if args.limit:
        symbols = symbols[: args.limit]
    print(f"symbols: {len(symbols)}  intervals: {intervals}  start: {args.start}")

    for interval in intervals:
        out_dir = OUT_ROOT / f"klines_{interval}"
        t0 = time.time()
        done = rows = 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(build_symbol, s, interval, args.start, out_dir, not args.no_resume): s for s in symbols}
            for fut in as_completed(futs):
                sym, n = fut.result()
                done += 1
                if n > 0:
                    rows += n
                if done % 50 == 0:
                    print(f"  [{interval}] {done}/{len(symbols)}  rows={rows:,}  {time.time()-t0:.0f}s")
        print(f"[{interval}] DONE {done} symbols, {rows:,} rows -> {out_dir}  ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
