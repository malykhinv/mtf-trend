from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import median

import pandas as pd

from domain.enums.timeframe import Timeframe
from strategy.hourly_asia_pump.static_combo import _build_monthly_returns_frame, _frame_to_markdown, _summarize_events
from strategy.hourly_asia_pump.unified_edge import _simulate_equity_risk_metrics
from strategy.hourly_asia_pump.xx00_pump_nature_research import _base_scope as _nature_base_scope
from strategy.hourly_asia_pump.xx00_pump_nature_research import _build_feature_frame, _label_scope
from vectorbt_runner.data_preparer import DataPreparer

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "xx00_regime_long_short_research"
NATURE_FEATURE_FRAME_PATH = (
    REPO_ROOT / ".output" / "results_prev_year_5m" / "xx00_pump_nature_research" / "feature_frame.csv"
)
CURRENT_CACHE_DIR = REPO_ROOT / ".output" / "cache"
PREV_CACHE_DIR = REPO_ROOT / ".output" / "cache_prev_year_5m"
BASE_INPUT_PATH = (
    REPO_ROOT
    / ".output"
    / "results_prev_year_5m"
    / "anomaly_category_lab"
    / "anomaly_feature_database_with_accumulation.csv"
)
ONE_MINUTE_MS = 60_000
FEE_RATE = 0.0004


@dataclass(frozen=True, slots=True)
class CohortSpec:
    cohort_id: str
    side: str
    title: str


@dataclass(frozen=True, slots=True)
class LongLaunchRule:
    rule_id: str
    family: str
    min_m0_return_pct: float
    min_m0_close_pos: float
    min_m0_volume_ratio: float
    require_break_prev240: bool
    rr_target: float


@dataclass(frozen=True, slots=True)
class LongFollowRule:
    rule_id: str
    family: str
    max_signal_offset: int
    min_signal_body_frac_m0range: float
    min_signal_close_pos: float
    min_signal_volume_ratio: float
    max_pullback_frac: float
    require_break_prev240: bool
    stop_style: str
    rr_target: float


@dataclass(frozen=True, slots=True)
class LongLatePushRule:
    rule_id: str
    family: str
    signal_offset: int
    min_last2_volume_share: float
    min_close_vs_prev240_pct: float
    min_signal_body_frac_m0range: float
    stop_style: str
    rr_target: float


@dataclass(frozen=True, slots=True)
class ShortFailRule:
    rule_id: str
    family: str
    max_signal_offset: int
    peak_within_minutes: int
    breakdown_level: str
    min_red_body_frac_m0range: float
    max_signal_close_pos: float
    min_signal_volume_ratio: float
    stop_style: str
    rr_target: float


@dataclass(frozen=True, slots=True)
class ShortFadeRule:
    rule_id: str
    family: str
    signal_offset: int
    early_peak_max: int
    require_close_below_prev60: bool
    max_event_close_pos: float
    max_last2_volume_share: float
    rr_target: float


@dataclass(frozen=True, slots=True)
class EventState:
    start_idx: int
    prev60_high: float
    prev240_high: float
    prelaunch_volume_median_30: float
    m0_open: float
    m0_high: float
    m0_low: float
    m0_close: float
    m0_range: float
    m0_return_pct: float
    m0_close_pos: float
    m0_body_frac_range: float
    m0_volume_ratio: float


def _cohort_specs() -> tuple[CohortSpec, ...]:
    return (
        CohortSpec("long_union", "long", "Long: overheated/high-range or drift-hot, above EMA200"),
        CohortSpec("long_hot_range", "long", "Long: overheated + above EMA200 + high pre-range"),
        CohortSpec("short_warm_below", "short", "Short: warm + below EMA200"),
        CohortSpec("short_warm_drift_below", "short", "Short: warm + drift_warm + below EMA200"),
    )


def _long_launch_rules() -> tuple[LongLaunchRule, ...]:
    rules: list[LongLaunchRule] = []
    for min_m0_return_pct in (0.01, 0.015):
        for min_m0_close_pos in (0.65, 0.80):
            for min_m0_volume_ratio in (4.0, 8.0):
                for require_break_prev240 in (False, True):
                    for rr_target in (1.5, 2.0):
                        rule_id = (
                            f"long_launch_r{int(min_m0_return_pct*1000):03d}"
                            f"_c{int(min_m0_close_pos*100):02d}"
                            f"_v{int(min_m0_volume_ratio):02d}"
                            f"_p{int(require_break_prev240)}"
                            f"_rr{int(rr_target*10):02d}"
                        )
                        rules.append(
                            LongLaunchRule(
                                rule_id=rule_id,
                                family="long_launch",
                                min_m0_return_pct=min_m0_return_pct,
                                min_m0_close_pos=min_m0_close_pos,
                                min_m0_volume_ratio=min_m0_volume_ratio,
                                require_break_prev240=require_break_prev240,
                                rr_target=rr_target,
                            )
                        )
    return tuple(rules)


def _long_follow_rules() -> tuple[LongFollowRule, ...]:
    rules: list[LongFollowRule] = []
    for max_signal_offset in (1, 2):
        for min_signal_body_frac_m0range in (0.10, 0.20):
            for min_signal_close_pos in (0.65, 0.80):
                for min_signal_volume_ratio in (1.5, 2.5):
                    for max_pullback_frac in (0.25, 0.50):
                        for require_break_prev240 in (False, True):
                            for stop_style in ("signal_low", "base_low"):
                                for rr_target in (1.5, 2.0):
                                    rule_id = (
                                        f"long_follow_w{max_signal_offset}"
                                        f"_b{int(min_signal_body_frac_m0range*100):02d}"
                                        f"_c{int(min_signal_close_pos*100):02d}"
                                        f"_v{int(min_signal_volume_ratio*10):03d}"
                                        f"_p{int(max_pullback_frac*100):02d}"
                                        f"_h{int(require_break_prev240)}"
                                        f"_{stop_style}"
                                        f"_rr{int(rr_target*10):02d}"
                                    )
                                    rules.append(
                                        LongFollowRule(
                                            rule_id=rule_id,
                                            family="long_follow",
                                            max_signal_offset=max_signal_offset,
                                            min_signal_body_frac_m0range=min_signal_body_frac_m0range,
                                            min_signal_close_pos=min_signal_close_pos,
                                            min_signal_volume_ratio=min_signal_volume_ratio,
                                            max_pullback_frac=max_pullback_frac,
                                            require_break_prev240=require_break_prev240,
                                            stop_style=stop_style,
                                            rr_target=rr_target,
                                        )
                                    )
    return tuple(rules)


