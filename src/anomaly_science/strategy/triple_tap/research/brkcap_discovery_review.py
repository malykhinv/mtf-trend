"""High-recall, causal BRK/CAP review population built from sleep→pump events.

This is intentionally a *discovery* layer.  It does not reuse the legacy
BRK/CAP detector and it does not turn uncalibrated geometry diagnostics into a
hard gate.  A sleep→pump candidate is observed through a fixed post-proposal
window; within that window we find a repeated resistance cluster and expose
its quality measurements to the reviewer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from anomaly_science.annotation.ohlcv import load_ohlcv_parquet, resample_ohlcv_np
from anomaly_science.annotation.schemas import ANNOTATION_CANDIDATE_SCHEMA_VERSION, LEVEL_LABEL_SCHEMA_VERSION
from anomaly_science.strategy.triple_tap.research.brkcap_foundations import CandleSeries
from anomaly_science.strategy.triple_tap.research.brkcap_geometry import ResistanceTouch, analyze_level_geometry


SOURCE_CANDIDATES = Path(".output/results/triple_tap_v1/sleep_pump_review/candidates.parquet")
CACHE_DIR = Path(".output/market/binance_vision/um_futures/enriched_1m")
OUTPUT_DIR = Path(".output/results/triple_tap_v1/brkcap_discovery_review_v4")


@dataclass(frozen=True, slots=True)
class DiscoveryPolicy:
    """Pre-registered structural contract derived from expert review feedback."""

    version: str = "brkcap_discovery_v4_2026-07-13"
    observation_bars: int = 72
    review_forward_bars: int = 24
    min_touches: int = 3
    touch_tolerance_pct: float = 0.015
    main_touch_merge_bars: int = 6
    min_pump_pct: float = 0.35
    min_pump_bars: int = 48
    min_pump_path_efficiency: float = 0.40
    preferred_pump_path_efficiency: float = 0.45
    max_pump_retrace_share: float = 0.30
    max_pump_single_bar_range_share: float = 0.35
    min_cap_position_in_pump: float = 0.50
    max_cap_position_in_pump: float = 1.00
    min_cap_initial_pullback_share: float = 0.20
    min_cap_height_to_initial_pullback: float = 0.40
    max_overhead_resistance_layers: int = 3
    preferred_overhead_resistance_layers: int = 1
    min_body_above_support_share: float = 0.90
    max_events: int = 64


POLICY = DiscoveryPolicy()


@dataclass(frozen=True, slots=True)
class DiscoveredLevel:
    family: str
    price: float
    touch_indices: tuple[int, ...]
    start_index: int
    end_index: int
    broken: bool
    score: float
    quality_flags: tuple[str, ...]
    support_contact_count: int
    body_above_support_share: float | None


@dataclass(frozen=True, slots=True)
class CapStructureMetrics:
    """Online cap measurements implied by the expert review protocol."""

    position_in_pump: float
    initial_pullback_share: float
    height_to_initial_pullback: float
    overhead_resistance_layers: int


def _tf_minutes(tf: str) -> int:
    if tf.endswith("m"):
        return int(tf[:-1])
    if tf.endswith("h"):
        return int(tf[:-1]) * 60
    if tf.endswith("d"):
        return int(tf[:-1]) * 1440
    raise ValueError(f"unsupported timeframe {tf!r}")


def _local_highs(high: np.ndarray, start: int, end: int, required: int) -> list[int]:
    indices = [required]
    for index in range(max(1, start), min(end, len(high) - 1) + 1):
        if high[index] >= high[index - 1] and high[index] >= high[index + 1]:
            indices.append(index)
    return sorted(set(indices))


def _main_touches(indices: tuple[int, ...], high: np.ndarray, merge_bars: int) -> tuple[int, ...]:
    """Collapse nearby same-level spikes to one salient touch.

    A horizontal level is defined by separate tests of resistance, not by every
    one-bar high inside a noisy plateau.  Within each short cluster we retain
    the highest wick; clusters remain ordered and causally observable.
    """

    selected: list[int] = []
    for index in indices:
        if not selected or index - selected[-1] >= merge_bars:
            selected.append(index)
        elif float(high[index]) > float(high[selected[-1]]):
            selected[-1] = index
    return tuple(selected)


def _credible_pump(source: dict[str, Any], policy: DiscoveryPolicy) -> bool:
    """Reject weak, choppy, or already-dumped pump hypotheses before geometry."""

    return (
        float(source["pump_pct"]) >= policy.min_pump_pct
        and int(source["pump_bars"]) >= policy.min_pump_bars
        and float(source["pump_path_efficiency"]) >= policy.min_pump_path_efficiency
        and float(source["pump_retrace_share"]) <= policy.max_pump_retrace_share
        and float(source["pump_single_bar_range_share"]) <= policy.max_pump_single_bar_range_share
    )


def _cap_structure_metrics(
    candles: CandleSeries,
    *,
    level: DiscoveredLevel,
    pump_start_index: int,
    culmination_index: int,
    source: dict[str, Any],
    policy: DiscoveryPolicy,
) -> CapStructureMetrics | None:
    """Measure whether a cap belongs to its pump and has a clear path above it."""

    pump_low = float(source["pump_low_price"])
    culmination = float(source["culmination_price"])
    if culmination <= pump_low:
        return None
    position = (level.price - pump_low) / (culmination - pump_low)
    if level.start_index <= culmination_index:
        return None
    amplitude = culmination - pump_low
    initial_low = float(np.min(candles.low[culmination_index + 1 : level.start_index + 1]))
    initial_pullback = (culmination - initial_low) / amplitude
    cap_low = float(np.min(candles.low[level.start_index : level.end_index + 1]))
    cap_height = max(0.0, level.price - cap_low) / amplitude

    overhead_prices: list[float] = []
    for index in _local_highs(candles.high, pump_start_index, level.start_index, culmination_index):
        price = float(candles.high[index])
        if price > level.price * (1.0 + policy.touch_tolerance_pct):
            overhead_prices.append(price)
    layers: list[float] = []
    for price in sorted(overhead_prices, reverse=True):
        if not any(abs(price - existing) / existing <= policy.touch_tolerance_pct for existing in layers):
            layers.append(price)
    return CapStructureMetrics(
        position_in_pump=float(position),
        initial_pullback_share=float(initial_pullback),
        height_to_initial_pullback=float(cap_height / initial_pullback) if initial_pullback > 0.0 else 0.0,
        overhead_resistance_layers=len(layers),
    )


def _cap_is_structurally_coherent(metrics: CapStructureMetrics | None, policy: DiscoveryPolicy) -> bool:
    if metrics is None:
        return False
    return (
        policy.min_cap_position_in_pump <= metrics.position_in_pump <= policy.max_cap_position_in_pump
        and metrics.initial_pullback_share >= policy.min_cap_initial_pullback_share
        and metrics.height_to_initial_pullback >= policy.min_cap_height_to_initial_pullback
        and metrics.overhead_resistance_layers <= policy.max_overhead_resistance_layers
    )


def _soft_quality_flags(
    *,
    level: DiscoveredLevel,
    cap_metrics: CapStructureMetrics | None,
    source: dict[str, Any],
    policy: DiscoveryPolicy,
) -> tuple[str, ...]:
    """Warnings that preserve a reviewable structure but prevent an A grade."""

    flags = list(level.quality_flags)
    if float(source["pump_path_efficiency"]) < policy.preferred_pump_path_efficiency:
        flags.append("pump_path_choppy")
    if cap_metrics is not None and cap_metrics.overhead_resistance_layers > policy.preferred_overhead_resistance_layers:
        flags.append("multiple_overhead_resistance_layers")
    return tuple(dict.fromkeys(flags))


def _first_close_above_until(candles: CandleSeries, *, level_price: float, start_index: int, end_index: int) -> int | None:
    """Causal level break: inspect no candle after the declared snapshot."""

    for index in range(start_index + 1, end_index + 1):
        if float(candles.close[index]) > level_price:
            return index
    return None


def discover_level(
    candles: CandleSeries,
    *,
    pump_start_index: int,
    culmination_index: int,
    snapshot_index: int,
    policy: DiscoveryPolicy = POLICY,
) -> DiscoveredLevel | None:
    """Find the best repeated resistance that is fully observed by snapshot."""

    if not (0 <= pump_start_index < culmination_index <= snapshot_index < len(candles.timestamp)):
        raise ValueError("pump and snapshot indices must be ordered inside candles")
    peaks = _local_highs(candles.high, culmination_index, snapshot_index, culmination_index)
    best: DiscoveredLevel | None = None
    culmination_price = float(candles.high[culmination_index])
    for anchor in peaks:
        anchor_price = float(candles.high[anchor])
        if anchor_price <= 0.0:
            continue
        nearby_touches = tuple(
            index for index in peaks
            if abs(float(candles.high[index]) - anchor_price) / anchor_price <= policy.touch_tolerance_pct
        )
        if len(nearby_touches) < policy.min_touches:
            continue
        touches = _main_touches(nearby_touches, candles.high, policy.main_touch_merge_bars)
        if len(touches) < policy.min_touches:
            continue
        price = float(np.median(candles.high[list(touches)]))
        start_index = touches[0]
        first_break_index = _first_close_above_until(
            candles,
            level_price=price,
            start_index=start_index,
            end_index=snapshot_index,
        )
        if first_break_index is None:
            end_index = snapshot_index
        else:
            end_index = first_break_index
        if end_index < touches[-1]:
            continue
        family = "breakout" if touches[0] == culmination_index else "cap"
        selected_touches = tuple(
            ResistanceTouch(index=index, time_ms=int(candles.timestamp[index]), price=float(candles.high[index]))
            for index in touches
        )
        geometry = analyze_level_geometry(
            candles,
            level_price=price,
            start_ms=int(candles.timestamp[start_index]),
            end_ms=int(candles.timestamp[end_index]),
            selected_touches=selected_touches,
            terminal_retrace_stop_index=end_index if first_break_index is not None else end_index + 1,
        )
        flags: list[str] = []
        if len(geometry.touches) < 3:
            flags.append("fewer_than_three_touches")
        if geometry.ascending_support is False:
            flags.append("descending_support")
        if geometry.retrace_depths_non_increasing is False:
            flags.append("retrace_depths_not_compressing")
        if not geometry.has_pairwise_retraces:
            flags.append("incomplete_pairwise_retraces")
        if (
            geometry.body_above_support_share is not None
            and geometry.body_above_support_share < policy.min_body_above_support_share
        ):
            flags.append("body_below_rising_support")
        closeness = float(np.mean(np.abs(candles.high[list(touches)] - price) / price))
        score = float(10.0 * len(touches) - 100.0 * closeness + (2.0 if geometry.ascending_support else 0.0))
        candidate = DiscoveredLevel(
            family,
            price,
            touches,
            start_index,
            end_index,
            first_break_index is not None,
            score,
            tuple(flags),
            len(geometry.support_contact_indices),
            geometry.body_above_support_share,
        )
        if best is None or candidate.score > best.score:
            best = candidate
    return best


def _event_id(source_event_id: str, level: DiscoveredLevel) -> str:
    raw = f"{source_event_id}|{level.family}|{level.price:.12g}|{level.start_index}".encode("utf-8")
    return "brkcap_discovery_" + hashlib.blake2b(raw, digest_size=10).hexdigest()


def _quality_tier(flags: tuple[str, ...]) -> str:
    """A is clean; B is structurally admissible but carries explicit warnings."""

    if not flags:
        return "A"
    return "B"


def _candles(cache_dir: Path, symbol: str, tf: str) -> CandleSeries:
    frame = load_ohlcv_parquet(cache_dir / f"{symbol}.parquet")
    minutes = _tf_minutes(tf)
    if minutes != 1:
        frame = resample_ohlcv_np(frame, minutes)
    return CandleSeries(
        timestamp=np.asarray(frame["timestamp"], dtype=np.int64),
        open=np.asarray(frame["open"], dtype=np.float64),
        high=np.asarray(frame["high"], dtype=np.float64),
        low=np.asarray(frame["low"], dtype=np.float64),
        close=np.asarray(frame["close"], dtype=np.float64),
    )


def _candidate_row(source: dict[str, Any], candles: CandleSeries, level: DiscoveredLevel) -> tuple[dict[str, Any], dict[str, Any]]:
    pump_start_ms = int(source["pump_start_ms"])
    culmination_ms = int(source["culmination_ms"])
    pump_start_index = candles.exact_index(pump_start_ms)
    culmination_index = candles.exact_index(culmination_ms)
    assert pump_start_index is not None and culmination_index is not None
    event_id = _event_id(str(source["event_id"]), level)
    review_start_index = max(0, pump_start_index - 120)
    review_end_index = min(len(candles.timestamp) - 1, level.end_index + POLICY.review_forward_bars)
    touch_times = [int(candles.timestamp[index]) for index in level.touch_indices]
    row = {
        "candidate_schema_version": ANNOTATION_CANDIDATE_SCHEMA_VERSION,
        "event_id": event_id,
        "source_event_id": str(source["event_id"]),
        "symbol": str(source["symbol"]),
        "tf": str(source["tf"]),
        "review_start_ms": int(candles.timestamp[review_start_index]),
        "review_end_ms": int(candles.timestamp[review_end_index]),
        "anchor_time_ms": int(candles.timestamp[level.start_index]),
        "feature_cutoff_time_ms": int(candles.timestamp[level.end_index]),
        "pump_start_ms": pump_start_ms,
        "culmination_ms": culmination_ms,
        "pump_low_price": float(source["pump_low_price"]),
        "culmination_price": float(source["culmination_price"]),
        "pump_pct": float(source["pump_pct"]),
        "pump_bars": int(source["pump_bars"]),
        "pump_hours": float(source["pump_hours"]),
        "score": float(source["score"]) + level.score,
        "discovery_policy_version": POLICY.version,
        "discovery_level_price": level.price,
        "discovery_touch_times_ms": touch_times,
        "discovery_quality_flags": list(level.quality_flags),
        "discovery_support_contact_count": level.support_contact_count,
        "discovery_body_above_support_share": level.body_above_support_share,
    }
    note = "none" if not level.quality_flags else ",".join(level.quality_flags)
    seed = {
        "event_id": event_id,
        "symbol": row["symbol"],
        "tf": row["tf"],
        "source_event_id": row["source_event_id"],
        "source_event_ids": [row["source_event_id"]],
        "selected_tf": row["tf"],
        "has_level": True,
        "has_pump_transition": True,
        "setups": [{
            "family": level.family,
            "quality": "ok" if not level.quality_flags else "bad",
            "notes": f"BRK/CAP discovery | quality_flags={note} | redraw / comment",
            "has_level": True,
            "has_pump_transition": True,
            "has_structure_break": False,
            "level_price": level.price,
            "level_start_ms": int(candles.timestamp[level.start_index]),
            "level_end_ms": int(candles.timestamp[level.end_index]),
            "level_broken": level.broken,
            "level_touch_times_ms": touch_times,
            "pump_start_ms": pump_start_ms,
            "pump_start_price": float(source["pump_low_price"]),
            "culmination_ms": culmination_ms,
            "culmination_price": float(source["culmination_price"]),
            "structure_break_ms": None, "structure_break_price": None,
            "structure_swing_low_ms": None, "structure_swing_low_price": None,
            "entry_ms": None, "entry_price": None, "entry_auto": None, "entry_source": None,
            "exit_ms": None, "exit_price": None,
            "sl_price": None, "sl_ms": None, "sl_hit_ms": None, "sl_auto": None, "sl_source": None,
            "zigzag_points": [],
        }],
        "source": "brkcap_discovery_v4",
        "label_schema_version": LEVEL_LABEL_SCHEMA_VERSION,
        "saved_at_ms": 0,
    }
    return row, seed


def build_discovery_review(
    *,
    source_candidates: Path = SOURCE_CANDIDATES,
    cache_dir: Path = CACHE_DIR,
    output_dir: Path = OUTPUT_DIR,
    policy: DiscoveryPolicy = POLICY,
) -> pd.DataFrame:
    """Materialise an independent, high-recall review queue and its seed marks."""

    source = pd.read_parquet(source_candidates)
    rows: list[dict[str, Any]] = []
    seeds: list[dict[str, Any]] = []
    cache: dict[tuple[str, str], CandleSeries] = {}
    for item in source.to_dict("records"):
        key = (str(item["symbol"]), str(item["tf"]))
        candles = cache.get(key)
        if candles is None:
            candles = _candles(cache_dir, *key)
            cache[key] = candles
        start = candles.exact_index(int(item["pump_start_ms"]))
        culmination = candles.exact_index(int(item["culmination_ms"]))
        proposal = candles.exact_index(int(item["proposal_time_ms"]))
        if start is None or culmination is None or proposal is None:
            continue
        if not _credible_pump(item, policy):
            continue
        snapshot = min(len(candles.timestamp) - 1, proposal + policy.observation_bars)
        level = discover_level(
            candles,
            pump_start_index=start,
            culmination_index=culmination,
            snapshot_index=snapshot,
            policy=policy,
        )
        if level is None:
            continue
        cap_metrics = _cap_structure_metrics(
            candles,
            level=level,
            pump_start_index=start,
            culmination_index=culmination,
            source=item,
            policy=policy,
        ) if level.family == "cap" else None
        if level.family == "cap" and not _cap_is_structurally_coherent(cap_metrics, policy):
            continue
        quality_flags = _soft_quality_flags(level=level, cap_metrics=cap_metrics, source=item, policy=policy)
        level = replace(level, quality_flags=quality_flags)
        row, seed = _candidate_row(item, candles, level)
        tier = _quality_tier(level.quality_flags)
        row["discovery_quality_tier"] = tier
        row["discovery_main_touch_count"] = len(level.touch_indices)
        row["discovery_pump_path_efficiency"] = float(item["pump_path_efficiency"])
        row["discovery_pump_retrace_share"] = float(item["pump_retrace_share"])
        if cap_metrics is not None:
            row["discovery_cap_position_in_pump"] = cap_metrics.position_in_pump
            row["discovery_cap_initial_pullback_share"] = cap_metrics.initial_pullback_share
            row["discovery_cap_height_to_initial_pullback"] = cap_metrics.height_to_initial_pullback
            row["discovery_overhead_resistance_layers"] = cap_metrics.overhead_resistance_layers
        seed["setups"][0]["notes"] = (
            f"BRK/CAP discovery tier={tier} | quality_flags="
            f"{'none' if not level.quality_flags else ','.join(level.quality_flags)} | redraw / comment"
        )
        rows.append(row)
        seeds.append(seed)
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError("no BRK/CAP discovery candidates")
    tier_order = {"A": 0, "B": 1, "C": 2}
    frame["_tier_order"] = frame["discovery_quality_tier"].map(tier_order)
    frame = (
        frame.sort_values(["_tier_order", "score", "pump_pct"], ascending=[True, False, False])
        .head(policy.max_events)
        .drop(columns="_tier_order")
        .sort_values(["discovery_quality_tier", "culmination_ms", "symbol"])
        .reset_index(drop=True)
    )
    selected = set(frame["event_id"])
    seeds = [seed for seed in seeds if seed["event_id"] in selected]
    seeds.sort(key=lambda seed: (int(seed["setups"][0]["culmination_ms"]), str(seed["symbol"])))
    labels_path = output_dir / "review_comments.jsonl"
    if labels_path.exists() and labels_path.stat().st_size:
        raise FileExistsError(f"refusing to overwrite expert labels: {labels_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(output_dir / "candidates.parquet", index=False)
    payload = "".join(json.dumps(seed, ensure_ascii=False, separators=(",", ":")) + "\n" for seed in seeds)
    (output_dir / "marks.jsonl").write_text(payload, encoding="utf-8")
    labels_path.touch(exist_ok=True)
    return frame


def main() -> None:
    parser = argparse.ArgumentParser(description="Build high-recall BRK/CAP discovery review candidates.")
    parser.add_argument("--source-candidates", type=Path, default=SOURCE_CANDIDATES)
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--max-events", type=int, default=POLICY.max_events)
    args = parser.parse_args()
    policy = DiscoveryPolicy(max_events=args.max_events)
    frame = build_discovery_review(source_candidates=args.source_candidates, cache_dir=args.cache_dir, output_dir=args.output_dir, policy=policy)
    print(f"BRK/CAP discovery candidates={len(frame)} -> {args.output_dir}")


if __name__ == "__main__":
    main()
