from __future__ import annotations

import csv
import math
import statistics
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from anomaly_science.artifacts import build_manifest, runtime_reproducibility_rows, write_csv_artifact_with_aliases, write_manifest
from anomaly_science.audit import build_methodology_v2_audit_rows
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow, RunConfigRow
from anomaly_science.contracts.features import AnomalyFeatureMatrixRow
from anomaly_science.contracts.market import FIVE_MINUTES_MS, Candle1m, LiquidationEvent, ONE_MINUTE_MS, OpenInterest5m, SymbolDayUniverseRow
from anomaly_science.contracts.state import AnomalyState1mRow
from anomaly_science.contracts.time import utc_ms_to_datetime
from anomaly_science.data.normalized import normalize_candles_1m, normalize_liquidations, normalize_open_interest_5m, normalize_symbol_universe_by_day
from anomaly_science.data.source import CsvDataSourceError, CsvDirectoryDataSource, MarketDataSource
from anomaly_science.features.catalog import FEATURE_SCHEMA_VERSION, build_default_feature_catalog, feature_rows_to_artifact
from anomaly_science.features.config import FeatureMatrixConfig
from anomaly_science.future.atr import AtrComputationError, compute_atr_1d_asof
from anomaly_science.future.builder import load_anomaly_state_1m_csv

EPS = 1e-12


def build_price_time_feature_matrix(
    *,
    candles_1m: Sequence[Candle1m] | Iterable[Candle1m],
    state_rows: Sequence[AnomalyState1mRow] | Iterable[AnomalyState1mRow],
    open_interest_5m: Sequence[OpenInterest5m] | Iterable[OpenInterest5m] | None = None,
    liquidations: Sequence[LiquidationEvent] | Iterable[LiquidationEvent] | None = None,
    symbol_universe_by_day: Sequence[SymbolDayUniverseRow] | Iterable[SymbolDayUniverseRow] | None = None,
    config: FeatureMatrixConfig | None = None,
) -> tuple[AnomalyFeatureMatrixRow, ...]:
    """Build as-of feature rows from state rows.

    This builder deliberately does not read `anomaly_future_paths.csv`. ATR and
    all rolling/self-history features are recomputed from normalized market data
    with `available_time_ms <= snapshot_time_ms`, so the feature matrix has no
    dependency on future outcome artifacts.
    """
    cfg = config or FeatureMatrixConfig()
    state_rows = tuple(state_rows)
    candles_by_symbol: dict[str, list[Candle1m]] = {}
    for candle in candles_1m:
        candles_by_symbol.setdefault(candle.symbol, []).append(candle)
    for symbol in candles_by_symbol:
        candles_by_symbol[symbol].sort(key=lambda item: (item.available_time_ms, item.open_time_ms))

    oi_by_symbol: dict[str, list[OpenInterest5m]] | None = None
    if open_interest_5m is not None:
        oi_by_symbol = {}
        for item in open_interest_5m:
            oi_by_symbol.setdefault(item.symbol, []).append(item)
        for symbol in oi_by_symbol:
            oi_by_symbol[symbol].sort(key=lambda item: (item.available_time_ms, item.timestamp_ms))

    liquidations_by_symbol: dict[str, list[LiquidationEvent]] | None = None
    if liquidations is not None:
        liquidations_by_symbol = {}
        for item in liquidations:
            liquidations_by_symbol.setdefault(item.symbol, []).append(item)
        for symbol in liquidations_by_symbol:
            liquidations_by_symbol[symbol].sort(key=lambda item: (item.available_time_ms, item.event_time_ms))

    universe_by_day = _universe_symbols_by_day(symbol_universe_by_day)
    states_by_snapshot: dict[int, list[AnomalyState1mRow]] = {}
    for state in state_rows:
        states_by_snapshot.setdefault(state.snapshot_time_ms, []).append(state)

    rows: list[AnomalyFeatureMatrixRow] = []
    for state in sorted(state_rows, key=lambda item: (item.symbol, item.snapshot_time_ms, item.event_id)):
        symbol_candles = candles_by_symbol.get(state.symbol, [])
        rows.append(
            _build_state_feature_row(
                state=state,
                candles=symbol_candles,
                open_interest_rows=None if oi_by_symbol is None else oi_by_symbol.get(state.symbol, []),
                liquidation_rows=None
                if liquidations_by_symbol is None
                else liquidations_by_symbol.get(state.symbol, []),
                candles_by_symbol=candles_by_symbol,
                open_interest_by_symbol=oi_by_symbol,
                liquidations_by_symbol=liquidations_by_symbol,
                universe_by_day=universe_by_day,
                states_at_snapshot=states_by_snapshot.get(state.snapshot_time_ms, []),
                config=cfg,
            )
        )
    return tuple(rows)


def build_price_time_feature_matrix_from_source(
    *,
    source: MarketDataSource,
    state_path: str | Path,
    config: FeatureMatrixConfig | None = None,
) -> tuple[AnomalyFeatureMatrixRow, ...]:
    frame = source.read_frame("candles_1m", required=True)
    if frame is None:
        raise CsvDataSourceError("required dataset 'candles_1m.csv' resolved to None")
    oi_frame = source.read_frame("open_interest_5m", required=False)
    liquidation_frame = source.read_frame("liquidations", required=False)
    universe_frame = source.read_frame("symbol_universe_by_day", required=False)
    state_rows = load_anomaly_state_1m_csv(state_path)
    return build_price_time_feature_matrix(
        candles_1m=normalize_candles_1m(frame),
        open_interest_5m=None if oi_frame is None else normalize_open_interest_5m(oi_frame),
        liquidations=None if liquidation_frame is None else normalize_liquidations(liquidation_frame),
        symbol_universe_by_day=None if universe_frame is None else normalize_symbol_universe_by_day(universe_frame),
        state_rows=state_rows,
        config=config,
    )