def _long_late_push_rules() -> tuple[LongLatePushRule, ...]:
    rules: list[LongLatePushRule] = []
    for signal_offset in (3, 4):
        for min_last2_volume_share in (0.35, 0.50):
            for min_close_vs_prev240_pct in (0.0, 0.01):
                for min_signal_body_frac_m0range in (0.10, 0.20):
                    for stop_style in ("signal_low", "recent_low"):
                        for rr_target in (1.5, 2.0):
                            rule_id = (
                                f"long_late_o{signal_offset}"
                                f"_v{int(min_last2_volume_share*100):02d}"
                                f"_p{int(min_close_vs_prev240_pct*1000):03d}"
                                f"_b{int(min_signal_body_frac_m0range*100):02d}"
                                f"_{stop_style}"
                                f"_rr{int(rr_target*10):02d}"
                            )
                            rules.append(
                                LongLatePushRule(
                                    rule_id=rule_id,
                                    family="long_late_push",
                                    signal_offset=signal_offset,
                                    min_last2_volume_share=min_last2_volume_share,
                                    min_close_vs_prev240_pct=min_close_vs_prev240_pct,
                                    min_signal_body_frac_m0range=min_signal_body_frac_m0range,
                                    stop_style=stop_style,
                                    rr_target=rr_target,
                                )
                            )
    return tuple(rules)


def _short_fail_rules() -> tuple[ShortFailRule, ...]:
    rules: list[ShortFailRule] = []
    for max_signal_offset in (2, 3):
        for peak_within_minutes in (1, 2):
            for breakdown_level in ("m0_open", "m0_mid", "peak_low"):
                for min_red_body_frac_m0range in (0.10, 0.20):
                    for max_signal_close_pos in (0.35, 0.50):
                        for min_signal_volume_ratio in (1.0, 1.5):
                            for stop_style in ("peak_high",):
                                for rr_target in (1.5, 2.0):
                                    rule_id = (
                                        f"short_fail_w{max_signal_offset}"
                                        f"_pk{peak_within_minutes}"
                                        f"_{breakdown_level}"
                                        f"_b{int(min_red_body_frac_m0range*100):02d}"
                                        f"_c{int(max_signal_close_pos*100):02d}"
                                        f"_v{int(min_signal_volume_ratio*100):03d}"
                                        f"_{stop_style}"
                                        f"_rr{int(rr_target*10):02d}"
                                    )
                                    rules.append(
                                        ShortFailRule(
                                            rule_id=rule_id,
                                            family="short_fail",
                                            max_signal_offset=max_signal_offset,
                                            peak_within_minutes=peak_within_minutes,
                                            breakdown_level=breakdown_level,
                                            min_red_body_frac_m0range=min_red_body_frac_m0range,
                                            max_signal_close_pos=max_signal_close_pos,
                                            min_signal_volume_ratio=min_signal_volume_ratio,
                                            stop_style=stop_style,
                                            rr_target=rr_target,
                                        )
                                    )
    return tuple(rules)


def _short_fade_rules() -> tuple[ShortFadeRule, ...]:
    rules: list[ShortFadeRule] = []
    for signal_offset in (3, 4):
        for early_peak_max in (1, 2):
            for require_close_below_prev60 in (False, True):
                for max_event_close_pos in (0.55, 0.65):
                    for max_last2_volume_share in (0.35, 0.50):
                        for rr_target in (1.5, 2.0):
                            rule_id = (
                                f"short_fade_o{signal_offset}"
                                f"_pk{early_peak_max}"
                                f"_h{int(require_close_below_prev60)}"
                                f"_c{int(max_event_close_pos*100):02d}"
                                f"_v{int(max_last2_volume_share*100):02d}"
                                f"_rr{int(rr_target*10):02d}"
                            )
                            rules.append(
                                ShortFadeRule(
                                    rule_id=rule_id,
                                    family="short_fade",
                                    signal_offset=signal_offset,
                                    early_peak_max=early_peak_max,
                                    require_close_below_prev60=require_close_below_prev60,
                                    max_event_close_pos=max_event_close_pos,
                                    max_last2_volume_share=max_last2_volume_share,
                                    rr_target=rr_target,
                                )
                            )
    return tuple(rules)


def _calendar_months_from_frame(frame: pd.DataFrame) -> list[str]:
    if frame.empty or "timestamp_ms" not in frame.columns:
        return []
    timestamps = pd.to_numeric(frame["timestamp_ms"], errors="coerce").dropna()
    if timestamps.empty:
        return []
    start = pd.to_datetime(int(timestamps.min()), unit="ms", utc=True).tz_localize(None).to_period("M")
    end = pd.to_datetime(int(timestamps.max()), unit="ms", utc=True).tz_localize(None).to_period("M")
    return [str(period) for period in pd.period_range(start=start, end=end, freq="M")]


def _load_feature_db() -> pd.DataFrame:
    if not BASE_INPUT_PATH.exists():
        raise FileNotFoundError(f"Anomaly database not found: {BASE_INPUT_PATH}")
    frame = pd.read_csv(BASE_INPUT_PATH, low_memory=False)
    numeric_columns = [
        "timestamp_ms",
        "trigger_open",
        "trigger_high",
        "trigger_low",
        "trigger_close",
        "trigger_volume",
        "trigger_return_pct",
        "trigger_range_pct",
        "range_atr",
        "body_atr",
        "volume_mult",
        "close_to_high_frac",
    ]
    for column in numeric_columns:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_ms"], unit="ms", utc=True, errors="coerce")
    frame["month_utc"] = frame["timestamp_utc"].dt.strftime("%Y-%m")
    frame["date_utc"] = frame["timestamp_utc"].dt.strftime("%Y-%m-%d")
    return frame


