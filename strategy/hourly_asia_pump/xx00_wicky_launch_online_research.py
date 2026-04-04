from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import median

import pandas as pd

from strategy.hourly_asia_pump.anomaly_category_lab import _calendar_months_from_frame
from strategy.hourly_asia_pump.static_combo import (
    _build_monthly_returns_frame,
    _frame_to_markdown,
    _summarize_events,
)
from strategy.hourly_asia_pump.tradeable_second_wave_models import (
    ONE_MINUTE_MS,
    _close_pos,
    _load_feature_db,
    _load_m1,
    _net_long_return,
    _simulate_exit_1m,
)
from strategy.hourly_asia_pump.unified_edge import _simulate_equity_risk_metrics

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "xx00_wicky_launch_online_research"

SUPER_SYMBOL = "SUPER/USDT:USDT"
SUPER_TIMESTAMP_MS = 1774483200000  # 2026-03-26 00:00:00 UTC
SONIC_SYMBOL = "SONIC/USDT:USDT"
SONIC_TIMESTAMP_MS = 1752278400000  # 2025-07-12 00:00:00 UTC


@dataclass(frozen=True, slots=True)
class CohortSpec:
    cohort_id: str
    title: str
    contexts: tuple[str, ...]
    min_trigger_return_pct: float
    min_volume_mult: float
    allowed_pre_accumulation_types: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class LaunchCloseRule:
    rule_id: str
    family: str
    min_launch_return_pct: float
    min_launch_close_pos: float
    min_launch_body_frac_range: float
    min_launch_volume_ratio: float
    min_close_break_buffer_pct: float
    max_pre_range_pct_60m: float
    max_pre_abs_drift_pct_60m: float
    rr_target: float


@dataclass(frozen=True, slots=True)
class FollowBreakRule:
    rule_id: str
    family: str
    min_launch_return_pct: float
    min_launch_volume_ratio: float
    min_high_break_buffer_pct: float
    max_signal_offset: int
    min_signal_body_frac_launch: float
    min_signal_close_pos: float
    min_signal_volume_ratio: float
    max_pullback_frac: float
    max_entry_extension_frac: float
    stop_style: str
    rr_target: float


@dataclass(frozen=True, slots=True)
class ReclaimBreakRule:
    rule_id: str
    family: str
    min_launch_return_pct: float
    min_launch_volume_ratio: float
    min_high_break_buffer_pct: float
    max_signal_offset: int
    min_reset_frac: float
    max_reset_frac: float
    min_signal_body_frac_launch: float
    min_signal_close_pos: float
    min_signal_volume_ratio: float
    max_entry_extension_frac: float
    stop_style: str
    rr_target: float


@dataclass(frozen=True, slots=True)
class EventState:
    start_idx: int
    prelaunch_volume_median_30: float
    prev_high_60: float
    launch_open: float
    launch_high: float
    launch_low: float
    launch_close: float
    launch_range: float
    launch_return_pct: float
    launch_close_pos: float
    launch_body_frac_range: float
    launch_volume_ratio: float
    launch_close_break_buffer_pct: float
    launch_high_break_buffer_pct: float
    pre_base_range_pct_60m: float
    pre_abs_drift_pct_60m: float


def _cohort_specs() -> tuple[CohortSpec, ...]:
    return (
        CohortSpec(
            cohort_id="anchor_like_warm_none_r020_v012",
            title="warm + wicky_spike + none, trigger >= 2%, volume >= 12",
            contexts=("context_warm",),
            min_trigger_return_pct=0.02,
            min_volume_mult=12.0,
            allowed_pre_accumulation_types=("none",),
        ),
        CohortSpec(
            cohort_id="analog_warm_hot_any_r020_v010",
            title="warm/hot + wicky_spike, trigger >= 2%, volume >= 10",
            contexts=("context_warm", "context_overheated"),
            min_trigger_return_pct=0.02,
            min_volume_mult=10.0,
        ),
        CohortSpec(
            cohort_id="analog_warm_hot_any_r030_v012",
            title="warm/hot + wicky_spike, trigger >= 3%, volume >= 12",
            contexts=("context_warm", "context_overheated"),
            min_trigger_return_pct=0.03,
            min_volume_mult=12.0,
        ),
    )


def _launch_close_rules() -> tuple[LaunchCloseRule, ...]:
    rules: list[LaunchCloseRule] = []
    for min_launch_return_pct in (0.015, 0.02, 0.03):
        for min_launch_close_pos in (0.70, 0.85):
            for min_launch_body_frac_range in (0.45, 0.60):
                for min_launch_volume_ratio in (4.0, 8.0, 12.0):
                    for min_close_break_buffer_pct in (0.0, 0.005):
                        for max_pre_range_pct_60m in (0.04, 0.08):
                            for rr_target in (1.5, 2.0):
                                rule_id = (
                                    f"launch_r{int(min_launch_return_pct*1000):03d}"
                                    f"_c{int(min_launch_close_pos*100):02d}"
                                    f"_b{int(min_launch_body_frac_range*100):02d}"
                                    f"_v{int(min_launch_volume_ratio):02d}"
                                    f"_x{int(min_close_break_buffer_pct*1000):03d}"
                                    f"_pr{int(max_pre_range_pct_60m*1000):03d}"
                                    f"_rr{int(rr_target*10):02d}"
                                )
                                rules.append(
                                    LaunchCloseRule(
                                        rule_id=rule_id,
                                        family="launch_close",
                                        min_launch_return_pct=min_launch_return_pct,
                                        min_launch_close_pos=min_launch_close_pos,
                                        min_launch_body_frac_range=min_launch_body_frac_range,
                                        min_launch_volume_ratio=min_launch_volume_ratio,
                                        min_close_break_buffer_pct=min_close_break_buffer_pct,
                                        max_pre_range_pct_60m=max_pre_range_pct_60m,
                                        max_pre_abs_drift_pct_60m=0.05,
                                        rr_target=rr_target,
                                    )
                                )
    return tuple(rules)


