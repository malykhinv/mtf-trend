from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from domain.enums.timeframe import Timeframe
from strategy.hourly_asia_pump.research import _format_duration, _progress_snapshot
from strategy.hourly_asia_pump.static_combo import (
    _build_monthly_returns_frame,
    _calendar_months_from_frames,
    _frame_to_markdown,
    _summarize_events,
)
from vectorbt_runner.data_preparer import DataPreparer

module_logger = logging.getLogger(__name__)

_SHORT_MAX_HOLD_MINUTES = 360
_SHORT_MIN_RR = 1.5
_SHORT_MIN_TRADES_PER_YEAR = 50.0
_SHORT_MIN_MEAN_RETURN_PCT = 0.025
_SHORT_MIN_WIN_RATE = 0.40
_SHORT_MIN_ANNUALIZED_UNIT_PNL_PCT = 1.0
_SHORT_MAX_DRAWDOWN_PCT = 0.30
_SHORT_MIN_POSITIVE_MONTHS = 9
_SHORT_MIN_STABLE_POSITIVE_MONTHS = 7
_SHORT_PROGRESS_LOG_EVERY_SYMBOLS = 25


@dataclass(frozen=True, slots=True)
class ShortSignalProfile:
    profile_id: str
    min_trigger_return_pct: float
    min_range_atr: float
    min_volume_mult: float
    max_close_to_high_frac: float


@dataclass(frozen=True, slots=True)
class ShortTriggerPressureProfile:
    profile_id: str
    min_upper_wick_frac: float
    max_body_frac: float


@dataclass(frozen=True, slots=True)
class ShortNextPressureProfile:
    profile_id: str
    min_next_pullback_frac: float
    max_next_pullback_frac: float
    min_next_close_from_high_frac: float
    max_next_close_pos_in_bar: float
    max_next_return_pct: float


@dataclass(frozen=True, slots=True)
class ShortGeometry:
    geometry_id: str
    entry_style: str
    entry_from_high_frac: float | None
    max_entry_bars: int
    stop_buffer_frac: float
    target_rr: float
    max_open_entry_from_high_frac: float | None = None


@dataclass(frozen=True, slots=True)
class ShortExecutionModel:
    model_id: str
    label: str
    signal_profile: ShortSignalProfile
    trigger_pressure_profile: ShortTriggerPressureProfile
    next_pressure_profile: ShortNextPressureProfile
    geometry: ShortGeometry
    rule_text: str


def _safe_float(value: object) -> float | None:
    if value is None or value is pd.NA:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric):
        return None
    return numeric


def _numeric_column(frame: pd.DataFrame, column: str) -> pd.Series:
    if frame.empty or column not in frame.columns:
        return pd.Series(0.0, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)