def _load_m1(cache_scope: str, symbol: str, cache: dict[tuple[str, str], pd.DataFrame]) -> pd.DataFrame:
    key = (cache_scope, symbol)
    if key in cache:
        return cache[key]
    preparer = DataPreparer(CURRENT_CACHE_DIR if cache_scope == "current" else PREV_CACHE_DIR)
    frame = preparer.load_symbol_data(symbol, Timeframe.M1)
    if frame.empty:
        cache[key] = frame
        return frame
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    cache[key] = frame
    return frame


def _close_pos(high_price: float, low_price: float, close_price: float) -> float:
    bar_range = high_price - low_price
    if bar_range <= 0.0:
        return 0.5
    return float((close_price - low_price) / bar_range)


def _net_long_return(entry_price: float, exit_price: float) -> float:
    if entry_price <= 0.0 or exit_price <= 0.0:
        return 0.0
    return float((exit_price * (1.0 - FEE_RATE) / (entry_price * (1.0 + FEE_RATE))) - 1.0)


def _simulate_exit_1m(
    *,
    timestamps: list[int],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    entry_idx: int,
    entry_price: float,
    stop_price: float,
    rr_target: float,
    max_hold_minutes: int,
    fast_fail_minutes: int,
    fast_fail_r: float,
) -> tuple[int, float, str]:
    risk = entry_price - stop_price
    if risk <= 0.0:
        return entry_idx, entry_price, "invalid"
    target_price = entry_price + (risk * rr_target)
    fast_fail_idx = min(len(closes) - 1, entry_idx + fast_fail_minutes)
    last_idx = min(len(closes) - 1, entry_idx + max_hold_minutes)
    highest_high = entry_price
    for idx in range(entry_idx + 1, last_idx + 1):
        low_price = float(lows[idx])
        high_price = float(highs[idx])
        if low_price <= stop_price:
            return idx, stop_price, "stop"
        highest_high = max(highest_high, high_price)
        if high_price >= target_price:
            return idx, target_price, "tp"
        if idx >= fast_fail_idx and highest_high < (entry_price + risk * fast_fail_r):
            return idx, float(closes[idx]), "fast_fail"
    return last_idx, float(closes[last_idx]), "time_exit"


def _safe_float(value: object, *, default: float = 0.0) -> float:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return default
    return float(numeric)


def _load_nature_feature_frame() -> pd.DataFrame:
    if NATURE_FEATURE_FRAME_PATH.exists():
        frame = pd.read_csv(NATURE_FEATURE_FRAME_PATH, low_memory=False)
    else:
        print("xx00-regime-long-short: feature_frame cache missing, rebuilding from base db")
        frame = _label_scope(_build_feature_frame(_nature_base_scope(_load_feature_db())))
    numeric_columns = [
        "timestamp_ms",
        "trigger_return_pct",
        "volume_mult",
        "pre_base_range_pct_60m",
        "pre_close_vs_ema200_pct",
        "pre_close_vs_ema50_pct",
        "pre_return_15m_pct",
        "pre_return_30m_pct",
        "pre_return_60m_pct",
        "pump_m0_return_pct",
        "pump_m0_close_pos",
        "pump_m0_body_frac_range",
        "pump_m0_volume_ratio_30",
    ]
    for column in numeric_columns:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "timestamp_utc" in frame.columns:
        frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_utc"], utc=True, errors="coerce")
    else:
        frame["timestamp_utc"] = pd.to_datetime(
            pd.to_numeric(frame["timestamp_ms"], errors="coerce"),
            unit="ms",
            utc=True,
            errors="coerce",
        )
    return frame.reset_index(drop=True)


def _cohort_scope(frame: pd.DataFrame, spec: CohortSpec) -> pd.DataFrame:
    above_ema200 = pd.to_numeric(frame.get("pre_close_vs_ema200_pct"), errors="coerce") > 0.0
    high_pre_range = pd.to_numeric(frame.get("pre_base_range_pct_60m"), errors="coerce") >= 0.05
    overheated = frame.get("context_archetype", pd.Series(index=frame.index)).astype(str) == "context_overheated"
    warm = frame.get("context_archetype", pd.Series(index=frame.index)).astype(str) == "context_warm"
    drift_hot = frame.get("context_drift_bucket", pd.Series(index=frame.index)).astype(str) == "drift_hot"
    drift_warm = frame.get("context_drift_bucket", pd.Series(index=frame.index)).astype(str) == "drift_warm"

    if spec.cohort_id == "long_union":
        mask = above_ema200 & ((overheated & high_pre_range) | drift_hot)
    elif spec.cohort_id == "long_hot_range":
        mask = overheated & above_ema200 & high_pre_range
    elif spec.cohort_id == "short_warm_below":
        mask = warm & (~above_ema200)
    elif spec.cohort_id == "short_warm_drift_below":
        mask = warm & drift_warm & (~above_ema200)
    else:
        raise ValueError(f"Unknown cohort: {spec.cohort_id}")

    scoped = frame[mask].copy()
    scoped["cohort_id"] = spec.cohort_id
    scoped["cohort_title"] = spec.title
    scoped["side"] = spec.side
    return scoped.reset_index(drop=True)


def _rule_family_rank(family: str) -> int:
    return {
        "long_launch": 0,
        "long_follow": 1,
        "long_late_push": 2,
        "short_fail": 0,
        "short_fade": 1,
    }.get(family, 9)


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
    if start_idx < 240 or (start_idx + 180) >= len(closes):
        return None

    prelaunch_volumes = [float(value) for value in volumes[start_idx - 30 : start_idx] if float(value) > 0.0]
    if not prelaunch_volumes:
        return None
    prelaunch_volume_median_30 = float(median(prelaunch_volumes))
    if prelaunch_volume_median_30 <= 0.0:
        return None

    m0_open = float(opens[start_idx])
    m0_high = float(highs[start_idx])
    m0_low = float(lows[start_idx])
    m0_close = float(closes[start_idx])
    if m0_open <= 0.0:
        return None

    m0_range = max(1e-12, m0_high - m0_low)
    m0_return_pct = (m0_close / m0_open) - 1.0
    m0_close_pos = _close_pos(m0_high, m0_low, m0_close)
    m0_body_frac_range = max(0.0, m0_close - m0_open) / m0_range
    m0_volume_ratio = float(volumes[start_idx] / prelaunch_volume_median_30)
    prev60_high = max(float(value) for value in highs[start_idx - 60 : start_idx])
    prev240_high = max(float(value) for value in highs[start_idx - 240 : start_idx])

    return EventState(
        start_idx=start_idx,
        prev60_high=prev60_high,
        prev240_high=prev240_high,
        prelaunch_volume_median_30=prelaunch_volume_median_30,
        m0_open=m0_open,
        m0_high=m0_high,
        m0_low=m0_low,
        m0_close=m0_close,
        m0_range=m0_range,
        m0_return_pct=m0_return_pct,
        m0_close_pos=m0_close_pos,
        m0_body_frac_range=m0_body_frac_range,
        m0_volume_ratio=m0_volume_ratio,
    )


