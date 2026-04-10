from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from strategy.hourly_asia_pump.static_combo import (
    _build_monthly_returns_frame,
    _calendar_months_from_frames,
    _frame_to_markdown,
    _summarize_events,
)
from strategy.hourly_asia_pump.tradeable_second_wave_models import (
    FIVE_MINUTES_MS,
    ONE_MINUTE_MS,
    _close_pos,
    _load_feature_db,
    _load_m1,
    _net_long_return,
    _prepare_scope,
    _pressure_score,
)
from strategy.hourly_asia_pump.unified_edge import _simulate_equity_risk_metrics

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "second_wave_execution_search"


@dataclass(frozen=True, slots=True)
class SignalSpec:
    signal_id: str
    regime_id: str
    max_wait_minutes: int
    min_break_body_ratio: float
    min_break_volume_ratio: float
    min_close_pos: float
    max_pullback_frac: float
    max_seller_pressure: float
    min_buyer_break_score: float
    max_entry_extension_frac: float


@dataclass(frozen=True, slots=True)
class EntrySpec:
    entry_id: str
    style: str
    valid_minutes: int
    pullback_frac: float | None = None


@dataclass(frozen=True, slots=True)
class StopSpec:
    stop_id: str
    style: str


@dataclass(frozen=True, slots=True)
class ManagementSpec:
    management_id: str
    final_target_r: float | None
    partial_take_r: float | None
    partial_fraction: float
    move_stop_to_be_after_partial: bool
    trail_after_partial: bool
    be_after_r: float | None
    trail_after_r: float | None
    fast_fail_minutes: int
    fast_fail_r: float
    max_hold_minutes: int


@dataclass(frozen=True, slots=True)
class ExecutionModel:
    model_id: str
    regime_id: str
    signal_spec: SignalSpec
    entry_spec: EntrySpec
    stop_spec: StopSpec
    management_spec: ManagementSpec


@dataclass(frozen=True, slots=True)
class SignalInfo:
    signal_idx: int
    signal_open: float
    signal_high: float
    signal_low: float
    signal_close: float
    signal_range: float
    signal_body_ratio: float
    signal_volume_ratio: float
    signal_close_pos: float
    prebreak_low: float
    pullback_frac: float
    seller_pressure_score: float
    buyer_break_score: float
    entry_extension_frac: float
    start_delay_min: int


@dataclass(frozen=True, slots=True)
class EntryInfo:
    entry_idx: int
    entry_price: float
    entry_detail: str


def _regime_scope(frame: pd.DataFrame) -> pd.DataFrame:
    scoped = _prepare_scope(frame).copy()
    scoped["regime_id"] = scoped["category_id"].astype(str).map(
        {
            "warm_continuation": "warm_second_wave",
            "overheated_retest": "overheated_second_wave",
        }
    )
    return scoped.dropna(subset=["regime_id"]).reset_index(drop=True)


def _signal_specs() -> tuple[SignalSpec, ...]:
    return (
        SignalSpec("w_bal", "warm_second_wave", 5, 1.00, 0.90, 0.65, 0.25, 0.10, 0.18, 0.18),
        SignalSpec("w_strong", "warm_second_wave", 5, 1.30, 1.20, 0.75, 0.25, 0.05, 0.22, 0.18),
        SignalSpec("w_tight", "warm_second_wave", 5, 1.30, 0.90, 0.70, 0.20, 0.05, 0.20, 0.15),
        SignalSpec("w_loose", "warm_second_wave", 5, 0.80, 0.90, 0.60, 0.25, 0.20, 0.14, 0.18),
        SignalSpec("o_bal", "overheated_second_wave", 8, 1.00, 0.90, 0.60, 0.30, 0.15, 0.18, 0.18),
        SignalSpec("o_strong", "overheated_second_wave", 8, 1.30, 1.10, 0.65, 0.30, 0.10, 0.22, 0.15),
        SignalSpec("o_loose", "overheated_second_wave", 10, 0.80, 0.80, 0.55, 0.35, 0.20, 0.14, 0.20),
    )


