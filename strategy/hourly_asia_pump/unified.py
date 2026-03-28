from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from strategy.hourly_asia_pump.static_combo import (
    _build_monthly_returns_frame,
    _calendar_months_from_frames,
    _frame_to_markdown,
    _save_priority_equity_curve_chart,
    _save_priority_monthly_returns_chart,
    _save_priority_trade_distribution_chart,
    _save_priority_trade_timeline_chart,
    _sort_summary_frame,
    _summarize_events,
)

_UNIFIED_MIN_TRADES_PER_YEAR = 50.0
_UNIFIED_MIN_MEAN_RETURN_PCT = 0.02
_UNIFIED_MIN_WIN_RATE = 0.40
_UNIFIED_MIN_ANNUALIZED_UNIT_PNL_PCT = 1.0
_UNIFIED_MAX_DRAWDOWN_PCT = 0.30
_UNIFIED_MIN_POSITIVE_MONTHS = 9
_UNIFIED_PROGRESS_LOG_EVERY = 10_000
_UNIFIED_HOLDOUT_SPLITS: tuple[tuple[str, int], ...] = (
    ("train8_test4", 8),
    ("train9_test3", 9),
)
_UNIFIED_HOLDOUT_TOP_CANDIDATES = 150
_UNIFIED_RESILIENCE_TOP_CANDIDATES = 200
_UNIFIED_FAMILY_GRIDS: tuple[dict[str, Any], ...] = (
    {
        "stop_style": "trigger_low",
        "trigger_values": (0.05, 0.06, 0.065, 0.07, 0.075, 0.08),
        "range_atr_values": (7.0, 7.5, 8.0, 8.5, 9.0, 10.0, 12.0),
        "body_atr_values": (None, 7.0),
        "volume_values": (8.0, 9.0, 10.0, 12.0, 15.0, 18.0),
        "close_to_high_values": (0.18, 0.17, 0.15, 0.13),
        "require_next_green_values": (False, True),
        "next_pullback_values": (None, 0.33, 0.25),
        "base_range_values": (None, 0.06, 0.05, 0.04),
        "base_drift_values": (None, 0.03, 0.02),
        "base_vs_trigger_values": (None, 0.8, 0.6),
    },
    {
        "stop_style": "next_low",
        "trigger_values": (0.05, 0.06, 0.065, 0.07, 0.075, 0.08),
        "range_atr_values": (7.0, 7.5, 8.0, 8.5, 9.0, 10.0, 12.0),
        "body_atr_values": (None, 7.0),
        "volume_values": (8.0, 9.0, 10.0, 12.0, 15.0, 18.0),
        "close_to_high_values": (0.18, 0.17, 0.15, 0.13),
        "require_next_green_values": (False, True),
        "next_pullback_values": (None, 0.33, 0.25),
        "base_range_values": (None, 0.06, 0.05, 0.04),
        "base_drift_values": (None, 0.03, 0.02),
        "base_vs_trigger_values": (None, 0.8, 0.6),
    },
)
_UNIFIED_TOP_CANDIDATES_LIMIT = 25


