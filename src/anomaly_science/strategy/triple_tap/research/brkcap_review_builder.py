"""Build a clean BRK/CAP review population from strict detector structures.

The old review seed mixed a broad pump candidate with an unrelated level setup.
This adapter never combines independent rows: pump, family and level are all
derived from one strict detector row, then checked by the frozen foundation
contract before it is exposed to human review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from anomaly_science.annotation.ohlcv import load_ohlcv_parquet, resample_ohlcv_np
from anomaly_science.annotation.schemas import ANNOTATION_CANDIDATE_SCHEMA_VERSION, LEVEL_LABEL_SCHEMA_VERSION
from anomaly_science.strategy.triple_tap.research.brkcap_foundations import (
    BRKCAP_FOUNDATION_POLICY_VERSION,
    BrkCapStructure,
    CandleSeries,
    PumpTransition,
    ResistanceLevel,
    SetupFamily,
    audit_structure,
    first_close_above_level_ms,
)


DEFAULT_SETUPS_PATH = Path(".output/results/triple_tap_v1/setups.parquet")
DEFAULT_CACHE_DIR = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEFAULT_REVIEW_DIR = Path(".output/results/triple_tap_v1/bot_brkcap_review_v3")


def tf_minutes(tf: str) -> int:
    if tf.endswith("m"):
        return int(tf[:-1])
    if tf.endswith("h"):
        return int(tf[:-1]) * 60
    raise ValueError(f"unsupported timeframe {tf!r}")


def _event_id(row: Mapping[str, Any]) -> str:
    identity = "|".join(
        (str(row["setup_family"]), str(row["symbol"]), str(row["tf"]), str(int(row["entry_time_ms"])))
    )
    return "brkcap_v2_" + hashlib.blake2b(identity.encode("utf-8"), digest_size=10).hexdigest()


def _candles(cache_dir: Path, symbol: str, tf: str) -> CandleSeries:
    frame = load_ohlcv_parquet(cache_dir / f"{symbol}.parquet")
    minutes = tf_minutes(tf)
    if minutes != 1:
        frame = resample_ohlcv_np(frame, minutes)
    return CandleSeries(
        timestamp=np.asarray(frame["timestamp"], dtype=np.int64),
        open=np.asarray(frame["open"], dtype=np.float64),
        high=np.asarray(frame["high"], dtype=np.float64),
        low=np.asarray(frame["low"], dtype=np.float64),
        close=np.asarray(frame["close"], dtype=np.float64),
    )


def structure_from_detector_row(row: Mapping[str, Any], candles: CandleSeries) -> BrkCapStructure:
    """Map one strict detector row to one complete, internally consistent setup."""

    family = SetupFamily(str(row["setup_family"]))
    pump_start_ms = int(row["pump_start_ms"])
    pump_start_idx = candles.exact_index(pump_start_ms)
    if pump_start_idx is None:
        raise ValueError("pump start is absent from the detector timeframe")

    if family is SetupFamily.BREAKOUT:
        culmination_ms = int(row["h1_time_ms"])
        culmination_price = float(row["h1"])
    else:
        bars = int(row["ignition_bars"])
        culmination_idx = pump_start_idx + bars
        if culmination_idx >= len(candles.timestamp):
            raise ValueError("cap culmination is outside the detector timeframe")
        culmination_ms = int(candles.timestamp[culmination_idx])
        culmination_price = float(row["culmination"])

    return BrkCapStructure(
        family=family,
        pump=PumpTransition(
            start_ms=pump_start_ms,
            start_price=float(candles.low[pump_start_idx]),
            culmination_ms=culmination_ms,
            culmination_price=culmination_price,
        ),
        level=ResistanceLevel(
            start_ms=int(row["h1_time_ms"]),
            # This is replaced with the causal first-close endpoint below.
            end_ms=int(row["entry_time_ms"]),
            price=float(row["level"]),
        ),
    )


def _with_causal_level_end(structure: BrkCapStructure, candles: CandleSeries, minutes: int) -> BrkCapStructure:
    level = structure.level
    if level is None:  # pragma: no cover - retained for a total typed helper
        return structure
    first_break = first_close_above_level_ms(candles, level_price=level.price, level_start_ms=level.start_ms)
    end_ms = first_break if first_break is not None else int(candles.timestamp[-1] + minutes * 60_000)
    return BrkCapStructure(
        family=structure.family,
        pump=structure.pump,
        level=ResistanceLevel(start_ms=level.start_ms, end_ms=end_ms, price=level.price),
    )


@dataclass(frozen=True, slots=True)
class FoundationReviewRow:
    candidate: dict[str, Any]
    seed_label: dict[str, Any]


def _review_row(row: Mapping[str, Any], candles: CandleSeries) -> FoundationReviewRow:
    structure = _with_causal_level_end(structure_from_detector_row(row, candles), candles, tf_minutes(str(row["tf"])))
    audit = audit_structure(candles, structure)
    if not audit.review_eligible:
        violations = ", ".join(v.value for v in audit.violations)
        raise ValueError(f"foundation contract rejected detector row: {violations}")
    assert structure.level is not None
    level = structure.level
    pump = structure.pump
    event_id = _event_id(row)
    first_break = first_close_above_level_ms(candles, level_price=level.price, level_start_ms=level.start_ms)
    start_idx = candles.exact_index(pump.start_ms)
    assert start_idx is not None
    review_start = int(candles.timestamp[max(0, start_idx - 120)])
    entry_ms = first_break if first_break is not None else None
    end_idx = candles.exact_index(entry_ms) if entry_ms is not None else len(candles.timestamp) - 1
    review_end = int(candles.timestamp[min(len(candles.timestamp) - 1, end_idx + 24)])
    pump_pct = (pump.culmination_price - pump.start_price) / pump.start_price
    quality_flags = [violation.value for violation in audit.quality_flags]
    quality_note = "none" if not quality_flags else ",".join(quality_flags)
    note = (
        f"BOT V3 {structure.family.value} | foundation={BRKCAP_FOUNDATION_POLICY_VERSION} "
        f"| quality_flags={quality_note} | redraw / comment"
    )
    candidate = {
        "candidate_schema_version": ANNOTATION_CANDIDATE_SCHEMA_VERSION,
        "event_id": event_id,
        "symbol": str(row["symbol"]),
        "tf": str(row["tf"]),
        "review_start_ms": review_start,
        "review_end_ms": review_end,
        "anchor_time_ms": level.start_ms,
        "pump_start_ms": pump.start_ms,
        "culmination_ms": pump.culmination_ms,
        "pump_low_price": pump.start_price,
        "culmination_price": pump.culmination_price,
        "pump_pct": float(pump_pct),
        "pump_bars": int(candles.exact_index(pump.culmination_ms) - start_idx),
        "pump_hours": float((pump.culmination_ms - pump.start_ms) / 3_600_000),
        "score": 1.0,
        "source_detector_entry_time_ms": int(row["entry_time_ms"]),
        "foundation_policy_version": BRKCAP_FOUNDATION_POLICY_VERSION,
        "foundation_quality_flags": quality_flags,
    }
    seed_label = {
        "event_id": event_id,
        "symbol": candidate["symbol"],
        "tf": candidate["tf"],
        "source_event_id": event_id,
        "source_event_ids": [event_id],
        "selected_tf": candidate["tf"],
        "has_level": True,
        "has_pump_transition": True,
        "setups": [
            {
                "family": structure.family.value,
                "quality": "ok",
                "notes": note,
                "has_level": True,
                "has_pump_transition": True,
                "has_structure_break": False,
                "level_price": level.price,
                "level_start_ms": level.start_ms,
                "level_end_ms": level.end_ms,
                "level_broken": first_break is not None,
                "level_touch_times_ms": [level.start_ms],
                "pump_start_ms": pump.start_ms,
                "pump_start_price": pump.start_price,
                "culmination_ms": pump.culmination_ms,
                "culmination_price": pump.culmination_price,
                "structure_break_ms": None,
                "structure_break_price": None,
                "structure_swing_low_ms": None,
                "structure_swing_low_price": None,
                "entry_ms": entry_ms,
                "entry_price": float(candles.close[candles.exact_index(entry_ms)]) if entry_ms is not None else None,
                "entry_auto": entry_ms is not None,
                "entry_source": "level_break" if entry_ms is not None else None,
                "exit_ms": None,
                "exit_price": None,
                "sl_price": None,
                "sl_ms": None,
                "sl_hit_ms": None,
                "sl_auto": None,
                "sl_source": None,
                "zigzag_points": [],
            }
        ],
        "source": "bot_brkcap_foundation_v2",
        "label_schema_version": LEVEL_LABEL_SCHEMA_VERSION,
        "saved_at_ms": 0,
    }
    return FoundationReviewRow(candidate=candidate, seed_label=seed_label)


def build_foundation_review(
    *,
    setups_path: Path = DEFAULT_SETUPS_PATH,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    review_dir: Path = DEFAULT_REVIEW_DIR,
) -> pd.DataFrame:
    """Write a new review population; never overwrite historic expert labels."""

    raw = pd.read_parquet(setups_path)
    required = {"setup_family", "symbol", "tf", "entry_time_ms", "pump_start_ms", "h1_time_ms", "h1", "level"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"strict setup artifact is missing columns: {sorted(missing)}")
    rows: list[FoundationReviewRow] = []
    cache: dict[tuple[str, str], CandleSeries] = {}
    for source in raw.sort_values(["symbol", "entry_time_ms", "tf"]).to_dict("records"):
        key = (str(source["symbol"]), str(source["tf"]))
        candles = cache.get(key)
        if candles is None:
            candles = _candles(cache_dir, *key)
            cache[key] = candles
        try:
            rows.append(_review_row(source, candles))
        except ValueError:
            continue

    candidates = pd.DataFrame([row.candidate for row in rows])
    if candidates.empty:
        raise ValueError("no strict detector rows satisfy the BRK/CAP foundation contract")
    # One physical pump event is one review candidate.  The smaller timeframe is
    # retained deterministically; no outcome or quality information participates.
    candidates["_tf_minutes"] = candidates["tf"].map(tf_minutes)
    candidates = (
        candidates.sort_values(["symbol", "source_detector_entry_time_ms", "_tf_minutes"])
        .drop_duplicates(["symbol", "source_detector_entry_time_ms"], keep="first")
        .drop(columns="_tf_minutes")
        .sort_values(["culmination_ms", "symbol", "tf"])
        .reset_index(drop=True)
    )
    keep = set(candidates["event_id"])
    seeds = [row.seed_label for row in rows if row.candidate["event_id"] in keep]
    seeds.sort(key=lambda row: (int(row["setups"][0]["culmination_ms"]), str(row["symbol"]), str(row["tf"])))

    review_dir.mkdir(parents=True, exist_ok=True)
    candidates.to_parquet(review_dir / "candidates.parquet", index=False)
    payload = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in seeds)
    (review_dir / "marks.jsonl").write_text(payload, encoding="utf-8")
    (review_dir / "review_comments.jsonl").write_text(payload, encoding="utf-8")
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser(description="Build strict BRK/CAP v2 review seeds.")
    parser.add_argument("--setups", type=Path, default=DEFAULT_SETUPS_PATH)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--review-dir", type=Path, default=DEFAULT_REVIEW_DIR)
    args = parser.parse_args()
    candidates = build_foundation_review(setups_path=args.setups, cache_dir=args.cache_dir, review_dir=args.review_dir)
    print(f"foundation BRK/CAP review candidates={len(candidates)} -> {args.review_dir}")


if __name__ == "__main__":
    main()
