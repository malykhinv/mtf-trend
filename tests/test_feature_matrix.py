from __future__ import annotations

import csv
import math
from pathlib import Path

from anomaly_science.artifacts import write_csv_artifact
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus
from anomaly_science.contracts.market import Candle1m, LiquidationEvent, ONE_MINUTE_MS, OpenInterest5m, SymbolDayUniverseRow
from anomaly_science.contracts.state import AnomalyState1mRow
from anomaly_science.contracts.time import utc_ms_to_datetime
from anomaly_science.features import (
    FeatureMatrixConfig,
    alpha_decay_bucket,
    build_price_time_feature_matrix,
    feature_matrix_rows_to_artifact,
    run_mvp1_feature_matrix,
)
from anomaly_science.state.builder import state_rows_to_artifact


def test_alpha_decay_buckets_are_pre_registered() -> None:
    assert alpha_decay_bucket(0) == "0-2m"
    assert alpha_decay_bucket(2) == "0-2m"
    assert alpha_decay_bucket(3) == "3-5m"
    assert alpha_decay_bucket(6) == "6-10m"
    assert alpha_decay_bucket(11) == "11-20m"
    assert alpha_decay_bucket(21) == "21-40m"
    assert alpha_decay_bucket(41) == ">40m"


def test_price_time_feature_matrix_is_atr_normalized_and_asof_only() -> None:
    candles = list(_candles(count=1442))
    snapshot_time_ms = candles[-2].available_time_ms
    state = _state(snapshot_time_ms=snapshot_time_ms, current_close=candles[-2].close)
    future_candle = Candle1m(
        symbol="AAAUSDT",
        open_time_ms=candles[-1].open_time_ms,
        available_time_ms=candles[-1].available_time_ms,
        open=500.0,
        high=600.0,
        low=400.0,
        close=550.0,
        volume=1.0,
        quote_volume=550.0,
    )

    rows = build_price_time_feature_matrix(candles_1m=[*candles[:-1], future_candle], state_rows=[state])

    assert len(rows) == 1
    row = rows[0]
    assert row.snapshot_time_ms == snapshot_time_ms
    assert row.feature_cutoff_time_ms == snapshot_time_ms
    assert row.core_atr_1440 is not None
    assert row.ATR_1d_pct_asof_t is not None
    assert row.range_since_start_atr == (state.running_high_asof_t - state.running_low_asof_t) / row.core_atr_1440
    assert row.distance_to_running_high_atr == (state.running_high_asof_t - state.current_close) / row.core_atr_1440
    assert row.distance_to_running_low_atr == (state.current_close - state.running_low_asof_t) / row.core_atr_1440
    assert row.retracement_from_high_atr == row.distance_to_running_high_atr
    assert row.price_speed_atr is not None
    assert abs(row.price_speed_atr) < 100.0
    assert row.clock_maturity == state.time_since_running_high_minutes / 7
    assert row.event_age_ratio == state.minutes_since_detection / 60
    assert row.alpha_decay_bucket == "3-5m"
    assert row.feature_source_status == "ok"


def test_feature_matrix_leaves_atr_features_null_without_fallback() -> None:
    candles = list(_candles(count=10))
    state = _state(snapshot_time_ms=candles[-1].available_time_ms, current_close=candles[-1].close)

    rows = build_price_time_feature_matrix(candles_1m=candles, state_rows=[state])

    row = rows[0]
    assert row.core_atr_1440 is None
    assert row.ATR_1d_pct_asof_t is None
    assert row.range_since_start_atr is None
    assert row.price_speed_atr is None
    assert row.feature_source_status == "insufficient_atr_history"



