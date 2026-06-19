from __future__ import annotations

import pytest

from anomaly_science.audit import TemporalAuditInput, audit_temporal_contract
from anomaly_science.contracts import (
    AnomalyFeatureMatrixRow,
    AnomalyState1mRow,
    AuditStatus,
    Candle1m,
    FeatureCatalogRow,
    FeatureFamily,
    FeatureMissingPolicy,
    FeatureNormalization,
    FuturePathRow,
    LiquidationEvent,
    OpenInterest5m,
    SymbolDayUniverseRow,
    TemporalContractError,
)
from anomaly_science.contracts.market import MarketDataContractError
from anomaly_science.contracts.time import SnapshotTiming


def test_snapshot_timing_enforces_feature_cutoff_before_snapshot() -> None:
    SnapshotTiming(snapshot_time_ms=120_000, feature_cutoff_time_ms=120_000)

    with pytest.raises(TemporalContractError):
        SnapshotTiming(snapshot_time_ms=120_000, feature_cutoff_time_ms=180_000)


def test_future_path_requires_future_start_after_snapshot() -> None:
    FuturePathRow(
        event_id="e1",
        symbol="AAA/USDT:USDT",
        snapshot_time_ms=120_000,
        feature_cutoff_time_ms=120_000,
        future_start_time_ms=180_000,
    )

    with pytest.raises(TemporalContractError):
        FuturePathRow(
            event_id="e1",
            symbol="AAA/USDT:USDT",
            snapshot_time_ms=120_000,
            feature_cutoff_time_ms=120_000,
            future_start_time_ms=120_000,
        )


def test_candle_contract_rejects_bad_ohlc_and_early_availability() -> None:
    Candle1m(
        symbol="AAA/USDT:USDT",
        open_time_ms=0,
        available_time_ms=60_000,
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=1.0,
        quote_volume=100.0,
        number_of_trades=10,
        taker_buy_quote_volume=55.0,
    )

    with pytest.raises(MarketDataContractError):
        Candle1m(
            symbol="AAA/USDT:USDT",
            open_time_ms=0,
            available_time_ms=59_999,
            open=100.0,
            high=102.0,
            low=99.0,
            close=101.0,
            volume=1.0,
            quote_volume=100.0,
        )

    with pytest.raises(MarketDataContractError):
        Candle1m(
            symbol="AAA/USDT:USDT",
            open_time_ms=0,
            available_time_ms=60_000,
            open=100.0,
            high=100.5,
            low=99.0,
            close=101.0,
            volume=1.0,
            quote_volume=100.0,
        )


def test_open_interest_is_closed_5m_only() -> None:
    OpenInterest5m(
        symbol="AAA/USDT:USDT",
        timestamp_ms=0,
        available_time_ms=300_000,
        open_interest=100.0,
        source="test",
    )

    with pytest.raises(MarketDataContractError):
        OpenInterest5m(
            symbol="AAA/USDT:USDT",
            timestamp_ms=0,
            available_time_ms=299_999,
            open_interest=100.0,
            source="test",
        )


def test_liquidation_event_is_timestamped_asof() -> None:
    LiquidationEvent(
        symbol="AAA/USDT:USDT",
        event_time_ms=100_000,
        available_time_ms=100_010,
        side="short",
        price=1.0,
        quantity=2.0,
        quote_quantity=2.0,
        source="test",
    )

    with pytest.raises(MarketDataContractError):
        LiquidationEvent(
            symbol="AAA/USDT:USDT",
            event_time_ms=100_000,
            available_time_ms=99_999,
            side="short",
            price=1.0,
            quantity=2.0,
            quote_quantity=2.0,
            source="test",
        )


def test_state_row_running_high_low_must_be_asof_snapshot() -> None:
    AnomalyState1mRow(
        event_id="e1",
        symbol="AAA/USDT:USDT",
        state_time_ms=180_000,
        snapshot_time_ms=180_000,
        feature_cutoff_time_ms=180_000,
        minutes_since_event_start=2,
        minutes_since_detection=1,
        event_alive=True,
        running_high_asof_t=105.0,
        running_high_time_asof_t_ms=120_000,
        running_low_asof_t=99.0,
        running_low_time_asof_t_ms=60_000,
        time_since_running_high_minutes=1,
        current_close=103.0,
        current_return_from_start=0.03,
        distance_to_running_high=-0.02,
        distance_to_running_low=0.04,
        distance_to_structural_low=None,
        distance_to_structural_high=None,
    )

    with pytest.raises(MarketDataContractError):
        AnomalyState1mRow(
            event_id="e1",
            symbol="AAA/USDT:USDT",
            state_time_ms=180_000,
            snapshot_time_ms=180_000,
            feature_cutoff_time_ms=180_000,
            minutes_since_event_start=2,
            minutes_since_detection=1,
            event_alive=True,
            running_high_asof_t=105.0,
            running_high_time_asof_t_ms=240_000,
            running_low_asof_t=99.0,
            running_low_time_asof_t_ms=60_000,
            time_since_running_high_minutes=1,
            current_close=103.0,
            current_return_from_start=0.03,
            distance_to_running_high=-0.02,
            distance_to_running_low=0.04,
            distance_to_structural_low=None,
            distance_to_structural_high=None,
        )


