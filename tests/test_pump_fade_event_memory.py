from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from anomaly_science.strategy.pump_fade.event_memory import (
    PumpEventMemoryRecord,
    build_event_memory_features,
    recurrence_chain_id,
)
from anomaly_science.strategy.pump_fade.event_memory_probability import (
    PUMP_FADE_EVENT_MEMORY_MODEL_FEATURES,
    load_pump_fade_event_memory_probability_config,
)


HOUR = 60 * 60 * 1_000


def _resolved(*, event_id: str = "S:1", resolution_time_ms: int = 3 * HOUR) -> PumpEventMemoryRecord:
    return PumpEventMemoryRecord(
        event_id=event_id,
        chain_id="S:recurrence:0",
        ignition_time_ms=HOUR,
        qualification_time_ms=HOUR + 60_000,
        base_level=100.0,
        qualification_high=105.0,
        qualification_size=0.05,
        resolution_time_ms=resolution_time_ms,
        faded=1,
        peak_level=110.0,
        peak_time_ms=2 * HOUR,
        resolution_close=99.0,
        turnover_to_resolution=1_000_000.0,
        trade_count_to_resolution=10_000.0,
        average_trade_notional_to_resolution=100.0,
        taker_buy_share_to_resolution=0.62,
        path_efficiency_to_resolution=0.4,
        max_1m_high_return_to_peak=0.08,
        max_1m_close_return_to_peak=0.06,
        mean_upper_wick_fraction_to_peak=0.2,
        max_upper_wick_fraction_to_peak=0.7,
    )


def test_event_memory_embargoes_prior_outcome_until_resolution() -> None:
    record = _resolved()

    before = build_event_memory_features(
        (record,),
        snapshot_time_ms=2 * HOUR + 30 * 60_000,
        current_base=100.0,
        current_anchor_high=108.0,
        current_close=106.0,
    )
    after = build_event_memory_features(
        (record,),
        snapshot_time_ms=4 * HOUR,
        current_base=100.0,
        current_anchor_high=108.0,
        current_close=106.0,
    )

    assert before["n_unresolved_prior_48h"] == 1.0
    assert before["n_prior_fades_48h"] == 0.0
    assert before["has_event_memory_48h"] is False
    assert before["last_prior_qualification_size"] == pytest.approx(0.05)
    assert after["n_unresolved_prior_48h"] == 0.0
    assert after["n_prior_fades_48h"] == 1.0
    assert after["prior_1_current_vs_peak"] == pytest.approx(106.0 / 110.0 - 1.0)
    assert after["prior_1_fade_depth_from_peak"] == pytest.approx(99.0 / 110.0 - 1.0)


def test_event_memory_ignores_future_changes_to_embargoed_summary() -> None:
    record = _resolved()
    changed_future = replace(
        record,
        peak_level=130.0,
        resolution_close=95.0,
        turnover_to_resolution=9_000_000.0,
    )
    kwargs = {
        "snapshot_time_ms": 2 * HOUR + 30 * 60_000,
        "current_base": 100.0,
        "current_anchor_high": 108.0,
        "current_close": 106.0,
    }

    assert build_event_memory_features((record,), **kwargs) == build_event_memory_features(
        (changed_future,), **kwargs
    )


def test_recurrence_chain_is_a_causal_48h_connected_component() -> None:
    record = _resolved()

    assert recurrence_chain_id(
        symbol="S",
        ignition_time_ms=record.ignition_time_ms + 47 * HOUR,
        prior_records=(record,),
    ) == record.chain_id
    assert recurrence_chain_id(
        symbol="S",
        ignition_time_ms=record.ignition_time_ms + 49 * HOUR,
        prior_records=(record,),
    ) != record.chain_id


def test_unresolved_record_rejects_finalized_fields() -> None:
    with pytest.raises(ValueError, match="unresolved.*finalized"):
        replace(
            _resolved(),
            resolution_time_ms=None,
            faded=None,
        )


def test_resolved_record_requires_flow_summary_even_when_value_may_be_nan() -> None:
    with pytest.raises(ValueError, match="complete summary"):
        replace(_resolved(), taker_buy_share_to_resolution=None)


def test_registered_event_memory_probability_protocol_matches_strategy() -> None:
    config = load_pump_fade_event_memory_probability_config(
        Path("research/pump_fade_event_memory_probability.json")
    )

    assert config.state_ordinals == (1, 2)
    assert config.event_memory_schema_version == "pump_fade_event_memory_v1"
    assert len(PUMP_FADE_EVENT_MEMORY_MODEL_FEATURES) > 70