def test_feature_matrix_materializes_volume_oi_liquidation_and_cvd_asof_only() -> None:
    candles = list(_candles(count=1446, with_taker=True, varying_volume=True))
    snapshot_time_ms = candles[-1].available_time_ms
    state = _state(snapshot_time_ms=snapshot_time_ms, current_close=candles[-1].close)
    future_liquidation = LiquidationEvent(
        symbol="AAAUSDT",
        event_time_ms=snapshot_time_ms + 1,
        available_time_ms=snapshot_time_ms + 1,
        side="short",
        price=120.0,
        quantity=1.0,
        quote_quantity=10_000.0,
        source="future",
    )
    open_interest = [
        OpenInterest5m(
            symbol="AAAUSDT",
            timestamp_ms=snapshot_time_ms - 15 * ONE_MINUTE_MS,
            available_time_ms=snapshot_time_ms - 10 * ONE_MINUTE_MS,
            open_interest=1000.0,
            source="fixture",
        ),
        OpenInterest5m(
            symbol="AAAUSDT",
            timestamp_ms=snapshot_time_ms - 10 * ONE_MINUTE_MS,
            available_time_ms=snapshot_time_ms - 5 * ONE_MINUTE_MS,
            open_interest=1100.0,
            source="fixture",
        ),
        OpenInterest5m(
            symbol="AAAUSDT",
            timestamp_ms=snapshot_time_ms - 5 * ONE_MINUTE_MS,
            available_time_ms=snapshot_time_ms,
            open_interest=1210.0,
            source="fixture",
        ),
        OpenInterest5m(
            symbol="AAAUSDT",
            timestamp_ms=snapshot_time_ms,
            available_time_ms=snapshot_time_ms + 5 * ONE_MINUTE_MS,
            open_interest=9999.0,
            source="future",
        ),
    ]
    liquidations = [
        LiquidationEvent(
            symbol="AAAUSDT",
            event_time_ms=snapshot_time_ms - 30_000,
            available_time_ms=snapshot_time_ms - 20_000,
            side="short",
            price=120.0,
            quantity=1.0,
            quote_quantity=20.0,
            source="fixture",
        ),
        LiquidationEvent(
            symbol="AAAUSDT",
            event_time_ms=snapshot_time_ms - 10_000,
            available_time_ms=snapshot_time_ms - 5_000,
            side="long",
            price=120.0,
            quantity=1.0,
            quote_quantity=5.0,
            source="fixture",
        ),
        future_liquidation,
    ]

    row = build_price_time_feature_matrix(
        candles_1m=candles,
        state_rows=[state],
        open_interest_5m=open_interest,
        liquidations=liquidations,
    )[0]

    assert row.quote_volume_1m_to_24h_median is not None
    assert row.volume_zscore is not None
    assert row.quote_volume_zscore is not None
    assert row.closed_5m_oi_asof_t == 1210.0
    assert row.oi_change_5m == 110.0
    assert row.oi_change_10m == 210.0
    assert row.oi_change_5m_pct_of_oi == 110.0 / 1210.0
    assert row.oi_change_10m_pct_of_oi == 210.0 / 1210.0
    assert row.missing_oi_flag is False
    assert row.short_liq_intensity == 20.0 / candles[-1].quote_volume
    assert row.long_liq_intensity == 5.0 / candles[-1].quote_volume
    assert row.liquidation_imbalance == (20.0 - 5.0) / 25.0
    assert row.cumulative_liq_intensity_since_event_start is not None
    assert row.missing_liquidation_flag is False
    assert row.cvd_quote_since_event_start is not None
    assert row.cvd_change_3m is not None
    assert row.cvd_change_5m is not None
    assert row.cvd_change_10m is not None
    assert row.cvd_price_divergence_5m is not None
    assert row.price_up_cvd_down_flag is True
    assert row.price_down_cvd_up_flag is False