def _prepare_anomaly_events(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if frame.empty:
        return frame
    prepared = frame.sort_values(["symbol", "timestamp_ms"]).drop_duplicates(
        ["symbol", "timeframe", "row_index", "timestamp_ms"],
        keep="first",
    ).copy()
    prepared["timestamp_ms"] = pd.to_numeric(prepared["timestamp_ms"], errors="coerce")
    prepared["row_index"] = pd.to_numeric(prepared["row_index"], errors="coerce")
    prepared["timestamp_utc"] = pd.to_datetime(prepared["timestamp_utc"], utc=True, errors="coerce")
    prepared["month_utc"] = prepared["timestamp_utc"].dt.strftime("%Y-%m")
    prepared = prepared[prepared["timeframe"].astype(str) == Timeframe.M5.value].copy()
    return prepared.reset_index(drop=True)


def _build_signal_profiles() -> tuple[ShortSignalProfile, ...]:
    return (
        ShortSignalProfile("sig_weak", 0.050, 6.0, 7.0, 0.15),
        ShortSignalProfile("sig_mid", 0.065, 6.0, 10.0, 0.10),
        ShortSignalProfile("sig_strong", 0.080, 8.0, 15.0, 0.10),
    )


def _build_trigger_pressure_profiles() -> tuple[ShortTriggerPressureProfile, ...]:
    return (
        ShortTriggerPressureProfile("tp_none", 0.0, 1.00),
        ShortTriggerPressureProfile("tp_wick05", 0.05, 0.95),
        ShortTriggerPressureProfile("tp_wick10", 0.10, 0.90),
        ShortTriggerPressureProfile("tp_wick15", 0.15, 0.85),
    )


def _build_next_pressure_profiles() -> tuple[ShortNextPressureProfile, ...]:
    return (
        ShortNextPressureProfile("np_soft", 0.15, 0.50, 0.10, 0.65, 0.01),
        ShortNextPressureProfile("np_mid", 0.25, 0.50, 0.20, 0.50, 0.00),
        ShortNextPressureProfile("np_strong", 0.33, 0.50, 0.30, 0.35, 0.00),
        ShortNextPressureProfile("np_exhaust", 0.45, 0.50, 0.40, 0.20, 0.00),
    )


def _build_geometries() -> tuple[ShortGeometry, ...]:
    return (
        ShortGeometry("limit_top05_s05_rr15", "limit_zone", 0.05, 3, 0.05, 1.5),
        ShortGeometry("limit_top05_s10_rr20", "limit_zone", 0.05, 3, 0.10, 2.0),
        ShortGeometry("limit_top10_s10_rr15", "limit_zone", 0.10, 3, 0.10, 1.5),
        ShortGeometry("limit_top10_s10_rr20", "limit_zone", 0.10, 3, 0.10, 2.0),
        ShortGeometry("limit_top20_s10_rr15", "limit_zone", 0.20, 3, 0.10, 1.5),
        ShortGeometry("open3_s05_rr15", "third_open", None, 0, 0.05, 1.5, max_open_entry_from_high_frac=0.40),
        ShortGeometry("open3_s10_rr15", "third_open", None, 0, 0.10, 1.5, max_open_entry_from_high_frac=0.40),
        ShortGeometry("open3_s10_rr20", "third_open", None, 0, 0.10, 2.0, max_open_entry_from_high_frac=0.40),
    )


def _build_rule_text(
    signal_profile: ShortSignalProfile,
    trigger_pressure_profile: ShortTriggerPressureProfile,
    next_pressure_profile: ShortNextPressureProfile,
    geometry: ShortGeometry,
) -> str:
    parts = [
        signal_profile.profile_id,
        trigger_pressure_profile.profile_id,
        next_pressure_profile.profile_id,
        geometry.geometry_id,
        f"trigger>={signal_profile.min_trigger_return_pct:.3f}",
        f"range_atr>={signal_profile.min_range_atr:.1f}",
        f"volume>={signal_profile.min_volume_mult:.1f}",
        f"close_to_high<={signal_profile.max_close_to_high_frac:.2f}",
        f"upper_wick>={trigger_pressure_profile.min_upper_wick_frac:.2f}",
        f"body<={trigger_pressure_profile.max_body_frac:.2f}",
        f"next_pb>={next_pressure_profile.min_next_pullback_frac:.2f}",
        f"next_pb<={next_pressure_profile.max_next_pullback_frac:.2f}",
        f"next_close_from_high>={next_pressure_profile.min_next_close_from_high_frac:.2f}",
        f"next_close_pos<={next_pressure_profile.max_next_close_pos_in_bar:.2f}",
        f"next_ret<={next_pressure_profile.max_next_return_pct:.3f}",
        f"rr={geometry.target_rr:.1f}",
        f"stop_buf={geometry.stop_buffer_frac:.2f}",
    ]
    if geometry.entry_style == "limit_zone":
        parts.append(f"entry_top={geometry.entry_from_high_frac:.2f}")
        parts.append(f"wait<={geometry.max_entry_bars}bars")
    else:
        parts.append(f"open3_top<={geometry.max_open_entry_from_high_frac:.2f}")
    return "; ".join(parts)


def _build_execution_models() -> list[ShortExecutionModel]:
    models: list[ShortExecutionModel] = []
    for signal_profile in _build_signal_profiles():
        for trigger_pressure_profile in _build_trigger_pressure_profiles():
            for next_pressure_profile in _build_next_pressure_profiles():
                for geometry in _build_geometries():
                    payload = {
                        "signal": signal_profile.profile_id,
                        "trigger_pressure": trigger_pressure_profile.profile_id,
                        "next_pressure": next_pressure_profile.profile_id,
                        "geometry": geometry.geometry_id,
                    }
                    model_hash = hashlib.md5(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:12]
                    models.append(
                        ShortExecutionModel(
                            model_id=f"short_{model_hash}",
                            label=" | ".join(
                                (
                                    signal_profile.profile_id,
                                    trigger_pressure_profile.profile_id,
                                    next_pressure_profile.profile_id,
                                    geometry.geometry_id,
                                )
                            ),
                            signal_profile=signal_profile,
                            trigger_pressure_profile=trigger_pressure_profile,
                            next_pressure_profile=next_pressure_profile,
                            geometry=geometry,
                            rule_text=_build_rule_text(
                                signal_profile=signal_profile,
                                trigger_pressure_profile=trigger_pressure_profile,
                                next_pressure_profile=next_pressure_profile,
                                geometry=geometry,
                            ),
                        )
                    )
    return models


def _simulate_limit_zone_geometry(
    *,
    timestamps_1m: pd.Index,
    open_values_1m: list[float],
    high_values_1m: list[float],
    low_values_1m: list[float],
    close_values_1m: list[float],
    event_timestamp_ms: int,
    trigger_high: float,
    trigger_low: float,
    trigger_open: float,
    trigger_range: float,
    geometry: ShortGeometry,
    commission_rate: float,
) -> dict[str, object] | None:
    entry_price = trigger_high - float(geometry.entry_from_high_frac or 0.0) * trigger_range
    midpoint_price = trigger_high - 0.5 * trigger_range
    stop_price = trigger_high + geometry.stop_buffer_frac * trigger_range
    risk_pct = (stop_price - entry_price) / entry_price if entry_price > 0 else None
    if risk_pct is None or risk_pct <= 0.0 or geometry.target_rr < _SHORT_MIN_RR:
        return None
    target_price = entry_price - geometry.target_rr * (stop_price - entry_price)
    reward_pct = (entry_price - target_price) / entry_price if entry_price > 0 else None
    if reward_pct is None or reward_pct / risk_pct < _SHORT_MIN_RR:
        return None

    start_ts = event_timestamp_ms + 5 * 60_000
    entry_deadline_ts = start_ts + geometry.max_entry_bars * 5 * 60_000
    finish_ts = start_ts + _SHORT_MAX_HOLD_MINUTES * 60_000
    start_idx = timestamps_1m.searchsorted(start_ts, side="left")
    end_idx = timestamps_1m.searchsorted(finish_ts, side="left")
    if end_idx <= start_idx:
        return None

    entered = False
    entry_timestamp_ms: int | None = None
    max_favorable_excursion = 0.0
    max_adverse_excursion = 0.0
    bars_held = 0
    exit_price: float | None = None
    exit_reason = "no_entry"
    exit_timestamp_ms: int | None = None

    for position in range(start_idx, end_idx):
        timestamp_ms = int(timestamps_1m[position])
        if not entered and timestamp_ms >= entry_deadline_ts:
            break
        open_price = float(open_values_1m[position])
        high_price = float(high_values_1m[position])
        low_price = float(low_values_1m[position])
        close_price = float(close_values_1m[position])

        if not entered:
            if open_price >= entry_price:
                entered = True
                entry_timestamp_ms = timestamp_ms
            elif low_price <= midpoint_price and high_price < entry_price:
                break
            elif high_price >= entry_price and low_price <= midpoint_price:
                break
            elif high_price >= entry_price:
                entered = True
                entry_timestamp_ms = timestamp_ms
            else:
                continue

        bars_held += 1
        max_favorable_excursion = max(max_favorable_excursion, (entry_price - low_price) / entry_price)
        max_adverse_excursion = max(max_adverse_excursion, (high_price - entry_price) / entry_price)
        if high_price >= stop_price:
            exit_price = stop_price
            exit_reason = "stop"
            exit_timestamp_ms = timestamp_ms
            break
        if low_price <= target_price:
            exit_price = target_price
            exit_reason = "target"
            exit_timestamp_ms = timestamp_ms
            break
        exit_price = close_price
        exit_reason = "time_exit"
        exit_timestamp_ms = timestamp_ms

    if not entered or entry_timestamp_ms is None or exit_price is None or exit_timestamp_ms is None:
        return None

    exit_return_pct = (entry_price - exit_price) / entry_price - (commission_rate * 2.0)
    return {
        "trade_triggered": True,
        "entry_price": entry_price,
        "entry_timestamp_ms": entry_timestamp_ms,
        "entry_delay_minutes": max(0, int((entry_timestamp_ms - start_ts) // 60_000)),
        "entry_from_high_frac": (trigger_high - entry_price) / trigger_range if trigger_range > 0 else None,
        "initial_stop_price": stop_price,
        "initial_risk_pct": risk_pct,
        "target_price": target_price,
        "target_rr": geometry.target_rr,
        "exit_price": exit_price,
        "exit_timestamp_ms": exit_timestamp_ms,
        "exit_return_pct": exit_return_pct,
        "exit_r_multiple": exit_return_pct / risk_pct if risk_pct > 0 else None,
        "exit_reason": exit_reason,
        "bars_held_after_entry": bars_held,
        "minutes_held_after_entry": max(0, int((exit_timestamp_ms - entry_timestamp_ms) // 60_000)),
        "max_favorable_excursion_pct": max_favorable_excursion,
        "max_adverse_excursion_pct": max_adverse_excursion,
    }


def _simulate_third_open_geometry(
    *,
    timestamps_5m: list[int],
    open_values_5m: list[float],
    timestamps_1m: pd.Index,
    high_values_1m: list[float],
    low_values_1m: list[float],
    close_values_1m: list[float],
    event_position_5m: int,
    trigger_high: float,
    trigger_low: float,
    trigger_range: float,
    geometry: ShortGeometry,
    commission_rate: float,
) -> dict[str, object] | None:
    entry_position = event_position_5m + 2
    if entry_position >= len(timestamps_5m):
        return None
    entry_timestamp_ms = int(timestamps_5m[entry_position])
    entry_price = float(open_values_5m[entry_position])
    midpoint_price = trigger_high - 0.5 * trigger_range
    if entry_price <= midpoint_price:
        return None
    entry_from_high_frac = (trigger_high - entry_price) / trigger_range if trigger_range > 0 else None
    if geometry.max_open_entry_from_high_frac is not None and (
        entry_from_high_frac is None or entry_from_high_frac > geometry.max_open_entry_from_high_frac
    ):
        return None
    stop_price = trigger_high + geometry.stop_buffer_frac * trigger_range
    risk_pct = (stop_price - entry_price) / entry_price if entry_price > 0 else None
    if risk_pct is None or risk_pct <= 0.0:
        return None
    target_price = entry_price - geometry.target_rr * (stop_price - entry_price)
    reward_pct = (entry_price - target_price) / entry_price if entry_price > 0 else None
    if reward_pct is None or reward_pct / risk_pct < _SHORT_MIN_RR:
        return None

    start_idx = timestamps_1m.searchsorted(entry_timestamp_ms, side="left")
    end_idx = timestamps_1m.searchsorted(entry_timestamp_ms + _SHORT_MAX_HOLD_MINUTES * 60_000, side="left")
    if end_idx <= start_idx:
        return None

    exit_price: float | None = None
    exit_reason = "time_exit"
    exit_timestamp_ms: int | None = None
    max_favorable_excursion = 0.0
    max_adverse_excursion = 0.0
    bars_held = 0
    for position in range(start_idx, end_idx):
        high_price = float(high_values_1m[position])
        low_price = float(low_values_1m[position])
        close_price = float(close_values_1m[position])
        bars_held += 1
        max_favorable_excursion = max(max_favorable_excursion, (entry_price - low_price) / entry_price)
        max_adverse_excursion = max(max_adverse_excursion, (high_price - entry_price) / entry_price)
        if high_price >= stop_price:
            exit_price = stop_price
            exit_reason = "stop"
            exit_timestamp_ms = int(timestamps_1m[position])
            break
        if low_price <= target_price:
            exit_price = target_price
            exit_reason = "target"
            exit_timestamp_ms = int(timestamps_1m[position])
            break
        exit_price = close_price
        exit_timestamp_ms = int(timestamps_1m[position])

    if exit_price is None or exit_timestamp_ms is None:
        return None
    exit_return_pct = (entry_price - exit_price) / entry_price - (commission_rate * 2.0)
    return {
        "trade_triggered": True,
        "entry_price": entry_price,
        "entry_timestamp_ms": entry_timestamp_ms,
        "entry_delay_minutes": 10,
        "entry_from_high_frac": entry_from_high_frac,
        "initial_stop_price": stop_price,
        "initial_risk_pct": risk_pct,
        "target_price": target_price,
        "target_rr": geometry.target_rr,
        "exit_price": exit_price,
        "exit_timestamp_ms": exit_timestamp_ms,
        "exit_return_pct": exit_return_pct,
        "exit_r_multiple": exit_return_pct / risk_pct if risk_pct > 0 else None,
        "exit_reason": exit_reason,
        "bars_held_after_entry": bars_held,
        "minutes_held_after_entry": max(0, int((exit_timestamp_ms - entry_timestamp_ms) // 60_000)),
        "max_favorable_excursion_pct": max_favorable_excursion,
        "max_adverse_excursion_pct": max_adverse_excursion,
    }


def _build_geometry_events(
    *,
    preparer: DataPreparer,
    selected_events: pd.DataFrame,
    geometries: tuple[ShortGeometry, ...],
    commission_rate: float,
    logger: logging.Logger,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    symbols = list(selected_events["symbol"].dropna().astype(str).unique())
    started_at = time.time()
    for index, symbol in enumerate(symbols, start=1):
        candles_5m = preparer.load_symbol_data(symbol, Timeframe.M5)
        candles_1m = preparer.load_symbol_data(symbol, Timeframe.M1)
        if candles_5m.empty or candles_1m.empty:
            continue
        timestamps_5m = pd.to_numeric(candles_5m["timestamp"], errors="coerce").fillna(0).astype("int64").tolist()
        open_values_5m = pd.to_numeric(candles_5m["open"], errors="coerce").fillna(0.0).astype("float64").tolist()
        high_values_5m = pd.to_numeric(candles_5m["high"], errors="coerce").fillna(0.0).astype("float64").tolist()
        low_values_5m = pd.to_numeric(candles_5m["low"], errors="coerce").fillna(0.0).astype("float64").tolist()
        close_values_5m = pd.to_numeric(candles_5m["close"], errors="coerce").fillna(0.0).astype("float64").tolist()
        timestamps_1m = pd.Index(pd.to_numeric(candles_1m["timestamp"], errors="coerce").fillna(0).astype("int64").tolist())
        open_values_1m = pd.to_numeric(candles_1m["open"], errors="coerce").fillna(0.0).astype("float64").tolist()
        high_values_1m = pd.to_numeric(candles_1m["high"], errors="coerce").fillna(0.0).astype("float64").tolist()
        low_values_1m = pd.to_numeric(candles_1m["low"], errors="coerce").fillna(0.0).astype("float64").tolist()
        close_values_1m = pd.to_numeric(candles_1m["close"], errors="coerce").fillna(0.0).astype("float64").tolist()
        symbol_events = selected_events[selected_events["symbol"].astype(str) == symbol].copy()
        for _, event in symbol_events.iterrows():
            timestamp_ms = int(event["timestamp_ms"])
            position_5m = pd.Index(timestamps_5m).searchsorted(timestamp_ms, side="left")
            if position_5m >= len(timestamps_5m) or int(timestamps_5m[position_5m]) != timestamp_ms:
                continue
            if position_5m + 1 >= len(timestamps_5m):
                continue
            trigger_open = float(event["trigger_open"])
            trigger_high = float(event["trigger_high"])
            trigger_low = float(event["trigger_low"])
            trigger_close = float(event["trigger_close"])
            trigger_range = max(1e-12, trigger_high - trigger_low)
            next_open = float(open_values_5m[position_5m + 1])
            next_high = float(high_values_5m[position_5m + 1])
            next_low = float(low_values_5m[position_5m + 1])
            next_close = float(close_values_5m[position_5m + 1])
            next_range = max(1e-12, next_high - next_low)
            common = {
                "symbol": symbol,
                "timestamp_ms": timestamp_ms,
                "timestamp_utc": event["timestamp_utc"],
                "month_utc": event["month_utc"],
                "hour_utc": int(event["hour_utc"]),
                "trigger_open": trigger_open,
                "trigger_high": trigger_high,
                "trigger_low": trigger_low,
                "trigger_close": trigger_close,
                "trigger_return_pct": float(event["trigger_return_pct"]),
                "trigger_range_pct": float(event["trigger_range_pct"]),
                "range_atr": float(event["range_atr"]),
                "volume_mult": float(event["volume_mult"]),
                "close_to_high_frac": float(event["close_to_high_frac"]),
                "upper_wick_frac": (trigger_high - trigger_close) / trigger_range,
                "lower_wick_frac": (trigger_open - trigger_low) / trigger_range,
                "body_frac": (trigger_close - trigger_open) / trigger_range,
                "next_open": next_open,
                "next_high": next_high,
                "next_low": next_low,
                "next_close": next_close,
                "next_return_pct": (next_close / next_open - 1.0) if next_open > 0 else None,
                "next_pullback_frac": (trigger_high - next_low) / trigger_range,
                "next_close_from_high_frac": (trigger_high - next_close) / trigger_range,
                "next_close_pos_in_bar": (next_close - next_low) / next_range,
                "next_extension_above_trigger_high_pct": (next_high / trigger_high - 1.0) if trigger_high > 0 else None,
            }
            for geometry in geometries:
                if geometry.entry_style == "limit_zone":
                    trade = _simulate_limit_zone_geometry(
                        timestamps_1m=timestamps_1m,
                        open_values_1m=open_values_1m,
                        high_values_1m=high_values_1m,
                        low_values_1m=low_values_1m,
                        close_values_1m=close_values_1m,
                        event_timestamp_ms=timestamp_ms,
                        trigger_high=trigger_high,
                        trigger_low=trigger_low,
                        trigger_open=trigger_open,
                        trigger_range=trigger_range,
                        geometry=geometry,
                        commission_rate=commission_rate,
                    )
                else:
                    trade = _simulate_third_open_geometry(
                        timestamps_5m=timestamps_5m,
                        open_values_5m=open_values_5m,
                        timestamps_1m=timestamps_1m,
                        high_values_1m=high_values_1m,
                        low_values_1m=low_values_1m,
                        close_values_1m=close_values_1m,
                        event_position_5m=position_5m,
                        trigger_high=trigger_high,
                        trigger_low=trigger_low,
                        trigger_range=trigger_range,
                        geometry=geometry,
                        commission_rate=commission_rate,
                    )
                if trade is None:
                    continue
                rows.append({**common, "geometry_id": geometry.geometry_id, "entry_style": geometry.entry_style, **trade})

        if index % _SHORT_PROGRESS_LOG_EVERY_SYMBOLS == 0 or index == len(symbols):
            progress_pct, elapsed, eta_seconds = _progress_snapshot(
                completed=index,
                total=len(symbols),
                started_at=started_at,
            )
            logger.info(
                "hourly-pump-short-edge: stage=geometry-events progress=%.1f%% symbols=%s/%s rows=%s elapsed=%s eta=%s",
                progress_pct,
                index,
                len(symbols),
                len(rows),
                _format_duration(elapsed),
                _format_duration(eta_seconds),
            )
    return pd.DataFrame(rows)


def _model_matches_row(model: ShortExecutionModel, row: pd.Series) -> bool:
    if str(row.get("geometry_id")) != model.geometry.geometry_id:
        return False
    if float(row.get("trigger_return_pct", 0.0) or 0.0) < model.signal_profile.min_trigger_return_pct:
        return False
    if float(row.get("range_atr", 0.0) or 0.0) < model.signal_profile.min_range_atr:
        return False
    if float(row.get("volume_mult", 0.0) or 0.0) < model.signal_profile.min_volume_mult:
        return False
    if float(row.get("close_to_high_frac", 1.0) or 1.0) > model.signal_profile.max_close_to_high_frac:
        return False
    if float(row.get("upper_wick_frac", 0.0) or 0.0) < model.trigger_pressure_profile.min_upper_wick_frac:
        return False
    if float(row.get("body_frac", 1.0) or 1.0) > model.trigger_pressure_profile.max_body_frac:
        return False
    next_pullback = float(row.get("next_pullback_frac", 0.0) or 0.0)
    if next_pullback < model.next_pressure_profile.min_next_pullback_frac:
        return False
    if next_pullback > model.next_pressure_profile.max_next_pullback_frac:
        return False
    if float(row.get("next_close_from_high_frac", 0.0) or 0.0) < model.next_pressure_profile.min_next_close_from_high_frac:
        return False
    if float(row.get("next_close_pos_in_bar", 1.0) or 1.0) > model.next_pressure_profile.max_next_close_pos_in_bar:
        return False
    if float(row.get("next_return_pct", 1.0) or 1.0) > model.next_pressure_profile.max_next_return_pct:
        return False
    return True


def _candidate_meets_goal(row: pd.Series | dict[str, object]) -> bool:
    return (
        float(row.get("trades_per_year", 0.0) or 0.0) >= _SHORT_MIN_TRADES_PER_YEAR
        and float(row.get("mean_return_pct", 0.0) or 0.0) >= _SHORT_MIN_MEAN_RETURN_PCT
        and float(row.get("win_rate", 0.0) or 0.0) >= _SHORT_MIN_WIN_RATE
        and float(row.get("annualized_unit_pnl_pct", 0.0) or 0.0) >= _SHORT_MIN_ANNUALIZED_UNIT_PNL_PCT
        and float(row.get("max_drawdown_pct", math.inf) or math.inf) <= _SHORT_MAX_DRAWDOWN_PCT
        and int(row.get("positive_months_count", 0) or 0) >= _SHORT_MIN_POSITIVE_MONTHS
        and int(row.get("stable_positive_months_count", 0) or 0) >= _SHORT_MIN_STABLE_POSITIVE_MONTHS
    )


def _candidate_score(row: pd.Series | dict[str, object]) -> float:
    mean_return_pct = float(row.get("mean_return_pct", 0.0) or 0.0)
    win_rate = float(row.get("win_rate", 0.0) or 0.0)
    trades_per_year = float(row.get("trades_per_year", 0.0) or 0.0)
    annualized = float(row.get("annualized_unit_pnl_pct", 0.0) or 0.0)
    max_drawdown = float(row.get("max_drawdown_pct", 1.0) or 1.0)
    positive_months = int(row.get("positive_months_count", 0) or 0)
    stable_positive_months = int(row.get("stable_positive_months_count", 0) or 0)
    top1_share = float(_safe_float(row.get("top1_trade_pnl_share")) or 0.0)
    score = 0.0
    score += min(mean_return_pct, 0.06) * 1800.0
    score += min(win_rate, 0.8) * 240.0
    score += min(trades_per_year, 120.0) * 0.35
    score += min(annualized, 3.0) * 80.0
    score += min(positive_months, 12) * 8.0
    score += min(stable_positive_months, 12) * 18.0
    score -= max(max_drawdown - 0.20, 0.0) * 260.0
    score -= top1_share * 24.0
    if trades_per_year < _SHORT_MIN_TRADES_PER_YEAR:
        score -= (_SHORT_MIN_TRADES_PER_YEAR - trades_per_year) * 2.0
    if mean_return_pct < _SHORT_MIN_MEAN_RETURN_PCT:
        score -= (_SHORT_MIN_MEAN_RETURN_PCT - mean_return_pct) * 1700.0
    if win_rate < _SHORT_MIN_WIN_RATE:
        score -= (_SHORT_MIN_WIN_RATE - win_rate) * 250.0
    if positive_months < _SHORT_MIN_POSITIVE_MONTHS:
        score -= (_SHORT_MIN_POSITIVE_MONTHS - positive_months) * 12.0
    if stable_positive_months < _SHORT_MIN_STABLE_POSITIVE_MONTHS:
        score -= (_SHORT_MIN_STABLE_POSITIVE_MONTHS - stable_positive_months) * 18.0
    if _candidate_meets_goal(row):
        score += 250.0
    return score


def _summarize_atomic_models(
    *,
    geometry_events: pd.DataFrame,
    models: list[ShortExecutionModel],
    calendar_months: list[str],
    logger: logging.Logger,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    rows: list[dict[str, object]] = []
    model_events: dict[str, pd.DataFrame] = {}
    started_at = time.time()
    for index, model in enumerate(models, start=1):
        scoped = geometry_events[geometry_events.apply(lambda row: _model_matches_row(model, row), axis=1)].copy()
        if scoped.empty:
            continue
        scoped = scoped.sort_values(["entry_timestamp_ms", "symbol", "timestamp_ms"]).reset_index(drop=True)
        summary = _summarize_events(scoped, calendar_months=calendar_months)
        if summary is None:
            continue
        monthly = _build_monthly_returns_frame(scoped, calendar_months=calendar_months)
        month_return = _numeric_column(monthly, "month_return_pct")
        month_trades = _numeric_column(monthly, "trades_count")
        month_win_rate = _numeric_column(monthly, "win_rate")
        stable_positive_months = int(
            (
                (month_return > 0.0)
                & (month_trades >= 2)
                & (month_win_rate >= 0.50)
            ).sum()
        )
        total_unit_pnl = float(summary.get("unit_pnl_sum_pct", 0.0) or 0.0)
        winners = pd.to_numeric(scoped["exit_return_pct"], errors="coerce")
        winners = winners[winners > 0.0].sort_values(ascending=False)
        top1_share = float(winners.head(1).sum() / total_unit_pnl) if total_unit_pnl > 0.0 and not winners.empty else None
        row = {
            "model_id": model.model_id,
            "model_label": model.label,
            "signal_profile_id": model.signal_profile.profile_id,
            "trigger_pressure_id": model.trigger_pressure_profile.profile_id,
            "next_pressure_id": model.next_pressure_profile.profile_id,
            "geometry_id": model.geometry.geometry_id,
            "entry_style": model.geometry.entry_style,
            "rule_text": model.rule_text,
            "stable_positive_months_count": stable_positive_months,
            "top1_trade_pnl_share": top1_share,
            **summary,
        }
        row["meets_goal"] = _candidate_meets_goal(row)
        row["selection_score"] = _candidate_score(row)
        rows.append(row)
        model_events[model.model_id] = scoped
        if index % 50 == 0 or index == len(models):
            progress_pct, elapsed, eta_seconds = _progress_snapshot(
                completed=index,
                total=len(models),
                started_at=started_at,
            )
            logger.info(
                "hourly-pump-short-edge: stage=model-summary progress=%.1f%% models=%s/%s kept=%s elapsed=%s eta=%s",
                progress_pct,
                index,
                len(models),
                len(rows),
                _format_duration(elapsed),
                _format_duration(eta_seconds),
            )
    summary_frame = pd.DataFrame(rows)
    if summary_frame.empty:
        return summary_frame, model_events
    summary_frame = summary_frame.sort_values(
        ["selection_score", "mean_return_pct", "win_rate", "trades_per_year"],
        ascending=[False, False, False, False],
    ).reset_index(drop=True)
    summary_frame["model_rank"] = range(1, len(summary_frame) + 1)
    return summary_frame, model_events


def _build_behavior_summary(summary_frame: pd.DataFrame, column: str) -> pd.DataFrame:
    if summary_frame.empty or column not in summary_frame.columns:
        return pd.DataFrame()
    grouped = summary_frame.groupby(column, sort=True).agg(
        models_count=("model_id", "count"),
        mean_of_mean_return_pct=("mean_return_pct", "mean"),
        mean_of_win_rate=("win_rate", "mean"),
        mean_of_trades_per_year=("trades_per_year", "mean"),
        mean_of_annualized_unit_pnl_pct=("annualized_unit_pnl_pct", "mean"),
        mean_of_max_drawdown_pct=("max_drawdown_pct", "mean"),
        goal_models_count=("meets_goal", "sum"),
    ).reset_index()
    return grouped.sort_values(
        ["mean_of_mean_return_pct", "mean_of_win_rate", "mean_of_trades_per_year"],
        ascending=[False, False, False],
    ).reset_index(drop=True)


def _build_feature_bucket_summary(canonical_events: pd.DataFrame) -> pd.DataFrame:
    if canonical_events.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for feature_name in (
        "trigger_return_pct",
        "upper_wick_frac",
        "body_frac",
        "range_atr",
        "volume_mult",
        "next_pullback_frac",
        "next_close_from_high_frac",
        "next_close_pos_in_bar",
    ):
        series = pd.to_numeric(canonical_events[feature_name], errors="coerce")
        if series.dropna().nunique() < 4:
            continue
        scoped = canonical_events.copy()
        scoped["feature_bucket"] = pd.qcut(series, q=4, duplicates="drop").astype(str)
        grouped = scoped.groupby("feature_bucket", sort=True).agg(
            trades_count=("exit_return_pct", "count"),
            mean_return_pct=("exit_return_pct", "mean"),
            win_rate=("exit_return_pct", lambda values: float((pd.to_numeric(values, errors="coerce") > 0.0).mean())),
            mean_mfe_pct=("max_favorable_excursion_pct", "mean"),
        ).reset_index()
        grouped["feature_name"] = feature_name
        rows.extend(grouped.to_dict("records"))
    return pd.DataFrame(rows)


def _build_holdout_summary(events: pd.DataFrame, calendar_months: list[str]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for split_id, train_months_count in (("train8_test4", 8), ("train9_test3", 9)):
        if len(calendar_months) <= train_months_count:
            continue
        test_months = calendar_months[train_months_count:]
        scoped = events[events["month_utc"].astype(str).isin(test_months)].copy()
        summary = _summarize_events(scoped, calendar_months=test_months)
        if summary is not None:
            rows.append({"split_id": split_id, **summary})
    return pd.DataFrame(rows)


def _build_monthly_detail(events: pd.DataFrame, calendar_months: list[str]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for month in calendar_months:
        scoped = events[events["month_utc"].astype(str) == str(month)].copy()
        returns = pd.to_numeric(scoped.get("exit_return_pct"), errors="coerce").fillna(0.0)
        trades_count = int(len(scoped))
        total_return_pct = float(returns.sum()) if trades_count > 0 else 0.0
        rows.append(
            {
                "month_utc": str(month),
                "trades_count": trades_count,
                "month_return_pct": total_return_pct,
                "win_rate": float((returns > 0.0).mean()) if trades_count > 0 else 0.0,
                "mean_return_pct": float(returns.mean()) if trades_count > 0 else 0.0,
                "month_positive": bool(total_return_pct > 0.0),
            }
        )
    return pd.DataFrame(rows)


def _build_report(
    *,
    report_path: Path,
    context: dict[str, object],
    best_summary: pd.DataFrame,
    best_monthly: pd.DataFrame,
    best_holdout: pd.DataFrame,
    behavior_by_signal: pd.DataFrame,
    behavior_by_trigger_pressure: pd.DataFrame,
    behavior_by_next_pressure: pd.DataFrame,
    behavior_by_geometry: pd.DataFrame,
    feature_buckets: pd.DataFrame,
    top_candidates: pd.DataFrame,
) -> None:
    best_summary_for_report = best_summary.copy()
    top_candidates_for_report = top_candidates.copy()
    if "model_label" in best_summary_for_report.columns:
        best_summary_for_report["model_label"] = best_summary_for_report["model_label"].astype(str).str.replace("|", " / ", regex=False)
    if "model_label" in top_candidates_for_report.columns:
        top_candidates_for_report["model_label"] = top_candidates_for_report["model_label"].astype(str).str.replace("|", " / ", regex=False)
    lines = [
        "# Шортовый анализ XX:00 аномалий",
        "",
        "Этот прогон ищет единый шортовый подход после аномальной `5m` свечи `XX:00`.",
        "Все модели строятся без развилок по часу, а входы позже `50%` отката от диапазона аномалии запрещены конструкцией сетапа.",
        "",
        "## Лучший найденный вариант",
        "",
        _frame_to_markdown(
            best_summary_for_report,
            columns=[
                "model_rank",
                "model_label",
                "trades_count",
                "trades_per_year",
                "mean_return_pct",
                "median_return_pct",
                "win_rate",
                "annualized_unit_pnl_pct",
                "max_drawdown_pct",
                "positive_months_count",
                "stable_positive_months_count",
                "mean_positive_return_pct",
                "mean_negative_return_pct",
                "top1_trade_pnl_share",
                "meets_goal",
            ],
            limit=1,
        ),
        "",
        "## Лучшие кандидаты",
        "",
        _frame_to_markdown(
            top_candidates_for_report,
            columns=[
                "model_rank",
                "model_label",
                "trades_per_year",
                "mean_return_pct",
                "win_rate",
                "annualized_unit_pnl_pct",
                "max_drawdown_pct",
                "positive_months_count",
                "stable_positive_months_count",
                "top1_trade_pnl_share",
                "meets_goal",
            ],
            limit=12,
        ),
        "",
        "## Месяцы лучшего варианта",
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
            columns=["split_id", "trades_count", "trades_per_year", "mean_return_pct", "win_rate", "annualized_unit_pnl_pct", "max_drawdown_pct", "positive_months_count"],
        ),
        "",
        "## Что говорят признаки давления",
        "",
        _frame_to_markdown(
            feature_buckets,
            columns=["feature_name", "feature_bucket", "trades_count", "mean_return_pct", "win_rate", "mean_mfe_pct"],
            limit=32,
        ),
        "",
        "## Поведение по семействам правил",
        "",
        "### Сигнал",
        "",
        _frame_to_markdown(
            behavior_by_signal,
            columns=["signal_profile_id", "models_count", "mean_of_mean_return_pct", "mean_of_win_rate", "mean_of_trades_per_year", "mean_of_annualized_unit_pnl_pct", "goal_models_count"],
        ),
        "",
        "### Давление в аномальной свече",
        "",
        _frame_to_markdown(
            behavior_by_trigger_pressure,
            columns=["trigger_pressure_id", "models_count", "mean_of_mean_return_pct", "mean_of_win_rate", "mean_of_trades_per_year", "mean_of_annualized_unit_pnl_pct", "goal_models_count"],
        ),
        "",
        "### Давление следующей свечи",
        "",
        _frame_to_markdown(
            behavior_by_next_pressure,
            columns=["next_pressure_id", "models_count", "mean_of_mean_return_pct", "mean_of_win_rate", "mean_of_trades_per_year", "mean_of_annualized_unit_pnl_pct", "goal_models_count"],
        ),
        "",
        "### Геометрия входа",
        "",
        _frame_to_markdown(
            behavior_by_geometry,
            columns=["geometry_id", "models_count", "mean_of_mean_return_pct", "mean_of_win_rate", "mean_of_trades_per_year", "mean_of_annualized_unit_pnl_pct", "goal_models_count"],
        ),
        "",
        "## Контекст",
        "",
        "```json",
        json.dumps(context, ensure_ascii=False, indent=2),
        "```",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")


def build_hourly_asia_pump_short_edge_artifacts(
    *,
    base_events_path: Path,
    output_dir: Path,
    cache_dir: Path | None = None,
    commission_rate: float = 0.0,
    preparer: DataPreparer | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, object]:
    active_logger = logger or module_logger
    output_dir.mkdir(parents=True, exist_ok=True)
    selected_events = _prepare_anomaly_events(base_events_path)
    if selected_events.empty:
        raise ValueError(f"Не найдены базовые аномалии в {base_events_path}")
    active_preparer = preparer or DataPreparer(Path(cache_dir))
    calendar_months = _calendar_months_from_frames(selected_events)
    geometries = _build_geometries()
    models = _build_execution_models()
    active_logger.info(
        "hourly-pump-short-edge: stage=init anomalies=%s geometries=%s models=%s output_dir=%s",
        len(selected_events),
        len(geometries),
        len(models),
        output_dir,
    )
    geometry_events = _build_geometry_events(
        preparer=active_preparer,
        selected_events=selected_events,
        geometries=geometries,
        commission_rate=commission_rate,
        logger=active_logger,
    )
    if geometry_events.empty:
        raise ValueError("Не удалось построить ни одной шортовой сделки")
    geometry_events["entry_timestamp_utc"] = pd.to_datetime(
        pd.to_numeric(geometry_events["entry_timestamp_ms"], errors="coerce"),
        unit="ms",
        utc=True,
        errors="coerce",
    ).dt.strftime("%Y-%m-%d %H:%M:%S")
    geometry_events["exit_timestamp_utc"] = pd.to_datetime(
        pd.to_numeric(geometry_events["exit_timestamp_ms"], errors="coerce"),
        unit="ms",
        utc=True,
        errors="coerce",
    ).dt.strftime("%Y-%m-%d %H:%M:%S")

    atomic_summary, model_events = _summarize_atomic_models(
        geometry_events=geometry_events,
        models=models,
        calendar_months=calendar_months,
        logger=active_logger,
    )
    if atomic_summary.empty:
        raise ValueError("Шортовый поиск не дал ни одного кандидата")
    best_summary = atomic_summary.head(1).copy()
    best_model_id = str(best_summary.iloc[0]["model_id"])
    best_events = model_events[best_model_id].copy().reset_index(drop=True)
    best_monthly = _build_monthly_detail(best_events, calendar_months=calendar_months)
    best_holdout = _build_holdout_summary(best_events, calendar_months=calendar_months)
    behavior_by_signal = _build_behavior_summary(atomic_summary, "signal_profile_id")
    behavior_by_trigger_pressure = _build_behavior_summary(atomic_summary, "trigger_pressure_id")
    behavior_by_next_pressure = _build_behavior_summary(atomic_summary, "next_pressure_id")
    behavior_by_geometry = _build_behavior_summary(atomic_summary, "geometry_id")
    feature_buckets = _build_feature_bucket_summary(
        geometry_events[geometry_events["geometry_id"] == "limit_top10_s10_rr20"].copy()
    )

    context = {
        "search_scope": {
            "same_rules_for_all_xx00": True,
            "timeframe": Timeframe.M5.value,
            "anomalies_count": int(len(selected_events)),
            "geometry_trade_rows": int(len(geometry_events)),
            "models_count": int(len(models)),
            "commission_rate": float(commission_rate),
            "round_trip_taker_fee_pct": float(commission_rate * 2.0),
            "min_rr": float(_SHORT_MIN_RR),
            "max_hold_minutes": int(_SHORT_MAX_HOLD_MINUTES),
        },
        "criteria": {
            "min_trades_per_year": _SHORT_MIN_TRADES_PER_YEAR,
            "min_mean_return_pct": _SHORT_MIN_MEAN_RETURN_PCT,
            "min_win_rate": _SHORT_MIN_WIN_RATE,
            "min_annualized_unit_pnl_pct": _SHORT_MIN_ANNUALIZED_UNIT_PNL_PCT,
            "max_drawdown_pct": _SHORT_MAX_DRAWDOWN_PCT,
            "min_positive_months": _SHORT_MIN_POSITIVE_MONTHS,
            "min_stable_positive_months": _SHORT_MIN_STABLE_POSITIVE_MONTHS,
        },
        "best_model_id": best_model_id,
        "best_rule_text": best_summary.iloc[0]["rule_text"],
    }

    artifacts = {
        "geometry_events": output_dir / "short_edge_geometry_events.csv",
        "atomic_summary": output_dir / "short_edge_atomic_summary.csv",
        "best_summary": output_dir / "short_edge_best_summary.csv",
        "best_events": output_dir / "short_edge_best_events.csv",
        "best_monthly": output_dir / "short_edge_best_monthly.csv",
        "best_holdout": output_dir / "short_edge_best_holdout.csv",
        "behavior_by_signal": output_dir / "short_edge_behavior_by_signal.csv",
        "behavior_by_trigger_pressure": output_dir / "short_edge_behavior_by_trigger_pressure.csv",
        "behavior_by_next_pressure": output_dir / "short_edge_behavior_by_next_pressure.csv",
        "behavior_by_geometry": output_dir / "short_edge_behavior_by_geometry.csv",
        "feature_buckets": output_dir / "short_edge_feature_buckets.csv",
        "context": output_dir / "short_edge_context.json",
        "report": output_dir / "short_edge_report.md",
    }
    geometry_events.to_csv(artifacts["geometry_events"], index=False)
    atomic_summary.to_csv(artifacts["atomic_summary"], index=False)
    best_summary.to_csv(artifacts["best_summary"], index=False)
    best_events.to_csv(artifacts["best_events"], index=False)
    best_monthly.to_csv(artifacts["best_monthly"], index=False)
    best_holdout.to_csv(artifacts["best_holdout"], index=False)
    behavior_by_signal.to_csv(artifacts["behavior_by_signal"], index=False)
    behavior_by_trigger_pressure.to_csv(artifacts["behavior_by_trigger_pressure"], index=False)
    behavior_by_next_pressure.to_csv(artifacts["behavior_by_next_pressure"], index=False)
    behavior_by_geometry.to_csv(artifacts["behavior_by_geometry"], index=False)
    feature_buckets.to_csv(artifacts["feature_buckets"], index=False)
    artifacts["context"].write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    _build_report(
        report_path=artifacts["report"],
        context=context,
        best_summary=best_summary,
        best_monthly=best_monthly,
        best_holdout=best_holdout,
        behavior_by_signal=behavior_by_signal,
        behavior_by_trigger_pressure=behavior_by_trigger_pressure,
        behavior_by_next_pressure=behavior_by_next_pressure,
        behavior_by_geometry=behavior_by_geometry,
        feature_buckets=feature_buckets,
        top_candidates=atomic_summary.head(12),
    )
    active_logger.info(
        "hourly-pump-short-edge: stage=done geometry_rows=%s models_kept=%s best=%s goal=%s",
        len(geometry_events),
        len(atomic_summary),
        best_summary.iloc[0]["model_label"],
        bool(best_summary.iloc[0]["meets_goal"]),
    )
    return {**artifacts, "goal_passed": bool(best_summary.iloc[0]["meets_goal"])}