def _entry_specs() -> tuple[EntrySpec, ...]:
    return (
        EntrySpec("close_signal", "signal_close", 0),
        EntrySpec("next_open", "next_open", 1),
        EntrySpec("break_sig_high", "break_signal_high", 2),
        EntrySpec("pull25", "pullback_limit", 2, 0.25),
        EntrySpec("pull40", "pullback_limit", 3, 0.40),
    )


def _stop_specs() -> tuple[StopSpec, ...]:
    return (
        StopSpec("sig_low", "signal_low"),
        StopSpec("sig_body", "signal_body_low"),
        StopSpec("prebreak", "prebreak_low"),
    )


def _management_specs() -> tuple[ManagementSpec, ...]:
    return (
        ManagementSpec("fx15", 1.5, None, 0.0, False, False, None, None, 5, 0.15, 60),
        ManagementSpec("fx20", 2.0, None, 0.0, False, False, None, None, 8, 0.25, 90),
        ManagementSpec("p10_be_f20", 2.0, 1.0, 0.50, True, False, None, None, 8, 0.25, 90),
        ManagementSpec("p10_be_trail", None, 1.0, 0.50, True, True, None, None, 8, 0.25, 120),
        ManagementSpec("p15_be_trail", None, 1.5, 0.35, True, True, None, None, 10, 0.35, 150),
        ManagementSpec("be10_trail", None, None, 0.0, False, False, 1.0, 1.0, 8, 0.25, 120),
    )


def _build_models() -> tuple[ExecutionModel, ...]:
    models: list[ExecutionModel] = []
    for signal_spec in _signal_specs():
        for entry_spec in _entry_specs():
            for stop_spec in _stop_specs():
                for management_spec in _management_specs():
                    model_id = (
                        f"{signal_spec.signal_id}__{entry_spec.entry_id}__"
                        f"{stop_spec.stop_id}__{management_spec.management_id}"
                    )
                    models.append(
                        ExecutionModel(
                            model_id=model_id,
                            regime_id=signal_spec.regime_id,
                            signal_spec=signal_spec,
                            entry_spec=entry_spec,
                            stop_spec=stop_spec,
                            management_spec=management_spec,
                        )
                    )
    return tuple(models)


def _detect_signal(
    *,
    event_row: pd.Series,
    timestamps: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    signal_spec: SignalSpec,
) -> SignalInfo | None:
    start_ts = int(event_row["timestamp_ms"]) + FIVE_MINUTES_MS
    try:
        start_idx = timestamps.index(start_ts)
    except ValueError:
        return None
    trigger_high = float(event_row["trigger_high"])
    trigger_low = float(event_row["trigger_low"])
    trigger_range = max(1e-12, trigger_high - trigger_low)
    trigger_body = max(1e-12, abs(float(event_row["trigger_close"]) - float(event_row["trigger_open"])))
    avg_trigger_body_1m = trigger_body / 5.0
    avg_trigger_vol_1m = max(1e-12, float(event_row["trigger_volume"]) / 5.0)
    prebreak_low = trigger_high
    seller_pressure = 0.0
    end_idx = min(len(closes), start_idx + signal_spec.max_wait_minutes)
    for idx in range(start_idx, end_idx):
        prebreak_low = min(prebreak_low, lows[idx])
        if closes[idx] <= trigger_high:
            if closes[idx] < opens[idx]:
                seller_pressure += _pressure_score(opens[idx], closes[idx], volumes[idx], trigger_range, avg_trigger_vol_1m)
            continue
        body = closes[idx] - opens[idx]
        if body <= 0.0:
            continue
        body_ratio = body / avg_trigger_body_1m if avg_trigger_body_1m > 0.0 else 0.0
        volume_ratio = volumes[idx] / avg_trigger_vol_1m if avg_trigger_vol_1m > 0.0 else 0.0
        close_pos = _close_pos(highs[idx], lows[idx], closes[idx])
        buyer_score = _pressure_score(opens[idx], closes[idx], volumes[idx], trigger_range, avg_trigger_vol_1m)
        pullback_frac = max(0.0, (trigger_high - prebreak_low) / trigger_range)
        entry_extension_frac = max(0.0, (closes[idx] - trigger_high) / trigger_range)
        if body_ratio < signal_spec.min_break_body_ratio:
            continue
        if volume_ratio < signal_spec.min_break_volume_ratio:
            continue
        if close_pos < signal_spec.min_close_pos:
            continue
        if pullback_frac > signal_spec.max_pullback_frac:
            continue
        if seller_pressure > signal_spec.max_seller_pressure:
            continue
        if buyer_score < signal_spec.min_buyer_break_score:
            continue
        if entry_extension_frac > signal_spec.max_entry_extension_frac:
            continue
        signal_range = max(1e-12, highs[idx] - lows[idx])
        return SignalInfo(
            signal_idx=idx,
            signal_open=float(opens[idx]),
            signal_high=float(highs[idx]),
            signal_low=float(lows[idx]),
            signal_close=float(closes[idx]),
            signal_range=float(signal_range),
            signal_body_ratio=float(body_ratio),
            signal_volume_ratio=float(volume_ratio),
            signal_close_pos=float(close_pos),
            prebreak_low=float(prebreak_low),
            pullback_frac=float(pullback_frac),
            seller_pressure_score=float(seller_pressure),
            buyer_break_score=float(buyer_score),
            entry_extension_frac=float(entry_extension_frac),
            start_delay_min=int(idx - start_idx + 1),
        )
    return None


