"""Schemas for append-only market-structure annotation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ANNOTATION_CANDIDATE_SCHEMA_VERSION = "ohlcv_level_candidate_v1"
# v2 introduces multiple independent setups per event (label["setups"]).
LEVEL_LABEL_SCHEMA_VERSION = "manual_level_annotation_v2"

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


def _validate_price_time_pair(setup: dict[str, Any], where: str, stem: str) -> None:
    time_key = f"{stem}_ms"
    price_key = f"{stem}_price"
    has_time = setup.get(time_key) is not None
    has_price = setup.get(price_key) is not None
    if has_time and not has_price:
        raise ValueError(f"{where}: {time_key} requires {price_key}")
    if has_price and not has_time:
        raise ValueError(f"{where}: {price_key} requires {time_key}")
    if has_price and float(setup[price_key]) <= 0:
        raise ValueError(f"{where}: {price_key} must be positive")


def _validate_setup(setup: dict[str, Any], where: str) -> None:
    if not isinstance(setup, dict):
        raise ValueError(f"{where} must be an object")
    has_level = bool(setup.get("has_level"))
    has_pump_transition = bool(setup.get("has_pump_transition"))
    has_structure_break = bool(setup.get("has_structure_break"))
    for point in ("pump_start", "culmination", "entry", "exit", "structure_break", "structure_swing_low"):
        _validate_price_time_pair(setup, where, point)
    if has_level:
        for key in ("level_price", "level_start_ms", "level_end_ms"):
            if setup.get(key) is None:
                raise ValueError(f"{where}: level setup requires {key}")
        if float(setup["level_price"]) <= 0:
            raise ValueError(f"{where}: level_price must be positive")
        if int(setup["level_start_ms"]) >= int(setup["level_end_ms"]):
            raise ValueError(f"{where}: level_start_ms must be before level_end_ms")
    elif any(setup.get(key) is not None for key in ("level_price", "level_start_ms", "level_end_ms")):
        raise ValueError(f"{where}: level fields require has_level=true")

    pump_complete = all(setup.get(key) is not None for key in ("pump_start_ms", "pump_start_price", "culmination_ms", "culmination_price"))
    if has_pump_transition != pump_complete:
        raise ValueError(f"{where}: has_pump_transition must match pump_start/culmination fields")

    structure_complete = all(
        setup.get(key) is not None
        for key in ("structure_break_ms", "structure_break_price", "structure_swing_low_ms", "structure_swing_low_price")
    )
    if has_structure_break != structure_complete:
        raise ValueError(f"{where}: has_structure_break must match structure swing fields")
    if has_structure_break and setup.get("family") != "structure_break":
        raise ValueError(f"{where}: structure swing fields require family=structure_break")

    zigzag = setup.get("zigzag_points")
    if zigzag is not None:
        if not isinstance(zigzag, list):
            raise ValueError(f"{where}: zigzag_points must be a list")
        for j, point in enumerate(zigzag):
            if not isinstance(point, dict) or point.get("ms") is None or point.get("price") is None:
                raise ValueError(f"{where}: zigzag point {j} needs ms and price")


def validate_label_payload(payload: dict[str, Any]) -> None:
    required = {"event_id", "symbol", "tf"}
    missing = required - set(payload)
    if missing:
        raise ValueError(f"missing label fields: {sorted(missing)}")
    setups = payload.get("setups")
    if setups is not None:
        if not isinstance(setups, list):
            raise ValueError("setups must be a list")
        if not setups:
            raise ValueError("setups must not be empty")
        for i, setup in enumerate(setups):
            _validate_setup(setup, f"setups[{i}]")
        return
    # Legacy single-setup payload (schema v1): the whole payload is one setup.
    if "has_level" not in payload:
        raise ValueError("missing label fields: ['has_level']")
    _validate_setup(payload, "label")
