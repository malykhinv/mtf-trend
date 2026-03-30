from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from domain.enums.timeframe import Timeframe
from strategy.hourly_asia_pump.research import _format_duration, _progress_snapshot, _session_mask
from strategy.hourly_asia_pump.short_edge import (
    _build_execution_models,
    _build_geometry_events,
    _build_holdout_summary,
    _build_monthly_detail,
    _candidate_meets_goal,
    _candidate_score,
    _model_matches_row,
)
from strategy.hourly_asia_pump.static_combo import (
    _calendar_months_from_frames,
    _frame_to_markdown,
    _summarize_events,
)
from strategy.hourly_asia_pump.unified_edge import (
    _build_month_stability_metrics,
    _build_symbol_concentration_metrics,
    _simulate_equity_risk_metrics,
)
from vectorbt_runner.data_preparer import DataPreparer

module_logger = logging.getLogger(__name__)

_SESSION_WINDOWS_UTC: dict[str, tuple[int, int]] = {
    "asia": (0, 8),
    "europe": (8, 16),
    "america": (16, 0),
}
_ALL_5M_MINUTES: tuple[int, ...] = tuple(range(0, 60, 5))
_UNIVERSE_MIN_TRIGGER_RETURN_PCT = 0.03
_UNIVERSE_MIN_RANGE_ATR = 4.0
_UNIVERSE_MIN_VOLUME_MULT = 4.0
_UNIVERSE_MAX_CLOSE_TO_HIGH_FRAC = 0.25
_SCAN_PROGRESS_LOG_EVERY_SYMBOLS = 25
_MODEL_PROGRESS_LOG_EVERY = 50
_FILTER_PROGRESS_LOG_EVERY = 200


@dataclass(frozen=True, slots=True)
class MinuteFilterSpec:
    filter_id: str
    label: str
    allowed_minutes: tuple[int, ...]
    filter_kind: str


def _safe_float(value: object) -> float | None:
    if value is None or value is pd.NA:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(numeric):
        return None
    return float(numeric)


def _numeric_column(frame: pd.DataFrame, column: str) -> pd.Series:
    if frame.empty or column not in frame.columns:
        return pd.Series(0.0, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)


def _resolve_session_window(session_id: str) -> tuple[int, int]:
    key = str(session_id).strip().lower()
    if key not in _SESSION_WINDOWS_UTC:
        supported = ", ".join(sorted(_SESSION_WINDOWS_UTC))
        raise ValueError(f"Неизвестная сессия: {session_id}. Поддерживаются: {supported}")
    return _SESSION_WINDOWS_UTC[key]


def _build_standard_minute_filters() -> list[MinuteFilterSpec]:
    return [
        MinuteFilterSpec("top_of_hour_only", "только xx:00", (0,), "standard"),
        MinuteFilterSpec("quarter_hours", "квартальные минуты", (0, 15, 30, 45), "standard"),
        MinuteFilterSpec("half_hours", "получасовые минуты", (0, 30), "standard"),
        MinuteFilterSpec("non_quarter_hours", "не квартальные минуты", (5, 10, 20, 25, 35, 40, 50, 55), "standard"),
        MinuteFilterSpec("all_5m_minutes", "все 5m минуты", _ALL_5M_MINUTES, "standard"),
    ]


def _build_exact_minute_filters() -> list[MinuteFilterSpec]:
    return [
        MinuteFilterSpec(
            filter_id=f"minute_{minute:02d}",
            label=f"только xx:{minute:02d}",
            allowed_minutes=(minute,),
            filter_kind="exact",
        )
        for minute in _ALL_5M_MINUTES
    ]


