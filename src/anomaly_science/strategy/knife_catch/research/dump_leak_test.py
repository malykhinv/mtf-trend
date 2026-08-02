"""STRICT look-ahead proof for the knife-catch feature grid.

For a sample of real flow entries we compute the feature dict twice: once on the true
series, once after CORRUPTING every array at indices > e (the signal bar). A feature that
depends only on data <= e is bit-identical between the two; any feature that changes is
leaking future information. Also asserts entry timing (>= b+CONFIRM, fill at e+1 open).

Run: python -m anomaly_science.strategy.knife_catch.research.dump_leak_test --limit 40
"""

from __future__ import annotations

import argparse
import glob
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.knife_catch.research.dump_review import _scan_symbol, CONFIRM_BARS
from anomaly_science.strategy.knife_catch.research import dump_reversal as DR
from anomaly_science.strategy.session_break.research.reclaim import _resample
from anomaly_science.strategy.session_break.research.sessions import block_seq_for_ms

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01T00:00:00Z").value // 10**6


def _arrays(path):
    raw = pd.read_parquet(path, columns=["timestamp", "open", "high", "low", "close",
                                         "quote_volume", "trade_count", "taker_buy_quote_volume",
                                         "open_interest"]).sort_values("timestamp").reset_index(drop=True)
    df = _resample(raw, DR.TF)
    a = dict(
        ts=df["timestamp"].to_numpy(np.int64), op=df["open"].to_numpy(float),
        hi=df["high"].to_numpy(float), lo=df["low"].to_numpy(float), cl=df["close"].to_numpy(float),
        qv=df["quote_volume"].to_numpy(float), tc=df["trade_count"].to_numpy(float),
        oi=df["open_interest"].to_numpy(float))
    tbq = df["taker_buy_quote_volume"].to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        a["taker"] = np.where(a["qv"] > 0, tbq / a["qv"], 0.5)
        a["ats"] = np.where(a["tc"] > 0, a["qv"] / a["tc"], np.nan)
    a["cvd"] = np.cumsum((2 * a["taker"] - 1) * a["qv"])
    a["atr"] = causal_atr(high=a["hi"], low=a["lo"], close=a["cl"], window=64)
    a["base_qv"] = pd.Series(a["qv"]).rolling(240, min_periods=30).median().to_numpy()
    a["ema60"] = DR._ema(a["cl"], 60); a["ema240"] = DR._ema(a["cl"], 240)
    a["rng_bar"] = a["hi"] - a["lo"]; a["seqs"] = block_seq_for_ms(a["ts"])
    return a


def _feat(a, d, b, e, prior_bottoms):
    n = len(a["ts"])
    return DR._features(d, b, e, float(a["cl"][e]), a["lo"][b], d.pump_start_price, a["ts"],
                        a["op"], a["hi"], a["lo"], a["cl"], a["qv"], a["tc"], a["taker"], a["cvd"],
                        a["oi"], a["atr"], a["base_qv"], a["ema60"], a["ema240"], a["ats"],
                        a["rng_bar"], a["seqs"], prior_bottoms, n)


def _corrupt_after(a, e):
    """Copy arrays and destroy everything strictly after bar e. cvd/atr/base_qv/ema are
    RECOMPUTED from the corrupted primitives so any hidden forward dependency would show."""
    rng = np.random.default_rng(12345)
    c = {k: (v.copy() if isinstance(v, np.ndarray) else v) for k, v in a.items()}
    m = len(a["ts"])
    for k in ("op", "hi", "lo", "cl", "qv", "tc", "oi"):
        c[k][e + 1:] = rng.uniform(1, 1000, size=m - e - 1)
    # keep hi>=lo sane
    c["hi"][e + 1:] = np.maximum(c["hi"][e + 1:], c["lo"][e + 1:])
    with np.errstate(invalid="ignore", divide="ignore"):
        c["taker"] = np.where(c["qv"] > 0, np.clip(rng.uniform(0, 1, m), 0, 1), 0.5)
        c["taker"][:e + 1] = a["taker"][:e + 1]
        c["ats"] = np.where(c["tc"] > 0, c["qv"] / c["tc"], np.nan)
    c["cvd"] = np.cumsum((2 * c["taker"] - 1) * c["qv"])
    c["atr"] = causal_atr(high=c["hi"], low=c["lo"], close=c["cl"], window=64)
    c["base_qv"] = pd.Series(c["qv"]).rolling(240, min_periods=30).median().to_numpy()
    c["ema60"] = DR._ema(c["cl"], 60); c["ema240"] = DR._ema(c["cl"], 240)
    c["rng_bar"] = c["hi"] - c["lo"]
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()
    files = sorted(glob.glob(str(CACHE / "*.parquet")))[:args.limit]
    DR._btc()  # warm BTC cache (its own series, always <= e in wall-clock)
    n_entries = 0; leaks = {}; bad_timing = 0
    for f in files:
        path = Path(f)
        try:
            dumps = [d for d in _scan_symbol(path) if d.culmination_ms < DEV_END]
        except Exception:
            continue
        if not dumps:
            continue
        a = _arrays(path); ts = a["ts"]; n = len(ts)
        prior_bottoms = np.array(sorted(int(np.searchsorted(ts, d.culmination_ms)) for d in dumps), dtype=np.int64)
        for d in dumps:
            b = int(np.searchsorted(ts, d.culmination_ms))
            if b <= 0 or b >= n - 5 or ts[b] != d.culmination_ms:
                continue
            e = DR._find_trigger("flow", b, a["hi"], a["lo"], a["cl"], a["op"], a["taker"], a["oi"], n)
            if e is None or e < b + CONFIRM_BARS or e + 1 >= n - 1:
                continue
            if not (e >= b + CONFIRM_BARS):
                bad_timing += 1
            F1 = _feat(a, d, b, e, prior_bottoms)
            F2 = _feat(_corrupt_after(a, e), d, b, e, prior_bottoms)
            for k in F1:
                v1, v2 = F1[k], F2[k]
                same = (v1 == v2) or (isinstance(v1, float) and isinstance(v2, float)
                                      and (np.isnan(v1) and np.isnan(v2) or np.isclose(v1, v2, atol=1e-9, equal_nan=True)))
                if not same:
                    leaks[k] = leaks.get(k, 0) + 1
            n_entries += 1
            if n_entries >= 400:
                break
        if n_entries >= 400:
            break
    print(f"tested {n_entries} flow entries across feature grid ({len(F1)} features)")
    print(f"entry-timing violations (e < b+CONFIRM): {bad_timing}")
    if not leaks:
        print("LOOK-AHEAD TEST PASSED: every feature is bit-identical after corrupting all data > e (no leak).")
    else:
        print("LEAK DETECTED in features (changed when future was corrupted):")
        for k, c in sorted(leaks.items(), key=lambda x: -x[1]):
            print(f"  {k}: changed in {c}/{n_entries} entries")


if __name__ == "__main__":
    main()