def test_feature_matrix_materializes_relaxed_geometry_asof_only() -> None:
    candles = list(_candles(count=1446, with_taker=True, varying_volume=True))
    snapshot_time_ms = candles[-1].available_time_ms
    current = candles[-1]
    previous = candles[-2]
    shelf_low = current.close - 0.005
    shelf_high = current.close + 0.75
    assert previous.close < shelf_low < current.close
    state = AnomalyState1mRow(
        event_id="e_geometry",
        symbol="AAAUSDT",
        state_time_ms=snapshot_time_ms,
        snapshot_time_ms=snapshot_time_ms,
        feature_cutoff_time_ms=snapshot_time_ms,
        minutes_since_event_start=10,
        minutes_since_detection=5,
        event_alive=True,
        running_high_asof_t=current.close + 2.0,
        running_high_time_asof_t_ms=snapshot_time_ms - 3 * ONE_MINUTE_MS,
        running_low_asof_t=current.close - 3.0,
        running_low_time_asof_t_ms=snapshot_time_ms - 9 * ONE_MINUTE_MS,
        time_since_running_high_minutes=3,
        current_close=current.close,
        current_return_from_start=0.03,
        distance_to_running_high=(current.close / (current.close + 2.0)) - 1.0,
        distance_to_running_low=(current.close / (current.close - 3.0)) - 1.0,
        distance_to_structural_low=(current.close / shelf_low) - 1.0,
        distance_to_structural_high=(current.close / shelf_high) - 1.0,
        structural_low_asof_t=shelf_low,
        structural_low_time_asof_t_ms=snapshot_time_ms - 2 * ONE_MINUTE_MS,
        structural_high_asof_t=shelf_high,
        structural_high_time_asof_t_ms=snapshot_time_ms - 4 * ONE_MINUTE_MS,
    )
    open_interest = [
        OpenInterest5m(
            symbol="AAAUSDT",
            timestamp_ms=snapshot_time_ms - 5 * ONE_MINUTE_MS,
            available_time_ms=snapshot_time_ms,
            open_interest=1210.0,
            source="fixture",
        ),
        OpenInterest5m(
            symbol="AAAUSDT",
            timestamp_ms=snapshot_time_ms - 10 * ONE_MINUTE_MS,
            available_time_ms=snapshot_time_ms - 5 * ONE_MINUTE_MS,
            open_interest=1100.0,
            source="fixture",
        ),
    ]
    liquidations = [
        LiquidationEvent(
            symbol="AAAUSDT",
            event_time_ms=snapshot_time_ms - 30_000,
            available_time_ms=snapshot_time_ms - 20_000,
            side="short",
            price=current.close,
            quantity=1.0,
            quote_quantity=20.0,
            source="fixture",
        )
    ]

    row = build_price_time_feature_matrix(
        candles_1m=candles,
        state_rows=[state],
        open_interest_5m=open_interest,
        liquidations=liquidations,
    )[0]

    assert row.initial_pump_height_core_atr_1440 is not None
    assert row.post_pump_consolidation_minutes == 3
    assert row.consolidation_width_ratio is not None
    assert row.shelf_low_asof_t == shelf_low
    assert row.shelf_high_asof_t == shelf_high
    assert row.current_low_minus_shelf_low_core_atr_1440 is not None
    assert row.current_low_minus_shelf_low_core_atr_1440 < 0.0
    assert row.current_close_minus_shelf_low_core_atr_1440 is not None
    assert row.current_close_minus_shelf_low_core_atr_1440 > 0.0
    assert row.current_high_minus_shelf_high_core_atr_1440 is not None
    assert row.minutes_spent_below_shelf is not None
    assert row.minutes_since_reclaim == 0
    assert row.volume_on_sweep_percentile is not None
    assert 0.0 <= row.volume_on_sweep_percentile <= 1.0
    assert row.trade_count_on_sweep_percentile is not None
    assert row.cvd_change_during_sweep is not None
    assert row.oi_change_during_sweep == 110.0 / 1210.0
    assert row.liq_intensity_during_sweep == 20.0 / current.quote_volume