def feature_matrix_rows_to_artifact(rows: Sequence[AnomalyFeatureMatrixRow]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        payload = asdict(row)
        result.append({key: _csv_value(value) for key, value in payload.items()})
    return result


class AnomalyFeatureMatrixArtifactError(ValueError):
    """Raised when anomaly_feature_matrix.csv violates its declared schema."""


def load_anomaly_feature_matrix_csv(path: str | Path) -> tuple[AnomalyFeatureMatrixRow, ...]:
    """Read anomaly_feature_matrix.csv through the strict artifact schema."""
    feature_path = Path(path)
    if not feature_path.exists():
        raise AnomalyFeatureMatrixArtifactError(f"feature matrix artifact is missing: {feature_path}")

    schema = get_artifact_schema("anomaly_feature_matrix.csv")
    expected_columns = list(schema.required_columns)
    with feature_path.open(encoding="utf-8-sig", newline="") as file_obj:
        reader = csv.DictReader(file_obj)
        actual_columns = list(reader.fieldnames or [])
        if actual_columns != expected_columns:
            raise AnomalyFeatureMatrixArtifactError(
                f"feature matrix artifact columns must match {expected_columns}, got {actual_columns}"
            )
        rows: list[AnomalyFeatureMatrixRow] = []
        for row_index, row in enumerate(reader):
            try:
                rows.append(_feature_matrix_row_from_csv(row))
            except (TypeError, ValueError, MarketDataContractError) as exc:
                raise AnomalyFeatureMatrixArtifactError(
                    f"invalid anomaly_feature_matrix.csv row {row_index}: {exc}"
                ) from exc
    return tuple(rows)


def _feature_matrix_row_from_csv(row: Mapping[str, object]) -> AnomalyFeatureMatrixRow:
    return AnomalyFeatureMatrixRow(
        feature_schema_version=_required_str(row, "feature_schema_version"),
        feature_matrix_version=_required_str(row, "feature_matrix_version"),
        event_id=_required_str(row, "event_id"),
        symbol=_required_str(row, "symbol"),
        snapshot_time_ms=_required_int(row, "snapshot_time_ms"),
        feature_cutoff_time_ms=_required_int(row, "feature_cutoff_time_ms"),
        minutes_since_trigger=_required_int(row, "minutes_since_trigger"),
        ATR_1d_asof_t=_optional_float(row, "ATR_1d_asof_t"),
        ATR_1d_pct_asof_t=_optional_float(row, "ATR_1d_pct_asof_t"),
        current_return_from_start=_required_float(row, "current_return_from_start"),
        range_since_start_atr=_optional_float(row, "range_since_start_atr"),
        distance_to_running_high_atr=_optional_float(row, "distance_to_running_high_atr"),
        distance_to_running_low_atr=_optional_float(row, "distance_to_running_low_atr"),
        retracement_from_high_atr=_optional_float(row, "retracement_from_high_atr"),
        price_speed_atr=_optional_float(row, "price_speed_atr"),
        clock_maturity=_required_float(row, "clock_maturity"),
        event_age_ratio=_required_float(row, "event_age_ratio"),
        alpha_decay_bucket=_required_str(row, "alpha_decay_bucket"),
        feature_source_status=_required_str(row, "feature_source_status"),
        quote_volume_1m_to_24h_median=_optional_float(row, "quote_volume_1m_to_24h_median"),
        volume_zscore=_optional_float(row, "volume_zscore"),
        quote_volume_zscore=_optional_float(row, "quote_volume_zscore"),
        closed_5m_oi_asof_t=_optional_float(row, "closed_5m_oi_asof_t"),
        oi_change_5m=_optional_float(row, "oi_change_5m"),
        oi_change_10m=_optional_float(row, "oi_change_10m"),
        oi_change_5m_pct_of_oi=_optional_float(row, "oi_change_5m_pct_of_oi"),
        oi_change_10m_pct_of_oi=_optional_float(row, "oi_change_10m_pct_of_oi"),
        missing_oi_flag=_required_bool(row, "missing_oi_flag"),
        short_liq_intensity=_optional_float(row, "short_liq_intensity"),
        long_liq_intensity=_optional_float(row, "long_liq_intensity"),
        liquidation_imbalance=_optional_float(row, "liquidation_imbalance"),
        cumulative_liq_intensity_since_event_start=_optional_float(row, "cumulative_liq_intensity_since_event_start"),
        missing_liquidation_flag=_required_bool(row, "missing_liquidation_flag"),
        cvd_quote_since_event_start=_optional_float(row, "cvd_quote_since_event_start"),
        cvd_change_3m=_optional_float(row, "cvd_change_3m"),
        cvd_change_5m=_optional_float(row, "cvd_change_5m"),
        cvd_change_10m=_optional_float(row, "cvd_change_10m"),
        cvd_price_divergence_3m=_optional_float(row, "cvd_price_divergence_3m"),
        cvd_price_divergence_5m=_optional_float(row, "cvd_price_divergence_5m"),
        cvd_price_divergence_10m=_optional_float(row, "cvd_price_divergence_10m"),
        price_up_cvd_down_flag=_required_bool(row, "price_up_cvd_down_flag"),
        price_down_cvd_up_flag=_required_bool(row, "price_down_cvd_up_flag"),
        cvd_failed_to_confirm_high_flag=_required_bool(row, "cvd_failed_to_confirm_high_flag"),
        volume_market_percentile=_optional_float(row, "volume_market_percentile"),
        quote_volume_market_percentile=_optional_float(row, "quote_volume_market_percentile"),
        return_1m_market_percentile=_optional_float(row, "return_1m_market_percentile"),
        return_from_event_market_percentile=_optional_float(row, "return_from_event_market_percentile"),
        oi_growth_market_percentile=_optional_float(row, "oi_growth_market_percentile"),
        liq_intensity_market_percentile=_optional_float(row, "liq_intensity_market_percentile"),
        range_expansion_market_percentile=_optional_float(row, "range_expansion_market_percentile"),
        cross_section_available=_required_bool(row, "cross_section_available"),
        cross_section_symbol_count=_required_int(row, "cross_section_symbol_count"),
        corr_with_btc_15m=_optional_float(row, "corr_with_btc_15m"),
        corr_with_btc_30m=_optional_float(row, "corr_with_btc_30m"),
        corr_with_btc_60m=_optional_float(row, "corr_with_btc_60m"),
        symbol_return_minus_btc_return_5m=_optional_float(row, "symbol_return_minus_btc_return_5m"),
        symbol_return_minus_btc_return_15m=_optional_float(row, "symbol_return_minus_btc_return_15m"),
        idiosyncratic_momentum_score=_optional_float(row, "idiosyncratic_momentum_score"),
        simultaneous_anomalies_count_1m=_required_int(row, "simultaneous_anomalies_count_1m"),
        simultaneous_anomalies_share_1m=_optional_float(row, "simultaneous_anomalies_share_1m"),
        systemic_cluster_regime=_required_str(row, "systemic_cluster_regime"),
        market_shock_id=_required_str(row, "market_shock_id"),
    )


def run_mvp1_feature_matrix(
    *,
    input_dir: str | Path,
    state_path: str | Path,
    out_dir: str | Path,
    config: FeatureMatrixConfig | None = None,
) -> Path:
    input_path = Path(input_dir)
    state_artifact_path = Path(state_path)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    cfg = config or FeatureMatrixConfig()

    source = CsvDirectoryDataSource(input_path)
    state_rows = load_anomaly_state_1m_csv(state_artifact_path)
    matrix_rows = build_price_time_feature_matrix_from_source(
        source=source,
        state_path=state_artifact_path,
        config=cfg,
    )
    protocol_rows = _protocol_rows(state_row_count=len(state_rows), feature_row_count=len(matrix_rows))
    run_config_rows = _run_config_rows(
        input_path=input_path,
        state_path=state_artifact_path,
        output_path=output_path,
        config=cfg,
    )

    written: list[Path] = []
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "anomaly_feature_matrix.csv",
            feature_matrix_rows_to_artifact(matrix_rows),
            get_artifact_schema("anomaly_feature_matrix.csv"),
        )
    )
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "anomaly_feature_catalog.csv",
            feature_rows_to_artifact(build_default_feature_catalog()),
            get_artifact_schema("anomaly_feature_catalog.csv"),
        )
    )
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "anomaly_protocol_audit.csv",
            _protocol_rows_to_artifact(protocol_rows),
            get_artifact_schema("anomaly_protocol_audit.csv"),
        )
    )
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "anomaly_run_config.csv",
            [asdict(row) for row in run_config_rows],
            get_artifact_schema("anomaly_run_config.csv"),
        )
    )
    manifest = build_manifest(run_id=_run_id(), artifact_paths=written, root=output_path)
    write_manifest(output_path / "artifact_manifest.json", manifest)
    return output_path