def _follow_break_rules() -> tuple[FollowBreakRule, ...]:
    rules: list[FollowBreakRule] = []
    for min_launch_return_pct in (0.003, 0.005, 0.015):
        for min_launch_volume_ratio in (4.0,):
            for min_high_break_buffer_pct in (0.0,):
                for max_signal_offset in (1, 2):
                    for min_signal_body_frac_launch in (0.10, 0.20):
                        for min_signal_close_pos in (0.65,):
                            for min_signal_volume_ratio in (2.0, 4.0):
                                for max_pullback_frac in (0.35, 0.55):
                                    for max_entry_extension_frac in (1.25, 2.00):
                                        for stop_style in ("signal_low", "setup_low"):
                                            for rr_target in (1.5, 2.0):
                                                rule_id = (
                                                    f"follow_lr{int(min_launch_return_pct*1000):03d}"
                                                    f"_lv{int(min_launch_volume_ratio):02d}"
                                                    f"_hb{int(min_high_break_buffer_pct*1000):03d}"
                                                    f"_w{max_signal_offset}"
                                                    f"_sb{int(min_signal_body_frac_launch*100):02d}"
                                                    f"_sc{int(min_signal_close_pos*100):02d}"
                                                    f"_sv{int(min_signal_volume_ratio):02d}"
                                                    f"_pb{int(max_pullback_frac*100):02d}"
                                                    f"_ex{int(max_entry_extension_frac*100):03d}"
                                                    f"_{stop_style}"
                                                    f"_rr{int(rr_target*10):02d}"
                                                )
                                                rules.append(
                                                    FollowBreakRule(
                                                        rule_id=rule_id,
                                                        family="follow_break",
                                                        min_launch_return_pct=min_launch_return_pct,
                                                        min_launch_volume_ratio=min_launch_volume_ratio,
                                                        min_high_break_buffer_pct=min_high_break_buffer_pct,
                                                        max_signal_offset=max_signal_offset,
                                                        min_signal_body_frac_launch=min_signal_body_frac_launch,
                                                        min_signal_close_pos=min_signal_close_pos,
                                                        min_signal_volume_ratio=min_signal_volume_ratio,
                                                        max_pullback_frac=max_pullback_frac,
                                                        max_entry_extension_frac=max_entry_extension_frac,
                                                        stop_style=stop_style,
                                                        rr_target=rr_target,
                                                    )
                                                )
    return tuple(rules)


def _reclaim_break_rules() -> tuple[ReclaimBreakRule, ...]:
    rules: list[ReclaimBreakRule] = []
    for min_launch_return_pct in (0.003, 0.005, 0.015):
        for min_launch_volume_ratio in (4.0,):
            for min_high_break_buffer_pct in (0.0,):
                for max_signal_offset in (3, 4):
                    for min_reset_frac in (0.10, 0.25):
                        for max_reset_frac in (0.40, 0.60):
                            for min_signal_body_frac_launch in (0.10, 0.20):
                                for min_signal_close_pos in (0.65,):
                                    for min_signal_volume_ratio in (1.0, 2.0):
                                        for max_entry_extension_frac in (0.50, 1.00):
                                            for stop_style in ("signal_low", "setup_low"):
                                                for rr_target in (1.5, 2.0):
                                                    rule_id = (
                                                        f"reclaim_lr{int(min_launch_return_pct*1000):03d}"
                                                        f"_lv{int(min_launch_volume_ratio):02d}"
                                                        f"_hb{int(min_high_break_buffer_pct*1000):03d}"
                                                        f"_w{max_signal_offset}"
                                                        f"_rs{int(min_reset_frac*100):02d}"
                                                        f"_rx{int(max_reset_frac*100):02d}"
                                                        f"_sb{int(min_signal_body_frac_launch*100):02d}"
                                                        f"_sc{int(min_signal_close_pos*100):02d}"
                                                        f"_sv{int(min_signal_volume_ratio*100):03d}"
                                                        f"_ex{int(max_entry_extension_frac*100):03d}"
                                                        f"_{stop_style}"
                                                        f"_rr{int(rr_target*10):02d}"
                                                    )
                                                    rules.append(
                                                        ReclaimBreakRule(
                                                            rule_id=rule_id,
                                                            family="reclaim_break",
                                                            min_launch_return_pct=min_launch_return_pct,
                                                            min_launch_volume_ratio=min_launch_volume_ratio,
                                                            min_high_break_buffer_pct=min_high_break_buffer_pct,
                                                            max_signal_offset=max_signal_offset,
                                                            min_reset_frac=min_reset_frac,
                                                            max_reset_frac=max_reset_frac,
                                                            min_signal_body_frac_launch=min_signal_body_frac_launch,
                                                            min_signal_close_pos=min_signal_close_pos,
                                                            min_signal_volume_ratio=min_signal_volume_ratio,
                                                            max_entry_extension_frac=max_entry_extension_frac,
                                                            stop_style=stop_style,
                                                            rr_target=rr_target,
                                                        )
                                                    )
    return tuple(rules)


def _base_scope(frame: pd.DataFrame) -> pd.DataFrame:
    scoped = frame[
        (frame["timeframe"].astype(str) == "5m")
        & (frame["session_id"].astype(str) == "asia")
        & (frame["market_side_hint"].astype(str) == "long")
        & (pd.to_numeric(frame["trigger_minute"], errors="coerce") == 0)
        & (frame["impulse_archetype"].astype(str) == "impulse_wicky_spike")
    ].copy()
    scoped["timestamp_utc"] = pd.to_datetime(pd.to_numeric(scoped["timestamp_ms"], errors="coerce"), unit="ms", utc=True, errors="coerce")
    scoped["cache_scope"] = scoped["dataset"].map({"current": "current", "old": "old"})
    last_timestamp = scoped["timestamp_utc"].max()
    last_year_cutoff = last_timestamp - pd.Timedelta(days=365) if pd.notna(last_timestamp) else None
    if last_year_cutoff is not None:
        current_mask = scoped["dataset"].astype(str) == "current"
        scoped = scoped[(~current_mask) | (scoped["timestamp_utc"] >= last_year_cutoff)].copy()
    return scoped.reset_index(drop=True)