def _find_entry(
    *,
    event_row: pd.Series,
    signal_info: SignalInfo,
    entry_spec: EntrySpec,
    opens: list[float],
    highs: list[float],
    lows: list[float],
) -> EntryInfo | None:
    trigger_high = float(event_row["trigger_high"])
    trigger_low = float(event_row["trigger_low"])
    trigger_range = max(1e-12, trigger_high - trigger_low)
    signal_idx = signal_info.signal_idx

    if entry_spec.style == "signal_close":
        return EntryInfo(signal_idx, float(signal_info.signal_close), "signal_close")

    if entry_spec.style == "next_open":
        next_idx = signal_idx + 1
        if next_idx >= len(opens):
            return None
        return EntryInfo(next_idx, float(opens[next_idx]), "next_open")

    if entry_spec.style == "break_signal_high":
        for idx in range(signal_idx + 1, min(len(highs), signal_idx + 1 + entry_spec.valid_minutes)):
            if highs[idx] >= signal_info.signal_high:
                return EntryInfo(idx, float(signal_info.signal_high), "break_signal_high")
        return None

    if entry_spec.style == "pullback_limit" and entry_spec.pullback_frac is not None:
        limit_price = max(trigger_high, signal_info.signal_close - signal_info.signal_range * entry_spec.pullback_frac)
        invalidation_floor = trigger_high - (0.05 * trigger_range)
        for idx in range(signal_idx + 1, min(len(lows), signal_idx + 1 + entry_spec.valid_minutes)):
            if lows[idx] < invalidation_floor:
                return None
            if lows[idx] <= limit_price:
                return EntryInfo(idx, float(limit_price), f"pullback_limit_{int(entry_spec.pullback_frac * 100):02d}")
        return None

    return None


def _stop_price(*, signal_info: SignalInfo, stop_spec: StopSpec) -> float:
    if stop_spec.style == "signal_low":
        return float(signal_info.signal_low)
    if stop_spec.style == "signal_body_low":
        return float(min(signal_info.signal_open, signal_info.signal_close))
    if stop_spec.style == "prebreak_low":
        return float(signal_info.prebreak_low)
    raise ValueError(f"Unknown stop style: {stop_spec.style}")


