"""Causal prior non-drawdown controls for filled drawdown-ladder states.

Every control is earlier than its signal by at least the complete response
horizon. Matching uses only values available at each respective snapshot. No
future outcome participates in candidate membership, scoring, or selection.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Callable, Iterator

import numpy as np
import pandas as pd
import polars as pl

from anomaly_science.binance_vision_cache import is_delivery_contract_symbol
from anomaly_science.future.recovery import RecoveryPathIndex, RecoveryPathMeasurement
from anomaly_science.market_context.sessions import (
    MS_PER_MINUTE,
    UTC_SESSION_BLOCKS,
)
from anomaly_science.strategy.drawdown_ladder.data import (
    last_open_time_ms_exclusive,
    read_is_symbol_minutes,
)
from anomaly_science.strategy.drawdown_ladder.spec import DrawdownLadderStage0Spec


class MatchedControlError(ValueError):
    """Raised when a matched-control row violates the frozen causal contract."""


@dataclass(frozen=True, slots=True)
class MatchedNonDrawdownSpec:
    protocol_version: str = "prior_non_drawdown_control_v1"
    protocol_freeze_id: str = "prior_non_drawdown_control_20260802_v1"
    candidate_schema_version: str = "prior_non_drawdown_candidate_v1"
    outcome_schema_version: str = "prior_non_drawdown_outcome_v1"
    lookback_days: int = 30
    minimum_separation_minutes: int = 48 * 60
    trailing_feature_minutes: int = 60
    excluded_drawdown_depth_pct: int = 3
    trade_through_bps: int = 5
    maximum_realized_vol_ratio: float = 4.0
    maximum_quote_volume_ratio: float = 10.0
    maximum_abs_return_difference: float = 0.03
    realized_vol_score_weight: float = 1.0
    quote_volume_score_weight: float = 0.5
    abs_return_score_weight: float = 0.5

    def __post_init__(self) -> None:
        if self.lookback_days <= 0:
            raise ValueError("matched-control lookback must be positive")
        if self.minimum_separation_minutes < 48 * 60:
            raise ValueError("control outcome must resolve before the signal")
        if self.trailing_feature_minutes < 2:
            raise ValueError("matched-control feature window is too short")
        if self.excluded_drawdown_depth_pct <= 0:
            raise ValueError("non-drawdown exclusion depth must be positive")
        if self.trade_through_bps <= 0:
            raise ValueError("matched-control trade-through must be positive")
        if self.maximum_realized_vol_ratio <= 1.0:
            raise ValueError("realized-volatility caliper ratio must exceed one")
        if self.maximum_quote_volume_ratio <= 1.0:
            raise ValueError("quote-volume caliper ratio must exceed one")
        if self.maximum_abs_return_difference <= 0.0:
            raise ValueError("absolute-return caliper must be positive")


MATCHED_CANDIDATE_COLUMNS: tuple[str, ...] = (
    "candidate_schema_version",
    "protocol_version",
    "protocol_freeze_id",
    "pair_id",
    "signal_candidate_id",
    "signal_parent_event_id",
    "symbol",
    "grid_step_pct",
    "deepest_filled_level_pct",
    "signal_snapshot_time_ms",
    "signal_session_start_time_ms",
    "signal_minutes_from_order_activation",
    "control_session_start_time_ms",
    "control_bar_open_time_ms",
    "control_snapshot_time_ms",
    "control_feature_cutoff_time_ms",
    "control_entry_price",
    "control_is_strictly_prior",
    "control_outcome_resolved_before_signal",
    "control_has_no_3pct_drawdown",
    "signal_realized_vol_60m",
    "control_realized_vol_60m",
    "realized_vol_log_distance",
    "signal_quote_volume_60m",
    "control_quote_volume_60m",
    "quote_volume_log_distance",
    "signal_abs_return_60m",
    "control_abs_return_60m",
    "abs_return_distance",
    "match_score",
    "eligible_pool_size_before_calipers",
    "eligible_pool_size_after_calipers",
    "untouched_2026_row_used",
)


def matched_outcome_columns(
    stage0_spec: DrawdownLadderStage0Spec = DrawdownLadderStage0Spec(),
) -> tuple[str, ...]:
    cost = tuple(
        column
        for bps in stage0_spec.round_trip_cost_bps
        for column in (
            f"break_even_{bps}bps_reached",
            f"time_to_break_even_{bps}bps_minutes",
        )
    )
    horizons = tuple(
        column
        for minutes in stage0_spec.response_horizons_minutes
        for column in (
            f"label_available_{minutes}m",
            f"future_return_{minutes}m",
            f"future_max_return_{minutes}m",
            f"future_min_return_{minutes}m",
        )
    )
    return (
        "outcome_schema_version",
        "protocol_version",
        "protocol_freeze_id",
        "pair_id",
        "signal_candidate_id",
        "symbol",
        "control_snapshot_time_ms",
        "future_start_time_ms",
        "available_future_minutes",
        "horizon_complete",
        "censor_reason",
        "gross_break_even_reached",
        "time_to_gross_break_even_minutes",
        *cost,
        *horizons,
        "untouched_2026_row_used",
    )


@dataclass(frozen=True, slots=True)
class SymbolMatchedControlStats:
    symbol: str
    signal_rows: int
    matched_rows: int
    no_prior_pool_rows: int
    failed_caliper_rows: int
    missing_quote_volume_rows: int


@dataclass(frozen=True, slots=True)
class MatchedControlBuildConfig:
    source_dir: Path = Path(".output/market/binance_vision/um_futures/enriched_1m")
    long_stage0_dir: Path = Path(".output/research/drawdown_ladder/stage0_is")
    output_dir: Path = Path(".output/research/drawdown_ladder/prior_non_drawdown_control_is")
    workers: int = 4
    max_inflight_symbols: int | None = None
    max_symbols: int | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.workers <= 8:
            raise ValueError("matched-control workers must be between 1 and 8")
        if self.max_inflight_symbols is not None and self.max_inflight_symbols < self.workers:
            raise ValueError("max inflight symbols cannot be lower than workers")
        if self.max_symbols is not None and self.max_symbols <= 0:
            raise ValueError("max symbols must be positive")


@dataclass(frozen=True, slots=True)
class MatchedControlBuildResult:
    output_dir: Path
    candidates_path: Path
    outcomes_path: Path
    coverage_path: Path
    audit_path: Path
    manifest_path: Path


def _stable_id(prefix: str, *parts: object) -> str:
    payload = "|".join(str(part) for part in parts)
    digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=12).hexdigest()
    return f"{prefix}_{digest}"


def _atomic_write(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False, compression="zstd")
    temporary.replace(path)


def _typed_frame(rows: list[dict[str, object]], columns: tuple[str, ...]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=columns)
    boolean_columns = {
        "control_is_strictly_prior",
        "control_outcome_resolved_before_signal",
        "control_has_no_3pct_drawdown",
        "untouched_2026_row_used",
        "horizon_complete",
        "gross_break_even_reached",
    }
    integer_columns = {
        "grid_step_pct",
        "deepest_filled_level_pct",
        "signal_minutes_from_order_activation",
        "eligible_pool_size_before_calipers",
        "eligible_pool_size_after_calipers",
        "available_future_minutes",
    }
    for column in columns:
        if (
            column in boolean_columns
            or column.startswith("label_available_")
            or column.startswith("break_even_") and column.endswith("_reached")
        ):
            frame[column] = frame[column].astype("bool")
        elif (
            column in integer_columns
            or column.endswith("_time_ms")
            or column.endswith("_minutes")
        ):
            frame[column] = pd.array(frame[column], dtype="Int64")
        elif (
            column.endswith("_price")
            or column.endswith("_60m")
            or column.endswith("_distance")
            or column == "match_score"
            or "return_" in column
        ):
            frame[column] = pd.array(frame[column], dtype="Float64")
        else:
            frame[column] = frame[column].astype("string")
    return frame


def _causal_features(
    close: np.ndarray,
    quote_volume: np.ndarray,
    *,
    window: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if bool((~np.isfinite(close)).any()) or bool((close <= 0.0).any()):
        raise MatchedControlError("matched-control close must be finite and positive")
    finite_quote = np.isfinite(quote_volume)
    if bool((finite_quote & (quote_volume < 0.0)).any()):
        raise MatchedControlError("matched-control quote volume cannot be negative")
    log_return = np.empty(len(close), dtype=float)
    log_return[0] = np.nan
    log_return[1:] = np.diff(np.log(close))
    squared = pd.Series(log_return * log_return).rolling(window, min_periods=window).sum()
    realized_vol = np.sqrt(squared.to_numpy(float))
    quote_sum = (
        pd.Series(quote_volume).rolling(window, min_periods=window).sum().to_numpy(float)
    )
    abs_return = np.full(len(close), np.nan, dtype=float)
    if len(close) > window:
        abs_return[window:] = np.abs(close[window:] / close[:-window] - 1.0)
    return realized_vol, quote_sum, abs_return


def _non_drawdown_threshold(depth_pct: int, trade_through_bps: int) -> float:
    return (1.0 - depth_pct / 100.0) * (1.0 - trade_through_bps / 10_000.0)


def _measurement_row(
    *,
    pair_id: str,
    signal_candidate_id: str,
    symbol: str,
    measurement: RecoveryPathMeasurement,
    censor_reason: str,
    control_spec: MatchedNonDrawdownSpec,
    stage0_spec: DrawdownLadderStage0Spec,
) -> dict[str, object]:
    row: dict[str, object] = {
        "outcome_schema_version": control_spec.outcome_schema_version,
        "protocol_version": control_spec.protocol_version,
        "protocol_freeze_id": control_spec.protocol_freeze_id,
        "pair_id": pair_id,
        "signal_candidate_id": signal_candidate_id,
        "symbol": symbol,
        "control_snapshot_time_ms": measurement.snapshot_time_ms,
        "future_start_time_ms": measurement.future_start_time_ms,
        "available_future_minutes": measurement.available_future_minutes,
        "horizon_complete": measurement.horizon_complete,
        "censor_reason": censor_reason,
        "gross_break_even_reached": measurement.gross_break_even_reached,
        "time_to_gross_break_even_minutes": measurement.time_to_gross_break_even_minutes,
    }
    for bps, minutes in zip(
        stage0_spec.round_trip_cost_bps,
        measurement.cost_break_even_times_minutes,
        strict=True,
    ):
        row[f"break_even_{bps}bps_reached"] = minutes is not None
        row[f"time_to_break_even_{bps}bps_minutes"] = minutes
    for horizon in measurement.horizon_metrics:
        suffix = f"{horizon.horizon_minutes}m"
        row[f"label_available_{suffix}"] = horizon.label_available
        row[f"future_return_{suffix}"] = horizon.close_return
        row[f"future_max_return_{suffix}"] = horizon.maximum_return
        row[f"future_min_return_{suffix}"] = horizon.minimum_return
    row["untouched_2026_row_used"] = False
    return row


def build_symbol_prior_non_drawdown_controls(
    *,
    source_path: str | Path,
    signal_candidates_path: str | Path,
    output_candidates_path: str | Path,
    output_outcomes_path: str | Path,
    control_spec: MatchedNonDrawdownSpec = MatchedNonDrawdownSpec(),
    stage0_spec: DrawdownLadderStage0Spec = DrawdownLadderStage0Spec(),
) -> SymbolMatchedControlStats:
    source = Path(source_path)
    symbol = source.stem.upper()
    signal = pd.read_parquet(signal_candidates_path)
    if signal.empty:
        _atomic_write(_typed_frame([], MATCHED_CANDIDATE_COLUMNS), Path(output_candidates_path))
        _atomic_write(
            _typed_frame([], matched_outcome_columns(stage0_spec)),
            Path(output_outcomes_path),
        )
        return SymbolMatchedControlStats(symbol, 0, 0, 0, 0, 0)
    if set(signal["symbol"].astype(str).str.upper()) != {symbol}:
        raise MatchedControlError("signal shard symbol does not match source symbol")
    minute = read_is_symbol_minutes(
        source,
        columns=("high", "low", "close", "quote_volume"),
    )
    timestamps = minute["timestamp"].to_numpy(dtype=np.int64)
    high = minute["high"].to_numpy(float)
    low = minute["low"].to_numpy(float)
    close = minute["close"].to_numpy(float)
    quote_volume = minute["quote_volume"].to_numpy(float)
    recovery = RecoveryPathIndex(
        timestamps_ms=timestamps,
        high=high,
        low=low,
        close=close,
    )
    realized_vol, quote_sum, abs_return = _causal_features(
        close,
        quote_volume,
        window=control_spec.trailing_feature_minutes,
    )
    minute_of_day = (timestamps % (24 * 60 * MS_PER_MINUTE)) // MS_PER_MINUTE
    starts_by_seq: dict[int, np.ndarray] = {}
    for block in UTC_SESSION_BLOCKS:
        starts_by_seq[block.seq] = np.flatnonzero(
            minute_of_day == block.start_hour * 60
        ).astype(np.int64)
    timestamp_to_index = {int(value): index for index, value in enumerate(timestamps)}
    threshold_ratio = _non_drawdown_threshold(
        control_spec.excluded_drawdown_depth_pct,
        control_spec.trade_through_bps,
    )
    candidate_rows: list[dict[str, object]] = []
    outcome_rows: list[dict[str, object]] = []
    no_pool = 0
    failed_caliper = 0
    for row in signal.sort_values(["snapshot_time_ms", "candidate_id"]).itertuples(index=False):
        signal_bar_index = timestamp_to_index.get(int(row.fill_bar_open_time_ms))
        if signal_bar_index is None:
            raise MatchedControlError("signal fill bar is missing from the IS minute path")
        signal_rv = float(realized_vol[signal_bar_index])
        signal_quote = float(quote_sum[signal_bar_index])
        signal_abs = float(abs_return[signal_bar_index])
        if not all(math.isfinite(value) for value in (signal_rv, signal_quote, signal_abs)):
            no_pool += 1
            continue
        signal_snapshot = int(row.snapshot_time_ms)
        earliest_snapshot = signal_snapshot - control_spec.lookback_days * 24 * 60 * MS_PER_MINUTE
        latest_snapshot = (
            signal_snapshot - control_spec.minimum_separation_minutes * MS_PER_MINUTE
        )
        age = int(row.minutes_from_order_activation)
        pool: list[tuple[float, int, int, float, float, float, float, float, float]] = []
        before_calipers = 0
        for start_index_raw in starts_by_seq.get(int(row.session_seq), np.asarray([], dtype=int)):
            start_index = int(start_index_raw)
            control_bar_index = start_index + age
            if start_index == 0 or control_bar_index + 1 >= len(timestamps):
                continue
            if timestamps[start_index - 1] != timestamps[start_index] - MS_PER_MINUTE:
                continue
            if timestamps[control_bar_index] != timestamps[start_index] + age * MS_PER_MINUTE:
                continue
            control_snapshot = int(timestamps[control_bar_index] + MS_PER_MINUTE)
            if timestamps[control_bar_index + 1] != control_snapshot:
                continue
            if not earliest_snapshot <= control_snapshot <= latest_snapshot:
                continue
            active_index = start_index + stage0_spec.order_activation_delay_minutes
            if active_index > control_bar_index:
                continue
            anchor = float(close[start_index - 1])
            if float(np.min(low[active_index : control_bar_index + 1])) <= anchor * threshold_ratio:
                continue
            control_rv = float(realized_vol[control_bar_index])
            control_quote = float(quote_sum[control_bar_index])
            control_abs = float(abs_return[control_bar_index])
            if not all(
                math.isfinite(value)
                for value in (control_rv, control_quote, control_abs)
            ):
                continue
            if control_rv <= 0.0 or control_quote <= 0.0 or signal_rv <= 0.0 or signal_quote <= 0.0:
                continue
            before_calipers += 1
            rv_distance = abs(math.log(control_rv / signal_rv))
            quote_distance = abs(math.log(control_quote / signal_quote))
            return_distance = abs(control_abs - signal_abs)
            if rv_distance > math.log(control_spec.maximum_realized_vol_ratio):
                continue
            if quote_distance > math.log(control_spec.maximum_quote_volume_ratio):
                continue
            if return_distance > control_spec.maximum_abs_return_difference:
                continue
            score = (
                control_spec.realized_vol_score_weight * rv_distance
                + control_spec.quote_volume_score_weight * quote_distance
                + control_spec.abs_return_score_weight
                * return_distance
                / control_spec.maximum_abs_return_difference
            )
            pool.append(
                (
                    score,
                    control_snapshot,
                    control_bar_index,
                    control_rv,
                    control_quote,
                    control_abs,
                    rv_distance,
                    quote_distance,
                    return_distance,
                )
            )
        if before_calipers == 0:
            no_pool += 1
            continue
        if not pool:
            failed_caliper += 1
            continue
        selected = min(pool, key=lambda value: (value[0], value[1]))
        (
            score,
            control_snapshot,
            control_bar_index,
            control_rv,
            control_quote,
            control_abs,
            rv_distance,
            quote_distance,
            return_distance,
        ) = selected
        control_session_start = int(timestamps[control_bar_index] - age * MS_PER_MINUTE)
        pair_id = _stable_id(
            "pndpair",
            row.candidate_id,
            control_snapshot,
            control_spec.protocol_freeze_id,
        )
        entry_price = float(close[control_bar_index])
        measurement = recovery.measure(
            entry_price=entry_price,
            snapshot_time_ms=control_snapshot,
            first_future_bar_index=control_bar_index + 1,
            maximum_horizon_minutes=stage0_spec.maximum_horizon_minutes,
            horizons_minutes=stage0_spec.response_horizons_minutes,
            round_trip_cost_bps=stage0_spec.round_trip_cost_bps,
            direction="long",
        )
        if measurement.horizon_complete:
            censor_reason = "none"
        elif measurement.ended_at_data_gap:
            censor_reason = "path_gap"
        elif timestamps[-1] >= last_open_time_ms_exclusive() - MS_PER_MINUTE:
            censor_reason = "is_boundary"
        else:
            censor_reason = "symbol_history_ended"
        candidate_rows.append(
            {
                "candidate_schema_version": control_spec.candidate_schema_version,
                "protocol_version": control_spec.protocol_version,
                "protocol_freeze_id": control_spec.protocol_freeze_id,
                "pair_id": pair_id,
                "signal_candidate_id": str(row.candidate_id),
                "signal_parent_event_id": str(row.parent_event_id),
                "symbol": symbol,
                "grid_step_pct": int(row.grid_step_pct),
                "deepest_filled_level_pct": int(row.deepest_filled_level_pct),
                "signal_snapshot_time_ms": signal_snapshot,
                "signal_session_start_time_ms": int(row.anchor_snapshot_time_ms),
                "signal_minutes_from_order_activation": age,
                "control_session_start_time_ms": control_session_start,
                "control_bar_open_time_ms": int(timestamps[control_bar_index]),
                "control_snapshot_time_ms": control_snapshot,
                "control_feature_cutoff_time_ms": control_snapshot,
                "control_entry_price": entry_price,
                "control_is_strictly_prior": control_snapshot < signal_snapshot,
                "control_outcome_resolved_before_signal": (
                    control_snapshot
                    + stage0_spec.maximum_horizon_minutes * MS_PER_MINUTE
                    <= signal_snapshot
                ),
                "control_has_no_3pct_drawdown": True,
                "signal_realized_vol_60m": signal_rv,
                "control_realized_vol_60m": control_rv,
                "realized_vol_log_distance": rv_distance,
                "signal_quote_volume_60m": signal_quote,
                "control_quote_volume_60m": control_quote,
                "quote_volume_log_distance": quote_distance,
                "signal_abs_return_60m": signal_abs,
                "control_abs_return_60m": control_abs,
                "abs_return_distance": return_distance,
                "match_score": score,
                "eligible_pool_size_before_calipers": before_calipers,
                "eligible_pool_size_after_calipers": len(pool),
                "untouched_2026_row_used": False,
            }
        )
        outcome_rows.append(
            _measurement_row(
                pair_id=pair_id,
                signal_candidate_id=str(row.candidate_id),
                symbol=symbol,
                measurement=measurement,
                censor_reason=censor_reason,
                control_spec=control_spec,
                stage0_spec=stage0_spec,
            )
        )
    candidates = _typed_frame(candidate_rows, MATCHED_CANDIDATE_COLUMNS)
    outcomes = _typed_frame(outcome_rows, matched_outcome_columns(stage0_spec))
    _atomic_write(candidates, Path(output_candidates_path))
    _atomic_write(outcomes, Path(output_outcomes_path))
    return SymbolMatchedControlStats(
        symbol=symbol,
        signal_rows=len(signal),
        matched_rows=len(candidates),
        no_prior_pool_rows=no_pool,
        failed_caliper_rows=failed_caliper,
        missing_quote_volume_rows=int((~np.isfinite(quote_volume)).sum()),
    )


def _symbol_output_paths(output_dir: Path, symbol: str) -> tuple[Path, Path]:
    return (
        output_dir / "shards" / "candidates" / f"{symbol}.parquet",
        output_dir / "shards" / "outcomes" / f"{symbol}.parquet",
    )


def _build_symbol_task(
    source_path: Path,
    signal_path: Path,
    output_dir: Path,
    control_spec: MatchedNonDrawdownSpec,
    stage0_spec: DrawdownLadderStage0Spec,
) -> SymbolMatchedControlStats:
    candidates_path, outcomes_path = _symbol_output_paths(output_dir, source_path.stem.upper())
    return build_symbol_prior_non_drawdown_controls(
        source_path=source_path,
        signal_candidates_path=signal_path,
        output_candidates_path=candidates_path,
        output_outcomes_path=outcomes_path,
        control_spec=control_spec,
        stage0_spec=stage0_spec,
    )


def _iter_bounded_builds(
    tasks: list[tuple[Path, Path]],
    *,
    output_dir: Path,
    control_spec: MatchedNonDrawdownSpec,
    stage0_spec: DrawdownLadderStage0Spec,
    workers: int,
    max_inflight: int,
) -> Iterator[SymbolMatchedControlStats]:
    if workers == 1:
        for source_path, signal_path in tasks:
            yield _build_symbol_task(
                source_path,
                signal_path,
                output_dir,
                control_spec,
                stage0_spec,
            )
        return
    executor = ProcessPoolExecutor(max_workers=workers)
    pending: dict[Future[SymbolMatchedControlStats], int] = {}
    completed: dict[int, SymbolMatchedControlStats] = {}
    next_submit = 0
    next_emit = 0

    def submit_until_capacity() -> None:
        nonlocal next_submit
        while next_submit < len(tasks) and len(pending) + len(completed) < max_inflight:
            source_path, signal_path = tasks[next_submit]
            future = executor.submit(
                _build_symbol_task,
                source_path,
                signal_path,
                output_dir,
                control_spec,
                stage0_spec,
            )
            pending[future] = next_submit
            next_submit += 1

    try:
        submit_until_capacity()
        while next_emit < len(tasks):
            while next_emit not in completed:
                if not pending:
                    raise MatchedControlError("bounded matched-control worker pool stalled")
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    completed[pending.pop(future)] = future.result()
                submit_until_capacity()
            yield completed.pop(next_emit)
            next_emit += 1
    finally:
        executor.shutdown(wait=True, cancel_futures=True)


def _consolidate(shard_dir: Path, output_path: Path) -> None:
    paths = sorted(shard_dir.glob("*.parquet"))
    if not paths:
        raise MatchedControlError(f"no matched-control shards found: {shard_dir}")
    pl.scan_parquet([str(path) for path in paths]).sink_parquet(
        output_path,
        compression="zstd",
        engine="streaming",
    )


def _audit_matched_outputs(
    *,
    candidates_path: Path,
    outcomes_path: Path,
    control_spec: MatchedNonDrawdownSpec,
) -> dict[str, object]:
    candidate = pl.read_parquet(
        candidates_path,
        columns=[
            "pair_id",
            "signal_candidate_id",
            "signal_snapshot_time_ms",
            "control_snapshot_time_ms",
            "control_feature_cutoff_time_ms",
            "control_is_strictly_prior",
            "control_outcome_resolved_before_signal",
            "control_has_no_3pct_drawdown",
            "untouched_2026_row_used",
        ],
    )
    outcome = pl.read_parquet(
        outcomes_path,
        columns=[
            "pair_id",
            "control_snapshot_time_ms",
            "future_start_time_ms",
            "untouched_2026_row_used",
        ],
    )
    if candidate["pair_id"].n_unique() != len(candidate):
        raise MatchedControlError("matched pair_id is not unique")
    if candidate["signal_candidate_id"].n_unique() != len(candidate):
        raise MatchedControlError("a signal has more than one matched control")
    if outcome["pair_id"].n_unique() != len(outcome) or len(outcome) != len(candidate):
        raise MatchedControlError("matched candidate/outcome cardinality differs")
    if candidate["pair_id"].sort().to_list() != outcome["pair_id"].sort().to_list():
        raise MatchedControlError("matched candidate/outcome keys differ")
    boolean_checks = {
        "control_is_strictly_prior": candidate["control_is_strictly_prior"],
        "control_outcome_resolved_before_signal": candidate[
            "control_outcome_resolved_before_signal"
        ],
        "control_has_no_3pct_drawdown": candidate["control_has_no_3pct_drawdown"],
    }
    for name, values in boolean_checks.items():
        if len(values) and not bool(values.all()):
            raise MatchedControlError(f"matched audit failed: {name}")
    if len(candidate) and not bool(
        candidate["control_feature_cutoff_time_ms"]
        .le(candidate["control_snapshot_time_ms"])
        .all()
    ):
        raise MatchedControlError("control feature cutoff exceeds snapshot")
    if len(outcome) and not bool(
        outcome["future_start_time_ms"].gt(outcome["control_snapshot_time_ms"]).all()
    ):
        raise MatchedControlError("matched future starts at or before snapshot")
    if len(candidate) and int(candidate["control_snapshot_time_ms"].max()) >= last_open_time_ms_exclusive():
        raise MatchedControlError("matched control crossed the physical IS boundary")
    if bool(candidate["untouched_2026_row_used"].any()) or bool(
        outcome["untouched_2026_row_used"].any()
    ):
        raise MatchedControlError("matched control reports OOS access")
    return {
        "protocol_freeze_id": control_spec.protocol_freeze_id,
        "status": "PASS",
        "candidate_rows": len(candidate),
        "outcome_rows": len(outcome),
        "pair_ids_unique": True,
        "signal_ids_unique": True,
        "control_strictly_prior": True,
        "control_outcome_resolved_before_signal": True,
        "control_non_drawdown": True,
        "feature_cutoff_le_snapshot": True,
        "future_start_gt_snapshot": True,
        "untouched_2026_rows_used": 0,
    }


def build_prior_non_drawdown_controls(
    config: MatchedControlBuildConfig = MatchedControlBuildConfig(),
    *,
    control_spec: MatchedNonDrawdownSpec = MatchedNonDrawdownSpec(),
    stage0_spec: DrawdownLadderStage0Spec = DrawdownLadderStage0Spec(),
    progress: Callable[[str], None] | None = print,
) -> MatchedControlBuildResult:
    if not config.source_dir.is_dir():
        raise FileNotFoundError(config.source_dir)
    signal_shard_dir = config.long_stage0_dir / "shards" / "ladder_state_candidates"
    if not signal_shard_dir.is_dir():
        raise FileNotFoundError(signal_shard_dir)
    long_audit_path = config.long_stage0_dir / "temporal_audit.json"
    if not long_audit_path.is_file():
        raise MatchedControlError("long Stage-0 temporal audit is missing")
    long_audit = json.loads(long_audit_path.read_text(encoding="utf-8"))
    if long_audit.get("status") != "PASS" or long_audit.get("protocol_freeze_id") != stage0_spec.protocol_freeze_id:
        raise MatchedControlError("long Stage-0 audit/protocol is not the frozen PASS input")
    manifest_path = config.output_dir / "manifest.json"
    if manifest_path.exists():
        raise MatchedControlError(f"refusing to mix matched-control runs: {manifest_path}")
    source_paths = sorted(
        (
            path
            for path in config.source_dir.glob("*.parquet")
            if not is_delivery_contract_symbol(path.stem)
        ),
        key=lambda path: path.stem,
    )
    tasks: list[tuple[Path, Path]] = []
    for source_path in source_paths:
        signal_path = signal_shard_dir / f"{source_path.stem.upper()}.parquet"
        if not signal_path.is_file():
            raise MatchedControlError(f"missing long signal shard: {signal_path}")
        tasks.append((source_path, signal_path))
    if config.max_symbols is not None:
        tasks = tasks[: config.max_symbols]
    if not tasks:
        raise MatchedControlError("no matched-control symbol tasks")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    max_inflight = config.max_inflight_symbols or config.workers * 2
    stats: list[SymbolMatchedControlStats] = []
    for completed, result in enumerate(
        _iter_bounded_builds(
            tasks,
            output_dir=config.output_dir,
            control_spec=control_spec,
            stage0_spec=stage0_spec,
            workers=config.workers,
            max_inflight=max_inflight,
        ),
        start=1,
    ):
        stats.append(result)
        if progress and (completed % 25 == 0 or completed == len(tasks)):
            progress(
                f"prior non-drawdown control {completed}/{len(tasks)}; "
                f"matched={sum(item.matched_rows for item in stats):,}/"
                f"{sum(item.signal_rows for item in stats):,}"
            )
    candidates_path = config.output_dir / "matched_candidates.parquet"
    outcomes_path = config.output_dir / "matched_outcomes.parquet"
    _consolidate(config.output_dir / "shards" / "candidates", candidates_path)
    _consolidate(config.output_dir / "shards" / "outcomes", outcomes_path)
    audit = _audit_matched_outputs(
        candidates_path=candidates_path,
        outcomes_path=outcomes_path,
        control_spec=control_spec,
    )
    audit_path = config.output_dir / "temporal_audit.json"
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    coverage_rows = [asdict(item) for item in sorted(stats, key=lambda item: item.symbol)]
    coverage_path = config.output_dir / "symbol_coverage.parquet"
    pd.DataFrame(coverage_rows).to_parquet(coverage_path, index=False, compression="zstd")
    total_signals = sum(item.signal_rows for item in stats)
    total_matched = sum(item.matched_rows for item in stats)
    manifest = {
        "protocol": asdict(control_spec),
        "long_stage0_protocol_freeze_id": stage0_spec.protocol_freeze_id,
        "research_partition": "is",
        "oos_rows_read": 0,
        "source_dir": str(config.source_dir),
        "long_stage0_dir": str(config.long_stage0_dir),
        "output_dir": str(config.output_dir),
        "workers": config.workers,
        "max_inflight_symbols": max_inflight,
        "source_symbol_count": len(tasks),
        "signal_rows": total_signals,
        "matched_rows": total_matched,
        "matched_coverage": total_matched / total_signals if total_signals else None,
        "no_prior_pool_rows": sum(item.no_prior_pool_rows for item in stats),
        "failed_caliper_rows": sum(item.failed_caliper_rows for item in stats),
        "missing_quote_volume_rows": sum(
            item.missing_quote_volume_rows for item in stats
        ),
        "artifacts": {
            "candidates": str(candidates_path),
            "outcomes": str(outcomes_path),
            "coverage": str(coverage_path),
            "temporal_audit": str(audit_path),
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return MatchedControlBuildResult(
        output_dir=config.output_dir,
        candidates_path=candidates_path,
        outcomes_path=outcomes_path,
        coverage_path=coverage_path,
        audit_path=audit_path,
        manifest_path=manifest_path,
    )


__all__ = [
    "MATCHED_CANDIDATE_COLUMNS",
    "MatchedControlBuildConfig",
    "MatchedControlBuildResult",
    "MatchedControlError",
    "MatchedNonDrawdownSpec",
    "SymbolMatchedControlStats",
    "build_symbol_prior_non_drawdown_controls",
    "build_prior_non_drawdown_controls",
    "matched_outcome_columns",
]