def _build_state_feature_row(
    *,
    state: AnomalyState1mRow,
    candles: Sequence[Candle1m],
    open_interest_rows: Sequence[OpenInterest5m] | None,
    liquidation_rows: Sequence[LiquidationEvent] | None,
    candles_by_symbol: Mapping[str, Sequence[Candle1m]],
    open_interest_by_symbol: Mapping[str, Sequence[OpenInterest5m]] | None,
    liquidations_by_symbol: Mapping[str, Sequence[LiquidationEvent]] | None,
    universe_by_day: Mapping[str, set[str]],
    states_at_snapshot: Sequence[AnomalyState1mRow],
    config: FeatureMatrixConfig,
) -> AnomalyFeatureMatrixRow:
    atr_value: float | None = None
    atr_pct_value: float | None = None
    range_since_start_atr: float | None = None
    distance_to_running_high_atr: float | None = None
    distance_to_running_low_atr: float | None = None
    retracement_from_high_atr: float | None = None
    price_speed_atr: float | None = None
    status = "ok"

    try:
        atr = compute_atr_1d_asof(
            candles_1m=candles,
            symbol=state.symbol,
            snapshot_time_ms=state.snapshot_time_ms,
            atr_window_minutes=config.atr_window_minutes,
        )
        atr_value = atr.atr_1d_asof_t
        atr_pct_value = atr.atr_1d_pct_asof_t
    except AtrComputationError:
        status = "insufficient_atr_history"

    if atr_value is not None:
        range_since_start_atr = (state.running_high_asof_t - state.running_low_asof_t) / atr_value
        distance_to_running_high_atr = max(state.running_high_asof_t - state.current_close, 0.0) / atr_value
        distance_to_running_low_atr = max(state.current_close - state.running_low_asof_t, 0.0) / atr_value
        retracement_from_high_atr = distance_to_running_high_atr
        price_speed_atr = _price_speed_atr(
            candles=candles,
            snapshot_time_ms=state.snapshot_time_ms,
            atr_value=atr_value,
            atr_window_minutes=config.atr_window_minutes,
        )
        if price_speed_atr is None and status == "ok":
            status = "missing_previous_close_for_speed"

    volume_features = _volume_features(candles=candles, state=state, config=config)
    oi_features = _oi_features(open_interest_rows=open_interest_rows, snapshot_time_ms=state.snapshot_time_ms)
    liquidation_features = _liquidation_features(
        candles=candles,
        liquidation_rows=liquidation_rows,
        state=state,
    )
    cvd_features = _cvd_features(
        candles=candles,
        state=state,
        atr_value=atr_value,
        windows_minutes=config.cvd_windows_minutes,
    )
    cross_section_features = _cross_section_features(
        state=state,
        candles_by_symbol=candles_by_symbol,
        open_interest_by_symbol=open_interest_by_symbol,
        liquidations_by_symbol=liquidations_by_symbol,
        universe_by_day=universe_by_day,
        states_at_snapshot=states_at_snapshot,
        min_cross_section_symbols=config.min_cross_section_symbols,
    )
    market_context_features = _market_context_features(
        state=state,
        symbol_candles=candles,
        btc_candles=candles_by_symbol.get(config.btc_symbol, ()),
        states_at_snapshot=states_at_snapshot,
        cross_section_symbol_count=cross_section_features.cross_section_symbol_count,
        volume_market_percentile=cross_section_features.volume_market_percentile,
        ATR_1d_pct_asof_t=atr_pct_value,
        corr_windows_minutes=config.btc_corr_window_minutes,
        return_windows_minutes=config.btc_relative_return_windows_minutes,
        moderate_cluster_min_count=config.moderate_cluster_min_count,
        systemic_cluster_min_count=config.systemic_cluster_min_count,
    )

    time_to_running_high = max(state.minutes_since_event_start - state.time_since_running_high_minutes, 0)
    clock_maturity = state.time_since_running_high_minutes / max(time_to_running_high, 1)
    event_age_ratio = state.minutes_since_detection / max(config.expected_event_lifetime_minutes, 1)

    return AnomalyFeatureMatrixRow(
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        feature_matrix_version=config.feature_matrix_version,
        event_id=state.event_id,
        symbol=state.symbol,
        snapshot_time_ms=state.snapshot_time_ms,
        feature_cutoff_time_ms=state.feature_cutoff_time_ms,
        minutes_since_trigger=state.minutes_since_detection,
        ATR_1d_asof_t=atr_value,
        ATR_1d_pct_asof_t=atr_pct_value,
        current_return_from_start=state.current_return_from_start,
        range_since_start_atr=range_since_start_atr,
        distance_to_running_high_atr=distance_to_running_high_atr,
        distance_to_running_low_atr=distance_to_running_low_atr,
        retracement_from_high_atr=retracement_from_high_atr,
        price_speed_atr=price_speed_atr,
        clock_maturity=clock_maturity,
        event_age_ratio=event_age_ratio,
        alpha_decay_bucket=alpha_decay_bucket(state.minutes_since_detection),
        feature_source_status=status,
        quote_volume_1m_to_24h_median=volume_features.quote_volume_1m_to_24h_median,
        volume_zscore=volume_features.volume_zscore,
        quote_volume_zscore=volume_features.quote_volume_zscore,
        closed_5m_oi_asof_t=oi_features.closed_5m_oi_asof_t,
        oi_change_5m=oi_features.oi_change_5m,
        oi_change_10m=oi_features.oi_change_10m,
        oi_change_5m_pct_of_oi=oi_features.oi_change_5m_pct_of_oi,
        oi_change_10m_pct_of_oi=oi_features.oi_change_10m_pct_of_oi,
        missing_oi_flag=oi_features.missing_oi_flag,
        short_liq_intensity=liquidation_features.short_liq_intensity,
        long_liq_intensity=liquidation_features.long_liq_intensity,
        liquidation_imbalance=liquidation_features.liquidation_imbalance,
        cumulative_liq_intensity_since_event_start=liquidation_features.cumulative_liq_intensity_since_event_start,
        missing_liquidation_flag=liquidation_features.missing_liquidation_flag,
        cvd_quote_since_event_start=cvd_features.cvd_quote_since_event_start,
        cvd_change_3m=cvd_features.cvd_change_by_window.get(3),
        cvd_change_5m=cvd_features.cvd_change_by_window.get(5),
        cvd_change_10m=cvd_features.cvd_change_by_window.get(10),
        cvd_price_divergence_3m=cvd_features.cvd_price_divergence_by_window.get(3),
        cvd_price_divergence_5m=cvd_features.cvd_price_divergence_by_window.get(5),
        cvd_price_divergence_10m=cvd_features.cvd_price_divergence_by_window.get(10),
        price_up_cvd_down_flag=cvd_features.price_up_cvd_down_flag,
        price_down_cvd_up_flag=cvd_features.price_down_cvd_up_flag,
        cvd_failed_to_confirm_high_flag=cvd_features.cvd_failed_to_confirm_high_flag,
        volume_market_percentile=cross_section_features.volume_market_percentile,
        quote_volume_market_percentile=cross_section_features.quote_volume_market_percentile,
        return_1m_market_percentile=cross_section_features.return_1m_market_percentile,
        return_from_event_market_percentile=cross_section_features.return_from_event_market_percentile,
        oi_growth_market_percentile=cross_section_features.oi_growth_market_percentile,
        liq_intensity_market_percentile=cross_section_features.liq_intensity_market_percentile,
        range_expansion_market_percentile=cross_section_features.range_expansion_market_percentile,
        cross_section_available=cross_section_features.cross_section_available,
        cross_section_symbol_count=cross_section_features.cross_section_symbol_count,
        corr_with_btc_15m=market_context_features.corr_with_btc_by_window.get(15),
        corr_with_btc_30m=market_context_features.corr_with_btc_by_window.get(30),
        corr_with_btc_60m=market_context_features.corr_with_btc_by_window.get(60),
        symbol_return_minus_btc_return_5m=market_context_features.symbol_return_minus_btc_by_window.get(5),
        symbol_return_minus_btc_return_15m=market_context_features.symbol_return_minus_btc_by_window.get(15),
        idiosyncratic_momentum_score=market_context_features.idiosyncratic_momentum_score,
        simultaneous_anomalies_count_1m=market_context_features.simultaneous_anomalies_count_1m,
        simultaneous_anomalies_share_1m=market_context_features.simultaneous_anomalies_share_1m,
        systemic_cluster_regime=market_context_features.systemic_cluster_regime,
        market_shock_id=market_context_features.market_shock_id,
    )