def _simulate_managed_exit(
    *,
    highs: list[float],
    lows: list[float],
    closes: list[float],
    entry_idx: int,
    entry_price: float,
    stop_price: float,
    management_spec: ManagementSpec,
) -> tuple[int, float, str, int]:
    risk = entry_price - stop_price
    if risk <= 0.0:
        return entry_idx, 0.0, "invalid", 0
    partial_price = entry_price + risk * management_spec.partial_take_r if management_spec.partial_take_r is not None else None
    final_price = entry_price + risk * management_spec.final_target_r if management_spec.final_target_r is not None else None
    current_stop = float(stop_price)
    last_idx = min(len(closes) - 1, entry_idx + management_spec.max_hold_minutes)
    fast_fail_idx = min(len(closes) - 1, entry_idx + management_spec.fast_fail_minutes)
    remaining = 1.0
    realized_return = 0.0
    highest_high = entry_price
    partial_taken = False
    partial_bar_idx = -1
    stop_to_be_pending = False
    trail_active = False
    be_armed = False
    exit_reason = "time_exit"

    for idx in range(entry_idx + 1, last_idx + 1):
        if stop_to_be_pending:
            current_stop = max(current_stop, entry_price)
            stop_to_be_pending = False
        if trail_active and idx - 1 > entry_idx:
            current_stop = max(current_stop, lows[idx - 1])

        low_price = lows[idx]
        high_price = highs[idx]
        close_price = closes[idx]
        if low_price <= current_stop:
            realized_return += remaining * _net_long_return(entry_price, current_stop)
            exit_reason = "stop" if not partial_taken else "trail_stop"
            return idx, float(realized_return), exit_reason, 1 if partial_taken else 0

        highest_high = max(highest_high, high_price)

        if not partial_taken and partial_price is not None and high_price >= partial_price:
            realized_return += management_spec.partial_fraction * _net_long_return(entry_price, partial_price)
            remaining -= management_spec.partial_fraction
            partial_taken = True
            partial_bar_idx = idx
            if management_spec.move_stop_to_be_after_partial:
                stop_to_be_pending = True
            if management_spec.trail_after_partial:
                trail_active = True

        if management_spec.be_after_r is not None and not be_armed and high_price >= entry_price + risk * management_spec.be_after_r:
            stop_to_be_pending = True
            be_armed = True
        if management_spec.trail_after_r is not None and not trail_active and high_price >= entry_price + risk * management_spec.trail_after_r:
            trail_active = True

        if final_price is not None and high_price >= final_price:
            realized_return += remaining * _net_long_return(entry_price, final_price)
            exit_reason = "tp" if not partial_taken else "partial_tp"
            return idx, float(realized_return), exit_reason, 1 if partial_taken else 0

        if idx >= fast_fail_idx and highest_high < (entry_price + risk * management_spec.fast_fail_r):
            realized_return += remaining * _net_long_return(entry_price, close_price)
            exit_reason = "fast_fail"
            return idx, float(realized_return), exit_reason, 1 if partial_taken else 0

        if partial_taken and final_price is None and idx > partial_bar_idx and trail_active:
            pass

    realized_return += remaining * _net_long_return(entry_price, closes[last_idx])
    return last_idx, float(realized_return), exit_reason, 1 if partial_taken else 0