def _long_base_ok(event_state: EventState) -> bool:
    return (
        event_state.m0_return_pct > 0.002
        and event_state.m0_close > event_state.m0_open
        and event_state.m0_close_pos >= 0.50
        and event_state.m0_volume_ratio >= 2.0
    )


def _net_short_return(entry_price: float, exit_price: float) -> float:
    if entry_price <= 0.0 or exit_price <= 0.0:
        return 0.0
    return float((entry_price * (1.0 - FEE_RATE) / (exit_price * (1.0 + FEE_RATE))) - 1.0)


def _simulate_short_exit_1m(
    *,
    highs: list[float],
    lows: list[float],
    closes: list[float],
    entry_idx: int,
    entry_price: float,
    stop_price: float,
    rr_target: float,
    max_hold_minutes: int,
    fast_fail_minutes: int,
    fast_fail_r: float,
) -> tuple[int, float, str]:
    risk = stop_price - entry_price
    if risk <= 0.0:
        return entry_idx, entry_price, "invalid"
    target_price = entry_price - (risk * rr_target)
    fast_fail_idx = min(len(closes) - 1, entry_idx + fast_fail_minutes)
    last_idx = min(len(closes) - 1, entry_idx + max_hold_minutes)
    lowest_low = entry_price
    for idx in range(entry_idx + 1, last_idx + 1):
        high_price = float(highs[idx])
        low_price = float(lows[idx])
        if high_price >= stop_price:
            return idx, stop_price, "stop"
        lowest_low = min(lowest_low, low_price)
        if low_price <= target_price:
            return idx, target_price, "tp"
        if idx >= fast_fail_idx and lowest_low > (entry_price - risk * fast_fail_r):
            return idx, float(closes[idx]), "fast_fail"
    return last_idx, float(closes[last_idx]), "time_exit"


def _simulate_long_launch_rule(
    *,
    event_state: EventState,
    timestamps: list[int],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    rule: LongLaunchRule,
) -> dict[str, object] | None:
    if event_state.m0_close <= event_state.prev60_high:
        return None
    if rule.require_break_prev240 and event_state.m0_close <= event_state.prev240_high:
        return None
    if event_state.m0_return_pct < rule.min_m0_return_pct:
        return None
    if event_state.m0_close_pos < rule.min_m0_close_pos:
        return None
    if event_state.m0_volume_ratio < rule.min_m0_volume_ratio:
        return None

    entry_idx = event_state.start_idx
    entry_price = event_state.m0_close
    stop_price = event_state.m0_low
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
        "entry_detail": "m0_close_break",
        "entry_delay_minutes": 1,
        "m0_return_pct_online": float(event_state.m0_return_pct),
        "m0_close_pos_online": float(event_state.m0_close_pos),
        "m0_volume_ratio_online": float(event_state.m0_volume_ratio),
    }


def _simulate_long_follow_rule(
    *,
    event_state: EventState,
    timestamps: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    rule: LongFollowRule,
) -> dict[str, object] | None:
    if not _long_base_ok(event_state):
        return None

    setup_low = min(event_state.m0_low, event_state.m0_open, event_state.m0_close)
    breakout_level = max(event_state.prev60_high, event_state.m0_high)
    for offset in range(1, rule.max_signal_offset + 1):
        idx = event_state.start_idx + offset
        signal_open = float(opens[idx])
        signal_high = float(highs[idx])
        signal_low = float(lows[idx])
        signal_close = float(closes[idx])
        signal_body = signal_close - signal_open
        setup_low = min(setup_low, signal_low)
        if signal_body <= 0.0:
            continue
        if signal_close <= breakout_level:
            continue
        if rule.require_break_prev240 and signal_close <= event_state.prev240_high:
            continue

        signal_body_frac_m0range = signal_body / event_state.m0_range
        signal_close_pos = _close_pos(signal_high, signal_low, signal_close)
        signal_volume_ratio = float(volumes[idx] / event_state.prelaunch_volume_median_30)
        pullback_frac = max(0.0, (event_state.m0_close - setup_low) / event_state.m0_range)
        if signal_body_frac_m0range < rule.min_signal_body_frac_m0range:
            continue
        if signal_close_pos < rule.min_signal_close_pos:
            continue
        if signal_volume_ratio < rule.min_signal_volume_ratio:
            continue
        if pullback_frac > rule.max_pullback_frac:
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
            "m0_return_pct_online": float(event_state.m0_return_pct),
            "m0_close_pos_online": float(event_state.m0_close_pos),
            "m0_volume_ratio_online": float(event_state.m0_volume_ratio),
            "signal_body_frac_m0range": float(signal_body_frac_m0range),
            "signal_close_pos_online": float(signal_close_pos),
            "signal_volume_ratio_online": float(signal_volume_ratio),
            "pullback_frac_before_entry": float(pullback_frac),
        }
    return None


