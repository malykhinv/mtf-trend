"""Модуль проекта."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import time
from collections import Counter
from dataclasses import asdict, dataclass, replace
from logging import Logger
from pathlib import Path
from typing import Callable, cast

import pandas as pd

from config import AppConfig
from constants import (
    DEFAULT_QUALITY_REPORT_OUTPUT_FILE,
    OI_STALE_MIN_OBSERVATIONS,
    OI_STALE_RATIO_THRESHOLD,
    QUALITY_OI_LEADING_GAPS_ISSUE,
    QUALITY_OI_MISSING_VALUES_ISSUE,
    QUALITY_OI_STALE_SERIES_ISSUE,
    QUALITY_OI_MISSING_COLUMN_ISSUE,
    QUALITY_SEVERITY_CRITICAL,
    QUALITY_SEVERITY_ERROR,
    QUALITY_SEVERITY_INFO,
    QUALITY_SEVERITY_WARNING,
)
from data.clients.noop_market_data_client import NoOpMarketDataClient
from data.exchanges.ccxt_futures_client import CcxtFuturesClient
from data.fetchers.market_data_fetcher import MarketDataFetcher
from data.fetchers.ohlcv_fetcher import OhlcvFetcher
from data.fetchers.oi_fetcher import OiFetcher
from data.liquidity.daily_volume_ranker import DailyVolumeRanker
from data.quality.data_validator import DataValidator
from data.quality.gap_detector import GapDetector
from data.storage.parquet_storage import ParquetStorage
from domain.enums.exchange import Exchange
from domain.enums.timeframe import Timeframe
from domain.models.reporting.quality_report import QualityReport
from domain.models.reporting.quality_summary import QualitySummary
from domain.models.reporting.quality_symbol_stats import QualitySymbolStats
from domain.models.reporting.symbol_fetch_result import SymbolFetchResult
from strategy.factory import build_strategy
from strategy.pno import PnoParams, PnoStrategy
from strategy.pno.config import (
    PNO_BACKTEST_TIMEFRAME_PAIRS,
    resolve_pno_default_timeframe_pair,
    validate_pno_timeframe_pair,
)
from strategy.pno.engine import PNO_STAGE_4_LEVEL, PNO_STAGE_5_POSITION, PNO_STAGE_SEQUENCE
from utils.logger import get_logger
from utils.symbols import normalize_symbol
from vectorbt_runner import BacktestRunner, DataPreparer, SymbolDataLoadResult, SymbolMtfFrames
from vectorbt_runner.backtest_runner import BacktestGenerationDiagnosticsCache
from cli.pno_diagnostics import (
    _export_pno_research_context,
    _export_pno_stage_reviews,
    _read_csv_with_status,
    _render_pno_position_charts_for_symbol,
    _safe_float,
    _safe_int,
    _select_stage_review_rejection_rows,
    _to_compact_json,
)

# region Приватные

_PROGRESS_LOG_EVERY = 50
_BACKTEST_RUNS_DIR_NAME = "backtest_runs"
_BACKTEST_RUN_CONTEXT_FILE_NAME = "run_context.json"
_BACKTEST_PLOT_REQUEST_FILE_NAME = "plot_request.json"
_BACKTEST_DATA_LOAD_STATUS_FILE_NAME = "data_load_status.csv"
_BACKTEST_DATA_LOAD_REJECTIONS_FILE_NAME = "data_load_rejections.csv"
_PNO_SECONDS_LOAD_STATUS_FILE_NAME = "seconds_load_status.csv"
_PNO_SPARSE_ENTRY_MATERIALIZATION_STATUS_FILE_NAME = "sparse_entry_materialization_status.csv"
_PNO_STAGE1_CACHE_STATUS_FILE_NAME = "stage1_cache_status.csv"
_PNO_STAGE5_REVIEW_SYNTHESIS_STATUS_FILE_NAME = "stage5_review_synthesis_status.csv"
_PNO_DIAGNOSTICS_COVERAGE_COLUMNS: tuple[str, ...] = (
    "symbol",
    "diagnostics_json_written",
    "skip_reason",
    "positions_generated",
    "selected_activity_count",
    "selected_stage_events_count",
    "selected_stage_rejections_count",
    "total_stage_events_count",
    "total_stage_rejections_count",
    "skipped_market_data_quality",
    "levels_trade_count_source",
    "entry_trade_count_source",
    "levels_quote_volume_source",
    "entry_quote_volume_source",
    "market_data_quality_status",
    "market_data_quality_reasons",
)
_PNO_DIAGNOSTICS_COVERAGE_SUMMARY_COLUMNS: tuple[str, ...] = ("metric", "value")
_PNO_DIAGNOSTICS_QUALITY_SOURCE_COLUMNS: tuple[str, ...] = ("field", "value", "count")
_PNO_DIAGNOSTICS_QUALITY_REASON_COLUMNS: tuple[str, ...] = ("reason", "count")
_PNO_SECONDS_LOAD_STATUS_COLUMNS: tuple[str, ...] = (
    "symbol",
    "source",
    "role",
    "target_timeframe",
    "timeframe",
    "utc_day",
    "window_start_timestamp_ms",
    "window_end_timestamp_ms",
    "ok",
    "status",
    "reason",
    "source_detail",
    "seconds_status",
    "seconds_reason",
    "path",
    "http_status",
    "fetched_rows",
    "raw_rows",
    "prepared_rows",
    "rows",
    "required_bars",
    "missing_columns",
    "exception_type",
    "exception_message",
)
_PNO_SPARSE_ENTRY_MATERIALIZATION_STATUS_COLUMNS: tuple[str, ...] = (
    "symbol",
    "ok",
    "status",
    "reason",
    "requested_entry_timeframe",
    "target_entry_timeframe",
    "target_entry_timeframe_ms",
    "source_entry_timeframe_ms",
    "entry_load_mode",
    "target_entry_checked",
    "target_entry_rows",
    "target_entry_required_bars",
    "target_entry_usable",
    "target_entry_usable_status",
    "target_entry_usable_reason",
    "materialization_status",
    "materialization_reason",
    "materialized",
    "materialized_bars",
    "windows_requested",
    "windows_loaded",
    "load_status_count",
    "load_reason_counts",
    "market_data_quality_status",
    "market_data_quality_reasons",
)
_PNO_STAGE1_CACHE_STATUS_COLUMNS: tuple[str, ...] = (
    "symbol",
    "source",
    "cache_key",
    "ok",
    "status",
    "reason",
    "path",
    "missing_arrays",
)
_PNO_STAGE5_REVIEW_SYNTHESIS_STATUS_COLUMNS: tuple[str, ...] = (
    "cycle_key",
    "symbol",
    "ok",
    "status",
    "reason",
    "stage4_timestamp_ms",
    "level_valid_timestamp_ms",
    "synthesized_timestamp_ms",
    "synthesized_reason",
    "score",
    "min_score",
    "level",
    "active_high",
)
_PNO_ARTIFACT_LOAD_STATUS_COLUMNS: tuple[str, ...] = (
    "path",
    "ok",
    "status",
    "reason",
    "rows",
)
_PNO_CATEGORY_RESEARCH_CONTEXT_FILTER_STATUS_COLUMNS: tuple[str, ...] = (
    "path",
    "target_path",
    "ok",
    "status",
    "reason",
    "source_rows",
    "target_rows",
    "has_pno_category_id",
)
_PNO_CATEGORY_CHART_COPY_STATUS_COLUMNS: tuple[str, ...] = (
    "symbol",
    "position_index",
    "ok",
    "status",
    "reason",
    "source_path",
    "target_path",
)
_BACKTEST_DATA_LOAD_STATUS_COLUMNS: tuple[str, ...] = (
    "role",
    "symbol",
    "timeframe",
    "requested_timeframe",
    "source_timeframe",
    "target_timeframe",
    "load_mode",
    "target_checked",
    "ok",
    "status",
    "reason",
    "path",
    "missing_columns",
    "raw_rows",
    "prepared_rows",
    "window_days",
    "end_timestamp_ms",
)
_BACKTEST_DATA_LOAD_REJECTION_COLUMNS: tuple[str, ...] = ("role", "status", "count")
_PNO_STAGE_REVIEW_RUN_SUMMARY_COLUMNS: tuple[str, ...] = (
    "preset",
    "stage_id",
    "events_count",
    "events_path",
    "summary_path",
)
_PNO_STAGE_PRESETS: dict[str, tuple[int | None, int | None]] = {
    **{f"s{idx}": (idx, None) for idx in range(1, len(PNO_STAGE_SEQUENCE) + 1)},
    **{f"stage{idx}": (idx, None) for idx in range(1, len(PNO_STAGE_SEQUENCE) + 1)},
    **{f"t{idx}": (None, idx) for idx in range(1, len(PNO_STAGE_SEQUENCE) + 1)},
    **{f"through{idx}": (None, idx) for idx in range(1, len(PNO_STAGE_SEQUENCE) + 1)},
}


def _pno_output_symbol_stem(symbol: object) -> str:
    normalized = str(symbol).replace("/", "_")
    return "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in normalized)


def _build_filtered_pno_stage_rejections(
    *,
    stage_rejections_by_stage: dict[str, dict[str, list[dict[str, object]]]],
    selected_stage_ids: tuple[str, ...],
) -> dict[str, dict[str, list[dict[str, object]]]]:
    filtered: dict[str, dict[str, list[dict[str, object]]]] = {}
    selected_stage_set = set(selected_stage_ids)
    for stage_id in PNO_STAGE_SEQUENCE:
        if stage_id not in selected_stage_set:
            filtered[stage_id] = {}
            continue
        filtered_groups: dict[str, list[dict[str, object]]] = {}
        for reason, rows in stage_rejections_by_stage.get(stage_id, {}).items():
            selected_rows = _select_stage_review_rejection_rows(stage_id=stage_id, reason=reason, rows=rows)
            if selected_rows:
                filtered_groups[reason] = selected_rows
        filtered[stage_id] = filtered_groups
    return filtered


def _warn_if_pno_backtest_window_too_short(
    *,
    strategy_id: str,
    backtest_days: int | None,
    levels_timeframe: Timeframe,
    logger: Logger,
) -> None:
    if strategy_id != "pno" or backtest_days is None:
        return

    defaults = PnoParams(symbol="", levels_timeframe=levels_timeframe)
    required_levels_bars_by_timeframe = {
        Timeframe.M5: defaults.min_data_5m,
        Timeframe.M1: defaults.min_data_1m,
    }
    required_levels_bars = required_levels_bars_by_timeframe.get(levels_timeframe)
    if required_levels_bars is None:
        return

    timeframe_ms = int(levels_timeframe.to_milliseconds())
    requested_levels_bars = int((int(backtest_days) * 86_400_000) // timeframe_ms) + 1
    if requested_levels_bars >= required_levels_bars:
        return

    required_days = max(
        1,
        int(((required_levels_bars - 1) * timeframe_ms + 86_400_000 - 1) // 86_400_000),
    )
    logger.warning(
        "Окно PNO слишком короткое: --days %s даёт максимум около %s баров %s, "
        "а Stage1 требует минимум %s. Увеличь --days минимум до %s.",
        backtest_days,
        requested_levels_bars,
        levels_timeframe.value,
        required_levels_bars,
        required_days,
    )


def _format_runtime_eta(seconds: float | None) -> str:
    if seconds is None:
        return "--ч --м --с"
    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}ч {minutes:02d}м {secs:02d}с"


def _format_runtime_progress(*, title: str, checked: int, total: int, eta_seconds: float | None) -> str:
    return f"{title}: {checked} из {total}. ETA: {_format_runtime_eta(eta_seconds)}"


def _to_bool_flag(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _resolve_plot_rejected_flag(args: argparse.Namespace) -> bool:
    raw_value = getattr(args, "plot_rejected", None)
    if raw_value is not None:
        return _to_bool_flag(raw_value, default=False)
    return _to_bool_flag(getattr(args, "plot", None), default=False)


def _resolve_collect_diagnostics_flag(args: argparse.Namespace) -> bool:
    raw_value = getattr(args, "collect_diagnostics", None)
    if raw_value is not None:
        return _to_bool_flag(raw_value, default=True)
    light_run = getattr(args, "light_run", None)
    if light_run is not None:
        return not _to_bool_flag(light_run, default=False)
    return True


def _resolve_pno_stage_cycle_key(row: dict[str, object]) -> str | None:
    symbol = str(row.get("symbol") or "")
    active_high_timestamp_ms = _safe_int(row.get("active_high_timestamp_ms"))
    pullback_low_timestamp_ms = _safe_int(row.get("pullback_low_timestamp_ms"))
    level = _safe_float(row.get("level"))
    level_first_timestamp_ms = _safe_int(row.get("level_first_local_high_timestamp_ms"))
    level_last_timestamp_ms = _safe_int(row.get("level_last_local_high_timestamp_ms"))
    if (
        not symbol
        or active_high_timestamp_ms is None
        or pullback_low_timestamp_ms is None
        or level is None
        or level_first_timestamp_ms is None
        or level_last_timestamp_ms is None
    ):
        return None
    return (
        f"{symbol}|{active_high_timestamp_ms}|{pullback_low_timestamp_ms}|"
        f"{level_first_timestamp_ms}|{level_last_timestamp_ms}|{level:.8f}"
    )




def _resolve_pno_stage4_review_key(row: dict[str, object]) -> str | None:
    """Returns a stable key for one unique Stage-4 level setup.

    Stage 4 may emit the same BOS/level on several consecutive entry bars while
    the setup is still alive. For review/export purposes those rows describe one
    setup, not several independent opportunities.
    """
    structural_key = _resolve_pno_stage_cycle_key(row)
    category_id = str(row.get("pno_category_id") or "")
    profile_variant_id = str(row.get("pno_profile_variant_id") or "")
    profile_key = f"{category_id}|{profile_variant_id}"
    if structural_key is not None:
        return f"{profile_key}|{structural_key}"

    symbol = str(row.get("symbol") or "")
    active_high_timestamp_ms = _safe_int(row.get("active_high_timestamp_ms"))
    pullback_low_timestamp_ms = _safe_int(row.get("pullback_low_timestamp_ms"))
    structure_high_timestamp_ms = _safe_int(row.get("structure_high_timestamp_ms"))
    structure_low_timestamp_ms = _safe_int(row.get("structure_low_timestamp_ms"))
    level = _safe_float(row.get("level"))
    if (
        not symbol
        or active_high_timestamp_ms is None
        or pullback_low_timestamp_ms is None
        or structure_high_timestamp_ms is None
        or structure_low_timestamp_ms is None
        or level is None
    ):
        return None
    return (
        f"{profile_key}|{symbol}|{active_high_timestamp_ms}|{pullback_low_timestamp_ms}|"
        f"{structure_high_timestamp_ms}|{structure_low_timestamp_ms}|{level:.8f}"
    )


def _resolve_pno_stage4_review_version(row: dict[str, object]) -> tuple[int, int, int]:
    """Sort key used to keep the most recent representation of a duplicate setup."""
    return (
        _safe_int(row.get("level_valid_timestamp_ms")) or 0,
        _safe_int(row.get("structure_break_timestamp_ms")) or 0,
        _safe_int(row.get("timestamp_ms")) or 0,
    )


def _deduplicate_pno_stage4_review_rows(
    rows: list[dict[str, object]],
) -> tuple[list[dict[str, object]], int]:
    """Collapses repeated Stage-4 rows for the same level/cycle/profile.

    Rows without enough metadata are left untouched. For duplicates we keep the
    latest row, because downstream Stage-5 synthetic rejection generation already
    interprets the latest Stage-4 row as the active setup state.
    """
    selected_by_key: dict[str, tuple[int, dict[str, object]]] = {}
    passthrough_rows: list[tuple[int, dict[str, object]]] = []
    duplicate_count = 0

    for index, row in enumerate(rows):
        key = _resolve_pno_stage4_review_key(row)
        if key is None:
            passthrough_rows.append((index, row))
            continue

        previous = selected_by_key.get(key)
        if previous is None:
            selected_by_key[key] = (index, row)
            continue

        duplicate_count += 1
        previous_row = previous[1]
        if _resolve_pno_stage4_review_version(row) >= _resolve_pno_stage4_review_version(previous_row):
            selected_by_key[key] = (index, row)

    selected_rows = [*passthrough_rows, *selected_by_key.values()]
    selected_rows.sort(key=lambda item: item[0])
    return [row for _, row in selected_rows], duplicate_count


def _build_stage5_review_rejections_from_stage4(
    *,
    symbol_frames: dict[str, SymbolMtfFrames],
    stage_rows_by_stage: dict[str, list[dict[str, object]]],
    stage_rejections_by_stage: dict[str, dict[str, list[dict[str, object]]]],
    position_rows: list[dict[str, object]],
    min_score: float,
) -> list[dict[str, object]]:
    existing_stage5_keys: set[str] = set()
    for row in position_rows:
        key = _resolve_pno_stage_cycle_key(row)
        if key is not None:
            existing_stage5_keys.add(key)
    for rows in stage_rejections_by_stage.get(PNO_STAGE_5_POSITION, {}).values():
        for row in rows:
            key = _resolve_pno_stage_cycle_key(row)
            if key is not None:
                existing_stage5_keys.add(key)

    latest_stage4_by_key: dict[str, dict[str, object]] = {}
    for row in stage_rows_by_stage.get(PNO_STAGE_4_LEVEL, []):
        key = _resolve_pno_stage_cycle_key(row)
        if key is None:
            continue
        prev = latest_stage4_by_key.get(key)
        row_timestamp_ms = _safe_int(row.get("timestamp_ms")) or 0
        prev_timestamp_ms = _safe_int(prev.get("timestamp_ms")) or -1 if prev is not None else -1
        if prev is None or row_timestamp_ms >= prev_timestamp_ms:
            latest_stage4_by_key[key] = row

    synthetic_rows: dict[str, list[dict[str, object]]] = {}
    status_rows: list[dict[str, object]] = []

    def _status_row(
        row: dict[str, object],
        *,
        key: str,
        ok: bool,
        status: str,
        reason: str,
        synthesized_timestamp_ms: int | None = None,
        synthesized_reason: str = "",
    ) -> dict[str, object]:
        return {
            "cycle_key": key,
            "symbol": str(row.get("symbol") or ""),
            "ok": bool(ok),
            "status": status,
            "reason": reason,
            "stage4_timestamp_ms": _safe_int(row.get("timestamp_ms")) or "",
            "level_valid_timestamp_ms": _safe_int(row.get("level_valid_timestamp_ms")) or "",
            "synthesized_timestamp_ms": synthesized_timestamp_ms if synthesized_timestamp_ms is not None else "",
            "synthesized_reason": synthesized_reason,
            "score": _safe_float(row.get("score")) if _safe_float(row.get("score")) is not None else "",
            "min_score": float(min_score),
            "level": _safe_float(row.get("level")) if _safe_float(row.get("level")) is not None else "",
            "active_high": _safe_float(row.get("active_high")) if _safe_float(row.get("active_high")) is not None else "",
        }

    for key, row in latest_stage4_by_key.items():
        if key in existing_stage5_keys:
            status_rows.append(
                _status_row(row, key=key, ok=True, status="skipped", reason="stage5_already_present")
            )
            continue
        symbol = str(row.get("symbol") or "")
        mtf_frames = symbol_frames.get(symbol)
        if mtf_frames is None or mtf_frames.entry_frame.empty:
            status_rows.append(
                _status_row(row, key=key, ok=False, status="not_synthesized", reason="entry_frame_missing_or_empty")
            )
            continue
        level = _safe_float(row.get("level"))
        if level is None:
            status_rows.append(
                _status_row(row, key=key, ok=False, status="not_synthesized", reason="level_missing")
            )
            continue
        active_high = _safe_float(row.get("active_high"))
        if active_high is not None and level >= active_high:
            status_rows.append(
                _status_row(row, key=key, ok=False, status="not_synthesized", reason="level_not_below_active_high")
            )
            continue
        level_valid_timestamp_ms = _safe_int(row.get("level_valid_timestamp_ms")) or _safe_int(row.get("timestamp_ms"))
        if level_valid_timestamp_ms is None:
            status_rows.append(
                _status_row(
                    row,
                    key=key,
                    ok=False,
                    status="not_synthesized",
                    reason="level_valid_timestamp_missing",
                )
            )
            continue
        entry_frame = mtf_frames.entry_frame
        if "timestamp" not in entry_frame.columns or "high" not in entry_frame.columns:
            status_rows.append(
                _status_row(
                    row,
                    key=key,
                    ok=False,
                    status="not_synthesized",
                    reason="entry_frame_missing_timestamp_or_high",
                )
            )
            continue
        post_level_frame = entry_frame.loc[entry_frame["timestamp"] >= level_valid_timestamp_ms]
        if post_level_frame.empty:
            status_rows.append(
                _status_row(row, key=key, ok=False, status="not_synthesized", reason="post_level_frame_empty")
            )
            continue
        crossed_frame = post_level_frame.loc[post_level_frame["high"] >= level]
        if crossed_frame.empty:
            status_rows.append(
                _status_row(
                    row,
                    key=key,
                    ok=False,
                    status="not_synthesized",
                    reason="no_wick_cross_after_level_valid",
                )
            )
            continue
        signal_row = crossed_frame.iloc[0]
        score = _safe_float(row.get("score")) or 0.0
        reason = "level_crossed_below_min_score" if score < float(min_score) else "level_crossed_no_trade"
        signal_timestamp_ms = int(signal_row["timestamp"])
        status_rows.append(
            _status_row(
                row,
                key=key,
                ok=True,
                status="synthesized",
                reason="stage5_review_rejection_synthesized",
                synthesized_timestamp_ms=signal_timestamp_ms,
                synthesized_reason=reason,
            )
        )
        synthetic_rows.setdefault(reason, []).append(
            {
                **row,
                "stage_id": PNO_STAGE_5_POSITION,
                "reason": reason,
                "timestamp_ms": signal_timestamp_ms,
                "entry_signal_timestamp_ms": signal_timestamp_ms,
                "entry_price": float(level),
                "entry_plan": float(level),
            }
        )

    for reason, rows in synthetic_rows.items():
        stage_rejections_by_stage[PNO_STAGE_5_POSITION].setdefault(reason, []).extend(rows)
    return status_rows


def _resolve_strategy_id(args: argparse.Namespace) -> str:
    strategy_override = getattr(args, "strategy", None)
    if strategy_override is None:
        return "pno"
    normalized = str(strategy_override).strip().lower()
    if normalized != "pno":
        raise ValueError(f"Неподдерживаемый strategy_id: {normalized}")
    return normalized


def _resolve_results_dir_for_strategy(base_results_dir: Path, strategy_id: str) -> Path:
    if strategy_id != "pno":
        raise ValueError(f"Неподдерживаемый strategy_id: {strategy_id}")
    return base_results_dir / "strategy" / strategy_id


def _resolve_backtest_run_root_dir(base_results_dir: Path, strategy_id: str) -> Path:
    timestamp_label = time.strftime("%Y%m%d_%H%M%S")
    return Path(base_results_dir) / _BACKTEST_RUNS_DIR_NAME / f"{timestamp_label}_{strategy_id}"


def _resolve_saved_backtest_root_dir(path: Path) -> Path:
    normalized = Path(path)
    candidates = [normalized, normalized.parent, normalized.parent.parent]
    for candidate in candidates:
        if (candidate / _BACKTEST_RUN_CONTEXT_FILE_NAME).exists():
            return candidate
    return normalized


def _resolve_results_row_number(results: pd.DataFrame, selected_row: pd.Series) -> int:
    selected_index = selected_row.name
    for row_number, (row_index, _) in enumerate(results.iterrows(), start=1):
        if row_index == selected_index:
            return row_number
    return 1


def _build_data_load_status_row(
    result: SymbolDataLoadResult,
    *,
    role: str,
    window_days: int | None,
    end_timestamp_ms: int | None,
    status_override: str | None = None,
    reason_override: str | None = None,
    requested_timeframe: Timeframe | None = None,
    source_timeframe: Timeframe | None = None,
    target_timeframe: Timeframe | None = None,
    load_mode: str | None = None,
    target_checked: bool | None = None,
) -> dict[str, object]:
    return {
        "role": role,
        "symbol": result.symbol,
        "timeframe": result.timeframe.value,
        "requested_timeframe": requested_timeframe.value if requested_timeframe is not None else result.timeframe.value,
        "source_timeframe": source_timeframe.value if source_timeframe is not None else result.timeframe.value,
        "target_timeframe": target_timeframe.value if target_timeframe is not None else result.timeframe.value,
        "load_mode": load_mode or "source_frame",
        "target_checked": "" if target_checked is None else bool(target_checked),
        "ok": bool(result.ok),
        "status": status_override or result.status,
        "reason": reason_override or result.reason,
        "path": "" if result.path is None else str(result.path),
        "missing_columns": "|".join(result.missing_columns),
        "raw_rows": int(result.raw_rows),
        "prepared_rows": int(result.prepared_rows),
        "window_days": int(window_days) if window_days is not None else "",
        "end_timestamp_ms": int(end_timestamp_ms) if end_timestamp_ms is not None else "",
    }

def _write_backtest_data_load_artifacts(
    run_root_dir: Path,
    *,
    status_rows: list[dict[str, object]],
) -> dict[str, int]:
    status_frame = (
        pd.DataFrame(status_rows)
        if status_rows
        else pd.DataFrame(columns=list(_BACKTEST_DATA_LOAD_STATUS_COLUMNS))
    )
    status_frame = status_frame.reindex(columns=list(_BACKTEST_DATA_LOAD_STATUS_COLUMNS))
    status_frame.to_csv(run_root_dir / _BACKTEST_DATA_LOAD_STATUS_FILE_NAME, index=False)

    rejection_counts: Counter[tuple[str, str]] = Counter()
    if not status_frame.empty:
        for row in status_frame.to_dict("records"):
            if bool(row.get("ok")):
                continue
            rejection_counts[(str(row.get("role") or "unknown"), str(row.get("status") or "unknown"))] += 1
    rejection_rows = [
        {"role": role, "status": status, "count": int(count)}
        for (role, status), count in sorted(rejection_counts.items())
    ]
    rejection_frame = (
        pd.DataFrame(rejection_rows)
        if rejection_rows
        else pd.DataFrame(columns=list(_BACKTEST_DATA_LOAD_REJECTION_COLUMNS))
    )
    rejection_frame = rejection_frame.reindex(columns=list(_BACKTEST_DATA_LOAD_REJECTION_COLUMNS))
    rejection_frame.to_csv(run_root_dir / _BACKTEST_DATA_LOAD_REJECTIONS_FILE_NAME, index=False)
    return {f"{role}:{status}": int(count) for (role, status), count in sorted(rejection_counts.items())}


def _resolve_pno_run_context_metadata(
    *,
    strategy: object,
    config: AppConfig,
) -> tuple[str | None, str | None, str | None]:
    if not isinstance(strategy, PnoStrategy):
        return None, None, None
    pno_category_mode = getattr(config.strategy, "pno_category_mode", None)
    pno_grid = strategy.build_parameter_grid()
    pno_entry_modes = sorted({params.entry_confirmation_mode for params in pno_grid})
    pno_variant_ids = sorted({params.pno_variant_id for params in pno_grid})
    pno_entry_confirmation_mode = "+".join(pno_entry_modes) if pno_entry_modes else None
    pno_variant_id = "+".join(pno_variant_ids) if pno_variant_ids else None
    return pno_category_mode, pno_entry_confirmation_mode, pno_variant_id


def _write_backtest_run_context(
    run_root_dir: Path,
    *,
    strategy_id: str,
    results_file_name: str,
    levels_timeframe: Timeframe,
    entry_timeframe: Timeframe,
    backtest_days: int | None,
    end_timestamp_ms: int | None,
    symbols: list[str],
    plot_requested: bool,
    pno_stage: int | None,
    pno_through_stage: int | None,
    pno_category_mode: str | None = None,
    pno_entry_confirmation_mode: str | None = None,
    pno_variant_id: str | None = None,
    data_load_rejections: dict[str, int] | None = None,
    data_load_status_file_name: str | None = None,
    data_load_rejections_file_name: str | None = None,
    source_entry_timeframe: Timeframe | None = None,
    entry_load_mode: str | None = None,
) -> None:
    payload = {
        "strategy_id": strategy_id,
        "strategy_results_rel_dir": str(Path("strategy") / strategy_id),
        "position_plots_rel_dir": str(Path("strategy") / strategy_id / "position_plots"),
        "results_file_name": results_file_name,
        "levels_tf": levels_timeframe.value,
        "entry_tf": entry_timeframe.value,
        "requested_entry_tf": entry_timeframe.value,
        "source_entry_tf": (source_entry_timeframe or entry_timeframe).value,
        "entry_load_mode": entry_load_mode or "source_frame",
        "target_entry_checked_in_data_load": bool((source_entry_timeframe or entry_timeframe) == entry_timeframe),
        "days": int(backtest_days) if backtest_days is not None else None,
        "end_timestamp_ms": int(end_timestamp_ms) if end_timestamp_ms is not None else None,
        "symbols": list(symbols),
        "symbols_count": int(len(symbols)),
        "plot_requested": bool(plot_requested),
        "pno_stage": int(pno_stage) if pno_stage is not None else None,
        "pno_through_stage": int(pno_through_stage) if pno_through_stage is not None else None,
        "pno_category_mode": pno_category_mode,
        "pno_entry_confirmation_mode": pno_entry_confirmation_mode,
        "pno_variant_id": pno_variant_id,
        "data_load_rejections": dict(data_load_rejections or {}),
        "data_load_status_file_name": data_load_status_file_name,
        "data_load_rejections_file_name": data_load_rejections_file_name,
    }
    (run_root_dir / _BACKTEST_RUN_CONTEXT_FILE_NAME).write_text(
        _to_compact_json(payload),
        encoding="utf-8",
    )


def _write_backtest_plot_request(
    run_root_dir: Path,
    *,
    selected_row_number: int,
) -> None:
    payload = {
        "selected_row_number": int(selected_row_number),
    }
    (run_root_dir / _BACKTEST_PLOT_REQUEST_FILE_NAME).write_text(
        _to_compact_json(payload),
        encoding="utf-8",
    )


def _load_saved_backtest_request(run_dir: Path) -> tuple[Path, dict[str, object], dict[str, object]]:
    run_root_dir = _resolve_saved_backtest_root_dir(run_dir)
    context_path = run_root_dir / _BACKTEST_RUN_CONTEXT_FILE_NAME
    if not context_path.exists():
        raise FileNotFoundError(f"Не нашёл контекст сохранённого прогона: {context_path}")
    context = json.loads(context_path.read_text(encoding="utf-8"))
    request_path = run_root_dir / _BACKTEST_PLOT_REQUEST_FILE_NAME
    request: dict[str, object] = {}
    if request_path.exists():
        request = json.loads(request_path.read_text(encoding="utf-8"))
    return run_root_dir, context, request


def _build_plot_backtest_args(
    *,
    run_root_dir: Path,
    context: dict[str, object],
    request: dict[str, object],
) -> argparse.Namespace:
    missing_context_fields = [
        field_name
        for field_name in (
            "strategy_id",
            "strategy_results_rel_dir",
            "position_plots_rel_dir",
            "results_file_name",
            "levels_tf",
            "entry_tf",
        )
        if not context.get(field_name)
    ]
    if missing_context_fields:
        raise ValueError(f"run_context_missing_required_fields: {', '.join(missing_context_fields)}")

    strategy_id = str(context["strategy_id"])
    strategy_results_rel_dir = Path(str(context["strategy_results_rel_dir"]))
    position_plots_rel_dir = Path(str(context["position_plots_rel_dir"]))
    results_file_name = str(context["results_file_name"])
    results_input = run_root_dir / strategy_results_rel_dir / results_file_name
    if not results_input.exists():
        raise FileNotFoundError(f"run_context_results_file_missing: {results_input}")
    raw_symbols = context.get("symbols")
    symbols = list(raw_symbols) if isinstance(raw_symbols, list) else None
    return argparse.Namespace(
        command="run-backtest",
        symbols=symbols,
        top_n=None,
        days=context.get("days"),
        end_timestamp_ms=context.get("end_timestamp_ms"),
        levels_tf=context.get("levels_tf"),
        entry_tf=context.get("entry_tf"),
        strategy=strategy_id,
        pno_deposit=None,
        pno_risk_pct=None,
        pno_entry_confirmation_mode=None,
        pno_category_mode=context.get("pno_category_mode"),
        pno_stage=context.get("pno_stage"),
        pno_through_stage=context.get("pno_through_stage"),
        plot_rejected=True,
        collect_diagnostics=None,
        plot=None,
        light_run=None,
        plot_from_results=True,
        results_input=str(results_input),
        output_dir=str(run_root_dir / position_plots_rel_dir),
        id=None,
        row_number=request.get("selected_row_number"),
    )


def _sanitize_artifact_dir_name(name: str) -> str:
    sanitized = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in name.strip().lower())
    sanitized = sanitized.strip("_")
    return sanitized or "combo"


def _resolve_pno_artifact_dir_name(params_row: pd.Series, *, row_number: int) -> str:
    entry_mode = str(params_row.get("entry_confirmation_mode") or "").strip().lower()
    if entry_mode:
        return _sanitize_artifact_dir_name(entry_mode)
    variant_id = str(params_row.get("pno_variant_id") or "").strip().lower()
    if variant_id == "baseline_close":
        return "close_above"
    if variant_id.startswith("baseline_"):
        variant_tail = variant_id.removeprefix("baseline_")
        if variant_tail:
            return _sanitize_artifact_dir_name(variant_tail)
    if variant_id:
        return _sanitize_artifact_dir_name(variant_id)
    return f"combo_{row_number}"


def _resolve_pno_artifact_rows(results: pd.DataFrame) -> list[tuple[str, pd.Series]]:
    resolved: list[tuple[str, pd.Series]] = []
    used_names: Counter[str] = Counter()
    for row_number, (_, row) in enumerate(results.iterrows(), start=1):
        base_name = _resolve_pno_artifact_dir_name(row, row_number=row_number)
        used_names[base_name] += 1
        final_name = base_name if used_names[base_name] == 1 else f"{base_name}_{used_names[base_name]}"
        resolved.append((final_name, row))
    return resolved


def _clone_args_with_output_dir(args: argparse.Namespace, *, output_dir: Path) -> argparse.Namespace:
    scoped_args = argparse.Namespace(**vars(args))
    scoped_args.output_dir = str(output_dir)
    return scoped_args


def _filter_pno_rows_by_category(
    rows: list[dict[str, object]],
    *,
    category_id: str,
) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in rows
        if str(row.get("pno_category_id") or "") == category_id
    ]


def _filter_pno_rejections_by_category(
    groups: dict[str, list[dict[str, object]]],
    *,
    category_id: str,
) -> dict[str, list[dict[str, object]]]:
    filtered: dict[str, list[dict[str, object]]] = {}
    for reason, rows in groups.items():
        filtered_rows = _filter_pno_rows_by_category(rows, category_id=category_id)
        if filtered_rows:
            filtered[reason] = filtered_rows
    return filtered


def _write_pno_category_research_context(
    *,
    source_diagnostics_dir: Path,
    target_diagnostics_dir: Path,
    category_id: str,
) -> None:
    source_research_dir = source_diagnostics_dir / "research_context"
    target_research_dir = target_diagnostics_dir / "research_context"
    target_research_dir.mkdir(parents=True, exist_ok=True)
    artifact_status_rows: list[dict[str, object]] = []
    filter_status_rows: list[dict[str, object]] = []
    if not source_research_dir.exists():
        filter_status_rows.append(
            {
                "path": str(source_research_dir),
                "target_path": str(target_research_dir),
                "ok": False,
                "status": "not_copied",
                "reason": "source_research_context_missing",
                "source_rows": 0,
                "target_rows": 0,
                "has_pno_category_id": False,
            }
        )
        _write_frame_from_records(
            target_research_dir / "research_context_filter_status.csv",
            filter_status_rows,
            columns=_PNO_CATEGORY_RESEARCH_CONTEXT_FILTER_STATUS_COLUMNS,
        )
        return
    for csv_path in source_research_dir.glob("*.csv"):
        read_result = _read_csv_with_status(csv_path)
        artifact_status_rows.append(_artifact_status_row(read_result))
        frame = read_result.frame
        target_path = target_research_dir / csv_path.name
        if frame.empty:
            frame.to_csv(target_path, index=False)
            filter_status_rows.append(
                {
                    "path": str(csv_path),
                    "target_path": str(target_path),
                    "ok": bool(read_result.ok),
                    "status": "copied_empty",
                    "reason": str(read_result.reason),
                    "source_rows": int(len(frame)),
                    "target_rows": int(len(frame)),
                    "has_pno_category_id": "pno_category_id" in frame.columns,
                }
            )
            continue
        if "pno_category_id" not in frame.columns:
            frame.to_csv(target_path, index=False)
            filter_status_rows.append(
                {
                    "path": str(csv_path),
                    "target_path": str(target_path),
                    "ok": bool(read_result.ok),
                    "status": "copied_unfiltered",
                    "reason": "pno_category_id_missing",
                    "source_rows": int(len(frame)),
                    "target_rows": int(len(frame)),
                    "has_pno_category_id": False,
                }
            )
            continue
        filtered = frame.loc[frame["pno_category_id"].astype(str) == category_id].copy()
        filtered.to_csv(target_path, index=False)
        filter_status_rows.append(
            {
                "path": str(csv_path),
                "target_path": str(target_path),
                "ok": bool(read_result.ok),
                "status": "filtered_by_category",
                "reason": "pno_category_id_matched",
                "source_rows": int(len(frame)),
                "target_rows": int(len(filtered)),
                "has_pno_category_id": True,
            }
        )
    _write_frame_from_records(
        target_research_dir / "artifact_load_status.csv",
        artifact_status_rows,
        columns=_PNO_ARTIFACT_LOAD_STATUS_COLUMNS,
    )
    _write_frame_from_records(
        target_research_dir / "research_context_filter_status.csv",
        filter_status_rows,
        columns=_PNO_CATEGORY_RESEARCH_CONTEXT_FILTER_STATUS_COLUMNS,
    )


def _export_pno_category_artifacts(
    *,
    diagnostics_dir: Path,
    export_result: dict[str, object],
    symbol_frames: dict[str, SymbolMtfFrames],
    logger: Logger,
    log_prefix: str,
) -> None:
    category_meta: dict[str, tuple[str, int]] = {}
    for position_row in cast(list[dict[str, object]], export_result.get("all_position_rows", [])):
        category_id = str(position_row.get("pno_category_id") or "")
        if not category_id:
            continue
        category_meta.setdefault(
            category_id,
            (
                str(position_row.get("pno_category_label") or category_id),
                int(position_row.get("pno_category_priority") or 0),
            ),
        )
    for rows in cast(dict[str, list[dict[str, object]]], export_result.get("stage_rows_by_stage", {})).values():
        for row in rows:
            category_id = str(row.get("pno_category_id") or "")
            if not category_id:
                continue
            category_meta.setdefault(
                category_id,
                (
                    str(row.get("pno_category_label") or category_id),
                    int(row.get("pno_category_priority") or 0),
                ),
            )
    for reason_groups in cast(dict[str, dict[str, list[dict[str, object]]]], export_result.get("stage_rejections_by_stage", {})).values():
        for rows in reason_groups.values():
            for row in rows:
                category_id = str(row.get("pno_category_id") or "")
                if not category_id:
                    continue
                category_meta.setdefault(
                    category_id,
                    (
                        str(row.get("pno_category_label") or category_id),
                        int(row.get("pno_category_priority") or 0),
                    ),
                )
    if not category_meta:
        return
    if set(category_meta) == {"discovery"}:
        return

    categories_root = diagnostics_dir.parent / "categories"
    categories_root.mkdir(parents=True, exist_ok=True)
    diagnostics_payloads = cast(list[dict[str, object]], export_result.get("diagnostics_payloads", []))
    stage_rows_by_stage = cast(dict[str, list[dict[str, object]]], export_result.get("stage_rows_by_stage", {}))
    stage_rejections_by_stage = cast(dict[str, dict[str, list[dict[str, object]]]], export_result.get("stage_rejections_by_stage", {}))
    selected_stage_ids = cast(tuple[str, ...], export_result.get("selected_stage_ids", ()))

    for category_id, (category_label, category_priority) in sorted(category_meta.items(), key=lambda category_item: (category_item[1][1], category_item[0])):
        category_root = categories_root / category_id
        if category_root.exists():
            shutil.rmtree(category_root)
        category_diagnostics_dir = category_root / "pno_diagnostics"
        category_diagnostics_dir.mkdir(parents=True, exist_ok=True)
        category_charts_dir = category_diagnostics_dir / "charts"
        category_charts_dir.mkdir(parents=True, exist_ok=True)

        category_position_rows_all: list[dict[str, object]] = []
        category_chart_copy_status_rows: list[dict[str, object]] = []
        category_symbols = 0
        for item in diagnostics_payloads:
            symbol = str(item["symbol"])
            base_name = _pno_output_symbol_stem(symbol)
            root_payload_path = diagnostics_dir / f"{base_name}_diagnostics.json"
            if not root_payload_path.exists():
                continue
            root_payload = json.loads(root_payload_path.read_text(encoding="utf-8"))
            position_rows_all = list(item.get("position_rows") or [])
            filtered_position_rows = _filter_pno_rows_by_category(position_rows_all, category_id=category_id)
            diagnostics_payload = dict(item.get("diagnostics") or {})
            stage_events = _filter_pno_rows_by_category(list(diagnostics_payload.get("stage_events") or []), category_id=category_id)
            stage_rejections = _filter_pno_rows_by_category(list(diagnostics_payload.get("stage_rejections") or []), category_id=category_id)
            if not filtered_position_rows and not stage_events and not stage_rejections:
                continue
            category_symbols += 1
            category_position_rows_all.extend(filtered_position_rows)
            chart_paths_root = list(root_payload.get("chart_paths") or [])
            selected_chart_paths: list[str] = []
            if chart_paths_root and len(chart_paths_root) == len(position_rows_all):
                for position_index, position_row in enumerate(position_rows_all):
                    if str(position_row.get("pno_category_id") or "") != category_id:
                        continue
                    source_chart = Path(str(chart_paths_root[position_index]))
                    if not source_chart.exists():
                        category_chart_copy_status_rows.append(
                            {
                                "symbol": symbol,
                                "position_index": int(position_index),
                                "ok": False,
                                "status": "not_copied",
                                "reason": "source_chart_missing",
                                "source_path": str(source_chart),
                                "target_path": "",
                            }
                        )
                        continue
                    target_chart = category_charts_dir / source_chart.name
                    shutil.copy2(source_chart, target_chart)
                    selected_chart_paths.append(str(target_chart))
                    category_chart_copy_status_rows.append(
                        {
                            "symbol": symbol,
                            "position_index": int(position_index),
                            "ok": True,
                            "status": "copied",
                            "reason": "ok",
                            "source_path": str(source_chart),
                            "target_path": str(target_chart),
                        }
                    )
            else:
                reason = "chart_paths_missing" if not chart_paths_root else "chart_paths_count_mismatch"
                for position_index, position_row in enumerate(position_rows_all):
                    if str(position_row.get("pno_category_id") or "") != category_id:
                        continue
                    category_chart_copy_status_rows.append(
                        {
                            "symbol": symbol,
                            "position_index": int(position_index),
                            "ok": False,
                            "status": "not_copied",
                            "reason": reason,
                            "source_path": "",
                            "target_path": "",
                        }
                    )
            payload = {
                "symbol": symbol,
                "positions_generated": len(filtered_position_rows),
                "diagnostics": {
                    **diagnostics_payload,
                    "positions_generated": len(filtered_position_rows),
                    "stage_events": stage_events,
                    "stage_rejections": stage_rejections,
                },
                "positions": filtered_position_rows,
                "chart_paths": selected_chart_paths,
            }
            (category_diagnostics_dir / f"{base_name}_diagnostics.json").write_text(
                _to_compact_json(payload),
                encoding="utf-8",
            )
            if filtered_position_rows:
                pd.DataFrame(filtered_position_rows).to_csv(category_diagnostics_dir / f"{base_name}_positions.csv", index=False)

        _write_pno_category_research_context(
            source_diagnostics_dir=diagnostics_dir,
            target_diagnostics_dir=category_diagnostics_dir,
            category_id=category_id,
        )
        _write_frame_from_records(
            category_diagnostics_dir / "chart_copy_status.csv",
            category_chart_copy_status_rows,
            columns=_PNO_CATEGORY_CHART_COPY_STATUS_COLUMNS,
        )
        category_stage_rows = {
            stage_id: _filter_pno_rows_by_category(rows, category_id=category_id)
            for stage_id, rows in stage_rows_by_stage.items()
        }
        category_stage_rejections = {
            stage_id: _filter_pno_rejections_by_category(reason_groups, category_id=category_id)
            for stage_id, reason_groups in stage_rejections_by_stage.items()
        }
        _export_pno_stage_reviews(
            diagnostics_dir=category_diagnostics_dir,
            symbol_frames=symbol_frames,
            stage_rows_by_stage=category_stage_rows,
            stage_rejections_by_stage=category_stage_rejections,
            selected_stage_ids=selected_stage_ids,
            render_charts=False,
            passed_chart_stage_ids=(),
            logger=logger,
            log_prefix=f"{log_prefix} [{category_id}]",
        )
        context_payload = {
            "category_id": category_id,
            "category_label": category_label,
            "category_priority": category_priority,
            "symbols": category_symbols,
            "positions_generated": len(category_position_rows_all),
        }
        (category_root / "category_context.json").write_text(_to_compact_json(context_payload), encoding="utf-8")
    logger.debug(
        "%s: PNO-артефакты разложены по категориям.\n  Категории: %s.",
        log_prefix,
        ", ".join(sorted(category_meta)),
    )


def _export_pno_category_csv_split(
    *,
    results_dir: Path,
    logger: Logger,
) -> None:
    """Разделяет results.csv по категориям в отдельные CSV файлы."""
    results_path = results_dir / "results.csv"
    if not results_path.exists():
        return

    results = pd.read_csv(results_path)
    if "pno_category_id" not in results.columns:
        return

    categories_root = results_dir / "categories"
    categories_root.mkdir(parents=True, exist_ok=True)

    category_ids = results["pno_category_id"].dropna().unique()
    for category_id in category_ids:
        category_df = results[results["pno_category_id"] == category_id].copy()
        if category_df.empty:
            continue
        category_path = categories_root / f"{category_id}_results.csv"
        category_df.to_csv(category_path, index=False)

    logger.debug(
        "Категории получили отдельные CSV.\n  Папка: %s.",
        categories_root,
    )


def _plot_pno_grid_artifacts(
    *,
    config: AppConfig,
    args: argparse.Namespace,
    logger: Logger,
    strategy: PnoStrategy,
    symbol_frames: dict[str, SymbolMtfFrames],
    results: pd.DataFrame,
    levels_timeframe: Timeframe,
    entry_timeframe: Timeframe,
    log_prefix: str,
    diagnostics_cache: BacktestGenerationDiagnosticsCache | None = None,
) -> bool:
    resolved_rows = _resolve_pno_artifact_rows(results)
    shared_stage_ids = tuple(stage_id for stage_id in PNO_STAGE_SEQUENCE if stage_id != PNO_STAGE_5_POSITION)
    shared_diagnostics_dir: Path | None = None
    for index, (artifact_name, params_row) in enumerate(resolved_rows):
        scoped_args = _clone_args_with_output_dir(
            args,
            output_dir=Path(config.backtest.results_dir) / "position_plots" / artifact_name,
        )
        if index == 0:
            if not _plot_for_strategy_dispatch(
                config=config,
                args=scoped_args,
                logger=logger,
                strategy_id="pno",
                strategy=strategy,
                symbol_frames=symbol_frames,
                params_row=params_row,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
                log_prefix=f"{log_prefix} [{artifact_name}]",
                diagnostics_cache=diagnostics_cache,
            ):
                return False
            shared_diagnostics_dir = Path(scoped_args.output_dir) / "pno_diagnostics"
            continue
        if shared_diagnostics_dir is None:
            return False
        _plot_pno_diagnostics_with_shared_stage_reviews(
            config=config,
            args=scoped_args,
            logger=logger,
            strategy=strategy,
            symbol_frames=symbol_frames,
            params_row=params_row,
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
            log_prefix=f"{log_prefix} [{artifact_name}]",
            shared_diagnostics_dir=shared_diagnostics_dir,
            shared_stage_ids=shared_stage_ids,
            diagnostics_cache=diagnostics_cache,
        )
    return True


def _export_pno_grid_artifacts_without_stage_charts(
    *,
    config: AppConfig,
    args: argparse.Namespace,
    logger: Logger,
    strategy: PnoStrategy,
    symbol_frames: dict[str, SymbolMtfFrames],
    results: pd.DataFrame,
    levels_timeframe: Timeframe,
    entry_timeframe: Timeframe,
    diagnostics_cache: BacktestGenerationDiagnosticsCache | None = None,
) -> None:
    for artifact_name, params_row in _resolve_pno_artifact_rows(results):
        output_dir = Path(config.backtest.results_dir) / "position_plots" / artifact_name
        diagnostics_dir = output_dir / "pno_diagnostics"
        export_result = _export_pno_diagnostics_context_for_symbols(
            diagnostics_dir=diagnostics_dir,
            args=_clone_args_with_output_dir(args, output_dir=output_dir),
            logger=logger,
            strategy=strategy,
            symbol_frames=symbol_frames,
            params_row=params_row,
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
            diagnostics_cache=diagnostics_cache,
        )
        _render_pno_position_charts_from_export_result(
            diagnostics_dir=diagnostics_dir,
            export_result=export_result,
            logger=logger,
        )
        _export_pno_category_artifacts(
            diagnostics_dir=diagnostics_dir,
            export_result=export_result,
            symbol_frames=symbol_frames,
            logger=logger,
            log_prefix=f"[{artifact_name}]",
        )


def _sync_shared_stage_reviews(
    *,
    shared_diagnostics_dir: Path,
    target_diagnostics_dir: Path,
    shared_stage_ids: tuple[str, ...],
) -> None:
    shared_stage_reviews_dir = shared_diagnostics_dir / "stage_reviews"
    target_stage_reviews_dir = target_diagnostics_dir / "stage_reviews"
    target_stage_reviews_dir.mkdir(parents=True, exist_ok=True)
    for stage_id in shared_stage_ids:
        source_stage_dir = shared_stage_reviews_dir / stage_id
        target_stage_dir = target_stage_reviews_dir / stage_id
        if not source_stage_dir.exists():
            continue
        shutil.copytree(source_stage_dir, target_stage_dir, dirs_exist_ok=True)

    shared_manifest_result = _read_csv_with_status(shared_stage_reviews_dir / "manifest.csv")
    target_manifest_result = _read_csv_with_status(target_stage_reviews_dir / "manifest.csv")
    _write_frame_from_records(
        target_stage_reviews_dir / "artifact_load_status.csv",
        [_artifact_status_row(shared_manifest_result), _artifact_status_row(target_manifest_result)],
        columns=_PNO_ARTIFACT_LOAD_STATUS_COLUMNS,
    )
    shared_manifest = shared_manifest_result.frame
    target_manifest = target_manifest_result.frame
    shared_rows: list[dict[str, object]] = []
    for _, row in shared_manifest.iterrows():
        stage_id = str(row.get("stage_id") or "")
        if stage_id not in shared_stage_ids:
            continue
        payload = {str(key): value for key, value in row.to_dict().items()}
        payload["passed_events_path"] = str(target_stage_reviews_dir / stage_id / "passed" / "events.csv")
        shared_rows.append(payload)
    target_rows = [
        row.to_dict()
        for _, row in target_manifest.iterrows()
        if str(row.get("stage_id") or "") not in shared_stage_ids
    ]
    _write_frame_from_records(
        target_stage_reviews_dir / "manifest.csv",
        [*shared_rows, *target_rows],
        columns=(
            "stage_id",
            "events_count",
            "passed_count",
            "rejected_count",
            "rejected_review_count",
            "rejected_filtered_count",
            "stage_dir",
            "events_path",
            "summary_path",
            "passed_events_path",
            "passed_charts_count",
            "rejected_charts_count",
            "trade_count_source_counts",
        ),
    )


def _plot_pno_diagnostics_with_shared_stage_reviews(
    *,
    config: AppConfig,
    args: argparse.Namespace,
    logger: Logger,
    strategy: PnoStrategy,
    symbol_frames: dict[str, SymbolMtfFrames],
    params_row: pd.Series,
    levels_timeframe: Timeframe,
    entry_timeframe: Timeframe,
    log_prefix: str,
    shared_diagnostics_dir: Path,
    shared_stage_ids: tuple[str, ...],
    diagnostics_cache: BacktestGenerationDiagnosticsCache | None = None,
) -> None:
    output_dir = Path(getattr(args, "output_dir", None) or (config.backtest.results_dir / "position_plots"))
    diagnostics_dir = output_dir / "pno_diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    export_result = _export_pno_diagnostics_context_for_symbols(
        diagnostics_dir=diagnostics_dir,
        args=args,
        logger=logger,
        strategy=strategy,
        symbol_frames=symbol_frames,
        params_row=params_row,
        levels_timeframe=levels_timeframe,
        entry_timeframe=entry_timeframe,
        diagnostics_cache=diagnostics_cache,
    )
    total_charts_generated = _render_pno_position_charts_from_export_result(
        diagnostics_dir=diagnostics_dir,
        export_result=export_result,
        logger=None,
    )
    _export_pno_stage_reviews(
        diagnostics_dir=diagnostics_dir,
        symbol_frames=symbol_frames,
        stage_rows_by_stage=cast(dict[str, list[dict[str, object]]], export_result["stage_rows_by_stage"]),
        stage_rejections_by_stage=cast(
            dict[str, dict[str, list[dict[str, object]]]],
            export_result["stage_rejections_by_stage"],
        ),
        selected_stage_ids=(PNO_STAGE_5_POSITION,),
        render_charts=True,
        passed_chart_stage_ids=(),
        logger=logger,
        log_prefix=log_prefix,
    )
    _sync_shared_stage_reviews(
        shared_diagnostics_dir=shared_diagnostics_dir,
        target_diagnostics_dir=diagnostics_dir,
        shared_stage_ids=shared_stage_ids,
    )
    _export_pno_category_artifacts(
        diagnostics_dir=diagnostics_dir,
        export_result=export_result,
        symbol_frames=symbol_frames,
        logger=logger,
        log_prefix=log_prefix,
    )


def _build_futures_symbol_map(symbols: list[str]) -> dict[str, str]:
    return {
        normalize_symbol(symbol): symbol
        for symbol in symbols
    }


_PNO_RESULT_ROW_STR_FIELDS: tuple[tuple[str, str], ...] = (
    ("pno_variant_id", "pno_variant_id"),
    ("entry_confirmation_mode", "pno_entry_confirmation_mode"),
)

_PNO_RESULT_ROW_BOOL_FIELDS: tuple[tuple[str, str], ...] = (
    ("ideal_like_impulse_enabled", "pno_ideal_like_impulse_enabled"),
    ("ideal_like_ignore_decay_invalidation", "pno_ideal_like_ignore_decay_invalidation"),
)

_PNO_RESULT_ROW_INT_FIELDS: tuple[tuple[str, str], ...] = (
    ("min_data_5m", "pno_min_data_5m"),
    ("min_data_1m", "pno_min_data_1m"),
    ("pullback_max_age_bars", "pno_pullback_max_age_bars"),
    ("structure_min_leg_bars", "pno_structure_min_leg_bars"),
    ("stage1_pre_pump_ema_crosses_min", "pno_stage1_pre_pump_ema_crosses_min"),
    ("stage1_flow_hold_bars", "pno_stage1_flow_hold_bars"),
    ("stage1_flow_hold_window_bars", "pno_stage1_flow_hold_window_bars"),
    ("stage3_fast_reclaim_max_pullback_age_bars", "pno_stage3_fast_reclaim_max_pullback_age_bars"),
    ("ideal_like_level_latest_high_max_age_bars", "pno_ideal_like_level_latest_high_max_age_bars"),
    ("level_latest_high_max_age_bars", "pno_level_latest_high_max_age_bars"),
    ("level_max_age_bars_upper_tf", "pno_level_max_age_bars_upper_tf"),
    ("max_level_touches", "pno_max_level_touches"),
    ("close_above_be_step_bars", "pno_close_above_be_step_bars"),
)

_PNO_RESULT_ROW_FLOAT_FIELDS: tuple[tuple[str, str], ...] = (
    ("pno_deposit", "pno_deposit"),
    ("pno_risk_pct", "pno_risk_pct"),
    ("pno_r_position", "pno_r_position"),
    ("fee_rate", "pno_fee_rate"),
    ("min_stage1_leg_v1", "pno_min_stage1_leg_v1"),
    ("min_stage1_leg_v5_fraction", "pno_min_stage1_leg_v5_fraction"),
    ("stage1_hold_fraction", "pno_stage1_hold_fraction"),
    ("pullback_min_v1", "pno_pullback_min_v1"),
    ("pullback_min_pump_fraction_5m", "pno_pullback_min_pump_fraction_5m"),
    ("pullback_valid_min_leg_fraction", "pno_pullback_valid_min_leg_fraction"),
    ("pullback_valid_max_leg_fraction", "pno_pullback_valid_max_leg_fraction"),
    ("pullback_invalid_max_leg_fraction", "pno_pullback_invalid_max_leg_fraction"),
    ("pullback_valid_max_v5", "pno_pullback_valid_max_v5"),
    ("pullback_invalid_max_v5", "pno_pullback_invalid_max_v5"),
    ("structure_terminal_retrace_fraction", "pno_structure_terminal_retrace_fraction"),
    ("stage1_min_cumulative_quote_volume", "pno_stage1_min_cumulative_quote_volume"),
    ("stage1_barcode_max_fraction_1h", "pno_stage1_barcode_max_fraction_1h"),
    ("stage1_barcode_tr_atr_fraction", "pno_stage1_barcode_tr_atr_fraction"),
    ("stage1_barcode_tr_price_fraction", "pno_stage1_barcode_tr_price_fraction"),
    ("stage1_min_impulse_atr_pre", "pno_stage1_min_impulse_atr_pre"),
    ("stage1_min_peak_bar_tr_atr_pre", "pno_stage1_min_peak_bar_tr_atr_pre"),
    ("stage1_min_volume_ratio_start", "pno_stage1_min_volume_ratio_start"),
    ("stage1_min_trade_ratio_start", "pno_stage1_min_trade_ratio_start"),
    ("stage1_min_volume_ratio_continue", "pno_stage1_min_volume_ratio_continue"),
    ("stage1_min_trade_ratio_continue", "pno_stage1_min_trade_ratio_continue"),
    ("stage1_flow_hold_min_start_fraction", "pno_stage1_flow_hold_min_start_fraction"),
    ("stage1_active_context_min_start_fraction", "pno_stage1_active_context_min_start_fraction"),
    ("stage1_active_context_min_baseline_ratio", "pno_stage1_active_context_min_baseline_ratio"),
    ("stage1_min_path_efficiency", "pno_stage1_min_path_efficiency"),
    ("stage1_max_wick_share", "pno_stage1_max_wick_share"),
    ("stage1_min_body_share_mean", "pno_stage1_min_body_share_mean"),
    ("stage1_max_flat_body_share", "pno_stage1_max_flat_body_share"),
    ("stage1_min_body_wick_edge", "pno_stage1_min_body_wick_edge"),
    ("stage1_max_micro_flat_bar_share", "pno_stage1_max_micro_flat_bar_share"),
    ("stage1_max_active_high_upper_wick_share", "pno_stage1_max_active_high_upper_wick_share"),
    ("stage1_max_red_body_share_5m", "pno_stage1_max_red_body_share_5m"),
    ("stage1_max_counterflow_ratio_5m", "pno_stage1_max_counterflow_ratio_5m"),
    ("stage1_max_red_body_share_1m", "pno_stage1_max_red_body_share_1m"),
    ("stage1_max_counterflow_ratio_1m", "pno_stage1_max_counterflow_ratio_1m"),
    ("stage1_min_pump_pct", "pno_stage1_min_pump_pct"),
    ("stage1_min_pretrend_range_ratio_2h", "pno_stage1_min_pretrend_range_ratio_2h"),
    ("stage1_pre_pump_high_max_fraction_of_leg", "pno_stage1_pre_pump_high_max_fraction_of_leg"),
    ("stage3_max_post_high_wick_share", "pno_stage3_max_post_high_wick_share"),
    ("stage3_max_post_high_body_overlap_rate", "pno_stage3_max_post_high_body_overlap_rate"),
    ("stage3_min_post_high_5m_volume_support_fraction", "pno_stage3_min_post_high_5m_volume_support_fraction"),
    ("stage3_fast_reclaim_min_post_high_5m_volume_support_fraction", "pno_stage3_fast_reclaim_min_post_high_5m_volume_support_fraction"),
    ("ideal_like_min_impulse_atr_pre", "pno_ideal_like_min_impulse_atr_pre"),
    ("ideal_like_min_peak_bar_tr_atr_pre", "pno_ideal_like_min_peak_bar_tr_atr_pre"),
    ("ideal_like_min_volume_ratio_start", "pno_ideal_like_min_volume_ratio_start"),
    ("ideal_like_min_path_efficiency", "pno_ideal_like_min_path_efficiency"),
    ("ideal_like_max_wick_share", "pno_ideal_like_max_wick_share"),
    ("ideal_like_min_body_share_mean", "pno_ideal_like_min_body_share_mean"),
    ("ideal_like_min_body_wick_edge", "pno_ideal_like_min_body_wick_edge"),
    ("ideal_like_max_micro_flat_bar_share", "pno_ideal_like_max_micro_flat_bar_share"),
    ("ideal_like_max_active_high_upper_wick_share", "pno_ideal_like_max_active_high_upper_wick_share"),
    ("ideal_like_max_counterflow_ratio_5m", "pno_ideal_like_max_counterflow_ratio_5m"),
    ("ideal_like_relaxed_level_maturity_fraction", "pno_ideal_like_relaxed_level_maturity_fraction"),
    ("level_cluster_spread_v1", "pno_level_cluster_spread_v1"),
    ("level_cluster_relaxed_spread_v1", "pno_level_cluster_relaxed_spread_v1"),
    ("level_touch_tolerance_v1", "pno_level_touch_tolerance_v1"),
    ("level_low_minor_break_v1", "pno_level_low_minor_break_v1"),
    ("level_low_major_break_v1", "pno_level_low_major_break_v1"),
    ("level_min_maturity_fraction", "pno_level_min_maturity_fraction"),
    ("level_rearm_min_distance_v1", "pno_level_rearm_min_distance_v1"),
    ("min_score", "pno_min_score"),
    ("strong_score", "pno_strong_score"),
    ("slip_plan_v1_fraction", "pno_slip_plan_v1_fraction"),
    ("min_tick_fraction", "pno_min_tick_fraction"),
    ("max_entry_pullback_fraction", "pno_max_entry_pullback_fraction"),
    ("min_entry_rr", "pno_min_entry_rr"),
    ("close_above_max_entry_pos", "pno_close_above_max_entry_pos"),
    ("close_above_min_entry_pos", "pno_close_above_min_entry_pos"),
    ("close_above_max_pullback_fraction_of_leg", "pno_close_above_max_pullback_fraction_of_leg"),
    ("close_above_max_post_high_wick_share", "pno_close_above_max_post_high_wick_share"),
    ("close_above_max_active_high_upper_wick_share", "pno_close_above_max_active_high_upper_wick_share"),
    ("close_above_min_active_high_close_position", "pno_close_above_min_active_high_close_position"),
    ("close_above_min_post_high_alternation_rate", "pno_close_above_min_post_high_alternation_rate"),
    ("close_above_min_signal_volume_vs_recent", "pno_close_above_min_signal_volume_vs_recent"),
    ("close_above_min_signal_ema9_slope_3", "pno_close_above_min_signal_ema9_slope_3"),
    ("close_above_min_signal_ema20_slope_3", "pno_close_above_min_signal_ema20_slope_3"),
    ("close_above_min_signal_ema_spread_pct", "pno_close_above_min_signal_ema_spread_pct"),
    ("close_above_min_signal_close_position_in_chop", "pno_close_above_min_signal_close_position_in_chop"),
    ("close_above_choppy_overlap_threshold", "pno_close_above_choppy_overlap_threshold"),
    ("tp1_share", "pno_tp1_share"),
    ("be_arm_to_active_high_fraction", "pno_be_arm_to_active_high_fraction"),
    ("close_above_be_start_fraction", "pno_close_above_be_start_fraction"),
    ("close_above_be_step_fraction", "pno_close_above_be_step_fraction"),
    ("close_above_be_min_fraction", "pno_close_above_be_min_fraction"),
    ("be_buffer_r_fraction", "pno_be_buffer_r_fraction"),
)

_PNO_REQUIRED_RESULTS_ROW_COLUMNS: tuple[str, ...] = tuple(
    dict.fromkeys(
        (
            "pno_levels_timeframe",
            "pno_entry_timeframe",
            *(column for _field_name, column in _PNO_RESULT_ROW_STR_FIELDS),
            *(column for _field_name, column in _PNO_RESULT_ROW_BOOL_FIELDS),
            *(column for _field_name, column in _PNO_RESULT_ROW_INT_FIELDS),
            *(column for _field_name, column in _PNO_RESULT_ROW_FLOAT_FIELDS),
        )
    )
)


def _is_missing_pno_results_scalar(value: object) -> bool:
    if value is None or value is pd.NA:
        return True
    if isinstance(value, (pd.Series, pd.DataFrame)):
        return False
    return bool(pd.isna(value))


def _find_missing_pno_results_row_columns(row: pd.Series) -> list[str]:
    return [
        column
        for column in _PNO_REQUIRED_RESULTS_ROW_COLUMNS
        if column not in row.index or _is_missing_pno_results_scalar(row.get(column))
    ]


def _build_pno_params_template_from_row(
    row: pd.Series,
    *,
    levels_timeframe: Timeframe,
    entry_timeframe: Timeframe,
) -> PnoParams:
    missing_columns = _find_missing_pno_results_row_columns(row)
    if missing_columns:
        raise ValueError(
            "results_row_missing_required_pno_fields: "
            + ",".join(missing_columns)
        )

    def _required_str(column_name: str) -> str:
        return str(row[column_name])

    def _required_float(column_name: str) -> float:
        try:
            return float(row[column_name])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"results_row_invalid_pno_field: {column_name}") from exc

    def _required_int(column_name: str) -> int:
        raw_value = row[column_name]
        try:
            numeric_value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"results_row_invalid_pno_field: {column_name}") from exc
        if not numeric_value.is_integer():
            raise ValueError(f"results_row_invalid_pno_field: {column_name}")
        return int(numeric_value)

    def _required_bool(column_name: str) -> bool:
        raw_value = row[column_name]
        if isinstance(raw_value, bool):
            return raw_value
        normalized = str(raw_value).strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
        raise ValueError(f"results_row_invalid_pno_field: {column_name}")

    row_levels_timeframe = _required_str("pno_levels_timeframe")
    row_entry_timeframe = _required_str("pno_entry_timeframe")
    if row_levels_timeframe != levels_timeframe.value or row_entry_timeframe != entry_timeframe.value:
        raise ValueError(
            "results_row_timeframe_mismatch: "
            f"row={row_levels_timeframe}/{row_entry_timeframe}, "
            f"requested={levels_timeframe.value}/{entry_timeframe.value}"
        )

    params_payload: dict[str, object] = {}
    for field_name, column_name in _PNO_RESULT_ROW_STR_FIELDS:
        params_payload[field_name] = _required_str(column_name)
    for field_name, column_name in _PNO_RESULT_ROW_BOOL_FIELDS:
        params_payload[field_name] = _required_bool(column_name)
    for field_name, column_name in _PNO_RESULT_ROW_INT_FIELDS:
        params_payload[field_name] = _required_int(column_name)
    for field_name, column_name in _PNO_RESULT_ROW_FLOAT_FIELDS:
        params_payload[field_name] = _required_float(column_name)

    return PnoParams(
        symbol="",
        levels_timeframe=levels_timeframe,
        entry_timeframe=entry_timeframe,
        **params_payload,
    )

def _build_pno_params_from_row(
    row: pd.Series,
    *,
    symbol: str,
    levels_timeframe: Timeframe,
    entry_timeframe: Timeframe,
) -> PnoParams:
    template = _build_pno_params_template_from_row(
        row,
        levels_timeframe=levels_timeframe,
        entry_timeframe=entry_timeframe,
    )
    return replace(template, symbol=symbol)


def _flatten_position_for_diagnostics(position: object) -> dict[str, object]:
    payload = {
        "entry_timestamp_ms": int(getattr(position, "entry_timestamp_ms")),
        "exit_timestamp_ms": int(getattr(position, "exit_timestamp_ms")),
        "pnl": float(getattr(position, "pnl")),
        "pnl_percent": float(getattr(getattr(position, "pnl_percent"), "value", getattr(position, "pnl_percent"))),
        "result_type": str(getattr(getattr(position, "result_type"), "value", getattr(position, "result_type"))),
    }
    metadata = getattr(position, "metadata", None)
    if isinstance(metadata, dict):
        payload.update(metadata)
    return payload


def _resolve_pno_stage_ids(args: argparse.Namespace) -> tuple[str, ...]:
    raw_stage = getattr(args, "pno_stage", None)
    raw_through_stage = getattr(args, "pno_through_stage", None)
    if raw_stage is not None and raw_through_stage is not None:
        raise ValueError("Use only one of --pno-stage or --pno-through-stage")

    if raw_stage is not None:
        stage_number = int(raw_stage)
        if stage_number < 1 or stage_number > len(PNO_STAGE_SEQUENCE):
            raise ValueError(f"--pno-stage must be in range 1..{len(PNO_STAGE_SEQUENCE)}")
        return (PNO_STAGE_SEQUENCE[stage_number - 1],)

    if raw_through_stage is not None:
        stage_number = int(raw_through_stage)
        if stage_number < 1 or stage_number > len(PNO_STAGE_SEQUENCE):
            raise ValueError(f"--pno-through-stage must be in range 1..{len(PNO_STAGE_SEQUENCE)}")
        return tuple(PNO_STAGE_SEQUENCE[:stage_number])

    return tuple(PNO_STAGE_SEQUENCE)


def _resolve_pno_stage_preset(raw_value: object) -> tuple[int | None, int | None, str]:
    preset = str(raw_value or "").strip().lower()
    if preset not in _PNO_STAGE_PRESETS:
        supported = ", ".join(sorted(_PNO_STAGE_PRESETS))
        raise ValueError(f"Unsupported pno-stage preset: {preset}. Supported: {supported}")
    stage, through_stage = _PNO_STAGE_PRESETS[preset]
    return stage, through_stage, preset


def _pno_stage_metric_column_name(stage_id: str) -> str:
    return f"pno_stage_hits_{stage_id}"


def _plot_pno_diagnostics_for_symbols(
    *,
    config: AppConfig,
    args: argparse.Namespace,
    logger: Logger,
    strategy: PnoStrategy,
    symbol_frames: dict[str, SymbolMtfFrames],
    params_row: pd.Series,
    levels_timeframe: Timeframe,
    entry_timeframe: Timeframe,
    log_prefix: str,
    diagnostics_cache: BacktestGenerationDiagnosticsCache | None = None,
) -> None:
    output_dir = Path(getattr(args, "output_dir", None) or (config.backtest.results_dir / "position_plots"))
    diagnostics_dir = output_dir / "pno_diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    export_result = _export_pno_diagnostics_context_for_symbols(
        diagnostics_dir=diagnostics_dir,
        args=args,
        logger=logger,
        strategy=strategy,
        symbol_frames=symbol_frames,
        params_row=params_row,
        levels_timeframe=levels_timeframe,
        entry_timeframe=entry_timeframe,
        diagnostics_cache=diagnostics_cache,
    )
    charts_dir = diagnostics_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    total_charts_generated = 0
    diagnostics_payloads = cast(list[dict[str, object]], export_result.get("diagnostics_payloads", []))
    chart_symbols_total = sum(1 for item in diagnostics_payloads if item["position_rows"])
    chart_symbols_done = 0
    chart_render_start_time = time.monotonic()
    if chart_symbols_total > 0:
        logger.warning("Графики: генерация")
    for item in diagnostics_payloads:
        symbol = str(item["symbol"])
        position_rows = list(item["position_rows"])
        if not position_rows:
            continue
        chart_paths = _render_pno_position_charts_for_symbol(
            charts_dir=charts_dir,
            symbol=symbol,
            mtf_frames=item["mtf_frames"],
            position_rows=position_rows,
            seconds_frame_provider=item.get("seconds_frame_provider"),
        )
        total_charts_generated += len(chart_paths)
        base_name = _pno_output_symbol_stem(symbol)
        diagnostics_path = diagnostics_dir / f"{base_name}_diagnostics.json"
        payload = json.loads(diagnostics_path.read_text(encoding="utf-8"))
        payload["chart_paths"] = chart_paths
        diagnostics_path.write_text(_to_compact_json(payload), encoding="utf-8")
        chart_symbols_done += 1
        if chart_symbols_total > 0 and (chart_symbols_done == chart_symbols_total or chart_symbols_done % 10 == 0):
            elapsed = max(time.monotonic() - chart_render_start_time, 1e-9)
            rate = chart_symbols_done / elapsed
            remaining = chart_symbols_total - chart_symbols_done
            eta_seconds = remaining / rate if rate > 0.0 else None
            logger.info(
                "%s",
                _format_runtime_progress(
                    title="Графики",
                    checked=chart_symbols_done,
                    total=chart_symbols_total,
                    eta_seconds=eta_seconds,
                ),
            )

    _export_pno_stage_reviews(
        diagnostics_dir=diagnostics_dir,
        symbol_frames=symbol_frames,
        stage_rows_by_stage=cast(dict[str, list[dict[str, object]]], export_result["stage_rows_by_stage"]),
        stage_rejections_by_stage=cast(
            dict[str, dict[str, list[dict[str, object]]]],
            export_result["stage_rejections_by_stage"],
        ),
        selected_stage_ids=cast(tuple[str, ...], export_result["selected_stage_ids"]),
        render_charts=True,
        passed_chart_stage_ids=cast(tuple[str, ...], export_result["passed_chart_stage_ids"]),
        logger=logger,
        log_prefix=log_prefix,
    )
    _export_pno_category_artifacts(
        diagnostics_dir=diagnostics_dir,
        export_result=export_result,
        symbol_frames=symbol_frames,
        logger=logger,
        log_prefix=log_prefix,
    )

    logger.debug(
        "%s: pno diagnostics saved stage_symbols=%s stage_events=%s positions_generated=%s charts_generated=%s stages=%s output_dir=%s",
        log_prefix,
        export_result["symbols_with_stage_events"],
        export_result["total_stage_events"],
        export_result["total_positions_generated"],
        total_charts_generated,
        ",".join(cast(tuple[str, ...], export_result["selected_stage_ids"])),
        diagnostics_dir,
    )
    if export_result["total_stage_events"] == 0 and export_result["symbols_with_positions"] == 0:
        logger.debug("Нет событий и позиций для визуализации")


def _render_pno_position_charts_from_export_result(
    *,
    diagnostics_dir: Path,
    export_result: dict[str, object],
    logger: Logger,
) -> int:
    charts_dir = diagnostics_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    total_charts_generated = 0
    diagnostics_payloads = cast(list[dict[str, object]], export_result.get("diagnostics_payloads", []))
    chart_symbols_total = sum(1 for item in diagnostics_payloads if item["position_rows"])
    chart_symbols_done = 0
    chart_render_start_time = time.monotonic()
    if chart_symbols_total > 0:
        logger.warning("Графики: генерация")
    for item in diagnostics_payloads:
        symbol = str(item["symbol"])
        position_rows = list(item["position_rows"])
        if not position_rows:
            continue
        chart_paths = _render_pno_position_charts_for_symbol(
            charts_dir=charts_dir,
            symbol=symbol,
            mtf_frames=item["mtf_frames"],
            position_rows=position_rows,
            seconds_frame_provider=item.get("seconds_frame_provider"),
        )
        total_charts_generated += len(chart_paths)
        base_name = _pno_output_symbol_stem(symbol)
        diagnostics_path = diagnostics_dir / f"{base_name}_diagnostics.json"
        payload = json.loads(diagnostics_path.read_text(encoding="utf-8"))
        payload["chart_paths"] = chart_paths
        diagnostics_path.write_text(_to_compact_json(payload), encoding="utf-8")
        chart_symbols_done += 1
        if chart_symbols_total > 0 and (chart_symbols_done == chart_symbols_total or chart_symbols_done % 10 == 0):
            elapsed = max(time.monotonic() - chart_render_start_time, 1e-9)
            rate = chart_symbols_done / elapsed
            remaining = chart_symbols_total - chart_symbols_done
            eta_seconds = remaining / rate if rate > 0.0 else None
            logger.info(
                "%s",
                _format_runtime_progress(
                    title="Графики",
                    checked=chart_symbols_done,
                    total=chart_symbols_total,
                    eta_seconds=eta_seconds,
                ),
            )
    if chart_symbols_total > 0:
        logger.warning("Графики готовы")
    return total_charts_generated


def _render_pno_position_charts_only(
    *,
    output_dir: Path,
    logger: Logger,
    strategy: PnoStrategy,
    symbol_frames: dict[str, SymbolMtfFrames],
    params_row: pd.Series,
    levels_timeframe: Timeframe,
    entry_timeframe: Timeframe,
) -> int:
    charts_dir = output_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    positions_dir = output_dir / "positions"
    positions_dir.mkdir(parents=True, exist_ok=True)
    pno_params_template = _build_pno_params_template_from_row(
        params_row,
        levels_timeframe=levels_timeframe,
        entry_timeframe=entry_timeframe,
    )
    params_payload = {f"param_{key}": value for key, value in params_row.to_dict().items()}
    total_symbols = len(symbol_frames)
    total_charts_generated = 0
    processed_symbols = 0
    all_position_rows: list[dict[str, object]] = []
    render_started_at = time.monotonic()
    if total_symbols > 0:
        logger.warning("Графики: генерация")
    for symbol, mtf_frames in symbol_frames.items():
        params = replace(pno_params_template, symbol=symbol)
        positions = strategy.generate_events_multi_tf(mtf_frames=mtf_frames, params=params)
        position_rows = [_flatten_position_for_diagnostics(position) for position in positions]
        if position_rows:
            chart_paths = _render_pno_position_charts_for_symbol(
                charts_dir=charts_dir,
                symbol=symbol,
                mtf_frames=mtf_frames,
                position_rows=position_rows,
                seconds_frame_provider=getattr(strategy, "_seconds_provider", None),
            )
            total_charts_generated += len(chart_paths)
            exported_rows: list[dict[str, object]] = []
            for position_index, position_row in enumerate(position_rows):
                chart_path = str(chart_paths[position_index]) if position_index < len(chart_paths) else ""
                exported_row = {
                    **params_payload,
                    **position_row,
                    "symbol": str(position_row.get("symbol") or symbol),
                    "levels_timeframe": levels_timeframe.value,
                    "entry_timeframe": entry_timeframe.value,
                    "chart_path": chart_path,
                }
                exported_rows.append(exported_row)
                all_position_rows.append(exported_row)
            pd.DataFrame(exported_rows).to_csv(positions_dir / f"{_pno_output_symbol_stem(symbol)}_positions.csv", index=False)
            pd.DataFrame(all_position_rows).to_csv(output_dir / "all_positions.csv", index=False)
        processed_symbols += 1
        if total_symbols > 0 and (processed_symbols == total_symbols or processed_symbols % _PROGRESS_LOG_EVERY == 0):
            elapsed = max(time.monotonic() - render_started_at, 1e-9)
            rate = processed_symbols / elapsed
            remaining = total_symbols - processed_symbols
            eta_seconds = remaining / rate if rate > 0.0 else None
            logger.info(
                "%s",
                _format_runtime_progress(
                    title="Графики",
                    checked=processed_symbols,
                    total=total_symbols,
                    eta_seconds=eta_seconds,
                ),
            )
    if all_position_rows:
        all_positions_path = output_dir / "all_positions.csv"
        pd.DataFrame(all_position_rows).to_csv(all_positions_path, index=False)
        logger.debug("Таблица позиций сохранена: строк %s, файл %s.", len(all_position_rows), all_positions_path)
    logger.debug("Графики сохранены: файлов %s, папка %s.", total_charts_generated, charts_dir)
    if total_symbols > 0:
        logger.warning("Графики готовы")
    return total_charts_generated


def _export_pno_grid_position_charts_only(
    *,
    config: AppConfig,
    logger: Logger,
    strategy: PnoStrategy,
    symbol_frames: dict[str, SymbolMtfFrames],
    results: pd.DataFrame,
    levels_timeframe: Timeframe,
    entry_timeframe: Timeframe,
) -> None:
    for artifact_name, params_row in _resolve_pno_artifact_rows(results):
        output_dir = Path(config.backtest.results_dir) / "position_plots" / artifact_name
        _render_pno_position_charts_only(
            output_dir=output_dir,
            logger=logger,
            strategy=strategy,
            symbol_frames=symbol_frames,
            params_row=params_row,
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
        )


def _log_human_backtest_summary(
    *,
    logger: Logger,
    results: pd.DataFrame,
) -> None:
    if results.empty:
        logger.debug("Итог бэктеста пустой: позиций нет.")
        return
    best_row = results.iloc[0]
    positions_count = int(best_row.get("positions_count", 0) or 0)
    win_rate = float(best_row.get("win_rate", 0.0) or 0.0) * 100.0
    pnl_percent = float(best_row.get("pnl_percent", 0.0) or 0.0)
    profit_factor = float(best_row.get("profit_factor", 0.0) or 0.0)
    max_drawdown_pct = float(best_row.get("max_drawdown_pct", 0.0) or 0.0)
    avg_position_pct = (pnl_percent / positions_count) if positions_count else 0.0
    runner_success_count = int(best_row.get("ppa_runner_success_above_tp1_count", 0) or 0)
    runner_be_count = int(best_row.get("ppa_runner_be_below_tp1_count", 0) or 0)
    runner_loss_count = int(best_row.get("ppa_runner_loss_below_entry_count", 0) or 0)
    logger.debug(
        "%s позиций, винрейт %.1f%%, средняя позиция %.2f%%, итог %.2f%%, профит-фактор %.2f, просадка %.2f%%.",
        positions_count,
        win_rate,
        avg_position_pct,
        pnl_percent,
        profit_factor,
        max_drawdown_pct,
    )
    logger.debug(
        "Выше TP1: %s, между BE и TP1: %s, ниже входа: %s.",
        runner_success_count,
        runner_be_count,
        runner_loss_count,
    )



def _serialize_diagnostics_context_value(value: object) -> object:
    if isinstance(value, (list, tuple, set)):
        return "|".join(str(item) for item in value)
    if isinstance(value, dict):
        return _to_compact_json(value)
    return value


def _write_frame_from_records(path: Path, records: list[dict[str, object]], *, columns: tuple[str, ...]) -> None:
    frame = pd.DataFrame(records) if records else pd.DataFrame(columns=list(columns))
    frame.to_csv(path, index=False)


def _artifact_status_row(result: object) -> dict[str, object]:
    return {
        "path": str(getattr(result, "path", "")),
        "ok": bool(getattr(result, "ok", False)),
        "status": str(getattr(result, "status", "unknown")),
        "reason": str(getattr(result, "reason", "unknown")),
        "rows": int(getattr(result, "rows", 0) or 0),
    }


def _split_quality_reasons(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, float) and pd.isna(value):
        return []
    text = str(value).strip()
    if not text:
        return []
    return [part for part in text.split("|") if part]


def _write_pno_diagnostics_quality_tables(*, diagnostics_dir: Path, coverage_frame: pd.DataFrame) -> None:
    source_rows: list[dict[str, object]] = []
    for field in (
        "levels_trade_count_source",
        "entry_trade_count_source",
        "levels_quote_volume_source",
        "entry_quote_volume_source",
        "market_data_quality_status",
    ):
        if field not in coverage_frame.columns:
            continue
        counts = coverage_frame[field].fillna("").astype(str).value_counts(dropna=False)
        for value, count in counts.items():
            source_rows.append({"field": field, "value": value, "count": int(count)})
    _write_frame_from_records(
        diagnostics_dir / "diagnostics_quality_sources.csv",
        source_rows,
        columns=_PNO_DIAGNOSTICS_QUALITY_SOURCE_COLUMNS,
    )

    reason_counts: Counter[str] = Counter()
    if "market_data_quality_reasons" in coverage_frame.columns:
        for raw_value in coverage_frame["market_data_quality_reasons"]:
            reason_counts.update(_split_quality_reasons(raw_value))
    reason_rows = [
        {"reason": reason, "count": int(count)}
        for reason, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    _write_frame_from_records(
        diagnostics_dir / "diagnostics_quality_reasons.csv",
        reason_rows,
        columns=_PNO_DIAGNOSTICS_QUALITY_REASON_COLUMNS,
    )


def _build_pno_sparse_materialization_status_row(
    *,
    symbol: str,
    context: dict[str, object],
) -> dict[str, object] | None:
    if context.get("entry_load_mode") != "sparse_deferred" and "seconds_materialization_status" not in context:
        return None
    materialization_status = str(context.get("seconds_materialization_status") or "not_attempted")
    usable_status = str(context.get("target_entry_usable_status") or "not_checked")
    usable_reason = str(context.get("target_entry_usable_reason") or "")
    status = usable_status if usable_status != "not_checked" else materialization_status
    reason = usable_reason or str(context.get("seconds_materialization_reason") or "")
    return {
        "symbol": symbol,
        "ok": bool(context.get("target_entry_usable")) if usable_status != "not_checked" else bool(context.get("seconds_materialized")),
        "status": status,
        "reason": reason,
        "requested_entry_timeframe": _serialize_diagnostics_context_value(context.get("requested_entry_timeframe")),
        "target_entry_timeframe": _serialize_diagnostics_context_value(context.get("target_entry_timeframe")),
        "target_entry_timeframe_ms": _serialize_diagnostics_context_value(context.get("target_entry_timeframe_ms")),
        "source_entry_timeframe_ms": _serialize_diagnostics_context_value(context.get("source_entry_timeframe_ms")),
        "entry_load_mode": _serialize_diagnostics_context_value(context.get("entry_load_mode")),
        "target_entry_checked": _serialize_diagnostics_context_value(context.get("target_entry_checked")),
        "target_entry_rows": _serialize_diagnostics_context_value(context.get("target_entry_rows")),
        "target_entry_required_bars": _serialize_diagnostics_context_value(context.get("target_entry_required_bars") or context.get("seconds_materialization_required_bars")),
        "target_entry_usable": _serialize_diagnostics_context_value(context.get("target_entry_usable")),
        "target_entry_usable_status": usable_status,
        "target_entry_usable_reason": usable_reason,
        "materialization_status": materialization_status,
        "materialization_reason": _serialize_diagnostics_context_value(context.get("seconds_materialization_reason")),
        "materialized": _serialize_diagnostics_context_value(context.get("seconds_materialized")),
        "materialized_bars": _serialize_diagnostics_context_value(context.get("seconds_materialized_bars")),
        "windows_requested": _serialize_diagnostics_context_value(context.get("seconds_materialization_windows_requested")),
        "windows_loaded": _serialize_diagnostics_context_value(context.get("seconds_materialization_windows_loaded")),
        "load_status_count": _serialize_diagnostics_context_value(context.get("seconds_materialization_load_status_count")),
        "load_reason_counts": _serialize_diagnostics_context_value(context.get("seconds_materialization_load_reason_counts")),
        "market_data_quality_status": _serialize_diagnostics_context_value(context.get("market_data_quality_status")),
        "market_data_quality_reasons": _serialize_diagnostics_context_value(context.get("market_data_quality_reasons")),
    }


def _write_pno_seconds_and_cache_status_tables(
    *,
    diagnostics_dir: Path,
    seconds_rows: list[dict[str, object]],
    sparse_materialization_rows: list[dict[str, object]],
    stage1_cache_rows: list[dict[str, object]],
) -> None:
    _write_frame_from_records(
        diagnostics_dir / _PNO_SECONDS_LOAD_STATUS_FILE_NAME,
        seconds_rows,
        columns=_PNO_SECONDS_LOAD_STATUS_COLUMNS,
    )
    _write_frame_from_records(
        diagnostics_dir / _PNO_SPARSE_ENTRY_MATERIALIZATION_STATUS_FILE_NAME,
        sparse_materialization_rows,
        columns=_PNO_SPARSE_ENTRY_MATERIALIZATION_STATUS_COLUMNS,
    )
    _write_frame_from_records(
        diagnostics_dir / _PNO_STAGE1_CACHE_STATUS_FILE_NAME,
        stage1_cache_rows,
        columns=_PNO_STAGE1_CACHE_STATUS_COLUMNS,
    )

def _write_pno_diagnostics_coverage(*, diagnostics_dir: Path, rows: list[dict[str, object]]) -> None:
    coverage_frame = pd.DataFrame(rows) if rows else pd.DataFrame(columns=list(_PNO_DIAGNOSTICS_COVERAGE_COLUMNS))
    coverage_path = diagnostics_dir / "diagnostics_coverage.csv"
    coverage_frame.to_csv(coverage_path, index=False)
    if coverage_frame.empty:
        _write_frame_from_records(
            diagnostics_dir / "diagnostics_coverage_summary.csv",
            [{"metric": "symbols_total", "value": 0}],
            columns=_PNO_DIAGNOSTICS_COVERAGE_SUMMARY_COLUMNS,
        )
        _write_pno_diagnostics_quality_tables(diagnostics_dir=diagnostics_dir, coverage_frame=coverage_frame)
        return
    written_mask = coverage_frame["diagnostics_json_written"].astype(bool)
    positions_mask = pd.to_numeric(coverage_frame.get("positions_generated", 0), errors="coerce").fillna(0).gt(0)
    skipped_quality_mask = pd.to_numeric(
        coverage_frame.get("skipped_market_data_quality", 0),
        errors="coerce",
    ).fillna(0).gt(0)
    missing_symbols = coverage_frame.loc[~written_mask, "symbol"].astype(str).tolist()
    summary_rows = [
        {"metric": "symbols_total", "value": int(len(coverage_frame))},
        {"metric": "diagnostics_json_written_count", "value": int(written_mask.sum())},
        {"metric": "diagnostics_json_missing_count", "value": int((~written_mask).sum())},
        {"metric": "symbols_with_positions_count", "value": int(positions_mask.sum())},
        {"metric": "skipped_market_data_quality_count", "value": int(skipped_quality_mask.sum())},
        {"metric": "missing_diagnostics_symbols", "value": "|".join(missing_symbols)},
    ]
    _write_frame_from_records(
        diagnostics_dir / "diagnostics_coverage_summary.csv",
        summary_rows,
        columns=_PNO_DIAGNOSTICS_COVERAGE_SUMMARY_COLUMNS,
    )
    _write_pno_diagnostics_quality_tables(diagnostics_dir=diagnostics_dir, coverage_frame=coverage_frame)

def _export_pno_diagnostics_context_for_symbols(
    *,
    diagnostics_dir: Path,
    args: argparse.Namespace,
    logger: Logger,
    strategy: PnoStrategy,
    symbol_frames: dict[str, SymbolMtfFrames],
    params_row: pd.Series,
    levels_timeframe: Timeframe,
    entry_timeframe: Timeframe,
    diagnostics_cache: BacktestGenerationDiagnosticsCache | None = None,
) -> dict[str, object]:
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    selected_stage_ids = _resolve_pno_stage_ids(args)
    selected_stage_id_set = set(selected_stage_ids)
    passed_chart_stage_ids = ()

    symbols_with_positions = 0
    symbols_with_stage_events = 0
    total_positions_generated = 0
    total_stage_events = 0
    all_position_rows: list[dict[str, object]] = []
    diagnostics_payloads: list[dict[str, object]] = []
    coverage_rows: list[dict[str, object]] = []
    seconds_load_status_rows: list[dict[str, object]] = []
    sparse_materialization_status_rows: list[dict[str, object]] = []
    stage1_cache_status_rows: list[dict[str, object]] = []
    total_symbols = len(symbol_frames)
    symbol_export_start_time = time.monotonic()
    stage_rows_by_stage: dict[str, list[dict[str, object]]] = {stage_id: [] for stage_id in PNO_STAGE_SEQUENCE}
    stage_rejections_by_stage: dict[str, dict[str, list[dict[str, object]]]] = {
        stage_id: {} for stage_id in PNO_STAGE_SEQUENCE
    }

    pno_params_template = _build_pno_params_template_from_row(
        params_row,
        levels_timeframe=levels_timeframe,
        entry_timeframe=entry_timeframe,
    )
    params_signature = BacktestRunner.build_params_signature(strategy, pno_params_template)
    diagnostics_cache_hits = 0
    diagnostics_cache_misses = 0
    if total_symbols > 0:
        logger.info(
            "%s",
            _format_runtime_progress(
                title="Диагностика",
                checked=0,
                total=total_symbols,
                eta_seconds=None,
            ),
        )

    for symbol_index, (symbol, mtf_frames) in enumerate(symbol_frames.items(), start=1):
        cached_generation = diagnostics_cache.get((params_signature, symbol)) if diagnostics_cache is not None else None
        if cached_generation is None:
            if diagnostics_cache is not None:
                diagnostics_cache_misses += 1
            params = replace(pno_params_template, symbol=symbol)
            positions = strategy.generate_events_multi_tf(mtf_frames=mtf_frames, params=params)
            diagnostics = strategy.consume_last_generation_diagnostics()
        else:
            diagnostics_cache_hits += 1
            positions = list(cached_generation.positions)
            diagnostics = dict(cached_generation.diagnostics)
        position_rows = [_flatten_position_for_diagnostics(position) for position in positions]
        all_position_rows.extend(position_rows)
        total_positions_generated += len(position_rows)
        if position_rows:
            symbols_with_positions += 1

        stage_events_raw = diagnostics.get("stage_events", [])
        symbol_stage_events = 0
        if isinstance(stage_events_raw, list):
            for raw_event in stage_events_raw:
                if not isinstance(raw_event, dict):
                    continue
                stage_id = raw_event.get("stage_id")
                if not isinstance(stage_id, str) or stage_id not in stage_rows_by_stage:
                    continue
                if stage_id in selected_stage_id_set:
                    symbol_stage_events += 1
                stage_rows_by_stage[stage_id].append({"symbol": symbol, **raw_event})
        total_stage_events += symbol_stage_events
        if symbol_stage_events > 0:
            symbols_with_stage_events += 1

        stage_rejections_raw = diagnostics.get("stage_rejections", [])
        symbol_stage_rejections = 0
        if isinstance(stage_rejections_raw, list):
            for raw_rejection in stage_rejections_raw:
                if not isinstance(raw_rejection, dict):
                    continue
                stage_id = raw_rejection.get("stage_id")
                if not isinstance(stage_id, str) or stage_id not in stage_rejections_by_stage:
                    continue
                if stage_id in selected_stage_id_set:
                    symbol_stage_rejections += 1
                reason = str(raw_rejection.get("reason") or "unknown")
                stage_rejections_by_stage[stage_id].setdefault(reason, []).append({"symbol": symbol, **raw_rejection})

        if symbol_index == total_symbols or symbol_index % _PROGRESS_LOG_EVERY == 0:
            elapsed = max(time.monotonic() - symbol_export_start_time, 1e-9)
            rate = symbol_index / elapsed
            remaining = total_symbols - symbol_index
            eta_seconds = remaining / rate if rate > 0.0 else None
            logger.info(
                "%s",
                _format_runtime_progress(
                    title="Диагностика",
                    checked=symbol_index,
                    total=total_symbols,
                    eta_seconds=eta_seconds,
                ),
            )

        context = diagnostics.get("context") if isinstance(diagnostics, dict) else {}
        if not isinstance(context, dict):
            context = {}
        seconds_load_statuses = context.get("seconds_materialization_load_statuses")
        if isinstance(seconds_load_statuses, list):
            for status_row in seconds_load_statuses:
                if isinstance(status_row, dict):
                    seconds_load_status_rows.append({"symbol": symbol, **status_row})
        sparse_status_row = _build_pno_sparse_materialization_status_row(symbol=symbol, context=context)
        if sparse_status_row is not None:
            sparse_materialization_status_rows.append(sparse_status_row)
        for cache_context_key in ("stage1_cache_status", "stage1_cache_store_status"):
            cache_status = context.get(cache_context_key)
            if isinstance(cache_status, dict):
                stage1_cache_status_rows.append({"symbol": symbol, **cache_status})
        diagnostics_json_written = bool(position_rows or symbol_stage_events or symbol_stage_rejections)
        selected_activity_count = int(symbol_stage_events + symbol_stage_rejections + len(position_rows))
        all_stage_events_count = len(stage_events_raw) if isinstance(stage_events_raw, list) else 0
        all_stage_rejections_count = len(stage_rejections_raw) if isinstance(stage_rejections_raw, list) else 0
        coverage_rows.append(
            {
                "symbol": symbol,
                "diagnostics_json_written": diagnostics_json_written,
                "skip_reason": "" if diagnostics_json_written else "no_selected_stage_activity",
                "positions_generated": len(position_rows),
                "selected_activity_count": selected_activity_count,
                "selected_stage_events_count": symbol_stage_events,
                "selected_stage_rejections_count": symbol_stage_rejections,
                "total_stage_events_count": all_stage_events_count,
                "total_stage_rejections_count": all_stage_rejections_count,
                "skipped_market_data_quality": int(diagnostics.get("skipped_market_data_quality", 0) or 0),
                "levels_trade_count_source": _serialize_diagnostics_context_value(context.get("levels_trade_count_source")),
                "entry_trade_count_source": _serialize_diagnostics_context_value(context.get("entry_trade_count_source")),
                "levels_quote_volume_source": _serialize_diagnostics_context_value(context.get("levels_quote_volume_source")),
                "entry_quote_volume_source": _serialize_diagnostics_context_value(context.get("entry_quote_volume_source")),
                "market_data_quality_status": _serialize_diagnostics_context_value(context.get("market_data_quality_status")),
                "market_data_quality_reasons": _serialize_diagnostics_context_value(context.get("market_data_quality_reasons")),
            }
        )

        if not diagnostics_json_written:
            continue

        diagnostics_payloads.append(
            {
                "symbol": symbol,
                "mtf_frames": mtf_frames,
                "seconds_frame_provider": getattr(strategy, "_seconds_provider", None),
                "position_rows": position_rows,
                "diagnostics": diagnostics,
            }
        )
        base_name = _pno_output_symbol_stem(symbol)
        payload = {
            "symbol": symbol,
            "positions_generated": len(position_rows),
            "diagnostics": diagnostics,
            "positions": position_rows,
        }
        (diagnostics_dir / f"{base_name}_diagnostics.json").write_text(_to_compact_json(payload), encoding="utf-8")
        if position_rows:
            pd.DataFrame(position_rows).to_csv(diagnostics_dir / f"{base_name}_positions.csv", index=False)

    deduplicated_stage4_rows, stage4_duplicate_count = _deduplicate_pno_stage4_review_rows(
        stage_rows_by_stage.get(PNO_STAGE_4_LEVEL, [])
    )
    stage_rows_by_stage[PNO_STAGE_4_LEVEL] = deduplicated_stage4_rows
    stage5_review_synthesis_status_rows = _build_stage5_review_rejections_from_stage4(
        symbol_frames=symbol_frames,
        stage_rows_by_stage=stage_rows_by_stage,
        stage_rejections_by_stage=stage_rejections_by_stage,
        position_rows=all_position_rows,
        min_score=float(params_row.get("pno_min_score", 0.0) or 0.0),
    )
    research_context_dir = diagnostics_dir / "research_context"
    research_context_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        stage5_review_synthesis_status_rows,
        columns=_PNO_STAGE5_REVIEW_SYNTHESIS_STATUS_COLUMNS,
    ).to_csv(
        research_context_dir / _PNO_STAGE5_REVIEW_SYNTHESIS_STATUS_FILE_NAME,
        index=False,
    )
    research_rejections_by_stage = _build_filtered_pno_stage_rejections(
        stage_rejections_by_stage=stage_rejections_by_stage,
        selected_stage_ids=selected_stage_ids,
    )
    _write_pno_diagnostics_coverage(diagnostics_dir=diagnostics_dir, rows=coverage_rows)
    _write_pno_seconds_and_cache_status_tables(
        diagnostics_dir=diagnostics_dir,
        seconds_rows=seconds_load_status_rows,
        sparse_materialization_rows=sparse_materialization_status_rows,
        stage1_cache_rows=stage1_cache_status_rows,
    )
    _export_pno_research_context(
        diagnostics_dir=diagnostics_dir,
        symbol_frames=symbol_frames,
        position_rows=all_position_rows,
        stage_rows_by_stage=stage_rows_by_stage,
        stage_rejections_by_stage=research_rejections_by_stage,
        stage_rejection_summary_by_stage=stage_rejections_by_stage,
        seconds_frame_provider=getattr(strategy, "_seconds_provider", None),
        logger=None,
    )
    _export_pno_stage_reviews(
        diagnostics_dir=diagnostics_dir,
        symbol_frames=symbol_frames,
        stage_rows_by_stage=stage_rows_by_stage,
        stage_rejections_by_stage=stage_rejections_by_stage,
        selected_stage_ids=selected_stage_ids,
        render_charts=False,
        passed_chart_stage_ids=passed_chart_stage_ids,
        logger=logger,
    )
    return {
        "all_position_rows": all_position_rows,
        "diagnostics_payloads": diagnostics_payloads,
        "stage_rows_by_stage": stage_rows_by_stage,
        "stage_rejections_by_stage": stage_rejections_by_stage,
        "selected_stage_ids": selected_stage_ids,
        "passed_chart_stage_ids": passed_chart_stage_ids,
        "symbols_with_positions": symbols_with_positions,
        "symbols_with_stage_events": symbols_with_stage_events,
        "total_positions_generated": total_positions_generated,
        "total_stage_events": total_stage_events,
    }


def _plot_for_strategy_dispatch(
    *,
    config: AppConfig,
    args: argparse.Namespace,
    logger: Logger,
    strategy_id: str,
    strategy: object,
    symbol_frames: dict[str, SymbolMtfFrames],
    params_row: pd.Series,
    levels_timeframe: Timeframe,
    entry_timeframe: Timeframe,
    log_prefix: str,
    diagnostics_cache: BacktestGenerationDiagnosticsCache | None = None,
) -> bool:
    if strategy_id != "pno" or not isinstance(strategy, PnoStrategy):
        logger.error("Визуализация для стратегии %s не поддерживается", strategy_id)
        return False
    _plot_pno_diagnostics_for_symbols(
        config=config,
        args=args,
        logger=logger,
        strategy=strategy,
        symbol_frames=symbol_frames,
        params_row=params_row,
        levels_timeframe=levels_timeframe,
        entry_timeframe=entry_timeframe,
        log_prefix=log_prefix,
        diagnostics_cache=diagnostics_cache,
    )
    return True


def _select_pno_plot_params_row_by_stage(
    *,
    args: argparse.Namespace,
    strategy: PnoStrategy,
    symbol_frames: dict[str, SymbolMtfFrames],
    results: pd.DataFrame,
    levels_timeframe: Timeframe,
    entry_timeframe: Timeframe,
    logger: Logger,
) -> pd.Series | None:
    if results.empty or not symbol_frames:
        return None

    selected_stage_ids = set(_resolve_pno_stage_ids(args))
    selected_stage_columns = [_pno_stage_metric_column_name(stage_id) for stage_id in selected_stage_ids]
    if selected_stage_columns and all(column in results.columns for column in selected_stage_columns):
        scored_rows: list[tuple[int, int, int, float, int, pd.Series]] = []
        needs_diagnostic_rescore = False
        for row_index, (_, row) in enumerate(results.iterrows()):
            stage_events_count = 0
            for column in selected_stage_columns:
                raw_value = pd.to_numeric(row.get(column, 0), errors="coerce")
                if not pd.isna(raw_value):
                    stage_events_count += int(raw_value)
            raw_positions_count = pd.to_numeric(row.get("positions_count", 0), errors="coerce")
            positions_generated = int(raw_positions_count) if not pd.isna(raw_positions_count) else 0
            profit_factor = float(row.get("profit_factor", 0.0) or 0.0)
            scored_rows.append((stage_events_count, 0, positions_generated, profit_factor, -row_index, row))
            if stage_events_count == 0:
                needs_diagnostic_rescore = True

        if not scored_rows:
            return None

        best_score = max(scored_rows, key=lambda item: item[:5])
        if best_score[0] > 0 or not needs_diagnostic_rescore:
            logger.debug(
                "Строка для графиков выбрана из результатов; событий %s, отказов %s, позиций %s, профит-фактор %.4f.",
                best_score[0],
                best_score[1],
                best_score[2],
                best_score[3],
            )
            return best_score[5]

    scored_rows: list[tuple[int, int, int, float, int, pd.Series]] = []
    for row_index, (_, row) in enumerate(results.iterrows()):
        stage_events_count = 0
        stage_rejections_count = 0
        positions_generated = 0

        pno_params_template = _build_pno_params_template_from_row(
            row,
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
        )
        for symbol, mtf_frames in symbol_frames.items():
            params = replace(pno_params_template, symbol=symbol)
            strategy.generate_events_multi_tf(mtf_frames=mtf_frames, params=params)
            diagnostics = strategy.consume_last_generation_diagnostics()

            stage_events = diagnostics.get("stage_events", [])
            if isinstance(stage_events, list):
                stage_events_count += sum(
                    1
                    for event in stage_events
                    if isinstance(event, dict) and event.get("stage_id") in selected_stage_ids
                )
            stage_rejections = diagnostics.get("stage_rejections", [])
            if isinstance(stage_rejections, list):
                stage_rejections_count += sum(
                    1
                    for rejection in stage_rejections
                    if isinstance(rejection, dict) and rejection.get("stage_id") in selected_stage_ids
                )
            positions_generated += int(diagnostics.get("positions_generated", 0) or 0)

        profit_factor = float(row.get("profit_factor", 0.0) or 0.0)
        scored_rows.append((stage_events_count, stage_rejections_count, positions_generated, profit_factor, -row_index, row))

    if not scored_rows:
        return None

    best_score = max(scored_rows, key=lambda item: item[:5])
    logger.debug(
        "Строка для графиков выбрана по диагностике; событий %s, отказов %s, позиций %s, профит-фактор %.4f.",
        best_score[0],
        best_score[1],
        best_score[2],
        best_score[3],
    )
    return best_score[5]


def _load_plot_params_row_from_results(
    config: AppConfig,
    args: argparse.Namespace,
    *,
    logger: Logger,
    strategy_id: str,
) -> pd.Series | None:
    explicit_csv_path = getattr(args, "results_input", None) or getattr(args, "input", None)
    if explicit_csv_path is None:
        logger.error("--plot-from-results требует явный --results-input или сохранённый plot-backtest run_context")
        return None
    csv_path = Path(explicit_csv_path)

    if not csv_path.exists():
        logger.error("Файл результатов не найден: %s", csv_path)
        return None

    frame = pd.read_csv(csv_path)
    if frame.empty:
        logger.error("Файл результатов пустой: %s", csv_path)
        return None

    if strategy_id != "pno":
        logger.error("Стратегия %s не поддерживается", strategy_id)
        return None
    missing_columns = [column for column in _PNO_REQUIRED_RESULTS_ROW_COLUMNS if column not in frame.columns]
    if missing_columns:
        logger.error("results_row_missing_required_pno_fields: %s", ",".join(missing_columns))
        return None

    selected_row_number_raw = getattr(args, "row_number", None)
    if selected_row_number_raw is not None:
        row_number = int(selected_row_number_raw)
        row_index = row_number - 1
        if row_index < 0 or row_index >= len(frame):
            logger.error(
                "Номер строки %s вне диапазона 1..%s",
                row_number,
                len(frame),
            )
            return None
        selected_row = frame.iloc[row_index]
        logger.debug(
            "Использована строка %s из %s: профит-фактор %s, позиций %s.",
            row_number,
            csv_path,
            selected_row.get("profit_factor", "n/a"),
            selected_row.get("positions_count", "n/a"),
        )
        return selected_row

    selected_id_raw = getattr(args, "id", None)
    selected_row: pd.Series
    if selected_id_raw is not None:
        selected_id = int(selected_id_raw)
        id_columns = ("id", "combination_id", "rank")
        matched_by_column: pd.DataFrame | None = None
        for column in id_columns:
            if column not in frame.columns:
                continue
            numeric_column = pd.Series(pd.to_numeric(frame[column], errors="coerce"), index=frame.index)
            match_mask = numeric_column.eq(selected_id)
            matches: pd.DataFrame = frame.loc[match_mask].copy()
            if not matches.empty:
                matched_by_column = matches
                logger.debug(
                    "Комбинация найдена по колонке %s: id %s, совпадений %s.",
                    column,
                    selected_id,
                    len(matches),
                )
                break

        if matched_by_column is not None:
            selected_row = matched_by_column.iloc[0]
        else:
            logger.error("ID %s не найден в колонках id/combination_id/rank файла %s", selected_id, csv_path)
            return None
    else:
        sorted_frame = frame.sort_values(["profit_factor", "positions_count"], ascending=[False, False], na_position="last")
        selected_row = sorted_frame.iloc[0]

    logger.debug(
        "Параметры взяты из %s: профит-фактор %s, позиций %s, id %s.",
        csv_path,
        selected_row.get("profit_factor", "n/a"),
        selected_row.get("positions_count", "n/a"),
        selected_id_raw if selected_id_raw is not None else "best",
    )
    return selected_row

def _run_with_logging(command_name: str, config: AppConfig, body: Callable[[], int]) -> int:
    logger = get_logger(
        command_name,
        level=config.backtest.log_level,
        logs_dir=config.backtest.logs_dir,
    )
    logger.debug("Команда запущена: %s", command_name)
    try:
        code = body()
        logger.debug("Команда завершена: %s, код %s", command_name, code)
        return code
    except Exception as exc:
        logger.exception("Ошибка: %s", exc)
        return 1


def _build_fetch_stack(config: AppConfig) -> tuple[MarketDataFetcher, CcxtFuturesClient]:
    exchange_client = CcxtFuturesClient(
        exchange=Exchange.BINANCE,
        api_key=config.fetch.binance_api_key,
        secret=config.fetch.binance_secret_key,
        retry_attempts=config.backtest.retry_attempts,
        retry_backoff_seconds=config.backtest.retry_backoff_seconds,
    )
    storage = ParquetStorage(
        base_dir=config.backtest.cache_dir,
        log_level=config.backtest.log_level,
        logs_dir=config.backtest.logs_dir,
    )
    ohlcv_fetcher = OhlcvFetcher(
        exchange_client=exchange_client,
        storage=storage,
        retry_attempts=config.backtest.retry_attempts,
        retry_backoff_seconds=config.backtest.retry_backoff_seconds,
        log_level=config.backtest.log_level,
        logs_dir=config.backtest.logs_dir,
    )
    oi_fetcher = OiFetcher(
        exchange_client=exchange_client,
        storage=storage,
        retry_attempts=config.backtest.retry_attempts,
        retry_backoff_seconds=config.backtest.retry_backoff_seconds,
        log_level=config.backtest.log_level,
        logs_dir=config.backtest.logs_dir,
    )
    return (
        MarketDataFetcher(
            ohlcv_fetcher=ohlcv_fetcher,
            oi_fetcher=oi_fetcher,
            market_data_client=NoOpMarketDataClient(),
            retry_attempts=config.backtest.retry_attempts,
            retry_backoff_seconds=config.backtest.retry_backoff_seconds,
            log_level=config.backtest.log_level,
            logs_dir=config.backtest.logs_dir,
        ),
        exchange_client,
    )


def _resolve_symbols(
        exchange_client: CcxtFuturesClient,
        top_n: int,
        min_volume_usd: float,
        logger: Logger,
        cache_dir: Path,
        liquidity_timeframe: Timeframe,
        futures_symbols_raw: list[str] | None = None,
) -> tuple[list[str], dict[str, dict[str, object]]]:
    futures_symbols_raw = futures_symbols_raw or exchange_client.get_futures_symbols()
    liquidity_quality_by_symbol: dict[str, dict[str, object]] = {}
    futures_symbol_map = _build_futures_symbol_map(futures_symbols_raw)
    exchange_symbols_normalized = sorted(futures_symbol_map)

    ranker = DailyVolumeRanker(cache_dir=cache_dir)
    symbols_raw = [futures_symbol_map[symbol] for symbol in exchange_symbols_normalized]
    avg_daily_volumes = ranker.calculate_avg_daily_volume_usd(
        symbols=symbols_raw,
        timeframe=liquidity_timeframe,
    )
    avg_daily_volumes_normalized = {
        normalize_symbol(raw_symbol): volume
        for raw_symbol, volume in avg_daily_volumes.items()
    }
    symbols_with_volume = [
        symbol
        for symbol in exchange_symbols_normalized
        if symbol in avg_daily_volumes_normalized
    ]

    liquid_symbols = [
        symbol
        for symbol in symbols_with_volume
        if avg_daily_volumes_normalized.get(symbol, 0.0) >= min_volume_usd
    ]
    combined_volume_score_by_symbol = {
        symbol: avg_daily_volumes_normalized[symbol]
        for symbol in liquid_symbols
    }
    liquidity_score_by_symbol: dict[str, float] = {}
    exchange_liquid_symbols: set[str] = set()

    try:
        ranked_metrics = exchange_client.get_futures_symbols_with_liquidity_metrics()
        for item in ranked_metrics:
            symbol_raw = str(item.get('symbol', ''))
            symbol = normalize_symbol(symbol_raw)
            if symbol not in futures_symbol_map:
                continue

            quote_volume = float(item.get('quote_volume', 0.0) or 0.0)
            liquidity_score = float(item.get('liquidity_score', 0.0) or 0.0)
            liquidity_score_by_symbol[symbol] = liquidity_score
            liquidity_quality_by_symbol[symbol_raw] = {
                'liquidity_score': liquidity_score,
                'quote_volume': quote_volume,
                'quote_volume_source': str(item.get('quote_volume_source', 'unknown')),
                'quote_volume_proxy': float(item.get('quote_volume_proxy', 0.0) or 0.0),
                'trade_count_24h': int(item.get('trade_count_24h', 0) or 0),
                'quality_flags': list(cast(list[object], item.get('quality_flags', []))),
                'quality_metadata': dict(cast(dict[str, object], item.get('quality_metadata', {}))),
            }

            if quote_volume >= min_volume_usd:
                exchange_liquid_symbols.add(symbol)
                combined_volume_score_by_symbol[symbol] = max(
                    combined_volume_score_by_symbol.get(symbol, 0.0),
                    quote_volume,
                )
    except Exception as exc:
        logger.warning(
            "Не удалось получить метрики ликвидности с биржи: %s",
            exc,
        )

    combined_symbols = set(liquid_symbols) | exchange_liquid_symbols
    if not combined_symbols:
        logger.warning(
            "Ликвидные символы не найдены; universe_selection_failed, произвольный fallback по первым символам отключён.",
        )
        liquidity_quality_by_symbol["__universe_selection__"] = {
            "liquidity_score": 0.0,
            "quote_volume": 0.0,
            "quote_volume_source": "none",
            "quote_volume_proxy": 0.0,
            "trade_count_24h": 0,
            "quality_flags": ["universe_selection_failed"],
            "quality_metadata": {
                "exchange_symbols_count": len(exchange_symbols_normalized),
                "cache_symbols_with_volume": len(symbols_with_volume),
                "cache_liquid_symbols": len(liquid_symbols),
                "exchange_liquid_symbols": len(exchange_liquid_symbols),
                "min_volume_usd": float(min_volume_usd),
            },
        }
        return [], liquidity_quality_by_symbol

    ranked_top_symbols = sorted(
        combined_symbols,
        key=lambda symbol: (
            combined_volume_score_by_symbol.get(symbol, 0.0),
            liquidity_score_by_symbol.get(symbol, 0.0),
            symbol,
        ),
        reverse=True,
    )[:top_n]

    logger.debug(
        "Отбор ликвидности: биржа %s, кэш %s, порог прошли %s, выбрано %s.",
        len(exchange_symbols_normalized),
        len(symbols_with_volume),
        len(liquid_symbols),
        len(ranked_top_symbols),
    )
    return [futures_symbol_map[symbol] for symbol in ranked_top_symbols], liquidity_quality_by_symbol


def _resolve_explicit_symbols(
    *,
    requested_symbols: list[str] | None,
    futures_symbols_raw: list[str],
    logger: Logger,
) -> list[str] | None:
    if not requested_symbols:
        return None

    futures_symbol_map = _build_futures_symbol_map(futures_symbols_raw)
    resolved: list[str] = []
    missing: list[str] = []
    seen: set[str] = set()
    for raw_symbol in requested_symbols:
        normalized = normalize_symbol(raw_symbol)
        resolved_symbol = futures_symbol_map.get(normalized)
        if resolved_symbol is None:
            missing.append(str(raw_symbol))
            continue
        if resolved_symbol in seen:
            continue
        seen.add(resolved_symbol)
        resolved.append(resolved_symbol)

    if missing:
        logger.warning("Пропущены неизвестные символы: %s", ",".join(missing))
    logger.debug("Явный список символов: запрошено %s, найдено %s.", len(requested_symbols), len(resolved))
    return resolved

def _resolve_fetch_anchor_timestamp_ms(config: AppConfig, end_timestamp_ms_raw: int | None) -> int:
    if end_timestamp_ms_raw is not None:
        return int(end_timestamp_ms_raw)

    if config.fetch.anchor_timestamp_ms is not None:
        return int(config.fetch.anchor_timestamp_ms)

    return int(time.time() * 1000)


def _fetch_period(config: AppConfig, days: int, end_timestamp_ms_raw: int | None = None) -> tuple[int, int]:
    if days <= 0:
        raise ValueError("--days must be > 0")
    end_timestamp_ms = _resolve_fetch_anchor_timestamp_ms(config, end_timestamp_ms_raw)
    start_timestamp_ms = end_timestamp_ms - (days * 86_400_000)
    return start_timestamp_ms, end_timestamp_ms


@dataclass(frozen=True, slots=True)
class FetchSummary:
    total_symbols: int
    success_symbols: int
    failed_symbols: int

    @property
    def failed_ratio(self) -> float:
        return (self.failed_symbols / self.total_symbols) if self.total_symbols else 0.0


def _log_fetch_summary(
    logger: Logger,
    total_symbols: int,
    failed_symbols_count: int,
    *,
    emit_log: bool = True,
) -> FetchSummary:
    summary = FetchSummary(
        total_symbols=total_symbols,
        success_symbols=total_symbols - failed_symbols_count,
        failed_symbols=failed_symbols_count,
    )
    if emit_log:
        logger.info(
            "Загрузка завершена: %s из %s, ошибок %s.",
            summary.success_symbols,
            summary.total_symbols,
            summary.failed_symbols,
        )
    return summary


def _fetch_exit_code(failed_symbols_count: int, critical_fail_threshold: int = 1) -> int:
    return 1 if failed_symbols_count >= critical_fail_threshold else 0


def _resolve_liquidity_skip_reason(summary: FetchSummary | None, threshold: float) -> str | None:
    if summary is None:
        return "no_ohlcv_cache_data"
    if summary.success_symbols == 0:
        return "no_ohlcv_cache_data"
    if summary.failed_ratio > threshold:
        return "ohlcv_error_ratio_above_threshold"
    return None


def _log_loaded_coins(logger: Logger, count: int, action: str) -> None:
    templates = {
        "loaded": "Загрузка данных завершена\nСимволов: %s",
        "updated": "Обновление кэша завершено\nСимволов: %s",
    }
    template = templates.get(action)
    if template is None:
        raise ValueError(f"Неподдерживаемое действие: {action}")
    logger.info(template, count)


def _attach_liquidity_quality_metadata(
    results: dict[str, SymbolFetchResult],
    liquidity_quality_by_symbol: dict[str, dict[str, object]],
) -> dict[str, SymbolFetchResult]:
    if not liquidity_quality_by_symbol:
        return results

    enriched: dict[str, SymbolFetchResult] = {}
    for symbol, symbol_result in results.items():
        metadata = liquidity_quality_by_symbol.get(symbol, {})
        if not metadata:
            enriched[symbol] = symbol_result
            continue
        enriched[symbol] = SymbolFetchResult(
            success=symbol_result.success,
            message=symbol_result.message,
            added_rows=symbol_result.added_rows,
            quality_metadata=dict(metadata),
        )
    return enriched


def _resolve_timeframe(value: str | None, *, fallback: Timeframe, argument_name: str) -> Timeframe:
    if value is None:
        return fallback

    normalized = value.strip().lower()
    for timeframe in Timeframe:
        if timeframe.value == normalized:
            return timeframe

    supported = ", ".join(tf.value for tf in Timeframe)
    raise ValueError(f"Некорректное значение {argument_name}: {value}. Поддерживаемые значения: {supported}")


def _resolve_timeframe_sequence(
    raw_values: list[str] | tuple[str, ...] | None,
    *,
    fallback: tuple[Timeframe, ...],
    fallback_timeframe: Timeframe,
    argument_name: str,
    supported: set[Timeframe] | None = None,
) -> tuple[Timeframe, ...]:
    if not raw_values:
        return fallback

    resolved: list[Timeframe] = []
    seen: set[Timeframe] = set()
    for raw_value in raw_values:
        timeframe = _resolve_timeframe(
            str(raw_value),
            fallback=fallback_timeframe,
            argument_name=argument_name,
        )
        if supported is not None and timeframe not in supported:
            supported_values = ", ".join(tf.value for tf in fallback)
            raise ValueError(
                f"{argument_name} supports only timeframes "
                f"{{{supported_values}}}, got {timeframe.value}"
            )
        if timeframe in seen:
            continue
        seen.add(timeframe)
        resolved.append(timeframe)
    if not resolved:
        return fallback
    return tuple(resolved)


def _resolve_fetch_timeframes(args: argparse.Namespace, fallback: tuple[Timeframe, ...]) -> tuple[Timeframe, ...]:
    return _resolve_timeframe_sequence(
        getattr(args, "timeframes", None),
        fallback=fallback,
        fallback_timeframe=Timeframe.M5,
        argument_name="--timeframes",
    )


def _resolve_backtest_timeframes(
    *,
    strategy_id: str,
    args: argparse.Namespace,
    configured_levels_timeframe: Timeframe,
    configured_entry_timeframe: Timeframe,
) -> tuple[Timeframe, Timeframe]:
    if strategy_id != "pno":
        raise ValueError(f"Unsupported strategy for backtest timeframe resolution: {strategy_id}")

    fallback_pair = (
        configured_levels_timeframe,
        configured_entry_timeframe,
    )
    if fallback_pair not in PNO_BACKTEST_TIMEFRAME_PAIRS:
        raise ValueError(
            "configured_timeframe_pair_invalid: "
            f"levels_tf={configured_levels_timeframe.value}, entry_tf={configured_entry_timeframe.value}"
        )
    entry_timeframe = _resolve_timeframe(
        getattr(args, "entry_tf", None),
        fallback=fallback_pair[1],
        argument_name="--entry-tf",
    )
    levels_timeframe = _resolve_timeframe(
        getattr(args, "levels_tf", None),
        fallback=fallback_pair[0],
        argument_name="--levels-tf",
    )
    validate_pno_timeframe_pair(
        levels_timeframe=levels_timeframe,
        entry_timeframe=entry_timeframe,
        supported_pairs=PNO_BACKTEST_TIMEFRAME_PAIRS,
        context="pno backtest",
    )
    return levels_timeframe, entry_timeframe


def _with_pno_stage_args(
    args: argparse.Namespace,
    *,
    preset_stage: int | None,
    preset_through_stage: int | None,
) -> argparse.Namespace:
    cloned = argparse.Namespace(**vars(args))
    default_levels_timeframe, default_entry_timeframe = resolve_pno_default_timeframe_pair(mode="backtest")
    cloned.command = "run-backtest"
    cloned.strategy = "pno"
    cloned.entry_tf = getattr(cloned, "entry_tf", None) or default_entry_timeframe.value
    cloned.levels_tf = getattr(cloned, "levels_tf", None) or default_levels_timeframe.value
    cloned.plot = True
    cloned.plot_from_results = False
    cloned.results_input = None
    cloned.id = None
    cloned.pno_stage = preset_stage
    cloned.pno_through_stage = preset_through_stage
    return cloned


def _fetch_data_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("fetch-data", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    fetch_timeframes = _resolve_fetch_timeframes(args, config.fetch.timeframes)

    if args.top_n is not None and args.top_n <= 0:
        logger.error("--top-n должен быть > 0")
        return 1
    if args.days <= 0:
        logger.error("--days должен быть > 0")
        return 1

    fetcher, exchange_client = _build_fetch_stack(config)
    futures_symbols = exchange_client.get_futures_symbols()
    all_futures_count = len(futures_symbols)
    explicit_symbols = _resolve_explicit_symbols(
        requested_symbols=getattr(args, "symbols", None),
        futures_symbols_raw=futures_symbols,
        logger=logger,
    )
    min_volume_usd = args.min_volume_usd if args.min_volume_usd is not None else config.fetch.min_volume_usd
    top_n = args.top_n if args.top_n is not None else all_futures_count
    if explicit_symbols is None:
        symbols, liquidity_quality_by_symbol = _resolve_symbols(
            exchange_client,
            top_n=top_n,
            min_volume_usd=min_volume_usd,
            logger=logger,
            cache_dir=config.backtest.cache_dir,
            liquidity_timeframe=config.fetch.timeframe,
            futures_symbols_raw=futures_symbols,
        )
    else:
        symbols = explicit_symbols
        liquidity_quality_by_symbol = {}
    logger.warning("Загрузка данных")
    logger.info("Отобрано %s символов", len(symbols))
    logger.debug("Фьючерсов на бирже: %s.", all_futures_count)
    if not symbols:
        liquidity_quality_by_symbol = {}
        logger.warning("Нет символов для загрузки")
        return 0

    start_timestamp_ms, end_timestamp_ms = _fetch_period(config, args.days, getattr(args, "end_timestamp_ms", None))
    include_open_interest = not _to_bool_flag(getattr(args, "skip_open_interest", False))
    failed_symbols: set[str] = set()
    fetch_summaries: dict[Timeframe, FetchSummary] = {}

    def _fetch_for_timeframe(
        requested_timeframe: Timeframe,
        symbols_to_fetch: list[str],
        *,
        emit_log: bool = True,
    ) -> None:
        logger.warning("Таймфрейм: %s", requested_timeframe.value)
        result = fetcher.fetch_all(
            symbols=symbols_to_fetch,
            timeframe=requested_timeframe,
            start_timestamp_ms=start_timestamp_ms,
            end_timestamp_ms=end_timestamp_ms,
            include_open_interest=include_open_interest,
        )
        enriched_ohlcv = _attach_liquidity_quality_metadata(result.ohlcv, liquidity_quality_by_symbol)
        result.ohlcv.clear()
        result.ohlcv.update(enriched_ohlcv)
        enriched_open_interest = _attach_liquidity_quality_metadata(result.open_interest, liquidity_quality_by_symbol)
        result.open_interest.clear()
        result.open_interest.update(enriched_open_interest)

        fetch_summaries[requested_timeframe] = _log_fetch_summary(
            logger,
            len(symbols_to_fetch),
            result.failed_symbols_count,
            emit_log=emit_log,
        )
        failed_symbols.update(
            symbol
            for symbol in symbols_to_fetch
            if (symbol in result.ohlcv and not result.ohlcv[symbol].success)
            or (symbol in result.open_interest and not result.open_interest[symbol].success)
            or isinstance(result.market_caps.market_caps.get(symbol), str)
        )

    primary_timeframe = fetch_timeframes[0]
    _fetch_for_timeframe(primary_timeframe, symbols, emit_log=True)

    root_stage_status = "ok"
    followup_symbols = symbols
    if explicit_symbols is None:
        liquidity_summary = fetch_summaries.get(primary_timeframe)
        skip_reason = _resolve_liquidity_skip_reason(
            liquidity_summary,
            config.fetch.liquidity_skip_error_ratio_threshold,
        )
        if skip_reason is None:
            followup_symbols, liquidity_quality_by_symbol = _resolve_symbols(
                exchange_client,
                top_n=top_n,
                min_volume_usd=min_volume_usd,
                logger=logger,
                cache_dir=config.backtest.cache_dir,
                liquidity_timeframe=config.fetch.timeframe,
                futures_symbols_raw=futures_symbols,
            )
            logger.debug("Список ликвидных символов пересчитан: %s.", len(followup_symbols))
        else:
            root_stage_status = "ohlcv_cache_failed"
            logger.warning("Отбор ликвидности пропущен для %s", primary_timeframe.value)
            logger.debug("Причина пропуска отбора ликвидности: %s", skip_reason)

    for timeframe in fetch_timeframes:
        if timeframe == primary_timeframe:
            continue
        _fetch_for_timeframe(timeframe, followup_symbols, emit_log=True)

    exit_code = _fetch_exit_code(len(failed_symbols))
    if root_stage_status == "ohlcv_cache_failed":
        exit_code = 2

    logger.debug("Загрузка данных завершилась с кодом %s.", exit_code)
    _log_loaded_coins(logger, len(followup_symbols), "loaded")
    return exit_code


def _resolve_fetch_symbol_list(
    *,
    config: AppConfig,
    args: argparse.Namespace,
    logger: Logger,
) -> list[str]:
    _, exchange_client = _build_fetch_stack(config)
    futures_symbols = exchange_client.get_futures_symbols()
    explicit_symbols = _resolve_explicit_symbols(
        requested_symbols=getattr(args, "symbols", None),
        futures_symbols_raw=futures_symbols,
        logger=logger,
    )
    if explicit_symbols is not None:
        return explicit_symbols

    all_futures_count = len(futures_symbols)
    min_volume_usd = args.min_volume_usd if args.min_volume_usd is not None else config.fetch.min_volume_usd
    top_n = args.top_n if args.top_n is not None else all_futures_count
    symbols, _ = _resolve_symbols(
        exchange_client,
        top_n=top_n,
        min_volume_usd=min_volume_usd,
        logger=logger,
        cache_dir=config.backtest.cache_dir,
        liquidity_timeframe=config.fetch.timeframe,
        futures_symbols_raw=futures_symbols,
    )
    return symbols


def _clone_fetch_args(
    args: argparse.Namespace,
    *,
    symbols: list[str],
    timeframes: list[str],
    skip_open_interest: bool,
) -> argparse.Namespace:
    cloned = argparse.Namespace(**vars(args))
    cloned.symbols = list(symbols)
    cloned.timeframes = list(timeframes)
    cloned.skip_open_interest = skip_open_interest
    return cloned


def _update_cache_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("update-cache", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    fetch_timeframes = _resolve_fetch_timeframes(args, config.fetch.timeframes)

    if args.top_n is not None and args.top_n <= 0:
        logger.error("--top-n должен быть > 0")
        return 1
    if args.days <= 0:
        logger.error("--days должен быть > 0")
        return 1

    fetcher, exchange_client = _build_fetch_stack(config)
    futures_symbols = exchange_client.get_futures_symbols()
    all_futures_count = len(futures_symbols)
    explicit_symbols = _resolve_explicit_symbols(
        requested_symbols=getattr(args, "symbols", None),
        futures_symbols_raw=futures_symbols,
        logger=logger,
    )
    min_volume_usd = args.min_volume_usd if args.min_volume_usd is not None else config.fetch.min_volume_usd
    top_n = args.top_n if args.top_n is not None else all_futures_count
    if explicit_symbols is None:
        symbols, liquidity_quality_by_symbol = _resolve_symbols(
            exchange_client,
            top_n=top_n,
            min_volume_usd=min_volume_usd,
            logger=logger,
            cache_dir=config.backtest.cache_dir,
            liquidity_timeframe=config.fetch.timeframe,
            futures_symbols_raw=futures_symbols,
        )
    else:
        symbols = explicit_symbols
        liquidity_quality_by_symbol = {}
    logger.warning("Обновление кэша")
    logger.info("Отобрано %s символов", len(symbols))
    logger.debug("Фьючерсов на бирже: %s.", all_futures_count)
    if not symbols:
        logger.warning("Нет символов для обновления")
        return 0

    start_timestamp_ms, end_timestamp_ms = _fetch_period(config, args.days, getattr(args, "end_timestamp_ms", None))
    include_open_interest = not _to_bool_flag(getattr(args, "skip_open_interest", False))
    failed_symbols: set[str] = set()
    for index, timeframe in enumerate(fetch_timeframes):
        logger.warning("Таймфрейм: %s", timeframe.value)
        result = fetcher.fetch_all(
            symbols=symbols,
            timeframe=timeframe,
            start_timestamp_ms=start_timestamp_ms,
            end_timestamp_ms=end_timestamp_ms,
            include_open_interest=include_open_interest,
        )
        enriched_ohlcv = _attach_liquidity_quality_metadata(result.ohlcv, liquidity_quality_by_symbol)
        result.ohlcv.clear()
        result.ohlcv.update(enriched_ohlcv)
        enriched_open_interest = _attach_liquidity_quality_metadata(result.open_interest, liquidity_quality_by_symbol)
        result.open_interest.clear()
        result.open_interest.update(enriched_open_interest)
        _log_fetch_summary(logger, len(symbols), result.failed_symbols_count)
        failed_symbols.update(
            symbol
            for symbol in symbols
            if (symbol in result.ohlcv and not result.ohlcv[symbol].success)
            or (symbol in result.open_interest and not result.open_interest[symbol].success)
            or isinstance(result.market_caps.market_caps.get(symbol), str)
        )
        if index < len(fetch_timeframes) - 1:
            logger.warning("Пауза перед следующим таймфреймом: 75с")
            time.sleep(75)

    exit_code = _fetch_exit_code(len(failed_symbols))
    _log_loaded_coins(logger, len(symbols), "updated")
    return exit_code


def _run_backtest_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("run-backtest", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    strategy_id = (
        _resolve_strategy_id(args)
        if getattr(args, "strategy", None) is not None
        else str(config.strategy.strategy_id).strip().lower()
    )
    config.strategy.strategy_id = strategy_id
    config.backtest.results_dir = _resolve_results_dir_for_strategy(config.backtest.results_dir, strategy_id)
    run_root_dir_raw = getattr(args, "backtest_run_root_dir", None)
    run_root_dir = Path(run_root_dir_raw) if run_root_dir_raw is not None else None
    if getattr(args, "pno_deposit", None) is not None:
        config.strategy.pno_deposit = float(args.pno_deposit)
    if getattr(args, "pno_risk_pct", None) is not None:
        config.strategy.pno_risk_pct = float(args.pno_risk_pct)
    if getattr(args, "pno_entry_confirmation_mode", None) is not None:
        config.strategy.pno_entry_confirmation_mode = str(args.pno_entry_confirmation_mode)
    if getattr(args, "pno_category_mode", None) is not None:
        config.strategy.pno_category_mode = str(args.pno_category_mode)
    levels_timeframe, entry_timeframe = _resolve_backtest_timeframes(
        strategy_id=strategy_id,
        args=args,
        configured_levels_timeframe=config.strategy.levels_timeframe,
        configured_entry_timeframe=config.strategy.entry_timeframe,
    )
    logger.debug(
        "Бэктест: уровни %s, входы %s.",
        levels_timeframe.value,
        entry_timeframe.value,
    )

    preparer = DataPreparer(config.backtest.cache_dir)
    backtest_days = getattr(args, "days", None)
    backtest_end_timestamp_ms = getattr(args, "end_timestamp_ms", None)
    pno_seconds_entry_pair = (
        strategy_id == "pno"
        and int(entry_timeframe.to_milliseconds()) < int(Timeframe.M1.to_milliseconds())
    )
    source_entry_timeframe = Timeframe.M1 if pno_seconds_entry_pair else entry_timeframe
    entry_load_mode = "sparse_deferred" if pno_seconds_entry_pair else "source_frame"
    entry_target_checked_in_data_load = not pno_seconds_entry_pair
    symbols = args.symbols or preparer.list_symbols(source_entry_timeframe)
    if not symbols:
        logger.warning("Нет данных в кэше")
        return 0
    if backtest_days is not None:
        logger.debug(
            "Окно бэктеста: %s дней, конец %s.",
            backtest_days,
            backtest_end_timestamp_ms if backtest_end_timestamp_ms is not None else "по кэшу",
        )
    _warn_if_pno_backtest_window_too_short(
        strategy_id=strategy_id,
        backtest_days=backtest_days,
        levels_timeframe=levels_timeframe,
        logger=logger,
    )

    should_plot = _resolve_plot_rejected_flag(args)
    collect_diagnostics = _resolve_collect_diagnostics_flag(args)
    strategy = build_strategy(config, logger)
    pno_fast_prefilter_active = (
        not collect_diagnostics
        and not should_plot
        and strategy_id == "pno"
        and isinstance(strategy, PnoStrategy)
    )
    symbols_before_ranking = len(symbols)
    top_n = getattr(args, "top_n", None)
    pre_rank_enabled = top_n is not None and top_n > 0
    ranked_symbols: list[tuple[str, float]] = []
    rejected_symbols_count = 0
    preloaded_levels_results: dict[str, SymbolDataLoadResult] = {}
    data_load_rejections: Counter[str] = Counter()
    data_load_status_rows: list[dict[str, object]] = []
    pre_rank_started_at = time.perf_counter()
    if pre_rank_enabled:
        for symbol in symbols:
            levels_result = preparer.load_symbol_data_result(
                symbol,
                levels_timeframe,
                days=backtest_days,
                end_timestamp_ms=backtest_end_timestamp_ms,
            )
            preloaded_levels_results[symbol] = levels_result
            data_load_status_rows.append(
                _build_data_load_status_row(
                    levels_result,
                    role="pre_rank_levels",
                    window_days=backtest_days,
                    end_timestamp_ms=backtest_end_timestamp_ms,
                )
            )
            levels_frame = levels_result.frame
            if not levels_result.ok:
                data_load_rejections[f"pre_rank_levels:{levels_result.status}"] += 1
                logger.debug(
                    "Символ %s исключён из предварительного отбора: %s %s (%s).",
                    symbol,
                    levels_timeframe.value,
                    levels_result.status,
                    levels_result.reason,
                )
                rejected_symbols_count += 1
                continue
            if "volume" not in levels_frame.columns:
                logger.debug(
                    "Символ %s исключён из предварительного отбора: нет объёма на %s.",
                    symbol,
                    levels_timeframe.value,
                )
                rejected_symbols_count += 1
                continue

            volume_numeric = pd.Series(pd.to_numeric(levels_frame["volume"], errors="coerce"), index=levels_frame.index)
            volume_series = volume_numeric.dropna()
            if volume_series.empty:
                logger.debug(
                    "Символ %s исключён из предварительного отбора: объём на %s невалиден.",
                    symbol,
                    levels_timeframe.value,
                )
                rejected_symbols_count += 1
                continue

            ranked_symbols.append((symbol, float(volume_series.mean())))

        ranked_symbols.sort(key=lambda item: item[1], reverse=True)
        ranked_symbols_count = len(ranked_symbols)
        selected_ranked_symbols = ranked_symbols[:top_n]
        symbols = [symbol for symbol, _ in selected_ranked_symbols]

        preview = selected_ranked_symbols[:10]
        top_preview_text = ", ".join(
            f"{symbol} avg_volume={avg_volume:.4f}"
            for symbol, avg_volume in preview
        )
        if not top_preview_text:
            top_preview_text = "пусто"
    else:
        ranked_symbols_count = 0
        symbols = list(symbols)
        top_preview_text = "предварительный отбор отключён"

    pre_rank_elapsed_seconds = time.perf_counter() - pre_rank_started_at
    logger.debug(
        "Предварительный отбор: %s символов → %s, отклонено %s, время %.3fс.",
        symbols_before_ranking,
        len(symbols),
        rejected_symbols_count,
        pre_rank_elapsed_seconds,
    )
    logger.debug("Предварительный список: %s", top_preview_text)

    if not symbols:
        if ranked_symbols_count == 0:
            logger.warning(
                "Нет символов с валидным объёмом на %s",
                levels_timeframe.value,
            )
        else:
            logger.warning("После top_n=%s список символов пуст", top_n)
        if run_root_dir is not None:
            data_load_artifact_rejections = _write_backtest_data_load_artifacts(
                run_root_dir,
                status_rows=data_load_status_rows,
            )
            pno_category_mode, pno_entry_confirmation_mode, pno_variant_id = _resolve_pno_run_context_metadata(
                strategy=strategy,
                config=config,
            )
            _write_backtest_run_context(
                run_root_dir,
                strategy_id=strategy_id,
                results_file_name=config.backtest.results_file_name,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
                backtest_days=backtest_days,
                end_timestamp_ms=backtest_end_timestamp_ms,
                symbols=[],
                plot_requested=should_plot,
                pno_stage=getattr(args, "pno_stage", None),
                pno_through_stage=getattr(args, "pno_through_stage", None),
                pno_category_mode=pno_category_mode,
                pno_entry_confirmation_mode=pno_entry_confirmation_mode,
                pno_variant_id=pno_variant_id,
                data_load_rejections=data_load_artifact_rejections,
                data_load_status_file_name=_BACKTEST_DATA_LOAD_STATUS_FILE_NAME,
                data_load_rejections_file_name=_BACKTEST_DATA_LOAD_REJECTIONS_FILE_NAME,
                source_entry_timeframe=source_entry_timeframe,
                entry_load_mode=entry_load_mode,
            )
        return 0

    symbol_frames: dict[str, SymbolMtfFrames] = {}
    symbols_total = len(symbols)
    symbols_prepare_started_at = time.perf_counter()
    symbols_missing_levels_tf = 0
    symbols_missing_entry_tf = 0
    symbols_used = 0
    symbols_fast_stage1_rejected = 0
    for idx, symbol in enumerate(symbols, start=1):
        resolved_end_timestamp_ms = backtest_end_timestamp_ms
        if backtest_days is not None and resolved_end_timestamp_ms is None:
            candidate_ends: list[int] = []
            levels_end = preparer.get_symbol_last_timestamp_ms(symbol, levels_timeframe)
            if levels_end is not None:
                candidate_ends.append(int(levels_end))
            entry_end = preparer.get_symbol_last_timestamp_ms(symbol, source_entry_timeframe)
            if entry_end is not None:
                candidate_ends.append(int(entry_end))
            resolved_end_timestamp_ms = min(candidate_ends) if candidate_ends else None

        levels_result = preloaded_levels_results.get(symbol)
        if levels_result is None:
            levels_result = preparer.load_symbol_data_result(
                symbol,
                levels_timeframe,
                days=backtest_days,
                end_timestamp_ms=resolved_end_timestamp_ms,
            )
        data_load_status_rows.append(
            _build_data_load_status_row(
                levels_result,
                role="levels",
                window_days=backtest_days,
                end_timestamp_ms=resolved_end_timestamp_ms,
            )
        )
        levels_frame = levels_result.frame
        if not levels_result.ok:
            symbols_missing_levels_tf += 1
            data_load_rejections[f"levels:{levels_result.status}"] += 1
            continue
        if pno_fast_prefilter_active and isinstance(strategy, PnoStrategy):
            if not strategy.has_fast_stage1_candidate(
                symbol=symbol,
                levels_frame=levels_frame,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
            ):
                symbols_fast_stage1_rejected += 1
                continue
        if levels_timeframe == source_entry_timeframe:
            data_load_status_rows.append(
                _build_data_load_status_row(
                    levels_result,
                    role="entry",
                    window_days=backtest_days,
                    end_timestamp_ms=resolved_end_timestamp_ms,
                    status_override=(
                        "reused_levels_source_target_entry_deferred"
                        if pno_seconds_entry_pair
                        else "reused_levels_frame"
                    ),
                    reason_override=(
                        "source timeframe equals levels timeframe; target entry materialized after Stage1"
                        if pno_seconds_entry_pair
                        else "entry timeframe equals levels timeframe"
                    ),
                    requested_timeframe=entry_timeframe,
                    source_timeframe=source_entry_timeframe,
                    target_timeframe=entry_timeframe,
                    load_mode=entry_load_mode,
                    target_checked=entry_target_checked_in_data_load,
                )
            )
            entry_frame = levels_frame
        else:
            entry_result = preparer.load_symbol_data_result(
                symbol,
                source_entry_timeframe,
                days=backtest_days,
                end_timestamp_ms=resolved_end_timestamp_ms,
            )
            data_load_status_rows.append(
                _build_data_load_status_row(
                    entry_result,
                    role="entry",
                    window_days=backtest_days,
                    end_timestamp_ms=resolved_end_timestamp_ms,
                    status_override=(
                        "source_loaded_target_entry_deferred"
                        if pno_seconds_entry_pair and entry_result.ok
                        else None
                    ),
                    reason_override=(
                        "target entry materialized after Stage1"
                        if pno_seconds_entry_pair and entry_result.ok
                        else None
                    ),
                    requested_timeframe=entry_timeframe,
                    source_timeframe=source_entry_timeframe,
                    target_timeframe=entry_timeframe,
                    load_mode=entry_load_mode,
                    target_checked=entry_target_checked_in_data_load,
                )
            )
            entry_frame = entry_result.frame
            if not entry_result.ok:
                symbols_missing_entry_tf += 1
                data_load_rejections[f"entry:{entry_result.status}"] += 1
                continue
        symbols_used += 1
        symbol_frames[symbol] = SymbolMtfFrames(
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
            levels_frame=levels_frame,
            entry_frame=entry_frame,
        )

    if data_load_rejections:
        logger.debug("Причины пропуска данных: %s", dict(sorted(data_load_rejections.items())))

    data_load_artifact_rejections: dict[str, int] = dict(sorted(data_load_rejections.items()))
    if run_root_dir is not None:
        data_load_artifact_rejections = _write_backtest_data_load_artifacts(
            run_root_dir,
            status_rows=data_load_status_rows,
        )

    if not symbol_frames:
        logger.warning("Не удалось подготовить данные для бэктеста")
        if run_root_dir is not None:
            pno_category_mode, pno_entry_confirmation_mode, pno_variant_id = _resolve_pno_run_context_metadata(
                strategy=strategy,
                config=config,
            )
            _write_backtest_run_context(
                run_root_dir,
                strategy_id=strategy_id,
                results_file_name=config.backtest.results_file_name,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
                backtest_days=backtest_days,
                end_timestamp_ms=backtest_end_timestamp_ms,
                symbols=[],
                plot_requested=should_plot,
                pno_stage=getattr(args, "pno_stage", None),
                pno_through_stage=getattr(args, "pno_through_stage", None),
                pno_category_mode=pno_category_mode,
                pno_entry_confirmation_mode=pno_entry_confirmation_mode,
                pno_variant_id=pno_variant_id,
                data_load_rejections=data_load_artifact_rejections,
                data_load_status_file_name=_BACKTEST_DATA_LOAD_STATUS_FILE_NAME,
                data_load_rejections_file_name=_BACKTEST_DATA_LOAD_REJECTIONS_FILE_NAME,
                source_entry_timeframe=source_entry_timeframe,
                entry_load_mode=entry_load_mode,
            )
        return 0

    if run_root_dir is not None:
        pno_category_mode, pno_entry_confirmation_mode, pno_variant_id = _resolve_pno_run_context_metadata(
            strategy=strategy,
            config=config,
        )
        _write_backtest_run_context(
            run_root_dir,
            strategy_id=strategy_id,
            results_file_name=config.backtest.results_file_name,
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
            backtest_days=backtest_days,
            end_timestamp_ms=backtest_end_timestamp_ms,
            symbols=list(symbol_frames.keys()),
            plot_requested=should_plot,
            pno_stage=getattr(args, "pno_stage", None),
            pno_through_stage=getattr(args, "pno_through_stage", None),
            pno_category_mode=pno_category_mode,
            pno_entry_confirmation_mode=pno_entry_confirmation_mode,
            pno_variant_id=pno_variant_id,
            data_load_rejections=data_load_artifact_rejections,
            data_load_status_file_name=_BACKTEST_DATA_LOAD_STATUS_FILE_NAME,
            data_load_rejections_file_name=_BACKTEST_DATA_LOAD_REJECTIONS_FILE_NAME,
            source_entry_timeframe=source_entry_timeframe,
            entry_load_mode=entry_load_mode,
        )

    runner = BacktestRunner(
        config.backtest.results_dir,
        config.backtest.results_file_name,
        logger=logger,
    )
    plot_from_results = _to_bool_flag(getattr(args, "plot_from_results", None), default=False)
    if plot_from_results:
        best_row = _load_plot_params_row_from_results(config, args, logger=logger, strategy_id=strategy_id)
        if best_row is None:
            return 1
        if not _plot_for_strategy_dispatch(
            config=config,
            args=args,
            logger=logger,
            strategy_id=strategy_id,
            strategy=strategy,
            symbol_frames=symbol_frames,
            params_row=best_row,
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
            log_prefix="plot-from-results",
        ):
            return 1
        return 0

    stage_metric_ids_for_run: tuple[str, ...] | None = None
    if (
        should_plot
        and strategy_id == "pno"
        and (getattr(args, "pno_stage", None) is not None or getattr(args, "pno_through_stage", None) is not None)
    ):
        stage_metric_ids_for_run = _resolve_pno_stage_ids(args)

    results = runner.run(
        strategy,
        symbol_frames,
        levels_timeframe=levels_timeframe,
        entry_timeframe=entry_timeframe,
        stage_metric_ids=stage_metric_ids_for_run,
        collect_diagnostics=collect_diagnostics,
    )
    diagnostics_cache = runner.last_generation_diagnostics_cache if collect_diagnostics else None
    summary = runner.build_summary(results)
    if (
        strategy_id == "pno"
        and getattr(config.strategy, "pno_category_mode", None) == "all"
    ):
        _export_pno_category_csv_split(
            results_dir=config.backtest.results_dir,
            logger=logger,
        )
    _log_human_backtest_summary(
        logger=logger,
        results=results,
    )
    if should_plot:
        if results.empty:
            logger.warning("Графики: нет данных")
            return 0

        if strategy_id == "pno" and isinstance(strategy, PnoStrategy):
            if run_root_dir is not None:
                _write_backtest_plot_request(
                    run_root_dir,
                    selected_row_number=_resolve_results_row_number(results, results.iloc[0]),
                )
            if not _plot_pno_grid_artifacts(
                config=config,
                args=args,
                logger=logger,
                strategy=strategy,
                symbol_frames=symbol_frames,
                results=results,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
                log_prefix="запуск-бэктеста: plot=true",
                diagnostics_cache=diagnostics_cache,
            ):
                return 1
            return 0

        if strategy_id == "pno" and (
            getattr(args, "pno_stage", None) is not None or getattr(args, "pno_through_stage", None) is not None
        ):
            best_row = _select_pno_plot_params_row_by_stage(
                args=args,
                strategy=strategy,
                symbol_frames=symbol_frames,
                results=results,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
                logger=logger,
            )
            if best_row is None:
                return 0
        else:
            best_row = results.iloc[0]
        if run_root_dir is not None:
            _write_backtest_plot_request(
                run_root_dir,
                selected_row_number=_resolve_results_row_number(results, best_row),
            )
        if not _plot_for_strategy_dispatch(
            config=config,
            args=args,
            logger=logger,
            strategy_id=strategy_id,
            strategy=strategy,
            symbol_frames=symbol_frames,
            params_row=best_row,
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
            log_prefix="запуск-бэктеста: plot=true",
            diagnostics_cache=diagnostics_cache,
        ):
            return 1
    elif strategy_id == "pno" and not results.empty and isinstance(strategy, PnoStrategy):
        if not collect_diagnostics:
            _export_pno_grid_position_charts_only(
                config=config,
                logger=logger,
                strategy=strategy,
                symbol_frames=symbol_frames,
                results=results,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
            )
        else:
            _export_pno_grid_artifacts_without_stage_charts(
                config=config,
                args=args,
                logger=logger,
                strategy=strategy,
                symbol_frames=symbol_frames,
                results=results,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
                diagnostics_cache=diagnostics_cache,
            )
    return 0


def _collect_pno_stage_summary_rows(
    *,
    scoped_results_dir: Path,
    preset_name: str,
) -> list[dict[str, object]]:
    strategy_results_dir = _resolve_results_dir_for_strategy(
        Path(scoped_results_dir),
        "pno",
    )
    stage_reviews_dir = strategy_results_dir / "position_plots" / "pno_diagnostics" / "stage_reviews"
    manifest_result = _read_csv_with_status(stage_reviews_dir / "manifest.csv")
    _write_frame_from_records(
        stage_reviews_dir / "artifact_load_status.csv",
        [_artifact_status_row(manifest_result)],
        columns=_PNO_ARTIFACT_LOAD_STATUS_COLUMNS,
    )
    manifest = manifest_result.frame
    if manifest.empty:
        return []

    summary_rows: list[dict[str, object]] = []
    for _, row in manifest.iterrows():
        summary_rows.append(
            {
                "preset": preset_name,
                "stage_id": row.get("stage_id"),
                "events_count": row.get("events_count"),
                "events_path": row.get("events_path"),
                "summary_path": row.get("summary_path"),
            }
        )
    return summary_rows


def _run_pno_stage_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("pno-stage", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    preset_stage, preset_through_stage, preset_name = _resolve_pno_stage_preset(getattr(args, "preset", None))
    timestamp_label = time.strftime("%Y%m%d_%H%M%S")
    root_output_dir = (
        Path(args.output_dir)
        if getattr(args, "output_dir", None)
        else Path(config.backtest.results_dir) / "stage_review" / "pno" / preset_name / timestamp_label
    )
    root_output_dir.mkdir(parents=True, exist_ok=True)

    logger.warning("Проверка стадий")
    logger.debug(
        "Пресет %s, стадия %s, до стадии %s, папка %s.",
        preset_name,
        preset_stage,
        preset_through_stage,
        root_output_dir,
    )

    scoped_config = replace(
        config,
        strategy=replace(config.strategy, strategy_id="pno"),
        backtest=replace(config.backtest, results_dir=root_output_dir),
    )
    scoped_args = _with_pno_stage_args(
        args,
        preset_stage=preset_stage,
        preset_through_stage=preset_through_stage,
    )
    exit_code = _run_backtest_inner(scoped_config, scoped_args)
    summary_rows = _collect_pno_stage_summary_rows(
        scoped_results_dir=Path(scoped_config.backtest.results_dir),
        preset_name=preset_name,
    )

    summary_path = root_output_dir / "stage_review_summary.csv"
    _write_frame_from_records(
        summary_path,
        summary_rows,
        columns=_PNO_STAGE_REVIEW_RUN_SUMMARY_COLUMNS,
    )
    context_path = root_output_dir / "stage_review_context.json"
    context_path.write_text(
        json.dumps(
            {
                "preset": preset_name,
                "stage": preset_stage,
                "through_stage": preset_through_stage,
                "symbols": list(getattr(args, "symbols", None) or []),
                "top_n": getattr(args, "top_n", None),
                "entry_timeframe": scoped_args.entry_tf,
                "levels_timeframe": scoped_args.levels_tf,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.warning("Проверка стадий завершена")
    logger.debug(
        "Сводка %s, контекст %s.",
        summary_path,
        context_path,
    )
    return exit_code


def _collect_oi_quality_issues(frame: pd.DataFrame) -> list[dict[str, str]]:
    if "open_interest" not in frame.columns:
        return [
            {
                "issue_type": QUALITY_OI_MISSING_COLUMN_ISSUE,
                "severity": QUALITY_SEVERITY_ERROR,
                "description": "Отсутствует колонка open_interest",
            }
        ]

    oi = pd.Series(pd.to_numeric(frame["open_interest"], errors="coerce"), index=frame.index)
    issues: list[dict[str, str]] = []

    if oi.isna().any():
        issues.append(
            {
                "issue_type": QUALITY_OI_MISSING_VALUES_ISSUE,
                "severity": QUALITY_SEVERITY_WARNING,
                "description": "Есть сырые пропуски open_interest",
            }
        )

    if len(oi) > 1:
        first_valid = oi.first_valid_index()
        if first_valid is not None:
            leading_missing = oi.loc[:first_valid].isna().sum()
            if leading_missing > 0:
                issues.append(
                    {
                        "issue_type": QUALITY_OI_LEADING_GAPS_ISSUE,
                        "severity": QUALITY_SEVERITY_WARNING,
                        "description": "Обнаружены пропуски open_interest в начале ряда",
                    }
                )

        valid_mask = oi.notna()
        comparison_mask = valid_mask & valid_mask.shift(1, fill_value=False)
        compared_observations = int(comparison_mask.sum())

        if compared_observations >= OI_STALE_MIN_OBSERVATIONS:
            stale_ratio = (oi.diff().eq(0) & comparison_mask).sum() / compared_observations
        else:
            stale_ratio = 0.0

        if stale_ratio > OI_STALE_RATIO_THRESHOLD:
            issues.append(
                {
                    "issue_type": QUALITY_OI_STALE_SERIES_ISSUE,
                    "severity": QUALITY_SEVERITY_ERROR,
                    "description": "open_interest почти не меняется на сырых данных",
                }
            )

    return issues


def _build_quality_recommendations(summary: QualitySummary, symbols: dict[str, QualitySymbolStats]) -> list[str]:
    recommendations: list[str] = []
    if summary.gaps_total > 0:
        recommendations.append("Дозагрузка диапазона: запустите update-cache для символов с пропусками")

    if any(data.gaps > 0 for data in symbols.values()):
        recommendations.append("Проверка таймфрейма: убедитесь, что timeframe совпадает с кэшем")

    if summary.issues_total > 0:
        recommendations.append("Дедупликация и очистка: переcохраните ряды с удалением дублей и аномалий")

    oi_problem_types = {
        QUALITY_OI_MISSING_COLUMN_ISSUE,
        QUALITY_OI_MISSING_VALUES_ISSUE,
        QUALITY_OI_LEADING_GAPS_ISSUE,
        QUALITY_OI_STALE_SERIES_ISSUE,
    }
    if any(problem in oi_problem_types for problem in summary.by_issue_type):
        recommendations.append("Проверка OI-источника: перезапустите загрузку OI и проверьте сырые пропуски/аномалии")

    if summary.issues_total > 0 or summary.gaps_total > 0:
        recommendations.append("Повторная валидация: после исправлений выполните check-quality повторно")

    return recommendations


def _save_quality_report(report: QualityReport, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix.lower() == ".csv":
        symbols = report.symbols
        with output_path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(["symbol", "issues", "gaps", "warning", "error", "critical", "info"])
            for symbol, data in symbols.items():
                sev = data.by_severity
                writer.writerow(
                    [
                        symbol,
                        data.issues,
                        data.gaps,
                        sev.get(QUALITY_SEVERITY_WARNING, 0),
                        sev.get(QUALITY_SEVERITY_ERROR, 0),
                        sev.get(QUALITY_SEVERITY_CRITICAL, 0),
                        sev.get(QUALITY_SEVERITY_INFO, 0),
                    ]
                )
    else:
        output_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")


def _check_quality_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("check-quality", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    preparer = DataPreparer(config.backtest.cache_dir)
    symbols = args.symbols or preparer.list_symbols(config.fetch.timeframe)
    if not symbols:
        logger.warning("Нет данных для проверки качества")
        return 0

    validator = DataValidator()
    gap_detector = GapDetector()

    issues_by_type: Counter[str] = Counter()
    issues_by_severity: Counter[str] = Counter()
    symbols_report: dict[str, QualitySymbolStats] = {}

    total_issues = 0
    total_gaps = 0
    for symbol in symbols:
        load_result = preparer.load_symbol_data_result(symbol, config.fetch.timeframe)
        frame = load_result.frame
        if not load_result.ok:
            logger.debug(
                "Проверка качества: %s пропущен, %s (%s).",
                symbol,
                load_result.status,
                load_result.reason,
            )
            continue

        issues = validator.validate(symbol, config.fetch.timeframe, frame)
        gaps = gap_detector.detect_gaps(frame, expected_step_ms=config.fetch.timeframe.to_milliseconds())
        oi_quality_issues = _collect_oi_quality_issues(frame)

        all_issue_types = [issue.issue_type for issue in issues]
        all_severities = [issue.severity.value for issue in issues]
        all_issue_types.extend(item["issue_type"] for item in oi_quality_issues)
        all_severities.extend(item["severity"] for item in oi_quality_issues)

        symbol_issue_counter = Counter(all_issue_types)
        symbol_severity_counter = Counter(all_severities)
        issues_by_type.update(symbol_issue_counter)
        issues_by_severity.update(symbol_severity_counter)

        symbol_total_issues = len(issues) + len(oi_quality_issues)
        total_issues += symbol_total_issues
        total_gaps += len(gaps)

        symbols_report[symbol] = QualitySymbolStats(
            issues=symbol_total_issues,
            gaps=len(gaps),
            by_issue_type=dict(sorted(symbol_issue_counter.items())),
            by_severity=dict(sorted(symbol_severity_counter.items())),
        )

        logger.debug(
            "Проверка качества: %s, проблем %s, пропусков %s, проблем OI %s.",
            symbol,
            symbol_total_issues,
            len(gaps),
            len(oi_quality_issues),
        )

    summary = QualitySummary(
        symbols_checked=len(symbols_report),
        issues_total=total_issues,
        gaps_total=total_gaps,
        by_issue_type=dict(sorted(issues_by_type.items())),
        by_severity=dict(sorted(issues_by_severity.items())),
    )
    report = QualityReport(
        summary=summary,
        symbols=symbols_report,
        recommendations=_build_quality_recommendations(summary, symbols_report),
    )

    output_path = Path(args.output) if args.output else config.backtest.results_dir / DEFAULT_QUALITY_REPORT_OUTPUT_FILE
    _save_quality_report(report, output_path)

    logger.warning("Проверка качества завершена\nПроблем: %s\nПропусков: %s", report.summary.issues_total, report.summary.gaps_total)
    logger.info("Отчёт качества: %s", output_path)
    return 0


def _clear_cache_inner(config: AppConfig, _args: argparse.Namespace) -> int:
    logger = get_logger("clear-cache", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)

    cache_dir = config.backtest.cache_dir
    cache_dir_str = str(cache_dir).strip()
    if not cache_dir_str:
        logger.error("Удаление отменено: путь кэша пустой")
        return 1

    resolved_cache_dir = cache_dir.expanduser().resolve()
    home_dir = Path.home().resolve()
    if resolved_cache_dir == Path(resolved_cache_dir.anchor):
        logger.error("Удаление отменено: путь кэша указывает на корень ФС (%s)", resolved_cache_dir)
        return 1

    if resolved_cache_dir == home_dir:
        logger.error("Удаление отменено: путь кэша указывает на домашнюю директорию (%s)", resolved_cache_dir)
        return 1

    logger.warning("Очистка кэша: %s", resolved_cache_dir)
    shutil.rmtree(resolved_cache_dir, ignore_errors=True)
    resolved_cache_dir.mkdir(parents=True, exist_ok=True)
    logger.warning("Кэш очищен")
    return 0



def _plot_backtest_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("plot-backtest", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    raw_run_dir = getattr(args, "run_dir", None)
    if raw_run_dir is None or not str(raw_run_dir).strip():
        logger.error("Нужен параметр --run-dir")
        return 1
    run_dir = Path(str(raw_run_dir))

    try:
        run_root_dir, context, request = _load_saved_backtest_request(run_dir)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.error("Не удалось открыть сохранённый прогон: %s", exc)
        return 1

    plot_args = _build_plot_backtest_args(
        run_root_dir=run_root_dir,
        context=context,
        request=request,
    )
    logger.debug(
        "Повторная генерация графиков: прогон %s, результаты %s, папка %s, строка %s.",
        run_root_dir,
        getattr(plot_args, "results_input", None),
        getattr(plot_args, "output_dir", None),
        getattr(plot_args, "row_number", None) if getattr(plot_args, "row_number", None) is not None else "best",
    )
    scoped_config = replace(
        config,
        strategy=replace(config.strategy),
        backtest=replace(config.backtest),
    )
    return _run_backtest_inner(scoped_config, plot_args)


# endregion Приватные

# Публичные точки входа

def fetch_data(config: AppConfig, args: argparse.Namespace) -> int:
    """Запускает сценарий загрузки рыночных данных."""
    return _run_with_logging("fetch-data", config, lambda: _fetch_data_inner(config, args))

def update_cache(config: AppConfig, args: argparse.Namespace) -> int:
    """Обновляет локальный кэш данных."""
    return _run_with_logging("update-cache", config, lambda: _update_cache_inner(config, args))


def run_backtest(config: AppConfig, args: argparse.Namespace) -> int:
    """Запускает бэктест по текущей конфигурации."""
    if _to_bool_flag(getattr(args, "plot_from_results", None), default=False):
        return _run_with_logging("run-backtest", config, lambda: _run_backtest_inner(config, args))

    strategy_id = (
        _resolve_strategy_id(args)
        if getattr(args, "strategy", None) is not None
        else str(config.strategy.strategy_id).strip().lower()
    )
    run_root_dir = _resolve_backtest_run_root_dir(config.backtest.results_dir, strategy_id)
    run_root_dir.mkdir(parents=True, exist_ok=True)
    if strategy_id == "pno" and bool(getattr(args, "pno_all_tf_pairs", False)):
        return _run_with_logging(
            "run-backtest",
            config,
            lambda: _run_backtest_all_pno_timeframe_pairs(
                config=config,
                args=args,
                strategy_id=strategy_id,
                run_root_dir=run_root_dir,
            ),
        )
    scoped_config = replace(
        config,
        strategy=replace(config.strategy),
        backtest=replace(config.backtest, results_dir=run_root_dir),
    )
    scoped_args = argparse.Namespace(**vars(args))
    scoped_args.backtest_run_root_dir = str(run_root_dir)
    return _run_with_logging("run-backtest", scoped_config, lambda: _run_backtest_inner(scoped_config, scoped_args))


def _run_backtest_all_pno_timeframe_pairs(
    *,
    config: AppConfig,
    args: argparse.Namespace,
    strategy_id: str,
    run_root_dir: Path,
) -> int:
    if str(strategy_id).strip().lower() != "pno":
        raise ValueError("multi-timeframe backtest run is supported only for PNO")

    exit_codes: list[int] = []
    for levels_timeframe, entry_timeframe in PNO_BACKTEST_TIMEFRAME_PAIRS:
        pair_label = f"{levels_timeframe.value}_{entry_timeframe.value}"
        pair_root_dir = run_root_dir / pair_label
        pair_root_dir.mkdir(parents=True, exist_ok=True)
        scoped_config = replace(
            config,
            strategy=replace(config.strategy),
            backtest=replace(config.backtest, results_dir=pair_root_dir),
        )
        scoped_args = argparse.Namespace(**vars(args))
        scoped_args.levels_tf = levels_timeframe.value
        scoped_args.entry_tf = entry_timeframe.value
        scoped_args.backtest_run_root_dir = str(pair_root_dir)
        exit_codes.append(_run_backtest_inner(scoped_config, scoped_args))
    return max(exit_codes, default=0)


def plot_backtest(config: AppConfig, args: argparse.Namespace) -> int:
    """Rebuilds plots for a saved backtest run without rerunning the grid."""
    return _run_with_logging("plot-backtest", config, lambda: _plot_backtest_inner(config, args))


def run_pno_stage(config: AppConfig, args: argparse.Namespace) -> int:
    """Runs compact stage review for PNO on the standard 1m/5m pipeline."""
    return _run_with_logging("pno-stage", config, lambda: _run_pno_stage_inner(config, args))


def run_anomaly_lab(config: AppConfig, args: argparse.Namespace) -> int:
    """Runs early anomaly-continuation research backtest artifacts."""

    def _run() -> int:
        from research_tools.anomaly_strategy_backtest import (
            AnomalyBacktestConfig,
            AnomalyLabConfig,
            _parse_grid_values,
            _parse_grid_profile_values,
            run_anomaly_strategy_backtest,
        )

        output_dir = (
            Path(str(args.output_dir))
            if getattr(args, "output_dir", None)
            else config.backtest.results_dir / "anomaly_lab"
        )
        lab_config = AnomalyLabConfig(
            cache_dir=config.backtest.cache_dir,
            output_dir=output_dir,
            timeframe=str(args.timeframe),
            days=int(args.days),
            end_timestamp_ms=getattr(args, "end_timestamp_ms", None),
            baseline_candles=int(args.baseline_candles),
            confirmation_candles=int(args.confirmation_candles),
            forward_high_candles=int(args.forward_high_candles),
            forward_low_candles=int(args.forward_low_candles),
            min_quote_ratio_start=float(args.min_quote_ratio_start),
            min_trade_ratio_start=float(args.min_trade_ratio_start),
        )
        backtest_config = AnomalyBacktestConfig(
            lab_config=lab_config,
            min_price_retention=float(args.min_price_retention),
            max_price_retention=(
                None if getattr(args, "max_price_retention", None) is None else float(args.max_price_retention)
            ),
            min_verticality_score=float(args.min_verticality_score),
            min_hold_count=int(args.min_hold_count),
            min_oi_change_pct_3x5m=(
                None
                if getattr(args, "min_oi_change_pct_3x5m", None) is None
                else float(args.min_oi_change_pct_3x5m)
            ),
            require_oi_status_ok=bool(getattr(args, "require_oi_status_ok", False)),
            exhaustion_profile=str(getattr(args, "exhaustion_profile", "none")),
            max_start_quote_ratio=(
                None if getattr(args, "max_start_quote_ratio", None) is None else float(args.max_start_quote_ratio)
            ),
            max_start_trade_ratio=(
                None if getattr(args, "max_start_trade_ratio", None) is None else float(args.max_start_trade_ratio)
            ),
            max_start_avg_trade_quote_size_ratio=(
                None
                if getattr(args, "max_start_avg_trade_quote_size_ratio", None) is None
                else float(args.max_start_avg_trade_quote_size_ratio)
            ),
            max_start_quote_ratio_per_abs_return=(
                None
                if getattr(args, "max_start_quote_ratio_per_abs_return", None) is None
                else float(args.max_start_quote_ratio_per_abs_return)
            ),
            max_start_range_pct_ratio_to_baseline=(
                None
                if getattr(args, "max_start_range_pct_ratio_to_baseline", None) is None
                else float(args.max_start_range_pct_ratio_to_baseline)
            ),
            min_next_taker_buy_quote_share=(
                None
                if getattr(args, "min_next_taker_buy_quote_share", None) is None
                else float(args.min_next_taker_buy_quote_share)
            ),
            max_initial_risk_pct=float(args.max_initial_risk_pct),
            entry_method=str(getattr(args, "entry_method", "market")),
            pullback_box_fraction=float(getattr(args, "pullback_box_fraction", 0.75)),
            entry_timeout_candles=int(getattr(args, "entry_timeout_candles", 60)),
            tp1_r=float(args.tp1_r),
            tp1_fraction=float(args.tp1_fraction),
            trail_lookback_candles=int(args.trail_lookback_candles),
            trail_buffer_r=float(args.trail_buffer_r),
            max_hold_candles=int(args.max_hold_candles),
            fee_rate=float(args.fee_rate),
        )
        run_anomaly_strategy_backtest(
            backtest_config,
            symbols=getattr(args, "symbols", None),
            run_entry_grid=bool(getattr(args, "run_entry_grid", False)),
            grid_oi3_values=_parse_grid_values(str(getattr(args, "grid_oi3_values", "0.01,0.02,0.03")), cast=float),
            grid_hold_values=_parse_grid_values(str(getattr(args, "grid_hold_values", "1,2")), cast=int),
            grid_pullback_fractions=_parse_grid_values(
                str(getattr(args, "grid_pullback_fractions", "0.65,0.75,0.85")),
                cast=float,
            ),
            grid_exhaustion_profiles=_parse_grid_profile_values(
                str(getattr(args, "grid_exhaustion_profiles", "none"))
            ),
        )
        return 0

    return _run_with_logging("run-anomaly-lab", config, _run)


def check_quality(config: AppConfig, args: argparse.Namespace) -> int:
    """Проверяет качество и целостность данных."""
    return _run_with_logging("check-quality", config, lambda: _check_quality_inner(config, args))


def clear_cache(config: AppConfig, args: argparse.Namespace) -> int:
    """Очищает директорию локального кэша и пересоздаёт её."""
    return _run_with_logging("clear-cache", config, lambda: _clear_cache_inner(config, args))