def test_symbol_universe_requires_exclusion_reason_for_non_tradable_day() -> None:
    row = SymbolDayUniverseRow(
        trade_date="2026-06-17",
        symbol="AAA/USDT:USDT",
        listed_asof_day=True,
        delisted_asof_day=False,
        tradable_on_day=True,
        has_1m_data=True,
        has_5m_data=True,
        has_oi_data=False,
        has_liquidation_data=False,
        liquidity_eligible_on_day=True,
        first_seen_data_time_ms=1_000,
        last_seen_data_time_ms=2_000,
        data_source_symbol_status="observed_on_day",
        listing_confidence="data_observed",
        delisting_confidence="unknown_without_external_metadata",
    )
    assert row.eligible_for_cross_section is True

    with pytest.raises(MarketDataContractError):
        SymbolDayUniverseRow(
            trade_date="2026-06-17",
            symbol="AAA/USDT:USDT",
            listed_asof_day=True,
            delisted_asof_day=False,
            tradable_on_day=False,
            has_1m_data=True,
            has_5m_data=True,
            has_oi_data=False,
            has_liquidation_data=False,
            liquidity_eligible_on_day=False,
        )


def test_symbol_universe_rejects_cross_section_eligibility_without_data() -> None:
    with pytest.raises(MarketDataContractError):
        SymbolDayUniverseRow(
            trade_date="2026-06-17",
            symbol="AAA/USDT:USDT",
            listed_asof_day=True,
            delisted_asof_day=False,
            tradable_on_day=False,
            has_1m_data=False,
            has_5m_data=False,
            has_oi_data=False,
            has_liquidation_data=False,
            liquidity_eligible_on_day=False,
            eligible_for_cross_section=True,
            reason_if_excluded="missing_1m_data;missing_5m_data",
        )


def test_feature_catalog_rejects_future_and_raw_model_features() -> None:
    FeatureCatalogRow(
        feature_schema_version="feature_schema_test",
        feature_name="return_from_start",
        feature_family=FeatureFamily.PRICE_PATH,
        source_artifact="anomaly_state_1m.csv",
        available_asof_time="computed from candles with available_time_ms <= snapshot_time_ms",
        uses_future_data=False,
        normalization_type=FeatureNormalization.DIMENSIONLESS_RATIO,
        is_model_feature=True,
        is_audit_field=True,
        missing_policy=FeatureMissingPolicy.NOT_NULL,
        dtype="float",
        description="Return from event start to snapshot.",
    )

    with pytest.raises(ValueError):
        FeatureCatalogRow(
            feature_schema_version="feature_schema_test",
            feature_name="future_max_60m_as_feature",
            feature_family=FeatureFamily.PRICE_PATH,
            source_artifact="future",
            available_asof_time="uses future window",
            uses_future_data=True,
            normalization_type=FeatureNormalization.ATR_NORMALIZED,
            is_model_feature=True,
            is_audit_field=False,
            missing_policy=FeatureMissingPolicy.NOT_NULL,
            dtype="float",
            description="invalid",
        )

    with pytest.raises(ValueError):
        FeatureCatalogRow(
            feature_schema_version="feature_schema_test",
            feature_name="raw_quote_volume",
            feature_family=FeatureFamily.VOLUME,
            source_artifact="candles_1m",
            available_asof_time="computed as-of snapshot",
            uses_future_data=False,
            normalization_type=FeatureNormalization.RAW_AUDIT_ONLY,
            is_model_feature=True,
            is_audit_field=False,
            missing_policy=FeatureMissingPolicy.NOT_NULL,
            dtype="float",
            description="invalid raw model feature",
        )


def test_feature_matrix_row_enforces_asof_and_registered_buckets() -> None:
    AnomalyFeatureMatrixRow(
        feature_schema_version="feature_schema_test",
        feature_matrix_version="matrix_v1",
        event_id="e1",
        symbol="AAAUSDT",
        snapshot_time_ms=120_000,
        feature_cutoff_time_ms=120_000,
        minutes_since_trigger=2,
        core_atr_1440=1.0,
        ATR_1d_pct_asof_t=0.01,
        current_return_from_start=0.02,
        range_since_start_atr=3.0,
        distance_to_running_high_atr=0.5,
        distance_to_running_low_atr=2.5,
        retracement_from_high_atr=0.5,
        price_speed_atr=1.2,
        clock_maturity=0.0,
        event_age_ratio=0.1,
        alpha_decay_bucket="0-2m",
        feature_source_status="ok",
    )

    with pytest.raises(TemporalContractError):
        AnomalyFeatureMatrixRow(
            feature_schema_version="feature_schema_test",
            feature_matrix_version="matrix_v1",
            event_id="e1",
            symbol="AAAUSDT",
            snapshot_time_ms=120_000,
            feature_cutoff_time_ms=180_000,
            minutes_since_trigger=2,
            core_atr_1440=1.0,
            ATR_1d_pct_asof_t=0.01,
            current_return_from_start=0.02,
            range_since_start_atr=3.0,
            distance_to_running_high_atr=0.5,
            distance_to_running_low_atr=2.5,
            retracement_from_high_atr=0.5,
            price_speed_atr=1.2,
            clock_maturity=0.0,
            event_age_ratio=0.1,
            alpha_decay_bucket="0-2m",
            feature_source_status="ok",
        )

    with pytest.raises(MarketDataContractError):
        AnomalyFeatureMatrixRow(
            feature_schema_version="feature_schema_test",
            feature_matrix_version="matrix_v1",
            event_id="e1",
            symbol="AAAUSDT",
            snapshot_time_ms=120_000,
            feature_cutoff_time_ms=120_000,
            minutes_since_trigger=2,
            core_atr_1440=1.0,
            ATR_1d_pct_asof_t=0.01,
            current_return_from_start=0.02,
            range_since_start_atr=3.0,
            distance_to_running_high_atr=0.5,
            distance_to_running_low_atr=2.5,
            retracement_from_high_atr=0.5,
            price_speed_atr=1.2,
            clock_maturity=0.0,
            event_age_ratio=0.1,
            alpha_decay_bucket="late-ish",
            feature_source_status="ok",
        )


