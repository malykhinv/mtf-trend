"""Survivorship / delisting audit for the knife-catch deep-dump edge.

The exit study requires ~3 days of forward data, which SILENTLY drops any dump whose coin
delisted / died right after (no recovery to measure) -> a survivorship bias that would
inflate the win rate. Here we DO NOT require full forward data: every M3_early deep-dump
entry is kept, its HOLD outcome measured over whatever data remains, and entries whose coin
data ENDS within the horizon are flagged as 'truncated' (possible delisting). If meanR over
ALL entries << meanR over only-full-horizon entries, the edge is partly survivorship.
"""

from __future__ import annotations

import argparse
import glob
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.knife_catch.research.dump_review import _scan_symbol
from anomaly_science.strategy.session_break.research.reclaim import _resample

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01T00:00:00Z").value // 10**6
DATA_MAX = None  # filled in main
MIN_DROP = 0.50
STOP_BUF = 0.004
SCAN_H = 8.0
HORIZON_H = 72.0


def build_symbol(path, tf, data_max):
    try:
        dumps = [d for d in _scan_symbol(path, tf) if d.culmination_ms < DEV_END and d.drop_pct > MIN_DROP]
    except (OSError, ValueError, KeyError):
        return []
    if not dumps:
        return []
    raw = pd.read_parquet(path, columns=["timestamp", "open", "high", "low", "close",
                                         "quote_volume", "trade_count", "taker_buy_quote_volume",
                                         "open_interest"]).sort_values("timestamp").reset_index(drop=True)
    df = _resample(raw, tf)
    ts = df["timestamp"].to_numpy(np.int64); op = df["open"].to_numpy(float)
    hi = df["high"].to_numpy(float); lo = df["low"].to_numpy(float); cl = df["close"].to_numpy(float)
    qv = df["quote_volume"].to_numpy(float); oi = df["open_interest"].to_numpy(float)
    tbq = df["taker_buy_quote_volume"].to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        taker = np.where(qv > 0, tbq / qv, 0.5)
    n = len(ts); sym = path.stem
    scan = max(2, round(SCAN_H * 60 / tf)); horizon = max(8, round(HORIZON_H * 60 / tf))
    oi_lb = max(1, round(5 / tf))
    end_ts = int(ts[-1]); coin_delisted = end_ts < data_max - 3 * 86_400_000   # ends >3d before dataset end
    out = []
    for d in dumps:
        b = int(np.searchsorted(ts, d.culmination_ms)); t = int(np.searchsorted(ts, d.pump_start_ms))
        if b <= 0 or t >= b or b >= n - 2:
            continue
        T = float(d.pump_start_price); run_min = float(lo[t]); ecb = None
        for j in range(t + 1, min(b + scan, n - 1)):
            run_min = min(run_min, lo[j])
            if (T - run_min) / T < MIN_DROP:
                continue
            if taker[j] > 0.5 and cl[j] > op[j] and oi[j] >= oi[max(0, j - oi_lb)]:
                ecb = j + 1; break
        if ecb is None or ecb >= n - 1:
            continue
        entry = float(op[ecb]); stop = run_min * (1 - STOP_BUF)
        if entry <= stop:
            continue
        risk = entry - stop
        fwd = n - 1 - ecb
        truncated = fwd < horizon
        we = min(ecb + horizon, n - 1)
        hold_R = float((cl[we] - entry) / risk)
        # if the coin delisted and this entry is in its final stretch, the realistic outcome
        # is the terminal close (often deep loss); already captured by hold_R at the data end.
        fwd_days = fwd * tf / (60 * 24)
        out.append({"symbol": sym, "R_hold": hold_R, "truncated": int(truncated),
                    "coin_delisted": int(coin_delisted), "fwd_days": fwd_days,
                    "week": pd.Timestamp(int(ts[ecb]), unit="ms", tz="UTC").strftime("%G-W%V")})
    return out


def _one(a):
    f, tf, dm = a
    try:
        return build_symbol(Path(f), tf, dm)
    except Exception:  # noqa: BLE001
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", type=int, default=5)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    files = sorted(glob.glob(str(CACHE / "*.parquet")))
    if args.limit:
        files = files[:args.limit]
    # dataset max timestamp (to detect coins that end early = delisted)
    dm = 0
    for f in files[:50]:
        try:
            e = int(pd.read_parquet(f, columns=["timestamp"]).timestamp.iloc[-1]); dm = max(dm, e)
        except Exception:
            pass
    print(f"survivorship audit tf={args.tf}m symbols={len(files)} dataset_end={pd.Timestamp(dm, unit='ms')}")
    rows = []; done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_one, (f, args.tf, dm)): f for f in files}
        for fut in as_completed(futs):
            rows += fut.result(); done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(files)} entries={len(rows):,}")
    t = pd.DataFrame(rows)
    print(f"\nALL M3 deep-dump entries (NO forward-data filter): n={len(t):,}")
    full = t[t.truncated == 0]; trunc = t[t.truncated == 1]
    print(f"  full-horizon (>=3d fwd): n={len(full):,}  HOLD meanR={full.R_hold.mean():+.3f}  win={ (full.R_hold>0).mean()*100:.1f}%")
    print(f"  TRUNCATED (<3d fwd, dropped by the study): n={len(trunc):,} ({len(trunc)/max(1,len(t))*100:.1f}%)  "
          f"HOLD meanR={trunc.R_hold.mean():+.3f}  win={ (trunc.R_hold>0).mean()*100:.1f}%")
    print(f"  ALL together (survivorship-free): n={len(t):,}  HOLD meanR={t.R_hold.mean():+.3f}  win={ (t.R_hold>0).mean()*100:.1f}%")
    de = t[t.coin_delisted == 1]
    print(f"  on coins that DELISTED (end >3d before dataset end): n={len(de):,}  HOLD meanR={de.R_hold.mean():+.3f}  "
          f"win={ (de.R_hold>0).mean()*100:.1f}%" if len(de) else "  no delisted-coin entries")
    print(f"\n(if ALL meanR << full-horizon meanR, the +1.4R was partly survivorship.)")


if __name__ == "__main__":
    main()
