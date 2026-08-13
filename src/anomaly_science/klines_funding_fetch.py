"""Standalone Binance Vision USD-M fundingRate fetcher (full history).

Funding was only cached for a short 2025-06+ event window; the daily/1h study needs
the full 2023-2026 period. Funding is small (one row per 4-8h), so this pulls the
monthly fundingRate archives for the requested symbols into one parquet each.

Writes: .output/market/binance_vision/um_futures/funding_v1/{SYMBOL}.parquet

Run:
  python -m anomaly_science.klines_funding_fetch --start 2023-01 --limit 200
"""

from __future__ import annotations

import argparse
import io
import re
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

BASE = "https://data.binance.vision"
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
PREFIX = "data/futures/um/monthly/fundingRate/"
OUT = Path(".output/market/binance_vision/um_futures/funding_v1")
RAW = ["calc_time", "funding_interval_hours", "last_funding_rate"]


def _get(url, retries=6, timeout=45):
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


def _s3_list(prefix, delimiter=""):
    out, marker = [], ""
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
        items = [x for x in items if x != prefix]
        out.extend(items)
        if "<IsTruncated>true</IsTruncated>" not in text:
            break
        marker = items[-1] if items else ""
        if not marker:
            break
    return out


def list_symbols(quotes=("USDT",)):
    prefixes = _s3_list(PREFIX, delimiter="/")
    syms = [p.split("/")[-2] for p in prefixes if p.endswith("/")]
    return sorted(s for s in syms if any(s.endswith(q) for q in quotes))


def build(symbol, start_label):
    out_path = OUT / f"{symbol}.parquet"
    keys = _s3_list(f"{PREFIX}{symbol}/")
    labels = sorted({m.group(1) for k in keys
                     if (m := re.search(rf"{symbol}-fundingRate-(\d{{4}}-\d{{2}})\.zip$", k))
                     and m.group(1) >= start_label})
    frames = []
    for lab in labels:
        url = f"{BASE}/{PREFIX}{symbol}/{symbol}-fundingRate-{lab}.zip"
        r = _get(url)
        if r is None:
            continue
        try:
            zf = zipfile.ZipFile(io.BytesIO(r.content))
            raw = zf.read(zf.namelist()[0])
            first = raw[:20].decode(errors="ignore")
            header = 0 if first.startswith("calc_time") else None
            df = pd.read_csv(io.BytesIO(raw), header=header, names=None if header == 0 else RAW)
            frames.append(df)
        except (zipfile.BadZipFile, pd.errors.ParserError):
            continue
    if not frames:
        return symbol, 0
    df = pd.concat(frames, ignore_index=True)
    df = df.rename(columns={"calc_time": "funding_time", "last_funding_rate": "funding_rate"})
    df["funding_time"] = pd.to_numeric(df["funding_time"], errors="coerce")
    df["funding_rate"] = pd.to_numeric(df["funding_rate"], errors="coerce")
    df = df.dropna(subset=["funding_time"]).drop_duplicates("funding_time").sort_values("funding_time")
    OUT.mkdir(parents=True, exist_ok=True)
    df[["funding_time", "funding_rate", "funding_interval_hours"]].to_parquet(out_path, index=False)
    return symbol, len(df)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-01")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()
    syms = list_symbols()
    if args.limit:
        syms = syms[: args.limit]
    print(f"funding fetch: {len(syms)} symbols from {args.start}")
    t0 = time.time(); done = rows = 0
    for s in syms:
        _, n = build(s, args.start)
        done += 1; rows += max(n, 0)
        if done % 25 == 0:
            print(f"  {done}/{len(syms)}  rows={rows:,}  {time.time()-t0:.0f}s")
    print(f"DONE {done} symbols, {rows:,} rows -> {OUT}")


if __name__ == "__main__":
    main()