def test_temporal_audit_reports_failures_without_throwing() -> None:
    rows = [
        TemporalAuditInput(
            check_name="ok",
            snapshot_time_ms=120_000,
            feature_cutoff_time_ms=120_000,
            future_start_time_ms=180_000,
        ),
        TemporalAuditInput(
            check_name="bad_future",
            snapshot_time_ms=120_000,
            feature_cutoff_time_ms=120_000,
            future_start_time_ms=120_000,
        ),
    ]

    result = audit_temporal_contract(rows)

    assert [row.status for row in result] == [AuditStatus.PASS, AuditStatus.FAIL]


def test_methodology_v2_audit_rows_emit_not_implemented_for_missing_checks() -> None:
    from anomaly_science.audit import METHODOLOGY_V2_REQUIRED_CHECKS, build_methodology_v2_audit_rows
    from anomaly_science.contracts.audit import ProtocolAuditRow

    rows = build_methodology_v2_audit_rows(
        stage="unit_test_stage",
        implemented=[
            ProtocolAuditRow(
                check_name="fixed_percent_labels_forbidden",
                status=AuditStatus.PASS,
                message="fixed-percent labels are not available",
            )
        ],
    )

    by_name = {row.check_name: row for row in rows}
    assert tuple(name for name in by_name if name != "protocol_interpretation_gate") == METHODOLOGY_V2_REQUIRED_CHECKS
    assert by_name["fixed_percent_labels_forbidden"].status == AuditStatus.PASS
    assert by_name["weekly_walk_forward_heavy_models_enforced"].status == AuditStatus.NOT_IMPLEMENTED
    assert "unit_test_stage" in by_name["weekly_walk_forward_heavy_models_enforced"].message
    assert by_name["protocol_interpretation_gate"].status == AuditStatus.WARN


def test_methodology_v2_audit_rows_are_stage_scoped_for_known_stages() -> None:
    from anomaly_science.audit import build_methodology_v2_audit_rows
    from anomaly_science.contracts.audit import ProtocolAuditRow

    rows = build_methodology_v2_audit_rows(
        stage="mvp1_future",
        implemented=[
            ProtocolAuditRow(
                check_name="fixed_percent_labels_forbidden",
                status=AuditStatus.PASS,
                message="fixed-percent labels are not emitted",
            )
        ],
    )

    by_name = {row.check_name: row for row in rows}
    assert tuple(name for name in by_name if name != "protocol_interpretation_gate") == (
        "ATR_1d_asof_t_computed_from_closed_past_candles",
        "fixed_percent_labels_forbidden",
        "intracandle_double_barrier_resolved_as_stop_loss_first",
    )
    assert by_name["fixed_percent_labels_forbidden"].status == AuditStatus.PASS
    assert "weekly_walk_forward_heavy_models_enforced" not in by_name
    assert by_name["protocol_interpretation_gate"].status == AuditStatus.WARN


def test_methodology_v2_audit_rejects_out_of_scope_known_check() -> None:
    from anomaly_science.audit import build_methodology_v2_audit_rows
    from anomaly_science.contracts.audit import ProtocolAuditRow

    with pytest.raises(ValueError, match="not required for stage mvp1_future"):
        build_methodology_v2_audit_rows(
            stage="mvp1_future",
            implemented=[
                ProtocolAuditRow(
                    check_name="weekly_walk_forward_heavy_models_enforced",
                    status=AuditStatus.PASS,
                    message="wrong stage",
                )
            ],
        )


def test_methodology_v2_audit_rejects_unknown_check_name() -> None:
    from anomaly_science.audit import build_methodology_v2_audit_rows
    from anomaly_science.contracts.audit import ProtocolAuditRow

    with pytest.raises(ValueError, match="unknown methodology-v2 check"):
        build_methodology_v2_audit_rows(
            stage="unit_test_stage",
            implemented=[ProtocolAuditRow(check_name="not_a_contract_check", status=AuditStatus.PASS, message="bad")],
        )