def test_feature_matrix_marks_missing_optional_flow_sources_without_fallback() -> None:
    candles = list(_candles(count=1442))
    state = _state(snapshot_time_ms=candles[-1].available_time_ms, current_close=candles[-1].close)

    row = build_price_time_feature_matrix(candles_1m=candles, state_rows=[state])[0]

    assert row.missing_oi_flag is True
    assert row.closed_5m_oi_asof_t is None
    assert row.missing_liquidation_flag is True
    assert row.short_liq_intensity is None
    assert row.cvd_quote_since_event_start is None
    assert row.price_up_cvd_down_flag is False


def test_feature_matrix_materializes_point_in_time_cross_section_percentiles() -> None:
    aaa = list(_candles_for_symbol(symbol="AAAUSDT", count=1446, base_price=100.0, volume_base=20.0))
    bbb = list(_candles_for_symbol(symbol="BBBUSDT", count=1446, base_price=80.0, volume_base=10.0))
    ccc = list(_candles_for_symbol(symbol="CCCUSDT", count=1446, base_price=60.0, volume_base=30.0))
    snapshot_time_ms = aaa[-1].available_time_ms
    trade_date = utc_ms_to_datetime(snapshot_time_ms).date().isoformat()
    states = [
        _state(snapshot_time_ms=snapshot_time_ms, current_close=aaa[-1].close),
        _state_for_symbol(
            event_id="e2",
            symbol="BBBUSDT",
            snapshot_time_ms=snapshot_time_ms,
            current_close=bbb[-1].close,
            current_return_from_start=0.01,
        ),
        _state_for_symbol(
            event_id="e3",
            symbol="CCCUSDT",
            snapshot_time_ms=snapshot_time_ms,
            current_close=ccc[-1].close,
            current_return_from_start=0.09,
        ),
    ]
    universe = [
        _universe_row(trade_date=trade_date, symbol="AAAUSDT"),
        _universe_row(trade_date=trade_date, symbol="BBBUSDT"),
        _universe_row(trade_date=trade_date, symbol="CCCUSDT"),
        _universe_row(trade_date=trade_date, symbol="FUTUREUSDT"),
    ]

    rows = build_price_time_feature_matrix(
        candles_1m=[*aaa, *bbb, *ccc],
        state_rows=states,
        symbol_universe_by_day=universe,
        config=FeatureMatrixConfig(volume_baseline_window_minutes=10, min_cross_section_symbols=3),
    )
    row = next(item for item in rows if item.symbol == "AAAUSDT")

    assert row.cross_section_available is True
    assert row.cross_section_symbol_count == 3
    assert row.volume_market_percentile == 0.5
    assert row.quote_volume_market_percentile == 1.0
    assert row.return_1m_market_percentile is not None
    assert row.return_from_event_market_percentile == 0.5
    assert row.range_expansion_market_percentile is not None
    assert 0.0 <= row.return_1m_market_percentile <= 1.0
    assert 0.0 <= row.range_expansion_market_percentile <= 1.0


def test_feature_matrix_excludes_universe_rows_not_cross_section_eligible() -> None:
    aaa = list(_candles_for_symbol(symbol="AAAUSDT", count=1446, base_price=100.0, volume_base=20.0))
    bbb = list(_candles_for_symbol(symbol="BBBUSDT", count=1446, base_price=80.0, volume_base=10.0))
    ccc = list(_candles_for_symbol(symbol="CCCUSDT", count=1446, base_price=60.0, volume_base=30.0))
    snapshot_time_ms = aaa[-1].available_time_ms
    trade_date = utc_ms_to_datetime(snapshot_time_ms).date().isoformat()
    states = [
        _state(snapshot_time_ms=snapshot_time_ms, current_close=aaa[-1].close),
        _state_for_symbol(event_id="e2", symbol="BBBUSDT", snapshot_time_ms=snapshot_time_ms, current_close=bbb[-1].close),
    ]
    universe = [
        _universe_row(trade_date=trade_date, symbol="AAAUSDT"),
        _universe_row(trade_date=trade_date, symbol="BBBUSDT"),
        SymbolDayUniverseRow(
            trade_date=trade_date,
            symbol="CCCUSDT",
            listed_asof_day=True,
            delisted_asof_day=False,
            tradable_on_day=True,
            has_1m_data=True,
            has_5m_data=True,
            has_oi_data=False,
            has_liquidation_data=False,
            liquidity_eligible_on_day=True,
            eligible_for_cross_section=False,
        ),
    ]

    row = build_price_time_feature_matrix(
        candles_1m=[*aaa, *bbb, *ccc],
        state_rows=states,
        symbol_universe_by_day=universe,
        config=FeatureMatrixConfig(volume_baseline_window_minutes=10, min_cross_section_symbols=2),
    )[0]

    assert row.cross_section_available is True
    assert row.cross_section_symbol_count == 2