def _build_dynamic_minute_filters(
    exact_best_summary: pd.DataFrame,
    *,
    max_source_minutes: int = 6,
) -> list[MinuteFilterSpec]:
    if exact_best_summary.empty:
        return []
    prepared = exact_best_summary.copy()
    prepared["minute_int"] = (
        prepared["minute_filter_id"]
        .astype(str)
        .str.replace("minute_", "", regex=False)
        .map(lambda value: int(value))
    )
    prepared["trades_per_year_num"] = _numeric_column(prepared, "trades_per_year")
    prepared["mean_return_pct_num"] = _numeric_column(prepared, "mean_return_pct")
    prepared["annualized_unit_pnl_pct_num"] = _numeric_column(prepared, "annualized_unit_pnl_pct")
    prepared["stability_score_num"] = _numeric_column(prepared, "stability_score")
    eligible = prepared[
        (prepared["trades_per_year_num"] >= 5.0)
        & (prepared["mean_return_pct_num"] > 0.0)
        & (prepared["annualized_unit_pnl_pct_num"] > 0.0)
    ].copy()
    if eligible.empty:
        return []
    eligible = eligible.sort_values(
        ["stability_score_num", "mean_return_pct_num", "trades_per_year_num"],
        ascending=[False, False, False],
    )
    source_minutes = eligible["minute_int"].dropna().astype("int64").tolist()[:max_source_minutes]
    source_minutes = list(dict.fromkeys(source_minutes))
    if len(source_minutes) < 2:
        return []

    specs: list[MinuteFilterSpec] = []
    seen: set[tuple[int, ...]] = set()
    max_combo_size = min(5, len(source_minutes))
    for combo_size in range(2, max_combo_size + 1):
        for combo in combinations(source_minutes, combo_size):
            if combo in seen:
                continue
            seen.add(combo)
            label = ", ".join(f"{minute:02d}" for minute in combo)
            specs.append(
                MinuteFilterSpec(
                    filter_id="combo_" + "_".join(f"{minute:02d}" for minute in combo),
                    label=f"комбо минут {label}",
                    allowed_minutes=tuple(combo),
                    filter_kind="dynamic",
                )
            )
    return specs