def _simulate_long_late_push_rule(
    *,
    event_state: EventState,
    timestamps: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    rule: LongLatePushRule,
) -> dict[str, object] | None:
    if not _long_base_ok(event_state):
        return None

    idx = event_state.start_idx + rule.signal_offset
    if idx >= len(closes):
        return None
    signal_open = float(opens[idx])
    signal_high = float(highs[idx])
    signal_low = float(lows[idx])
    signal_close = float(closes[idx])
    signal_body = signal_close - signal_open
    if signal_body <= 0.0:
        return None

    prior_high = max(float(value) for value in highs[event_state.start_idx:idx])
    if signal_close <= max(prior_high, event_state.prev60_high):
        return None

    total_volume = float(sum(float(value) for value in volumes[event_state.start_idx : idx + 1]))
    last2_start = max(event_state.start_idx, idx - 1)
    last2_volume = float(sum(float(value) for value in volumes[last2_start : idx + 1]))
    last2_volume_share = (last2_volume / total_volume) if total_volume > 0.0 else 0.0
    close_vs_prev240_pct = ((signal_close / event_state.prev240_high) - 1.0) if event_state.prev240_high > 0.0 else 0.0
    signal_body_frac_m0range = signal_body / event_state.m0_range

    if last2_volume_share < rule.min_last2_volume_share:
        return None
    if close_vs_prev240_pct < rule.min_close_vs_prev240_pct:
        return None
    if signal_body_frac_m0range < rule.min_signal_body_frac_m0range:
        return None

    stop_price = (
        signal_low
        if rule.stop_style == "signal_low"
        else min(float(value) for value in lows[max(event_state.start_idx, idx - 2) : idx + 1])
    )
    entry_price = signal_close
    if stop_price >= entry_price:
        return None

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
        "entry_detail": "late_push",
        "entry_delay_minutes": int(rule.signal_offset + 1),
        "m0_return_pct_online": float(event_state.m0_return_pct),
        "m0_close_pos_online": float(event_state.m0_close_pos),
        "m0_volume_ratio_online": float(event_state.m0_volume_ratio),
        "signal_body_frac_m0range": float(signal_body_frac_m0range),
        "last2_volume_share_online": float(last2_volume_share),
        "close_vs_prev240_pct_online": float(close_vs_prev240_pct),
    }


def _running_peak_state(
    *,
    start_idx: int,
    end_idx: int,
    highs: list[float],
    lows: list[float],
) -> tuple[int, float, float]:
    observed_highs = [float(value) for value in highs[start_idx : end_idx + 1]]
    peak_rel_idx = int(max(range(len(observed_highs)), key=lambda index: observed_highs[index]))
    peak_idx = start_idx + peak_rel_idx
    peak_high = float(highs[peak_idx])
    peak_low = float(lows[peak_idx])
    return peak_rel_idx, peak_high, peak_low


def _short_breakdown_level(event_state: EventState, breakdown_level: str, peak_low: float) -> float:
    if breakdown_level == "m0_open":
        return event_state.m0_open
    if breakdown_level == "m0_mid":
        return event_state.m0_low + (0.5 * event_state.m0_range)
    return peak_low


def _simulate_short_fail_rule(
    *,
    event_state: EventState,
    timestamps: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    rule: ShortFailRule,
) -> dict[str, object] | None:
    for offset in range(1, rule.max_signal_offset + 1):
        idx = event_state.start_idx + offset
        signal_open = float(opens[idx])
        signal_high = float(highs[idx])
        signal_low = float(lows[idx])
        signal_close = float(closes[idx])
        peak_offset, peak_high, peak_low = _running_peak_state(
            start_idx=event_state.start_idx,
            end_idx=idx,
            highs=highs,
            lows=lows,
        )
        if peak_offset > rule.peak_within_minutes:
            continue
        red_body = signal_open - signal_close
        if red_body <= 0.0:
            continue

        breakdown_price = _short_breakdown_level(event_state, rule.breakdown_level, peak_low)
        signal_red_body_frac_m0range = red_body / event_state.m0_range
        signal_close_pos = _close_pos(signal_high, signal_low, signal_close)
        signal_volume_ratio = float(volumes[idx] / event_state.prelaunch_volume_median_30)
        if signal_close >= breakdown_price:
            continue
        if signal_red_body_frac_m0range < rule.min_red_body_frac_m0range:
            continue
        if signal_close_pos > rule.max_signal_close_pos:
            continue
        if signal_volume_ratio < rule.min_signal_volume_ratio:
            continue

        stop_price = peak_high
        entry_price = signal_close
        if stop_price <= entry_price:
            continue

        exit_idx, exit_price, exit_reason = _simulate_short_exit_1m(
            highs=highs,
            lows=lows,
            closes=closes,
            entry_idx=idx,
            entry_price=entry_price,
            stop_price=stop_price,
            rr_target=rule.rr_target,
            max_hold_minutes=120,
            fast_fail_minutes=8,
            fast_fail_r=0.25,
        )
        return {
            "signal_bar_timestamp_ms": int(timestamps[idx]),
            "entry_timestamp_ms": int(timestamps[idx] + ONE_MINUTE_MS),
            "exit_timestamp_ms": int(timestamps[exit_idx] + ONE_MINUTE_MS),
            "entry_price": float(entry_price),
            "stop_price": float(stop_price),
            "initial_risk_pct": float((stop_price - entry_price) / entry_price),
            "exit_price": float(exit_price),
            "exit_reason": exit_reason,
            "exit_return_pct": _net_short_return(entry_price, exit_price),
            "entry_detail": "early_fail_break",
            "entry_delay_minutes": int(offset + 1),
            "m0_return_pct_online": float(event_state.m0_return_pct),
            "m0_close_pos_online": float(event_state.m0_close_pos),
            "m0_volume_ratio_online": float(event_state.m0_volume_ratio),
            "peak_offset_online": int(peak_offset),
            "breakdown_level_online": str(rule.breakdown_level),
            "signal_red_body_frac_m0range": float(signal_red_body_frac_m0range),
            "signal_close_pos_online": float(signal_close_pos),
            "signal_volume_ratio_online": float(signal_volume_ratio),
        }
    return None