def test_feature_matrix_leaves_cross_section_null_when_universe_is_missing() -> None:
    candles = list(_candles(count=1442))
    state = _state(snapshot_time_ms=candles[-1].available_time_ms, current_close=candles[-1].close)

    row = build_price_time_feature_matrix(candles_1m=candles, state_rows=[state])[0]

    assert row.cross_section_available is False
    assert row.cross_section_symbol_count == 0
    assert row.volume_market_percentile is None
    assert row.quote_volume_market_percentile is None
    assert row.return_1m_market_percentile is None
    assert row.return_from_event_market_percentile is None



def test_feature_matrix_materializes_btc_relative_and_systemic_cluster_features() -> None:
    aaa = list(_candles_for_symbol(symbol="AAAUSDT", count=1446, base_price=100.0, volume_base=20.0))
    bbb = list(_candles_for_symbol(symbol="BBBUSDT", count=1446, base_price=80.0, volume_base=10.0))
    ccc = list(_candles_for_symbol(symbol="CCCUSDT", count=1446, base_price=60.0, volume_base=30.0))
    btc = list(_candles_for_symbol(symbol="BTCUSDT", count=1446, base_price=50_000.0, volume_base=100.0))
    snapshot_time_ms = aaa[-1].available_time_ms
    trade_date = utc_ms_to_datetime(snapshot_time_ms).date().isoformat()
    states = [
        _state(snapshot_time_ms=snapshot_time_ms, current_close=aaa[-1].close),
        _state_for_symbol(
            event_id="e2",
            symbol="BBBUSDT",
            snapshot_time_ms=snapshot_time_ms,
            current_close=bbb[-1].close,
            current_return_from_start=0.01,
        ),
        _state_for_symbol(
            event_id="e3",
            symbol="CCCUSDT",
            snapshot_time_ms=snapshot_time_ms,
            current_close=ccc[-1].close,
            current_return_from_start=0.09,
        ),
    ]
    universe = [
        _universe_row(trade_date=trade_date, symbol="AAAUSDT"),
        _universe_row(trade_date=trade_date, symbol="BBBUSDT"),
        _universe_row(trade_date=trade_date, symbol="CCCUSDT"),
        _universe_row(trade_date=trade_date, symbol="BTCUSDT"),
    ]

    rows = build_price_time_feature_matrix(
        candles_1m=[*aaa, *bbb, *ccc, *btc],
        state_rows=states,
        symbol_universe_by_day=universe,
        config=FeatureMatrixConfig(volume_baseline_window_minutes=10, min_cross_section_symbols=3),
    )
    row = next(item for item in rows if item.symbol == "AAAUSDT")

    assert row.corr_with_btc_15m is not None
    assert row.corr_with_btc_30m is not None
    assert row.corr_with_btc_60m is not None
    assert row.symbol_return_minus_btc_return_5m is not None
    assert row.symbol_return_minus_btc_return_15m is not None
    assert row.idiosyncratic_momentum_score is not None
    assert row.idiosyncratic_momentum_score >= 0.0
    assert row.simultaneous_anomalies_count_1m == 3
    assert row.simultaneous_anomalies_share_1m == 3 / 4
    assert row.systemic_cluster_regime == "moderate_cluster"
    assert row.market_shock_id == f"market_shock:{snapshot_time_ms}"


