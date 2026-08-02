from __future__ import annotations

from dataclasses import asdict

import numpy as np
import pandas as pd
import pytest

from anomaly_science.contracts.enrichment import (
    CausalEventSelection,
    EventEnrichmentContractError,
    EventScopedEnrichmentRequest,
)
from anomaly_science.data.event_scoped import causal_event_enrichment_view
from anomaly_science.market_context.sessions import (
    UTC_SESSION_CALENDAR_VERSION,
    block_seq_for_ms,
    session_instance_for_ms,
)
from anomaly_science.strategy.residual_absorption.spec import (
    RESIDUAL_ABSORPTION_RESEARCH_SPLIT,
    ResidualAbsorptionResearchSpec,
)
from anomaly_science.strategy.session_break.research.sessions import BLOCKS
from anomaly_science.universe.session_liquidity import (
    SessionLiquidityUniverseConfig,
    build_session_liquidity_universe,
)
from anomaly_science.validation.research_split import ResearchSplitError

MINUTE_MS = 60_000


def _ms(value: str) -> int:
    return int(pd.Timestamp(value).timestamp() * 1_000)


def _session_bars(
    *,
    day: str,
    symbol: str,
    quote_per_minute: float,
    minutes: int = 300,
    start_hour: int = 8,
) -> list[dict[str, object]]:
    start = pd.Timestamp(f"{day} {start_hour:02d}:00:00Z")
    return [
        {
            "symbol": symbol,
            "open_time_ms": int((start + pd.Timedelta(minutes=offset)).timestamp() * 1_000),
            "available_time_ms": int((start + pd.Timedelta(minutes=offset + 1)).timestamp() * 1_000),
            "quote_volume": quote_per_minute,
        }
        for offset in range(minutes)
    ]


def _universe_fixture() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for day in ("2025-08-01", "2025-08-02", "2025-08-03"):
        rows.extend(_session_bars(day=day, symbol="AAAUSDT", quote_per_minute=100.0))
        rows.extend(_session_bars(day=day, symbol="BBBUSDT", quote_per_minute=200.0))
    rows.extend(
        _session_bars(
            day="2025-08-04",
            symbol="AAAUSDT",
            quote_per_minute=400.0,
            minutes=30,
        )
    )
    rows.extend(
        _session_bars(
            day="2025-08-04",
            symbol="BBBUSDT",
            quote_per_minute=200.0,
            minutes=30,
        )
    )
    return pd.DataFrame(rows)


def _universe_config() -> SessionLiquidityUniverseConfig:
    return SessionLiquidityUniverseConfig(
        history_same_type_sessions=3,
        minimum_core_history_sessions=2,
        minimum_expansion_history_sessions=2,
        core_top_n=1,
        expansion_top_n=1,
        expansion_liquidity_rank_ceiling=2,
        minimum_current_elapsed_minutes=5,
        expansion_minimum_activity_ratio=3.0,
    )


def test_registered_calendar_split_is_2025_and_oos_is_2026() -> None:
    split = RESIDUAL_ABSORPTION_RESEARCH_SPLIT

    assert split.partition_for_timestamp_ms(_ms("2025-06-03T00:00:00Z")) == "is"
    assert split.partition_for_timestamp_ms(_ms("2025-12-31T23:59:59.999Z")) == "is"
    assert split.partition_for_timestamp_ms(_ms("2026-01-01T00:00:00Z")) == "oos"
    assert split.partition_for_timestamp_ms(_ms("2026-06-17T23:59:59.999Z")) == "oos"
    assert split.partition_for_timestamp_ms(_ms("2026-06-18T00:00:00Z")) == "after_research"
    assert split.is_max_input_time_ms_exclusive == _ms("2026-01-01T00:00:00Z")

    with pytest.raises(ResearchSplitError, match="partition=oos"):
        split.require_is_timestamp_ms(_ms("2026-01-01T00:00:00Z"))
    with pytest.raises(ResearchSplitError, match="freeze"):
        split.require_oos_access(protocol_freeze_id="", access_approved=True)
    split.require_oos_access(protocol_freeze_id="frozen-v1", access_approved=True)


def test_shared_session_calendar_preserves_existing_five_blocks() -> None:
    timestamps = np.asarray(
        [
            _ms("2025-08-04T07:59:00Z"),
            _ms("2025-08-04T08:00:00Z"),
            _ms("2025-08-04T13:00:00Z"),
            _ms("2025-08-04T16:00:00Z"),
            _ms("2025-08-04T21:00:00Z"),
        ],
        dtype=np.int64,
    )

    assert block_seq_for_ms(timestamps).tolist() == [0, 1, 2, 3, 4]
    assert [block.name for block in BLOCKS] == ["ASIA", "EU", "OVERLAP", "US", "LATE"]
    instance = session_instance_for_ms(_ms("2025-08-04T08:10:00Z"))
    assert instance.block.name == "EU"
    assert instance.elapsed_minutes_at(_ms("2025-08-04T08:10:00Z")) == 10