def _build_symbol_anomalies(
    *,
    preparer: DataPreparer,
    symbol: str,
    timeframe: Timeframe,
    allowed_trigger_minutes: Sequence[int],
    session_start_hour_utc: int,
    session_end_hour_utc: int,
) -> pd.DataFrame:
    frame = preparer.load_symbol_data(symbol, timeframe)
    if frame.empty:
        return pd.DataFrame()

    timestamps = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
    hours = timestamps.dt.hour
    minutes = timestamps.dt.minute

    true_range = pd.to_numeric(frame["high"] - frame["low"], errors="coerce")
    atr = true_range.rolling(36, min_periods=12).mean().shift(1)
    volume_baseline = pd.to_numeric(frame["volume"], errors="coerce").rolling(144, min_periods=36).median().shift(1)
    pre_base_high = pd.to_numeric(frame["high"], errors="coerce").rolling(12, min_periods=6).max().shift(1)
    pre_base_low = pd.to_numeric(frame["low"], errors="coerce").rolling(12, min_periods=6).min().shift(1)
    pre_base_open = pd.to_numeric(frame["open"], errors="coerce").shift(12)
    pre_base_close = pd.to_numeric(frame["close"], errors="coerce").shift(1)
    close_to_high_frac = (pd.to_numeric(frame["high"], errors="coerce") - pd.to_numeric(frame["close"], errors="coerce")) / true_range.where(true_range > 0)
    trigger_return_pct = pd.to_numeric(frame["close"], errors="coerce") / pd.to_numeric(frame["open"], errors="coerce") - 1.0
    trigger_range_pct = true_range / pd.to_numeric(frame["open"], errors="coerce")
    pre_base_range_pct = pd.to_numeric((pre_base_high - pre_base_low) / pd.to_numeric(frame["open"], errors="coerce"), errors="coerce")
    pre_base_drift_pct = pd.to_numeric((pre_base_close / pre_base_open - 1.0).abs(), errors="coerce")

    event_mask = (
        minutes.isin([int(value) for value in allowed_trigger_minutes])
        & _session_mask(hours, start_hour_utc=session_start_hour_utc, end_hour_utc=session_end_hour_utc)
        & (pd.to_numeric(frame["close"], errors="coerce") > pd.to_numeric(frame["open"], errors="coerce"))
    )
    if not bool(event_mask.any()):
        return pd.DataFrame()

    events = pd.DataFrame(
        {
            "symbol": symbol,
            "timeframe": timeframe.value,
            "row_index": frame.index,
            "timestamp_ms": pd.to_numeric(frame["timestamp"], errors="coerce").astype("int64"),
            "timestamp_utc": timestamps.dt.strftime("%Y-%m-%d %H:%M:%S"),
            "month_utc": timestamps.dt.strftime("%Y-%m"),
            "hour_utc": hours.astype("int64"),
            "trigger_minute": minutes.astype("int64"),
            "trigger_open": pd.to_numeric(frame["open"], errors="coerce"),
            "trigger_high": pd.to_numeric(frame["high"], errors="coerce"),
            "trigger_low": pd.to_numeric(frame["low"], errors="coerce"),
            "trigger_close": pd.to_numeric(frame["close"], errors="coerce"),
            "trigger_volume": pd.to_numeric(frame["volume"], errors="coerce"),
            "trigger_return_pct": trigger_return_pct,
            "trigger_range_pct": trigger_range_pct,
            "range_atr": pd.to_numeric(true_range / atr, errors="coerce"),
            "volume_mult": pd.to_numeric(frame["volume"], errors="coerce") / volume_baseline,
            "close_to_high_frac": pd.to_numeric(close_to_high_frac.fillna(1.0), errors="coerce"),
            "pre_base_range_pct_60m": pre_base_range_pct,
            "pre_base_drift_pct_60m": pre_base_drift_pct,
        }
    )
    events = events[event_mask].copy()
    if events.empty:
        return pd.DataFrame()

    loose_mask = (
        (events["trigger_return_pct"] >= _UNIVERSE_MIN_TRIGGER_RETURN_PCT)
        & (events["range_atr"] >= _UNIVERSE_MIN_RANGE_ATR)
        & (events["volume_mult"] >= _UNIVERSE_MIN_VOLUME_MULT)
        & (events["close_to_high_frac"] <= _UNIVERSE_MAX_CLOSE_TO_HIGH_FRAC)
    )
    events = events[loose_mask.fillna(False)].copy()
    if events.empty:
        return pd.DataFrame()
    return events.reset_index(drop=True)


def _build_session_anomalies(
    *,
    preparer: DataPreparer,
    timeframe: Timeframe,
    session_id: str,
    symbols: Sequence[str],
    allowed_trigger_minutes: Sequence[int],
    logger: logging.Logger,
) -> pd.DataFrame:
    session_start_hour_utc, session_end_hour_utc = _resolve_session_window(session_id)
    parts: list[pd.DataFrame] = []
    started_at = time.time()
    total_rows = 0
    for index, symbol in enumerate(symbols, start=1):
        events = _build_symbol_anomalies(
            preparer=preparer,
            symbol=str(symbol),
            timeframe=timeframe,
            allowed_trigger_minutes=allowed_trigger_minutes,
            session_start_hour_utc=session_start_hour_utc,
            session_end_hour_utc=session_end_hour_utc,
        )
        if not events.empty:
            parts.append(events)
            total_rows += len(events)
        if index % _SCAN_PROGRESS_LOG_EVERY_SYMBOLS == 0 or index == len(symbols):
            progress_pct, elapsed, eta_seconds = _progress_snapshot(
                completed=index,
                total=len(symbols),
                started_at=started_at,
            )
            logger.info(
                "session-short-edge: stage=scan session=%s progress=%.1f%% scanned_symbols=%s/%s anomaly_rows=%s elapsed=%s eta=%s",
                session_id,
                progress_pct,
                index,
                len(symbols),
                total_rows,
                _format_duration(elapsed),
                _format_duration(eta_seconds),
            )
    if not parts:
        return pd.DataFrame()
    result = pd.concat(parts, ignore_index=True)
    return result.sort_values(["timestamp_ms", "symbol"]).reset_index(drop=True)