def _simulate_model_for_event(
    *,
    event_row: pd.Series,
    model: ExecutionModel,
    timestamps: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
) -> dict[str, object] | None:
    signal_info = _detect_signal(
        event_row=event_row,
        timestamps=timestamps,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
        signal_spec=model.signal_spec,
    )
    if signal_info is None:
        return None
    entry_info = _find_entry(
        event_row=event_row,
        signal_info=signal_info,
        entry_spec=model.entry_spec,
        opens=opens,
        highs=highs,
        lows=lows,
    )
    if entry_info is None:
        return None
    stop_price = _stop_price(signal_info=signal_info, stop_spec=model.stop_spec)
    if stop_price >= entry_info.entry_price:
        return None
    exit_idx, exit_return_pct, exit_reason, partial_taken = _simulate_managed_exit(
        highs=highs,
        lows=lows,
        closes=closes,
        entry_idx=entry_info.entry_idx,
        entry_price=entry_info.entry_price,
        stop_price=stop_price,
        management_spec=model.management_spec,
    )
    return {
        "entry_timestamp_ms": int(timestamps[entry_info.entry_idx] + ONE_MINUTE_MS),
        "exit_timestamp_ms": int(timestamps[exit_idx] + ONE_MINUTE_MS),
        "entry_price": float(entry_info.entry_price),
        "stop_price": float(stop_price),
        "initial_risk_pct": float((entry_info.entry_price - stop_price) / entry_info.entry_price),
        "exit_price": float(closes[exit_idx]),
        "exit_reason": exit_reason,
        "exit_return_pct": float(exit_return_pct),
        "entry_detail": entry_info.entry_detail,
        "partial_taken": int(partial_taken),
        "signal_delay_min": int(signal_info.start_delay_min),
        "signal_body_ratio": float(signal_info.signal_body_ratio),
        "signal_volume_ratio": float(signal_info.signal_volume_ratio),
        "signal_close_pos": float(signal_info.signal_close_pos),
        "signal_pullback_frac": float(signal_info.pullback_frac),
        "seller_pressure_score": float(signal_info.seller_pressure_score),
        "buyer_break_score": float(signal_info.buyer_break_score),
    }