def test_session_universe_uses_prior_same_type_sessions_and_elapsed_slice() -> None:
    snapshot = _ms("2025-08-04T08:10:00Z")
    rows = build_session_liquidity_universe(
        _universe_fixture(),
        snapshot_time_ms=snapshot,
        config=_universe_config(),
    )
    by_symbol = {row.symbol: row for row in rows}

    aaa = by_symbol["AAAUSDT"]
    bbb = by_symbol["BBBUSDT"]
    assert aaa.session_calendar_version == UTC_SESSION_CALENDAR_VERSION
    assert aaa.history_session_count == 3
    assert aaa.prior_elapsed_quote_volume_median == pytest.approx(1_000.0)
    assert aaa.current_partial_quote_volume == pytest.approx(4_000.0)
    assert aaa.current_activity_ratio == pytest.approx(4.0)
    assert aaa.activity_expansion_eligible is True
    assert aaa.core_eligible is False
    assert aaa.eligibility_channel == "activity_expansion"

    assert bbb.prior_session_quote_volume_median == pytest.approx(60_000.0)
    assert bbb.core_liquidity_rank == 1
    assert bbb.core_eligible is True
    assert bbb.current_activity_ratio == pytest.approx(1.0)
    assert bbb.activity_expansion_eligible is False


def test_session_universe_is_invariant_to_unseen_current_and_future_tail() -> None:
    snapshot = _ms("2025-08-04T08:10:00Z")
    frame = _universe_fixture()
    base = build_session_liquidity_universe(
        frame,
        snapshot_time_ms=snapshot,
        config=_universe_config(),
    )

    changed = frame.copy()
    future_mask = changed["available_time_ms"].gt(snapshot)
    changed.loc[future_mask, "quote_volume"] = 10**12
    changed = pd.concat(
        [
            changed,
            pd.DataFrame(
                _session_bars(
                    day="2025-08-05",
                    symbol="AAAUSDT",
                    quote_per_minute=10**15,
                )
            ),
        ],
        ignore_index=True,
    )
    replay = build_session_liquidity_universe(
        changed,
        snapshot_time_ms=snapshot,
        config=_universe_config(),
    )

    assert [asdict(row) for row in replay] == [asdict(row) for row in base]
    assert all(
        row.latest_input_available_time_ms is None
        or row.latest_input_available_time_ms <= snapshot
        for row in replay
    )


def test_event_scoped_high_resolution_is_selected_coarsely_then_sliced_asof() -> None:
    selection = CausalEventSelection(
        event_id="AAAUSDT:signal",
        symbol="AAAUSDT",
        selection_snapshot_time_ms=120_000,
        selection_feature_cutoff_time_ms=120_000,
        selection_granularity_ms=60_000,
        selection_schema_version="coarse_signal_v1",
    )
    request = EventScopedEnrichmentRequest(
        request_version="event_scoped_high_resolution_v1",
        selection=selection,
        source="candles_1s",
        enrichment_granularity_ms=1_000,
        requested_start_time_ms=60_000,
        requested_end_time_ms_exclusive=240_000,
    )
    frame = pd.DataFrame(
        {
            "symbol": ["AAAUSDT", "AAAUSDT", "AAAUSDT", "BBBUSDT"],
            "open_time_ms": [119_000, 120_000, 180_000, 119_000],
            "available_time_ms": [120_000, 121_000, 181_000, 120_000],
            "close": [1.0, 2.0, 999.0, 5.0],
        }
    )

    at_selection = causal_event_enrichment_view(
        frame,
        request=request,
        decision_snapshot_time_ms=120_000,
        event_time_column="open_time_ms",
        available_time_column="available_time_ms",
    )
    assert at_selection["open_time_ms"].tolist() == [119_000]

    mutated = frame.copy()
    mutated.loc[mutated["available_time_ms"].gt(120_000), "close"] = -10**9
    replay = causal_event_enrichment_view(
        mutated,
        request=request,
        decision_snapshot_time_ms=120_000,
        event_time_column="open_time_ms",
        available_time_column="available_time_ms",
    )
    pd.testing.assert_frame_equal(at_selection, replay)

    with pytest.raises(EventEnrichmentContractError, match="before"):
        causal_event_enrichment_view(
            frame,
            request=request,
            decision_snapshot_time_ms=119_000,
            event_time_column="open_time_ms",
            available_time_column="available_time_ms",
        )
    with pytest.raises(EventEnrichmentContractError, match="sub-minute"):
        CausalEventSelection(
            event_id="bad",
            symbol="AAAUSDT",
            selection_snapshot_time_ms=120_000,
            selection_feature_cutoff_time_ms=120_000,
            selection_granularity_ms=1_000,
            selection_schema_version="leaky_selection",
        )


def test_strategy_spec_registers_structural_distance_rejection() -> None:
    spec = ResidualAbsorptionResearchSpec()

    assert spec.research_split is RESIDUAL_ABSORPTION_RESEARCH_SPLIT
    assert spec.allowed_high_resolution_sources == ("candles_1s", "aggtrades")
    assert spec.distance_floor.primary_noise_quantile == 0.90
    assert spec.distance_floor.action_when_anchor_inside_floor == "reject_trade"