def alpha_decay_bucket(minutes_since_trigger: int) -> str:
    if minutes_since_trigger < 0:
        raise ValueError("minutes_since_trigger must be non-negative")
    if minutes_since_trigger <= 2:
        return "0-2m"
    if minutes_since_trigger <= 5:
        return "3-5m"
    if minutes_since_trigger <= 10:
        return "6-10m"
    if minutes_since_trigger <= 20:
        return "11-20m"
    if minutes_since_trigger <= 40:
        return "21-40m"
    return ">40m"


def _price_speed_atr(
    *,
    candles: Sequence[Candle1m],
    snapshot_time_ms: int,
    atr_value: float,
    atr_window_minutes: int,
) -> float | None:
    asof_candles = _asof_candles(candles=candles, snapshot_time_ms=snapshot_time_ms)
    if len(asof_candles) < 2:
        return None
    current = asof_candles[-1]
    previous = asof_candles[-2]
    if current.available_time_ms != snapshot_time_ms:
        return None
    expected_one_minute_atr = atr_value / max(atr_window_minutes, 1)
    if not math.isfinite(expected_one_minute_atr) or expected_one_minute_atr <= 0:
        return None
    return (current.close - previous.close) / expected_one_minute_atr


class _VolumeFeatures:
    def __init__(
        self,
        *,
        quote_volume_1m_to_24h_median: float | None,
        volume_zscore: float | None,
        quote_volume_zscore: float | None,
    ) -> None:
        self.quote_volume_1m_to_24h_median = quote_volume_1m_to_24h_median
        self.volume_zscore = volume_zscore
        self.quote_volume_zscore = quote_volume_zscore


def _volume_features(*, candles: Sequence[Candle1m], state: AnomalyState1mRow, config: FeatureMatrixConfig) -> _VolumeFeatures:
    current = _current_candle(candles=candles, snapshot_time_ms=state.snapshot_time_ms)
    if current is None:
        return _VolumeFeatures(quote_volume_1m_to_24h_median=None, volume_zscore=None, quote_volume_zscore=None)
    history = [candle for candle in candles if candle.available_time_ms < current.available_time_ms]
    history.sort(key=lambda item: (item.available_time_ms, item.open_time_ms))
    history = history[-config.volume_baseline_window_minutes :]
    if len(history) < config.volume_baseline_window_minutes:
        return _VolumeFeatures(quote_volume_1m_to_24h_median=None, volume_zscore=None, quote_volume_zscore=None)

    quote_values = [candle.quote_volume for candle in history]
    volume_values = [candle.volume for candle in history]
    quote_median = statistics.median(quote_values)
    quote_ratio = None if quote_median <= 0 else current.quote_volume / quote_median
    return _VolumeFeatures(
        quote_volume_1m_to_24h_median=quote_ratio,
        volume_zscore=_zscore(current.volume, volume_values),
        quote_volume_zscore=_zscore(current.quote_volume, quote_values),
    )


class _OiFeatures:
    def __init__(
        self,
        *,
        closed_5m_oi_asof_t: float | None,
        oi_change_5m: float | None,
        oi_change_10m: float | None,
        oi_change_5m_pct_of_oi: float | None,
        oi_change_10m_pct_of_oi: float | None,
        missing_oi_flag: bool,
    ) -> None:
        self.closed_5m_oi_asof_t = closed_5m_oi_asof_t
        self.oi_change_5m = oi_change_5m
        self.oi_change_10m = oi_change_10m
        self.oi_change_5m_pct_of_oi = oi_change_5m_pct_of_oi
        self.oi_change_10m_pct_of_oi = oi_change_10m_pct_of_oi
        self.missing_oi_flag = missing_oi_flag


def _oi_features(*, open_interest_rows: Sequence[OpenInterest5m] | None, snapshot_time_ms: int) -> _OiFeatures:
    if open_interest_rows is None:
        return _missing_oi_features()
    asof_rows = [item for item in open_interest_rows if item.available_time_ms <= snapshot_time_ms]
    if not asof_rows:
        return _missing_oi_features()
    asof_rows.sort(key=lambda item: (item.timestamp_ms, item.available_time_ms))
    latest = asof_rows[-1]
    prev_5m = _last_oi_at_or_before(asof_rows, latest.timestamp_ms - FIVE_MINUTES_MS)
    prev_10m = _last_oi_at_or_before(asof_rows, latest.timestamp_ms - 2 * FIVE_MINUTES_MS)
    change_5m = None if prev_5m is None else latest.open_interest - prev_5m.open_interest
    change_10m = None if prev_10m is None else latest.open_interest - prev_10m.open_interest
    pct_5m = None if change_5m is None or latest.open_interest <= 0 else change_5m / latest.open_interest
    pct_10m = None if change_10m is None or latest.open_interest <= 0 else change_10m / latest.open_interest
    return _OiFeatures(
        closed_5m_oi_asof_t=latest.open_interest,
        oi_change_5m=change_5m,
        oi_change_10m=change_10m,
        oi_change_5m_pct_of_oi=pct_5m,
        oi_change_10m_pct_of_oi=pct_10m,
        missing_oi_flag=False,
    )


def _missing_oi_features() -> _OiFeatures:
    return _OiFeatures(
        closed_5m_oi_asof_t=None,
        oi_change_5m=None,
        oi_change_10m=None,
        oi_change_5m_pct_of_oi=None,
        oi_change_10m_pct_of_oi=None,
        missing_oi_flag=True,
    )


class _LiquidationFeatures:
    def __init__(
        self,
        *,
        short_liq_intensity: float | None,
        long_liq_intensity: float | None,
        liquidation_imbalance: float | None,
        cumulative_liq_intensity_since_event_start: float | None,
        missing_liquidation_flag: bool,
    ) -> None:
        self.short_liq_intensity = short_liq_intensity
        self.long_liq_intensity = long_liq_intensity
        self.liquidation_imbalance = liquidation_imbalance
        self.cumulative_liq_intensity_since_event_start = cumulative_liq_intensity_since_event_start
        self.missing_liquidation_flag = missing_liquidation_flag


def _liquidation_features(
    *,
    candles: Sequence[Candle1m],
    liquidation_rows: Sequence[LiquidationEvent] | None,
    state: AnomalyState1mRow,
) -> _LiquidationFeatures:
    if liquidation_rows is None:
        return _missing_liquidation_features()
    current = _current_candle(candles=candles, snapshot_time_ms=state.snapshot_time_ms)
    if current is None:
        return _missing_liquidation_features()
    minute_start_ms = state.snapshot_time_ms - ONE_MINUTE_MS
    asof_rows = [item for item in liquidation_rows if item.available_time_ms <= state.snapshot_time_ms]
    minute_rows = [item for item in asof_rows if minute_start_ms <= item.event_time_ms < state.snapshot_time_ms]
    short_quote = sum(item.quote_quantity for item in minute_rows if item.side == "short")
    long_quote = sum(item.quote_quantity for item in minute_rows if item.side == "long")
    total_quote = short_quote + long_quote
    quote_volume = max(current.quote_volume, EPS)
    event_start_time_ms = _event_start_time_ms(state)
    cumulative_liq_quote = sum(
        item.quote_quantity for item in asof_rows if event_start_time_ms <= item.event_time_ms < state.snapshot_time_ms
    )
    cumulative_quote_volume = sum(
        candle.quote_volume
        for candle in candles
        if candle.open_time_ms >= event_start_time_ms and candle.available_time_ms <= state.snapshot_time_ms
    )
    return _LiquidationFeatures(
        short_liq_intensity=short_quote / quote_volume,
        long_liq_intensity=long_quote / quote_volume,
        liquidation_imbalance=0.0 if total_quote <= 0 else (short_quote - long_quote) / total_quote,
        cumulative_liq_intensity_since_event_start=None
        if cumulative_quote_volume <= 0
        else cumulative_liq_quote / cumulative_quote_volume,
        missing_liquidation_flag=False,
    )


