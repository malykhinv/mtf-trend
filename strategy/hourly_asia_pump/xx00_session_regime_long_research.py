from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from domain.enums.timeframe import Timeframe
from strategy.hourly_asia_pump.config import build_hourly_asia_pump_profile
from strategy.hourly_asia_pump.research import _build_timeframe_candidates
from strategy.hourly_asia_pump.static_combo import _build_monthly_returns_frame, _frame_to_markdown, _summarize_events
from strategy.hourly_asia_pump.unified_edge import _simulate_equity_risk_metrics
from strategy.hourly_asia_pump.xx00_regime_long_short_research import (
    EventState,
    LongFollowRule,
    LongLaunchRule,
    LongLatePushRule,
    _calendar_months_from_frame,
    _close_pos,
    _coverage_summary,
    _long_follow_rules,
    _long_late_push_rules,
    _long_launch_rules,
    _prepare_event_state,
    _rule_family_rank,
    _rule_summary,
    _select_rules,
    _simulate_exit_1m,
)
from vectorbt_runner.data_preparer import DataPreparer

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "xx00_session_regime_long_research"
CURRENT_CACHE_DIR = REPO_ROOT / ".output" / "cache"
PREV_CACHE_DIR = REPO_ROOT / ".output" / "cache_prev_year_5m"
PRIOR_CROSS_SESSION_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "xx00_edge_bias_and_cross_session_scan"
ONE_MINUTE_MS = 60_000
FEE_RATE = 0.0004
STRONG_CONTINUATION_THRESHOLD = 0.025
DEAD_PUMP_THRESHOLD = 0.01

LOGGER = logging.getLogger("xx00-session-regime-long")


@dataclass(frozen=True, slots=True)
class SessionSpec:
    session_id: str
    start_hour_utc: int
    end_hour_utc: int


def _session_specs() -> tuple[SessionSpec, ...]:
    return (
        SessionSpec("asia", 0, 8),
        SessionSpec("europe", 8, 16),
        SessionSpec("america", 16, 0),
    )


def _safe_label(value: object) -> str:
    text = str(value)
    return text.encode("ascii", errors="ignore").decode("ascii") or "<non-ascii>"


def _safe_float(value: object, *, default: float = 0.0) -> float:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return default
    return float(numeric)


def _net_long_return(entry_price: float, exit_price: float) -> float:
    if entry_price <= 0.0 or exit_price <= 0.0:
        return 0.0
    return float((exit_price * (1.0 - FEE_RATE) / (entry_price * (1.0 + FEE_RATE))) - 1.0)


