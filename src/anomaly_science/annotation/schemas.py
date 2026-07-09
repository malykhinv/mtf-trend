"""Schemas for append-only market-structure annotation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ANNOTATION_CANDIDATE_SCHEMA_VERSION = "ohlcv_level_candidate_v1"
LEVEL_LABEL_SCHEMA_VERSION = "manual_level_annotation_v1"

REQUIRED_CANDIDATE_COLUMNS = (
    "event_id",
    "symbol",
    "tf",
    "review_start_ms",
    "review_end_ms",
)


@dataclass(frozen=True)
class LevelLabel:
    event_id: str
    symbol: str
    tf: str
    has_level: bool
    level_price: float | None
    level_start_ms: int | None
    level_end_ms: int | None
    family: str
    quality: str
    notes: str
    label_schema_version: str
    source: str
    saved_at_ms: int


def validate_label_payload(payload: dict[str, Any]) -> None:
    required = {"event_id", "symbol", "tf", "has_level"}
    missing = required - set(payload)
    if missing:
        raise ValueError(f"missing label fields: {sorted(missing)}")
    has_level = bool(payload["has_level"])
    for point in ("pump_start", "culmination", "entry", "exit"):
        time_key = f"{point}_ms"
        price_key = f"{point}_price"
        if payload.get(time_key) is not None and payload.get(price_key) is None:
            raise ValueError(f"{time_key} requires {price_key}")
        if payload.get(price_key) is not None and payload.get(time_key) is None:
            raise ValueError(f"{price_key} requires {time_key}")
    if has_level:
        for key in ("level_price", "level_start_ms", "level_end_ms"):
            if payload.get(key) is None:
                raise ValueError(f"level label requires {key}")
        if float(payload["level_price"]) <= 0:
            raise ValueError("level_price must be positive")
        if int(payload["level_start_ms"]) >= int(payload["level_end_ms"]):
            raise ValueError("level_start_ms must be before level_end_ms")
