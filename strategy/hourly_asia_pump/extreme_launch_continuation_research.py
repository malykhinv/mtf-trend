from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import median

import pandas as pd

from strategy.hourly_asia_pump.tradeable_second_wave_models import (
    FIVE_MINUTES_MS,
    ONE_MINUTE_MS,
    _close_pos,
    _load_feature_db,
    _load_m1,
    _net_long_return,
    _simulate_exit_1m,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "extreme_launch_continuation_research"

SUPER_SYMBOL = "SUPER/USDT:USDT"
SUPER_TIMESTAMP_MS = 1774483200000  # 2026-03-26 00:00:00 UTC


@dataclass(frozen=True, slots=True)
class CohortSpec:
    cohort_id: str
    min_trigger_return_pct: float
    min_volume_mult: float


@dataclass(frozen=True, slots=True)
class ReclaimRule:
    rule_id: str
    family: str
    max_wait_minutes: int
    min_reset_frac: float
    min_body_frac_range: float
    min_close_pos: float
    min_recent_volume_ratio: float
    max_extension_frac: float
    stop_style: str
    rr_target: float


@dataclass(frozen=True, slots=True)
class LaunchRule:
    rule_id: str
    family: str
    max_wait_minutes: int
    min_reset_frac: float
    min_reclaim_body_frac_range: float
    min_reclaim_volume_ratio: float
    confirm_bars: int
    min_confirm_body_frac_range: float
    min_confirm_close_pos: float
    max_entry_extension_frac: float
    stop_style: str
    rr_target: float


def _cohort_specs() -> tuple[CohortSpec, ...]:
    return (
        CohortSpec("warm_wicky_extreme_r020_v012", 0.02, 12.0),
        CohortSpec("warm_wicky_extreme_r030_v012", 0.03, 12.0),
        CohortSpec("warm_wicky_extreme_r030_v020", 0.03, 20.0),
    )


def _reclaim_rules() -> tuple[ReclaimRule, ...]:
    rules: list[ReclaimRule] = []
    for max_wait_minutes in (20, 30):
        for min_reset_frac in (0.25, 0.40):
            for min_body_frac_range in (0.20, 0.30):
                for min_close_pos in (0.70, 0.85):
                    for min_recent_volume_ratio in (1.25, 1.75):
                        for max_extension_frac in (0.25, 0.50):
                            for stop_style in ("signal_low",):
                                for rr_target in (1.5, 2.0):
                                    rule_id = (
                                        f"reclaim_m{max_wait_minutes}"
                                        f"_r{int(min_reset_frac*100):02d}"
                                        f"_b{int(min_body_frac_range*100):02d}"
                                        f"_c{int(min_close_pos*100):02d}"
                                        f"_v{int(min_recent_volume_ratio*100):03d}"
                                        f"_e{int(max_extension_frac*100):02d}"
                                        f"_{stop_style}"
                                        f"_rr{int(rr_target*10):02d}"
                                    )
                                    rules.append(
                                        ReclaimRule(
                                            rule_id=rule_id,
                                            family="reclaim_close",
                                            max_wait_minutes=max_wait_minutes,
                                            min_reset_frac=min_reset_frac,
                                            min_body_frac_range=min_body_frac_range,
                                            min_close_pos=min_close_pos,
                                            min_recent_volume_ratio=min_recent_volume_ratio,
                                            max_extension_frac=max_extension_frac,
                                            stop_style=stop_style,
                                            rr_target=rr_target,
                                        )
                                    )
    return tuple(rules)


def _launch_rules() -> tuple[LaunchRule, ...]:
    rules: list[LaunchRule] = []
    for max_wait_minutes in (30,):
        for min_reset_frac in (0.25, 0.40):
            for min_reclaim_body_frac_range in (0.10, 0.20):
                for min_reclaim_volume_ratio in (1.00, 1.50):
                    for confirm_bars in (1, 2):
                        for min_confirm_body_frac_range in (0.20, 0.30):
                            for max_entry_extension_frac in (1.00,):
                                for stop_style in ("reclaim_low", "setup_low"):
                                    for rr_target in (1.5, 2.0):
                                        rule_id = (
                                            f"launch_m{max_wait_minutes}"
                                            f"_r{int(min_reset_frac*100):02d}"
                                            f"_rb{int(min_reclaim_body_frac_range*100):02d}"
                                            f"_rv{int(min_reclaim_volume_ratio*100):03d}"
                                            f"_cf{confirm_bars}"
                                            f"_cb{int(min_confirm_body_frac_range*100):02d}"
                                            f"_e{int(max_entry_extension_frac*100):03d}"
                                            f"_{stop_style}"
                                            f"_rr{int(rr_target*10):02d}"
                                        )
                                        rules.append(
                                            LaunchRule(
                                                rule_id=rule_id,
                                                family="launch_break",
                                                max_wait_minutes=max_wait_minutes,
                                                min_reset_frac=min_reset_frac,
                                                min_reclaim_body_frac_range=min_reclaim_body_frac_range,
                                                min_reclaim_volume_ratio=min_reclaim_volume_ratio,
                                                confirm_bars=confirm_bars,
                                                min_confirm_body_frac_range=min_confirm_body_frac_range,
                                                min_confirm_close_pos=0.70,
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
        & (frame["context_archetype"].astype(str) == "context_warm")
        & (frame["impulse_archetype"].astype(str) == "impulse_wicky_spike")
        & (frame["pre_accumulation_type"].astype(str) == "none")
    ].copy()
    scoped["cache_scope"] = scoped["dataset"].map({"current": "current", "old": "old"})
    return scoped.reset_index(drop=True)


def _cohort_scope(frame: pd.DataFrame, spec: CohortSpec) -> pd.DataFrame:
    scoped = frame[
        (pd.to_numeric(frame["trigger_return_pct"], errors="coerce") >= spec.min_trigger_return_pct)
        & (pd.to_numeric(frame["volume_mult"], errors="coerce") >= spec.min_volume_mult)
    ].copy()
    scoped["cohort_id"] = spec.cohort_id
    return scoped.reset_index(drop=True)


def _recent_volume_ratio(volumes: list[float], start_idx: int, idx: int, lookback: int = 5) -> float | None:
    history = [value for value in volumes[max(start_idx, idx - lookback) : idx] if value > 0.0]
    if not history:
        return None
    base = median(history)
    if base <= 0.0:
        return None
    return float(volumes[idx] / base)


def _simulate_reclaim_rule(
    *,
    event_row: pd.Series,
    timestamps: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    rule: ReclaimRule,
) -> dict[str, object] | None:
    start_ts = int(event_row["timestamp_ms"]) + FIVE_MINUTES_MS
    try:
        start_idx = timestamps.index(start_ts)
    except ValueError:
        return None

    trigger_high = float(event_row["trigger_high"])
    trigger_low = float(event_row["trigger_low"])
    trigger_close = float(event_row["trigger_close"])
    trigger_range = max(1e-12, trigger_high - trigger_low)

    rolling_low = float("inf")
    end_idx = min(len(closes), start_idx + rule.max_wait_minutes)
    for idx in range(start_idx, end_idx):
        rolling_low = min(rolling_low, lows[idx])
        if closes[idx] <= trigger_high:
            continue
        body = closes[idx] - opens[idx]
        if body <= 0.0:
            continue
        recent_volume_ratio = _recent_volume_ratio(volumes, start_idx, idx)
        if recent_volume_ratio is None:
            continue
        reset_frac = max(0.0, (trigger_close - rolling_low) / trigger_range)
        body_frac_range = body / trigger_range
        close_pos = _close_pos(highs[idx], lows[idx], closes[idx])
        extension_frac = max(0.0, (closes[idx] - trigger_high) / trigger_range)
        if reset_frac < rule.min_reset_frac:
            continue
        if body_frac_range < rule.min_body_frac_range:
            continue
        if close_pos < rule.min_close_pos:
            continue
        if recent_volume_ratio < rule.min_recent_volume_ratio:
            continue
        if extension_frac > rule.max_extension_frac:
            continue
        stop_price = lows[idx] if rule.stop_style == "signal_low" else rolling_low
        entry_price = closes[idx]
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
            fast_fail_r=0.25,
        )
        return {
            "entry_timestamp_ms": int(timestamps[idx] + ONE_MINUTE_MS),
            "exit_timestamp_ms": int(timestamps[exit_idx] + ONE_MINUTE_MS),
            "entry_price": float(entry_price),
            "stop_price": float(stop_price),
            "initial_risk_pct": float((entry_price - stop_price) / entry_price),
            "exit_price": float(exit_price),
            "exit_reason": exit_reason,
            "exit_return_pct": _net_long_return(entry_price, exit_price),
            "signal_bar_timestamp_ms": int(timestamps[idx]),
            "reset_frac": float(reset_frac),
            "signal_body_frac_range": float(body_frac_range),
            "signal_close_pos": float(close_pos),
            "signal_volume_ratio": float(recent_volume_ratio),
            "entry_extension_frac": float(extension_frac),
        }
    return None


def _simulate_launch_rule(
    *,
    event_row: pd.Series,
    timestamps: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    rule: LaunchRule,
) -> dict[str, object] | None:
    start_ts = int(event_row["timestamp_ms"]) + FIVE_MINUTES_MS
    try:
        start_idx = timestamps.index(start_ts)
    except ValueError:
        return None

    trigger_high = float(event_row["trigger_high"])
    trigger_low = float(event_row["trigger_low"])
    trigger_close = float(event_row["trigger_close"])
    trigger_range = max(1e-12, trigger_high - trigger_low)

    reclaim_idx: int | None = None
    reclaim_low = float("inf")
    rolling_low = float("inf")
    end_idx = min(len(closes), start_idx + rule.max_wait_minutes)
    for idx in range(start_idx, end_idx):
        rolling_low = min(rolling_low, lows[idx])
        if closes[idx] <= trigger_high:
            continue
        body = closes[idx] - opens[idx]
        if body <= 0.0:
            continue
        recent_volume_ratio = _recent_volume_ratio(volumes, start_idx, idx)
        if recent_volume_ratio is None:
            continue
        reset_frac = max(0.0, (trigger_close - rolling_low) / trigger_range)
        body_frac_range = body / trigger_range
        if reset_frac < rule.min_reset_frac:
            continue
        if body_frac_range < rule.min_reclaim_body_frac_range:
            continue
        if recent_volume_ratio < rule.min_reclaim_volume_ratio:
            continue
        reclaim_idx = idx
        reclaim_low = lows[idx]
        break

    if reclaim_idx is None:
        return None

    setup_low = reclaim_low
    confirm_end = min(len(closes), reclaim_idx + rule.confirm_bars + 1)
    for idx in range(reclaim_idx + 1, confirm_end):
        setup_low = min(setup_low, lows[idx])
        prior_high = max(highs[reclaim_idx:idx])
        if closes[idx] <= prior_high:
            continue
        body = closes[idx] - opens[idx]
        if body <= 0.0:
            continue
        recent_volume_ratio = _recent_volume_ratio(volumes, start_idx, idx)
        if recent_volume_ratio is None:
            continue
        body_frac_range = body / trigger_range
        close_pos = _close_pos(highs[idx], lows[idx], closes[idx])
        extension_frac = max(0.0, (closes[idx] - trigger_high) / trigger_range)
        if body_frac_range < rule.min_confirm_body_frac_range:
            continue
        if close_pos < rule.min_confirm_close_pos:
            continue
        if extension_frac > rule.max_entry_extension_frac:
            continue
        stop_price = reclaim_low if rule.stop_style == "reclaim_low" else setup_low
        entry_price = closes[idx]
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
            fast_fail_r=0.25,
        )
        return {
            "entry_timestamp_ms": int(timestamps[idx] + ONE_MINUTE_MS),
            "exit_timestamp_ms": int(timestamps[exit_idx] + ONE_MINUTE_MS),
            "entry_price": float(entry_price),
            "stop_price": float(stop_price),
            "initial_risk_pct": float((entry_price - stop_price) / entry_price),
            "exit_price": float(exit_price),
            "exit_reason": exit_reason,
            "exit_return_pct": _net_long_return(entry_price, exit_price),
            "reclaim_timestamp_ms": int(timestamps[reclaim_idx]),
            "signal_bar_timestamp_ms": int(timestamps[idx]),
            "reclaim_low": float(reclaim_low),
            "confirm_volume_ratio": float(recent_volume_ratio),
            "entry_extension_frac": float(extension_frac),
        }
    return None