def _format_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return "н/д"
    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _to_json_ready(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _to_json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json_ready(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if value is pd.NA:
        return None
    return value


def _optional_float(value: object) -> float | None:
    if value is None or value is pd.NA:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric):
        return None
    return numeric


def _prepare_confirmed_unique_events(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if frame.empty:
        return frame
    for column_name in (
        "timestamp_ms",
        "entry_timestamp_ms",
        "exit_timestamp_ms",
        "trigger_return_pct",
        "range_atr",
        "body_atr",
        "volume_mult",
        "close_to_high_frac",
        "pre_base_range_pct_60m",
        "pre_base_drift_pct_60m",
        "pre_base_range_vs_trigger",
        "next_bar_pullback_frac",
        "next_bar_low_frac_of_trigger_range",
        "initial_risk_pct",
        "trigger_high",
        "trigger_low",
        "next_bar_high_price",
        "next_bar_low_price",
        "next_bar_close_price",
        "exit_return_pct",
    ):
        if column_name in frame.columns:
            frame[column_name] = pd.to_numeric(frame[column_name], errors="coerce")
    if "next_bar_is_green" in frame.columns:
        frame["next_bar_is_green"] = frame["next_bar_is_green"].fillna(False).astype(bool)
    frame["entry_timestamp_utc"] = pd.to_datetime(frame.get("entry_timestamp_utc"), utc=True, errors="coerce")
    frame["month_utc"] = frame["entry_timestamp_utc"].dt.strftime("%Y-%m")
    frame["date_utc"] = frame["entry_timestamp_utc"].dt.strftime("%Y-%m-%d")

    next_range = (frame["next_bar_high_price"] - frame["next_bar_low_price"]).replace(0, pd.NA)
    trigger_range = (frame["trigger_high"] - frame["trigger_low"]).replace(0, pd.NA)
    frame["next_close_to_high_frac"] = (frame["next_bar_high_price"] - frame["next_bar_close_price"]) / next_range
    frame["next_range_vs_trigger"] = next_range / trigger_range

    ordered = frame.sort_values(
        ["symbol", "timestamp_ms", "stop_style", "exit_return_pct", "config_id"],
        ascending=[True, True, True, True, True],
        na_position="last",
    ).copy()
    return ordered.drop_duplicates(["symbol", "timestamp_ms", "stop_style"]).reset_index(drop=True)


def _candidate_meets_goal(row: pd.Series | dict[str, object]) -> bool:
    return (
        float(row.get("trades_per_year", 0.0) or 0.0) >= _UNIFIED_MIN_TRADES_PER_YEAR
        and float(row.get("mean_return_pct", 0.0) or 0.0) >= _UNIFIED_MIN_MEAN_RETURN_PCT
        and float(row.get("win_rate", 0.0) or 0.0) >= _UNIFIED_MIN_WIN_RATE
        and float(row.get("annualized_unit_pnl_pct", 0.0) or 0.0) >= _UNIFIED_MIN_ANNUALIZED_UNIT_PNL_PCT
        and float(row.get("max_drawdown_pct", math.inf) or math.inf) <= _UNIFIED_MAX_DRAWDOWN_PCT
        and int(row.get("positive_months_count", 0) or 0) >= _UNIFIED_MIN_POSITIVE_MONTHS
    )


def _candidate_priority_score(row: pd.Series | dict[str, object]) -> float:
    mean_return_pct = float(row.get("mean_return_pct", 0.0) or 0.0)
    win_rate = float(row.get("win_rate", 0.0) or 0.0)
    trades_per_year = float(row.get("trades_per_year", 0.0) or 0.0)
    annualized_unit_pnl_pct = float(row.get("annualized_unit_pnl_pct", 0.0) or 0.0)
    max_drawdown_pct = float(row.get("max_drawdown_pct", 0.0) or 0.0)
    positive_months_count = int(row.get("positive_months_count", 0) or 0)
    active_rules_count = int(row.get("active_rules_count", 0) or 0)

    score = 0.0
    score += min(mean_return_pct, 0.04) * 1200.0
    score += min(win_rate, 0.70) * 200.0
    score += min(annualized_unit_pnl_pct, 3.5) * 40.0
    score += min(trades_per_year, 150.0) * 0.15
    score += min(positive_months_count, 12) * 8.0
    score -= max(max_drawdown_pct - 0.20, 0.0) * 180.0
    score -= max(active_rules_count - 4, 0) * 1.5
    if trades_per_year < _UNIFIED_MIN_TRADES_PER_YEAR:
        score -= (_UNIFIED_MIN_TRADES_PER_YEAR - trades_per_year) * 2.0
    if mean_return_pct < _UNIFIED_MIN_MEAN_RETURN_PCT:
        score -= (_UNIFIED_MIN_MEAN_RETURN_PCT - mean_return_pct) * 1500.0
    if win_rate < _UNIFIED_MIN_WIN_RATE:
        score -= (_UNIFIED_MIN_WIN_RATE - win_rate) * 200.0
    if annualized_unit_pnl_pct < _UNIFIED_MIN_ANNUALIZED_UNIT_PNL_PCT:
        score -= (_UNIFIED_MIN_ANNUALIZED_UNIT_PNL_PCT - annualized_unit_pnl_pct) * 80.0
    if positive_months_count < _UNIFIED_MIN_POSITIVE_MONTHS:
        score -= (_UNIFIED_MIN_POSITIVE_MONTHS - positive_months_count) * 12.0
    if _candidate_meets_goal(row):
        score += 1_000.0
    return score


def _build_candidate_rule_text(candidate: dict[str, object]) -> str:
    parts: list[str] = [
        f"stop={candidate['stop_style']}",
        f"trigger >= {float(candidate['min_trigger_return_pct']):.3f}",
        f"range_atr >= {float(candidate['min_range_atr']):.1f}",
    ]
    min_body_atr = _optional_float(candidate.get("min_body_atr"))
    if min_body_atr is not None:
        parts.append(f"body_atr >= {float(min_body_atr):.1f}")
    parts.append(f"volume >= {float(candidate['min_volume_mult']):.1f}")
    parts.append(f"close_to_high <= {float(candidate['max_close_to_high_frac']):.2f}")
    if bool(candidate.get("require_next_green")):
        parts.append("следующая свеча зелёная")
    max_next_pullback_frac = _optional_float(candidate.get("max_next_pullback_frac"))
    if max_next_pullback_frac is not None:
        parts.append(f"pullback_next <= {float(max_next_pullback_frac):.2f}")
    max_pre_base_range_pct_60m = _optional_float(candidate.get("max_pre_base_range_pct_60m"))
    if max_pre_base_range_pct_60m is not None:
        parts.append(f"base_range_60m <= {float(max_pre_base_range_pct_60m):.2f}")
    max_pre_base_drift_pct_60m = _optional_float(candidate.get("max_pre_base_drift_pct_60m"))
    if max_pre_base_drift_pct_60m is not None:
        parts.append(f"base_drift_60m <= {float(max_pre_base_drift_pct_60m):.2f}")
    max_pre_base_range_vs_trigger = _optional_float(candidate.get("max_pre_base_range_vs_trigger"))
    if max_pre_base_range_vs_trigger is not None:
        parts.append(f"base_vs_trigger <= {float(max_pre_base_range_vs_trigger):.2f}")
    return "; ".join(parts)


def _build_candidate_human_description(candidate: dict[str, object]) -> str:
    stop_style = str(candidate["stop_style"])
    stop_text = "стоп под low аномальной свечи" if stop_style == "trigger_low" else "стоп под low подтверждающей свечи"
    parts = [
        "Сигнал ищется одинаково для всех XX:00 в выбранном окне.",
        "После аномальной 5m-свечи ждём завершения следующей 5m-свечи и входим на открытии третьей.",
        stop_text + ".",
        "Дальше сопровождение одинаковое для всех сделок и берётся из confirmed-модели: переносы и выходы не меняются от часа к часу.",
        "Фильтры качества: " + _build_candidate_rule_text(candidate) + ".",
    ]
    return " ".join(parts)


def _build_filter_mask(
    *,
    arrays: dict[str, np.ndarray],
    candidate: dict[str, object],
) -> np.ndarray:
    mask = (
        (arrays["trigger_return_pct"] >= float(candidate["min_trigger_return_pct"]))
        & (arrays["range_atr"] >= float(candidate["min_range_atr"]))
        & (arrays["volume_mult"] >= float(candidate["min_volume_mult"]))
        & (arrays["close_to_high_frac"] <= float(candidate["max_close_to_high_frac"]))
    )
    min_body_atr = _optional_float(candidate.get("min_body_atr"))
    if min_body_atr is not None:
        mask &= arrays["body_atr"] >= float(min_body_atr)
    if bool(candidate.get("require_next_green")):
        mask &= arrays["next_bar_is_green"]
    max_next_pullback_frac = _optional_float(candidate.get("max_next_pullback_frac"))
    if max_next_pullback_frac is not None:
        mask &= arrays["next_bar_pullback_frac"] <= float(max_next_pullback_frac)
    max_pre_base_range_pct_60m = _optional_float(candidate.get("max_pre_base_range_pct_60m"))
    if max_pre_base_range_pct_60m is not None:
        mask &= arrays["pre_base_range_pct_60m"] <= float(max_pre_base_range_pct_60m)
    max_pre_base_drift_pct_60m = _optional_float(candidate.get("max_pre_base_drift_pct_60m"))
    if max_pre_base_drift_pct_60m is not None:
        mask &= arrays["pre_base_drift_pct_60m"] <= float(max_pre_base_drift_pct_60m)
    max_pre_base_range_vs_trigger = _optional_float(candidate.get("max_pre_base_range_vs_trigger"))
    if max_pre_base_range_vs_trigger is not None:
        mask &= arrays["pre_base_range_vs_trigger"] <= float(max_pre_base_range_vs_trigger)
    return mask


def _build_fast_summary(
    *,
    mask: np.ndarray,
    returns: np.ndarray,
    month_idx: np.ndarray,
    date_idx: np.ndarray,
    span_days: float,
    calendar_months_count: int,
) -> dict[str, object] | None:
    selected_idx = np.flatnonzero(mask)
    trades_count = int(len(selected_idx))
    if trades_count < 20:
        return None
    scoped_returns = returns[selected_idx]
    equity_curve = 1.0 + np.cumsum(scoped_returns)
    running_max = np.maximum.accumulate(equity_curve)
    drawdown = (running_max - equity_curve) / running_max
    monthly_returns = np.bincount(month_idx[selected_idx], weights=scoped_returns, minlength=calendar_months_count)
    winning_returns = scoped_returns[scoped_returns > 0]
    losing_returns = scoped_returns[scoped_returns < 0]
    wins_sum = float(winning_returns.sum()) if winning_returns.size else 0.0
    losses_sum = float(losing_returns.sum()) if losing_returns.size else 0.0
    profit_factor = None
    if losses_sum == 0.0:
        profit_factor = math.inf if wins_sum > 0.0 else None
    else:
        profit_factor = wins_sum / abs(losses_sum)
    annual_sum_return_pct = float(scoped_returns.sum())
    annualized_unit_pnl_pct = annual_sum_return_pct * (12.0 / max(1, calendar_months_count))
    return {
        "trades_count": trades_count,
        "trade_days_count": int(np.unique(date_idx[selected_idx]).size),
        "trades_per_year": float((trades_count / span_days) * 365.0),
        "mean_return_pct": float(scoped_returns.mean()),
        "median_return_pct": float(np.median(scoped_returns)),
        "win_rate": float((scoped_returns > 0).mean()),
        "profit_factor": profit_factor,
        "annual_sum_return_pct": annual_sum_return_pct,
        "annualized_sum_return_pct": annualized_unit_pnl_pct,
        "unit_pnl_sum_pct": annual_sum_return_pct,
        "annualized_unit_pnl_pct": annualized_unit_pnl_pct,
        "mean_pos_trade_pct": float(winning_returns.mean()) if winning_returns.size else None,
        "mean_neg_trade_pct": float(losing_returns.mean()) if losing_returns.size else None,
        "positive_months_count": int((monthly_returns > 0).sum()),
        "non_positive_months_count": int((monthly_returns <= 0).sum()),
        "all_active_months_positive": bool((monthly_returns > 0).all()) if len(monthly_returns) else False,
        "best_month_return_pct": float(monthly_returns.max()) if len(monthly_returns) else None,
        "worst_month_return_pct": float(monthly_returns.min()) if len(monthly_returns) else None,
        "max_drawdown_pct": float(drawdown.max()) if len(drawdown) else None,
        "max_concurrent_trades": None,
        "overlapping_entries_count": None,
    }


def _build_candidate_rows(
    *,
    confirmed_events: pd.DataFrame,
    calendar_months: list[str],
    logger: logging.Logger | None,
) -> pd.DataFrame:
    if confirmed_events.empty or not calendar_months:
        return pd.DataFrame()
    start_time = time.perf_counter()
    periods = pd.PeriodIndex(calendar_months, freq="M")
    start_timestamp = periods.min().to_timestamp(how="start").tz_localize("UTC")
    end_timestamp = (periods.max() + 1).to_timestamp(how="start").tz_localize("UTC")
    span_days = max(1.0, (end_timestamp - start_timestamp).total_seconds() / 86_400)
    month_positions = {month: idx for idx, month in enumerate(calendar_months)}
    candidates_by_signature: dict[str, dict[str, object]] = {}
    total_combos = sum(
        len(grid["trigger_values"])
        * len(grid["range_atr_values"])
        * len(grid["body_atr_values"])
        * len(grid["volume_values"])
        * len(grid["close_to_high_values"])
        * len(grid["require_next_green_values"])
        * len(grid["next_pullback_values"])
        * len(grid["base_range_values"])
        * len(grid["base_drift_values"])
        * len(grid["base_vs_trigger_values"])
        for grid in _UNIFIED_FAMILY_GRIDS
    )
    processed_combos = 0
    frames_by_stop: dict[str, pd.DataFrame] = {}
    arrays_by_stop: dict[str, dict[str, np.ndarray]] = {}

    for grid in _UNIFIED_FAMILY_GRIDS:
        stop_style = str(grid["stop_style"])
        scoped = confirmed_events[confirmed_events["stop_style"].astype(str) == stop_style].copy().reset_index(drop=True)
        frames_by_stop[stop_style] = scoped
        arrays_by_stop[stop_style] = {
            "trigger_return_pct": pd.to_numeric(scoped["trigger_return_pct"], errors="coerce").to_numpy(dtype="float64"),
            "range_atr": pd.to_numeric(scoped["range_atr"], errors="coerce").to_numpy(dtype="float64"),
            "body_atr": pd.to_numeric(scoped["body_atr"], errors="coerce").to_numpy(dtype="float64"),
            "volume_mult": pd.to_numeric(scoped["volume_mult"], errors="coerce").to_numpy(dtype="float64"),
            "close_to_high_frac": pd.to_numeric(scoped["close_to_high_frac"], errors="coerce").to_numpy(dtype="float64"),
            "next_bar_pullback_frac": pd.to_numeric(scoped["next_bar_pullback_frac"], errors="coerce").to_numpy(dtype="float64"),
            "pre_base_range_pct_60m": pd.to_numeric(scoped["pre_base_range_pct_60m"], errors="coerce").to_numpy(dtype="float64"),
            "pre_base_drift_pct_60m": pd.to_numeric(scoped["pre_base_drift_pct_60m"], errors="coerce").to_numpy(dtype="float64"),
            "pre_base_range_vs_trigger": pd.to_numeric(scoped["pre_base_range_vs_trigger"], errors="coerce").to_numpy(dtype="float64"),
            "next_bar_is_green": scoped["next_bar_is_green"].fillna(False).astype(bool).to_numpy(dtype="bool"),
            "returns": pd.to_numeric(scoped["exit_return_pct"], errors="coerce").to_numpy(dtype="float64"),
            "month_idx": scoped["month_utc"].astype(str).map(month_positions).to_numpy(dtype="int64"),
            "date_idx": pd.factorize(scoped["date_utc"].astype(str), sort=False)[0].astype("int64"),
        }

    for grid in _UNIFIED_FAMILY_GRIDS:
        stop_style = str(grid["stop_style"])
        scoped = frames_by_stop[stop_style]
        arrays = arrays_by_stop[stop_style]
        combos = product(
            enumerate(grid["trigger_values"]),
            enumerate(grid["range_atr_values"]),
            enumerate(grid["body_atr_values"]),
            enumerate(grid["volume_values"]),
            enumerate(grid["close_to_high_values"]),
            enumerate(grid["require_next_green_values"]),
            enumerate(grid["next_pullback_values"]),
            enumerate(grid["base_range_values"]),
            enumerate(grid["base_drift_values"]),
            enumerate(grid["base_vs_trigger_values"]),
        )
        for (
            (trigger_idx, trigger_value),
            (range_atr_idx, range_atr_value),
            (body_atr_idx, body_atr_value),
            (volume_idx, volume_value),
            (close_to_high_idx, close_to_high_value),
            (next_green_idx, require_next_green),
            (next_pullback_idx, next_pullback_value),
            (base_range_idx, base_range_value),
            (base_drift_idx, base_drift_value),
            (base_vs_trigger_idx, base_vs_trigger_value),
        ) in combos:
            processed_combos += 1
            if logger is not None and (
                processed_combos == 1
                or processed_combos == total_combos
                or processed_combos % _UNIFIED_PROGRESS_LOG_EVERY == 0
            ):
                elapsed = time.perf_counter() - start_time
                progress = processed_combos / max(1, total_combos)
                eta = (elapsed / progress - elapsed) if progress > 0 else None
                logger.info(
                    "unified-search: progress=%.1f%% processed=%s/%s elapsed=%s eta=%s stop=%s найдено_уникальных=%s",
                    progress * 100.0,
                    processed_combos,
                    total_combos,
                    _format_elapsed(elapsed),
                    _format_elapsed(eta),
                    stop_style,
                    len(candidates_by_signature),
                )

            candidate = {
                "stop_style": stop_style,
                "min_trigger_return_pct": float(trigger_value),
                "min_range_atr": float(range_atr_value),
                "min_body_atr": None if body_atr_value is None else float(body_atr_value),
                "min_volume_mult": float(volume_value),
                "max_close_to_high_frac": float(close_to_high_value),
                "require_next_green": bool(require_next_green),
                "max_next_pullback_frac": None if next_pullback_value is None else float(next_pullback_value),
                "max_pre_base_range_pct_60m": None if base_range_value is None else float(base_range_value),
                "max_pre_base_drift_pct_60m": None if base_drift_value is None else float(base_drift_value),
                "max_pre_base_range_vs_trigger": None if base_vs_trigger_value is None else float(base_vs_trigger_value),
                "trigger_idx": trigger_idx,
                "range_atr_idx": range_atr_idx,
                "body_atr_idx": body_atr_idx,
                "volume_idx": volume_idx,
                "close_to_high_idx": close_to_high_idx,
                "next_green_idx": next_green_idx,
                "next_pullback_idx": next_pullback_idx,
                "base_range_idx": base_range_idx,
                "base_drift_idx": base_drift_idx,
                "base_vs_trigger_idx": base_vs_trigger_idx,
            }
            mask = _build_filter_mask(arrays=arrays, candidate=candidate)
            if int(mask.sum()) < 20:
                continue
            signature = hashlib.md5(mask.astype(np.uint8).tobytes()).hexdigest()
            summary = _build_fast_summary(
                mask=mask,
                returns=arrays["returns"],
                month_idx=arrays["month_idx"],
                date_idx=arrays["date_idx"],
                span_days=span_days,
                calendar_months_count=len(calendar_months),
            )
            if summary is None:
                continue
            active_rules_count = sum(
                1
                for value in (
                    candidate["min_body_atr"],
                    candidate["require_next_green"],
                    candidate["max_next_pullback_frac"],
                    candidate["max_pre_base_range_pct_60m"],
                    candidate["max_pre_base_drift_pct_60m"],
                    candidate["max_pre_base_range_vs_trigger"],
                )
                if value not in {None, False}
            ) + 4
            row = {
                **candidate,
                "candidate_id": f"unified_{stop_style}_{signature[:10]}",
                "events_signature": signature,
                "active_rules_count": active_rules_count,
                "rule_text": _build_candidate_rule_text(candidate),
                "human_description": _build_candidate_human_description(candidate),
                **summary,
            }
            row["priority_score"] = _candidate_priority_score(row)
            row["meets_goal"] = _candidate_meets_goal(row)

            existing = candidates_by_signature.get(signature)
            if existing is None:
                candidates_by_signature[signature] = row
                continue
            replace = False
            if int(row["active_rules_count"]) < int(existing["active_rules_count"]):
                replace = True
            elif int(row["active_rules_count"]) == int(existing["active_rules_count"]):
                if float(row["priority_score"]) > float(existing["priority_score"]):
                    replace = True
                elif float(row["priority_score"]) == float(existing["priority_score"]) and str(row["rule_text"]) < str(existing["rule_text"]):
                    replace = True
            if replace:
                candidates_by_signature[signature] = row

    candidates = pd.DataFrame(candidates_by_signature.values())
    if candidates.empty:
        return candidates
    candidates = _sort_summary_frame(
        candidates,
        sort_columns=[
            "meets_goal",
            "priority_score",
            "mean_return_pct",
            "annualized_unit_pnl_pct",
            "win_rate",
            "trades_per_year",
            "max_drawdown_pct",
        ],
        ascending=[False, False, False, False, False, False, True],
    )
    candidates["candidate_rank"] = np.arange(1, len(candidates) + 1)
    candidates["candidate_variant"] = [f"U{idx}" for idx in candidates["candidate_rank"]]
    return candidates


def _build_local_robustness_summary(candidates: pd.DataFrame, best_candidate: pd.Series) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    index_columns = [
        "trigger_idx",
        "range_atr_idx",
        "body_atr_idx",
        "volume_idx",
        "close_to_high_idx",
        "next_green_idx",
        "next_pullback_idx",
        "base_range_idx",
        "base_drift_idx",
        "base_vs_trigger_idx",
    ]
    optional_columns = [
        "min_body_atr",
        "require_next_green",
        "max_next_pullback_frac",
        "max_pre_base_range_pct_60m",
        "max_pre_base_drift_pct_60m",
        "max_pre_base_range_vs_trigger",
    ]
    local_mask = candidates["stop_style"].astype(str) == str(best_candidate["stop_style"])
    for column_name in index_columns:
        local_mask &= (
            (pd.to_numeric(candidates[column_name], errors="coerce") - float(best_candidate[column_name]))
            .abs()
            <= 1
        )
    local = candidates[local_mask].copy()
    if local.empty:
        return pd.DataFrame()
    same_structure_mask = local["stop_style"].astype(str) == str(best_candidate["stop_style"])
    for column_name in optional_columns:
        best_value = best_candidate[column_name]
        if isinstance(best_value, (bool, np.bool_)):
            same_structure_mask &= local[column_name].fillna(False).astype(bool) == bool(best_value)
        else:
            same_structure_mask &= local[column_name].apply(_optional_float).isna() == (pd.isna(best_value) or _optional_float(best_value) is None)
    same_structure = local[same_structure_mask].copy()
    if same_structure.empty:
        same_structure = local.copy()
    return pd.DataFrame(
        [
            {
                "candidate_variant": best_candidate["candidate_variant"],
                "candidate_id": best_candidate["candidate_id"],
                "local_neighbors_count": int(len(local)),
                "local_goal_rate": float(local["meets_goal"].astype(bool).mean()),
                "local_mean_return_p25_pct": float(pd.to_numeric(local["mean_return_pct"], errors="coerce").quantile(0.25)),
                "local_mean_return_median_pct": float(pd.to_numeric(local["mean_return_pct"], errors="coerce").median()),
                "local_annualized_median_pct": float(pd.to_numeric(local["annualized_unit_pnl_pct"], errors="coerce").median()),
                "local_drawdown_p75_pct": float(pd.to_numeric(local["max_drawdown_pct"], errors="coerce").quantile(0.75)),
                "local_positive_months_min": int(pd.to_numeric(local["positive_months_count"], errors="coerce").min()),
                "local_positive_months_median": float(pd.to_numeric(local["positive_months_count"], errors="coerce").median()),
                "same_structure_neighbors_count": int(len(same_structure)),
                "same_structure_goal_rate": float(same_structure["meets_goal"].astype(bool).mean()),
                "same_structure_mean_return_p25_pct": float(pd.to_numeric(same_structure["mean_return_pct"], errors="coerce").quantile(0.25)),
                "same_structure_annualized_median_pct": float(pd.to_numeric(same_structure["annualized_unit_pnl_pct"], errors="coerce").median()),
                "same_structure_drawdown_p75_pct": float(pd.to_numeric(same_structure["max_drawdown_pct"], errors="coerce").quantile(0.75)),
                "same_structure_positive_months_min": int(pd.to_numeric(same_structure["positive_months_count"], errors="coerce").min()),
            }
        ]
    )


def _build_late_holdout_sanity(
    *,
    best_events: pd.DataFrame,
    calendar_months: list[str],
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for split_id, train_months in _UNIFIED_HOLDOUT_SPLITS:
        if len(calendar_months) <= train_months:
            continue
        test_months = calendar_months[train_months:]
        scoped = best_events[best_events["month_utc"].astype(str).isin(test_months)].copy()
        summary = _summarize_events(scoped, calendar_months=test_months)
        if summary is None:
            continue
        rows.append(
            {
                "split_id": split_id,
                "train_months": train_months,
                "test_months": len(test_months),
                "test_month_list": ",".join(test_months),
                **summary,
            }
        )
    return pd.DataFrame(rows)


def _build_candidate_holdout_summary(
    *,
    candidates: pd.DataFrame,
    confirmed_events: pd.DataFrame,
    calendar_months: list[str],
) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    top_candidates = candidates.head(_UNIFIED_HOLDOUT_TOP_CANDIDATES).copy()
    events_by_stop = {
        stop_style: confirmed_events[confirmed_events["stop_style"].astype(str) == str(stop_style)].copy().reset_index(drop=True)
        for stop_style in top_candidates["stop_style"].astype(str).unique().tolist()
    }
    arrays_by_stop = {
        stop_style: {
            "trigger_return_pct": pd.to_numeric(frame["trigger_return_pct"], errors="coerce").to_numpy(dtype="float64"),
            "range_atr": pd.to_numeric(frame["range_atr"], errors="coerce").to_numpy(dtype="float64"),
            "body_atr": pd.to_numeric(frame["body_atr"], errors="coerce").to_numpy(dtype="float64"),
            "volume_mult": pd.to_numeric(frame["volume_mult"], errors="coerce").to_numpy(dtype="float64"),
            "close_to_high_frac": pd.to_numeric(frame["close_to_high_frac"], errors="coerce").to_numpy(dtype="float64"),
            "next_bar_pullback_frac": pd.to_numeric(frame["next_bar_pullback_frac"], errors="coerce").to_numpy(dtype="float64"),
            "pre_base_range_pct_60m": pd.to_numeric(frame["pre_base_range_pct_60m"], errors="coerce").to_numpy(dtype="float64"),
            "pre_base_drift_pct_60m": pd.to_numeric(frame["pre_base_drift_pct_60m"], errors="coerce").to_numpy(dtype="float64"),
            "pre_base_range_vs_trigger": pd.to_numeric(frame["pre_base_range_vs_trigger"], errors="coerce").to_numpy(dtype="float64"),
            "next_bar_is_green": frame["next_bar_is_green"].fillna(False).astype(bool).to_numpy(dtype="bool"),
        }
        for stop_style, frame in events_by_stop.items()
    }

    for _, candidate in top_candidates.iterrows():
        stop_style = str(candidate["stop_style"])
        scoped = events_by_stop[stop_style]
        arrays = arrays_by_stop[stop_style]
        mask = _build_filter_mask(arrays=arrays, candidate=candidate.to_dict())
        selected = scoped.loc[mask].copy().reset_index(drop=True)
        row: dict[str, object] = {
            "candidate_id": candidate["candidate_id"],
            "candidate_variant": candidate["candidate_variant"],
        }
        all_positive = True
        all_non_empty = True
        late_score = 0.0
        for split_id, train_months in _UNIFIED_HOLDOUT_SPLITS:
            if len(calendar_months) <= train_months:
                continue
            test_months = calendar_months[train_months:]
            split_summary = _summarize_events(
                selected[selected["month_utc"].astype(str).isin(test_months)].copy(),
                calendar_months=test_months,
            )
            if split_summary is None:
                split_summary = {
                    "trades_count": 0,
                    "trades_per_year": 0.0,
                    "mean_return_pct": 0.0,
                    "win_rate": 0.0,
                    "annualized_unit_pnl_pct": 0.0,
                    "max_drawdown_pct": 0.0,
                    "positive_months_count": 0,
                    "non_positive_months_count": len(test_months),
                }
            row[f"{split_id}_trades_count"] = split_summary.get("trades_count")
            row[f"{split_id}_trades_per_year"] = split_summary.get("trades_per_year")
            row[f"{split_id}_mean_return_pct"] = split_summary.get("mean_return_pct")
            row[f"{split_id}_win_rate"] = split_summary.get("win_rate")
            row[f"{split_id}_annualized_unit_pnl_pct"] = split_summary.get("annualized_unit_pnl_pct")
            row[f"{split_id}_max_drawdown_pct"] = split_summary.get("max_drawdown_pct")
            row[f"{split_id}_positive_months_count"] = split_summary.get("positive_months_count")
            row[f"{split_id}_non_positive_months_count"] = split_summary.get("non_positive_months_count")
            split_trades = int(split_summary.get("trades_count", 0) or 0)
            split_positive_months = int(split_summary.get("positive_months_count", 0) or 0)
            split_test_months = len(test_months)
            split_mean = float(split_summary.get("mean_return_pct", 0.0) or 0.0)
            split_wr = float(split_summary.get("win_rate", 0.0) or 0.0)
            split_ann = float(split_summary.get("annualized_unit_pnl_pct", 0.0) or 0.0)
            split_dd = float(split_summary.get("max_drawdown_pct", 0.0) or 0.0)
            split_all_positive = split_positive_months == split_test_months and split_test_months > 0
            row[f"{split_id}_all_positive"] = split_all_positive
            all_positive = all_positive and split_all_positive
            all_non_empty = all_non_empty and split_trades > 0
            late_score += min(split_mean, 0.04) * 500.0
            late_score += min(split_wr, 0.70) * 90.0
            late_score += min(split_ann, 3.0) * 25.0
            late_score += split_positive_months * 10.0
            late_score -= max(split_dd - 0.20, 0.0) * 120.0
        row["late_holdout_all_positive"] = all_positive
        row["late_holdout_all_non_empty"] = all_non_empty
        row["late_holdout_score"] = late_score
        rows.append(row)
    return pd.DataFrame(rows)


def _build_candidate_resilience_summary(
    *,
    candidates: pd.DataFrame,
    confirmed_events: pd.DataFrame,
    calendar_months: list[str],
) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    top_candidates = candidates.head(_UNIFIED_RESILIENCE_TOP_CANDIDATES).copy()
    events_by_stop = {
        stop_style: confirmed_events[confirmed_events["stop_style"].astype(str) == str(stop_style)].copy().reset_index(drop=True)
        for stop_style in top_candidates["stop_style"].astype(str).unique().tolist()
    }
    arrays_by_stop = {
        stop_style: {
            "trigger_return_pct": pd.to_numeric(frame["trigger_return_pct"], errors="coerce").to_numpy(dtype="float64"),
            "range_atr": pd.to_numeric(frame["range_atr"], errors="coerce").to_numpy(dtype="float64"),
            "body_atr": pd.to_numeric(frame["body_atr"], errors="coerce").to_numpy(dtype="float64"),
            "volume_mult": pd.to_numeric(frame["volume_mult"], errors="coerce").to_numpy(dtype="float64"),
            "close_to_high_frac": pd.to_numeric(frame["close_to_high_frac"], errors="coerce").to_numpy(dtype="float64"),
            "next_bar_pullback_frac": pd.to_numeric(frame["next_bar_pullback_frac"], errors="coerce").to_numpy(dtype="float64"),
            "pre_base_range_pct_60m": pd.to_numeric(frame["pre_base_range_pct_60m"], errors="coerce").to_numpy(dtype="float64"),
            "pre_base_drift_pct_60m": pd.to_numeric(frame["pre_base_drift_pct_60m"], errors="coerce").to_numpy(dtype="float64"),
            "pre_base_range_vs_trigger": pd.to_numeric(frame["pre_base_range_vs_trigger"], errors="coerce").to_numpy(dtype="float64"),
            "next_bar_is_green": frame["next_bar_is_green"].fillna(False).astype(bool).to_numpy(dtype="bool"),
        }
        for stop_style, frame in events_by_stop.items()
    }
    calendar_months_count = max(1, len(calendar_months))

    for _, candidate in top_candidates.iterrows():
        stop_style = str(candidate["stop_style"])
        scoped = events_by_stop[stop_style]
        arrays = arrays_by_stop[stop_style]
        mask = _build_filter_mask(arrays=arrays, candidate=candidate.to_dict())
        selected = scoped.loc[mask].copy().reset_index(drop=True)
        if selected.empty:
            continue

        symbol_pnl = (
            selected.groupby("symbol", sort=False)["exit_return_pct"]
            .sum()
            .sort_values(ascending=False)
        )
        total_unit_pnl = float(pd.to_numeric(selected["exit_return_pct"], errors="coerce").fillna(0.0).sum())

        def _top_share(top_n: int) -> float | None:
            if symbol_pnl.empty or total_unit_pnl == 0.0:
                return None
            return float(symbol_pnl.head(top_n).sum() / total_unit_pnl)

        def _annualized_without_top_symbols(top_n: int) -> float:
            excluded_symbols = set(symbol_pnl.head(top_n).index.tolist())
            scoped_without = selected[~selected["symbol"].astype(str).isin(excluded_symbols)].copy()
            summary = _summarize_events(scoped_without, calendar_months=calendar_months)
            if summary is None:
                return 0.0
            return float(summary.get("annualized_unit_pnl_pct", 0.0) or 0.0)

        worst_leave_month_annualized = 0.0
        if calendar_months:
            leave_month_values: list[float] = []
            for month in calendar_months:
                scoped_without_month = selected[selected["month_utc"].astype(str) != str(month)].copy()
                summary = _summarize_events(
                    scoped_without_month,
                    calendar_months=[item for item in calendar_months if item != month],
                )
                if summary is None:
                    leave_month_values.append(0.0)
                else:
                    leave_month_values.append(float(summary.get("annualized_unit_pnl_pct", 0.0) or 0.0))
            if leave_month_values:
                worst_leave_month_annualized = float(min(leave_month_values))

        annualized_remove_top1 = _annualized_without_top_symbols(1)
        annualized_remove_top3 = _annualized_without_top_symbols(3)
        annualized_remove_top5 = _annualized_without_top_symbols(5)
        top1_share = _top_share(1)
        top3_share = _top_share(3)
        top5_share = _top_share(5)

        resilience_score = 0.0
        resilience_score += min(annualized_remove_top1, 2.0) * 10.0
        resilience_score += min(annualized_remove_top3, 2.0) * 30.0
        resilience_score += max(annualized_remove_top5, -1.0) * 45.0
        resilience_score += min(worst_leave_month_annualized, 2.0) * 20.0
        resilience_score -= float(top1_share or 0.0) * 10.0
        resilience_score -= float(top3_share or 0.0) * 25.0
        resilience_score -= float(max((top5_share or 0.0) - 1.0, 0.0)) * 40.0

        rows.append(
            {
                "candidate_id": candidate["candidate_id"],
                "candidate_variant": candidate["candidate_variant"],
                "symbol_count": int(selected["symbol"].astype(str).nunique()),
                "top1_symbol_pnl_share": top1_share,
                "top3_symbol_pnl_share": top3_share,
                "top5_symbol_pnl_share": top5_share,
                "annualized_remove_top1_unit_pnl_pct": annualized_remove_top1,
                "annualized_remove_top3_unit_pnl_pct": annualized_remove_top3,
                "annualized_remove_top5_unit_pnl_pct": annualized_remove_top5,
                "worst_leave_month_annualized_unit_pnl_pct": worst_leave_month_annualized,
                "remove_top5_stays_positive": annualized_remove_top5 > 0.0,
                "resilience_score": resilience_score,
                "calendar_months_count": calendar_months_count,
            }
        )
    return pd.DataFrame(rows)


def build_hourly_asia_pump_unified_artifacts(
    *,
    confirmed_events_path: Path,
    output_dir: Path,
    logger: logging.Logger | None = None,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    charts_dir = output_dir / "unified_charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    if logger is not None:
        logger.info("unified-search: stage=load-inputs confirmed_events=%s", confirmed_events_path)
    raw_confirmed_events = pd.read_csv(confirmed_events_path)
    confirmed_events = _prepare_confirmed_unique_events(confirmed_events_path)
    calendar_months = _calendar_months_from_frames(confirmed_events)
    if logger is not None:
        logger.info(
            "unified-search: stage=prepared raw_rows=%s unique_events=%s removed_duplicates=%s months=%s",
            len(raw_confirmed_events),
            len(confirmed_events),
            len(raw_confirmed_events) - len(confirmed_events),
            len(calendar_months),
        )

    candidate_summary = _build_candidate_rows(
        confirmed_events=confirmed_events,
        calendar_months=calendar_months,
        logger=logger,
    )

    candidate_summary_path = output_dir / "unified_strategy_candidate_summary.csv"
    best_summary_path = output_dir / "unified_strategy_best_summary.csv"
    best_monthly_path = output_dir / "unified_strategy_best_monthly.csv"
    best_events_path = output_dir / "unified_strategy_best_events.csv"
    holdout_path = output_dir / "unified_strategy_best_holdout.csv"
    candidate_holdout_path = output_dir / "unified_strategy_candidate_holdout.csv"
    candidate_resilience_path = output_dir / "unified_strategy_candidate_resilience.csv"
    robustness_path = output_dir / "unified_strategy_best_robustness.csv"
    context_path = output_dir / "unified_strategy_context.json"
    report_path = output_dir / "unified_strategy_report.md"
    equity_chart_path = charts_dir / "unified_equity_curve.png"
    monthly_chart_path = charts_dir / "unified_monthly_returns.png"
    distribution_chart_path = charts_dir / "unified_trade_distribution.png"
    timeline_chart_path = charts_dir / "unified_trade_timeline.png"

    if candidate_summary.empty:
        pd.DataFrame().to_csv(candidate_summary_path, index=False)
        pd.DataFrame().to_csv(best_summary_path, index=False)
        pd.DataFrame().to_csv(best_monthly_path, index=False)
        pd.DataFrame().to_csv(best_events_path, index=False)
        pd.DataFrame().to_csv(holdout_path, index=False)
        pd.DataFrame().to_csv(candidate_holdout_path, index=False)
        pd.DataFrame().to_csv(candidate_resilience_path, index=False)
        pd.DataFrame().to_csv(robustness_path, index=False)
        context_path.write_text(json.dumps({"status": "empty"}, ensure_ascii=False, indent=2), encoding="utf-8")
        report_path.write_text("# Unified XX:00 Report\n\nКандидаты не найдены.\n", encoding="utf-8")
        return {
            "candidate_summary": candidate_summary_path,
            "best_summary": best_summary_path,
            "best_monthly": best_monthly_path,
            "best_events": best_events_path,
            "best_holdout": holdout_path,
            "candidate_resilience": candidate_resilience_path,
            "best_robustness": robustness_path,
            "context": context_path,
            "report": report_path,
            "goal_passed": False,
        }

    candidate_holdout_summary = _build_candidate_holdout_summary(
        candidates=candidate_summary,
        confirmed_events=confirmed_events,
        calendar_months=calendar_months,
    )
    if not candidate_holdout_summary.empty:
        candidate_summary = candidate_summary.merge(candidate_holdout_summary, on=["candidate_id", "candidate_variant"], how="left")
    if "late_holdout_all_positive" in candidate_summary.columns:
        candidate_summary["late_holdout_all_positive"] = candidate_summary["late_holdout_all_positive"].astype("boolean").fillna(False).astype(bool)
    else:
        candidate_summary["late_holdout_all_positive"] = pd.Series(False, index=candidate_summary.index, dtype="bool")
    if "late_holdout_all_non_empty" in candidate_summary.columns:
        candidate_summary["late_holdout_all_non_empty"] = candidate_summary["late_holdout_all_non_empty"].astype("boolean").fillna(False).astype(bool)
    else:
        candidate_summary["late_holdout_all_non_empty"] = pd.Series(False, index=candidate_summary.index, dtype="bool")
    candidate_summary["late_holdout_score"] = pd.to_numeric(candidate_summary.get("late_holdout_score", pd.Series(0.0, index=candidate_summary.index)), errors="coerce").fillna(0.0)
    candidate_summary["stable_priority_score"] = (
        pd.to_numeric(candidate_summary["priority_score"], errors="coerce").fillna(0.0)
        + candidate_summary["late_holdout_score"]
        + (candidate_summary["late_holdout_all_positive"].astype(int) * 250.0)
        + (candidate_summary["late_holdout_all_non_empty"].astype(int) * 40.0)
    )
    candidate_summary = _sort_summary_frame(
        candidate_summary,
        sort_columns=[
            "meets_goal",
            "late_holdout_all_positive",
            "late_holdout_all_non_empty",
            "stable_priority_score",
            "priority_score",
            "mean_return_pct",
            "annualized_unit_pnl_pct",
            "max_drawdown_pct",
        ],
        ascending=[False, False, False, False, False, False, False, True],
    )
    candidate_resilience_summary = _build_candidate_resilience_summary(
        candidates=candidate_summary,
        confirmed_events=confirmed_events,
        calendar_months=calendar_months,
    )
    if not candidate_resilience_summary.empty:
        candidate_summary = candidate_summary.merge(
            candidate_resilience_summary.drop(columns=["candidate_variant"], errors="ignore"),
            on="candidate_id",
            how="left",
        )
    candidate_summary["resilience_score"] = pd.to_numeric(
        candidate_summary.get("resilience_score", pd.Series(0.0, index=candidate_summary.index)),
        errors="coerce",
    ).fillna(0.0)
    candidate_summary["robust_selection_score"] = candidate_summary["stable_priority_score"] + candidate_summary["resilience_score"]
    candidate_summary = _sort_summary_frame(
        candidate_summary,
        sort_columns=[
            "meets_goal",
            "late_holdout_all_positive",
            "late_holdout_all_non_empty",
            "robust_selection_score",
            "stable_priority_score",
            "priority_score",
            "mean_return_pct",
            "annualized_unit_pnl_pct",
            "max_drawdown_pct",
        ],
        ascending=[False, False, False, False, False, False, False, False, True],
    )
    candidate_summary["candidate_rank"] = np.arange(1, len(candidate_summary) + 1)
    candidate_summary["candidate_variant"] = [f"U{idx}" for idx in candidate_summary["candidate_rank"]]
    if not candidate_holdout_summary.empty:
        candidate_holdout_summary = candidate_holdout_summary.drop(columns=["candidate_variant"], errors="ignore").merge(
            candidate_summary[["candidate_id", "candidate_variant"]],
            on="candidate_id",
            how="left",
        )
    if not candidate_resilience_summary.empty:
        candidate_resilience_summary = candidate_resilience_summary.drop(columns=["candidate_variant"], errors="ignore").merge(
            candidate_summary[["candidate_id", "candidate_variant"]],
            on="candidate_id",
            how="left",
        )

    best_candidate = candidate_summary.iloc[0].copy()
    best_scoped = confirmed_events[confirmed_events["stop_style"].astype(str) == str(best_candidate["stop_style"])].copy().reset_index(drop=True)
    best_arrays = {
        "trigger_return_pct": pd.to_numeric(best_scoped["trigger_return_pct"], errors="coerce").to_numpy(dtype="float64"),
        "range_atr": pd.to_numeric(best_scoped["range_atr"], errors="coerce").to_numpy(dtype="float64"),
        "body_atr": pd.to_numeric(best_scoped["body_atr"], errors="coerce").to_numpy(dtype="float64"),
        "volume_mult": pd.to_numeric(best_scoped["volume_mult"], errors="coerce").to_numpy(dtype="float64"),
        "close_to_high_frac": pd.to_numeric(best_scoped["close_to_high_frac"], errors="coerce").to_numpy(dtype="float64"),
        "next_bar_pullback_frac": pd.to_numeric(best_scoped["next_bar_pullback_frac"], errors="coerce").to_numpy(dtype="float64"),
        "pre_base_range_pct_60m": pd.to_numeric(best_scoped["pre_base_range_pct_60m"], errors="coerce").to_numpy(dtype="float64"),
        "pre_base_drift_pct_60m": pd.to_numeric(best_scoped["pre_base_drift_pct_60m"], errors="coerce").to_numpy(dtype="float64"),
        "pre_base_range_vs_trigger": pd.to_numeric(best_scoped["pre_base_range_vs_trigger"], errors="coerce").to_numpy(dtype="float64"),
        "next_bar_is_green": best_scoped["next_bar_is_green"].fillna(False).astype(bool).to_numpy(dtype="bool"),
    }
    best_mask = _build_filter_mask(arrays=best_arrays, candidate=best_candidate.to_dict())
    best_events = best_scoped.loc[best_mask].copy().reset_index(drop=True)
    best_events["candidate_id"] = str(best_candidate["candidate_id"])
    best_events["candidate_variant"] = str(best_candidate["candidate_variant"])

    best_summary = candidate_summary.head(1).copy()
    best_monthly = _build_monthly_returns_frame(best_events, calendar_months=calendar_months)
    holdout_sanity = _build_late_holdout_sanity(best_events=best_events, calendar_months=calendar_months)
    robustness_summary = _build_local_robustness_summary(candidate_summary, best_candidate)
    if not candidate_resilience_summary.empty:
        best_resilience = candidate_resilience_summary[
            candidate_resilience_summary["candidate_id"].astype(str) == str(best_candidate["candidate_id"])
        ].copy()
        if not best_resilience.empty:
            robustness_summary = robustness_summary.merge(
                best_resilience.drop(columns=["candidate_variant"], errors="ignore"),
                on="candidate_id",
                how="left",
            )

    candidate_summary.to_csv(candidate_summary_path, index=False)
    best_summary.to_csv(best_summary_path, index=False)
    best_monthly.to_csv(best_monthly_path, index=False)
    best_events.to_csv(best_events_path, index=False)
    holdout_sanity.to_csv(holdout_path, index=False)
    candidate_holdout_summary.to_csv(candidate_holdout_path, index=False)
    candidate_resilience_summary.to_csv(candidate_resilience_path, index=False)
    robustness_summary.to_csv(robustness_path, index=False)

    _save_priority_equity_curve_chart(best_events, equity_chart_path)
    _save_priority_monthly_returns_chart(best_monthly, monthly_chart_path)
    _save_priority_trade_distribution_chart(best_events, distribution_chart_path)
    _save_priority_trade_timeline_chart(best_events, timeline_chart_path)

    context = {
        "version": "2026-03-28-unified-v2",
        "goal": {
            "annualized_unit_pnl_pct_min": _UNIFIED_MIN_ANNUALIZED_UNIT_PNL_PCT,
            "mean_return_pct_min": _UNIFIED_MIN_MEAN_RETURN_PCT,
            "win_rate_min": _UNIFIED_MIN_WIN_RATE,
            "trades_per_year_min": _UNIFIED_MIN_TRADES_PER_YEAR,
            "max_drawdown_pct_max": _UNIFIED_MAX_DRAWDOWN_PCT,
            "positive_months_min": _UNIFIED_MIN_POSITIVE_MONTHS,
        },
        "search_scope": {
            "uses_hour_utc_in_optimization": False,
            "same_entry_for_all_hours": True,
            "same_stop_for_all_hours": True,
            "same_exit_for_all_hours": True,
            "source": str(confirmed_events_path),
            "calendar_months": calendar_months,
            "raw_confirmed_rows": int(len(raw_confirmed_events)),
            "unique_confirmed_events": int(len(confirmed_events)),
        },
        "best_candidate": best_candidate.to_dict(),
        "charts": {
            "equity_curve": str(equity_chart_path),
            "monthly_returns": str(monthly_chart_path),
            "trade_distribution": str(distribution_chart_path),
            "trade_timeline": str(timeline_chart_path),
        },
        "selection_logic": {
            "selection_mode": "full_year_plus_late_holdout_plus_resilience",
            "late_holdout_splits": [split_id for split_id, _ in _UNIFIED_HOLDOUT_SPLITS],
            "resilience_top_candidates": _UNIFIED_RESILIENCE_TOP_CANDIDATES,
        },
    }
    context_path.write_text(json.dumps(_to_json_ready(context), ensure_ascii=False, indent=2), encoding="utf-8")

    report_lines = [
        "# Unified XX:00 Report",
        "",
        "## Что Это За Анализ",
        "",
        "Здесь ищется одна общая стратегия для всех `XX:00` внутри окна, без развилок по отдельным часам.",
        "В оптимизации не используется `hour_utc`: час можно смотреть только как пост-анализ, но не как правило входа.",
        "",
        "## Лучшая Единая Конфигурация",
        "",
        _frame_to_markdown(
            best_summary,
            columns=(
                "candidate_variant",
                "stop_style",
                "trades_count",
                "trades_per_year",
                "mean_return_pct",
                "median_return_pct",
                "win_rate",
                "annualized_unit_pnl_pct",
                "max_drawdown_pct",
                "mean_pos_trade_pct",
                "mean_neg_trade_pct",
                "positive_months_count",
                "non_positive_months_count",
                "late_holdout_all_positive",
            ),
            header_labels={
                "candidate_variant": "Вариант",
                "stop_style": "Стоп",
                "trades_count": "Сделок",
                "trades_per_year": "Сделок/Год",
                "mean_return_pct": "Средняя Сделка",
                "median_return_pct": "Медиана Сделки",
                "win_rate": "WR",
                "annualized_unit_pnl_pct": "Годовой Unit PnL",
                "max_drawdown_pct": "Max DD",
                "mean_pos_trade_pct": "Средняя Плюсовая",
                "mean_neg_trade_pct": "Средняя Минусовая",
                "positive_months_count": "Плюсовых Месяцев",
                "non_positive_months_count": "Неплюсовых Месяцев",
                "late_holdout_all_positive": "Позднее Окно Всё Плюс",
            },
        ),
        "",
        "Правило:",
        "",
        str(best_candidate["rule_text"]),
        "",
        "Человеческое описание:",
        "",
        str(best_candidate["human_description"]),
        "",
        "## Помесячное Распределение",
        "",
        _frame_to_markdown(
            best_monthly,
            columns=("month_utc", "total_return_pct", "trades_count", "month_positive"),
            header_labels={
                "month_utc": "Месяц",
                "total_return_pct": "Сумма Сделок",
                "trades_count": "Сделок",
                "month_positive": "Плюсовой",
            },
        ),
        "",
        "## Поздняя Sanity-Проверка",
        "",
        _frame_to_markdown(
            holdout_sanity,
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
                "split_id": "Окно",
                "trades_count": "Сделок",
                "trades_per_year": "Сделок/Год",
                "mean_return_pct": "Средняя Сделка",
                "win_rate": "WR",
                "annualized_unit_pnl_pct": "Годовой Unit PnL",
                "max_drawdown_pct": "Max DD",
                "positive_months_count": "Плюсовых Месяцев",
                "non_positive_months_count": "Неплюсовых Месяцев",
            },
        ),
        "",
        "## Устойчивость Соседних Порогов",
        "",
        _frame_to_markdown(
            robustness_summary,
            columns=(
                "local_neighbors_count",
                "local_goal_rate",
                "local_mean_return_p25_pct",
                "local_mean_return_median_pct",
                "local_annualized_median_pct",
                "local_drawdown_p75_pct",
                "local_positive_months_min",
                "local_positive_months_median",
                "same_structure_neighbors_count",
                "same_structure_goal_rate",
                "same_structure_mean_return_p25_pct",
                "same_structure_annualized_median_pct",
                "same_structure_drawdown_p75_pct",
                "same_structure_positive_months_min",
            ),
            header_labels={
                "local_neighbors_count": "Соседей",
                "local_goal_rate": "Доля Проходящих",
                "local_mean_return_p25_pct": "P25 Средней Сделки",
                "local_mean_return_median_pct": "Медиана Средней Сделки",
                "local_annualized_median_pct": "Медиана Годового Unit PnL",
                "local_drawdown_p75_pct": "P75 Max DD",
                "local_positive_months_min": "Минимум Плюсовых Месяцев",
                "local_positive_months_median": "Медиана Плюсовых Месяцев",
                "same_structure_neighbors_count": "Соседей Той Же Структуры",
                "same_structure_goal_rate": "Доля Проходящих Той Же Структуры",
                "same_structure_mean_return_p25_pct": "P25 Средней Сделки Той Же Структуры",
                "same_structure_annualized_median_pct": "Медиана Годового Unit PnL Той Же Структуры",
                "same_structure_drawdown_p75_pct": "P75 Max DD Той Же Структуры",
                "same_structure_positive_months_min": "Минимум Плюсовых Месяцев Той Же Структуры",
            },
        ),
        "",
        "## Топ Unified-Кандидатов",
        "",
        _frame_to_markdown(
            candidate_summary.head(_UNIFIED_TOP_CANDIDATES_LIMIT),
            columns=(
                "candidate_variant",
                "rule_text",
                "trades_per_year",
                "mean_return_pct",
                "win_rate",
                "annualized_unit_pnl_pct",
                "max_drawdown_pct",
                "positive_months_count",
            ),
            header_labels={
                "candidate_variant": "Вариант",
                "rule_text": "Правило",
                "trades_per_year": "Сделок/Год",
                "mean_return_pct": "Средняя Сделка",
                "win_rate": "WR",
                "annualized_unit_pnl_pct": "Годовой Unit PnL",
                "max_drawdown_pct": "Max DD",
                "positive_months_count": "Плюсовых Месяцев",
            },
        ),
        "",
        "## Вывод",
        "",
        "Оптимальный конфиг в этом отчёте выбирается по полному году целиком, без monthly-adaptive логики и без hour-specific развилок.",
        "Дополнительно лучший вариант выбирается не только по full-year, но и с учётом late-holdout на последних месяцах.",
        (
            "Текущий лучший unified-кандидат проходит целевой минимум."
            if bool(best_candidate["meets_goal"])
            else "Текущий лучший unified-кандидат ещё не проходит целевой минимум, но это ближайший честный near-miss."
        ),
        "",
        "Графики:",
        "",
        f"- equity: `{equity_chart_path}`",
        f"- months: `{monthly_chart_path}`",
        f"- trades: `{distribution_chart_path}`",
        f"- timeline: `{timeline_chart_path}`",
    ]
    report_lines.extend(
        [
            "",
            "## Проверка На Перекос По Символам И Месяцам",
            "",
            _frame_to_markdown(
                robustness_summary,
                columns=(
                    "symbol_count",
                    "top1_symbol_pnl_share",
                    "top3_symbol_pnl_share",
                    "top5_symbol_pnl_share",
                    "annualized_remove_top1_unit_pnl_pct",
                    "annualized_remove_top3_unit_pnl_pct",
                    "annualized_remove_top5_unit_pnl_pct",
                    "worst_leave_month_annualized_unit_pnl_pct",
                    "remove_top5_stays_positive",
                ),
                header_labels={
                    "symbol_count": "Символов",
                    "top1_symbol_pnl_share": "Доля Топ-1 Символа",
                    "top3_symbol_pnl_share": "Доля Топ-3 Символов",
                    "top5_symbol_pnl_share": "Доля Топ-5 Символов",
                    "annualized_remove_top1_unit_pnl_pct": "Годовой Unit PnL Без Топ-1",
                    "annualized_remove_top3_unit_pnl_pct": "Годовой Unit PnL Без Топ-3",
                    "annualized_remove_top5_unit_pnl_pct": "Годовой Unit PnL Без Топ-5",
                    "worst_leave_month_annualized_unit_pnl_pct": "Худший Leave-One-Month-Out",
                    "remove_top5_stays_positive": "Без Топ-5 Всё Ещё Плюс",
                },
            ),
            "",
            "Коротко: лучший вариант теперь выбирается не только по full-year и late-holdout, но и по устойчивости к выбрасыванию лучших символов и отдельных месяцев.",
        ]
    )
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    if logger is not None:
        logger.info(
            "unified-search: stage=complete best=%s goal_passed=%s trades_per_year=%.1f mean_trade=%.2f%% wr=%.2f%% dd=%.2f%%",
            best_candidate["candidate_variant"],
            bool(best_candidate["meets_goal"]),
            float(best_candidate["trades_per_year"]),
            float(best_candidate["mean_return_pct"]) * 100.0,
            float(best_candidate["win_rate"]) * 100.0,
            float(best_candidate["max_drawdown_pct"]) * 100.0,
        )

    return {
        "candidate_summary": candidate_summary_path,
        "best_summary": best_summary_path,
        "best_monthly": best_monthly_path,
        "best_events": best_events_path,
        "best_holdout": holdout_path,
        "candidate_holdout": candidate_holdout_path,
        "candidate_resilience": candidate_resilience_path,
        "best_robustness": robustness_path,
        "context": context_path,
        "report": report_path,
        "equity_chart": equity_chart_path,
        "monthly_chart": monthly_chart_path,
        "distribution_chart": distribution_chart_path,
        "timeline_chart": timeline_chart_path,
        "goal_passed": bool(best_candidate["meets_goal"]),
    }
