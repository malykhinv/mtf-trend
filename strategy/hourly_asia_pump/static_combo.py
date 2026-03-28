from __future__ import annotations

import hashlib
import itertools
import json
import logging
import math
import time
from pathlib import Path
from typing import Any, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import pandas as pd

from domain.enums.timeframe import Timeframe
from strategy.hourly_asia_pump.static_combo_trade_plotter import (
    StaticComboTradePlotSpec,
    StaticComboTradePlotter,
)
from vectorbt_runner.data_preparer import DataPreparer

_STATIC_TOP_K_VALUES: tuple[int, ...] = (99, 1, 2)
_STATIC_MIN_COMPONENTS = 2
_STATIC_MAX_COMPONENTS = 5
_STATIC_MIN_CANDIDATE_EVENTS = 6
_STATIC_MIN_TRADES_PER_YEAR = 50.0
_STATIC_MIN_MEAN_RETURN_PCT = 0.02
_STATIC_MIN_WIN_RATE = 0.40
_STATIC_MIN_ANNUALIZED_UNIT_PNL_PCT = 1.0
_STATIC_MAX_DRAWDOWN_PCT = 0.30
_STATIC_MIN_POSITIVE_MONTHS = 9
_STATIC_HOLDOUT_SPLITS: tuple[tuple[str, int], ...] = (
    ("train8_test4", 8),
    ("train9_test3", 9),
)
_STATIC_PRIORITY_TOP_N_VALUES: tuple[int, ...] = (1, 3, 5, 10, 20, 50, 100)
_STATIC_LOCAL_COMPONENT_DISTANCE_MAX = 2
_STATIC_PROGRESS_LOG_EVERY = 100
_STATIC_FROZEN_THRESHOLD_VERSION = "2026-03-28-v1"
_STATIC_MB5_01_SHALLOW_PULLBACK_FRAC_MAX = 0.0
_STATIC_MB3_03_TRIGGER_RETURN_PCT_Q75_MIN = 0.08412202070738665
_STATIC_SM75_00_INITIAL_RISK_PCT_Q25_MAX = 0.0919540229885057
_STATIC_MB5_06_SHALLOW_PULLBACK_FRAC_MAX = 0.0


def _safe_numeric(value: object) -> float | None:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, bool):
        return float(value)
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric):
        return None
    return numeric


def _format_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return "н/д"
    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_value(column_name: str, value: object) -> str:
    if value is None or value is pd.NA:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    numeric = _safe_numeric(value)
    if numeric is None:
        return str(value)
    if column_name.endswith("_rate") or column_name.endswith("_pct"):
        return f"{numeric * 100:.2f}%"
    return f"{numeric:.4f}" if not float(numeric).is_integer() else f"{int(numeric)}"


def _frame_to_markdown(
    frame: pd.DataFrame,
    *,
    columns: Sequence[str],
    limit: int | None = None,
    header_labels: dict[str, str] | None = None,
) -> str:
    if frame.empty:
        return "_Нет данных._"
    prepared = frame.copy()
    existing_columns = [column for column in columns if column in prepared.columns]
    if existing_columns:
        prepared = prepared[existing_columns]
    if limit is not None and limit >= 0:
        prepared = prepared.head(limit)
    display_headers = [header_labels.get(str(column), str(column)) if header_labels else str(column) for column in prepared.columns]
    lines = [
        "| " + " | ".join(display_headers) + " |",
        "| " + " | ".join("---" for _ in prepared.columns) + " |",
    ]
    for _, row in prepared.iterrows():
        lines.append(
            "| "
            + " | ".join(_format_value(str(column), row[column]) for column in prepared.columns)
            + " |"
        )
    return "\n".join(lines)


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _normalize_calendar_months(months: Sequence[str]) -> list[str]:
    cleaned = [str(month).strip() for month in months if str(month).strip()]
    if not cleaned:
        return []
    periods = pd.PeriodIndex(cleaned, freq="M")
    full_periods = pd.period_range(periods.min(), periods.max(), freq="M")
    return [period.strftime("%Y-%m") for period in full_periods]


def _calendar_months_from_frames(*frames: pd.DataFrame) -> list[str]:
    months: list[str] = []
    for frame in frames:
        if frame.empty or "month_utc" not in frame.columns:
            continue
        months.extend(frame["month_utc"].dropna().astype(str).tolist())
    return _normalize_calendar_months(months)


def _monthly_returns_series(
    frame: pd.DataFrame,
    *,
    calendar_months: Sequence[str] | None = None,
) -> pd.Series:
    if frame.empty or "month_utc" not in frame.columns or "exit_return_pct" not in frame.columns:
        if calendar_months:
            return pd.Series(0.0, index=list(calendar_months), dtype="float64")
        return pd.Series(dtype="float64")
    monthly = frame.groupby("month_utc", sort=True)["exit_return_pct"].sum()
    if calendar_months:
        return monthly.reindex(list(calendar_months), fill_value=0.0).astype("float64")
    return monthly.astype("float64")


def _profit_factor(returns: pd.Series) -> float | None:
    if returns.empty:
        return None
    wins = float(returns[returns > 0].sum())
    losses = float(returns[returns < 0].sum())
    if losses == 0.0:
        return math.inf if wins > 0.0 else None
    return wins / abs(losses)


def _compute_overlap_stats(frame: pd.DataFrame) -> tuple[int | None, int | None]:
    if frame.empty:
        return None, None
    entry_source = frame["entry_timestamp_ms"] if "entry_timestamp_ms" in frame.columns else pd.Series(index=frame.index, dtype="float64")
    exit_source = frame["exit_timestamp_ms"] if "exit_timestamp_ms" in frame.columns else pd.Series(index=frame.index, dtype="float64")
    source_exit_source = (
        frame["source_exit_timestamp_ms"] if "source_exit_timestamp_ms" in frame.columns else pd.Series(index=frame.index, dtype="float64")
    )
    entry_series = pd.to_numeric(entry_source, errors="coerce")
    exit_series = pd.to_numeric(exit_source, errors="coerce")
    if exit_series.isna().all():
        exit_series = pd.to_numeric(source_exit_source, errors="coerce")
    scoped = pd.DataFrame(
        {
            "entry_timestamp_ms": entry_series,
            "exit_timestamp_ms": exit_series,
        }
    ).dropna(subset=["entry_timestamp_ms"])
    if scoped.empty:
        return None, None
    scoped["entry_timestamp_ms"] = scoped["entry_timestamp_ms"].astype("int64")
    scoped["exit_timestamp_ms"] = scoped["exit_timestamp_ms"].fillna(scoped["entry_timestamp_ms"]).astype("int64")
    scoped = scoped.sort_values(["entry_timestamp_ms", "exit_timestamp_ms"]).reset_index(drop=True)

    active_exit_times: list[int] = []
    max_concurrent = 0
    overlapping_entries = 0
    for _, row in scoped.iterrows():
        entry_timestamp_ms = int(row["entry_timestamp_ms"])
        exit_timestamp_ms = int(max(row["exit_timestamp_ms"], entry_timestamp_ms))
        active_exit_times = [timestamp for timestamp in active_exit_times if timestamp > entry_timestamp_ms]
        if active_exit_times:
            overlapping_entries += 1
        active_exit_times.append(exit_timestamp_ms)
        max_concurrent = max(max_concurrent, len(active_exit_times))
    return max_concurrent, overlapping_entries