def _filter_events_by_minutes(events: pd.DataFrame, allowed_minutes: Sequence[int]) -> pd.DataFrame:
    if events.empty:
        return events
    allowed_set = {int(value) for value in allowed_minutes}
    return events[events["trigger_minute"].astype("int64").isin(sorted(allowed_set))].copy()


def _build_model_event_cache(
    *,
    geometry_events: pd.DataFrame,
    models: Sequence[Any],
    logger: logging.Logger,
) -> dict[str, pd.DataFrame]:
    rows_cache: dict[str, pd.DataFrame] = {}
    started_at = time.time()
    for index, model in enumerate(models, start=1):
        scoped = geometry_events[geometry_events.apply(lambda row: _model_matches_row(model, row), axis=1)].copy()
        if not scoped.empty:
            rows_cache[str(model.model_id)] = scoped.sort_values(["entry_timestamp_ms", "symbol", "timestamp_ms"]).reset_index(drop=True)
        if index % _MODEL_PROGRESS_LOG_EVERY == 0 or index == len(models):
            progress_pct, elapsed, eta_seconds = _progress_snapshot(
                completed=index,
                total=len(models),
                started_at=started_at,
            )
            logger.info(
                "session-short-edge: stage=model-match progress=%.1f%% models=%s/%s kept=%s elapsed=%s eta=%s",
                progress_pct,
                index,
                len(models),
                len(rows_cache),
                _format_duration(elapsed),
                _format_duration(eta_seconds),
            )
    return rows_cache


def _summarize_filter_candidate(
    *,
    events: pd.DataFrame,
    calendar_months: list[str],
    minute_filter: MinuteFilterSpec,
    model: Any,
) -> dict[str, object] | None:
    if events.empty:
        return None
    summary = _summarize_events(events, calendar_months=calendar_months)
    if summary is None:
        return None
    month_stability = _build_month_stability_metrics(events, calendar_months=calendar_months)
    symbol_concentration = _build_symbol_concentration_metrics(events, calendar_months=calendar_months)
    equity_5, _ = _simulate_equity_risk_metrics(events, calendar_months=calendar_months, risk_fraction=0.05)
    equity_9, _ = _simulate_equity_risk_metrics(events, calendar_months=calendar_months, risk_fraction=0.09)

    row: dict[str, object] = {
        "model_id": str(model.model_id),
        "model_label": str(model.label),
        "signal_profile_id": str(model.signal_profile.profile_id),
        "trigger_pressure_id": str(model.trigger_pressure_profile.profile_id),
        "next_pressure_id": str(model.next_pressure_profile.profile_id),
        "context_profile_id": str(model.context_profile.profile_id),
        "geometry_id": str(model.geometry.geometry_id),
        "entry_style": str(model.geometry.entry_style),
        "minute_filter_id": minute_filter.filter_id,
        "minute_filter_label": minute_filter.label,
        "minute_filter_type": minute_filter.filter_kind,
        "allowed_minutes": ",".join(f"{minute:02d}" for minute in minute_filter.allowed_minutes),
        "rule_text": str(model.rule_text),
        **summary,
        **month_stability,
        **symbol_concentration,
        "equity_5_total_return_pct": equity_5["equity_total_return_pct"],
        "equity_5_annualized_return_pct": equity_5["equity_annualized_return_pct"],
        "equity_5_max_drawdown_pct": equity_5["equity_max_drawdown_pct"],
        "equity_5_positive_months_count": equity_5["equity_positive_months_count"],
        "equity_5_stable_positive_months_count": equity_5["equity_stable_positive_months_count"],
        "equity_9_total_return_pct": equity_9["equity_total_return_pct"],
        "equity_9_annualized_return_pct": equity_9["equity_annualized_return_pct"],
        "equity_9_max_drawdown_pct": equity_9["equity_max_drawdown_pct"],
        "equity_9_positive_months_count": equity_9["equity_positive_months_count"],
        "equity_9_stable_positive_months_count": equity_9["equity_stable_positive_months_count"],
    }
    row["meets_goal"] = _candidate_meets_goal(row)
    row["selection_score"] = _candidate_score(row)
    top3_symbol_share = float(_safe_float(row.get("top3_symbol_pnl_share")) or 0.0)
    top5_symbol_share = float(_safe_float(row.get("top5_symbol_pnl_share")) or 0.0)
    row["stability_score"] = (
        float(row["selection_score"])
        + float(row["equity_5_annualized_return_pct"]) * 65.0
        + float(row["equity_9_annualized_return_pct"]) * 35.0
        - top3_symbol_share * 45.0
        - top5_symbol_share * 30.0
    )
    return row