def _cohort_scope(frame: pd.DataFrame, spec: CohortSpec) -> pd.DataFrame:
    scoped = frame[
        frame["context_archetype"].astype(str).isin(spec.contexts)
        & (pd.to_numeric(frame["trigger_return_pct"], errors="coerce") >= spec.min_trigger_return_pct)
        & (pd.to_numeric(frame["volume_mult"], errors="coerce") >= spec.min_volume_mult)
    ].copy()
    if spec.allowed_pre_accumulation_types is not None:
        scoped = scoped[scoped["pre_accumulation_type"].astype(str).isin(spec.allowed_pre_accumulation_types)].copy()
    scoped["cohort_id"] = spec.cohort_id
    scoped["cohort_title"] = spec.title
    return scoped.reset_index(drop=True)


def _safe_float(value: object, *, default: float = 0.0) -> float:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return default
    return float(numeric)


def _prepare_event_state(
    event_row: pd.Series,
    timestamp_to_idx: dict[int, int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
) -> EventState | None:
    start_ts = int(event_row["timestamp_ms"])
    start_idx = timestamp_to_idx.get(start_ts)
    if start_idx is None:
        return None
    if start_idx < 60 or (start_idx + 121) >= len(closes):
        return None
    prelaunch_volumes = [value for value in volumes[start_idx - 30 : start_idx] if value > 0.0]
    if not prelaunch_volumes:
        return None
    prelaunch_volume_median_30 = float(median(prelaunch_volumes))
    if prelaunch_volume_median_30 <= 0.0:
        return None

    launch_open = float(opens[start_idx])
    launch_high = float(highs[start_idx])
    launch_low = float(lows[start_idx])
    launch_close = float(closes[start_idx])
    launch_range = max(1e-12, launch_high - launch_low)
    launch_body = launch_close - launch_open
    launch_return_pct = (launch_close - launch_open) / launch_open if launch_open > 0.0 else 0.0
    launch_close_pos = _close_pos(launch_high, launch_low, launch_close)
    launch_body_frac_range = max(0.0, launch_body) / launch_range
    launch_volume_ratio = float(volumes[start_idx] / prelaunch_volume_median_30)
    prev_high_60 = max(highs[start_idx - 60 : start_idx])
    launch_close_break_buffer_pct = ((launch_close - prev_high_60) / launch_open) if launch_open > 0.0 else 0.0
    launch_high_break_buffer_pct = ((launch_high - prev_high_60) / launch_open) if launch_open > 0.0 else 0.0
    return EventState(
        start_idx=start_idx,
        prelaunch_volume_median_30=prelaunch_volume_median_30,
        prev_high_60=float(prev_high_60),
        launch_open=launch_open,
        launch_high=launch_high,
        launch_low=launch_low,
        launch_close=launch_close,
        launch_range=launch_range,
        launch_return_pct=float(launch_return_pct),
        launch_close_pos=float(launch_close_pos),
        launch_body_frac_range=float(launch_body_frac_range),
        launch_volume_ratio=float(launch_volume_ratio),
        launch_close_break_buffer_pct=float(launch_close_break_buffer_pct),
        launch_high_break_buffer_pct=float(launch_high_break_buffer_pct),
        pre_base_range_pct_60m=_safe_float(event_row.get("pre_base_range_pct_60m"), default=999.0),
        pre_abs_drift_pct_60m=abs(_safe_float(event_row.get("pre_base_drift_pct_60m"), default=999.0)),
    )


def _simulate_launch_close_rule(
    *,
    event_state: EventState,
    timestamps: list[int],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    rule: LaunchCloseRule,
) -> dict[str, object] | None:
    if event_state.launch_close <= event_state.prev_high_60:
        return None
    if event_state.launch_return_pct < rule.min_launch_return_pct:
        return None
    if event_state.launch_close_pos < rule.min_launch_close_pos:
        return None
    if event_state.launch_body_frac_range < rule.min_launch_body_frac_range:
        return None
    if event_state.launch_volume_ratio < rule.min_launch_volume_ratio:
        return None
    if event_state.launch_close_break_buffer_pct < rule.min_close_break_buffer_pct:
        return None
    if event_state.pre_base_range_pct_60m > rule.max_pre_range_pct_60m:
        return None
    if event_state.pre_abs_drift_pct_60m > rule.max_pre_abs_drift_pct_60m:
        return None

    entry_idx = event_state.start_idx
    entry_price = event_state.launch_close
    stop_price = event_state.launch_low
    if stop_price >= entry_price:
        return None
    exit_idx, exit_price, exit_reason = _simulate_exit_1m(
        timestamps=timestamps,
        highs=highs,
        lows=lows,
        closes=closes,
        entry_idx=entry_idx,
        entry_price=entry_price,
        stop_price=stop_price,
        rr_target=rule.rr_target,
        max_hold_minutes=120,
        fast_fail_minutes=8,
        fast_fail_r=0.25,
    )
    return {
        "signal_bar_timestamp_ms": int(timestamps[entry_idx]),
        "entry_timestamp_ms": int(timestamps[entry_idx] + ONE_MINUTE_MS),
        "exit_timestamp_ms": int(timestamps[exit_idx] + ONE_MINUTE_MS),
        "entry_price": float(entry_price),
        "stop_price": float(stop_price),
        "initial_risk_pct": float((entry_price - stop_price) / entry_price),
        "exit_price": float(exit_price),
        "exit_reason": exit_reason,
        "exit_return_pct": _net_long_return(entry_price, exit_price),
        "entry_detail": "launch_close",
        "entry_delay_minutes": 1,
        "launch_return_pct_online": float(event_state.launch_return_pct),
        "launch_close_pos_online": float(event_state.launch_close_pos),
        "launch_volume_ratio_online": float(event_state.launch_volume_ratio),
        "pullback_frac_before_entry": 0.0,
    }


def _setup_base_ok(event_state: EventState, min_launch_return_pct: float, min_launch_volume_ratio: float, min_high_break_buffer_pct: float) -> bool:
    if event_state.launch_high <= event_state.prev_high_60:
        return False
    if event_state.launch_return_pct < min_launch_return_pct:
        return False
    if event_state.launch_close_pos < 0.65:
        return False
    if event_state.launch_body_frac_range < 0.40:
        return False
    if event_state.launch_volume_ratio < min_launch_volume_ratio:
        return False
    if event_state.launch_high_break_buffer_pct < min_high_break_buffer_pct:
        return False
    if event_state.pre_base_range_pct_60m > 0.08:
        return False
    if event_state.pre_abs_drift_pct_60m > 0.05:
        return False
    return True


def _simulate_follow_break_rule(
    *,
    event_state: EventState,
    timestamps: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    rule: FollowBreakRule,
) -> dict[str, object] | None:
    if not _setup_base_ok(event_state, rule.min_launch_return_pct, rule.min_launch_volume_ratio, rule.min_high_break_buffer_pct):
        return None

    setup_low = event_state.launch_close
    for offset in range(1, rule.max_signal_offset + 1):
        idx = event_state.start_idx + offset
        signal_close = float(closes[idx])
        signal_open = float(opens[idx])
        signal_high = float(highs[idx])
        signal_low = float(lows[idx])
        signal_body = signal_close - signal_open
        if signal_close <= event_state.launch_high or signal_body <= 0.0:
            setup_low = min(setup_low, signal_low)
            continue
        signal_body_frac_launch = signal_body / event_state.launch_range
        signal_close_pos = _close_pos(signal_high, signal_low, signal_close)
        signal_volume_ratio = float(volumes[idx] / event_state.prelaunch_volume_median_30)
        pullback_frac = max(0.0, (event_state.launch_close - min(setup_low, signal_low)) / event_state.launch_range)
        entry_extension_frac = max(0.0, (signal_close - event_state.launch_high) / event_state.launch_range)
        if signal_body_frac_launch < rule.min_signal_body_frac_launch:
            setup_low = min(setup_low, signal_low)
            continue
        if signal_close_pos < rule.min_signal_close_pos:
            setup_low = min(setup_low, signal_low)
            continue
        if signal_volume_ratio < rule.min_signal_volume_ratio:
            setup_low = min(setup_low, signal_low)
            continue
        if pullback_frac > rule.max_pullback_frac:
            setup_low = min(setup_low, signal_low)
            continue
        if entry_extension_frac > rule.max_entry_extension_frac:
            setup_low = min(setup_low, signal_low)
            continue

        stop_price = signal_low if rule.stop_style == "signal_low" else min(setup_low, signal_low)
        entry_price = signal_close
        if stop_price >= entry_price:
            setup_low = min(setup_low, signal_low)
            continue
        exit_idx, exit_price, exit_reason = _simulate_exit_1m(
            timestamps=timestamps,
            highs=highs,
            lows=lows,
            closes=closes,
            entry_idx=idx,
            entry_price=entry_price,
            stop_price=stop_price,
            rr_target=rule.rr_target,
            max_hold_minutes=120,
            fast_fail_minutes=10,
            fast_fail_r=0.25,
        )
        return {
            "signal_bar_timestamp_ms": int(timestamps[idx]),
            "entry_timestamp_ms": int(timestamps[idx] + ONE_MINUTE_MS),
            "exit_timestamp_ms": int(timestamps[exit_idx] + ONE_MINUTE_MS),
            "entry_price": float(entry_price),
            "stop_price": float(stop_price),
            "initial_risk_pct": float((entry_price - stop_price) / entry_price),
            "exit_price": float(exit_price),
            "exit_reason": exit_reason,
            "exit_return_pct": _net_long_return(entry_price, exit_price),
            "entry_detail": "follow_break",
            "entry_delay_minutes": int(offset + 1),
            "launch_return_pct_online": float(event_state.launch_return_pct),
            "launch_close_pos_online": float(event_state.launch_close_pos),
            "launch_volume_ratio_online": float(event_state.launch_volume_ratio),
            "signal_body_frac_launch": float(signal_body_frac_launch),
            "signal_close_pos_online": float(signal_close_pos),
            "signal_volume_ratio_online": float(signal_volume_ratio),
            "pullback_frac_before_entry": float(pullback_frac),
            "entry_extension_frac_online": float(entry_extension_frac),
        }
    return None


def _simulate_reclaim_break_rule(
    *,
    event_state: EventState,
    timestamps: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    rule: ReclaimBreakRule,
) -> dict[str, object] | None:
    if not _setup_base_ok(event_state, rule.min_launch_return_pct, rule.min_launch_volume_ratio, rule.min_high_break_buffer_pct):
        return None

    setup_low = event_state.launch_close
    for offset in range(1, rule.max_signal_offset + 1):
        idx = event_state.start_idx + offset
        signal_close = float(closes[idx])
        signal_open = float(opens[idx])
        signal_high = float(highs[idx])
        signal_low = float(lows[idx])
        setup_low = min(setup_low, signal_low)
        if signal_close <= event_state.launch_high:
            continue
        signal_body = signal_close - signal_open
        if signal_body <= 0.0:
            continue
        reset_frac = max(0.0, (event_state.launch_close - setup_low) / event_state.launch_range)
        signal_body_frac_launch = signal_body / event_state.launch_range
        signal_close_pos = _close_pos(signal_high, signal_low, signal_close)
        signal_volume_ratio = float(volumes[idx] / event_state.prelaunch_volume_median_30)
        entry_extension_frac = max(0.0, (signal_close - event_state.launch_high) / event_state.launch_range)
        if reset_frac < rule.min_reset_frac or reset_frac > rule.max_reset_frac:
            continue
        if signal_body_frac_launch < rule.min_signal_body_frac_launch:
            continue
        if signal_close_pos < rule.min_signal_close_pos:
            continue
        if signal_volume_ratio < rule.min_signal_volume_ratio:
            continue
        if entry_extension_frac > rule.max_entry_extension_frac:
            continue

        stop_price = signal_low if rule.stop_style == "signal_low" else setup_low
        entry_price = signal_close
        if stop_price >= entry_price:
            continue
        exit_idx, exit_price, exit_reason = _simulate_exit_1m(
            timestamps=timestamps,
            highs=highs,
            lows=lows,
            closes=closes,
            entry_idx=idx,
            entry_price=entry_price,
            stop_price=stop_price,
            rr_target=rule.rr_target,
            max_hold_minutes=120,
            fast_fail_minutes=12,
            fast_fail_r=0.30,
        )
        return {
            "signal_bar_timestamp_ms": int(timestamps[idx]),
            "entry_timestamp_ms": int(timestamps[idx] + ONE_MINUTE_MS),
            "exit_timestamp_ms": int(timestamps[exit_idx] + ONE_MINUTE_MS),
            "entry_price": float(entry_price),
            "stop_price": float(stop_price),
            "initial_risk_pct": float((entry_price - stop_price) / entry_price),
            "exit_price": float(exit_price),
            "exit_reason": exit_reason,
            "exit_return_pct": _net_long_return(entry_price, exit_price),
            "entry_detail": "reclaim_break",
            "entry_delay_minutes": int(offset + 1),
            "launch_return_pct_online": float(event_state.launch_return_pct),
            "launch_close_pos_online": float(event_state.launch_close_pos),
            "launch_volume_ratio_online": float(event_state.launch_volume_ratio),
            "signal_body_frac_launch": float(signal_body_frac_launch),
            "signal_close_pos_online": float(signal_close_pos),
            "signal_volume_ratio_online": float(signal_volume_ratio),
            "pullback_frac_before_entry": float(reset_frac),
            "entry_extension_frac_online": float(entry_extension_frac),
        }
    return None


def _rule_family_rank(family: str) -> int:
    return {"launch_close": 0, "follow_break": 1, "reclaim_break": 2}.get(str(family), 9)


def _build_events(scope: pd.DataFrame, rules: tuple[LaunchCloseRule | FollowBreakRule | ReclaimBreakRule, ...]) -> pd.DataFrame:
    cache: dict[tuple[str, str], pd.DataFrame] = {}
    records: list[dict[str, object]] = []
    grouped = scope.groupby(["cache_scope", "symbol"], sort=True)
    total_groups = grouped.ngroups
    for group_index, ((cache_scope, symbol), scoped) in enumerate(grouped, start=1):
        candles_1m = _load_m1(str(cache_scope), str(symbol), cache)
        if candles_1m.empty:
            continue
        timestamps = pd.to_numeric(candles_1m["timestamp"], errors="coerce").astype("int64").tolist()
        timestamp_to_idx = {int(timestamp): idx for idx, timestamp in enumerate(timestamps)}
        opens = pd.to_numeric(candles_1m["open"], errors="coerce").astype(float).tolist()
        highs = pd.to_numeric(candles_1m["high"], errors="coerce").astype(float).tolist()
        lows = pd.to_numeric(candles_1m["low"], errors="coerce").astype(float).tolist()
        closes = pd.to_numeric(candles_1m["close"], errors="coerce").astype(float).tolist()
        volumes = pd.to_numeric(candles_1m["volume"], errors="coerce").astype(float).tolist()
        if group_index == 1 or group_index % 25 == 0 or group_index == total_groups:
            print(
                f"xx00-wicky-online: progress={group_index}/{total_groups} "
                f"cache={cache_scope} symbol={symbol} events={len(scoped)}"
            )

        for _, row in scoped.iterrows():
            event_state = _prepare_event_state(
                event_row=row,
                timestamp_to_idx=timestamp_to_idx,
                opens=opens,
                highs=highs,
                lows=lows,
                closes=closes,
                volumes=volumes,
            )
            if event_state is None:
                continue
            base = {
                "cohort_id": str(row["cohort_id"]),
                "cohort_title": str(row["cohort_title"]),
                "dataset": str(row["dataset"]),
                "cache_scope": str(row["cache_scope"]),
                "symbol": str(row["symbol"]),
                "timestamp_ms": int(row["timestamp_ms"]),
                "month_utc": str(row["month_utc"]),
                "date_utc": str(row["date_utc"]),
                "context_archetype": str(row["context_archetype"]),
                "pre_accumulation_type": str(row["pre_accumulation_type"]),
                "trigger_return_pct": _safe_float(row.get("trigger_return_pct")),
                "volume_mult": _safe_float(row.get("volume_mult")),
                "pre_base_range_pct_60m": float(event_state.pre_base_range_pct_60m),
                "pre_base_abs_drift_pct_60m": float(event_state.pre_abs_drift_pct_60m),
            }

            for rule in rules:
                if isinstance(rule, LaunchCloseRule):
                    simulated = _simulate_launch_close_rule(
                        event_state=event_state,
                        timestamps=timestamps,
                        highs=highs,
                        lows=lows,
                        closes=closes,
                        rule=rule,
                    )
                elif isinstance(rule, FollowBreakRule):
                    simulated = _simulate_follow_break_rule(
                        event_state=event_state,
                        timestamps=timestamps,
                        opens=opens,
                        highs=highs,
                        lows=lows,
                        closes=closes,
                        volumes=volumes,
                        rule=rule,
                    )
                else:
                    simulated = _simulate_reclaim_break_rule(
                        event_state=event_state,
                        timestamps=timestamps,
                        opens=opens,
                        highs=highs,
                        lows=lows,
                        closes=closes,
                        volumes=volumes,
                        rule=rule,
                    )
                if simulated is None:
                    continue
                records.append({**base, **simulated, "rule_id": rule.rule_id, "family": rule.family})

    events = pd.DataFrame(records)
    if events.empty:
        return events
    for column in ("timestamp_ms", "signal_bar_timestamp_ms", "entry_timestamp_ms", "exit_timestamp_ms"):
        events[column.replace("_ms", "_utc")] = pd.to_datetime(
            pd.to_numeric(events[column], errors="coerce"),
            unit="ms",
            utc=True,
            errors="coerce",
        )
    if "entry_timestamp_utc" in events.columns:
        events["entry_utc"] = events["entry_timestamp_utc"]
    if "exit_timestamp_utc" in events.columns:
        events["exit_utc"] = events["exit_timestamp_utc"]
    if "signal_bar_timestamp_utc" in events.columns:
        events["signal_utc"] = events["signal_bar_timestamp_utc"]
    return events.sort_values(["cohort_id", "family", "rule_id", "dataset", "timestamp_ms", "symbol"]).reset_index(drop=True)


def _coverage_summary(scope: pd.DataFrame) -> pd.DataFrame:
    if scope.empty:
        return pd.DataFrame()
    grouped = (
        scope.groupby(["cohort_id", "cohort_title", "dataset"], sort=True)
        .agg(
            analog_cases=("symbol", "size"),
            symbols=("symbol", pd.Series.nunique),
            min_timestamp_utc=("timestamp_utc", "min"),
            max_timestamp_utc=("timestamp_utc", "max"),
        )
        .reset_index()
    )
    return grouped.sort_values(["cohort_id", "dataset"]).reset_index(drop=True)


def _summary_row(
    *,
    scope_slice: pd.DataFrame,
    event_slice: pd.DataFrame,
    calendar_months: list[str],
    prefix: str,
) -> dict[str, object]:
    summary = _summarize_events(event_slice, calendar_months=calendar_months) or {}
    equity, _ = _simulate_equity_risk_metrics(event_slice, calendar_months=calendar_months, risk_fraction=0.05)
    return {
        f"{prefix}_cases": int(len(scope_slice)),
        f"{prefix}_trades": int(summary.get("trades_count", 0)),
        f"{prefix}_trade_rate": float(summary.get("trades_count", 0) / len(scope_slice)) if len(scope_slice) > 0 else 0.0,
        f"{prefix}_trades_per_year": float(summary.get("trades_per_year", 0.0)),
        f"{prefix}_mean_return_pct": float(summary.get("mean_return_pct", 0.0)),
        f"{prefix}_median_return_pct": float(summary.get("median_return_pct", 0.0)),
        f"{prefix}_win_rate": float(summary.get("win_rate", 0.0)),
        f"{prefix}_annualized_unit_pnl_pct": float(summary.get("annualized_unit_pnl_pct", 0.0)),
        f"{prefix}_profit_factor": summary.get("profit_factor"),
        f"{prefix}_max_drawdown_pct": float(summary.get("max_drawdown_pct", 0.0) or 0.0),
        f"{prefix}_mean_entry_delay_minutes": float(pd.to_numeric(event_slice.get("entry_delay_minutes"), errors="coerce").mean())
        if not event_slice.empty
        else None,
        f"{prefix}_equity_total_return_pct_5": float(equity.get("equity_total_return_pct", 0.0)),
        f"{prefix}_equity_annualized_return_pct_5": float(equity.get("equity_annualized_return_pct", 0.0)),
        f"{prefix}_equity_max_drawdown_pct_5": float(equity.get("equity_max_drawdown_pct", 0.0)),
        f"{prefix}_equity_positive_months_count_5": int(equity.get("equity_positive_months_count", 0)),
        f"{prefix}_equity_stable_positive_months_count_5": int(equity.get("equity_stable_positive_months_count", 0)),
    }


def _rule_summary(scope: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    if scope.empty:
        return pd.DataFrame()
    coverage_counts = (
        scope.groupby(["cohort_id", "dataset"], sort=True).size().unstack(fill_value=0).rename(columns={"current": "coverage_current", "old": "coverage_old"})
    )
    rows: list[dict[str, object]] = []
    for cohort_id, cohort_scope in scope.groupby("cohort_id", sort=True):
        cohort_scope_current = cohort_scope[cohort_scope["dataset"].astype(str) == "current"].copy()
        cohort_scope_old = cohort_scope[cohort_scope["dataset"].astype(str) == "old"].copy()
        calendar_months_current = _calendar_months_from_frame(cohort_scope_current)
        calendar_months_old = _calendar_months_from_frame(cohort_scope_old)
        calendar_months_combined = _calendar_months_from_frame(cohort_scope)
        cohort_events = events[events["cohort_id"].astype(str) == str(cohort_id)].copy() if not events.empty else pd.DataFrame()
        if cohort_events.empty:
            continue
        for (_, family, rule_id), scoped_events in cohort_events.groupby(["cohort_id", "family", "rule_id"], sort=True):
            current_events = scoped_events[scoped_events["dataset"].astype(str) == "current"].copy()
            old_events = scoped_events[scoped_events["dataset"].astype(str) == "old"].copy()
            combined_events = scoped_events.copy()
            row = {
                "cohort_id": str(cohort_id),
                "cohort_title": str(cohort_scope["cohort_title"].iloc[0]),
                "family": str(family),
                "rule_id": str(rule_id),
                "coverage_current": int(coverage_counts.loc[cohort_id, "coverage_current"]) if cohort_id in coverage_counts.index and "coverage_current" in coverage_counts.columns else 0,
                "coverage_old": int(coverage_counts.loc[cohort_id, "coverage_old"]) if cohort_id in coverage_counts.index and "coverage_old" in coverage_counts.columns else 0,
            }
            row.update(_summary_row(scope_slice=cohort_scope_current, event_slice=current_events, calendar_months=calendar_months_current, prefix="current"))
            row.update(_summary_row(scope_slice=cohort_scope_old, event_slice=old_events, calendar_months=calendar_months_old, prefix="old"))
            row.update(_summary_row(scope_slice=cohort_scope, event_slice=combined_events, calendar_months=calendar_months_combined, prefix="combined"))
            super_rows = combined_events[
                (combined_events["symbol"].astype(str) == SUPER_SYMBOL)
                & (pd.to_numeric(combined_events["timestamp_ms"], errors="coerce") == SUPER_TIMESTAMP_MS)
            ].copy()
            sonic_rows = combined_events[
                (combined_events["symbol"].astype(str) == SONIC_SYMBOL)
                & (pd.to_numeric(combined_events["timestamp_ms"], errors="coerce") == SONIC_TIMESTAMP_MS)
            ].copy()
            row["super_triggered"] = bool(not super_rows.empty)
            row["super_entry_utc"] = str(super_rows["entry_utc"].min()) if not super_rows.empty else None
            row["sonic_triggered"] = bool(not sonic_rows.empty)
            row["sonic_entry_utc"] = str(sonic_rows["entry_utc"].min()) if not sonic_rows.empty else None
            row["family_rank"] = _rule_family_rank(str(family))
            row["robust_score"] = (
                float(row["current_equity_total_return_pct_5"])
                + (0.50 * float(row["old_equity_total_return_pct_5"]))
                + float(row["current_mean_return_pct"])
                + (0.50 * float(row["old_mean_return_pct"]))
                - (0.75 * float(row["current_equity_max_drawdown_pct_5"]))
                - (0.35 * float(row["old_equity_max_drawdown_pct_5"]))
                + (0.05 if bool(row["super_triggered"]) else 0.0)
                - (0.01 * int(row["family_rank"]))
            )
            rows.append(row)
    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary
    return summary.sort_values(
        [
            "cohort_id",
            "robust_score",
            "current_equity_total_return_pct_5",
            "current_mean_return_pct",
            "combined_trades",
            "family_rank",
        ],
        ascending=[True, False, False, False, False, True],
    ).reset_index(drop=True)


def _select_rules(rule_summary: pd.DataFrame) -> pd.DataFrame:
    if rule_summary.empty:
        return rule_summary
    selected_rows: list[pd.Series] = []
    for cohort_id, scoped in rule_summary.groupby("cohort_id", sort=True):
        ready = scoped[
            (pd.to_numeric(scoped["current_trades"], errors="coerce") >= 6)
            & (pd.to_numeric(scoped["old_trades"], errors="coerce") >= 2)
            & (pd.to_numeric(scoped["current_mean_return_pct"], errors="coerce") > 0.0)
            & (pd.to_numeric(scoped["current_equity_total_return_pct_5"], errors="coerce") > 0.0)
            & (pd.to_numeric(scoped["old_mean_return_pct"], errors="coerce") >= 0.0)
        ].copy()
        if not ready.empty:
            best = ready.iloc[0].copy()
            best["selection_type"] = "best_ready"
            selected_rows.append(best)

        fastest = scoped[
            (pd.to_numeric(scoped["current_trades"], errors="coerce") >= 4)
            & (pd.to_numeric(scoped["current_mean_return_pct"], errors="coerce") > 0.0)
            & (pd.to_numeric(scoped["current_equity_total_return_pct_5"], errors="coerce") > 0.0)
        ].copy()
        if not fastest.empty:
            fastest = fastest.sort_values(
                [
                    "family_rank",
                    "current_mean_entry_delay_minutes",
                    "current_equity_total_return_pct_5",
                    "current_mean_return_pct",
                    "robust_score",
                ],
                ascending=[True, True, False, False, False],
            )
            row = fastest.iloc[0].copy()
            row["selection_type"] = "fastest_viable"
            if not any(
                str(existing["cohort_id"]) == str(cohort_id)
                and str(existing["rule_id"]) == str(row["rule_id"])
                and str(existing["selection_type"]) == str(row["selection_type"])
                for existing in selected_rows
            ):
                selected_rows.append(row)
            continue

        fallback = scoped.iloc[0].copy()
        fallback["selection_type"] = "fallback_top"
        selected_rows.append(fallback)
    selected = pd.DataFrame(selected_rows)
    if selected.empty:
        return selected
    return selected.sort_values(["cohort_id", "selection_type", "family_rank", "robust_score"], ascending=[True, True, True, False]).reset_index(drop=True)


def _selected_outputs(events: pd.DataFrame, selected: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if events.empty or selected.empty:
        return pd.DataFrame(), pd.DataFrame()
    chosen = events.merge(selected[["cohort_id", "rule_id", "selection_type"]], on=["cohort_id", "rule_id"], how="inner").copy()
    chosen_current = chosen[chosen["dataset"].astype(str) == "current"].copy()
    if chosen_current.empty:
        return chosen, pd.DataFrame()
    monthly_frames: list[pd.DataFrame] = []
    for (cohort_id, selection_type), scoped in chosen_current.groupby(["cohort_id", "selection_type"], sort=True):
        monthly = _build_monthly_returns_frame(scoped)
        monthly.insert(0, "selection_type", selection_type)
        monthly.insert(0, "cohort_id", cohort_id)
        monthly_frames.append(monthly)
    monthly = pd.concat(monthly_frames, ignore_index=True) if monthly_frames else pd.DataFrame()
    return chosen, monthly


def _anchor_hits(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    anchor_rows = events[
        ((events["symbol"].astype(str) == SUPER_SYMBOL) & (pd.to_numeric(events["timestamp_ms"], errors="coerce") == SUPER_TIMESTAMP_MS))
        | ((events["symbol"].astype(str) == SONIC_SYMBOL) & (pd.to_numeric(events["timestamp_ms"], errors="coerce") == SONIC_TIMESTAMP_MS))
    ].copy()
    if anchor_rows.empty:
        return anchor_rows
    anchor_rows["anchor_case"] = anchor_rows.apply(
        lambda row: "SUPER_2026_03_26_0000UTC" if str(row["symbol"]) == SUPER_SYMBOL else "SONIC_2025_07_12_0000UTC",
        axis=1,
    )
    return anchor_rows.sort_values(["anchor_case", "cohort_id", "entry_delay_minutes", "rule_id"]).reset_index(drop=True)


def _build_report(coverage: pd.DataFrame, rule_summary: pd.DataFrame, selected: pd.DataFrame, anchor_hits: pd.DataFrame) -> str:
    lines = [
        "# XX:00 Wicky Launch Online Research",
        "",
        "Цель:",
        "- найти ранний long-вход в пампы, стартующие ровно в `00` минут, без знания будущего;",
        "- проверить, можно ли входить в первые `1-5m` после старта;",
        "- посчитать статистику по аналогичным кейсам на последнем году и отдельно посмотреть старую выборку.",
        "",
        "Что считается честным online-входом:",
        "- в правилах используются только pre-event признаки и закрытые `1m` к моменту сигнала;",
        "- выходы считаются уже после входа стандартным risk/reward-менеджментом;",
        "- сами когорты аналогичных кейсов задаются по форме top-of-hour `5m` аномалии.",
        "",
        "Датировка anchor-кейса:",
        "- скрин из локального кэша совпал с `SUPER/USDT:USDT`, старт `2026-03-26 00:00:00 UTC`;",
        "- если график был в `UTC+3`, это отображается как `2026-03-26 03:00`.",
        "",
        "## Покрытие когорт",
        _frame_to_markdown(
            coverage,
            columns=["cohort_id", "dataset", "analog_cases", "symbols", "min_timestamp_utc", "max_timestamp_utc"],
        )
        if not coverage.empty
        else "_Нет данных._",
        "",
    ]
    if not selected.empty:
        lines.extend(
            [
                "## Выбранные правила",
                _frame_to_markdown(
                    selected,
                    columns=[
                        "cohort_id",
                        "selection_type",
                        "family",
                        "rule_id",
                        "current_cases",
                        "current_trades",
                        "current_trade_rate",
                        "current_mean_entry_delay_minutes",
                        "current_win_rate",
                        "current_mean_return_pct",
                        "current_annualized_unit_pnl_pct",
                        "current_equity_total_return_pct_5",
                        "current_equity_max_drawdown_pct_5",
                        "old_trades",
                        "old_mean_return_pct",
                        "super_triggered",
                        "sonic_triggered",
                    ],
                ),
                "",
                "Статусы:",
                "- `best_ready`: лучший устойчивый кандидат с приемлемой текущей и старой выборкой.",
                "- `fastest_viable`: самый ранний рабочий вход среди положительных current-правил.",
                "- `fallback_top`: лучший по скору, когда устойчивости пока не хватает.",
                "",
            ]
        )
    if not rule_summary.empty:
        lines.extend(
            [
                "## Топ правил",
                _frame_to_markdown(
                    rule_summary.head(20),
                    columns=[
                        "cohort_id",
                        "family",
                        "rule_id",
                        "current_trades",
                        "old_trades",
                        "current_mean_entry_delay_minutes",
                        "current_win_rate",
                        "current_mean_return_pct",
                        "current_annualized_unit_pnl_pct",
                        "current_equity_total_return_pct_5",
                        "current_equity_max_drawdown_pct_5",
                        "old_mean_return_pct",
                        "old_equity_total_return_pct_5",
                        "robust_score",
                        "super_triggered",
                        "sonic_triggered",
                    ],
                ),
                "",
            ]
        )
    if not anchor_hits.empty:
        lines.extend(
            [
                "## Anchor Hits",
                _frame_to_markdown(
                    anchor_hits,
                    columns=[
                        "anchor_case",
                        "cohort_id",
                        "family",
                        "rule_id",
                        "entry_delay_minutes",
                        "entry_utc",
                        "entry_price",
                        "stop_price",
                        "exit_reason",
                        "exit_return_pct",
                    ],
                    limit=20,
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## Reading Guide",
            "- `current` = последний год относительно максимальной даты базы.",
            "- `old` = предыдущая историческая выборка из старого кэша.",
            "- `current_mean_entry_delay_minutes` показывает, насколько рано правило реально входит после старта `XX:00`.",
            "- `current_equity_total_return_pct_5` и `current_equity_max_drawdown_pct_5` считаются через модель риска `5%` на сделку.",
        ]
    )
    return "\n".join(lines)


def run() -> dict[str, Path]:
    print("xx00-wicky-online: start")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    base = _base_scope(_load_feature_db())
    scopes = [_cohort_scope(base, spec) for spec in _cohort_specs()]
    scope = pd.concat(scopes, ignore_index=True) if scopes else pd.DataFrame()
    print(f"xx00-wicky-online: scoped_events={len(scope)}")

    rules: tuple[LaunchCloseRule | FollowBreakRule | ReclaimBreakRule, ...] = (
        *_launch_close_rules(),
        *_follow_break_rules(),
        *_reclaim_break_rules(),
    )
    print(f"xx00-wicky-online: rules={len(rules)}")
    events = _build_events(scope, rules)
    print(f"xx00-wicky-online: built_events={len(events)}")

    coverage = _coverage_summary(scope)
    rule_summary = _rule_summary(scope, events)
    selected = _select_rules(rule_summary)
    selected_events, selected_monthly = _selected_outputs(events, selected)
    anchors = _anchor_hits(events)
    report = _build_report(coverage, rule_summary, selected, anchors)

    paths = {
        "coverage": OUTPUT_DIR / "coverage.csv",
        "events": OUTPUT_DIR / "events.csv",
        "rule_summary": OUTPUT_DIR / "rule_summary.csv",
        "selected": OUTPUT_DIR / "selected_rules.csv",
        "selected_events": OUTPUT_DIR / "selected_events.csv",
        "selected_monthly": OUTPUT_DIR / "selected_monthly.csv",
        "anchor_hits": OUTPUT_DIR / "anchor_hits.csv",
        "report": OUTPUT_DIR / "report.md",
    }
    coverage.to_csv(paths["coverage"], index=False)
    events.to_csv(paths["events"], index=False)
    rule_summary.to_csv(paths["rule_summary"], index=False)
    selected.to_csv(paths["selected"], index=False)
    selected_events.to_csv(paths["selected_events"], index=False)
    selected_monthly.to_csv(paths["selected_monthly"], index=False)
    anchors.to_csv(paths["anchor_hits"], index=False)
    paths["report"].write_text(report, encoding="utf-8")
    print("xx00-wicky-online: done")
    return paths


if __name__ == "__main__":
    output_paths = run()
    for key, value in output_paths.items():
        print(f"{key}: {value}")
