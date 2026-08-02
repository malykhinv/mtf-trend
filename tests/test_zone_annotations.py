from __future__ import annotations

import pytest

from anomaly_science.annotation.schemas import validate_label_payload


def _payload(zone: dict) -> dict:
    return {
        "event_id": "evt", "symbol": "AAAUSDT", "tf": "15m",
        "setups": [{"family": "unknown", "quality": "ok", "notes": "", "has_level": False,
                     "has_pump_transition": False, "has_structure_break": False,
                     "zigzag_points": [], "zones": [zone]}],
    }


def test_seiden_zone_annotation_accepts_a_well_formed_demand_base() -> None:
    validate_label_payload(_payload({
        "pattern": "rbr", "kind": "demand", "base_start_ms": 1_000,
        "base_end_ms": 2_000, "lower_price": 10.0, "upper_price": 11.0,
        "boundary_mode": "wicks",
    }))


def test_seiden_zone_annotation_rejects_pattern_direction_conflict() -> None:
    with pytest.raises(ValueError, match="pattern/kind conflict"):
        validate_label_payload(_payload({
            "pattern": "rbr", "kind": "supply", "base_start_ms": 1_000,
            "base_end_ms": 2_000, "lower_price": 10.0, "upper_price": 11.0,
            "boundary_mode": "bodies",
        }))