def _build_events(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    models = _build_models()
    models_by_regime: dict[str, list[ExecutionModel]] = {}
    for model in models:
        models_by_regime.setdefault(model.regime_id, []).append(model)

    cache: dict[tuple[str, str], pd.DataFrame] = {}
    records: list[dict[str, object]] = []
    coverage_rows: list[dict[str, object]] = []
    grouped = frame.groupby(["cache_scope", "symbol"], sort=True)
    total_groups = grouped.ngroups

    for group_index, ((cache_scope, symbol), scoped) in enumerate(grouped, start=1):
        candles_1m = _load_m1(str(cache_scope), str(symbol), cache)
        if candles_1m.empty:
            continue
        timestamps = pd.to_numeric(candles_1m["timestamp"], errors="coerce").astype("int64").tolist()
        opens = pd.to_numeric(candles_1m["open"], errors="coerce").astype(float).tolist()
        highs = pd.to_numeric(candles_1m["high"], errors="coerce").astype(float).tolist()
        lows = pd.to_numeric(candles_1m["low"], errors="coerce").astype(float).tolist()
        closes = pd.to_numeric(candles_1m["close"], errors="coerce").astype(float).tolist()
        volumes = pd.to_numeric(candles_1m["volume"], errors="coerce").astype(float).tolist()

        if group_index == 1 or group_index % 25 == 0 or group_index == total_groups:
            print(
                f"second-wave-execution: progress={group_index}/{total_groups} "
                f"cache={cache_scope} symbol={symbol} events={len(scoped)}"
            )

        for _, row in scoped.iterrows():
            regime_id = str(row["regime_id"])
            base = {
                "regime_id": regime_id,
                "dataset": row["dataset"],
                "symbol": row["symbol"],
                "timestamp_ms": int(row["timestamp_ms"]),
                "month_utc": row["month_utc"],
                "date_utc": row["date_utc"],
                "trigger_return_pct": float(row["trigger_return_pct"]),
                "range_atr": float(row["range_atr"]),
                "body_atr": float(row["body_atr"]),
                "volume_mult": float(row["volume_mult"]),
                "pre_base_range_pct_60m": float(row["pre_base_range_pct_60m"]),
                "pre_base_drift_pct_60m": float(row["pre_base_drift_pct_60m"]),
                "pre_accumulation_type": row["pre_accumulation_type"],
            }
            coverage_rows.append(
                {
                    "regime_id": regime_id,
                    "dataset": row["dataset"],
                    "symbol": row["symbol"],
                    "timestamp_ms": int(row["timestamp_ms"]),
                }
            )
            for model in models_by_regime.get(regime_id, []):
                simulated = _simulate_model_for_event(
                    event_row=row,
                    model=model,
                    timestamps=timestamps,
                    opens=opens,
                    highs=highs,
                    lows=lows,
                    closes=closes,
                    volumes=volumes,
                )
                if simulated is None:
                    continue
                records.append(
                    {
                        **base,
                        **simulated,
                        "model_id": model.model_id,
                        "signal_id": model.signal_spec.signal_id,
                        "entry_id": model.entry_spec.entry_id,
                        "stop_id": model.stop_spec.stop_id,
                        "management_id": model.management_spec.management_id,
                    }
                )

    coverage = pd.DataFrame(coverage_rows).drop_duplicates()
    events = (
        pd.DataFrame(records)
        .sort_values(["regime_id", "model_id", "dataset", "timestamp_ms", "symbol"])
        .reset_index(drop=True)
        if records
        else pd.DataFrame()
    )
    return events, coverage


def _dataset_metrics(events: pd.DataFrame) -> dict[str, float | int]:
    if events.empty:
        return {
            "trades": 0,
            "mean_return_pct": 0.0,
            "win_rate": 0.0,
            "annualized_unit_pnl_pct": 0.0,
            "equity_annualized_return_pct_5": 0.0,
            "equity_max_drawdown_pct_5": 0.0,
            "equity_positive_months_count": 0,
            "equity_stable_positive_months_count": 0,
        }
    calendar_months = _calendar_months_from_frames(events)
    summary = _summarize_events(events, calendar_months=calendar_months)
    equity, _ = _simulate_equity_risk_metrics(events, calendar_months=calendar_months, risk_fraction=0.05)
    return {
        "trades": int(len(events)),
        "mean_return_pct": float(summary["mean_return_pct"]),
        "win_rate": float(summary["win_rate"]),
        "annualized_unit_pnl_pct": float(summary["annualized_unit_pnl_pct"]),
        "equity_annualized_return_pct_5": float(equity["equity_annualized_return_pct"]),
        "equity_max_drawdown_pct_5": float(equity["equity_max_drawdown_pct"]),
        "equity_positive_months_count": int(equity["equity_positive_months_count"]),
        "equity_stable_positive_months_count": int(equity["equity_stable_positive_months_count"]),
    }


def _summarize_models(events: pd.DataFrame, coverage: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    coverage_counts = (
        coverage.groupby(["regime_id", "dataset"]).size().unstack(fill_value=0).rename(columns={"current": "coverage_current", "old": "coverage_old"})
    )
    rows: list[dict[str, object]] = []
    for (regime_id, model_id), scoped in events.groupby(["regime_id", "model_id"], sort=True):
        current = scoped[scoped["dataset"] == "current"].copy()
        old = scoped[scoped["dataset"] == "old"].copy()
        combined = scoped.copy()
        combined_metrics = _dataset_metrics(combined)
        current_metrics = _dataset_metrics(current)
        old_metrics = _dataset_metrics(old)
        coverage_current = int(coverage_counts.loc[regime_id, "coverage_current"]) if regime_id in coverage_counts.index and "coverage_current" in coverage_counts.columns else 0
        coverage_old = int(coverage_counts.loc[regime_id, "coverage_old"]) if regime_id in coverage_counts.index and "coverage_old" in coverage_counts.columns else 0
        rows.append(
            {
                "regime_id": regime_id,
                "model_id": model_id,
                "signal_id": scoped["signal_id"].iloc[0],
                "entry_id": scoped["entry_id"].iloc[0],
                "stop_id": scoped["stop_id"].iloc[0],
                "management_id": scoped["management_id"].iloc[0],
                "coverage_current": coverage_current,
                "coverage_old": coverage_old,
                "current_trades": int(current_metrics["trades"]),
                "old_trades": int(old_metrics["trades"]),
                "coverage_rate_current": float(current_metrics["trades"] / coverage_current) if coverage_current > 0 else 0.0,
                "coverage_rate_old": float(old_metrics["trades"] / coverage_old) if coverage_old > 0 else 0.0,
                "current_mean_return_pct": float(current_metrics["mean_return_pct"]),
                "old_mean_return_pct": float(old_metrics["mean_return_pct"]),
                "current_win_rate": float(current_metrics["win_rate"]),
                "old_win_rate": float(old_metrics["win_rate"]),
                "current_equity_annualized_return_pct_5": float(current_metrics["equity_annualized_return_pct_5"]),
                "old_equity_annualized_return_pct_5": float(old_metrics["equity_annualized_return_pct_5"]),
                "current_equity_max_drawdown_pct_5": float(current_metrics["equity_max_drawdown_pct_5"]),
                "old_equity_max_drawdown_pct_5": float(old_metrics["equity_max_drawdown_pct_5"]),
                "current_equity_stable_positive_months_count": int(current_metrics["equity_stable_positive_months_count"]),
                "old_equity_stable_positive_months_count": int(old_metrics["equity_stable_positive_months_count"]),
                "combined_trades_per_year": float(combined_metrics["trades"]) / 2.0857142857,
                "combined_mean_return_pct": float(combined_metrics["mean_return_pct"]),
                "combined_win_rate": float(combined_metrics["win_rate"]),
                "combined_annualized_unit_pnl_pct": float(combined_metrics["annualized_unit_pnl_pct"]),
                "combined_equity_annualized_return_pct_5": float(combined_metrics["equity_annualized_return_pct_5"]),
                "combined_equity_max_drawdown_pct_5": float(combined_metrics["equity_max_drawdown_pct_5"]),
                "combined_equity_positive_months_count": int(combined_metrics["equity_positive_months_count"]),
                "combined_equity_stable_positive_months_count": int(combined_metrics["equity_stable_positive_months_count"]),
            }
        )
    summary = pd.DataFrame(rows)
    summary["score"] = (
        summary[["current_equity_annualized_return_pct_5", "old_equity_annualized_return_pct_5"]].min(axis=1)
        - 0.60 * summary[["current_equity_max_drawdown_pct_5", "old_equity_max_drawdown_pct_5"]].max(axis=1)
        + 12.0 * summary[["current_mean_return_pct", "old_mean_return_pct"]].min(axis=1)
        + 0.12 * summary[["current_trades", "old_trades"]].min(axis=1)
        + 0.30 * summary[["current_equity_stable_positive_months_count", "old_equity_stable_positive_months_count"]].min(axis=1)
    )
    return summary.sort_values(
        [
            "regime_id",
            "score",
            "combined_equity_annualized_return_pct_5",
            "combined_mean_return_pct",
            "combined_trades_per_year",
        ],
        ascending=[True, False, False, False, False],
    ).reset_index(drop=True)


def _select_best(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return summary
    selected_rows: list[pd.Series] = []
    for regime_id, scoped in summary.groupby("regime_id", sort=True):
        ready = scoped[
            (scoped["current_trades"] >= 15)
            & (scoped["old_trades"] >= 6)
            & (scoped["current_mean_return_pct"] > 0.0)
            & (scoped["old_mean_return_pct"] > 0.0)
            & (scoped["current_equity_annualized_return_pct_5"] > 0.0)
            & (scoped["old_equity_annualized_return_pct_5"] > 0.0)
        ].copy()
        if not ready.empty:
            row = ready.iloc[0].copy()
            row["selection_status"] = "candidate_ready"
            selected_rows.append(row)
            continue
        thin = scoped[
            (scoped["current_mean_return_pct"] > 0.0)
            & (scoped["old_mean_return_pct"] > 0.0)
            & (
                (scoped["current_equity_annualized_return_pct_5"] > 0.0)
                | (scoped["old_equity_annualized_return_pct_5"] > 0.0)
            )
        ].copy()
        if not thin.empty:
            row = thin.iloc[0].copy()
            row["selection_status"] = "thin_positive"
            selected_rows.append(row)
            continue
        row = scoped.iloc[0].copy()
        row["selection_status"] = "not_ready"
        selected_rows.append(row)
    return pd.DataFrame(selected_rows).reset_index(drop=True)


def _build_selected_outputs(events: pd.DataFrame, selected: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if events.empty or selected.empty:
        return pd.DataFrame(), pd.DataFrame()
    chosen = events.merge(selected[["regime_id", "model_id", "selection_status"]], on=["regime_id", "model_id"], how="inner").copy()
    chosen = chosen[chosen["selection_status"].astype(str) != "not_ready"].copy()
    monthly = _build_monthly_returns_frame(chosen)
    return chosen, monthly


def _build_report(summary: pd.DataFrame, selected: pd.DataFrame) -> str:
    lines = [
        "# Online Second-Wave Execution Search",
        "",
        "Цель: честно протестировать исполнение входа во вторую волну по данным, известным только к моменту входа.",
        "",
        "Что перебиралось:",
        "- online-сигнал старта второй волны по `1m` после аномальной `5m`;",
        "- точки входа: `signal_close`, `next_open`, `break_signal_high`, `pullback_limit`;",
        "- стопы: `signal_low`, `signal_body_low`, `prebreak_low`;",
        "- менеджмент: fixed `R`, partial + `BE`, partial + trail, `BE`/trail после `1R`.",
        "",
    ]
    if summary.empty:
        lines.append("Подходящих моделей не найдено.")
        return "\n".join(lines)
    if not selected.empty:
        lines.extend(
            [
                "## Лучшие режимы",
                _frame_to_markdown(
                    selected,
                    columns=[
                        "regime_id",
                        "selection_status",
                        "model_id",
                        "signal_id",
                        "entry_id",
                        "stop_id",
                        "management_id",
                        "current_trades",
                        "old_trades",
                        "current_mean_return_pct",
                        "old_mean_return_pct",
                        "current_equity_annualized_return_pct_5",
                        "old_equity_annualized_return_pct_5",
                        "combined_equity_max_drawdown_pct_5",
                    ],
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## Топ моделей",
            _frame_to_markdown(
                summary.head(20),
                columns=[
                    "regime_id",
                    "model_id",
                    "signal_id",
                    "entry_id",
                    "stop_id",
                    "management_id",
                    "current_trades",
                    "old_trades",
                    "current_mean_return_pct",
                    "old_mean_return_pct",
                    "current_equity_annualized_return_pct_5",
                    "old_equity_annualized_return_pct_5",
                    "combined_equity_max_drawdown_pct_5",
                ],
            ),
            "",
        ]
    )
    return "\n".join(lines)


def run() -> dict[str, Path]:
    print("second-wave-execution: start")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    scoped = _regime_scope(_load_feature_db())
    print(f"second-wave-execution: scoped_events={len(scoped)}")
    events, coverage = _build_events(scoped)
    print(f"second-wave-execution: built_events={len(events)}")
    summary = _summarize_models(events, coverage)
    selected = _select_best(summary)
    selected_events, selected_monthly = _build_selected_outputs(events, selected)
    paths = {
        "coverage": OUTPUT_DIR / "coverage.csv",
        "events": OUTPUT_DIR / "events.csv",
        "summary": OUTPUT_DIR / "summary.csv",
        "selected": OUTPUT_DIR / "selected_models.csv",
        "selected_events": OUTPUT_DIR / "selected_events.csv",
        "selected_monthly": OUTPUT_DIR / "selected_monthly.csv",
        "report": OUTPUT_DIR / "report.md",
    }
    coverage.to_csv(paths["coverage"], index=False)
    events.to_csv(paths["events"], index=False)
    summary.to_csv(paths["summary"], index=False)
    selected.to_csv(paths["selected"], index=False)
    selected_events.to_csv(paths["selected_events"], index=False)
    selected_monthly.to_csv(paths["selected_monthly"], index=False)
    paths["report"].write_text(_build_report(summary, selected), encoding="utf-8")
    print("second-wave-execution: done")
    return paths


if __name__ == "__main__":
    run()