def _missing_liquidation_features() -> _LiquidationFeatures:
    return _LiquidationFeatures(
        short_liq_intensity=None,
        long_liq_intensity=None,
        liquidation_imbalance=None,
        cumulative_liq_intensity_since_event_start=None,
        missing_liquidation_flag=True,
    )


class _CvdFeatures:
    def __init__(
        self,
        *,
        cvd_quote_since_event_start: float | None,
        cvd_change_by_window: dict[int, float | None],
        cvd_price_divergence_by_window: dict[int, float | None],
        price_up_cvd_down_flag: bool,
        price_down_cvd_up_flag: bool,
        cvd_failed_to_confirm_high_flag: bool,
    ) -> None:
        self.cvd_quote_since_event_start = cvd_quote_since_event_start
        self.cvd_change_by_window = cvd_change_by_window
        self.cvd_price_divergence_by_window = cvd_price_divergence_by_window
        self.price_up_cvd_down_flag = price_up_cvd_down_flag
        self.price_down_cvd_up_flag = price_down_cvd_up_flag
        self.cvd_failed_to_confirm_high_flag = cvd_failed_to_confirm_high_flag


def _cvd_features(
    *,
    candles: Sequence[Candle1m],
    state: AnomalyState1mRow,
    atr_value: float | None,
    windows_minutes: tuple[int, ...],
) -> _CvdFeatures:
    current = _current_candle(candles=candles, snapshot_time_ms=state.snapshot_time_ms)
    missing = _CvdFeatures(
        cvd_quote_since_event_start=None,
        cvd_change_by_window={window: None for window in windows_minutes},
        cvd_price_divergence_by_window={window: None for window in windows_minutes},
        price_up_cvd_down_flag=False,
        price_down_cvd_up_flag=False,
        cvd_failed_to_confirm_high_flag=False,
    )
    if current is None:
        return missing
    event_start_time_ms = _event_start_time_ms(state)
    event_candles = [
        candle
        for candle in candles
        if candle.open_time_ms >= event_start_time_ms and candle.available_time_ms <= state.snapshot_time_ms
    ]
    event_candles.sort(key=lambda item: (item.available_time_ms, item.open_time_ms))
    if not event_candles or any(candle.taker_buy_quote_volume is None for candle in event_candles):
        return missing
    cumulative_quote = sum(candle.quote_volume for candle in event_candles)
    if cumulative_quote <= 0:
        return missing
    cvd_since_event = sum(_candle_delta_quote(candle) for candle in event_candles) / cumulative_quote

    cvd_change_by_window: dict[int, float | None] = {}
    divergence_by_window: dict[int, float | None] = {}
    price_up_cvd_down = False
    price_down_cvd_up = False
    for window in windows_minutes:
        window_candles = _window_candles(candles=candles, snapshot_time_ms=state.snapshot_time_ms, window_minutes=window)
        cvd_change = _window_cvd_ratio(window_candles)
        cvd_change_by_window[window] = cvd_change
        price_change_atr = _window_price_change_atr(window_candles=window_candles, atr_value=atr_value)
        divergence_by_window[window] = None if cvd_change is None or price_change_atr is None else price_change_atr - cvd_change
        if window == 5 and cvd_change is not None and window_candles:
            raw_price_change = window_candles[-1].close - window_candles[0].open
            price_up_cvd_down = raw_price_change > 0 and cvd_change < 0
            price_down_cvd_up = raw_price_change < 0 and cvd_change > 0

    cvd5 = cvd_change_by_window.get(5)
    failed_to_confirm_high = bool(cvd5 is not None and cvd5 <= 0 and math.isclose(current.close, state.running_high_asof_t, rel_tol=0.0, abs_tol=EPS))
    return _CvdFeatures(
        cvd_quote_since_event_start=cvd_since_event,
        cvd_change_by_window=cvd_change_by_window,
        cvd_price_divergence_by_window=divergence_by_window,
        price_up_cvd_down_flag=price_up_cvd_down,
        price_down_cvd_up_flag=price_down_cvd_up,
        cvd_failed_to_confirm_high_flag=failed_to_confirm_high,
    )


def _asof_candles(*, candles: Sequence[Candle1m], snapshot_time_ms: int) -> list[Candle1m]:
    rows = [candle for candle in candles if candle.available_time_ms <= snapshot_time_ms]
    rows.sort(key=lambda item: (item.available_time_ms, item.open_time_ms))
    return rows


def _current_candle(*, candles: Sequence[Candle1m], snapshot_time_ms: int) -> Candle1m | None:
    asof_rows = _asof_candles(candles=candles, snapshot_time_ms=snapshot_time_ms)
    if not asof_rows:
        return None
    current = asof_rows[-1]
    if current.available_time_ms != snapshot_time_ms:
        return None
    return current


def _event_start_time_ms(state: AnomalyState1mRow) -> int:
    return state.snapshot_time_ms - max(state.minutes_since_event_start, 0) * ONE_MINUTE_MS


def _zscore(value: float, history_values: Sequence[float]) -> float | None:
    if len(history_values) < 2:
        return None
    mean_value = statistics.fmean(history_values)
    std_value = statistics.stdev(history_values)
    if std_value <= 0 or not math.isfinite(std_value):
        return None
    return (value - mean_value) / std_value


def _last_oi_at_or_before(rows: Sequence[OpenInterest5m], timestamp_ms: int) -> OpenInterest5m | None:
    eligible = [item for item in rows if item.timestamp_ms <= timestamp_ms]
    if not eligible:
        return None
    eligible.sort(key=lambda item: (item.timestamp_ms, item.available_time_ms))
    return eligible[-1]


def _candle_delta_quote(candle: Candle1m) -> float:
    if candle.taker_buy_quote_volume is None:
        raise ValueError("taker_buy_quote_volume is required for CVD computation")
    return 2.0 * candle.taker_buy_quote_volume - candle.quote_volume


def _window_candles(*, candles: Sequence[Candle1m], snapshot_time_ms: int, window_minutes: int) -> list[Candle1m]:
    window_start_ms = snapshot_time_ms - window_minutes * ONE_MINUTE_MS
    rows = [candle for candle in candles if window_start_ms <= candle.open_time_ms and candle.available_time_ms <= snapshot_time_ms]
    rows.sort(key=lambda item: (item.available_time_ms, item.open_time_ms))
    if len(rows) < window_minutes:
        return []
    return rows[-window_minutes:]


def _window_cvd_ratio(window_candles: Sequence[Candle1m]) -> float | None:
    if not window_candles or any(candle.taker_buy_quote_volume is None for candle in window_candles):
        return None
    quote_volume = sum(candle.quote_volume for candle in window_candles)
    if quote_volume <= 0:
        return None
    return sum(_candle_delta_quote(candle) for candle in window_candles) / quote_volume


def _window_price_change_atr(*, window_candles: Sequence[Candle1m], atr_value: float | None) -> float | None:
    if not window_candles or atr_value is None or atr_value <= 0:
        return None
    return (window_candles[-1].close - window_candles[0].open) / atr_value


class _CrossSectionFeatures:
    def __init__(
        self,
        *,
        volume_market_percentile: float | None,
        quote_volume_market_percentile: float | None,
        return_1m_market_percentile: float | None,
        return_from_event_market_percentile: float | None,
        oi_growth_market_percentile: float | None,
        liq_intensity_market_percentile: float | None,
        range_expansion_market_percentile: float | None,
        cross_section_available: bool,
        cross_section_symbol_count: int,
    ) -> None:
        self.volume_market_percentile = volume_market_percentile
        self.quote_volume_market_percentile = quote_volume_market_percentile
        self.return_1m_market_percentile = return_1m_market_percentile
        self.return_from_event_market_percentile = return_from_event_market_percentile
        self.oi_growth_market_percentile = oi_growth_market_percentile
        self.liq_intensity_market_percentile = liq_intensity_market_percentile
        self.range_expansion_market_percentile = range_expansion_market_percentile
        self.cross_section_available = cross_section_available
        self.cross_section_symbol_count = cross_section_symbol_count


