"""Outcome-independent detector for the top-of-pump congestion breakout study.

Emits two artifacts per run (see spec.py for the geometry narrative):

  * events.parquet     - one row per qualifying anomaly (pump >= 20% from base
                         that then stalled near the high >= 30 min). These are
                         the DESK CANDIDATES the human reviews.
  * congestions.parquet - one row per LOCAL congestion that sat in the lower
                         part of that event's range. The detector emits EVERY
                         such congestion; this is the honest comparison pool.
                         Each carries a causally-computed outcome (did price
                         break to a new pump high before invalidating under the
                         congestion) used ONLY for study/pool-splitting - never
                         as a detection filter.

The pump/stall geometry looks forward on purpose: an event is a manual-review
object, not a live signal. Causality is enforced where it matters - the per-
congestion OUTCOME only ever reads bars after the congestion ends, and no
outcome column is allowed to feed detection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from anomaly_science.strategy.top_congestion.spec import (
    DETECTION_TIMEFRAMES_MINUTES,
    HOUR_MS,
    IS_END_EXCLUSIVE_MS,
    IS_START_MS,
    PROTOCOL_FREEZE_ID,
    UNIVERSE_SCHEMA_VERSION,
    TopCongestionConfig,
)

CACHE_DIR = Path(".output/market/binance_vision/um_futures/enriched_1m")
OUTPUT_DIR = Path(".output/research/top_congestion/universe_is")
SOURCE_COLUMNS: tuple[str, ...] = (
    "timestamp", "open", "high", "low", "close", "quote_volume", "trade_count",
)
# Warm the trailing baseline (activity + ATR) before IS starts.
WARMUP_START_MS = IS_START_MS - 2 * 24 * HOUR_MS
FORBIDDEN_EVENT_COLUMNS: frozenset[str] = frozenset(
    {"label", "marked", "human_quality", "is_winner"}
)


def _hash_id(prefix: str, *parts: object) -> str:
    raw = "|".join([PROTOCOL_FREEZE_ID, *(str(p) for p in parts)]).encode("utf-8")
    return f"{prefix}_{hashlib.blake2b(raw, digest_size=10).hexdigest()}"


def _resample(frame: pd.DataFrame, tf_min: int) -> dict[str, np.ndarray]:
    src = frame.loc[:, SOURCE_COLUMNS].copy()
    for col in SOURCE_COLUMNS:
        src[col] = pd.to_numeric(src[col], errors="coerce")
    src = src.dropna(subset=["timestamp"]).sort_values("timestamp")
    src = src.drop_duplicates("timestamp").reset_index(drop=True)
    if tf_min == 1:
        ts = src["timestamp"].to_numpy(np.int64)
        return {
            "ts": ts,
            "open": src["open"].to_numpy(float), "high": src["high"].to_numpy(float),
            "low": src["low"].to_numpy(float), "close": src["close"].to_numpy(float),
            "qv": src["quote_volume"].to_numpy(float), "tc": src["trade_count"].to_numpy(float),
        }
    width = tf_min * 60_000
    bucket = (src["timestamp"].astype("int64") // width) * width
    g = src.groupby(bucket, sort=True)
    return {
        "ts": g["timestamp"].first().to_numpy(np.int64),
        "open": g["open"].first().to_numpy(float),
        "high": g["high"].max().to_numpy(float),
        "low": g["low"].min().to_numpy(float),
        "close": g["close"].last().to_numpy(float),
        "qv": g["quote_volume"].sum().to_numpy(float),
        "tc": g["trade_count"].sum().to_numpy(float),
    }


def _causal_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, window: int) -> np.ndarray:
    prior_close = np.concatenate(([np.nan], close[:-1]))
    tr = np.maximum(high - low, np.maximum(np.abs(high - prior_close), np.abs(low - prior_close)))
    tr[0] = high[0] - low[0]
    return pd.Series(tr).rolling(window, min_periods=window).mean().to_numpy(float)


def detect_symbol(
    frame: pd.DataFrame, *, symbol: str, config: TopCongestionConfig
) -> tuple[pd.DataFrame, pd.DataFrame]:
    tf = config.timeframe_minutes
    tf_ms = tf * 60_000
    bars = _resample(frame, tf)
    ts, o, h, low, c = bars["ts"], bars["open"], bars["high"], bars["low"], bars["close"]
    qv, tc = bars["qv"], bars["tc"]
    n = len(ts)
    a, s, cg = config.anomaly, config.stall, config.congestion
    oc = config.outcome

    max_pump_bars = max(1, a.max_pump_period_minutes // tf)
    baseline_bars = max(2, a.baseline_hours * 60 // tf)
    max_stall_bars = max(1, s.max_stall_lookahead_minutes // tf)
    min_stall_bars = max(1, s.min_stall_minutes // tf)
    horizon_bars = max(1, oc.resolution_horizon_minutes // tf)
    atr = _causal_atr(h, low, c, cg.atr_window_bars)
    atr_gate = _causal_atr(h, low, c, a.atr_window_minutes // tf if a.atr_window_minutes >= tf else 1)

    # Vectorized ignition screen: high breaks >= min_pump_pct above the recent
    # swing low (rolling min low over the prior max_pump_bars). A fast proxy for
    # "base = low of last red candle before ignition".
    prior_min_low = pd.Series(low).rolling(max_pump_bars, min_periods=1).min().shift(1).to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        rise_from_base = h / prior_min_low - 1.0
    pump_bar = np.nan_to_num(rise_from_base, nan=0.0) >= a.min_pump_pct

    events: list[dict[str, object]] = []
    congs: list[dict[str, object]] = []

    i = baseline_bars
    while i < n - 1:
        if not pump_bar[i]:
            i += 1
            continue
        # Peak: extend forward while price keeps making new highs (small noise ok).
        peak = i
        trail = h[i]
        j = i + 1
        while j < min(n, i + max_pump_bars) and h[j] >= trail * 0.97:
            if h[j] > trail:
                trail, peak = h[j], j
            j += 1
        window_lo = max(0, peak - max_pump_bars)
        ign = window_lo + int(np.argmin(low[window_lo:peak + 1]))
        base_low = float(low[ign])
        peak_high = float(h[peak])
        if base_low <= 0 or peak_high / base_low - 1.0 < a.min_pump_pct:
            i += 1
            continue

        # --- anomaly gates over the pump period [ign..peak] ---
        turnover = float(np.nansum(qv[ign:peak + 1]))
        base_qv = float(np.nanmean(qv[max(0, ign - baseline_bars):ign])) if ign > 0 else np.nan
        base_tc = float(np.nanmean(tc[max(0, ign - baseline_bars):ign])) if ign > 0 else np.nan
        peak_qv = float(np.nanmax(qv[ign:peak + 1]))
        peak_tc = float(np.nanmax(tc[ign:peak + 1]))
        activity_ok = (
            (base_qv > 0 and peak_qv >= a.min_activity_ratio * base_qv)
            or (base_tc > 0 and peak_tc >= a.min_activity_ratio * base_tc)
        )
        atr_base = atr_gate[ign - 1] if ign > 0 else np.nan
        atr_ok = np.isfinite(atr_gate[peak]) and np.isfinite(atr_base) and atr_base > 0 \
            and atr_gate[peak] >= a.min_atr_ratio * atr_base
        if not (turnover >= a.min_turnover_usdt and activity_ok and atr_ok):
            i += 1
            continue

        # --- stall near the top, forward from the peak ---
        stall_end = peak
        left_top = False
        broke_up = False
        for k in range(peak + 1, min(n, peak + 1 + max_stall_bars)):
            if h[k] > peak_high * (1.0 + oc.new_high_buffer_pct):
                broke_up = True
                stall_end = k
                break
            if low[k] < peak_high * (1.0 - s.max_leave_top_pct):
                left_top = True
                stall_end = k
                break
            stall_end = k
        stall_bars = stall_end - peak
        if stall_bars < min_stall_bars:
            i = peak + 1
            continue
        stall_hi_after = float(np.nanmax(h[peak + 1:stall_end + 1])) if stall_end > peak else peak_high
        if stall_hi_after < peak_high * (1.0 - s.top_band_pct):
            # price fell away and never tested near the high again -> not stalled at top
            i = stall_end + 1
            continue

        range_high = peak_high
        range_low = float(np.nanmin(low[peak:stall_end + 1]))
        range_mid = 0.5 * (range_high + range_low)
        lower_ceiling = range_low + cg.lower_zone_fraction * (range_high - range_low)
        event_id = _hash_id("tce", symbol, tf, int(ts[peak]))

        # --- enumerate every lower-zone congestion inside the stall ---
        n_cong = n_win = 0
        k = peak
        while k < stall_end:
            lo_run, hi_run, end = low[k], h[k], k
            m = k
            while m <= stall_end:
                nlo, nhi = min(lo_run, low[m]), max(hi_run, h[m])
                band_ok = np.isfinite(atr[m]) and (nhi - nlo) <= cg.max_band_atr * atr[m]
                if band_ok and nhi <= lower_ceiling:
                    lo_run, hi_run, end = nlo, nhi, m
                    m += 1
                else:
                    break
            if end - k + 1 >= cg.min_congestion_bars:
                cong_low = float(lo_run)
                cong_high = float(hi_run)
                target = peak_high * (1.0 + oc.new_high_buffer_pct)
                stop = cong_low * (1.0 - oc.stop_buffer_pct)
                outcome, res_idx = "censored", None
                for w in range(end + 1, min(n, end + 1 + horizon_bars)):
                    hit_stop = low[w] <= stop
                    hit_target = h[w] >= target
                    if hit_target and not hit_stop:
                        outcome, res_idx = "reached_new_high", w
                        break
                    if hit_stop and not hit_target:
                        outcome, res_idx = "stopped", w
                        break
                    if hit_stop and hit_target:
                        outcome, res_idx = "ambiguous_same_bar", w
                        break
                congs.append({
                    "universe_schema_version": UNIVERSE_SCHEMA_VERSION,
                    "protocol_freeze_id": PROTOCOL_FREEZE_ID,
                    "congestion_id": _hash_id("tcc", event_id, int(ts[k])),
                    "event_id": event_id,
                    "symbol": symbol,
                    "timeframe_minutes": tf,
                    "cong_start_time_ms": int(ts[k]),
                    "cong_end_time_ms": int(ts[end] + tf_ms),
                    "cong_bars": end - k + 1,
                    "cong_low": cong_low,
                    "cong_high": cong_high,
                    "cong_pos_in_range": (0.5 * (cong_low + cong_high) - range_low) / (range_high - range_low)
                        if range_high > range_low else np.nan,
                    "band_atr": (cong_high - cong_low) / atr[end] if np.isfinite(atr[end]) and atr[end] > 0 else np.nan,
                    "target_new_high": target,
                    "stop_price": stop,
                    "risk_pct": (cong_high - stop) / cong_high if cong_high > 0 else np.nan,
                    "reward_pct": (target - cong_high) / cong_high if cong_high > 0 else np.nan,
                    "outcome": outcome,
                    "resolved_time_ms": int(ts[res_idx]) if res_idx is not None else pd.NA,
                })
                n_cong += 1
                k = end + 1
            else:
                k += 1
            n_win += 1

        events.append({
            "universe_schema_version": UNIVERSE_SCHEMA_VERSION,
            "protocol_freeze_id": PROTOCOL_FREEZE_ID,
            "event_id": event_id,
            "symbol": symbol,
            "tf": f"{tf}m",
            "timeframe_minutes": tf,
            "ignition_time_ms": int(ts[ign]),
            "base_price": base_low,
            "pump_peak_time_ms": int(ts[peak]),
            "pump_peak_price": peak_high,
            "pump_pct": peak_high / base_low - 1.0,
            "pump_turnover_usdt": turnover,
            "stall_end_time_ms": int(ts[stall_end] + tf_ms),
            "stall_minutes": stall_bars * tf,
            "stall_exit": "broke_up" if broke_up else ("left_top" if left_top else "timeout"),
            "range_high": range_high,
            "range_mid": range_mid,
            "range_low": range_low,
            "lower_zone_ceiling": lower_ceiling,
            "n_lower_congestions": n_cong,
            "review_start_ms": int(ts[max(0, ign - max_pump_bars)]),
            "review_end_ms": int(ts[min(n - 1, stall_end + horizon_bars)] + tf_ms),
        })
        i = stall_end + 1

    events_df = pd.DataFrame(events)
    congs_df = pd.DataFrame(congs)
    return events_df, congs_df


def _read_source(path: Path) -> pd.DataFrame:
    return pd.read_parquet(
        path, columns=list(SOURCE_COLUMNS),
        filters=[("timestamp", ">=", WARMUP_START_MS), ("timestamp", "<", IS_END_EXCLUSIVE_MS)],
    )


def _build_one(args: tuple[str, int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    path_text, tf = args
    path = Path(path_text)
    try:
        frame = _read_source(path)
    except (OSError, ValueError):
        return pd.DataFrame(), pd.DataFrame()
    if frame.empty:
        return pd.DataFrame(), pd.DataFrame()
    return detect_symbol(frame, symbol=path.stem, config=TopCongestionConfig(timeframe_minutes=tf))


def _eligible_paths(paths: Iterable[Path]) -> list[Path]:
    eligible: list[Path] = []
    required = set(SOURCE_COLUMNS)
    for path in sorted(Path(p) for p in paths):
        stem = path.stem
        dated = len(stem.rsplit("_", 1)) == 2 and stem.rsplit("_", 1)[1].isdigit()
        if dated:
            continue
        if required - set(pq.read_schema(path).names):
            continue
        eligible.append(path)
    return eligible


def build_universe(
    paths: Iterable[Path], *, output_dir: Path = OUTPUT_DIR,
    timeframes: tuple[int, ...] = DETECTION_TIMEFRAMES_MINUTES, workers: int = 6,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    source_paths = _eligible_paths(paths)
    if not source_paths:
        raise ValueError("no eligible source paths")
    tasks = [(str(p), tf) for tf in timeframes for p in source_paths]
    event_frames: list[pd.DataFrame] = []
    cong_frames: list[pd.DataFrame] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for ev, cg in ex.map(_build_one, tasks, chunksize=8):
            if not ev.empty:
                event_frames.append(ev)
            if not cg.empty:
                cong_frames.append(cg)
    events = pd.concat(event_frames, ignore_index=True) if event_frames else pd.DataFrame()
    congs = pd.concat(cong_frames, ignore_index=True) if cong_frames else pd.DataFrame()
    if not events.empty:
        events = events.sort_values(["pump_peak_time_ms", "symbol", "tf"]).reset_index(drop=True)
        if events["event_id"].duplicated().any():
            raise AssertionError("duplicate event ids")
        if FORBIDDEN_EVENT_COLUMNS & set(events.columns):
            raise AssertionError("outcome/label columns leaked into events")
    output_dir.mkdir(parents=True, exist_ok=True)
    events.to_parquet(output_dir / "events.parquet", index=False)
    congs.to_parquet(output_dir / "congestions.parquet", index=False)
    (output_dir / "report.json").write_text(
        json.dumps(_base_rate_report(events, congs, timeframes), indent=2, sort_keys=True), encoding="utf-8"
    )
    return events, congs


def _base_rate_report(events: pd.DataFrame, congs: pd.DataFrame, timeframes: tuple[int, ...]) -> dict:
    report: dict[str, object] = {
        "protocol_freeze_id": PROTOCOL_FREEZE_ID,
        "config": {tf: asdict(TopCongestionConfig(timeframe_minutes=tf)) for tf in timeframes},
        "events": int(len(events)),
        "congestions": int(len(congs)),
    }
    if not events.empty:
        report["events_by_tf"] = {k: int(v) for k, v in events["tf"].value_counts().items()}
        report["event_symbols"] = int(events["symbol"].nunique())
    if not congs.empty:
        oc = congs["outcome"].value_counts()
        report["congestion_outcomes"] = {k: int(v) for k, v in oc.items()}
        resolved = congs[congs["outcome"].isin(["reached_new_high", "stopped"])]
        if not resolved.empty:
            report["new_high_base_rate"] = float((resolved["outcome"] == "reached_new_high").mean())
        report["congestions_by_tf"] = {k: int(v) for k, v in congs["timeframe_minutes"].value_counts().items()}
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the top-congestion IS universe.")
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--timeframes", default="1,5")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    paths = sorted(args.cache_dir.glob("*.parquet"))
    if args.limit:
        paths = paths[: args.limit]
    tfs = tuple(int(x) for x in args.timeframes.split(","))
    events, congs = build_universe(paths, output_dir=args.output_dir, timeframes=tfs, workers=args.workers)
    print(f"events={len(events):,}  congestions={len(congs):,} -> {args.output_dir}")
    print((args.output_dir / "report.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