def test_feature_matrix_leaves_btc_relative_null_without_btc_source_but_keeps_shock_id() -> None:
    aaa = list(_candles_for_symbol(symbol="AAAUSDT", count=1446, base_price=100.0, volume_base=20.0))
    snapshot_time_ms = aaa[-1].available_time_ms
    trade_date = utc_ms_to_datetime(snapshot_time_ms).date().isoformat()
    state = _state(snapshot_time_ms=snapshot_time_ms, current_close=aaa[-1].close)

    row = build_price_time_feature_matrix(
        candles_1m=aaa,
        state_rows=[state],
        symbol_universe_by_day=[_universe_row(trade_date=trade_date, symbol="AAAUSDT")],
        config=FeatureMatrixConfig(volume_baseline_window_minutes=10, min_cross_section_symbols=3),
    )[0]

    assert row.corr_with_btc_15m is None
    assert row.corr_with_btc_30m is None
    assert row.corr_with_btc_60m is None
    assert row.symbol_return_minus_btc_return_5m is None
    assert row.symbol_return_minus_btc_return_15m is None
    assert row.idiosyncratic_momentum_score is None
    assert row.simultaneous_anomalies_count_1m == 1
    assert row.simultaneous_anomalies_share_1m == 1.0
    assert row.systemic_cluster_regime == "idiosyncratic"
    assert row.market_shock_id == f"idiosyncratic:AAAUSDT:{snapshot_time_ms}"

def test_feature_matrix_artifact_schema_roundtrip() -> None:
    candles = list(_candles(count=1442))
    state = _state(snapshot_time_ms=candles[-1].available_time_ms, current_close=candles[-1].close)
    rows = build_price_time_feature_matrix(candles_1m=candles, state_rows=[state])
    payload = feature_matrix_rows_to_artifact(rows)
    schema = get_artifact_schema("anomaly_feature_matrix.csv")

    assert payload
    assert tuple(payload[0]) == schema.required_columns