def _simulate_short_fade_rule(
    *,
    event_state: EventState,
    timestamps: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    rule: ShortFadeRule,
) -> dict[str, object] | None:
    idx = event_state.start_idx + rule.signal_offset
    if idx >= len(closes):
        return None

    signal_open = float(opens[idx])
    signal_high = float(highs[idx])
    signal_low = float(lows[idx])
    signal_close = float(closes[idx])
    if signal_close >= signal_open:
        return None

    peak_offset, peak_high, _ = _running_peak_state(
        start_idx=event_state.start_idx,
        end_idx=idx,
        highs=highs,
        lows=lows,
    )
    if peak_offset > rule.early_peak_max:
        return None

    observed_high = max(float(value) for value in highs[event_state.start_idx : idx + 1])
    observed_low = min(float(value) for value in lows[event_state.start_idx : idx + 1])
    event_close_pos = _close_pos(observed_high, observed_low, signal_close)
    total_volume = float(sum(float(value) for value in volumes[event_state.start_idx : idx + 1]))
    last2_start = max(event_state.start_idx, idx - 1)
    last2_volume = float(sum(float(value) for value in volumes[last2_start : idx + 1]))
    last2_volume_share = (last2_volume / total_volume) if total_volume > 0.0 else 1.0

    if rule.require_close_below_prev60 and signal_close >= event_state.prev60_high:
        return None
    if signal_close >= event_state.m0_open:
        return None
    if event_close_pos > rule.max_event_close_pos:
        return None
    if last2_volume_share > rule.max_last2_volume_share:
        return None

    stop_price = peak_high
    entry_price = signal_close
    if stop_price <= entry_price:
        return None

    exit_idx, exit_price, exit_reason = _simulate_short_exit_1m(
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
        "initial_risk_pct": float((stop_price - entry_price) / entry_price),
        "exit_price": float(exit_price),
        "exit_reason": exit_reason,
        "exit_return_pct": _net_short_return(entry_price, exit_price),
        "entry_detail": "late_fade",
        "entry_delay_minutes": int(rule.signal_offset + 1),
        "m0_return_pct_online": float(event_state.m0_return_pct),
        "m0_close_pos_online": float(event_state.m0_close_pos),
        "m0_volume_ratio_online": float(event_state.m0_volume_ratio),
        "peak_offset_online": int(peak_offset),
        "event_close_pos_online": float(event_close_pos),
        "last2_volume_share_online": float(last2_volume_share),
        "signal_close_pos_online": float(_close_pos(signal_high, signal_low, signal_close)),
    }