def _summarize_models_by_filters(
    *,
    model_events_cache: dict[str, pd.DataFrame],
    models: Sequence[Any],
    minute_filters: Sequence[MinuteFilterSpec],
    calendar_months: list[str],
    logger: logging.Logger,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    total = len(models) * len(minute_filters)
    completed = 0
    started_at = time.time()
    models_by_id = {str(model.model_id): model for model in models}
    for minute_filter in minute_filters:
        for model_id, base_events in model_events_cache.items():
            completed += 1
            scoped = _filter_events_by_minutes(base_events, minute_filter.allowed_minutes)
            if not scoped.empty:
                candidate = _summarize_filter_candidate(
                    events=scoped,
                    calendar_months=calendar_months,
                    minute_filter=minute_filter,
                    model=models_by_id[model_id],
                )
                if candidate is not None:
                    rows.append(candidate)
            if completed % _FILTER_PROGRESS_LOG_EVERY == 0 or completed == total:
                progress_pct, elapsed, eta_seconds = _progress_snapshot(
                    completed=completed,
                    total=total,
                    started_at=started_at,
                )
                logger.info(
                    "session-short-edge: stage=minute-summary progress=%.1f%% items=%s/%s rows=%s elapsed=%s eta=%s",
                    progress_pct,
                    completed,
                    total,
                    len(rows),
                    _format_duration(elapsed),
                    _format_duration(eta_seconds),
                )
    if not rows:
        return pd.DataFrame()
    summary = pd.DataFrame(rows).sort_values(
        ["meets_goal", "stability_score", "mean_return_pct", "win_rate", "trades_per_year"],
        ascending=[False, False, False, False, False],
    ).reset_index(drop=True)
    summary["candidate_rank"] = range(1, len(summary) + 1)
    return summary


def _best_per_filter(summary: pd.DataFrame, *, filter_kind: str) -> pd.DataFrame:
    if summary.empty:
        return pd.DataFrame()
    scoped = summary[summary["minute_filter_type"].astype(str) == str(filter_kind)].copy()
    if scoped.empty:
        return scoped
    scoped = scoped.sort_values(
        ["meets_goal", "stability_score", "mean_return_pct", "win_rate", "trades_per_year"],
        ascending=[False, False, False, False, False],
    )
    return scoped.groupby("minute_filter_id", as_index=False, sort=False).head(1).reset_index(drop=True)


def _write_report(
    *,
    report_path: Path,
    context: dict[str, object],
    best_overall: pd.DataFrame,
    best_exact: pd.DataFrame,
    best_standard: pd.DataFrame,
    best_dynamic: pd.DataFrame,
    best_monthly: pd.DataFrame,
    best_holdout: pd.DataFrame,
) -> None:
    lines = [
        f"# Шорт {str(context['session_ru'])} по минутам часа",
        "",
        "Этот прогон ищет стабильный short-edge после аномальной зелёной `5m` свечи.",
        "Поиск идёт сразу по разным минутам часа и по комбинациям минут, без развилок по часам внутри модели.",
        "",
        "## Лучший общий кандидат",
        "",
        _frame_to_markdown(
            best_overall,
            columns=[
                "candidate_rank",
                "minute_filter_label",
                "model_label",
                "context_profile_id",
                "trades_per_year",
                "mean_return_pct",
                "win_rate",
                "annualized_unit_pnl_pct",
                "max_drawdown_pct",
                "positive_months_count",
                "stable_positive_months_count",
                "equity_5_annualized_return_pct",
                "equity_9_annualized_return_pct",
                "top3_symbol_pnl_share",
                "top5_symbol_pnl_share",
            ],
            limit=1,
        ),
        "",
        "## Лучшие exact-минуты",
        "",
        _frame_to_markdown(
            best_exact,
            columns=[
                "minute_filter_id",
                "model_label",
                "context_profile_id",
                "trades_per_year",
                "mean_return_pct",
                "win_rate",
                "annualized_unit_pnl_pct",
                "max_drawdown_pct",
                "equity_5_annualized_return_pct",
                "equity_9_annualized_return_pct",
                "top3_symbol_pnl_share",
            ],
            limit=12,
        ),
        "",
        "## Лучшие стандартные категории минут",
        "",
        _frame_to_markdown(
            best_standard,
            columns=[
                "minute_filter_id",
                "model_label",
                "context_profile_id",
                "trades_per_year",
                "mean_return_pct",
                "win_rate",
                "annualized_unit_pnl_pct",
                "max_drawdown_pct",
                "positive_months_count",
                "stable_positive_months_count",
                "equity_5_annualized_return_pct",
                "equity_9_annualized_return_pct",
                "top3_symbol_pnl_share",
                "top5_symbol_pnl_share",
            ],
        ),
        "",
        "## Лучшие динамические комбинации минут",
        "",
        _frame_to_markdown(
            best_dynamic,
            columns=[
                "minute_filter_id",
                "allowed_minutes",
                "model_label",
                "context_profile_id",
                "trades_per_year",
                "mean_return_pct",
                "win_rate",
                "annualized_unit_pnl_pct",
                "max_drawdown_pct",
                "positive_months_count",
                "stable_positive_months_count",
                "equity_5_annualized_return_pct",
                "equity_9_annualized_return_pct",
                "top3_symbol_pnl_share",
                "top5_symbol_pnl_share",
            ],
            limit=12,
        ),
        "",
        "## Месяцы лучшего общего кандидата",
        "",
        _frame_to_markdown(
            best_monthly,
            columns=["month_utc", "trades_count", "month_return_pct", "win_rate", "mean_return_pct"],
        ),
        "",
        "## Позднее sanity-окно",
        "",
        _frame_to_markdown(
            best_holdout,
            columns=[
                "split_id",
                "trades_count",
                "trades_per_year",
                "mean_return_pct",
                "win_rate",
                "annualized_unit_pnl_pct",
                "max_drawdown_pct",
                "positive_months_count",
            ],
        ),
        "",
        "## Контекст",
        "",
        "```json",
        json.dumps(context, ensure_ascii=False, indent=2),
        "```",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")


def build_hourly_asia_pump_session_short_edge_artifacts(
    *,
    session_id: str,
    output_dir: Path,
    cache_dir: Path,
    commission_rate: float = 0.0,
    symbols: Sequence[str] | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, object]:
    active_logger = logger or module_logger
    output_dir.mkdir(parents=True, exist_ok=True)
    session_start_hour_utc, session_end_hour_utc = _resolve_session_window(session_id)
    session_ru = {
        "asia": "Азии",
        "europe": "Европы",
        "america": "Америки",
    }[str(session_id).lower()]

    preparer = DataPreparer(Path(cache_dir))
    resolved_symbols = list(dict.fromkeys(symbols)) if symbols else preparer.list_symbols(Timeframe.M5)
    active_logger.info(
        "session-short-edge: stage=init session=%s session_hours=%s-%s symbols=%s output_dir=%s",
        session_id,
        session_start_hour_utc,
        session_end_hour_utc,
        len(resolved_symbols),
        output_dir,
    )

    selected_events = _build_session_anomalies(
        preparer=preparer,
        timeframe=Timeframe.M5,
        session_id=session_id,
        symbols=resolved_symbols,
        allowed_trigger_minutes=_ALL_5M_MINUTES,
        logger=active_logger,
    )
    if selected_events.empty:
        raise ValueError(f"Не найдено аномалий для сессии {session_id}")

    geometries = tuple({model.geometry.geometry_id: model.geometry for model in _build_execution_models()}.values())
    geometry_events = _build_geometry_events(
        preparer=preparer,
        selected_events=selected_events,
        geometries=tuple(geometries),
        commission_rate=float(commission_rate),
        logger=active_logger,
    )
    if geometry_events.empty:
        raise ValueError(f"Не удалось построить ни одной шортовой сделки для сессии {session_id}")
    if "trigger_minute" not in geometry_events.columns:
        geometry_events["trigger_minute"] = pd.to_datetime(
            pd.to_numeric(geometry_events["timestamp_ms"], errors="coerce"),
            unit="ms",
            utc=True,
            errors="coerce",
        ).dt.minute.astype("Int64")

    models = _build_execution_models()
    model_events_cache = _build_model_event_cache(
        geometry_events=geometry_events,
        models=models,
        logger=active_logger,
    )
    if not model_events_cache:
        raise ValueError(f"Не найдено ни одной рабочей модели для сессии {session_id}")

    calendar_months = _calendar_months_from_frames(selected_events)
    initial_filters = [*_build_exact_minute_filters(), *_build_standard_minute_filters()]
    initial_summary = _summarize_models_by_filters(
        model_events_cache=model_events_cache,
        models=models,
        minute_filters=initial_filters,
        calendar_months=calendar_months,
        logger=active_logger,
    )
    if initial_summary.empty:
        raise ValueError(f"Не удалось собрать minute summary для сессии {session_id}")

    best_exact_initial = _best_per_filter(initial_summary, filter_kind="exact")
    dynamic_filters = _build_dynamic_minute_filters(best_exact_initial)
    final_summary = initial_summary.copy()
    if dynamic_filters:
        dynamic_summary = _summarize_models_by_filters(
            model_events_cache=model_events_cache,
            models=models,
            minute_filters=dynamic_filters,
            calendar_months=calendar_months,
            logger=active_logger,
        )
        if not dynamic_summary.empty:
            final_summary = pd.concat([initial_summary, dynamic_summary], ignore_index=True)
            final_summary = final_summary.sort_values(
                ["meets_goal", "stability_score", "mean_return_pct", "win_rate", "trades_per_year"],
                ascending=[False, False, False, False, False],
            ).reset_index(drop=True)
            final_summary["candidate_rank"] = range(1, len(final_summary) + 1)

    best_overall = final_summary.head(1).copy()
    best_exact = _best_per_filter(final_summary, filter_kind="exact").sort_values(
        ["stability_score", "mean_return_pct", "trades_per_year"],
        ascending=[False, False, False],
    ).reset_index(drop=True)
    best_standard = _best_per_filter(final_summary, filter_kind="standard").sort_values(
        ["stability_score", "mean_return_pct", "trades_per_year"],
        ascending=[False, False, False],
    ).reset_index(drop=True)
    best_dynamic = _best_per_filter(final_summary, filter_kind="dynamic").sort_values(
        ["stability_score", "mean_return_pct", "trades_per_year"],
        ascending=[False, False, False],
    ).reset_index(drop=True)

    best_row = best_overall.iloc[0]
    best_model_events = model_events_cache[str(best_row["model_id"])].copy()
    best_events = _filter_events_by_minutes(
        best_model_events,
        [int(value) for value in str(best_row["allowed_minutes"]).split(",") if str(value).strip()],
    ).sort_values(["entry_timestamp_ms", "symbol"]).reset_index(drop=True)
    best_monthly = _build_monthly_detail(best_events, calendar_months=calendar_months)
    best_holdout = _build_holdout_summary(best_events, calendar_months=calendar_months)

    context = {
        "session_id": str(session_id),
        "session_ru": session_ru,
        "session_start_hour_utc": int(session_start_hour_utc),
        "session_end_hour_utc": int(session_end_hour_utc),
        "timeframe": Timeframe.M5.value,
        "symbols_count": int(len(resolved_symbols)),
        "selected_anomalies_count": int(len(selected_events)),
        "geometry_rows_count": int(len(geometry_events)),
        "models_count": int(len(models)),
        "minute_filters_initial_count": int(len(initial_filters)),
        "minute_filters_dynamic_count": int(len(dynamic_filters)),
        "commission_rate": float(commission_rate),
        "round_trip_taker_fee_pct": float(commission_rate * 2.0),
        "loose_universe": {
            "min_trigger_return_pct": _UNIVERSE_MIN_TRIGGER_RETURN_PCT,
            "min_range_atr": _UNIVERSE_MIN_RANGE_ATR,
            "min_volume_mult": _UNIVERSE_MIN_VOLUME_MULT,
            "max_close_to_high_frac": _UNIVERSE_MAX_CLOSE_TO_HIGH_FRAC,
        },
        "best_candidate": {
            "minute_filter_id": str(best_row["minute_filter_id"]),
            "allowed_minutes": str(best_row["allowed_minutes"]),
            "model_label": str(best_row["model_label"]),
            "rule_text": str(best_row["rule_text"]),
        },
    }

    session_prefix = f"{str(session_id).lower()}_short"
    artifacts = {
        "selected_events": output_dir / f"{session_prefix}_selected_events.csv",
        "geometry_events": output_dir / f"{session_prefix}_geometry_events.csv",
        "model_minute_summary": output_dir / f"{session_prefix}_model_minute_summary.csv",
        "best_overall": output_dir / f"{session_prefix}_best_overall.csv",
        "best_by_exact_minute": output_dir / f"{session_prefix}_best_by_exact_minute.csv",
        "best_by_standard_group": output_dir / f"{session_prefix}_best_by_group.csv",
        "best_by_dynamic_combo": output_dir / f"{session_prefix}_best_by_dynamic_combo.csv",
        "best_events": output_dir / f"{session_prefix}_best_events.csv",
        "best_monthly": output_dir / f"{session_prefix}_best_monthly.csv",
        "best_holdout": output_dir / f"{session_prefix}_best_holdout.csv",
        "context": output_dir / f"{session_prefix}_context.json",
        "report": output_dir / f"{session_prefix}_report.md",
    }
    selected_events.to_csv(artifacts["selected_events"], index=False)
    geometry_events.to_csv(artifacts["geometry_events"], index=False)
    final_summary.to_csv(artifacts["model_minute_summary"], index=False)
    best_overall.to_csv(artifacts["best_overall"], index=False)
    best_exact.to_csv(artifacts["best_by_exact_minute"], index=False)
    best_standard.to_csv(artifacts["best_by_standard_group"], index=False)
    best_dynamic.to_csv(artifacts["best_by_dynamic_combo"], index=False)
    best_events.to_csv(artifacts["best_events"], index=False)
    best_monthly.to_csv(artifacts["best_monthly"], index=False)
    best_holdout.to_csv(artifacts["best_holdout"], index=False)
    artifacts["context"].write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_report(
        report_path=artifacts["report"],
        context=context,
        best_overall=best_overall,
        best_exact=best_exact,
        best_standard=best_standard,
        best_dynamic=best_dynamic,
        best_monthly=best_monthly,
        best_holdout=best_holdout,
    )
    active_logger.info(
        "session-short-edge: stage=done session=%s best_filter=%s best_model=%s trades_per_year=%.2f mean_trade=%.2f%% equity9=%.2f%%",
        session_id,
        best_row["minute_filter_id"],
        best_row["model_label"],
        float(best_row["trades_per_year"]),
        float(best_row["mean_return_pct"]) * 100.0,
        float(best_row.get("equity_9_annualized_return_pct", 0.0) or 0.0) * 100.0,
    )
    return artifacts