def _dedupe_source_events(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    # Resolve duplicate source rows conservatively: when the same event appears
    # under multiple configs, keep the weakest realized outcome instead of the best.
    ordered = frame.sort_values(["symbol", "timestamp_ms", "exit_return_pct"], ascending=[True, True, True]).copy()
    return ordered.drop_duplicates(["symbol", "timestamp_ms"]).reset_index(drop=True)


def _prepare_base_events(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if frame.empty:
        return frame
    frame = frame[frame.get("trade_triggered", pd.Series(False, index=frame.index)).astype(str).str.lower() == "true"].copy()
    frame["entry_timestamp_ms"] = pd.to_numeric(frame.get("entry_timestamp_ms"), errors="coerce")
    frame["entry_timestamp_utc"] = pd.to_datetime(frame.get("entry_timestamp_utc"), utc=True, errors="coerce")
    frame["month_utc"] = frame["entry_timestamp_utc"].dt.strftime("%Y-%m")
    frame["date_utc"] = frame["entry_timestamp_utc"].dt.strftime("%Y-%m-%d")
    return frame


def _prepare_confirmed_events(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if frame.empty:
        return frame
    frame = frame[frame.get("trade_triggered", pd.Series(False, index=frame.index)).astype(str).str.lower() == "true"].copy()
    frame["entry_timestamp_ms"] = pd.to_numeric(frame.get("entry_timestamp_ms"), errors="coerce")
    frame["entry_timestamp_utc"] = pd.to_datetime(frame.get("entry_timestamp_utc"), utc=True, errors="coerce")
    frame["month_utc"] = frame["entry_timestamp_utc"].dt.strftime("%Y-%m")
    frame["date_utc"] = frame["entry_timestamp_utc"].dt.strftime("%Y-%m-%d")
    next_high = pd.to_numeric(frame.get("next_bar_high_price"), errors="coerce")
    next_low = pd.to_numeric(frame.get("next_bar_low_price"), errors="coerce")
    next_close = pd.to_numeric(frame.get("next_bar_close_price"), errors="coerce")
    trigger_high = pd.to_numeric(frame.get("trigger_high"), errors="coerce")
    trigger_low = pd.to_numeric(frame.get("trigger_low"), errors="coerce")
    next_range = (next_high - next_low).replace(0, pd.NA)
    trigger_range = (trigger_high - trigger_low).replace(0, pd.NA)
    frame["next_close_to_high_frac"] = (next_high - next_close) / next_range
    frame["next_range_vs_trigger"] = next_range / trigger_range
    return frame


def _build_static_candidate_frames(
    *,
    base_events: pd.DataFrame,
    confirmed_events: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    candidates: dict[str, pd.DataFrame] = {}

    def _add_candidate(candidate_id: str, frame: pd.DataFrame) -> None:
        deduped = _dedupe_source_events(frame)
        if len(deduped) >= _STATIC_MIN_CANDIDATE_EVENTS:
            candidates[candidate_id] = deduped

    base = base_events.copy()
    confirmed = confirmed_events.copy()

    mb5_01 = base[
        (base.get("trade_model_id", pd.Series(dtype="object")).astype(str) == "monster_break_5pct")
        & (pd.to_numeric(base.get("hour_utc"), errors="coerce") == 1)
    ].copy()
    mb5_01_pullback = _numeric_series(mb5_01, "pre_entry_pullback_frac")
    _add_candidate("mb5_01_shallow", mb5_01[mb5_01_pullback <= _STATIC_MB5_01_SHALLOW_PULLBACK_FRAC_MAX])

    mb3_03 = base[
        (base.get("trade_model_id", pd.Series(dtype="object")).astype(str) == "monster_break_3pct")
        & (pd.to_numeric(base.get("hour_utc"), errors="coerce") == 3)
    ].copy()
    mb3_03_trigger = _numeric_series(mb3_03, "trigger_return_pct")
    _add_candidate("mb3_03_trg_q75", mb3_03[mb3_03_trigger >= _STATIC_MB3_03_TRIGGER_RETURN_PCT_Q75_MIN])
    _add_candidate("mb3_03_raw", mb3_03)

    sm75_00 = base[
        (base.get("trade_model_id", pd.Series(dtype="object")).astype(str) == "super_monster_7p5pct")
        & (pd.to_numeric(base.get("hour_utc"), errors="coerce") == 0)
    ].copy()
    sm75_risk = _numeric_series(sm75_00, "initial_risk_pct")
    _add_candidate("sm75_00_risk_q25", sm75_00[sm75_risk <= _STATIC_SM75_00_INITIAL_RISK_PCT_Q25_MAX])

    mb5_06 = base[
        (base.get("trade_model_id", pd.Series(dtype="object")).astype(str) == "monster_break_5pct")
        & (pd.to_numeric(base.get("hour_utc"), errors="coerce") == 6)
    ].copy()
    mb5_06_pullback = _numeric_series(mb5_06, "pre_entry_pullback_frac")
    _add_candidate("mb5_06_shallow", mb5_06[mb5_06_pullback <= _STATIC_MB5_06_SHALLOW_PULLBACK_FRAC_MAX])

    _add_candidate(
        "mb5_03_raw",
        base[
            (base.get("trade_model_id", pd.Series(dtype="object")).astype(str) == "monster_break_5pct")
            & (pd.to_numeric(base.get("hour_utc"), errors="coerce") == 3)
        ].copy(),
    )
    _add_candidate(
        "tf7_raw",
        base[
            (base.get("trade_model_id", pd.Series(dtype="object")).astype(str) == "tight_flag_runner")
            & (pd.to_numeric(base.get("hour_utc"), errors="coerce") == 7)
        ].copy(),
    )

    confirmed = confirmed[pd.to_numeric(confirmed.get("hour_utc"), errors="coerce") == 0].copy()
    shared_confirmed_filter = (
        (confirmed.get("stop_style", pd.Series(dtype="object")).astype(str) == "next_low")
        & (pd.to_numeric(confirmed.get("next_close_to_high_frac"), errors="coerce") <= 0.5)
        & (pd.to_numeric(confirmed.get("pre_base_range_pct_60m"), errors="coerce") <= 0.06)
    )
    _add_candidate(
        "conf00_trg65_pb50",
        confirmed[
            shared_confirmed_filter
            & (pd.to_numeric(confirmed.get("trigger_return_pct"), errors="coerce") >= 0.065)
            & (pd.to_numeric(confirmed.get("next_bar_pullback_frac"), errors="coerce") <= 0.5)
            & (pd.to_numeric(confirmed.get("next_range_vs_trigger"), errors="coerce") <= 2.0)
        ].copy(),
    )
    _add_candidate(
        "conf00_trg75_pb50",
        confirmed[
            shared_confirmed_filter
            & (pd.to_numeric(confirmed.get("trigger_return_pct"), errors="coerce") >= 0.075)
            & (pd.to_numeric(confirmed.get("next_bar_pullback_frac"), errors="coerce") <= 0.5)
            & (pd.to_numeric(confirmed.get("next_range_vs_trigger"), errors="coerce") <= 2.0)
        ].copy(),
    )
    _add_candidate(
        "conf00_trg65_pb25_rng12",
        confirmed[
            shared_confirmed_filter
            & (pd.to_numeric(confirmed.get("trigger_return_pct"), errors="coerce") >= 0.065)
            & (pd.to_numeric(confirmed.get("next_bar_pullback_frac"), errors="coerce") <= 0.25)
            & (pd.to_numeric(confirmed.get("next_range_vs_trigger"), errors="coerce") <= 1.2)
        ].copy(),
    )

    return candidates


def _summarize_events(
    frame: pd.DataFrame,
    *,
    calendar_months: Sequence[str] | None = None,
) -> dict[str, object] | None:
    if frame.empty and not calendar_months:
        return None
    ordered = frame.sort_values("entry_timestamp_ms").copy() if not frame.empty else frame.copy()
    returns = pd.to_numeric(ordered.get("exit_return_pct"), errors="coerce").dropna()

    normalized_calendar_months = _normalize_calendar_months(calendar_months or [])
    if normalized_calendar_months:
        periods = pd.PeriodIndex(normalized_calendar_months, freq="M")
        start_timestamp = periods.min().to_timestamp(how="start").tz_localize("UTC")
        end_timestamp = (periods.max() + 1).to_timestamp(how="start").tz_localize("UTC")
        span_days = max(1.0, (end_timestamp - start_timestamp).total_seconds() / 86_400)
    else:
        entry_timestamps = pd.to_datetime(ordered.get("entry_timestamp_ms"), unit="ms", utc=True, errors="coerce").dropna()
        if len(entry_timestamps) >= 2:
            span_days = max(1.0, (entry_timestamps.max() - entry_timestamps.min()).total_seconds() / 86_400)
        else:
            span_days = float(max(1, ordered.get("date_utc", pd.Series(dtype="object")).nunique()))

    monthly_returns = _monthly_returns_series(ordered, calendar_months=normalized_calendar_months)
    equity_curve = 1.0 + returns.cumsum() if not returns.empty else pd.Series([1.0], dtype="float64")
    drawdown = ((equity_curve.cummax() - equity_curve) / equity_curve.cummax().replace(0, pd.NA)).fillna(0.0)
    max_concurrent_trades, overlapping_entries_count = _compute_overlap_stats(ordered)
    annual_sum_return_pct = float(returns.sum()) if not returns.empty else 0.0
    annualized_unit_pnl_pct = float(annual_sum_return_pct * (12.0 / max(1, len(monthly_returns))))

    return {
        "trades_count": int(len(ordered)),
        "trade_days_count": int(ordered.get("date_utc", pd.Series(dtype="object")).nunique()),
        "trades_per_year": float((len(ordered) / span_days) * 365.0),
        "mean_return_pct": float(returns.mean()) if not returns.empty else 0.0,
        "median_return_pct": float(returns.median()) if not returns.empty else 0.0,
        "win_rate": float((returns > 0).mean()) if not returns.empty else 0.0,
        "profit_factor": _profit_factor(returns),
        "annual_sum_return_pct": annual_sum_return_pct,
        "annualized_sum_return_pct": annualized_unit_pnl_pct,
        "unit_pnl_sum_pct": annual_sum_return_pct,
        "annualized_unit_pnl_pct": annualized_unit_pnl_pct,
        "mean_pos_trade_pct": float(returns[returns > 0].mean()) if (returns > 0).any() else None,
        "mean_neg_trade_pct": float(returns[returns < 0].mean()) if (returns < 0).any() else None,
        "positive_months_count": int((monthly_returns > 0).sum()),
        "non_positive_months_count": int((monthly_returns <= 0).sum()),
        "all_active_months_positive": bool((monthly_returns > 0).all()) if not monthly_returns.empty else False,
        "best_month_return_pct": float(monthly_returns.max()) if not monthly_returns.empty else None,
        "worst_month_return_pct": float(monthly_returns.min()) if not monthly_returns.empty else None,
        "max_drawdown_pct": float(drawdown.max()) if not drawdown.empty else None,
        "max_concurrent_trades": max_concurrent_trades,
        "overlapping_entries_count": overlapping_entries_count,
    }


def _sort_summary_frame(
    frame: pd.DataFrame,
    *,
    sort_columns: list[str],
    ascending: list[bool],
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    available_columns = [column for column in sort_columns if column in frame.columns]
    if not available_columns:
        return frame.reset_index(drop=True)
    available_ascending = [ascending[sort_columns.index(column)] for column in available_columns]
    return frame.sort_values(available_columns, ascending=available_ascending, na_position="last").reset_index(drop=True)


def _build_combo_id(component_ids: list[str], top_k_per_timestamp: int) -> str:
    return f"{'|'.join(component_ids)}__top{top_k_per_timestamp}"


def _build_combo_events(
    *,
    candidate_frames: dict[str, pd.DataFrame],
    component_ids: list[str],
    top_k_per_timestamp: int,
) -> pd.DataFrame:
    component_parts: list[pd.DataFrame] = []
    for component_id in component_ids:
        part = candidate_frames[component_id].copy()
        part["combo_component_id"] = component_id
        component_parts.append(part)
    if not component_parts:
        return pd.DataFrame()

    combined = pd.concat(component_parts, ignore_index=True)
    rows: list[dict[str, object]] = []
    grouped = combined.groupby(["symbol", "timestamp_ms"], sort=True)
    for (symbol, timestamp_ms), group in grouped:
        sorted_group = group.sort_values(["entry_timestamp_ms", "combo_component_id"], na_position="last").copy()
        exit_returns = pd.to_numeric(sorted_group.get("exit_return_pct"), errors="coerce").dropna()
        if exit_returns.empty:
            continue
        sorted_group["_exit_return_pct_numeric"] = pd.to_numeric(sorted_group.get("exit_return_pct"), errors="coerce")
        representative = sorted_group.sort_values(
            ["_exit_return_pct_numeric", "entry_timestamp_ms", "combo_component_id"],
            ascending=[True, True, True],
            na_position="last",
        ).iloc[0]
        target_return = float(representative["_exit_return_pct_numeric"])
        matched_return_min = float(exit_returns.min())
        matched_return_mean = float(exit_returns.mean())
        matched_return_max = float(exit_returns.max())
        entry_price = _safe_numeric(representative.get("entry_price"))
        if entry_price is None:
            entry_price = _safe_numeric(representative.get("next_bar_open_price"))
        stop_price = _safe_numeric(representative.get("initial_stop_price"))
        if stop_price is None:
            stop_price = _safe_numeric(representative.get("next_bar_low_price"))
        exit_price = None
        if entry_price is not None:
            exit_price = entry_price * (1.0 + target_return)
        rows.append(
            {
                "symbol": str(symbol),
                "timestamp_ms": int(timestamp_ms),
                "entry_timestamp_ms": representative.get("entry_timestamp_ms"),
                "entry_timestamp_utc": representative.get("entry_timestamp_utc"),
                "date_utc": representative.get("date_utc"),
                "month_utc": representative.get("month_utc"),
                "hour_utc": representative.get("hour_utc"),
                "combo_matched_component_count": int(sorted_group["combo_component_id"].nunique()),
                "combo_matched_component_ids": ",".join(sorted(sorted_group["combo_component_id"].astype(str).unique().tolist())),
                "combo_return_resolution": "conservative_min",
                "combo_matched_return_min_pct": matched_return_min,
                "combo_matched_return_mean_pct": matched_return_mean,
                "combo_matched_return_max_pct": matched_return_max,
                "combo_matched_return_spread_pct": float(matched_return_max - matched_return_min),
                "exit_return_pct": target_return,
                "trigger_return_pct": float(pd.to_numeric(sorted_group.get("trigger_return_pct"), errors="coerce").max()),
                "volume_mult": float(pd.to_numeric(sorted_group.get("volume_mult"), errors="coerce").max()),
                "range_atr": float(pd.to_numeric(sorted_group.get("range_atr"), errors="coerce").max()),
                "source_timeframe": representative.get("timeframe"),
                "source_trade_model_id": representative.get("trade_model_id"),
                "source_trade_model_label": representative.get("trade_model_label"),
                "source_config_id": representative.get("config_id"),
                "source_hour_utc": representative.get("hour_utc"),
                "source_entry_timestamp_ms": representative.get("entry_timestamp_ms"),
                "source_entry_timestamp_utc": representative.get("entry_timestamp_utc"),
                "source_entry_price": entry_price,
                "source_entry_reason": representative.get("entry_reason"),
                "source_stop_price": stop_price,
                "source_initial_stop_reason": representative.get("initial_stop_reason"),
                "source_initial_risk_pct": _safe_numeric(representative.get("initial_risk_pct")),
                "source_exit_timestamp_ms": representative.get("exit_timestamp_ms"),
                "source_exit_timestamp_utc": representative.get("exit_timestamp_utc"),
                "source_exit_price": exit_price,
                "source_exit_reason": representative.get("exit_reason"),
                "source_trigger_open": _safe_numeric(representative.get("trigger_open")),
                "source_trigger_high": _safe_numeric(representative.get("trigger_high")),
                "source_trigger_low": _safe_numeric(representative.get("trigger_low")),
                "source_trigger_close": _safe_numeric(representative.get("trigger_close")),
                "source_trigger_return_pct": _safe_numeric(representative.get("trigger_return_pct")),
                "source_trigger_range_pct": _safe_numeric(representative.get("trigger_range_pct")),
                "source_range_atr": _safe_numeric(representative.get("range_atr")),
                "source_body_atr": _safe_numeric(representative.get("body_atr")),
                "source_volume_mult": _safe_numeric(representative.get("volume_mult")),
                "source_close_to_high_frac": _safe_numeric(representative.get("close_to_high_frac")),
                "source_peak_timestamp_ms": _safe_numeric(representative.get("peak_timestamp_ms")),
                "source_peak_timestamp_utc": representative.get("peak_timestamp_utc"),
                "source_peak_price_before_50pct_retrace": _safe_numeric(representative.get("peak_price_before_50pct_retrace")),
                "source_peak_return_pct": _safe_numeric(representative.get("peak_return_pct")),
                "source_continuation_peak_return_pct": _safe_numeric(representative.get("continuation_peak_return_pct")),
                "source_pre_base_range_pct_60m": _safe_numeric(representative.get("pre_base_range_pct_60m")),
                "source_pre_base_drift_pct_60m": _safe_numeric(representative.get("pre_base_drift_pct_60m")),
                "source_pre_base_range_vs_trigger": _safe_numeric(representative.get("pre_base_range_vs_trigger")),
                "source_pre_entry_pullback_frac": _safe_numeric(representative.get("pre_entry_pullback_frac")),
                "source_pre_entry_red_volume_frac": _safe_numeric(representative.get("pre_entry_red_volume_frac")),
                "source_next_bar_pullback_frac": _safe_numeric(representative.get("next_bar_pullback_frac")),
                "source_next_close_to_high_frac": _safe_numeric(representative.get("next_close_to_high_frac")),
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    if top_k_per_timestamp != 99:
        ranked = frame.sort_values(["timestamp_ms", "trigger_return_pct", "volume_mult", "range_atr"], ascending=[True, False, False, False]).copy()
        ranked["rank_in_timestamp"] = ranked.groupby("timestamp_ms").cumcount() + 1
        frame = ranked[ranked["rank_in_timestamp"] <= top_k_per_timestamp].copy()
    frame["combo_id"] = _build_combo_id(component_ids, top_k_per_timestamp)
    frame["component_ids"] = ",".join(component_ids)
    frame["component_count"] = len(component_ids)
    frame["top_k_per_timestamp"] = top_k_per_timestamp
    return frame.reset_index(drop=True)


def _build_event_signature(frame: pd.DataFrame) -> str:
    if frame.empty:
        return ""
    payload = "\n".join(
        sorted(
            f"{row.symbol}|{int(row.timestamp_ms)}|{round(float(row.exit_return_pct), 8)}"
            for row in frame[["symbol", "timestamp_ms", "exit_return_pct"]].itertuples(index=False)
        )
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _meets_static_goal(summary_row: pd.Series) -> bool:
    return bool(
        pd.to_numeric(summary_row.get("trades_per_year"), errors="coerce") >= _STATIC_MIN_TRADES_PER_YEAR
        and pd.to_numeric(summary_row.get("mean_return_pct"), errors="coerce") >= _STATIC_MIN_MEAN_RETURN_PCT
        and pd.to_numeric(summary_row.get("win_rate"), errors="coerce") > _STATIC_MIN_WIN_RATE
        and pd.to_numeric(summary_row.get("annualized_unit_pnl_pct"), errors="coerce") >= _STATIC_MIN_ANNUALIZED_UNIT_PNL_PCT
        and pd.to_numeric(summary_row.get("max_drawdown_pct"), errors="coerce") <= _STATIC_MAX_DRAWDOWN_PCT
        and pd.to_numeric(summary_row.get("positive_months_count"), errors="coerce") >= _STATIC_MIN_POSITIVE_MONTHS
    )


def _priority_score(summary_row: pd.Series) -> float:
    mean_return = float(pd.to_numeric(summary_row.get("mean_return_pct"), errors="coerce") or 0.0)
    win_rate = float(pd.to_numeric(summary_row.get("win_rate"), errors="coerce") or 0.0)
    trades_per_year = float(pd.to_numeric(summary_row.get("trades_per_year"), errors="coerce") or 0.0)
    annualized_sum = float(pd.to_numeric(summary_row.get("annualized_unit_pnl_pct"), errors="coerce") or 0.0)
    max_drawdown = float(pd.to_numeric(summary_row.get("max_drawdown_pct"), errors="coerce") or 1.0)
    positive_months = float(pd.to_numeric(summary_row.get("positive_months_count"), errors="coerce") or 0.0)
    return (
        mean_return * 4.0
        + win_rate * 1.5
        + min(trades_per_year, 100.0) * 0.01
        + annualized_sum * 0.5
        + positive_months * 0.05
        - max_drawdown * 1.25
    )


def _rank_combo_summary(combo_summary: pd.DataFrame) -> pd.DataFrame:
    if combo_summary.empty:
        return combo_summary.copy()
    ranked = combo_summary.copy()
    ranked["meets_goal"] = ranked.apply(_meets_static_goal, axis=1)
    ranked["priority_score"] = ranked.apply(_priority_score, axis=1)
    ranked = ranked.sort_values(
        [
            "meets_goal",
            "priority_score",
            "mean_return_pct",
            "annualized_unit_pnl_pct",
            "win_rate",
            "trades_per_year",
        ],
        ascending=[False, False, False, False, False, False],
        na_position="last",
    ).reset_index(drop=True)
    return ranked


def _index_to_variant_label(index: int) -> str:
    if index < 0:
        raise ValueError("Variant index must be non-negative")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    label = ""
    current = index
    while True:
        current, remainder = divmod(current, len(alphabet))
        label = alphabet[remainder] + label
        if current == 0:
            return label
        current -= 1


def _assign_combo_variants(great_combos: pd.DataFrame) -> pd.DataFrame:
    if great_combos.empty:
        return great_combos.copy()
    labeled = great_combos.copy().reset_index(drop=True)
    labeled["combo_variant"] = [_index_to_variant_label(idx) for idx in range(len(labeled))]
    labeled["combo_priority"] = labeled.index + 1
    return labeled


def _build_priority_selected_events(
    *,
    combo_events: pd.DataFrame,
    combo_catalog: pd.DataFrame,
) -> pd.DataFrame:
    if combo_events.empty or combo_catalog.empty:
        return pd.DataFrame()
    priority_map = {
        str(row["combo_id"]): {
            "combo_variant": str(row["combo_variant"]),
            "combo_priority": int(row["combo_priority"]),
            "priority_score": float(row["priority_score"]),
        }
        for row in combo_catalog.to_dict("records")
    }
    scoped = combo_events[combo_events["combo_id"].astype(str).isin(priority_map)].copy()
    if scoped.empty:
        return scoped
    scoped["combo_priority"] = scoped["combo_id"].astype(str).map(lambda combo_id: priority_map[combo_id]["combo_priority"])
    scoped["combo_variant"] = scoped["combo_id"].astype(str).map(lambda combo_id: priority_map[combo_id]["combo_variant"])
    scoped["combo_priority_score"] = scoped["combo_id"].astype(str).map(lambda combo_id: priority_map[combo_id]["priority_score"])
    grouped = scoped.groupby(["symbol", "timestamp_ms"], sort=True)
    rows: list[dict[str, object]] = []
    for (_, _), group in grouped:
        sorted_group = group.sort_values(["combo_priority", "combo_priority_score"], ascending=[True, False]).copy()
        chosen = sorted_group.iloc[0]
        matched_combo_ids = sorted_group["combo_id"].astype(str).tolist()
        matched_combo_variants = sorted_group["combo_variant"].astype(str).tolist()
        row = chosen.to_dict()
        row["matched_combo_count"] = int(len(sorted_group))
        row["matched_combo_ids"] = ",".join(matched_combo_ids)
        row["matched_combo_variants"] = ",".join(matched_combo_variants)
        rows.append(row)
    return pd.DataFrame(rows)


def _build_holdout_sanity_rows(
    *,
    combo_summary: pd.DataFrame,
    combo_events_by_id: dict[str, pd.DataFrame],
    calendar_months: Sequence[str],
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if combo_summary.empty:
        return pd.DataFrame()
    normalized_calendar_months = _normalize_calendar_months(calendar_months)
    for combo in combo_summary.to_dict("records"):
        combo_id = str(combo["combo_id"])
        events = combo_events_by_id.get(combo_id)
        if events is None:
            continue
        for split_id, train_months_count in _STATIC_HOLDOUT_SPLITS:
            if len(normalized_calendar_months) <= train_months_count:
                continue
            train_months = normalized_calendar_months[:train_months_count]
            test_months = normalized_calendar_months[train_months_count:]
            test_events = events[events["month_utc"].astype(str).isin(test_months)].copy()
            summary = _summarize_events(test_events, calendar_months=test_months)
            rows.append(
                {
                    "combo_id": combo_id,
                    "combo_variant": combo.get("combo_variant"),
                    "split_id": split_id,
                    "train_start_month_utc": train_months[0],
                    "train_end_month_utc": train_months[-1],
                    "test_start_month_utc": test_months[0],
                    "test_end_month_utc": test_months[-1],
                    **summary,
                }
            )
    return pd.DataFrame(rows)


def _empty_priority_summary() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "trades_count",
            "trade_days_count",
            "trades_per_year",
            "mean_return_pct",
            "median_return_pct",
            "win_rate",
            "profit_factor",
            "annual_sum_return_pct",
            "annualized_sum_return_pct",
            "unit_pnl_sum_pct",
            "annualized_unit_pnl_pct",
            "mean_pos_trade_pct",
            "mean_neg_trade_pct",
            "positive_months_count",
            "non_positive_months_count",
            "all_active_months_positive",
            "best_month_return_pct",
            "worst_month_return_pct",
            "max_drawdown_pct",
            "max_concurrent_trades",
            "overlapping_entries_count",
            "matched_combo_variants_count",
        ]
    )


def _parse_component_ids(raw_value: object) -> tuple[str, ...]:
    if raw_value is None or raw_value is pd.NA:
        return tuple()
    parts = [part.strip() for part in str(raw_value).split(",")]
    return tuple(part for part in parts if part)


def _top_k_rank(top_k_value: object) -> int:
    try:
        parsed = int(top_k_value)
    except (TypeError, ValueError):
        return 99
    order = {1: 0, 2: 1, 99: 2}
    return order.get(parsed, 99)


def _build_priority_topn_summary(
    *,
    great_combos: pd.DataFrame,
    combo_events: pd.DataFrame,
    calendar_months: Sequence[str],
) -> pd.DataFrame:
    if great_combos.empty or combo_events.empty:
        return pd.DataFrame()

    top_n_values = [value for value in _STATIC_PRIORITY_TOP_N_VALUES if value < len(great_combos)]
    top_n_values.append(len(great_combos))
    rows: list[dict[str, object]] = []
    for top_n in sorted(set(top_n_values)):
        scoped_catalog = great_combos.head(top_n).copy()
        priority_events = _build_priority_selected_events(combo_events=combo_events, combo_catalog=scoped_catalog)
        summary = _summarize_events(priority_events, calendar_months=calendar_months)
        if summary is None:
            continue
        rows.append(
            {
                "top_n_variants": int(top_n),
                "top_variant": str(scoped_catalog.iloc[0]["combo_variant"]),
                "last_variant": str(scoped_catalog.iloc[-1]["combo_variant"]),
                "selected_event_count": int(len(priority_events)),
                **summary,
            }
        )
    return pd.DataFrame(rows)


def _quantile_value(series: pd.Series, quantile: float) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return float(clean.quantile(quantile))


def _build_combo_robustness_summary(
    *,
    selected_combos: pd.DataFrame,
    all_unique_combos: pd.DataFrame,
) -> pd.DataFrame:
    if selected_combos.empty or all_unique_combos.empty:
        return pd.DataFrame()

    universe = all_unique_combos.copy().reset_index(drop=True)
    universe["component_tuple"] = universe.get("component_ids", pd.Series(dtype="object")).apply(_parse_component_ids)
    universe["component_set"] = universe["component_tuple"].apply(set)
    universe["top_k_rank"] = universe.get("top_k_per_timestamp", pd.Series(dtype="float64")).apply(_top_k_rank)

    rows: list[dict[str, object]] = []
    for combo in selected_combos.to_dict("records"):
        combo_id = str(combo.get("combo_id", ""))
        component_tuple = _parse_component_ids(combo.get("component_ids"))
        component_set = set(component_tuple)
        if not component_set:
            continue
        top_k_rank = _top_k_rank(combo.get("top_k_per_timestamp"))
        neighbors = universe[universe.get("combo_id", pd.Series(dtype="object")).astype(str) != combo_id].copy()
        neighbors["component_distance"] = neighbors["component_set"].apply(
            lambda other_set: len(component_set.symmetric_difference(other_set))
        )
        neighbors["same_components"] = neighbors["component_tuple"].apply(lambda other_tuple: other_tuple == component_tuple)
        neighbors["minor_component_change"] = (~neighbors["same_components"]) & (
            neighbors["component_distance"] <= _STATIC_LOCAL_COMPONENT_DISTANCE_MAX
        )
        neighbors["top_k_step_distance"] = (neighbors["top_k_rank"] - top_k_rank).abs()

        local_neighbors = neighbors[neighbors["same_components"] | neighbors["minor_component_change"]].copy()
        same_component_neighbors = local_neighbors[local_neighbors["same_components"]].copy()
        minor_change_neighbors = local_neighbors[local_neighbors["minor_component_change"]].copy()

        local_goal_rate = float(pd.to_numeric(local_neighbors.get("meets_goal"), errors="coerce").fillna(0.0).mean()) if not local_neighbors.empty else None
        minor_change_goal_rate = float(pd.to_numeric(minor_change_neighbors.get("meets_goal"), errors="coerce").fillna(0.0).mean()) if not minor_change_neighbors.empty else None
        local_mean_p25 = _quantile_value(local_neighbors.get("mean_return_pct", pd.Series(dtype="float64")), 0.25)
        local_dd_p75 = _quantile_value(local_neighbors.get("max_drawdown_pct", pd.Series(dtype="float64")), 0.75)
        local_ann_median = _quantile_value(local_neighbors.get("annualized_unit_pnl_pct", pd.Series(dtype="float64")), 0.50)

        robustness_label = "fragile"
        if (
            local_goal_rate is not None
            and local_mean_p25 is not None
            and local_dd_p75 is not None
            and local_ann_median is not None
        ):
            if local_goal_rate >= 0.60 and local_mean_p25 >= 0.02 and local_dd_p75 <= 0.30 and local_ann_median >= 1.0:
                robustness_label = "strong"
            elif local_goal_rate >= 0.35 and local_mean_p25 >= 0.015 and local_dd_p75 <= 0.35 and local_ann_median >= 0.75:
                robustness_label = "mixed"

        rows.append(
            {
                "combo_variant": combo.get("combo_variant"),
                "combo_priority": combo.get("combo_priority"),
                "combo_id": combo_id,
                "component_ids": combo.get("component_ids"),
                "top_k_per_timestamp": combo.get("top_k_per_timestamp"),
                "local_neighbors_count": int(len(local_neighbors)),
                "same_components_other_topk_count": int(len(same_component_neighbors)),
                "minor_component_change_count": int(len(minor_change_neighbors)),
                "local_goal_rate": local_goal_rate,
                "minor_change_goal_rate": minor_change_goal_rate,
                "local_mean_return_min_pct": _quantile_value(local_neighbors.get("mean_return_pct", pd.Series(dtype="float64")), 0.0),
                "local_mean_return_p25_pct": local_mean_p25,
                "local_mean_return_median_pct": _quantile_value(local_neighbors.get("mean_return_pct", pd.Series(dtype="float64")), 0.50),
                "local_mean_return_max_pct": _quantile_value(local_neighbors.get("mean_return_pct", pd.Series(dtype="float64")), 1.0),
                "local_win_rate_min": _quantile_value(local_neighbors.get("win_rate", pd.Series(dtype="float64")), 0.0),
                "local_win_rate_median": _quantile_value(local_neighbors.get("win_rate", pd.Series(dtype="float64")), 0.50),
                "local_win_rate_max": _quantile_value(local_neighbors.get("win_rate", pd.Series(dtype="float64")), 1.0),
                "local_annualized_min_pct": _quantile_value(local_neighbors.get("annualized_unit_pnl_pct", pd.Series(dtype="float64")), 0.0),
                "local_annualized_median_pct": local_ann_median,
                "local_annualized_max_pct": _quantile_value(local_neighbors.get("annualized_unit_pnl_pct", pd.Series(dtype="float64")), 1.0),
                "local_dd_min_pct": _quantile_value(local_neighbors.get("max_drawdown_pct", pd.Series(dtype="float64")), 0.0),
                "local_dd_p75_pct": local_dd_p75,
                "local_dd_max_pct": _quantile_value(local_neighbors.get("max_drawdown_pct", pd.Series(dtype="float64")), 1.0),
                "robustness_label": robustness_label,
            }
        )

    return pd.DataFrame(rows)


def _build_recommended_shortlist(
    *,
    great_combos: pd.DataFrame,
    combo_robustness_summary: pd.DataFrame,
    limit: int = 12,
) -> pd.DataFrame:
    if great_combos.empty:
        return pd.DataFrame()
    merged = great_combos.copy()
    if not combo_robustness_summary.empty and "combo_id" in combo_robustness_summary.columns:
        merged = merged.merge(
            combo_robustness_summary[
                [
                    "combo_id",
                    "robustness_label",
                    "local_goal_rate",
                    "local_mean_return_p25_pct",
                    "local_dd_p75_pct",
                    "local_annualized_median_pct",
                ]
            ],
            on="combo_id",
            how="left",
        )
    return merged.drop_duplicates(subset=["component_ids"], keep="first").head(limit).reset_index(drop=True)


def _build_monthly_returns_frame(
    events: pd.DataFrame,
    *,
    calendar_months: Sequence[str] | None = None,
) -> pd.DataFrame:
    normalized_calendar_months = _normalize_calendar_months(calendar_months or [])
    if events.empty or "month_utc" not in events.columns or "exit_return_pct" not in events.columns:
        if not normalized_calendar_months:
            return pd.DataFrame(columns=["month_utc", "total_return_pct", "trades_count", "month_positive"])
        monthly = pd.DataFrame(
            {
                "month_utc": normalized_calendar_months,
                "total_return_pct": [0.0] * len(normalized_calendar_months),
                "trades_count": [0] * len(normalized_calendar_months),
            }
        )
        monthly["month_positive"] = False
        return monthly
    monthly = (
        events.groupby("month_utc", sort=True)
        .agg(
            total_return_pct=("exit_return_pct", "sum"),
            trades_count=("exit_return_pct", "size"),
        )
        .reset_index()
    )
    if normalized_calendar_months:
        monthly = (
            monthly.set_index("month_utc")
            .reindex(normalized_calendar_months, fill_value=0.0)
            .rename_axis("month_utc")
            .reset_index()
        )
        monthly["trades_count"] = pd.to_numeric(monthly["trades_count"], errors="coerce").fillna(0).astype(int)
    monthly["month_positive"] = monthly["total_return_pct"] > 0
    return monthly


def _as_percent_formatter() -> FuncFormatter:
    return FuncFormatter(lambda value, _: f"{value * 100:.0f}%")


def _save_placeholder_chart(path: Path, *, title: str) -> None:
    figure, axis = plt.subplots(figsize=(10, 5))
    axis.axis("off")
    axis.text(0.5, 0.5, "Нет данных для отображения", ha="center", va="center", fontsize=14)
    axis.set_title(title)
    figure.tight_layout()
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def _save_priority_equity_curve_chart(events: pd.DataFrame, path: Path) -> None:
    if events.empty:
        _save_placeholder_chart(path, title="Кривая результатов приоритетного портфеля")
        return
    ordered = events.sort_values("entry_timestamp_ms").copy()
    ordered["entry_datetime_utc"] = pd.to_datetime(ordered["entry_timestamp_ms"], unit="ms", utc=True, errors="coerce")
    ordered["equity_curve"] = 1.0 + pd.to_numeric(ordered["exit_return_pct"], errors="coerce").fillna(0.0).cumsum()

    figure, axis = plt.subplots(figsize=(12, 5))
    axis.plot(ordered["entry_datetime_utc"], ordered["equity_curve"], color="#125B50", linewidth=2.0)
    axis.set_title("Кривая результатов приоритетного портфеля")
    axis.set_ylabel("Капитал (1 + накопленная сумма доходностей)")
    axis.grid(alpha=0.25)
    figure.tight_layout()
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def _save_priority_monthly_returns_chart(monthly_returns: pd.DataFrame, path: Path) -> None:
    if monthly_returns.empty:
        _save_placeholder_chart(path, title="Помесячная доходность приоритетного портфеля")
        return
    colors = ["#198754" if positive else "#DC3545" for positive in monthly_returns["month_positive"].astype(bool)]
    figure, axis = plt.subplots(figsize=(12, 5))
    axis.bar(monthly_returns["month_utc"], monthly_returns["total_return_pct"], color=colors)
    axis.axhline(0.0, color="#222222", linewidth=1.0)
    axis.set_title("Помесячная доходность приоритетного портфеля")
    axis.set_ylabel("Доходность")
    axis.yaxis.set_major_formatter(_as_percent_formatter())
    axis.tick_params(axis="x", rotation=45)
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def _save_priority_trade_distribution_chart(events: pd.DataFrame, path: Path) -> None:
    if events.empty:
        _save_placeholder_chart(path, title="Распределение доходности сделок")
        return
    returns = pd.to_numeric(events["exit_return_pct"], errors="coerce").dropna()
    if returns.empty:
        _save_placeholder_chart(path, title="Распределение доходности сделок")
        return
    figure, axis = plt.subplots(figsize=(12, 5))
    axis.hist(returns, bins=min(40, max(10, len(returns) // 3)), color="#0D6EFD", alpha=0.85, edgecolor="white")
    axis.axvline(float(returns.mean()), color="#198754", linewidth=2.0, label="Среднее")
    axis.axvline(float(returns.median()), color="#FD7E14", linewidth=2.0, linestyle="--", label="Медиана")
    axis.set_title("Распределение доходности сделок приоритетного портфеля")
    axis.set_xlabel("Доходность сделки")
    axis.xaxis.set_major_formatter(_as_percent_formatter())
    axis.grid(alpha=0.25)
    axis.legend()
    figure.tight_layout()
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def _save_priority_trade_timeline_chart(events: pd.DataFrame, path: Path) -> None:
    if events.empty:
        _save_placeholder_chart(path, title="Лента сделок приоритетного портфеля")
        return
    ordered = events.sort_values("entry_timestamp_ms").copy()
    ordered["entry_datetime_utc"] = pd.to_datetime(ordered["entry_timestamp_ms"], unit="ms", utc=True, errors="coerce")
    returns = pd.to_numeric(ordered["exit_return_pct"], errors="coerce").fillna(0.0)
    colors = ["#198754" if value > 0 else "#DC3545" for value in returns]

    figure, axis = plt.subplots(figsize=(12, 5))
    axis.scatter(ordered["entry_datetime_utc"], returns, c=colors, s=34, alpha=0.85)
    axis.axhline(0.0, color="#222222", linewidth=1.0)
    axis.set_title("Лента сделок приоритетного портфеля")
    axis.set_ylabel("Доходность сделки")
    axis.yaxis.set_major_formatter(_as_percent_formatter())
    axis.grid(alpha=0.25)
    figure.tight_layout()
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def _save_top_variants_scatter_chart(shortlist: pd.DataFrame, path: Path) -> None:
    if shortlist.empty:
        _save_placeholder_chart(path, title="Лучшие статические варианты")
        return
    scoped = shortlist.copy()
    scoped["robustness_label"] = scoped.get("robustness_label", pd.Series("mixed", index=scoped.index)).fillna("mixed")
    palette = {
        "strong": "#198754",
        "mixed": "#FD7E14",
        "fragile": "#DC3545",
    }
    figure, axis = plt.subplots(figsize=(12, 7))
    for label, group in scoped.groupby("robustness_label", sort=True):
        axis.scatter(
            pd.to_numeric(group["trades_per_year"], errors="coerce"),
            pd.to_numeric(group["mean_return_pct"], errors="coerce"),
            s=140,
            alpha=0.9,
            label=label,
            color=palette.get(str(label), "#6C757D"),
        )
    for _, row in scoped.iterrows():
        axis.annotate(
            str(row["combo_variant"]),
            (
                float(pd.to_numeric(row["trades_per_year"], errors="coerce")),
                float(pd.to_numeric(row["mean_return_pct"], errors="coerce")),
            ),
            xytext=(6, 6),
            textcoords="offset points",
            fontsize=9,
        )
    axis.set_title("Лучшие статические варианты: частота против средней сделки")
    axis.set_xlabel("Сделок в год")
    axis.set_ylabel("Средняя доходность сделки")
    axis.yaxis.set_major_formatter(_as_percent_formatter())
    axis.grid(alpha=0.25)
    axis.legend(title="Устойчивость")
    figure.tight_layout()
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def _save_priority_topn_chart(priority_topn_summary: pd.DataFrame, path: Path) -> None:
    if priority_topn_summary.empty:
        _save_placeholder_chart(path, title="Устойчивость top-N")
        return
    scoped = priority_topn_summary.sort_values("top_n_variants").copy()
    x_values = pd.to_numeric(scoped["top_n_variants"], errors="coerce")
    figure, axes = plt.subplots(2, 2, figsize=(12, 8), sharex=True)
    metrics = [
        ("mean_return_pct", "Средняя сделка", True),
        ("annualized_unit_pnl_pct", "Годовой unit-PnL", True),
        ("max_drawdown_pct", "Макс. просадка", True),
        ("trades_per_year", "Сделок в год", False),
    ]
    for axis, (column, title, is_percent) in zip(axes.flat, metrics, strict=False):
        axis.plot(x_values, pd.to_numeric(scoped[column], errors="coerce"), marker="o", linewidth=2.0, color="#0D6EFD")
        axis.set_title(title)
        if is_percent:
            axis.yaxis.set_major_formatter(_as_percent_formatter())
        axis.grid(alpha=0.25)
    axes[1, 0].set_xlabel("Сколько top-N вариантов включено")
    axes[1, 1].set_xlabel("Сколько top-N вариантов включено")
    figure.suptitle("Устойчивость приоритетного отбора при расширении пула вариантов", fontsize=14)
    figure.tight_layout()
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def _save_leader_robustness_chart(shortlist: pd.DataFrame, path: Path) -> None:
    if shortlist.empty:
        _save_placeholder_chart(path, title="Устойчивость лидера")
        return
    scoped = shortlist.head(8).copy()
    labels = scoped["combo_variant"].astype(str).tolist()
    figure, axes = plt.subplots(1, 3, figsize=(15, 5))
    metrics = [
        ("local_goal_rate", "Доля соседей, проходящих цель", True),
        ("local_mean_return_p25_pct", "P25 средней сделки у соседей", True),
        ("local_dd_p75_pct", "P75 просадки у соседей", True),
    ]
    for axis, (column, title, is_percent) in zip(axes, metrics, strict=False):
        axis.bar(labels, pd.to_numeric(scoped.get(column), errors="coerce"), color="#125B50")
        axis.set_title(title)
        if is_percent:
            axis.yaxis.set_major_formatter(_as_percent_formatter())
        axis.grid(axis="y", alpha=0.25)
    figure.suptitle("Устойчивость лидера на соседних параметрах", fontsize=14)
    figure.tight_layout()
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def _sanitize_filename(value: str) -> str:
    sanitized = "".join(character if character.isalnum() or character in {"-", "_"} else "_" for character in value)
    while "__" in sanitized:
        sanitized = sanitized.replace("__", "_")
    return sanitized.strip("_") or "item"


def _resolve_trade_chart_timeframe(raw_value: object) -> Timeframe | None:
    if raw_value is None or raw_value is pd.NA:
        return Timeframe.M5
    normalized = str(raw_value).strip().lower()
    for timeframe in Timeframe:
        if timeframe.value == normalized:
            return timeframe
    return None


def _select_trade_chart_samples(manifest: pd.DataFrame) -> pd.DataFrame:
    if manifest.empty or "chart_status" not in manifest.columns:
        return pd.DataFrame(columns=list(manifest.columns) if not manifest.empty else ["chart_path"])
    available = manifest[manifest["chart_status"].astype(str) == "создан"].copy()
    if available.empty:
        return available

    picks: list[pd.Series] = []
    available = available.sort_values("entry_timestamp_ms").copy()
    picks.append(available.iloc[0])
    best = available.sort_values("exit_return_pct", ascending=False).iloc[0]
    worst = available.sort_values("exit_return_pct", ascending=True).iloc[0]
    median_value = float(pd.to_numeric(available["exit_return_pct"], errors="coerce").median())
    median_row = available.assign(_median_distance=(pd.to_numeric(available["exit_return_pct"], errors="coerce") - median_value).abs()).sort_values("_median_distance").iloc[0]
    picks.extend([best, worst, median_row])

    deduped_rows: list[pd.Series] = []
    seen_paths: set[str] = set()
    for row in picks:
        chart_path = str(row.get("chart_path", ""))
        if chart_path and chart_path not in seen_paths:
            deduped_rows.append(row)
            seen_paths.add(chart_path)
    return pd.DataFrame(deduped_rows).reset_index(drop=True)


def _build_trade_chart_manifest(
    *,
    priority_events: pd.DataFrame,
    cache_dir: Path | str | None,
    charts_dir: Path,
    logger: logging.Logger | None = None,
) -> pd.DataFrame:
    empty_manifest = pd.DataFrame(
        columns=[
            "chart_index",
            "symbol",
            "combo_variant",
            "component_ids",
            "entry_timestamp_ms",
            "entry_timestamp_utc",
            "exit_return_pct",
            "chart_status",
            "chart_path",
        ]
    )
    if cache_dir is None:
        return empty_manifest
    if priority_events.empty:
        return empty_manifest

    active_logger = logger or logging.getLogger("hourly-asia-pump-static-combo")
    trade_charts_dir = charts_dir / "trades"
    trade_charts_dir.mkdir(parents=True, exist_ok=True)
    preparer = DataPreparer(Path(cache_dir))
    plotter = StaticComboTradePlotter()
    frame_cache: dict[tuple[str, str], pd.DataFrame] = {}

    rows: list[dict[str, object]] = []
    ordered = priority_events.sort_values("entry_timestamp_ms").reset_index(drop=True)
    total_events = len(ordered)
    stage_started_at = time.perf_counter()
    for idx, (_, event) in enumerate(ordered.iterrows(), start=1):
        symbol = str(event.get("symbol", ""))
        combo_variant = str(event.get("combo_variant", ""))
        timeframe = _resolve_trade_chart_timeframe(event.get("source_timeframe"))
        entry_timestamp_ms = _safe_numeric(event.get("source_entry_timestamp_ms")) or _safe_numeric(event.get("entry_timestamp_ms"))
        exit_timestamp_ms = _safe_numeric(event.get("source_exit_timestamp_ms"))
        entry_price = _safe_numeric(event.get("source_entry_price"))
        stop_price = _safe_numeric(event.get("source_stop_price"))
        exit_price = _safe_numeric(event.get("source_exit_price"))
        trigger_open = _safe_numeric(event.get("source_trigger_open"))
        trigger_high = _safe_numeric(event.get("source_trigger_high"))
        trigger_low = _safe_numeric(event.get("source_trigger_low"))
        trigger_close = _safe_numeric(event.get("source_trigger_close"))
        exit_return_pct = _safe_numeric(event.get("exit_return_pct"))
        chart_status = "создан"
        chart_path: Path | None = None

        if timeframe is None or entry_timestamp_ms is None:
            chart_status = "нет_метаданных_сделки"
        else:
            cache_key = (symbol, timeframe.value)
            frame = frame_cache.get(cache_key)
            if frame is None:
                frame = preparer.load_symbol_data(symbol, timeframe)
                frame_cache[cache_key] = frame
            if frame.empty:
                chart_status = "нет_данных_в_кеше"
            else:
                timeframe_ms = timeframe.to_milliseconds()
                start_ts = int((_safe_numeric(event.get("timestamp_ms")) or entry_timestamp_ms) - (timeframe_ms * 18))
                right_anchor = exit_timestamp_ms if exit_timestamp_ms is not None else entry_timestamp_ms + (timeframe_ms * 24)
                end_ts = int(right_anchor + (timeframe_ms * 18))
                scoped = frame[
                    (pd.to_numeric(frame["timestamp"], errors="coerce") >= start_ts)
                    & (pd.to_numeric(frame["timestamp"], errors="coerce") <= end_ts)
                ].copy()
                if scoped.empty:
                    chart_status = "пустое_окно_графика"
                else:
                    chart_file_name = (
                        f"{idx:03d}_{combo_variant}_{_sanitize_filename(symbol)}_"
                        f"{pd.to_datetime(int(entry_timestamp_ms), unit='ms', utc=True).strftime('%Y%m%d_%H%M')}.png"
                    )
                    chart_path = trade_charts_dir / chart_file_name
                    try:
                        plotter.plot_trade(
                            frame=scoped,
                            spec=StaticComboTradePlotSpec(
                                symbol=symbol,
                                combo_variant=combo_variant,
                                component_ids=str(event.get("component_ids", "")),
                                trigger_timestamp_ms=int(_safe_numeric(event.get("timestamp_ms")) or entry_timestamp_ms),
                                entry_timestamp_ms=int(entry_timestamp_ms),
                                exit_timestamp_ms=int(exit_timestamp_ms) if exit_timestamp_ms is not None else None,
                                entry_price=entry_price,
                                stop_price=stop_price,
                                exit_price=exit_price,
                                trigger_open=trigger_open,
                                trigger_high=trigger_high,
                                trigger_low=trigger_low,
                                trigger_close=trigger_close,
                                trigger_return_pct=_safe_numeric(event.get("source_trigger_return_pct")) or _safe_numeric(event.get("trigger_return_pct")),
                                trigger_range_pct=_safe_numeric(event.get("source_trigger_range_pct")),
                                range_atr=_safe_numeric(event.get("source_range_atr")) or _safe_numeric(event.get("range_atr")),
                                body_atr=_safe_numeric(event.get("source_body_atr")),
                                volume_mult=_safe_numeric(event.get("source_volume_mult")) or _safe_numeric(event.get("volume_mult")),
                                close_to_high_frac=_safe_numeric(event.get("source_close_to_high_frac")),
                                pre_base_range_pct_60m=_safe_numeric(event.get("source_pre_base_range_pct_60m")),
                                pre_base_drift_pct_60m=_safe_numeric(event.get("source_pre_base_drift_pct_60m")),
                                pre_base_range_vs_trigger=_safe_numeric(event.get("source_pre_base_range_vs_trigger")),
                                pre_entry_pullback_frac=_safe_numeric(event.get("source_pre_entry_pullback_frac")),
                                pre_entry_red_volume_frac=_safe_numeric(event.get("source_pre_entry_red_volume_frac")),
                                next_bar_pullback_frac=_safe_numeric(event.get("source_next_bar_pullback_frac")),
                                next_close_to_high_frac=_safe_numeric(event.get("source_next_close_to_high_frac")),
                                initial_risk_pct=_safe_numeric(event.get("source_initial_risk_pct")),
                                peak_timestamp_ms=int(_safe_numeric(event.get("source_peak_timestamp_ms"))) if _safe_numeric(event.get("source_peak_timestamp_ms")) is not None else None,
                                peak_price=_safe_numeric(event.get("source_peak_price_before_50pct_retrace")),
                                exit_return_pct=exit_return_pct,
                                exit_reason=str(event.get("source_exit_reason", "")) or None,
                                entry_reason=str(event.get("source_entry_reason", "")) or None,
                                initial_stop_reason=str(event.get("source_initial_stop_reason", "")) or None,
                                source_trade_model_id=str(event.get("source_trade_model_id", "")) or None,
                                source_trade_model_label=str(event.get("source_trade_model_label", "")) or None,
                                source_config_id=str(event.get("source_config_id", "")) or None,
                                hour_utc=int(_safe_numeric(event.get("source_hour_utc"))) if _safe_numeric(event.get("source_hour_utc")) is not None else None,
                            ),
                            output_path=chart_path,
                        )
                    except Exception as exc:
                        chart_status = f"ошибка_графика:{type(exc).__name__}"
                        chart_path = None
                        active_logger.warning(
                            "hourly-asia-pump-static-combo: этап=графики-сделок предупреждение symbol=%s combo=%s reason=%s",
                            symbol,
                            combo_variant,
                            exc,
                        )

        rows.append(
            {
                "chart_index": idx,
                "symbol": symbol,
                "combo_variant": combo_variant,
                "component_ids": event.get("component_ids"),
                "entry_timestamp_ms": int(entry_timestamp_ms) if entry_timestamp_ms is not None else None,
                "entry_timestamp_utc": event.get("source_entry_timestamp_utc") or event.get("entry_timestamp_utc"),
                "exit_return_pct": exit_return_pct,
                "chart_status": chart_status,
                "chart_path": str(chart_path) if chart_path is not None else None,
            }
        )

        if idx == 1 or idx == total_events or idx % 10 == 0:
            elapsed = time.perf_counter() - stage_started_at
            eta = (elapsed / idx) * (total_events - idx) if idx > 0 else None
            active_logger.info(
                "hourly-asia-pump-static-combo: этап=графики-сделок прогресс=%.1f%% графиков=%s/%s прошло=%s eta=%s",
                (idx / total_events) * 100.0,
                idx,
                total_events,
                _format_elapsed(elapsed),
                _format_elapsed(eta),
            )

    return pd.DataFrame(rows) if rows else empty_manifest


def _build_chart_manifest(
    *,
    charts_dir: Path,
    priority_events: pd.DataFrame,
    priority_topn_summary: pd.DataFrame,
    recommended_shortlist: pd.DataFrame,
) -> dict[str, Path]:
    charts_dir.mkdir(parents=True, exist_ok=True)
    monthly_returns = _build_monthly_returns_frame(priority_events)
    chart_paths = {
        "priority_equity_curve": charts_dir / "priority_equity_curve.png",
        "priority_monthly_returns": charts_dir / "priority_monthly_returns.png",
        "priority_trade_distribution": charts_dir / "priority_trade_distribution.png",
        "priority_trade_timeline": charts_dir / "priority_trade_timeline.png",
        "top_variants_scatter": charts_dir / "top_variants_scatter.png",
        "priority_topn_stability": charts_dir / "priority_topn_stability.png",
        "leader_robustness": charts_dir / "leader_robustness.png",
    }
    _save_priority_equity_curve_chart(priority_events, chart_paths["priority_equity_curve"])
    _save_priority_monthly_returns_chart(monthly_returns, chart_paths["priority_monthly_returns"])
    _save_priority_trade_distribution_chart(priority_events, chart_paths["priority_trade_distribution"])
    _save_priority_trade_timeline_chart(priority_events, chart_paths["priority_trade_timeline"])
    _save_top_variants_scatter_chart(recommended_shortlist, chart_paths["top_variants_scatter"])
    _save_priority_topn_chart(priority_topn_summary, chart_paths["priority_topn_stability"])
    _save_leader_robustness_chart(recommended_shortlist, chart_paths["leader_robustness"])
    return chart_paths


def _write_static_combo_report(
    *,
    output_dir: Path,
    context: dict[str, object],
    great_combos: pd.DataFrame,
    recommended_shortlist: pd.DataFrame,
    priority_summary: pd.DataFrame,
    priority_monthly_returns: pd.DataFrame,
    priority_holdout_sanity: pd.DataFrame,
    priority_topn_summary: pd.DataFrame,
    combo_robustness_summary: pd.DataFrame,
    trade_chart_manifest: pd.DataFrame,
    trade_chart_samples: pd.DataFrame,
    chart_paths: dict[str, Path],
) -> Path:
    great_combo_count = int(len(great_combos))
    commission_rate = _safe_numeric(context.get("commission_rate"))
    round_trip_taker_fee_pct = _safe_numeric(context.get("round_trip_taker_fee_pct"))
    unique_component_sets = int(great_combos["component_ids"].astype(str).nunique()) if not great_combos.empty and "component_ids" in great_combos.columns else 0
    topn_equal_up_to_10 = False
    if not priority_topn_summary.empty:
        topn_subset = priority_topn_summary[priority_topn_summary["top_n_variants"].astype(int).isin({1, 3, 5, 10})].copy()
        if not topn_subset.empty:
            comparison_columns = ["trades_count", "mean_return_pct", "win_rate", "annualized_unit_pnl_pct", "max_drawdown_pct"]
            first_row = topn_subset.iloc[0]
            topn_equal_up_to_10 = bool(
                topn_subset[comparison_columns]
                .apply(lambda row: all(abs(float(row[column]) - float(first_row[column])) < 1e-12 for column in comparison_columns), axis=1)
                .all()
            )

    top_variant = str(great_combos.iloc[0]["combo_variant"]) if not great_combos.empty else ""
    lines = [
        "# Отчёт По Статическим Комбинациям Hourly Asia Pump",
        "",
        "## Краткий Вывод",
        "",
        f"- Статических вариантов, проходящих годовую цель: `{great_combo_count}`.",
        f"- Уникальных наборов компонентов в каталоге: `{unique_component_sets}`.",
        f"- Приоритетный портфель строится по правилу: если один сигнал проходит несколько вариантов, берём вариант с наименьшим `combo_priority`.",
        f"- Лидер по итоговому рангу: `{top_variant}`.",
        f"- Результат `top-1..top-10` одинаковый: `{str(topn_equal_up_to_10).lower()}`.",
        "",
        "## Контекст Запуска",
        "",
        _frame_to_markdown(
            pd.DataFrame([{"key": key, "value": json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value} for key, value in context.items()]),
            columns=("key", "value"),
            header_labels={"key": "Параметр", "value": "Значение"},
        ),
        "",
        "## Итоговый Приоритетный Портфель",
        "",
        _frame_to_markdown(
            priority_summary,
            columns=(
                "trades_count",
                "trades_per_year",
                "mean_return_pct",
                "median_return_pct",
                "win_rate",
                "profit_factor",
                "annualized_unit_pnl_pct",
                "max_drawdown_pct",
                "mean_pos_trade_pct",
                "mean_neg_trade_pct",
                "positive_months_count",
                "non_positive_months_count",
                "max_concurrent_trades",
                "overlapping_entries_count",
                "matched_combo_variants_count",
            ),
            header_labels={
                "trades_count": "Сделок",
                "trades_per_year": "Сделок в год",
                "mean_return_pct": "Средняя сделка",
                "median_return_pct": "Медианная сделка",
                "win_rate": "WR",
                "profit_factor": "PF",
                "annualized_unit_pnl_pct": "Годовой unit-PnL",
                "max_drawdown_pct": "Макс. просадка",
                "mean_pos_trade_pct": "Средняя прибыльная",
                "mean_neg_trade_pct": "Средняя убыточная",
                "positive_months_count": "Плюсовых месяцев",
                "non_positive_months_count": "Неплюсовых месяцев",
                "max_concurrent_trades": "Макс. одновременных сделок",
                "overlapping_entries_count": "Перекрывающихся входов",
                "matched_combo_variants_count": "Подошедших вариантов",
            },
        ),
        "",
        "## Распределение По Месяцам",
        "",
        _frame_to_markdown(
            priority_monthly_returns,
            columns=(
                "month_utc",
                "total_return_pct",
                "trades_count",
                "month_positive",
            ),
            header_labels={
                "month_utc": "Месяц",
                "total_return_pct": "Сумма доходности",
                "trades_count": "Сделок",
                "month_positive": "Месяц в плюс",
            },
        ),
        "",
        "## Рекомендуемые Варианты",
        "",
        _frame_to_markdown(
            recommended_shortlist,
            columns=(
                "combo_variant",
                "component_ids",
                "trades_per_year",
                "mean_return_pct",
                "win_rate",
                "annualized_unit_pnl_pct",
                "max_drawdown_pct",
                "positive_months_count",
                "robustness_label",
            ),
            limit=12,
            header_labels={
                "combo_variant": "Вариант",
                "component_ids": "Компоненты",
                "trades_per_year": "Сделок в год",
                "mean_return_pct": "Средняя сделка",
                "win_rate": "WR",
                "annualized_unit_pnl_pct": "Годовой unit-PnL",
                "max_drawdown_pct": "Макс. просадка",
                "positive_months_count": "Плюсовых месяцев",
                "robustness_label": "Устойчивость",
            },
        ),
        "",
        "## Устойчивость На Соседних Параметрах",
        "",
        _frame_to_markdown(
            combo_robustness_summary[combo_robustness_summary["combo_variant"].astype(str).isin(recommended_shortlist["combo_variant"].astype(str).tolist())].copy(),
            columns=(
                "combo_variant",
                "local_neighbors_count",
                "same_components_other_topk_count",
                "minor_component_change_count",
                "local_goal_rate",
                "local_mean_return_p25_pct",
                "local_dd_p75_pct",
                "local_annualized_median_pct",
                "robustness_label",
            ),
            header_labels={
                "combo_variant": "Вариант",
                "local_neighbors_count": "Соседей",
                "same_components_other_topk_count": "Те же компоненты, другой top-k",
                "minor_component_change_count": "Соседи с малой заменой",
                "local_goal_rate": "Доля проходящих цель",
                "local_mean_return_p25_pct": "P25 средней сделки",
                "local_dd_p75_pct": "P75 просадки",
                "local_annualized_median_pct": "Медиана годового unit-PnL",
                "robustness_label": "Устойчивость",
            },
        ),
        "",
        "## Устойчивость Приоритетного Top-N",
        "",
        _frame_to_markdown(
            priority_topn_summary,
            columns=(
                "top_n_variants",
                "trades_count",
                "trades_per_year",
                "mean_return_pct",
                "win_rate",
                "annualized_unit_pnl_pct",
                "max_drawdown_pct",
                "positive_months_count",
                "non_positive_months_count",
            ),
            header_labels={
                "top_n_variants": "Сколько top-N вариантов",
                "trades_count": "Сделок",
                "trades_per_year": "Сделок в год",
                "mean_return_pct": "Средняя сделка",
                "win_rate": "WR",
                "annualized_unit_pnl_pct": "Годовой unit-PnL",
                "max_drawdown_pct": "Макс. просадка",
                "positive_months_count": "Плюсовых месяцев",
                "non_positive_months_count": "Неплюсовых месяцев",
            },
        ),
        "",
        "## Поздняя Проверка На Отложенном Окне",
        "",
        _frame_to_markdown(
            priority_holdout_sanity,
            columns=(
                "split_id",
                "trades_count",
                "trades_per_year",
                "mean_return_pct",
                "win_rate",
                "annualized_unit_pnl_pct",
                "max_drawdown_pct",
                "positive_months_count",
                "non_positive_months_count",
            ),
            header_labels={
                "split_id": "Сплит",
                "trades_count": "Сделок",
                "trades_per_year": "Сделок в год",
                "mean_return_pct": "Средняя сделка",
                "win_rate": "WR",
                "annualized_unit_pnl_pct": "Годовой unit-PnL",
                "max_drawdown_pct": "Макс. просадка",
                "positive_months_count": "Плюсовых месяцев",
                "non_positive_months_count": "Неплюсовых месяцев",
            },
        ),
        "",
        "## Список Графиков Сделок",
        "",
        _frame_to_markdown(
            trade_chart_manifest,
            columns=(
                "chart_index",
                "symbol",
                "combo_variant",
                "entry_timestamp_utc",
                "exit_return_pct",
                "chart_status",
                "chart_path",
            ),
            limit=30,
            header_labels={
                "chart_index": "№",
                "symbol": "Инструмент",
                "combo_variant": "Вариант",
                "entry_timestamp_utc": "Вход UTC",
                "exit_return_pct": "Доходность",
                "chart_status": "Статус графика",
                "chart_path": "Путь к графику",
            },
        ),
        "",
        "## Галерея Графиков",
        "",
        "### Кривая Результатов",
        "",
        "![Кривая результатов](charts/priority_equity_curve.png)",
        "",
        "### Помесячная Доходность",
        "",
        "![Помесячная доходность](charts/priority_monthly_returns.png)",
        "",
        "### Лента Сделок",
        "",
        "![Лента сделок](charts/priority_trade_timeline.png)",
        "",
        "### Распределение Сделок",
        "",
        "![Распределение сделок](charts/priority_trade_distribution.png)",
        "",
        "### Лучшие Варианты",
        "",
        "![Лучшие варианты](charts/top_variants_scatter.png)",
        "",
        "### Устойчивость Top-N",
        "",
        "![Устойчивость top-n](charts/priority_topn_stability.png)",
        "",
        "### Устойчивость Лидера",
        "",
        "![Устойчивость лидера](charts/leader_robustness.png)",
        "",
        "## Важные Оговорки",
        "",
        "- `annualized_unit_pnl_pct` — это additive unit-PnL по сделкам, а не CAGR счёта с ограничением по капиталу.",
            f"- Доходности сделок уже учитывают комиссию тейкера: `да`."
        + (
            f" Принята комиссия тейкера = `{commission_rate * 100:.02f}%` на сторону, `{round_trip_taker_fee_pct * 100:.02f}%` за круг."
            if commission_rate is not None and round_trip_taker_fee_pct is not None
            else ""
        ),
        "- `max_concurrent_trades` и `overlapping_entries_count` показывают, где сделки перекрываются по времени.",
        "- Пороги `shallow/q75/q25` зафиксированы как численные значения в коде и не пересчитываются квантилизацией при каждом запуске.",
        "- Поиск и ранжирование комбинаций всё ещё сделаны на этом же полном годе, поэтому отчёт остаётся исследовательским, а не независимым доказательством для реальной торговли.",
        "",
        "## Примеры Графиков Сделок",
        "",
    ]
    for _, row in trade_chart_samples.iterrows():
        chart_path = row.get("chart_path")
        if chart_path is None or chart_path is pd.NA:
            continue
        chart_name = Path(str(chart_path)).name
        lines.extend(
            [
                f"### {row.get('combo_variant', '')} | {row.get('symbol', '')} | {_format_value('exit_return_pct', row.get('exit_return_pct'))}",
                "",
                f"![Сделка {row.get('symbol', '')}](charts/trades/{chart_name})",
                "",
            ]
        )
    report_path = output_dir / "static_combo_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def build_hourly_asia_pump_static_combo_artifacts(
    *,
    base_events_path: Path | str,
    confirmed_events_path: Path | str,
    output_dir: Path | str,
    cache_dir: Path | str | None = None,
    commission_rate: float | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, Path]:
    active_logger = logger or logging.getLogger("hourly-asia-pump-static-combo")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    run_started_at = time.perf_counter()

    active_logger.info("hourly-asia-pump-static-combo: этап=загрузка-входных-данных")
    base_events = _prepare_base_events(Path(base_events_path))
    confirmed_events = _prepare_confirmed_events(Path(confirmed_events_path))
    analysis_calendar_months = _calendar_months_from_frames(base_events, confirmed_events)
    candidate_frames = _build_static_candidate_frames(base_events=base_events, confirmed_events=confirmed_events)
    active_logger.info(
        "hourly-asia-pump-static-combo: этап=кандидаты кандидатов=%s базовых_сделок=%s подтвержденных_сделок=%s прошло=%s",
        len(candidate_frames),
        len(base_events),
        len(confirmed_events),
        _format_elapsed(time.perf_counter() - run_started_at),
    )

    candidate_rows: list[dict[str, object]] = []
    for candidate_id, frame in candidate_frames.items():
        summary = _summarize_events(frame, calendar_months=analysis_calendar_months)
        if summary is None:
            continue
        summary["candidate_id"] = candidate_id
        candidate_rows.append(summary)
    candidate_summary = _sort_summary_frame(
        pd.DataFrame(candidate_rows),
        sort_columns=["mean_return_pct", "annualized_unit_pnl_pct", "win_rate"],
        ascending=[False, False, False],
    )

    combo_summary_rows: list[dict[str, object]] = []
    combo_event_frames: list[pd.DataFrame] = []
    combo_events_by_id: dict[str, pd.DataFrame] = {}
    candidate_ids = list(candidate_frames.keys())
    total_combo_jobs = (
        sum(math.comb(len(candidate_ids), combo_size) for combo_size in range(_STATIC_MIN_COMPONENTS, min(_STATIC_MAX_COMPONENTS, len(candidate_ids)) + 1))
        * len(_STATIC_TOP_K_VALUES)
        if candidate_ids
        else 0
    )
    combo_stage_started_at = time.perf_counter()
    processed_combo_jobs = 0
    active_logger.info(
        "hourly-asia-pump-static-combo: этап=старт-сетки-комбинаций кандидатов=%s задач_по_комбинациям=%s прошло=%s eta=н/д",
        len(candidate_ids),
        total_combo_jobs,
        _format_elapsed(combo_stage_started_at - run_started_at),
    )
    for combo_size in range(_STATIC_MIN_COMPONENTS, min(_STATIC_MAX_COMPONENTS, len(candidate_ids)) + 1):
        for component_ids in itertools.combinations(candidate_ids, combo_size):
            component_id_list = list(component_ids)
            for top_k_per_timestamp in _STATIC_TOP_K_VALUES:
                processed_combo_jobs += 1
                combo_events = _build_combo_events(
                    candidate_frames=candidate_frames,
                    component_ids=component_id_list,
                    top_k_per_timestamp=top_k_per_timestamp,
                )
                if combo_events.empty:
                    continue
                combo_id = str(combo_events.iloc[0]["combo_id"])
                combo_events_by_id[combo_id] = combo_events
                combo_event_frames.append(combo_events)
                summary = _summarize_events(combo_events, calendar_months=analysis_calendar_months)
                if summary is not None:
                    summary["combo_id"] = combo_id
                    summary["component_ids"] = ",".join(component_id_list)
                    summary["component_count"] = len(component_id_list)
                    summary["top_k_per_timestamp"] = top_k_per_timestamp
                    summary["event_signature"] = _build_event_signature(combo_events)
                    combo_summary_rows.append(summary)
                if (
                    processed_combo_jobs == 1
                    or processed_combo_jobs == total_combo_jobs
                    or processed_combo_jobs % _STATIC_PROGRESS_LOG_EVERY == 0
                ):
                    combo_elapsed = time.perf_counter() - combo_stage_started_at
                    combo_eta = (
                        (combo_elapsed / processed_combo_jobs) * (total_combo_jobs - processed_combo_jobs)
                        if processed_combo_jobs > 0
                        else None
                    )
                    active_logger.info(
                        "hourly-asia-pump-static-combo: этап=сетка-комбинаций прогресс=%.1f%% обработано=%s/%s размер=%s top_k=%s строк_в_сводке=%s прошло=%s eta=%s",
                        (processed_combo_jobs / total_combo_jobs) * 100.0 if total_combo_jobs > 0 else 100.0,
                        processed_combo_jobs,
                        total_combo_jobs,
                        combo_size,
                        top_k_per_timestamp,
                        len(combo_summary_rows),
                        _format_elapsed(combo_elapsed),
                        _format_elapsed(combo_eta),
                    )

    combo_event_frames_for_concat = [frame.dropna(axis=1, how="all") for frame in combo_event_frames if not frame.empty]
    combo_events_all = (
        pd.concat(combo_event_frames_for_concat, ignore_index=True, sort=False)
        if combo_event_frames_for_concat
        else pd.DataFrame()
    )
    active_logger.info(
        "hourly-asia-pump-static-combo: этап=сетка-комбинаций-завершена обработано=%s/%s событий_комбинаций=%s сводок_комбинаций=%s прошло=%s",
        processed_combo_jobs,
        total_combo_jobs,
        len(combo_events_all),
        len(combo_summary_rows),
        _format_elapsed(time.perf_counter() - combo_stage_started_at),
    )
    active_logger.info("hourly-asia-pump-static-combo: этап=ранжирование")
    combo_summary = _rank_combo_summary(pd.DataFrame(combo_summary_rows))

    if not combo_summary.empty and "event_signature" in combo_summary.columns:
        unique_combo_summary = combo_summary.drop_duplicates(subset=["event_signature"], keep="first").reset_index(drop=True)
    else:
        unique_combo_summary = combo_summary.copy()
    if not unique_combo_summary.empty and "meets_goal" in unique_combo_summary.columns:
        great_combos = _assign_combo_variants(unique_combo_summary[unique_combo_summary["meets_goal"].astype(bool)].copy())
    else:
        great_combos = unique_combo_summary.iloc[0:0].copy()
    active_logger.info(
        "hourly-asia-pump-static-combo: этап=отбор уникальных_комбинаций=%s сильных_комбинаций=%s прошло=%s",
        len(unique_combo_summary),
        len(great_combos),
        _format_elapsed(time.perf_counter() - run_started_at),
    )
    priority_events = _build_priority_selected_events(combo_events=combo_events_all, combo_catalog=great_combos)
    if not priority_events.empty:
        priority_summary_payload = _summarize_events(priority_events, calendar_months=analysis_calendar_months)
        priority_summary = pd.DataFrame([priority_summary_payload]) if priority_summary_payload is not None else _empty_priority_summary()
    else:
        priority_summary = _empty_priority_summary()
    priority_summary["matched_combo_variants_count"] = int(great_combos["combo_variant"].nunique()) if not great_combos.empty and "combo_variant" in great_combos.columns else 0

    holdout_sanity = _build_holdout_sanity_rows(
        combo_summary=great_combos,
        combo_events_by_id=combo_events_by_id,
        calendar_months=analysis_calendar_months,
    )
    priority_topn_summary = _build_priority_topn_summary(
        great_combos=great_combos,
        combo_events=combo_events_all,
        calendar_months=analysis_calendar_months,
    )
    combo_robustness_summary = _build_combo_robustness_summary(
        selected_combos=great_combos,
        all_unique_combos=unique_combo_summary,
    )
    recommended_shortlist = _build_recommended_shortlist(
        great_combos=great_combos,
        combo_robustness_summary=combo_robustness_summary,
    )
    priority_monthly_returns = _build_monthly_returns_frame(priority_events, calendar_months=analysis_calendar_months)
    priority_holdout_sanity_rows: list[dict[str, object]] = []
    for split_id, train_months_count in _STATIC_HOLDOUT_SPLITS:
        if len(analysis_calendar_months) <= train_months_count:
            continue
        train_months = analysis_calendar_months[:train_months_count]
        test_months = analysis_calendar_months[train_months_count:]
        test_events = priority_events[priority_events["month_utc"].astype(str).isin(test_months)].copy()
        summary = _summarize_events(test_events, calendar_months=test_months)
        priority_holdout_sanity_rows.append(
            {
                "portfolio_id": "priority_selected_static_year",
                "split_id": split_id,
                "train_start_month_utc": train_months[0],
                "train_end_month_utc": train_months[-1],
                "test_start_month_utc": test_months[0],
                "test_end_month_utc": test_months[-1],
                **summary,
            }
        )
    priority_holdout_sanity = pd.DataFrame(priority_holdout_sanity_rows)
    active_logger.info(
        "hourly-asia-pump-static-combo: этап=пост-анализ приоритетных_сделок=%s строк_по_месяцам=%s строк_holdout=%s прошло=%s",
        len(priority_events),
        len(priority_monthly_returns),
        len(priority_holdout_sanity),
        _format_elapsed(time.perf_counter() - run_started_at),
    )

    candidate_summary_path = output_path / "candidate_component_summary.csv"
    candidate_summary.to_csv(candidate_summary_path, index=False)
    combo_summary_path = output_path / "combo_summary.csv"
    combo_summary.to_csv(combo_summary_path, index=False)
    unique_combo_summary_path = output_path / "combo_summary_unique.csv"
    unique_combo_summary.to_csv(unique_combo_summary_path, index=False)
    great_combo_catalog_path = output_path / "great_combo_catalog.csv"
    great_combos.to_csv(great_combo_catalog_path, index=False)
    combo_events_path = output_path / "combo_events.csv"
    combo_events_all.to_csv(combo_events_path, index=False)
    priority_events_path = output_path / "priority_selected_events.csv"
    priority_events.to_csv(priority_events_path, index=False)
    priority_summary_path = output_path / "priority_selected_summary.csv"
    priority_summary.to_csv(priority_summary_path, index=False)
    holdout_sanity_path = output_path / "great_combo_holdout_sanity.csv"
    holdout_sanity.to_csv(holdout_sanity_path, index=False)
    priority_holdout_sanity_path = output_path / "priority_selected_holdout_sanity.csv"
    priority_holdout_sanity.to_csv(priority_holdout_sanity_path, index=False)
    priority_topn_summary_path = output_path / "priority_topn_summary.csv"
    priority_topn_summary.to_csv(priority_topn_summary_path, index=False)
    combo_robustness_summary_path = output_path / "combo_robustness_summary.csv"
    combo_robustness_summary.to_csv(combo_robustness_summary_path, index=False)
    recommended_shortlist_path = output_path / "recommended_combo_shortlist.csv"
    recommended_shortlist.to_csv(recommended_shortlist_path, index=False)
    priority_monthly_returns_path = output_path / "priority_selected_monthly.csv"
    priority_monthly_returns.to_csv(priority_monthly_returns_path, index=False)

    context = {
        "base_events_path": str(Path(base_events_path)),
        "confirmed_events_path": str(Path(confirmed_events_path)),
        "cache_dir": str(Path(cache_dir)) if cache_dir is not None else None,
        "commission_rate": float(commission_rate) if commission_rate is not None else None,
        "round_trip_taker_fee_pct": float(commission_rate * 2.0) if commission_rate is not None else None,
        "source_trade_returns_net_of_taker_fees": bool(commission_rate is not None),
        "analysis_calendar_months": analysis_calendar_months,
        "frozen_threshold_profile_version": _STATIC_FROZEN_THRESHOLD_VERSION,
        "frozen_thresholds": {
            "mb5_01_shallow_pre_entry_pullback_frac_max": _STATIC_MB5_01_SHALLOW_PULLBACK_FRAC_MAX,
            "mb3_03_trg_q75_trigger_return_pct_min": _STATIC_MB3_03_TRIGGER_RETURN_PCT_Q75_MIN,
            "sm75_00_risk_q25_initial_risk_pct_max": _STATIC_SM75_00_INITIAL_RISK_PCT_Q25_MAX,
            "mb5_06_shallow_pre_entry_pullback_frac_max": _STATIC_MB5_06_SHALLOW_PULLBACK_FRAC_MAX,
        },
        "candidate_ids": list(candidate_frames.keys()),
        "great_combo_count": int(len(great_combos)),
        "recommended_shortlist_count": int(len(recommended_shortlist)),
    }
    context_path = output_path / "static_combo_context.json"
    context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")

    charts_dir = output_path / "charts"
    trade_chart_manifest = _build_trade_chart_manifest(
        priority_events=priority_events,
        cache_dir=cache_dir,
        charts_dir=charts_dir,
        logger=active_logger,
    )
    trade_chart_manifest_path = output_path / "priority_trade_chart_manifest.csv"
    trade_chart_manifest.to_csv(trade_chart_manifest_path, index=False)
    trade_chart_samples = _select_trade_chart_samples(trade_chart_manifest)
    trade_chart_samples_path = output_path / "priority_trade_chart_samples.csv"
    trade_chart_samples.to_csv(trade_chart_samples_path, index=False)
    chart_paths = _build_chart_manifest(
        charts_dir=charts_dir,
        priority_events=priority_events,
        priority_topn_summary=priority_topn_summary,
        recommended_shortlist=recommended_shortlist,
    )
    charts_manifest_path = output_path / "charts_manifest.json"
    charts_manifest_path.write_text(
        json.dumps({key: str(path) for key, path in chart_paths.items()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report_path = _write_static_combo_report(
        output_dir=output_path,
        context=context,
        great_combos=great_combos,
        recommended_shortlist=recommended_shortlist,
        priority_summary=priority_summary,
        priority_monthly_returns=priority_monthly_returns,
        priority_holdout_sanity=priority_holdout_sanity,
        priority_topn_summary=priority_topn_summary,
        combo_robustness_summary=combo_robustness_summary,
        trade_chart_manifest=trade_chart_manifest,
        trade_chart_samples=trade_chart_samples,
        chart_paths=chart_paths,
    )

    active_logger.info(
        "hourly-asia-pump-static-combo: артефакты сохранены output_dir=%s сильных_комбинаций=%s приоритетных_сделок=%s report=%s charts_dir=%s прошло=%s",
        output_path,
        len(great_combos),
        len(priority_events),
        report_path,
        charts_dir,
        _format_elapsed(time.perf_counter() - run_started_at),
    )
    return {
        "candidate_component_summary": candidate_summary_path,
        "combo_summary": combo_summary_path,
        "combo_summary_unique": unique_combo_summary_path,
        "great_combo_catalog": great_combo_catalog_path,
        "combo_events": combo_events_path,
        "priority_selected_events": priority_events_path,
        "priority_selected_summary": priority_summary_path,
        "great_combo_holdout_sanity": holdout_sanity_path,
        "priority_selected_holdout_sanity": priority_holdout_sanity_path,
        "priority_topn_summary": priority_topn_summary_path,
        "combo_robustness_summary": combo_robustness_summary_path,
        "recommended_combo_shortlist": recommended_shortlist_path,
        "priority_selected_monthly": priority_monthly_returns_path,
        "priority_trade_chart_manifest": trade_chart_manifest_path,
        "priority_trade_chart_samples": trade_chart_samples_path,
        "charts_manifest": charts_manifest_path,
        "report": report_path,
        "static_combo_context": context_path,
    }


__all__ = [
    "build_hourly_asia_pump_static_combo_artifacts",
    "_assign_combo_variants",
    "_build_priority_selected_events",
    "_build_static_candidate_frames",
    "_index_to_variant_label",
]