def _build_events(scope: pd.DataFrame, rules: tuple[ReclaimRule | LaunchRule, ...]) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    cache: dict[tuple[str, str], pd.DataFrame] = {}
    grouped = scope.groupby(["cache_scope", "symbol"], sort=True)
    total_groups = grouped.ngroups
    for group_index, ((cache_scope, symbol), symbol_frame) in enumerate(grouped, start=1):
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
                f"extreme-launch: progress={group_index}/{total_groups} "
                f"cache={cache_scope} symbol={symbol} events={len(symbol_frame)}"
            )
        for _, event_row in symbol_frame.iterrows():
            for rule in rules:
                if isinstance(rule, ReclaimRule):
                    result = _simulate_reclaim_rule(
                        event_row=event_row,
                        timestamps=timestamps,
                        opens=opens,
                        highs=highs,
                        lows=lows,
                        closes=closes,
                        volumes=volumes,
                        rule=rule,
                    )
                else:
                    result = _simulate_launch_rule(
                        event_row=event_row,
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
                records.append(
                    {
                        "cohort_id": event_row["cohort_id"],
                        "dataset": event_row["dataset"],
                        "symbol": event_row["symbol"],
                        "timestamp_ms": int(event_row["timestamp_ms"]),
                        "rule_id": rule.rule_id,
                        "family": rule.family,
                        **result,
                    }
                )
    return pd.DataFrame(records)


def _cohort_summary(scope: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for cohort_id, cohort_frame in scope.groupby("cohort_id", sort=True):
        for dataset in ("combined", "current", "old"):
            part = cohort_frame if dataset == "combined" else cohort_frame[cohort_frame["dataset"].astype(str) == dataset].copy()
            if part.empty:
                continue
            up30 = pd.to_numeric(part["post_max_up_extension_pct_30m"], errors="coerce")
            rows.append(
                {
                    "cohort_id": cohort_id,
                    "dataset": dataset,
                    "events": int(len(part)),
                    "mean_trigger_return_pct": float(pd.to_numeric(part["trigger_return_pct"], errors="coerce").mean()),
                    "median_trigger_return_pct": float(pd.to_numeric(part["trigger_return_pct"], errors="coerce").median()),
                    "mean_volume_mult": float(pd.to_numeric(part["volume_mult"], errors="coerce").mean()),
                    "mean_up_extension_pct_30m": float(up30.mean()),
                    "hit_2pct_30m": float((up30 >= 0.02).mean()),
                    "hit_4pct_30m": float((up30 >= 0.04).mean()),
                    "super_present": bool(
                        (
                            (part["symbol"].astype(str) == SUPER_SYMBOL)
                            & (pd.to_numeric(part["timestamp_ms"], errors="coerce") == SUPER_TIMESTAMP_MS)
                        ).any()
                    ),
                }
            )
    return pd.DataFrame(rows)


def _rule_summary(scope: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()

    totals: dict[tuple[str, str], int] = {}
    for cohort_id, cohort_frame in scope.groupby("cohort_id", sort=True):
        totals[(cohort_id, "combined")] = int(len(cohort_frame))
        totals[(cohort_id, "current")] = int((cohort_frame["dataset"].astype(str) == "current").sum())
        totals[(cohort_id, "old")] = int((cohort_frame["dataset"].astype(str) == "old").sum())

    rows: list[dict[str, object]] = []
    grouped = events.groupby(["cohort_id", "family", "rule_id"], sort=True)
    for (cohort_id, family, rule_id), rule_events in grouped:
        row: dict[str, object] = {
            "cohort_id": cohort_id,
            "family": family,
            "rule_id": rule_id,
            "super_triggered": bool(
                (
                    (rule_events["symbol"].astype(str) == SUPER_SYMBOL)
                    & (pd.to_numeric(rule_events["timestamp_ms"], errors="coerce") == SUPER_TIMESTAMP_MS)
                ).any()
            ),
        }
        for dataset in ("combined", "current", "old"):
            part = rule_events if dataset == "combined" else rule_events[rule_events["dataset"].astype(str) == dataset].copy()
            total_events = totals[(cohort_id, dataset)]
            row[f"{dataset}_events"] = total_events
            row[f"{dataset}_trades"] = int(len(part))
            row[f"{dataset}_trade_rate"] = float(len(part) / total_events) if total_events > 0 else 0.0
            if part.empty:
                row[f"{dataset}_mean_return_pct"] = None
                row[f"{dataset}_median_return_pct"] = None
                row[f"{dataset}_win_rate"] = None
                row[f"{dataset}_tp_rate"] = None
                row[f"{dataset}_stop_rate"] = None
                continue
            returns = pd.to_numeric(part["exit_return_pct"], errors="coerce")
            row[f"{dataset}_mean_return_pct"] = float(returns.mean())
            row[f"{dataset}_median_return_pct"] = float(returns.median())
            row[f"{dataset}_win_rate"] = float((returns > 0.0).mean())
            row[f"{dataset}_tp_rate"] = float((part["exit_reason"].astype(str) == "tp").mean())
            row[f"{dataset}_stop_rate"] = float((part["exit_reason"].astype(str) == "stop").mean())
        current_mean = row.get("current_mean_return_pct")
        old_mean = row.get("old_mean_return_pct")
        current_trades = int(row.get("current_trades") or 0)
        old_trades = int(row.get("old_trades") or 0)
        if current_mean is None or old_mean is None or current_trades == 0 or old_trades == 0:
            row["robust_score"] = None
        else:
            row["robust_score"] = float(min(current_mean, old_mean) * (current_trades + old_trades))
        if row["super_triggered"]:
            super_rows = rule_events[
                (rule_events["symbol"].astype(str) == SUPER_SYMBOL)
                & (pd.to_numeric(rule_events["timestamp_ms"], errors="coerce") == SUPER_TIMESTAMP_MS)
            ].copy()
            super_rows["entry_utc"] = pd.to_datetime(
                pd.to_numeric(super_rows["entry_timestamp_ms"], errors="coerce"),
                unit="ms",
                utc=True,
                errors="coerce",
            )
            row["super_entry_utc"] = str(super_rows["entry_utc"].min())
            row["super_entry_price"] = float(pd.to_numeric(super_rows["entry_price"], errors="coerce").iloc[0])
        else:
            row["super_entry_utc"] = None
            row["super_entry_price"] = None
        rows.append(row)
    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary
    return summary.sort_values(
        ["super_triggered", "robust_score", "combined_mean_return_pct", "combined_trades"],
        ascending=[False, False, False, False],
    ).reset_index(drop=True)


def _super_timeline() -> pd.DataFrame:
    cache: dict[tuple[str, str], pd.DataFrame] = {}
    candles = _load_m1("current", SUPER_SYMBOL, cache)
    if candles.empty:
        return pd.DataFrame()
    timeline = candles.copy()
    timeline["timestamp_utc"] = pd.to_datetime(pd.to_numeric(timeline["timestamp"], errors="coerce"), unit="ms", utc=True, errors="coerce")
    timeline = timeline[
        (timeline["timestamp_utc"] >= "2026-03-26 00:05:00+00:00")
        & (timeline["timestamp_utc"] <= "2026-03-26 00:35:00+00:00")
    ].copy()

    feature_db = _load_feature_db()
    super_row = feature_db[
        (feature_db["symbol"].astype(str) == SUPER_SYMBOL)
        & (pd.to_numeric(feature_db["timestamp_ms"], errors="coerce") == SUPER_TIMESTAMP_MS)
    ].copy()
    if super_row.empty:
        return pd.DataFrame()
    event = super_row.iloc[0]
    trigger_high = float(event["trigger_high"])
    trigger_low = float(event["trigger_low"])
    trigger_close = float(event["trigger_close"])
    trigger_range = max(1e-12, trigger_high - trigger_low)

    opens = pd.to_numeric(timeline["open"], errors="coerce").astype(float).tolist()
    highs = pd.to_numeric(timeline["high"], errors="coerce").astype(float).tolist()
    lows = pd.to_numeric(timeline["low"], errors="coerce").astype(float).tolist()
    closes = pd.to_numeric(timeline["close"], errors="coerce").astype(float).tolist()
    volumes = pd.to_numeric(timeline["volume"], errors="coerce").astype(float).tolist()

    rolling_low = float("inf")
    recent_volume_ratios: list[float | None] = []
    reset_fracs: list[float] = []
    body_fracs: list[float] = []
    close_pos_values: list[float] = []
    extension_fracs: list[float] = []
    for idx in range(len(closes)):
        rolling_low = min(rolling_low, lows[idx])
        recent_volume_ratios.append(_recent_volume_ratio(volumes, 0, idx))
        reset_fracs.append(max(0.0, (trigger_close - rolling_low) / trigger_range))
        body_fracs.append(max(0.0, closes[idx] - opens[idx]) / trigger_range)
        close_pos_values.append(_close_pos(highs[idx], lows[idx], closes[idx]))
        extension_fracs.append(max(0.0, (closes[idx] - trigger_high) / trigger_range))

    timeline["close_above_trigger_high"] = pd.to_numeric(timeline["close"], errors="coerce") > trigger_high
    timeline["recent_volume_ratio"] = recent_volume_ratios
    timeline["reset_frac_from_trigger_close"] = reset_fracs
    timeline["body_frac_of_trigger_range"] = body_fracs
    timeline["close_pos"] = close_pos_values
    timeline["extension_frac_above_trigger_high"] = extension_fracs
    return timeline.reset_index(drop=True)


def _frame_to_block(frame: pd.DataFrame, rows: int) -> str:
    if frame.empty:
        return "_empty_"
    return "```\n" + frame.head(rows).to_string(index=False) + "\n```"


def _compress_super_hits(rule_summary: pd.DataFrame) -> pd.DataFrame:
    if rule_summary.empty:
        return pd.DataFrame()
    super_hits = rule_summary[rule_summary["super_triggered"].astype(bool)].copy()
    if super_hits.empty:
        return super_hits
    grouped = (
        super_hits.groupby(["family", "super_entry_utc", "super_entry_price"], dropna=False)
        .agg(
            representative_rule_id=("rule_id", "first"),
            equivalent_rules=("rule_id", "count"),
            best_combined_mean_return_pct=("combined_mean_return_pct", "max"),
            best_combined_trades=("combined_trades", "max"),
            best_current_trades=("current_trades", "max"),
            best_old_trades=("old_trades", "max"),
        )
        .reset_index()
    )
    return grouped.sort_values(
        ["best_combined_mean_return_pct", "best_combined_trades"],
        ascending=[False, False],
    ).reset_index(drop=True)


def _build_report(cohort_summary: pd.DataFrame, rule_summary: pd.DataFrame, super_hits: pd.DataFrame) -> str:
    lines: list[str] = []
    lines.append("# Extreme Launch Continuation Research")
    lines.append("")
    lines.append("Research target:")
    lines.append("- Asia long, top-of-hour 5m anomalies")
    lines.append("- context_warm + impulse_wicky_spike + no pre-accumulation")
    lines.append("- continuation entries without lookahead after the first 5m pump already closed")
    lines.append("")
    lines.append("Important anchor case:")
    lines.append("- SUPER event in cache is 2026-03-26 00:00:00 UTC")
    lines.append("- if the chart timezone is UTC+3, that same launch is shown at 03:00")
    lines.append("")
    lines.append("## Cohort Summary")
    lines.append("")
    lines.append(_frame_to_block(cohort_summary, 20))
    lines.append("")
    lines.append("## Top Robust Rules")
    lines.append("")
    robust = rule_summary[
        (pd.to_numeric(rule_summary["current_trades"], errors="coerce") >= 5)
        & (pd.to_numeric(rule_summary["old_trades"], errors="coerce") >= 2)
    ].copy()
    if robust.empty:
        lines.append("No rule passed the strict current/old filter yet. Best evidence on this run is below:")
        lines.append("")
        evidence = rule_summary[pd.to_numeric(rule_summary["combined_trades"], errors="coerce") >= 3].copy()
        evidence = evidence.sort_values(
            ["combined_mean_return_pct", "combined_trades"],
            ascending=[False, False],
        )
        lines.append(
            _frame_to_block(
                evidence[
                    [
                        "cohort_id",
                        "family",
                        "rule_id",
                        "combined_trades",
                        "combined_trade_rate",
                        "combined_mean_return_pct",
                        "combined_win_rate",
                        "current_trades",
                        "current_mean_return_pct",
                        "old_trades",
                        "old_mean_return_pct",
                        "super_triggered",
                        "super_entry_utc",
                    ]
                ]
                if not evidence.empty
                else evidence,
                20,
            )
        )
    else:
        robust = robust.sort_values(
            ["robust_score", "combined_mean_return_pct", "combined_trades"],
            ascending=[False, False, False],
        )
        lines.append(
            _frame_to_block(
                robust[
                    [
                        "cohort_id",
                        "family",
                        "rule_id",
                        "combined_trades",
                        "combined_trade_rate",
                        "combined_mean_return_pct",
                        "combined_win_rate",
                        "current_trades",
                        "current_mean_return_pct",
                        "old_trades",
                        "old_mean_return_pct",
                        "super_triggered",
                        "super_entry_utc",
                    ]
                ],
                20,
            )
        )
    lines.append("")
    lines.append("## SUPER-Matching Rules")
    lines.append("")
    lines.append(
        _frame_to_block(
            super_hits[
                [
                    "family",
                    "super_entry_utc",
                    "super_entry_price",
                    "equivalent_rules",
                    "best_combined_mean_return_pct",
                    "best_combined_trades",
                    "best_current_trades",
                    "best_old_trades",
                    "representative_rule_id",
                ]
            ]
            if not super_hits.empty
            else super_hits,
            20,
        )
    )
    lines.append("")
    lines.append("## Reading Guide")
    lines.append("")
    lines.append("- `combined_trade_rate` = share of cohort events where a rule actually generated a trade")
    lines.append("- `combined_mean_return_pct` = net return after fee model from entry to managed exit")
    lines.append("- `robust_score` rewards rules that stay positive in both `current` and `old`")
    lines.append("- `super_triggered` marks rules that would have participated in the anchor SUPER launch")
    return "\n".join(lines)


def run() -> dict[str, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    base = _base_scope(_load_feature_db())
    scopes = [_cohort_scope(base, spec) for spec in _cohort_specs()]
    scope = pd.concat(scopes, ignore_index=True)

    rules: tuple[ReclaimRule | LaunchRule, ...] = (*_reclaim_rules(), *_launch_rules())
    events = _build_events(scope, rules)
    if not events.empty:
        for column in ("timestamp_ms", "entry_timestamp_ms", "exit_timestamp_ms", "signal_bar_timestamp_ms", "reclaim_timestamp_ms"):
            if column in events.columns:
                events[column.replace("_ms", "_utc")] = pd.to_datetime(
                    pd.to_numeric(events[column], errors="coerce"),
                    unit="ms",
                    utc=True,
                    errors="coerce",
                )

    cohort_summary = _cohort_summary(scope)
    rule_summary = _rule_summary(scope, events)
    super_hits = _compress_super_hits(rule_summary)

    super_timeline = _super_timeline()
    report = _build_report(cohort_summary, rule_summary, super_hits)

    paths = {
        "cohort_summary": OUTPUT_DIR / "cohort_summary.csv",
        "rule_summary": OUTPUT_DIR / "rule_summary.csv",
        "signal_events": OUTPUT_DIR / "signal_events.csv",
        "super_case_timeline": OUTPUT_DIR / "super_case_timeline.csv",
        "super_hits": OUTPUT_DIR / "super_hits.csv",
        "report": OUTPUT_DIR / "report.md",
    }
    cohort_summary.to_csv(paths["cohort_summary"], index=False)
    rule_summary.to_csv(paths["rule_summary"], index=False)
    events.to_csv(paths["signal_events"], index=False)
    super_timeline.to_csv(paths["super_case_timeline"], index=False)
    super_hits.to_csv(paths["super_hits"], index=False)
    paths["report"].write_text(report, encoding="utf-8")
    return paths


if __name__ == "__main__":
    output_paths = run()
    for key, value in output_paths.items():
        print(f"{key}: {value}")