def _cross_section_features(
    *,
    state: AnomalyState1mRow,
    candles_by_symbol: Mapping[str, Sequence[Candle1m]],
    open_interest_by_symbol: Mapping[str, Sequence[OpenInterest5m]] | None,
    liquidations_by_symbol: Mapping[str, Sequence[LiquidationEvent]] | None,
    universe_by_day: Mapping[str, set[str]],
    states_at_snapshot: Sequence[AnomalyState1mRow],
    min_cross_section_symbols: int,
) -> _CrossSectionFeatures:
    trade_date = utc_ms_to_datetime(state.snapshot_time_ms).date().isoformat()
    universe_symbols = universe_by_day.get(trade_date, set())
    if not universe_symbols:
        return _missing_cross_section_features(symbol_count=0)

    metric_values: dict[str, dict[str, float]] = {
        "volume": {},
        "quote_volume": {},
        "return_1m": {},
        "oi_growth": {},
        "liq_intensity": {},
        "range_expansion": {},
    }
    symbols_with_current_candle = 0
    for symbol in sorted(universe_symbols):
        candles = candles_by_symbol.get(symbol, ())
        current = _current_candle(candles=candles, snapshot_time_ms=state.snapshot_time_ms)
        if current is None:
            continue
        symbols_with_current_candle += 1
        metric_values["volume"][symbol] = current.volume
        metric_values["quote_volume"][symbol] = current.quote_volume
        return_1m = _one_minute_return(candles=candles, snapshot_time_ms=state.snapshot_time_ms)
        if return_1m is not None:
            metric_values["return_1m"][symbol] = return_1m
        if current.close > 0:
            metric_values["range_expansion"][symbol] = (current.high - current.low) / current.close
        if open_interest_by_symbol is not None:
            oi = _oi_features(
                open_interest_rows=open_interest_by_symbol.get(symbol, ()),
                snapshot_time_ms=state.snapshot_time_ms,
            )
            if oi.oi_change_5m_pct_of_oi is not None:
                metric_values["oi_growth"][symbol] = oi.oi_change_5m_pct_of_oi
        if liquidations_by_symbol is not None:
            liq_intensity = _minute_liq_intensity(
                current=current,
                liquidation_rows=liquidations_by_symbol.get(symbol, ()),
                snapshot_time_ms=state.snapshot_time_ms,
            )
            if liq_intensity is not None:
                metric_values["liq_intensity"][symbol] = liq_intensity

    if symbols_with_current_candle < min_cross_section_symbols:
        return _missing_cross_section_features(symbol_count=symbols_with_current_candle)

    return _CrossSectionFeatures(
        volume_market_percentile=_rank_percentile(metric_values["volume"], state.symbol),
        quote_volume_market_percentile=_rank_percentile(metric_values["quote_volume"], state.symbol),
        return_1m_market_percentile=_rank_percentile(metric_values["return_1m"], state.symbol),
        return_from_event_market_percentile=_return_from_event_percentile(
            states_at_snapshot=states_at_snapshot,
            symbol=state.symbol,
            min_cross_section_symbols=min_cross_section_symbols,
        ),
        oi_growth_market_percentile=_rank_percentile(metric_values["oi_growth"], state.symbol),
        liq_intensity_market_percentile=_rank_percentile(metric_values["liq_intensity"], state.symbol),
        range_expansion_market_percentile=_rank_percentile(metric_values["range_expansion"], state.symbol),
        cross_section_available=True,
        cross_section_symbol_count=symbols_with_current_candle,
    )


def _missing_cross_section_features(*, symbol_count: int) -> _CrossSectionFeatures:
    return _CrossSectionFeatures(
        volume_market_percentile=None,
        quote_volume_market_percentile=None,
        return_1m_market_percentile=None,
        return_from_event_market_percentile=None,
        oi_growth_market_percentile=None,
        liq_intensity_market_percentile=None,
        range_expansion_market_percentile=None,
        cross_section_available=False,
        cross_section_symbol_count=symbol_count,
    )


def _universe_symbols_by_day(rows: Sequence[SymbolDayUniverseRow] | Iterable[SymbolDayUniverseRow] | None) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    if rows is None:
        return result
    for row in rows:
        if row.tradable_on_day and row.liquidity_eligible_on_day and row.has_1m_data:
            result.setdefault(row.trade_date, set()).add(row.symbol)
    return result


def _one_minute_return(*, candles: Sequence[Candle1m], snapshot_time_ms: int) -> float | None:
    asof_rows = _asof_candles(candles=candles, snapshot_time_ms=snapshot_time_ms)
    if len(asof_rows) < 2:
        return None
    current = asof_rows[-1]
    previous = asof_rows[-2]
    if current.available_time_ms != snapshot_time_ms or previous.close <= 0:
        return None
    return current.close / previous.close - 1.0


def _minute_liq_intensity(
    *,
    current: Candle1m,
    liquidation_rows: Sequence[LiquidationEvent],
    snapshot_time_ms: int,
) -> float | None:
    minute_start_ms = snapshot_time_ms - ONE_MINUTE_MS
    quote = sum(
        item.quote_quantity
        for item in liquidation_rows
        if item.available_time_ms <= snapshot_time_ms and minute_start_ms <= item.event_time_ms < snapshot_time_ms
    )
    if current.quote_volume <= 0:
        return None
    return quote / current.quote_volume


def _return_from_event_percentile(
    *,
    states_at_snapshot: Sequence[AnomalyState1mRow],
    symbol: str,
    min_cross_section_symbols: int,
) -> float | None:
    values = {
        f"{item.symbol}::{item.event_id}": item.current_return_from_start
        for item in states_at_snapshot
        if item.current_return_from_start is not None
    }
    current_keys = [key for key in values if key.startswith(f"{symbol}::")]
    if len(values) < min_cross_section_symbols or not current_keys:
        return None
    # If several events for the same symbol are active at the same snapshot,
    # use the strongest as-of return for this symbol instead of leaking any future outcome.
    symbol_value = max(values[key] for key in current_keys)
    symbol_values: dict[str, float] = {}
    for key, value in values.items():
        row_symbol = key.split("::", 1)[0]
        previous = symbol_values.get(row_symbol)
        if previous is None or value > previous:
            symbol_values[row_symbol] = value
    return _rank_percentile(symbol_values, symbol)


def _rank_percentile(values_by_symbol: Mapping[str, float], symbol: str) -> float | None:
    if symbol not in values_by_symbol:
        return None
    items = [(item_symbol, value) for item_symbol, value in values_by_symbol.items() if math.isfinite(value)]
    if not items or symbol not in {item_symbol for item_symbol, _ in items}:
        return None
    sorted_values = sorted(value for _, value in items)
    value = values_by_symbol[symbol]
    tied_positions = [index + 1 for index, item_value in enumerate(sorted_values) if item_value == value]
    if not tied_positions:
        return None
    average_rank = statistics.fmean(tied_positions)
    denominator = max(len(sorted_values) - 1, 1)
    percentile = (average_rank - 1.0) / denominator
    return min(max(percentile, 0.0), 1.0)



