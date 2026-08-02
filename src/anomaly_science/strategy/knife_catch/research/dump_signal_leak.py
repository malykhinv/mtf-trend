"""STRICT look-ahead proof for the break_long / aggression entry signals.

For a sample of detected entries we take the SIGNAL bar j (= entry_bar - 1; the fill is the
next open, an action not a decision), CORRUPT every array at indices > j, and re-run the
signal detector. A causal signal fires at the same j with the same bottom/top reference; any
change means the DECISION used future data. (The R outcome legitimately uses future data and
is not tested here.)

Run: python -m anomaly_science.strategy.knife_catch.research.dump_signal_leak --limit 60
"""

from __future__ import annotations

import argparse
import glob
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.knife_catch.research.dump_review import _scan_symbol
from anomaly_science.strategy.knife_catch.research.dump_signals import _first_entry
from anomaly_science.strategy.knife_catch.research.dump_signal_ev import (
    MIN_DROP, SCAN_H, TFS, REASONS,
)
from anomaly_science.strategy.session_break.research.reclaim import _resample

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01T00:00:00Z").value // 10**6


def _arrays(path, tf):
    raw = pd.read_parquet(path, columns=["timestamp", "open", "high", "low", "close",
                                         "quote_volume", "trade_count", "taker_buy_quote_volume",
                                         "open_interest"]).sort_values("timestamp").reset_index(drop=True)
    df = _resample(raw, tf)
    a = dict(ts=df["timestamp"].to_numpy(np.int64), op=df["open"].to_numpy(float),
             hi=df["high"].to_numpy(float), lo=df["low"].to_numpy(float), cl=df["close"].to_numpy(float),
             qv=df["quote_volume"].to_numpy(float), oi=df["open_interest"].to_numpy(float))
    tbq = df["taker_buy_quote_volume"].to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        a["taker"] = np.where(a["qv"] > 0, tbq / a["qv"], 0.5)
    a["base_qv"] = pd.Series(a["qv"]).rolling(240, min_periods=30).median().to_numpy()
    return a


def _corrupt_after(a, j):
    rng = np.random.default_rng(999)
    c = {k: (v.copy() if isinstance(v, np.ndarray) else v) for k, v in a.items()}
    m = len(a["ts"])
    for k in ("op", "hi", "lo", "cl", "qv", "oi"):
        c[k][j + 1:] = rng.uniform(1, 1000, size=m - j - 1)
    c["hi"][j + 1:] = np.maximum(c["hi"][j + 1:], c["lo"][j + 1:])
    c["taker"][j + 1:] = rng.uniform(0, 1, size=m - j - 1)
    c["base_qv"] = pd.Series(c["qv"]).rolling(240, min_periods=30).median().to_numpy()
    return c


def _fire(a, reason, t, b, min_drop, scan, oi_lb, tf, n):
    return _first_entry(reason, t, b, a["hi"], a["lo"], a["cl"], a["op"], a["taker"], a["oi"],
                        a["qv"], a["base_qv"], min_drop, scan, oi_lb, tf, n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60)
    args = ap.parse_args()
    files = sorted(glob.glob(str(CACHE / "*.parquet")))[:args.limit]
    tested = 0; leaks = 0; examples = []
    for f in files:
        path = Path(f)
        for tf in TFS:
            try:
                dumps = [d for d in _scan_symbol(path, tf) if d.culmination_ms < DEV_END and d.drop_pct > MIN_DROP]
            except Exception:
                continue
            if not dumps:
                continue
            a = _arrays(path, tf); ts = a["ts"]; n = len(ts)
            scan = max(2, round(SCAN_H * 60 / tf)); oi_lb = max(1, round(5 / tf))
            for d in dumps:
                b = int(np.searchsorted(ts, d.culmination_ms)); t = int(np.searchsorted(ts, d.pump_start_ms))
                if b <= 0 or t >= b or b >= n - 2:
                    continue
                for reason in REASONS:
                    eb, bottom = _fire(a, reason, t, b, MIN_DROP, scan, oi_lb, tf, n)
                    if eb is None or eb >= n - 2:
                        continue
                    j = eb - 1
                    c = _corrupt_after(a, j)
                    eb2, bottom2 = _fire(c, reason, t, b, MIN_DROP, scan, oi_lb, tf, n)
                    tested += 1
                    same = (eb2 == eb) and (bottom2 is not None and abs(bottom2 - bottom) < 1e-9)
                    if not same:
                        leaks += 1
                        if len(examples) < 5:
                            examples.append((path.stem, tf, reason, eb, eb2))
                    if tested >= 600:
                        break
                if tested >= 600:
                    break
            if tested >= 600:
                break
        if tested >= 600:
            break
    print(f"tested {tested} break_long/aggression entries")
    if leaks == 0:
        print("LOOK-AHEAD TEST PASSED: every signal fires at the same bar with the same bottom "
              "after corrupting all data > signal bar (decision is causal; fill is next-open).")
    else:
        print(f"LEAK DETECTED: {leaks}/{tested} entries changed. examples:", examples)


if __name__ == "__main__":
    main()