def _build_missing_session_universe(missing_sessions: list[SessionSpec]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for session in missing_sessions:
        for dataset, cache_dir in (("current", CURRENT_CACHE_DIR), ("old", PREV_CACHE_DIR)):
            preparer = DataPreparer(cache_dir)
            symbols = preparer.list_symbols(Timeframe.M5)
            params = build_hourly_asia_pump_profile(
                timeframe=Timeframe.M5,
                profile_id="loose",
                asia_start_hour_utc=session.start_hour_utc,
                asia_end_hour_utc=session.end_hour_utc,
                trigger_minute=0,
                max_follow_minutes=120,
            )
            LOGGER.info(
                "xx00-session-regime-long: build-universe session=%s dataset=%s symbols=%s",
                session.session_id,
                dataset,
                len(symbols),
            )
            scope = _build_timeframe_candidates(
                preparer=preparer,
                timeframe=Timeframe.M5,
                symbols=symbols,
                base_params=params,
                logger=LOGGER,
            )
            if scope.empty:
                continue
            scope["session_id"] = session.session_id
            scope["dataset"] = dataset
            scope["cache_scope"] = dataset
            frames.append(scope)
    universe = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if universe.empty:
        return universe
    universe["timestamp_utc"] = pd.to_datetime(
        pd.to_numeric(universe["timestamp_ms"], errors="coerce"),
        unit="ms",
        utc=True,
        errors="coerce",
    )
    universe["month_utc"] = universe["timestamp_utc"].dt.strftime("%Y-%m")
    universe["date_utc"] = universe["timestamp_utc"].dt.strftime("%Y-%m-%d")
    current_mask = universe["dataset"].astype(str) == "current"
    last_timestamp = universe.loc[current_mask, "timestamp_utc"].max()
    if pd.notna(last_timestamp):
        cutoff = last_timestamp - pd.Timedelta(days=365)
        universe = universe[(~current_mask) | (universe["timestamp_utc"] >= cutoff)].copy()
    return universe


def _load_or_build_session_universe(path: Path) -> pd.DataFrame:
    if path.exists():
        return pd.read_csv(path, low_memory=False)

    frames: list[pd.DataFrame] = []
    covered: set[str] = set()
    prior_path = PRIOR_CROSS_SESSION_DIR / "session_universe.csv"
    if prior_path.exists():
        prior = pd.read_csv(prior_path, low_memory=False)
        prior = prior[prior["session_id"].astype(str).isin({"europe", "america"})].copy()
        if not prior.empty:
            frames.append(prior)
            covered.update(prior["session_id"].astype(str).unique().tolist())

    missing = [session for session in _session_specs() if session.session_id not in covered]
    if missing:
        built = _build_missing_session_universe(missing)
        if not built.empty:
            frames.append(built)

    universe = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if universe.empty:
        return universe
    universe["timestamp_utc"] = pd.to_datetime(
        universe.get("timestamp_utc", pd.Series(index=universe.index)),
        utc=True,
        errors="coerce",
    )
    if universe["timestamp_utc"].isna().all():
        universe["timestamp_utc"] = pd.to_datetime(
            pd.to_numeric(universe["timestamp_ms"], errors="coerce"),
            unit="ms",
            utc=True,
            errors="coerce",
        )
    universe["month_utc"] = universe.get("month_utc", universe["timestamp_utc"].dt.strftime("%Y-%m"))
    universe["date_utc"] = universe.get("date_utc", universe["timestamp_utc"].dt.strftime("%Y-%m-%d"))
    current_mask = universe["dataset"].astype(str) == "current"
    last_timestamp = universe.loc[current_mask, "timestamp_utc"].max()
    if pd.notna(last_timestamp):
        cutoff = last_timestamp - pd.Timedelta(days=365)
        universe = universe[(~current_mask) | (universe["timestamp_utc"] >= cutoff)].copy()
    return universe.sort_values(["session_id", "dataset", "timestamp_ms", "symbol"]).reset_index(drop=True)


def _bucket_by_thresholds(series: pd.Series, q1: float, q2: float, low_label: str, mid_label: str, high_label: str) -> pd.Series:
    clean = pd.to_numeric(series, errors="coerce")
    bucket = pd.Series(index=series.index, dtype="object")
    bucket.loc[clean <= q1] = low_label
    bucket.loc[(clean > q1) & (clean <= q2)] = mid_label
    bucket.loc[clean > q2] = high_label
    return bucket.fillna(mid_label)


def _add_regime_categories(universe: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if universe.empty:
        return universe, pd.DataFrame()
    enriched = universe.copy()
    trigger_range = (
        pd.to_numeric(enriched["trigger_high"], errors="coerce")
        - pd.to_numeric(enriched["trigger_low"], errors="coerce")
    ).replace(0.0, pd.NA)
    top = enriched[["trigger_open", "trigger_close"]].max(axis=1)
    bottom = enriched[["trigger_open", "trigger_close"]].min(axis=1)
    enriched["upper_wick_frac"] = (pd.to_numeric(enriched["trigger_high"], errors="coerce") - top) / trigger_range
    enriched["lower_wick_frac"] = (bottom - pd.to_numeric(enriched["trigger_low"], errors="coerce")) / trigger_range
    enriched["body_frac"] = (
        pd.to_numeric(enriched["trigger_close"], errors="coerce")
        - pd.to_numeric(enriched["trigger_open"], errors="coerce")
    ).abs() / trigger_range
    enriched["body_return_pct"] = (
        (
            pd.to_numeric(enriched["trigger_close"], errors="coerce")
            - pd.to_numeric(enriched["trigger_open"], errors="coerce")
        ).abs()
        / pd.to_numeric(enriched["trigger_open"], errors="coerce")
    )
    enriched["trigger_close_pos_in_bar"] = (
        pd.to_numeric(enriched["trigger_close"], errors="coerce")
        - pd.to_numeric(enriched["trigger_low"], errors="coerce")
    ) / trigger_range
    enriched["impulse_archetype"] = "impulse_balanced"
    enriched.loc[
        (pd.to_numeric(enriched["body_frac"], errors="coerce") >= 0.65)
        & (pd.to_numeric(enriched["upper_wick_frac"], errors="coerce") <= 0.15),
        "impulse_archetype",
    ] = "impulse_body_drive"
    enriched.loc[
        (pd.to_numeric(enriched["upper_wick_frac"], errors="coerce") >= 0.25)
        | (pd.to_numeric(enriched["close_to_high_frac"], errors="coerce") >= 0.15),
        "impulse_archetype",
    ] = "impulse_wicky_spike"

    threshold_rows: list[dict[str, object]] = []
    for session_id, scoped in enriched.groupby("session_id", sort=True):
        old = scoped[scoped["dataset"].astype(str) == "old"].copy()
        if old.empty:
            old = scoped.copy()
        drift = pd.to_numeric(old["pre_base_drift_pct_60m"], errors="coerce").dropna()
        base_range = pd.to_numeric(old["pre_base_range_pct_60m"], errors="coerce").dropna()
        drift_q1 = float(drift.quantile(1.0 / 3.0)) if not drift.empty else 0.0
        drift_q2 = float(drift.quantile(2.0 / 3.0)) if not drift.empty else 0.0
        range_q1 = float(base_range.quantile(1.0 / 3.0)) if not base_range.empty else 0.0
        range_q2 = float(base_range.quantile(2.0 / 3.0)) if not base_range.empty else 0.0
        mask = enriched["session_id"].astype(str) == str(session_id)
        enriched.loc[mask, "context_drift_bucket"] = _bucket_by_thresholds(
            enriched.loc[mask, "pre_base_drift_pct_60m"],
            drift_q1,
            drift_q2,
            "drift_cold",
            "drift_warm",
            "drift_hot",
        )
        enriched.loc[mask, "context_range_bucket"] = _bucket_by_thresholds(
            enriched.loc[mask, "pre_base_range_pct_60m"],
            range_q1,
            range_q2,
            "range_tight",
            "range_mid",
            "range_wide",
        )
        threshold_rows.append(
            {
                "session_id": str(session_id),
                "threshold_source": "old_only",
                "drift_q1": drift_q1,
                "drift_q2": drift_q2,
                "range_q1": range_q1,
                "range_q2": range_q2,
                "old_events_for_thresholds": int(len(old)),
            }
        )
    enriched["context_archetype"] = "context_warm"
    enriched.loc[
        (enriched["context_drift_bucket"].astype(str) == "drift_cold")
        & (enriched["context_range_bucket"].astype(str) == "range_tight"),
        "context_archetype",
    ] = "context_coiled"
    enriched.loc[
        (enriched["context_drift_bucket"].astype(str) == "drift_hot")
        | (enriched["context_range_bucket"].astype(str) == "range_wide"),
        "context_archetype",
    ] = "context_overheated"
    return enriched.reset_index(drop=True), pd.DataFrame(threshold_rows)


def _base_scope(universe: pd.DataFrame) -> pd.DataFrame:
    if universe.empty:
        return universe
    scoped = universe[
        (universe["impulse_archetype"].astype(str) == "impulse_wicky_spike")
        & (universe["context_archetype"].astype(str).isin({"context_warm", "context_overheated"}))
        & (pd.to_numeric(universe["trigger_return_pct"], errors="coerce") >= 0.02)
        & (pd.to_numeric(universe["volume_mult"], errors="coerce") >= 10.0)
    ].copy()
    scoped["event_id"] = (
        scoped["dataset"].astype(str)
        + "|"
        + scoped["session_id"].astype(str)
        + "|"
        + scoped["symbol"].astype(str)
        + "|"
        + pd.to_numeric(scoped["timestamp_ms"], errors="coerce").astype("int64").astype(str)
    )
    return scoped.sort_values(["session_id", "dataset", "timestamp_ms", "symbol"]).reset_index(drop=True)


def _derive_base_features(scope: pd.DataFrame) -> pd.DataFrame:
    if scope.empty:
        return scope
    cache_m1: dict[tuple[str, str], pd.DataFrame] = {}
    cache_m5: dict[tuple[str, str], pd.DataFrame] = {}
    rows: list[dict[str, object]] = []
    grouped = scope.groupby(["cache_scope", "symbol"], sort=True)
    total_groups = grouped.ngroups
    for group_index, ((cache_scope, symbol), scoped) in enumerate(grouped, start=1):
        preparer = DataPreparer(CURRENT_CACHE_DIR if str(cache_scope) == "current" else PREV_CACHE_DIR)
        candles_m1 = cache_m1.get((str(cache_scope), str(symbol)))
        if candles_m1 is None:
            candles_m1 = preparer.load_symbol_data(str(symbol), Timeframe.M1)
            if candles_m1.empty or "timestamp" not in candles_m1.columns:
                candles_m1 = pd.DataFrame()
            else:
                candles_m1 = candles_m1.sort_values("timestamp").reset_index(drop=True)
            cache_m1[(str(cache_scope), str(symbol))] = candles_m1
        candles_m5 = cache_m5.get((str(cache_scope), str(symbol)))
        if candles_m5 is None:
            candles_m5 = preparer.load_symbol_data(str(symbol), Timeframe.M5)
            if candles_m5.empty or "timestamp" not in candles_m5.columns:
                candles_m5 = pd.DataFrame()
            else:
                candles_m5 = candles_m5.sort_values("timestamp").reset_index(drop=True)
            cache_m5[(str(cache_scope), str(symbol))] = candles_m5
        if candles_m1.empty or candles_m5.empty:
            continue

        timestamps_m1 = pd.to_numeric(candles_m1["timestamp"], errors="coerce").astype("int64").tolist()
        timestamp_to_idx_m1 = {int(timestamp): idx for idx, timestamp in enumerate(timestamps_m1)}
        opens_m1 = pd.to_numeric(candles_m1["open"], errors="coerce").astype(float).tolist()
        highs_m1 = pd.to_numeric(candles_m1["high"], errors="coerce").astype(float).tolist()
        lows_m1 = pd.to_numeric(candles_m1["low"], errors="coerce").astype(float).tolist()
        closes_m1 = pd.to_numeric(candles_m1["close"], errors="coerce").astype(float).tolist()
        volumes_m1 = pd.to_numeric(candles_m1["volume"], errors="coerce").astype(float).tolist()
        ema200_m1 = pd.Series(closes_m1, dtype="float64").ewm(span=200, adjust=False).mean().tolist()

        timestamps_m5 = pd.to_numeric(candles_m5["timestamp"], errors="coerce").astype("int64").tolist()
        timestamp_to_idx_m5 = {int(timestamp): idx for idx, timestamp in enumerate(timestamps_m5)}
        highs_m5 = pd.to_numeric(candles_m5["high"], errors="coerce").astype(float).tolist()
        lows_m5 = pd.to_numeric(candles_m5["low"], errors="coerce").astype(float).tolist()

        if group_index == 1 or group_index % 25 == 0 or group_index == total_groups:
            print(
                f"xx00-session-regime-long: feature-progress={group_index}/{total_groups} "
                f"cache={cache_scope} symbol={_safe_label(symbol)} events={len(scoped)}"
            )

        for _, row in scoped.iterrows():
            state = _prepare_event_state(
                event_row=row,
                timestamp_to_idx=timestamp_to_idx_m1,
                opens=opens_m1,
                highs=highs_m1,
                lows=lows_m1,
                closes=closes_m1,
                volumes=volumes_m1,
            )
            if state is None:
                continue
            pre_idx = state.start_idx - 1
            pre_close = float(closes_m1[pre_idx])
            ema200_value = float(ema200_m1[pre_idx]) if pre_idx < len(ema200_m1) else 0.0
            next_open_price = float(opens_m1[state.start_idx + 1]) if (state.start_idx + 1) < len(opens_m1) else None
            m5_idx = timestamp_to_idx_m5.get(int(row["timestamp_ms"]))
            if m5_idx is None:
                continue
            future_indices = range(m5_idx + 1, min(len(highs_m5), m5_idx + 7))
            trigger_high = float(row["trigger_high"])
            trigger_low = float(row["trigger_low"])
            post_max_up_extension = max(
                [max(0.0, (float(highs_m5[idx]) - trigger_high) / trigger_high) for idx in future_indices],
                default=0.0,
            )
            post_max_down_extension = max(
                [max(0.0, (trigger_low - float(lows_m5[idx])) / trigger_low) for idx in future_indices],
                default=0.0,
            )
            rows.append(
                {
                    **row.to_dict(),
                    "pre_close_vs_ema200_pct": ((pre_close / ema200_value) - 1.0) if ema200_value > 0.0 else None,
                    "m0_return_pct_online": float(state.m0_return_pct),
                    "m0_close_pos_online": float(state.m0_close_pos),
                    "m0_volume_ratio_online": float(state.m0_volume_ratio),
                    "next_open_price_m1": next_open_price,
                    "next_open_gap_vs_signal_close_pct": ((next_open_price / state.m0_close) - 1.0)
                    if next_open_price is not None and state.m0_close > 0.0
                    else None,
                    "post_max_up_extension_pct_30m": post_max_up_extension,
                    "post_max_down_extension_pct_30m": post_max_down_extension,
                }
            )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["continuation_label"] = "middle"
    continuation = pd.to_numeric(frame["post_max_up_extension_pct_30m"], errors="coerce")
    frame.loc[continuation >= STRONG_CONTINUATION_THRESHOLD, "continuation_label"] = "strong"
    frame.loc[continuation <= DEAD_PUMP_THRESHOLD, "continuation_label"] = "dead"
    frame["is_labeled"] = frame["continuation_label"].isin({"strong", "dead"})
    return frame.sort_values(["session_id", "dataset", "timestamp_ms", "symbol"]).reset_index(drop=True)


def _cohort_scope(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    rows: list[pd.DataFrame] = []
    for session_id, scoped in frame.groupby("session_id", sort=True):
        above_ema200 = pd.to_numeric(scoped["pre_close_vs_ema200_pct"], errors="coerce") > 0.0
        high_pre_range = pd.to_numeric(scoped["pre_base_range_pct_60m"], errors="coerce") >= 0.05
        overheated = scoped["context_archetype"].astype(str) == "context_overheated"
        drift_hot = scoped["context_drift_bucket"].astype(str) == "drift_hot"

        union = scoped[above_ema200 & ((overheated & high_pre_range) | drift_hot)].copy()
        union["cohort_id"] = f"{session_id}__long_union"
        union["cohort_title"] = f"{session_id}: long_union"
        union["side"] = "long"
        union["base_cohort_id"] = "long_union"
        rows.append(union)

        hot_range = scoped[overheated & above_ema200 & high_pre_range].copy()
        hot_range["cohort_id"] = f"{session_id}__long_hot_range"
        hot_range["cohort_title"] = f"{session_id}: long_hot_range"
        hot_range["side"] = "long"
        hot_range["base_cohort_id"] = "long_hot_range"
        rows.append(hot_range)
    scoped = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    return scoped.sort_values(["session_id", "cohort_id", "dataset", "timestamp_ms", "symbol"]).reset_index(drop=True)


def _long_base_ok(event_state: EventState) -> bool:
    return (
        event_state.m0_return_pct > 0.002
        and event_state.m0_close > event_state.m0_open
        and event_state.m0_close_pos >= 0.50
        and event_state.m0_volume_ratio >= 2.0
    )


def _simulate_long_launch_rule_next_open(
    *,
    event_state: EventState,
    timestamps: list[int],
    opens: list[float],
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

    fill_idx = event_state.start_idx + 1
    if fill_idx >= len(opens):
        return None
    entry_price = float(opens[fill_idx])
    stop_price = event_state.m0_low
    if stop_price >= entry_price:
        return None

    exit_idx, exit_price, exit_reason = _simulate_exit_1m(
        timestamps=timestamps,
        highs=highs,
        lows=lows,
        closes=closes,
        entry_idx=event_state.start_idx,
        entry_price=entry_price,
        stop_price=stop_price,
        rr_target=rule.rr_target,
        max_hold_minutes=120,
        fast_fail_minutes=8,
        fast_fail_r=0.25,
    )
    return {
        "signal_bar_timestamp_ms": int(timestamps[event_state.start_idx]),
        "entry_timestamp_ms": int(timestamps[event_state.start_idx] + ONE_MINUTE_MS),
        "exit_timestamp_ms": int(timestamps[exit_idx] + ONE_MINUTE_MS),
        "entry_price": float(entry_price),
        "stop_price": float(stop_price),
        "initial_risk_pct": float((entry_price - stop_price) / entry_price),
        "exit_price": float(exit_price),
        "exit_reason": exit_reason,
        "exit_return_pct": _net_long_return(entry_price, exit_price),
        "entry_detail": "m0_close_break_next_open",
        "entry_delay_minutes": 1,
        "signal_close_price": float(event_state.m0_close),
        "entry_gap_vs_signal_close_pct": float((entry_price / event_state.m0_close) - 1.0) if event_state.m0_close > 0 else None,
        "m0_return_pct_online": float(event_state.m0_return_pct),
        "m0_close_pos_online": float(event_state.m0_close_pos),
        "m0_volume_ratio_online": float(event_state.m0_volume_ratio),
    }


def _simulate_long_follow_rule_next_open(
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
        if idx >= len(closes) or (idx + 1) >= len(opens):
            continue
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
        entry_price = float(opens[idx + 1])
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
            "entry_detail": "follow_break_next_open",
            "entry_delay_minutes": int(offset + 1),
            "signal_close_price": float(signal_close),
            "entry_gap_vs_signal_close_pct": float((entry_price / signal_close) - 1.0) if signal_close > 0 else None,
            "m0_return_pct_online": float(event_state.m0_return_pct),
            "m0_close_pos_online": float(event_state.m0_close_pos),
            "m0_volume_ratio_online": float(event_state.m0_volume_ratio),
            "signal_body_frac_m0range": float(signal_body_frac_m0range),
            "signal_close_pos_online": float(signal_close_pos),
            "signal_volume_ratio_online": float(signal_volume_ratio),
            "pullback_frac_before_entry": float(pullback_frac),
        }
    return None


def _simulate_long_late_push_rule_next_open(
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
    if idx >= len(closes) or (idx + 1) >= len(opens):
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
    entry_price = float(opens[idx + 1])
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
        "entry_detail": "late_push_next_open",
        "entry_delay_minutes": int(rule.signal_offset + 1),
        "signal_close_price": float(signal_close),
        "entry_gap_vs_signal_close_pct": float((entry_price / signal_close) - 1.0) if signal_close > 0 else None,
        "m0_return_pct_online": float(event_state.m0_return_pct),
        "m0_close_pos_online": float(event_state.m0_close_pos),
        "m0_volume_ratio_online": float(event_state.m0_volume_ratio),
        "signal_body_frac_m0range": float(signal_body_frac_m0range),
        "last2_volume_share_online": float(last2_volume_share),
        "close_vs_prev240_pct_online": float(close_vs_prev240_pct),
    }


def _build_events(
    scope: pd.DataFrame,
    rules: tuple[LongLaunchRule | LongFollowRule | LongLatePushRule, ...],
) -> pd.DataFrame:
    if scope.empty:
        return pd.DataFrame()
    cache: dict[tuple[str, str], pd.DataFrame] = {}
    rows: list[dict[str, object]] = []
    grouped = scope.groupby(["cache_scope", "symbol"], sort=True)
    total_groups = grouped.ngroups
    for group_index, ((cache_scope, symbol), scoped) in enumerate(grouped, start=1):
        candles = cache.get((str(cache_scope), str(symbol)))
        if candles is None:
            preparer = DataPreparer(CURRENT_CACHE_DIR if str(cache_scope) == "current" else PREV_CACHE_DIR)
            candles = preparer.load_symbol_data(str(symbol), Timeframe.M1)
            if candles.empty or "timestamp" not in candles.columns:
                candles = pd.DataFrame()
            else:
                candles = candles.sort_values("timestamp").reset_index(drop=True)
            cache[(str(cache_scope), str(symbol))] = candles
        if candles.empty:
            continue
        timestamps = pd.to_numeric(candles["timestamp"], errors="coerce").astype("int64").tolist()
        timestamp_to_idx = {int(timestamp): idx for idx, timestamp in enumerate(timestamps)}
        opens = pd.to_numeric(candles["open"], errors="coerce").astype(float).tolist()
        highs = pd.to_numeric(candles["high"], errors="coerce").astype(float).tolist()
        lows = pd.to_numeric(candles["low"], errors="coerce").astype(float).tolist()
        closes = pd.to_numeric(candles["close"], errors="coerce").astype(float).tolist()
        volumes = pd.to_numeric(candles["volume"], errors="coerce").astype(float).tolist()
        if group_index == 1 or group_index % 25 == 0 or group_index == total_groups:
            print(
                f"xx00-session-regime-long: simulate-progress={group_index}/{total_groups} "
                f"cache={cache_scope} symbol={_safe_label(symbol)} events={len(scoped)}"
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
                "event_id": str(row["event_id"]),
                "session_id": str(row["session_id"]),
                "base_cohort_id": str(row["base_cohort_id"]),
                "cohort_id": str(row["cohort_id"]),
                "cohort_title": str(row["cohort_title"]),
                "side": "long",
                "dataset": str(row["dataset"]),
                "cache_scope": str(row["cache_scope"]),
                "symbol": str(row["symbol"]),
                "timestamp_ms": int(row["timestamp_ms"]),
                "timestamp_utc": row.get("timestamp_utc"),
                "month_utc": str(row["month_utc"]),
                "date_utc": str(row["date_utc"]),
                "context_archetype": str(row["context_archetype"]),
                "context_drift_bucket": str(row["context_drift_bucket"]),
                "continuation_label": str(row["continuation_label"]),
                "trigger_return_pct": _safe_float(row.get("trigger_return_pct")),
                "volume_mult": _safe_float(row.get("volume_mult")),
                "pre_base_range_pct_60m": _safe_float(row.get("pre_base_range_pct_60m")),
                "pre_close_vs_ema200_pct": _safe_float(row.get("pre_close_vs_ema200_pct")),
            }
            for rule in rules:
                if isinstance(rule, LongLaunchRule):
                    result = _simulate_long_launch_rule_next_open(
                        event_state=event_state,
                        timestamps=timestamps,
                        opens=opens,
                        highs=highs,
                        lows=lows,
                        closes=closes,
                        rule=rule,
                    )
                elif isinstance(rule, LongFollowRule):
                    result = _simulate_long_follow_rule_next_open(
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
                    result = _simulate_long_late_push_rule_next_open(
                        event_state=event_state,
                        timestamps=timestamps,
                        opens=opens,
                        highs=highs,
                        lows=lows,
                        closes=closes,
                        volumes=volumes,
                        rule=rule,
                    )
                if result is None:
                    continue
                rows.append({**base, **result, "family": rule.family, "rule_id": rule.rule_id})
    events = pd.DataFrame(rows)
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
    return events.sort_values(
        ["session_id", "cohort_id", "family", "rule_id", "dataset", "timestamp_ms", "symbol"]
    ).reset_index(drop=True)


def _positive_trade_share(event_slice: pd.DataFrame, top_n: int) -> float:
    if event_slice.empty:
        return 0.0
    positive = pd.to_numeric(event_slice["exit_return_pct"], errors="coerce").clip(lower=0.0).sort_values(ascending=False)
    total = float(positive.sum())
    if total <= 0.0:
        return 0.0
    return float(positive.head(top_n).sum() / total)


def _positive_symbol_share(event_slice: pd.DataFrame, top_n: int) -> float:
    if event_slice.empty:
        return 0.0
    grouped = (
        event_slice.assign(pos_return=pd.to_numeric(event_slice["exit_return_pct"], errors="coerce").clip(lower=0.0))
        .groupby("symbol", as_index=False)["pos_return"]
        .sum()
        .sort_values("pos_return", ascending=False)
    )
    total = float(grouped["pos_return"].sum())
    if total <= 0.0:
        return 0.0
    return float(grouped["pos_return"].head(top_n).sum() / total)


def _best_month_share(event_slice: pd.DataFrame) -> float:
    if event_slice.empty:
        return 0.0
    monthly = (
        event_slice.assign(pos_return=pd.to_numeric(event_slice["exit_return_pct"], errors="coerce").clip(lower=0.0))
        .groupby("month_utc", as_index=False)["pos_return"]
        .sum()
    )
    total = float(monthly["pos_return"].sum())
    if total <= 0.0:
        return 0.0
    return float(monthly["pos_return"].max() / total)


def _diagnostic_rows(scope: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    if scope.empty or events.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    coverage_counts = (
        scope.groupby(["cohort_id", "dataset"], sort=True)
        .size()
        .unstack(fill_value=0)
        .rename(columns={"current": "coverage_current", "old": "coverage_old"})
    )
    for cohort_id, cohort_scope in scope.groupby("cohort_id", sort=True):
        cohort_events = events[events["cohort_id"].astype(str) == str(cohort_id)].copy()
        current_scope = cohort_scope[cohort_scope["dataset"].astype(str) == "current"].copy()
        old_scope = cohort_scope[cohort_scope["dataset"].astype(str) == "old"].copy()
        current_months = _calendar_months_from_frame(current_scope)
        old_months = _calendar_months_from_frame(old_scope)
        for (_, family, rule_id), scoped_events in cohort_events.groupby(["cohort_id", "family", "rule_id"], sort=True):
            current_events = scoped_events[scoped_events["dataset"].astype(str) == "current"].copy()
            old_events = scoped_events[scoped_events["dataset"].astype(str) == "old"].copy()
            current_equity_3, _ = _simulate_equity_risk_metrics(current_events, calendar_months=current_months, risk_fraction=0.03)
            old_equity_3, _ = _simulate_equity_risk_metrics(old_events, calendar_months=old_months, risk_fraction=0.03)
            current_trades_per_year = float((_summarize_events(current_events, calendar_months=current_months) or {}).get("trades_per_year", 0.0))
            row = {
                "cohort_id": str(cohort_id),
                "family": str(family),
                "rule_id": str(rule_id),
                "family_rank": _rule_family_rank(str(family)),
                "coverage_current": int(coverage_counts.loc[cohort_id, "coverage_current"]) if "coverage_current" in coverage_counts.columns else 0,
                "coverage_old": int(coverage_counts.loc[cohort_id, "coverage_old"]) if "coverage_old" in coverage_counts.columns else 0,
                "current_equity_total_return_pct_3": float(current_equity_3.get("equity_total_return_pct", 0.0)),
                "current_equity_annualized_return_pct_3": float(current_equity_3.get("equity_annualized_return_pct", 0.0)),
                "current_equity_max_drawdown_pct_3": float(current_equity_3.get("equity_max_drawdown_pct", 0.0)),
                "old_equity_total_return_pct_3": float(old_equity_3.get("equity_total_return_pct", 0.0)),
                "old_equity_annualized_return_pct_3": float(old_equity_3.get("equity_annualized_return_pct", 0.0)),
                "old_equity_max_drawdown_pct_3": float(old_equity_3.get("equity_max_drawdown_pct", 0.0)),
                "current_top1_trade_share": _positive_trade_share(current_events, 1),
                "current_top3_trade_share": _positive_trade_share(current_events, 3),
                "current_top5_trade_share": _positive_trade_share(current_events, 5),
                "current_top1_symbol_share": _positive_symbol_share(current_events, 1),
                "current_top3_symbol_share": _positive_symbol_share(current_events, 3),
                "current_top5_symbol_share": _positive_symbol_share(current_events, 5),
                "current_best_month_share": _best_month_share(current_events),
                "current_trades_per_year_recheck": current_trades_per_year,
            }
            rows.append(row)
    return pd.DataFrame(rows)


def _apply_user_gate(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return summary
    enriched = summary.copy()
    checks = {
        "gate_trades": pd.to_numeric(enriched["current_trades_per_year"], errors="coerce") >= 50.0,
        "gate_return_3": pd.to_numeric(enriched["current_equity_annualized_return_pct_3"], errors="coerce") >= 1.0,
        "gate_return_5": pd.to_numeric(enriched["current_equity_annualized_return_pct_5"], errors="coerce") >= 1.0,
        "gate_dd_3": pd.to_numeric(enriched["current_equity_max_drawdown_pct_3"], errors="coerce") <= 0.10,
        "gate_dd_5": pd.to_numeric(enriched["current_equity_max_drawdown_pct_5"], errors="coerce") <= 0.15,
        "gate_months": pd.to_numeric(enriched["current_equity_positive_months_count_5"], errors="coerce") >= 8,
        "gate_stable_months": pd.to_numeric(enriched["current_equity_stable_positive_months_count_5"], errors="coerce") >= 7,
        "gate_old_nonnegative": (pd.to_numeric(enriched["old_mean_return_pct"], errors="coerce") >= 0.0)
        & (pd.to_numeric(enriched["old_trades"], errors="coerce") >= 2),
        "gate_symbol_top1": pd.to_numeric(enriched["current_top1_symbol_share"], errors="coerce") <= 0.30,
        "gate_symbol_top3": pd.to_numeric(enriched["current_top3_symbol_share"], errors="coerce") <= 0.50,
        "gate_symbol_top5": pd.to_numeric(enriched["current_top5_symbol_share"], errors="coerce") <= 0.65,
        "gate_trade_top1": pd.to_numeric(enriched["current_top1_trade_share"], errors="coerce") <= 0.25,
        "gate_trade_top3": pd.to_numeric(enriched["current_top3_trade_share"], errors="coerce") <= 0.50,
        "gate_trade_top5": pd.to_numeric(enriched["current_top5_trade_share"], errors="coerce") <= 0.65,
        "gate_best_month": pd.to_numeric(enriched["current_best_month_share"], errors="coerce") <= 0.35,
    }
    for column, mask in checks.items():
        enriched[column] = mask.fillna(False)
    gate_columns = list(checks.keys())
    enriched["gate_pass_count"] = enriched[gate_columns].astype(int).sum(axis=1)
    enriched["passes_user_gate"] = enriched[gate_columns].all(axis=1)
    return enriched


def _qualified_or_near_miss(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return summary
    qualified = summary[summary["passes_user_gate"].astype(bool)].copy()
    if not qualified.empty:
        return qualified.sort_values(
            ["passes_user_gate", "current_equity_annualized_return_pct_5", "current_trades_per_year", "robust_score"],
            ascending=[False, False, False, False],
        ).reset_index(drop=True)
    return summary.sort_values(
        ["gate_pass_count", "current_equity_annualized_return_pct_5", "current_trades_per_year", "robust_score"],
        ascending=[False, False, False, False],
    ).head(18).reset_index(drop=True)


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


def _lookahead_audit(selected_events: pd.DataFrame) -> pd.DataFrame:
    if selected_events.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for (session_id, cohort_id, rule_id), scoped in selected_events.groupby(["session_id", "cohort_id", "rule_id"], sort=True):
        entry_ts = pd.to_numeric(scoped["entry_timestamp_ms"], errors="coerce")
        signal_ts = pd.to_numeric(scoped["signal_bar_timestamp_ms"], errors="coerce")
        gap = pd.to_numeric(scoped["entry_gap_vs_signal_close_pct"], errors="coerce").dropna()
        rows.append(
            {
                "session_id": str(session_id),
                "cohort_id": str(cohort_id),
                "rule_id": str(rule_id),
                "trades": int(len(scoped)),
                "all_entries_after_signal": bool((entry_ts > signal_ts).all()),
                "min_signal_to_entry_minutes": float(((entry_ts - signal_ts) / ONE_MINUTE_MS).min()) if not scoped.empty else None,
                "mean_entry_gap_vs_signal_close_pct": float(gap.mean()) if not gap.empty else 0.0,
                "median_entry_gap_vs_signal_close_pct": float(gap.median()) if not gap.empty else 0.0,
                "p95_abs_entry_gap_vs_signal_close_pct": float(gap.abs().quantile(0.95)) if not gap.empty else 0.0,
                "max_abs_entry_gap_vs_signal_close_pct": float(gap.abs().max()) if not gap.empty else 0.0,
                "uses_next_open_fill": True,
                "thresholds_frozen_on_old": True,
            }
        )
    return pd.DataFrame(rows)


def _session_parse(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return summary
    parsed = summary.copy()
    parsed["session_id"] = parsed["cohort_id"].astype(str).str.split("__").str[0]
    parsed["base_cohort_id"] = parsed["cohort_id"].astype(str).str.split("__").str[1]
    return parsed


def _build_report(
    thresholds: pd.DataFrame,
    coverage: pd.DataFrame,
    selected: pd.DataFrame,
    qualified: pd.DataFrame,
    audit: pd.DataFrame,
) -> str:
    selected_report = _session_parse(selected).copy()
    qualified_report = _session_parse(qualified).copy()
    audit_report = audit.copy()
    for frame in (selected_report, qualified_report):
        if "passes_user_gate" in frame.columns:
            frame["passes_user_gate"] = frame["passes_user_gate"].map(lambda value: "yes" if bool(value) else "no")
    for column in ("all_entries_after_signal", "uses_next_open_fill", "thresholds_frozen_on_old"):
        if column in audit_report.columns:
            audit_report[column] = audit_report[column].map(lambda value: "yes" if bool(value) else "no")
    lines = [
        "# XX:00 Session-Separate Regime Long Research",
        "",
        "Goal:",
        "- research Asia, Europe, and America separately under the same XX:00 long logic;",
        "- rebuild warm / overheated regimes inside each session with thresholds frozen on old data only;",
        "- simulate entries conservatively at next 1m open after the signal close;",
        "- surface only edges that can survive high annualized targets, drawdown limits, and concentration checks.",
        "",
        "Session thresholds (frozen on old only):",
        _frame_to_markdown(
            thresholds,
            columns=["session_id", "drift_q1", "drift_q2", "range_q1", "range_q2", "old_events_for_thresholds"],
        )
        if not thresholds.empty
        else "_No threshold data._",
        "",
        "Coverage:",
        _frame_to_markdown(
            _session_parse(coverage),
            columns=["session_id", "base_cohort_id", "dataset", "analog_cases", "strong_cases", "dead_cases", "symbols"],
        )
        if not coverage.empty
        else "_No coverage data._",
        "",
        "Selected rules:",
        _frame_to_markdown(
            selected_report,
            columns=[
                "session_id",
                "base_cohort_id",
                "selection_type",
                "family",
                "rule_id",
                "current_trades",
                "current_trades_per_year",
                "current_mean_entry_delay_minutes",
                "current_win_rate",
                "current_mean_return_pct",
                "current_equity_annualized_return_pct_3",
                "current_equity_max_drawdown_pct_3",
                "current_equity_annualized_return_pct_5",
                "current_equity_max_drawdown_pct_5",
                "current_top3_symbol_share",
                "current_top3_trade_share",
                "gate_pass_count",
                "passes_user_gate",
            ],
        )
        if not selected.empty
        else "_No selected rules._",
        "",
        "Qualified rules or best near-misses:",
        _frame_to_markdown(
            qualified_report,
            columns=[
                "session_id",
                "base_cohort_id",
                "family",
                "rule_id",
                "current_trades_per_year",
                "current_win_rate",
                "current_mean_return_pct",
                "current_equity_annualized_return_pct_3",
                "current_equity_max_drawdown_pct_3",
                "current_equity_annualized_return_pct_5",
                "current_equity_max_drawdown_pct_5",
                "current_top3_symbol_share",
                "current_top5_symbol_share",
                "current_top3_trade_share",
                "current_top5_trade_share",
                "current_best_month_share",
                "gate_pass_count",
                "passes_user_gate",
            ],
        )
        if not qualified.empty
        else "_No qualified or near-miss rows._",
        "",
        "Lookahead audit:",
        _frame_to_markdown(
            audit_report,
            columns=[
                "session_id",
                "cohort_id",
                "rule_id",
                "trades",
                "all_entries_after_signal",
                "min_signal_to_entry_minutes",
                "mean_entry_gap_vs_signal_close_pct",
                "p95_abs_entry_gap_vs_signal_close_pct",
                "max_abs_entry_gap_vs_signal_close_pct",
                "uses_next_open_fill",
                "thresholds_frozen_on_old",
            ],
        )
        if not audit.empty
        else "_No audit rows._",
    ]
    return "\n".join(lines)


def run() -> dict[str, Path]:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = {
        "session_universe": OUTPUT_DIR / "session_universe.csv",
        "session_thresholds": OUTPUT_DIR / "session_thresholds.csv",
        "base_scope": OUTPUT_DIR / "base_scope.csv",
        "feature_frame": OUTPUT_DIR / "feature_frame.csv",
        "cohort_scope": OUTPUT_DIR / "cohort_scope.csv",
        "coverage": OUTPUT_DIR / "coverage.csv",
        "events": OUTPUT_DIR / "events.csv",
        "rule_summary": OUTPUT_DIR / "rule_summary.csv",
        "selected_rules": OUTPUT_DIR / "selected_rules.csv",
        "qualified_rules": OUTPUT_DIR / "qualified_rules.csv",
        "selected_events": OUTPUT_DIR / "selected_events.csv",
        "selected_monthly": OUTPUT_DIR / "selected_monthly.csv",
        "lookahead_audit": OUTPUT_DIR / "lookahead_audit.csv",
        "report": OUTPUT_DIR / "report.md",
    }

    if paths["session_universe"].exists():
        universe = pd.read_csv(paths["session_universe"], low_memory=False)
    else:
        universe = _load_or_build_session_universe(paths["session_universe"])
        universe.to_csv(paths["session_universe"], index=False)

    categorized, thresholds = _add_regime_categories(universe)
    base_scope = _base_scope(categorized)
    if paths["feature_frame"].exists():
        feature_frame = pd.read_csv(paths["feature_frame"], low_memory=False)
    else:
        feature_frame = _derive_base_features(base_scope)
        feature_frame.to_csv(paths["feature_frame"], index=False)
    cohort_scope = _cohort_scope(feature_frame)
    coverage = _coverage_summary(cohort_scope)

    if paths["events"].exists():
        events = pd.read_csv(paths["events"], low_memory=False)
    else:
        rules: tuple[LongLaunchRule | LongFollowRule | LongLatePushRule, ...] = (
            *_long_launch_rules(),
            *_long_follow_rules(),
            *_long_late_push_rules(),
        )
        events = _build_events(cohort_scope, rules)
        events.to_csv(paths["events"], index=False)

    base_summary = _rule_summary(cohort_scope, events)
    diagnostics = _diagnostic_rows(cohort_scope, events)
    rule_summary = base_summary.merge(
        diagnostics,
        on=["cohort_id", "family", "rule_id", "family_rank", "coverage_current", "coverage_old"],
        how="left",
    )
    rule_summary = _apply_user_gate(rule_summary)
    selected = _select_rules(rule_summary)
    qualified = _qualified_or_near_miss(rule_summary)
    selected_events, selected_monthly = _selected_outputs(events, selected)
    audit = _lookahead_audit(selected_events)

    thresholds.to_csv(paths["session_thresholds"], index=False)
    base_scope.to_csv(paths["base_scope"], index=False)
    cohort_scope.to_csv(paths["cohort_scope"], index=False)
    coverage.to_csv(paths["coverage"], index=False)
    rule_summary.to_csv(paths["rule_summary"], index=False)
    selected.to_csv(paths["selected_rules"], index=False)
    qualified.to_csv(paths["qualified_rules"], index=False)
    selected_events.to_csv(paths["selected_events"], index=False)
    selected_monthly.to_csv(paths["selected_monthly"], index=False)
    audit.to_csv(paths["lookahead_audit"], index=False)

    report = _build_report(thresholds, coverage, selected, qualified, audit)
    paths["report"].write_text(report, encoding="utf-8")
    print("xx00-session-regime-long: done")
    return paths


if __name__ == "__main__":
    output_paths = run()
    for key, value in output_paths.items():
        print(f"{key}: {value}")