class _MarketContextFeatures:
    def __init__(
        self,
        *,
        corr_with_btc_by_window: dict[int, float | None],
        symbol_return_minus_btc_by_window: dict[int, float | None],
        idiosyncratic_momentum_score: float | None,
        simultaneous_anomalies_count_1m: int,
        simultaneous_anomalies_share_1m: float | None,
        systemic_cluster_regime: str,
        market_shock_id: str,
    ) -> None:
        self.corr_with_btc_by_window = corr_with_btc_by_window
        self.symbol_return_minus_btc_by_window = symbol_return_minus_btc_by_window
        self.idiosyncratic_momentum_score = idiosyncratic_momentum_score
        self.simultaneous_anomalies_count_1m = simultaneous_anomalies_count_1m
        self.simultaneous_anomalies_share_1m = simultaneous_anomalies_share_1m
        self.systemic_cluster_regime = systemic_cluster_regime
        self.market_shock_id = market_shock_id


def _market_context_features(
    *,
    state: AnomalyState1mRow,
    symbol_candles: Sequence[Candle1m],
    btc_candles: Sequence[Candle1m],
    states_at_snapshot: Sequence[AnomalyState1mRow],
    cross_section_symbol_count: int,
    volume_market_percentile: float | None,
    ATR_1d_pct_asof_t: float | None,
    corr_windows_minutes: tuple[int, ...],
    return_windows_minutes: tuple[int, ...],
    moderate_cluster_min_count: int,
    systemic_cluster_min_count: int,
) -> _MarketContextFeatures:
    corr_by_window = {
        window: _rolling_return_correlation_with_btc(
            symbol_candles=symbol_candles,
            btc_candles=btc_candles,
            snapshot_time_ms=state.snapshot_time_ms,
            window_minutes=window,
        )
        for window in corr_windows_minutes
    }
    return_minus_btc_by_window = {
        window: _symbol_return_minus_btc_return(
            symbol_candles=symbol_candles,
            btc_candles=btc_candles,
            snapshot_time_ms=state.snapshot_time_ms,
            window_minutes=window,
        )
        for window in return_windows_minutes
    }
    simultaneous_count = len({row.symbol for row in states_at_snapshot if row.event_alive})
    simultaneous_share = None
    if cross_section_symbol_count > 0:
        simultaneous_share = min(simultaneous_count / cross_section_symbol_count, 1.0)

    regime = _systemic_cluster_regime(
        simultaneous_count=simultaneous_count,
        cross_section_symbol_count=cross_section_symbol_count,
        moderate_cluster_min_count=moderate_cluster_min_count,
        systemic_cluster_min_count=systemic_cluster_min_count,
    )
    market_shock_id = _market_shock_id(
        snapshot_time_ms=state.snapshot_time_ms,
        regime=regime,
        symbol=state.symbol,
    )

    return_15m = return_minus_btc_by_window.get(15)
    corr_30m = corr_by_window.get(30)
    idiosyncratic_score = _idiosyncratic_momentum_score(
        volume_market_percentile=volume_market_percentile,
        symbol_return_minus_btc_return_15m=return_15m,
        corr_with_btc_30m=corr_30m,
        ATR_1d_pct_asof_t=ATR_1d_pct_asof_t,
    )

    return _MarketContextFeatures(
        corr_with_btc_by_window=corr_by_window,
        symbol_return_minus_btc_by_window=return_minus_btc_by_window,
        idiosyncratic_momentum_score=idiosyncratic_score,
        simultaneous_anomalies_count_1m=simultaneous_count,
        simultaneous_anomalies_share_1m=simultaneous_share,
        systemic_cluster_regime=regime,
        market_shock_id=market_shock_id,
    )


def _systemic_cluster_regime(
    *,
    simultaneous_count: int,
    cross_section_symbol_count: int,
    moderate_cluster_min_count: int,
    systemic_cluster_min_count: int,
) -> str:
    if cross_section_symbol_count <= 0:
        return "unknown"
    if simultaneous_count >= systemic_cluster_min_count:
        return "systemic_beta_shock"
    if simultaneous_count >= moderate_cluster_min_count:
        return "moderate_cluster"
    return "idiosyncratic"


def _market_shock_id(*, snapshot_time_ms: int, regime: str, symbol: str) -> str:
    if regime in {"moderate_cluster", "systemic_beta_shock"}:
        return f"market_shock:{snapshot_time_ms}"
    if regime == "idiosyncratic":
        return f"idiosyncratic:{symbol}:{snapshot_time_ms}"
    return f"unknown:{snapshot_time_ms}"


def _idiosyncratic_momentum_score(
    *,
    volume_market_percentile: float | None,
    symbol_return_minus_btc_return_15m: float | None,
    corr_with_btc_30m: float | None,
    ATR_1d_pct_asof_t: float | None,
) -> float | None:
    if volume_market_percentile is None:
        return None
    if symbol_return_minus_btc_return_15m is None:
        return None
    if corr_with_btc_30m is None:
        return None
    if ATR_1d_pct_asof_t is None or ATR_1d_pct_asof_t <= 0:
        return None
    btc_relative_return_atr = symbol_return_minus_btc_return_15m / max(ATR_1d_pct_asof_t, EPS)
    return volume_market_percentile * max(btc_relative_return_atr, 0.0) * max(1.0 - corr_with_btc_30m, 0.0)


def _symbol_return_minus_btc_return(
    *,
    symbol_candles: Sequence[Candle1m],
    btc_candles: Sequence[Candle1m],
    snapshot_time_ms: int,
    window_minutes: int,
) -> float | None:
    symbol_return = _window_return(candles=symbol_candles, snapshot_time_ms=snapshot_time_ms, window_minutes=window_minutes)
    btc_return = _window_return(candles=btc_candles, snapshot_time_ms=snapshot_time_ms, window_minutes=window_minutes)
    if symbol_return is None or btc_return is None:
        return None
    return symbol_return - btc_return


def _window_return(*, candles: Sequence[Candle1m], snapshot_time_ms: int, window_minutes: int) -> float | None:
    window_candles = _window_candles(candles=candles, snapshot_time_ms=snapshot_time_ms, window_minutes=window_minutes)
    if len(window_candles) < window_minutes:
        return None
    first = window_candles[0]
    last = window_candles[-1]
    if last.available_time_ms != snapshot_time_ms or first.open <= 0:
        return None
    return last.close / first.open - 1.0


def _rolling_return_correlation_with_btc(
    *,
    symbol_candles: Sequence[Candle1m],
    btc_candles: Sequence[Candle1m],
    snapshot_time_ms: int,
    window_minutes: int,
) -> float | None:
    symbol_returns = _one_minute_returns_by_available_time(
        candles=symbol_candles,
        snapshot_time_ms=snapshot_time_ms,
        window_minutes=window_minutes,
    )
    btc_returns = _one_minute_returns_by_available_time(
        candles=btc_candles,
        snapshot_time_ms=snapshot_time_ms,
        window_minutes=window_minutes,
    )
    common_times = sorted(set(symbol_returns) & set(btc_returns))
    if len(common_times) < window_minutes:
        return None
    symbol_values = [symbol_returns[item] for item in common_times[-window_minutes:]]
    btc_values = [btc_returns[item] for item in common_times[-window_minutes:]]
    return _correlation(symbol_values, btc_values)


def _one_minute_returns_by_available_time(
    *,
    candles: Sequence[Candle1m],
    snapshot_time_ms: int,
    window_minutes: int,
) -> dict[int, float]:
    asof_rows = _asof_candles(candles=candles, snapshot_time_ms=snapshot_time_ms)
    if len(asof_rows) < window_minutes + 1:
        return {}
    rows = asof_rows[-(window_minutes + 1) :]
    if rows[-1].available_time_ms != snapshot_time_ms:
        return {}
    result: dict[int, float] = {}
    for previous, current in zip(rows, rows[1:]):
        if previous.close <= 0:
            continue
        result[current.available_time_ms] = current.close / previous.close - 1.0
    return result


