"""Outcome-complete Desk artifact for causal session-reclaim review.

Every causal signal is included.  Mechanics outcomes enrich the card but never
decide whether the card exists or where it appears in the queue.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from anomaly_science.annotation.review_questions import ReviewOption, ReviewQuestion
from anomaly_science.strategy.session_reclaim.mechanics import MECHANICS_DIR
from anomaly_science.strategy.session_reclaim.spec import (
    IS_END_EXCLUSIVE_MS,
    MECHANICS_SCHEMA_VERSION,
    MINUTE_MS,
    PROTOCOL_FREEZE_ID,
)
from anomaly_science.strategy.session_reclaim.universe import OUTPUT_DIR


DESK_DIR = Path(".output/research/session_reclaim_short/desk_review_is")
DESK_SCHEMA_VERSION = "session_reclaim_trade_review_v1"
PRIMARY_VARIANT_ID = "market_close1_mid"
REVIEW_LEAD_MINUTES = 12 * 60
REVIEW_TAIL_MINUTES = 48 * 60


def _options(*values: tuple[str, str]) -> tuple[ReviewOption, ...]:
    return tuple(ReviewOption(value=value, label=label) for value, label in values)


SESSION_RECLAIM_REVIEW_QUESTIONS: tuple[ReviewQuestion, ...] = (
    ReviewQuestion(
        "reference_quality",
        "Prior-session high",
        _options(("clean", "clean structure"), ("weak", "weak / noisy"), ("invalid", "invalid"), ("uncertain", "uncertain")),
    ),
    ReviewQuestion(
        "break_quality",
        "Break above high",
        _options(("failed_break", "clear failed break"), ("accepted_above", "acceptance above"), ("noise", "noise"), ("uncertain", "uncertain")),
    ),
    ReviewQuestion(
        "reclaim_quality",
        "Return below high",
        _options(("decisive", "decisive"), ("marginal", "marginal"), ("late", "late"), ("invalid", "invalid"), ("uncertain", "uncertain")),
    ),
    ReviewQuestion(
        "path_failure_mode",
        "Why win / loss",
        _options(
            ("clean_rejection", "clean rejection"),
            ("wick_then_worked", "wicked stop, then worked"),
            ("accepted_above", "accepted above"),
            ("range_noise", "range noise"),
            ("market_beta", "broad-market move"),
            ("coin_specific", "coin-specific activity"),
            ("no_fill", "no fill"),
            ("uncertain", "uncertain"),
        ),
    ),
    ReviewQuestion(
        "best_execution",
        "Best causal execution",
        _options(
            ("market", "market reclaim"),
            ("maker", "maker retest"),
            ("rejection", "retest rejection"),
            ("none", "none"),
            ("uncertain", "uncertain"),
        ),
    ),
    ReviewQuestion(
        "tradeable",
        "Tradeable live",
        _options(("yes", "yes"), ("conditional", "conditional"), ("no", "no"), ("uncertain", "uncertain")),
    ),
)


def _json_value(value: Any) -> Any:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, np.generic):
        return _json_value(value.item())
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_json_value(item) for item in value]
    return value


def _compact_variant(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "variant_id", "entry_policy", "invalidation_policy", "profit_policy",
        "status", "entry_status", "entry_time_ms", "entry_price", "entry_liquidity",
        "entry_latency_minutes", "same_minute_ambiguous", "hard_stop_price",
        "path_continuous", "exit_reason", "exit_time_ms", "exit_price", "duration_minutes",
        "target_mid_hit", "target_low_hit", "gross_bps", "gross_r", "hard_risk_fraction",
        "mfe_bps", "mae_bps", "fills_json", "net_2bps", "net_4bps", "net_6bps",
        "net_10bps", "net_20bps", "net_30bps",
    )
    return {key: _json_value(row.get(key)) for key in keys}


def _selection_hash(event_id: str) -> str:
    payload = f"{PROTOCOL_FREEZE_ID}|desk|{event_id}".encode("utf-8")
    return hashlib.blake2b(payload, digest_size=10).hexdigest()


def build_desk_candidates(universe: pd.DataFrame, mechanics: pd.DataFrame) -> pd.DataFrame:
    """Build an all-signal queue; future results are display-only payloads."""

    if universe.empty:
        raise ValueError("universe is empty")
    if mechanics.empty:
        raise ValueError("mechanics is empty")
    if set(universe["event_id"].astype(str)) != set(mechanics["event_id"].astype(str)):
        raise ValueError("mechanics must cover every causal universe event")
    if mechanics["protocol_freeze_id"].ne(PROTOCOL_FREEZE_ID).any():
        raise ValueError("mechanics protocol does not match the frozen universe")
    if mechanics["mechanics_schema_version"].ne(MECHANICS_SCHEMA_VERSION).any():
        raise ValueError("unexpected mechanics schema")

    grouped = {
        str(event_id): [
            _compact_variant(row)
            for row in part.sort_values("variant_id").replace({np.nan: None}).to_dict("records")
        ]
        for event_id, part in mechanics.groupby("event_id", sort=False)
    }
    rows: list[dict[str, Any]] = []
    for event in universe.sort_values(["signal_time_ms", "symbol", "event_id"]).replace({np.nan: None}).to_dict("records"):
        event_id = str(event["event_id"])
        variants = grouped[event_id]
        signal_ms = int(event["signal_time_ms"])
        review_start = max(0, int(event["reference_start_time_ms"]) - REVIEW_LEAD_MINUTES * MINUTE_MS)
        review_end = min(
            signal_ms + REVIEW_TAIL_MINUTES * MINUTE_MS - MINUTE_MS,
            IS_END_EXCLUSIVE_MS - MINUTE_MS,
        )
        if not review_start < signal_ms <= review_end < IS_END_EXCLUSIVE_MS:
            raise ValueError(f"invalid IS-only Desk window for {event_id}")
        payload = json.dumps(variants, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        rows.append(
            {
                "candidate_schema_version": DESK_SCHEMA_VERSION,
                "protocol_freeze_id": PROTOCOL_FREEZE_ID,
                "event_id": event_id,
                "source_event_id": event_id,
                "annotation_group_id": event_id,
                "symbol": str(event["symbol"]),
                "tf": "15m",
                "signal_tf": "1h",
                "review_start_ms": review_start,
                "review_end_ms": review_end,
                "selection_hash": _selection_hash(event_id),
                # Ordering is point-in-time only: no PnL, MFE, labels, or fills.
                "score": float(min(event["reference_swing_rise_atr"], event["reference_swing_fall_atr"])),
                "primary_variant_id": PRIMARY_VARIANT_ID,
                "trade_review_variants_json": payload,
                "suggested_level": float(event["reference_high"]),
                "suggested_level_start_ms": int(event["reference_high_time_ms"]),
                "suggested_level_end_ms": review_end,
                "range_mid": float(event["reference_mid"]),
                "range_low": float(event["reference_low"]),
                "poke_high": float(event["poke_high"]),
                "upper_structure_price": _json_value(event.get("upper_structure_price")),
                "upper_structure_time_ms": _json_value(event.get("upper_structure_time_ms")),
                "reference_start_time_ms": int(event["reference_start_time_ms"]),
                "reference_end_time_ms": int(event["reference_end_time_ms"]),
                "reference_high_time_ms": int(event["reference_high_time_ms"]),
                "poke_start_time_ms": int(event["poke_start_time_ms"]),
                "poke_peak_time_ms": int(event["poke_peak_time_ms"]),
                "signal_bar_start_time_ms": int(event["signal_bar_start_time_ms"]),
                "signal_time_ms": signal_ms,
                "order_activation_time_ms": int(event["order_activation_time_ms"]),
                "reference_swing_rise_atr": float(event["reference_swing_rise_atr"]),
                "reference_swing_fall_atr": float(event["reference_swing_fall_atr"]),
                "poke_depth_fraction_of_range": float(event["poke_depth_fraction_of_range"]),
                "signal_close": float(event["signal_close"]),
                "signal_quote_volume": float(event["signal_quote_volume"]),
                "signal_trade_count": float(event["signal_trade_count"]),
                "signal_taker_buy_share": float(event["signal_taker_buy_share"]),
                "all_causal_signals_included": True,
                "untouched_2026_rows_used": 0,
            }
        )
    result = pd.DataFrame(rows)
    if result["event_id"].duplicated().any():
        raise ValueError("Desk event ids must be unique")
    return result


def write_desk_review(
    *,
    universe_path: Path = OUTPUT_DIR / "population.parquet",
    mechanics_path: Path = MECHANICS_DIR / "trades.parquet",
    output_dir: Path = DESK_DIR,
) -> pd.DataFrame:
    universe = pd.read_parquet(universe_path)
    mechanics = pd.read_parquet(mechanics_path)
    candidates = build_desk_candidates(universe, mechanics)
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates.to_parquet(output_dir / "candidates.parquet", index=False)
    report = {
        "desk_schema_version": DESK_SCHEMA_VERSION,
        "protocol_freeze_id": PROTOCOL_FREEZE_ID,
        "candidate_membership": "all causal universe events; no future selection",
        "events": len(candidates),
        "symbols": int(candidates["symbol"].nunique()),
        "mechanics_variants_per_event": int(mechanics["variant_id"].nunique()),
        "primary_variant_id": PRIMARY_VARIANT_ID,
        "review_questions": [question.as_payload() for question in SESSION_RECLAIM_REVIEW_QUESTIONS],
        "source_rows_from_2026": 0,
        "lookahead_audit": "PASS",
    }
    (output_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the complete session-reclaim Desk queue.")
    parser.add_argument("--universe", type=Path, default=OUTPUT_DIR / "population.parquet")
    parser.add_argument("--mechanics", type=Path, default=MECHANICS_DIR / "trades.parquet")
    parser.add_argument("--output-dir", type=Path, default=DESK_DIR)
    args = parser.parse_args()
    candidates = write_desk_review(
        universe_path=args.universe,
        mechanics_path=args.mechanics,
        output_dir=args.output_dir,
    )
    print(f"session-reclaim Desk events={len(candidates):,} -> {args.output_dir}")


if __name__ == "__main__":
    main()