def test_run_mvp1_feature_matrix_writes_artifacts(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    out_dir = tmp_path / "features"

    candles = list(_candles(count=1442))
    _write_candles_csv(input_dir / "candles_1m.csv", candles)
    state = _state(snapshot_time_ms=candles[-1].available_time_ms, current_close=candles[-1].close)
    write_csv_artifact(
        state_dir / "anomaly_state_1m.csv",
        state_rows_to_artifact([state]),
        get_artifact_schema("anomaly_state_1m.csv"),
    )

    out = run_mvp1_feature_matrix(
        input_dir=input_dir,
        state_path=state_dir / "anomaly_state_1m.csv",
        out_dir=out_dir,
        config=FeatureMatrixConfig(expected_event_lifetime_minutes=30),
    )

    matrix_path = out / "strategy_feature_matrix.csv"
    catalog_path = out / "strategy_feature_catalog.csv"
    audit_path = out / "strategy_protocol_audit.csv"
    run_config_path = out / "strategy_run_config.csv"
    manifest_path = out / "artifact_manifest.json"
    assert matrix_path.is_file()
    assert catalog_path.is_file()
    assert audit_path.is_file()
    assert run_config_path.is_file()
    assert (out / "anomaly_feature_matrix.csv").is_file()
    assert manifest_path.is_file()

    with matrix_path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        matrix_rows = list(csv.DictReader(file_obj))
    assert len(matrix_rows) == 1
    assert matrix_rows[0]["alpha_decay_bucket"] == "3-5m"
    assert float(matrix_rows[0]["event_age_ratio"]) == state.minutes_since_detection / 30

    with audit_path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        audit_rows = {row["check_name"]: row for row in csv.DictReader(file_obj)}
    assert audit_rows["relative_over_absolute_feature_contract_enforced"]["status"] == AuditStatus.PASS.value
    assert audit_rows["ATR_1d_asof_t_computed_from_closed_past_candles"]["status"] == AuditStatus.PASS.value
    assert audit_rows["market_shock_id_assigned"]["status"] == AuditStatus.PASS.value
    assert audit_rows["simultaneous_anomalies_count_1m_point_in_time"]["status"] == AuditStatus.PASS.value


def test_run_mvp1_feature_matrix_preserves_state_row_order_and_cleans_temp_files(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    out_dir = tmp_path / "features"

    aaa = list(_candles_for_symbol(symbol="AAAUSDT", count=1443, base_price=100.0, volume_base=20.0))
    bbb = list(_candles_for_symbol(symbol="BBBUSDT", count=1443, base_price=80.0, volume_base=10.0))
    _write_candles_csv(input_dir / "candles_1m.csv", [*aaa, *bbb])
    first_snapshot = aaa[-2].available_time_ms
    second_snapshot = aaa[-1].available_time_ms
    state_rows = [
        _state_for_symbol(
            event_id="a1",
            symbol="AAAUSDT",
            snapshot_time_ms=first_snapshot,
            current_close=aaa[-2].close,
        ),
        _state_for_symbol(
            event_id="b2",
            symbol="BBBUSDT",
            snapshot_time_ms=second_snapshot,
            current_close=bbb[-1].close,
        ),
        _state_for_symbol(
            event_id="b1",
            symbol="BBBUSDT",
            snapshot_time_ms=first_snapshot,
            current_close=bbb[-2].close,
        ),
    ]
    write_csv_artifact(
        state_dir / "strategy_state_1m.csv",
        state_rows_to_artifact(state_rows),
        get_artifact_schema("strategy_state_1m.csv"),
    )

    run_mvp1_feature_matrix(
        input_dir=input_dir,
        state_path=state_dir / "strategy_state_1m.csv",
        out_dir=out_dir,
        config=FeatureMatrixConfig(expected_event_lifetime_minutes=30),
    )

    with (out_dir / "strategy_feature_matrix.csv").open("r", encoding="utf-8-sig", newline="") as file_obj:
        matrix_rows = list(csv.DictReader(file_obj))
    ordered_keys = [
        (int(row["snapshot_time_ms"]), row["symbol"], row["event_id"])
        for row in matrix_rows
    ]
    assert ordered_keys == [
        (first_snapshot, "AAAUSDT", "a1"),
        (second_snapshot, "BBBUSDT", "b2"),
        (first_snapshot, "BBBUSDT", "b1"),
    ]
    assert not list(out_dir.glob("*.tmp*"))


def _candles(*, count: int, with_taker: bool = False, varying_volume: bool = False) -> tuple[Candle1m, ...]:
    rows: list[Candle1m] = []
    for index in range(count):
        open_price = 100.0 + index * 0.01
        volume = 10.0 + (index % 17) if varying_volume else 10.0
        quote_volume = volume * open_price
        taker_buy_quote_volume = quote_volume * 0.4 if with_taker else None
        rows.append(
            Candle1m(
                symbol="AAAUSDT",
                open_time_ms=index * ONE_MINUTE_MS,
                available_time_ms=(index + 1) * ONE_MINUTE_MS,
                open=open_price,
                high=open_price + 1.0,
                low=open_price - 1.0,
                close=open_price + 0.2,
                volume=volume,
                quote_volume=quote_volume,
                number_of_trades=float(10 + index % 7),
                taker_buy_quote_volume=taker_buy_quote_volume,
            )
        )
    return tuple(rows)


def _state(*, snapshot_time_ms: int, current_close: float) -> AnomalyState1mRow:
    return AnomalyState1mRow(
        event_id="e1",
        symbol="AAAUSDT",
        state_time_ms=snapshot_time_ms,
        snapshot_time_ms=snapshot_time_ms,
        feature_cutoff_time_ms=snapshot_time_ms,
        minutes_since_event_start=10,
        minutes_since_detection=5,
        event_alive=True,
        running_high_asof_t=current_close + 2.0,
        running_high_time_asof_t_ms=snapshot_time_ms - 3 * ONE_MINUTE_MS,
        running_low_asof_t=current_close - 3.0,
        running_low_time_asof_t_ms=snapshot_time_ms - 9 * ONE_MINUTE_MS,
        time_since_running_high_minutes=3,
        current_close=current_close,
        current_return_from_start=0.03,
        distance_to_running_high=(current_close / (current_close + 2.0)) - 1.0,
        distance_to_running_low=(current_close / (current_close - 3.0)) - 1.0,
        distance_to_structural_low=None,
        distance_to_structural_high=None,
    )



def _candles_for_symbol(*, symbol: str, count: int, base_price: float, volume_base: float) -> tuple[Candle1m, ...]:
    rows: list[Candle1m] = []
    for index in range(count):
        open_price = base_price + index * 0.01
        volume = volume_base + (index % 5)
        quote_volume = volume * open_price
        rows.append(
            Candle1m(
                symbol=symbol,
                open_time_ms=index * ONE_MINUTE_MS,
                available_time_ms=(index + 1) * ONE_MINUTE_MS,
                open=open_price,
                high=open_price + 1.0,
                low=open_price - 1.0,
                close=open_price + 0.2,
                volume=volume,
                quote_volume=quote_volume,
                number_of_trades=float(10 + index % 7),
                taker_buy_quote_volume=quote_volume * 0.5,
            )
        )
    return tuple(rows)


def _state_for_symbol(
    *,
    event_id: str,
    symbol: str,
    snapshot_time_ms: int,
    current_close: float,
    current_return_from_start: float = 0.01,
) -> AnomalyState1mRow:
    base = _state(snapshot_time_ms=snapshot_time_ms, current_close=current_close)
    return AnomalyState1mRow(
        event_id=event_id,
        symbol=symbol,
        state_time_ms=base.state_time_ms,
        snapshot_time_ms=base.snapshot_time_ms,
        feature_cutoff_time_ms=base.feature_cutoff_time_ms,
        minutes_since_event_start=base.minutes_since_event_start,
        minutes_since_detection=base.minutes_since_detection,
        event_alive=base.event_alive,
        running_high_asof_t=current_close + 2.0,
        running_high_time_asof_t_ms=base.running_high_time_asof_t_ms,
        running_low_asof_t=current_close - 3.0,
        running_low_time_asof_t_ms=base.running_low_time_asof_t_ms,
        time_since_running_high_minutes=base.time_since_running_high_minutes,
        current_close=current_close,
        current_return_from_start=current_return_from_start,
        distance_to_running_high=base.distance_to_running_high,
        distance_to_running_low=base.distance_to_running_low,
        distance_to_structural_low=None,
        distance_to_structural_high=None,
    )


def _universe_row(*, trade_date: str, symbol: str) -> SymbolDayUniverseRow:
    return SymbolDayUniverseRow(
        trade_date=trade_date,
        symbol=symbol,
        listed_asof_day=True,
        delisted_asof_day=False,
        tradable_on_day=True,
        has_1m_data=True,
        has_5m_data=True,
        has_oi_data=False,
        has_liquidation_data=False,
        liquidity_eligible_on_day=True,
    )

def _write_candles_csv(path: Path, candles: list[Candle1m]) -> None:
    fieldnames = [
        "symbol",
        "open_time_ms",
        "available_time_ms",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "taker_buy_quote_volume",
    ]
    with path.open("w", encoding="utf-8", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
        writer.writeheader()
        for candle in candles:
            writer.writerow({name: getattr(candle, name) for name in fieldnames})