def _correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    left_centered = [value - left_mean for value in left]
    right_centered = [value - right_mean for value in right]
    left_var = sum(value * value for value in left_centered)
    right_var = sum(value * value for value in right_centered)
    if left_var <= 0 or right_var <= 0:
        return None
    corr = sum(lval * rval for lval, rval in zip(left_centered, right_centered)) / math.sqrt(left_var * right_var)
    return min(max(corr, -1.0), 1.0)

def _protocol_rows(*, state_row_count: int, feature_row_count: int) -> list[ProtocolAuditRow]:
    base_rows = [
        ProtocolAuditRow(
            check_name="mvp1_feature_matrix_scope",
            status=AuditStatus.PASS,
            message="price/time/alpha-decay plus volume/OI/liquidation/CVD, point-in-time cross-sectional, BTC-relative, and systemic cluster feature matrix only; no ML/decision/trade simulation",
            artifact="anomaly_feature_matrix.csv",
        ),
        ProtocolAuditRow(
            check_name="state_artifact_schema_boundary",
            status=AuditStatus.PASS,
            message=f"anomaly_state_1m.csv accepted through strict schema boundary with {state_row_count} state rows",
            artifact="anomaly_state_1m.csv",
        ),
        ProtocolAuditRow(
            check_name="feature_matrix_rows_written",
            status=AuditStatus.PASS if feature_row_count == state_row_count else AuditStatus.FAIL,
            message=f"anomaly_feature_matrix.csv written with {feature_row_count} rows for {state_row_count} state rows",
            artifact="anomaly_feature_matrix.csv",
        ),
        ProtocolAuditRow(
            check_name="feature_matrix_no_future_artifact_dependency",
            status=AuditStatus.PASS,
            message="feature matrix recomputes ATR and all rolling features from normalized as-of market data and does not read anomaly_future_paths.csv",
            artifact="anomaly_feature_matrix.csv",
        ),
        ProtocolAuditRow(
            check_name="feature_matrix_flow_oi_liquidation_cvd_asof",
            status=AuditStatus.PASS,
            message="OI uses closed 5m rows available <= snapshot; liquidation and CVD use events/candles available <= snapshot only",
            artifact="anomaly_feature_matrix.csv",
        ),
        ProtocolAuditRow(
            check_name="feature_matrix_cross_section_point_in_time",
            status=AuditStatus.PASS,
            message="cross-sectional percentiles use only point-in-time universe symbols with current candles available <= snapshot_time_ms",
            artifact="anomaly_feature_matrix.csv",
        ),
        ProtocolAuditRow(
            check_name="legacy_import_boundary",
            status=AuditStatus.PASS,
            message="mvp1 feature matrix uses anomaly_science modules only; legacy_quarantine is reference-only",
        ),
    ]
    implemented_methodology_rows = [
        ProtocolAuditRow(
            check_name="relative_over_absolute_feature_contract_enforced",
            status=AuditStatus.PASS,
            message="materialized feature matrix contains ATR-normalized, self-history-relative, dimensionless, categorical, boolean, or audit-only fields; no raw absolute model features",
            artifact="anomaly_feature_matrix.csv",
        ),
        ProtocolAuditRow(
            check_name="ATR_1d_asof_t_computed_from_closed_past_candles",
            status=AuditStatus.PASS,
            message="feature matrix computes ATR_1d_asof_t from closed 1m candles available <= snapshot_time_ms and leaves ATR features null when history is insufficient",
            artifact="anomaly_feature_matrix.csv",
        ),
        ProtocolAuditRow(
            check_name="market_shock_id_assigned",
            status=AuditStatus.PASS,
            message="feature matrix assigns a point-in-time market_shock_id for every row; clustered rows share the snapshot-level market shock id",
            artifact="anomaly_feature_matrix.csv",
        ),
        ProtocolAuditRow(
            check_name="simultaneous_anomalies_count_1m_point_in_time",
            status=AuditStatus.PASS,
            message="simultaneous anomaly counts are computed from anomaly_state_1m rows at the same snapshot_time_ms and normalized by the point-in-time cross-section when available",
            artifact="anomaly_feature_matrix.csv",
        ),
    ]
    return base_rows + build_methodology_v2_audit_rows(
        stage="mvp1_feature_matrix",
        implemented=implemented_methodology_rows,
    )


def _protocol_rows_to_artifact(rows: list[ProtocolAuditRow]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        payload = asdict(row)
        payload["status"] = row.status.value
        result.append(payload)
    return result


def _run_config_rows(
    *,
    input_path: Path,
    state_path: Path,
    output_path: Path,
    config: FeatureMatrixConfig,
) -> list[RunConfigRow]:
    return [
        RunConfigRow(key="command", value="run-mvp1-feature-matrix", source="cli"),
        RunConfigRow(key="input_dir", value=str(input_path), source="cli"),
        RunConfigRow(key="state_path", value=str(state_path), source="cli"),
        RunConfigRow(key="output_dir", value=str(output_path), source="cli"),
        *runtime_reproducibility_rows(),
        RunConfigRow(key="stage", value="mvp1_feature_matrix", source="runtime"),
        RunConfigRow(key="feature_schema_version", value=FEATURE_SCHEMA_VERSION, source="runtime"),
        RunConfigRow(key="feature_matrix_version", value=config.feature_matrix_version, source="runtime"),
        RunConfigRow(key="atr_window_minutes", value=str(config.atr_window_minutes), source="runtime"),
        RunConfigRow(
            key="expected_event_lifetime_minutes",
            value=str(config.expected_event_lifetime_minutes),
            source="runtime",
        ),
        RunConfigRow(
            key="volume_baseline_window_minutes",
            value=str(config.volume_baseline_window_minutes),
            source="runtime",
        ),
        RunConfigRow(key="cvd_windows_minutes", value=",".join(str(item) for item in config.cvd_windows_minutes), source="runtime"),
        RunConfigRow(key="min_cross_section_symbols", value=str(config.min_cross_section_symbols), source="runtime"),
        RunConfigRow(key="btc_symbol", value=config.btc_symbol, source="runtime"),
        RunConfigRow(
            key="btc_corr_window_minutes",
            value=",".join(str(item) for item in config.btc_corr_window_minutes),
            source="runtime",
        ),
        RunConfigRow(
            key="btc_relative_return_windows_minutes",
            value=",".join(str(item) for item in config.btc_relative_return_windows_minutes),
            source="runtime",
        ),
        RunConfigRow(key="moderate_cluster_min_count", value=str(config.moderate_cluster_min_count), source="runtime"),
        RunConfigRow(key="systemic_cluster_min_count", value=str(config.systemic_cluster_min_count), source="runtime"),
    ]


def _required_str(row: Mapping[str, object], name: str) -> str:
    value = row[name]
    if _is_missing(value):
        raise ValueError(f"{name} is required")
    result = str(value)
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _required_int(row: Mapping[str, object], name: str) -> int:
    value = row[name]
    if _is_missing(value):
        raise ValueError(f"{name} is required")
    return int(value)


def _required_float(row: Mapping[str, object], name: str) -> float:
    value = row[name]
    if _is_missing(value):
        raise ValueError(f"{name} is required")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _optional_float(row: Mapping[str, object], name: str) -> float | None:
    value = row[name]
    if _is_missing(value):
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite when provided")
    return result


def _required_bool(row: Mapping[str, object], name: str) -> bool:
    value = row[name]
    if isinstance(value, bool):
        return value
    if _is_missing(value):
        raise ValueError(f"{name} is required")
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1"}:
        return True
    if text in {"false", "0"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _is_missing(value: object) -> bool:
    return value is None or value == ""


def _csv_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return value


def _run_id() -> str:
    return "mvp1-feature-matrix-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