def _build_events(
    scope: pd.DataFrame,
    rules: tuple[LongLaunchRule | LongFollowRule | LongLatePushRule | ShortFailRule | ShortFadeRule, ...],
) -> pd.DataFrame:
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
                f"xx00-regime-long-short: progress={group_index}/{total_groups} "
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
                "side": str(row["side"]),
                "dataset": str(row["dataset"]),
                "cache_scope": str(row["cache_scope"]),
                "symbol": str(row["symbol"]),
                "timestamp_ms": int(row["timestamp_ms"]),
                "timestamp_utc": row.get("timestamp_utc"),
                "month_utc": str(row["month_utc"]),
                "date_utc": str(row["date_utc"]),
                "context_archetype": str(row["context_archetype"]),
                "context_drift_bucket": str(row.get("context_drift_bucket")),
                "continuation_label": str(row.get("continuation_label")),
                "trigger_return_pct": _safe_float(row.get("trigger_return_pct")),
                "volume_mult": _safe_float(row.get("volume_mult")),
                "pre_base_range_pct_60m": _safe_float(row.get("pre_base_range_pct_60m")),
                "pre_close_vs_ema200_pct": _safe_float(row.get("pre_close_vs_ema200_pct")),
            }

            for rule in rules:
                if base["side"] == "long" and isinstance(rule, ShortFailRule | ShortFadeRule):
                    continue
                if base["side"] == "short" and isinstance(rule, LongLaunchRule | LongFollowRule | LongLatePushRule):
                    continue

                if isinstance(rule, LongLaunchRule):
                    simulated = _simulate_long_launch_rule(
                        event_state=event_state,
                        timestamps=timestamps,
                        highs=highs,
                        lows=lows,
                        closes=closes,
                        rule=rule,
                    )
                elif isinstance(rule, LongFollowRule):
                    simulated = _simulate_long_follow_rule(
                        event_state=event_state,
                        timestamps=timestamps,
                        opens=opens,
                        highs=highs,
                        lows=lows,
                        closes=closes,
                        volumes=volumes,
                        rule=rule,
                    )
                elif isinstance(rule, LongLatePushRule):
                    simulated = _simulate_long_late_push_rule(
                        event_state=event_state,
                        timestamps=timestamps,
                        opens=opens,
                        highs=highs,
                        lows=lows,
                        closes=closes,
                        volumes=volumes,
                        rule=rule,
                    )
                elif isinstance(rule, ShortFailRule):
                    simulated = _simulate_short_fail_rule(
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
                    simulated = _simulate_short_fade_rule(
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
    events["entry_utc"] = events["entry_timestamp_utc"]
    events["exit_utc"] = events["exit_timestamp_utc"]
    events["signal_utc"] = events["signal_bar_timestamp_utc"]
    events["event_id"] = (
        events["dataset"].astype(str)
        + "|"
        + events["symbol"].astype(str)
        + "|"
        + pd.to_numeric(events["timestamp_ms"], errors="coerce").astype("int64").astype(str)
    )
    return events.sort_values(["cohort_id", "family", "rule_id", "dataset", "timestamp_ms", "symbol"]).reset_index(drop=True)


def _coverage_summary(scope: pd.DataFrame) -> pd.DataFrame:
    if scope.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    for (cohort_id, dataset), scoped in scope.groupby(["cohort_id", "dataset"], sort=True):
        labels = scoped["continuation_label"].astype(str).value_counts()
        rows.append(
            {
                "cohort_id": str(cohort_id),
                "cohort_title": str(scoped["cohort_title"].iloc[0]),
                "side": str(scoped["side"].iloc[0]),
                "dataset": str(dataset),
                "analog_cases": int(len(scoped)),
                "symbols": int(scoped["symbol"].nunique()),
                "strong_cases": int(labels.get("strong", 0)),
                "dead_cases": int(labels.get("dead", 0)),
                "middle_cases": int(labels.get("middle", 0)),
                "min_timestamp_utc": scoped["timestamp_utc"].min(),
                "max_timestamp_utc": scoped["timestamp_utc"].max(),
            }
        )
    return pd.DataFrame(rows).sort_values(["cohort_id", "dataset"]).reset_index(drop=True)


def _label_mix(event_slice: pd.DataFrame, side: str) -> dict[str, float]:
    if event_slice.empty:
        return {"strong_share": 0.0, "dead_share": 0.0, "alignment_score": 0.0}
    label_counts = event_slice["continuation_label"].astype(str).value_counts(normalize=True)
    strong_share = float(label_counts.get("strong", 0.0))
    dead_share = float(label_counts.get("dead", 0.0))
    alignment_score = (strong_share - dead_share) if side == "long" else (dead_share - strong_share)
    return {
        "strong_share": strong_share,
        "dead_share": dead_share,
        "alignment_score": alignment_score,
    }


def _summary_row(
    *,
    side: str,
    scope_slice: pd.DataFrame,
    event_slice: pd.DataFrame,
    calendar_months: list[str],
    prefix: str,
) -> dict[str, object]:
    summary = _summarize_events(event_slice, calendar_months=calendar_months) or {}
    equity, _ = _simulate_equity_risk_metrics(event_slice, calendar_months=calendar_months, risk_fraction=0.05)
    label_mix = _label_mix(event_slice, side)
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
        f"{prefix}_strong_share": float(label_mix["strong_share"]),
        f"{prefix}_dead_share": float(label_mix["dead_share"]),
        f"{prefix}_alignment_score": float(label_mix["alignment_score"]),
    }


def _rule_summary(scope: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    if scope.empty or events.empty:
        return pd.DataFrame()

    coverage_counts = (
        scope.groupby(["cohort_id", "dataset"], sort=True)
        .size()
        .unstack(fill_value=0)
        .rename(columns={"current": "coverage_current", "old": "coverage_old"})
    )
    rows: list[dict[str, object]] = []
    for cohort_id, cohort_scope in scope.groupby("cohort_id", sort=True):
        side = str(cohort_scope["side"].iloc[0])
        cohort_scope_current = cohort_scope[cohort_scope["dataset"].astype(str) == "current"].copy()
        cohort_scope_old = cohort_scope[cohort_scope["dataset"].astype(str) == "old"].copy()
        calendar_months_current = _calendar_months_from_frame(cohort_scope_current)
        calendar_months_old = _calendar_months_from_frame(cohort_scope_old)
        calendar_months_combined = _calendar_months_from_frame(cohort_scope)
        cohort_events = events[events["cohort_id"].astype(str) == str(cohort_id)].copy()
        for (_, family, rule_id), scoped_events in cohort_events.groupby(["cohort_id", "family", "rule_id"], sort=True):
            current_events = scoped_events[scoped_events["dataset"].astype(str) == "current"].copy()
            old_events = scoped_events[scoped_events["dataset"].astype(str) == "old"].copy()
            combined_events = scoped_events.copy()
            row = {
                "cohort_id": str(cohort_id),
                "cohort_title": str(cohort_scope["cohort_title"].iloc[0]),
                "side": side,
                "family": str(family),
                "rule_id": str(rule_id),
                "coverage_current": int(coverage_counts.loc[cohort_id, "coverage_current"]) if "coverage_current" in coverage_counts.columns else 0,
                "coverage_old": int(coverage_counts.loc[cohort_id, "coverage_old"]) if "coverage_old" in coverage_counts.columns else 0,
                "family_rank": _rule_family_rank(str(family)),
            }
            row.update(_summary_row(side=side, scope_slice=cohort_scope_current, event_slice=current_events, calendar_months=calendar_months_current, prefix="current"))
            row.update(_summary_row(side=side, scope_slice=cohort_scope_old, event_slice=old_events, calendar_months=calendar_months_old, prefix="old"))
            row.update(_summary_row(side=side, scope_slice=cohort_scope, event_slice=combined_events, calendar_months=calendar_months_combined, prefix="combined"))
            row["robust_score"] = (
                float(row["current_equity_total_return_pct_5"])
                + (0.50 * float(row["old_equity_total_return_pct_5"]))
                + float(row["current_mean_return_pct"])
                + (0.50 * float(row["old_mean_return_pct"]))
                + (10.0 * float(row["current_trade_rate"]))
                + (5.0 * float(row["old_trade_rate"]))
                + (5.0 * float(row["current_alignment_score"]))
                + (2.0 * float(row["old_alignment_score"]))
                - (0.75 * float(row["current_equity_max_drawdown_pct_5"]))
                - (0.35 * float(row["old_equity_max_drawdown_pct_5"]))
                - (0.15 * float(row["current_mean_entry_delay_minutes"] or 0.0))
                - (0.05 * float(row["family_rank"]))
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
    used_rule_keys: set[tuple[str, str]] = set()
    covered_cohorts: set[str] = set()
    for cohort_id, scoped in rule_summary.groupby("cohort_id", sort=True):
        side = str(scoped["side"].iloc[0])
        min_current_ready = 6 if side == "long" else 4
        min_old_ready = 2 if side == "long" else 1
        min_current_early = 4 if side == "long" else 3

        ready = scoped[
            (pd.to_numeric(scoped["current_trades"], errors="coerce") >= min_current_ready)
            & (pd.to_numeric(scoped["old_trades"], errors="coerce") >= min_old_ready)
            & (pd.to_numeric(scoped["current_mean_return_pct"], errors="coerce") > 0.0)
            & (pd.to_numeric(scoped["current_equity_total_return_pct_5"], errors="coerce") > 0.0)
            & (pd.to_numeric(scoped["old_mean_return_pct"], errors="coerce") >= 0.0)
            & (pd.to_numeric(scoped["current_alignment_score"], errors="coerce") > 0.0)
        ].copy()
        if not ready.empty:
            best = ready.iloc[0].copy()
            best["selection_type"] = "best_ready"
            selected_rows.append(best)
            used_rule_keys.add((str(best["cohort_id"]), str(best["rule_id"])))
            covered_cohorts.add(str(best["cohort_id"]))

        earliest = scoped[
            (pd.to_numeric(scoped["current_trades"], errors="coerce") >= min_current_early)
            & (pd.to_numeric(scoped["current_mean_return_pct"], errors="coerce") > 0.0)
            & (pd.to_numeric(scoped["current_equity_total_return_pct_5"], errors="coerce") > 0.0)
            & (pd.to_numeric(scoped["current_alignment_score"], errors="coerce") > 0.0)
        ].copy()
        if not earliest.empty:
            earliest = earliest.sort_values(
                [
                    "current_mean_entry_delay_minutes",
                    "robust_score",
                    "current_equity_total_return_pct_5",
                    "current_mean_return_pct",
                    "family_rank",
                ],
                ascending=[True, False, False, False, True],
            )
            row = earliest.iloc[0].copy()
            if (str(row["cohort_id"]), str(row["rule_id"])) not in used_rule_keys:
                row["selection_type"] = "earliest_viable"
                selected_rows.append(row)
                used_rule_keys.add((str(row["cohort_id"]), str(row["rule_id"])))
                covered_cohorts.add(str(row["cohort_id"]))

        if str(cohort_id) not in covered_cohorts:
            fallback = scoped.iloc[0].copy()
            positive_exists = bool(
                (
                    (pd.to_numeric(scoped["current_mean_return_pct"], errors="coerce") > 0.0)
                    & (pd.to_numeric(scoped["current_equity_total_return_pct_5"], errors="coerce") > 0.0)
                ).any()
            )
            fallback["selection_type"] = "fallback_top" if positive_exists else "no_positive_rule"
            selected_rows.append(fallback)
            covered_cohorts.add(str(fallback["cohort_id"]))

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


def _build_report(coverage: pd.DataFrame, rule_summary: pd.DataFrame, selected: pd.DataFrame) -> str:
    lines = [
        "# XX:00 Regime Long/Short Research",
        "",
        "Goal:",
        "- convert the strongest XX:00 pump regimes into early long entries without lookahead;",
        "- convert the weakest XX:00 pump regimes into confirmed short entries without lookahead;",
        "- use only pre-event features and closed 1m bars available by the signal minute;",
        "- measure coverage, trade rate, win rate, pnl, and drawdown on current and old caches.",
        "",
        "Coverage:",
        _frame_to_markdown(
            coverage,
            columns=[
                "cohort_id",
                "dataset",
                "analog_cases",
                "strong_cases",
                "dead_cases",
                "middle_cases",
                "symbols",
            ],
        )
        if not coverage.empty
        else "_No data._",
        "",
    ]
    if not selected.empty:
        lines.extend(
            [
                "Selected rules:",
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
                        "current_equity_total_return_pct_5",
                        "current_equity_max_drawdown_pct_5",
                        "current_alignment_score",
                        "old_trades",
                        "old_mean_return_pct",
                        "old_alignment_score",
                    ],
                ),
                "",
            ]
        )
    if not rule_summary.empty:
        lines.extend(
            [
                "Top rules:",
                _frame_to_markdown(
                    rule_summary.head(24),
                    columns=[
                        "cohort_id",
                        "family",
                        "rule_id",
                        "current_trades",
                        "old_trades",
                        "current_mean_entry_delay_minutes",
                        "current_win_rate",
                        "current_mean_return_pct",
                        "current_equity_total_return_pct_5",
                        "current_equity_max_drawdown_pct_5",
                        "current_alignment_score",
                        "old_mean_return_pct",
                        "old_equity_total_return_pct_5",
                        "old_alignment_score",
                        "robust_score",
                    ],
                ),
                "",
            ]
        )
    lines.extend(
        [
            "Reading guide:",
            "- `alignment_score` rewards rules that fire on strong labels for long and dead labels for short.",
            "- `current_mean_entry_delay_minutes` measures how quickly the rule enters after 00:00.",
            "- `equity_total_return_pct_5` and `equity_max_drawdown_pct_5` use 5% risk per trade.",
            "- `best_ready` means the rule has minimal support in both current and old samples.",
            "- `earliest_viable` means the rule is the earliest still-positive candidate in current data.",
            "- `no_positive_rule` means the searched online rule family did not produce a positive current candidate.",
        ]
    )
    return "\n".join(lines)


def run() -> dict[str, Path]:
    print("xx00-regime-long-short: start")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    feature_frame = _load_nature_feature_frame()
    scopes = [_cohort_scope(feature_frame, spec) for spec in _cohort_specs()]
    scope = pd.concat(scopes, ignore_index=True) if scopes else pd.DataFrame()
    print(f"xx00-regime-long-short: scoped_events={len(scope)}")

    rules: tuple[LongLaunchRule | LongFollowRule | LongLatePushRule | ShortFailRule | ShortFadeRule, ...] = (
        *_long_launch_rules(),
        *_long_follow_rules(),
        *_long_late_push_rules(),
        *_short_fail_rules(),
        *_short_fade_rules(),
    )
    print(f"xx00-regime-long-short: rules={len(rules)}")
    events = _build_events(scope, rules)
    print(f"xx00-regime-long-short: built_events={len(events)}")

    paths = {
        "coverage": OUTPUT_DIR / "coverage.csv",
        "events": OUTPUT_DIR / "events.csv",
        "rule_summary": OUTPUT_DIR / "rule_summary.csv",
        "selected": OUTPUT_DIR / "selected_rules.csv",
        "selected_events": OUTPUT_DIR / "selected_events.csv",
        "selected_monthly": OUTPUT_DIR / "selected_monthly.csv",
        "report": OUTPUT_DIR / "report.md",
    }
    coverage = _coverage_summary(scope)
    rule_summary = _rule_summary(scope, events)
    selected = _select_rules(rule_summary)
    coverage.to_csv(paths["coverage"], index=False)
    events.to_csv(paths["events"], index=False)
    rule_summary.to_csv(paths["rule_summary"], index=False)
    selected.to_csv(paths["selected"], index=False)

    try:
        selected_events, selected_monthly = _selected_outputs(events, selected)
        report = _build_report(coverage, rule_summary, selected)
        selected_events.to_csv(paths["selected_events"], index=False)
        selected_monthly.to_csv(paths["selected_monthly"], index=False)
        paths["report"].write_text(report, encoding="utf-8")
    except Exception as exc:
        print(f"xx00-regime-long-short: report-layer-failed: {exc}")
        pd.DataFrame().to_csv(paths["selected_events"], index=False)
        pd.DataFrame().to_csv(paths["selected_monthly"], index=False)
        paths["report"].write_text(
            "# XX:00 Regime Long/Short Research\n\n"
            "Report layer failed after core CSV outputs were already written.\n\n"
            f"Error: `{type(exc).__name__}: {exc}`\n",
            encoding="utf-8",
        )
    print("xx00-regime-long-short: done")
    return paths


if __name__ == "__main__":
    output_paths = run()
    for key, value in output_paths.items():
        print(f"{key}: {value}")
