from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from pathlib import Path

import pandas as pd

from strategy.hourly_asia_pump.second_wave_execution_search import (
    ManagementSpec,
    _simulate_managed_exit,
)
from strategy.hourly_asia_pump.static_combo import (
    _calendar_months_from_frames,
    _frame_to_markdown,
    _summarize_events,
)
from strategy.hourly_asia_pump.tradeable_second_wave_models import (
    FIVE_MINUTES_MS,
    ONE_MINUTE_MS,
    _load_feature_db,
    _load_m1,
    _prepare_scope,
)
from strategy.hourly_asia_pump.unified_edge import _simulate_equity_risk_metrics

REPO_ROOT = Path(__file__).resolve().parents[2]
SIGNALS_PATH = (
    REPO_ROOT
    / ".output"
    / "results_prev_year_5m"
    / "second_wave_online_signal_diagnostics"
    / "second_wave_start_signals.csv"
)
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "second_wave_signal_trade_lab"

FEE_RATE = 0.0004


@dataclass(frozen=True, slots=True)
class LongRule:
    rule_id: str
    min_body_ratio: float
    min_volume_ratio: float
    min_buyer_score: float
    min_extension_frac: float
    max_seller_pressure: float
    max_pullback_frac: float


@dataclass(frozen=True, slots=True)
class LongModel:
    model_id: str
    rule: LongRule
    management: ManagementSpec


@dataclass(frozen=True, slots=True)
class ShortRule:
    rule_id: str
    context_archetype: str
    max_body_ratio: float
    max_volume_ratio: float
    max_buyer_score: float
    max_extension_frac: float
    min_seller_pressure: float
    max_start_delay_min: int


@dataclass(frozen=True, slots=True)
class ShortEntrySpec:
    entry_id: str
    style: str
    confirm_minutes: int


@dataclass(frozen=True, slots=True)
class ShortStopSpec:
    stop_id: str
    style: str


@dataclass(frozen=True, slots=True)
class ShortManagementSpec:
    management_id: str
    final_target_r: float | None
    be_after_r: float | None
    trail_after_r: float | None
    fast_fail_minutes: int
    fast_fail_r: float
    max_hold_minutes: int


@dataclass(frozen=True, slots=True)
class ShortModel:
    model_id: str
    rule: ShortRule
    entry: ShortEntrySpec
    stop: ShortStopSpec
    management: ShortManagementSpec


def _load_signal_events() -> pd.DataFrame:
    signals = pd.read_csv(SIGNALS_PATH, low_memory=False)
    signals["found_start_break"] = signals["found_start_break"].astype(str).str.lower().isin(["true", "1"])
    signals = signals[signals["found_start_break"].astype(bool)].copy()
    for column in [
        "timestamp_ms",
        "start_delay_min",
        "break_body_ratio",
        "break_volume_ratio",
        "break_close_pos",
        "break_extension_frac",
        "pre_break_pullback_frac",
        "seller_pressure_score",
        "buyer_break_score",
        "pre_base_range_pct_60m",
        "pre_base_drift_pct_60m",
        "trigger_return_pct",
        "range_atr",
        "volume_mult",
        "close_to_high_frac",
    ]:
        if column in signals.columns:
            signals[column] = pd.to_numeric(signals[column], errors="coerce")

    scoped = _prepare_scope(_load_feature_db())[
        [
            "dataset",
            "symbol",
            "timestamp_ms",
            "month_utc",
            "date_utc",
            "cache_scope",
            "trigger_open",
            "trigger_high",
            "trigger_low",
            "trigger_close",
            "trigger_volume",
            "category_id",
            "pre_accumulation_type",
        ]
    ].copy()
    merged = signals.merge(scoped, on=["dataset", "symbol", "timestamp_ms"], how="inner", validate="one_to_one")
    merged["event_id"] = (
        merged["dataset"].astype(str)
        + "|"
        + merged["symbol"].astype(str)
        + "|"
        + merged["timestamp_ms"].astype("int64").astype(str)
    )
    return merged.reset_index(drop=True)


def _long_management_specs() -> tuple[ManagementSpec, ...]:
    return (
        ManagementSpec("fx15", 1.5, None, 0.0, False, False, None, None, 8, 0.25, 90),
        ManagementSpec("fx20", 2.0, None, 0.0, False, False, None, None, 8, 0.25, 120),
        ManagementSpec("fx25", 2.5, None, 0.0, False, False, None, None, 10, 0.35, 150),
        ManagementSpec("be10_trail", None, None, 0.0, False, False, 1.0, 1.0, 8, 0.25, 120),
        ManagementSpec("p10_be_trail", None, 1.0, 0.50, True, True, None, None, 8, 0.25, 150),
    )


def _long_rules() -> tuple[LongRule, ...]:
    rules: list[LongRule] = []
    for body, volume, buyer, ext, seller, pull in product(
        (1.2, 1.5, 2.0),
        (1.0, 1.2, 1.5),
        (0.24, 0.30, 0.40),
        (0.15, 0.20, 0.25),
        (0.03, 0.05, 0.08),
        (0.20, 0.25, 0.35),
    ):
        rules.append(
            LongRule(
                rule_id=f"warm_b{int(body*100):03d}_v{int(volume*100):03d}_q{int(buyer*100):03d}_e{int(ext*100):03d}_s{int(seller*100):03d}_p{int(pull*100):03d}",
                min_body_ratio=body,
                min_volume_ratio=volume,
                min_buyer_score=buyer,
                min_extension_frac=ext,
                max_seller_pressure=seller,
                max_pullback_frac=pull,
            )
        )
    return tuple(rules)


def _short_rules() -> tuple[ShortRule, ...]:
    rules: list[ShortRule] = []
    for context in ("context_warm", "context_overheated"):
        for body, volume, buyer, ext, seller, delay in product(
            (0.8, 1.0, 1.2),
            (0.8, 1.0),
            (0.15, 0.20, 0.24),
            (0.05, 0.10),
            (0.03, 0.05),
            (1, 2),
        ):
            rules.append(
                ShortRule(
                    rule_id=f"{context}_weak_b{int(body*100):03d}_v{int(volume*100):03d}_q{int(buyer*100):03d}_e{int(ext*100):03d}_s{int(seller*100):03d}_d{delay}",
                    context_archetype=context,
                    max_body_ratio=body,
                    max_volume_ratio=volume,
                    max_buyer_score=buyer,
                    max_extension_frac=ext,
                    min_seller_pressure=seller,
                    max_start_delay_min=delay,
                )
            )
    return tuple(rules)


def _short_entry_specs() -> tuple[ShortEntrySpec, ...]:
    return (
        ShortEntrySpec("next_open", "next_open", 0),
        ShortEntrySpec("below_trigger_nopen", "below_trigger_next_open", 3),
        ShortEntrySpec("below_siglow_nopen", "below_signal_low_next_open", 3),
    )


def _short_stop_specs() -> tuple[ShortStopSpec, ...]:
    return (
        ShortStopSpec("sig_high", "signal_high"),
        ShortStopSpec("sig_high_buf10", "signal_high_buf10"),
    )


def _short_management_specs() -> tuple[ShortManagementSpec, ...]:
    return (
        ShortManagementSpec("fx15", 1.5, None, None, 8, 0.25, 120),
        ShortManagementSpec("fx20", 2.0, None, None, 8, 0.25, 150),
        ShortManagementSpec("fx25", 2.5, None, None, 10, 0.35, 180),
        ShortManagementSpec("be10_trail", None, 1.0, 1.0, 8, 0.25, 180),
    )


def _long_models() -> tuple[LongModel, ...]:
    models: list[LongModel] = []
    for rule in _long_rules():
        for management in _long_management_specs():
            models.append(LongModel(model_id=f"{rule.rule_id}__{management.management_id}", rule=rule, management=management))
    return tuple(models)


def _short_models() -> tuple[ShortModel, ...]:
    models: list[ShortModel] = []
    for rule in _short_rules():
        for entry in _short_entry_specs():
            for stop in _short_stop_specs():
                for management in _short_management_specs():
                    models.append(
                        ShortModel(
                            model_id=f"{rule.rule_id}__{entry.entry_id}__{stop.stop_id}__{management.management_id}",
                            rule=rule,
                            entry=entry,
                            stop=stop,
                            management=management,
                        )
                    )
    return tuple(models)


def _net_short_return(entry_price: float, exit_price: float) -> float:
    if entry_price <= 0.0 or exit_price <= 0.0:
        return 0.0
    return float((entry_price * (1.0 - FEE_RATE) / (exit_price * (1.0 + FEE_RATE))) - 1.0)


def _simulate_short_exit(
    *,
    highs: list[float],
    lows: list[float],
    closes: list[float],
    entry_idx: int,
    entry_price: float,
    stop_price: float,
    management: ShortManagementSpec,
) -> tuple[int, float, str]:
    risk = stop_price - entry_price
    if risk <= 0.0:
        return entry_idx, 0.0, "invalid"
    final_price = entry_price - (risk * management.final_target_r) if management.final_target_r is not None else None
    current_stop = float(stop_price)
    lowest_low = entry_price
    last_idx = min(len(closes) - 1, entry_idx + management.max_hold_minutes)
    fast_fail_idx = min(len(closes) - 1, entry_idx + management.fast_fail_minutes)
    trail_active = False
    be_armed = False

    for idx in range(entry_idx + 1, last_idx + 1):
        if trail_active and idx - 1 > entry_idx:
            current_stop = min(current_stop, highs[idx - 1])

        high_price = highs[idx]
        low_price = lows[idx]
        close_price = closes[idx]

        if high_price >= current_stop:
            return idx, _net_short_return(entry_price, current_stop), "stop" if not trail_active else "trail_stop"

        lowest_low = min(lowest_low, low_price)

        if management.be_after_r is not None and not be_armed and low_price <= entry_price - risk * management.be_after_r:
            current_stop = min(current_stop, entry_price)
            be_armed = True
        if management.trail_after_r is not None and not trail_active and low_price <= entry_price - risk * management.trail_after_r:
            trail_active = True

        if final_price is not None and low_price <= final_price:
            return idx, _net_short_return(entry_price, final_price), "target"

        if idx >= fast_fail_idx and lowest_low > (entry_price - risk * management.fast_fail_r):
            return idx, _net_short_return(entry_price, close_price), "fast_fail"

    return last_idx, _net_short_return(entry_price, closes[last_idx]), "time_exit"


def _concentration_metrics(frame: pd.DataFrame) -> dict[str, float]:
    if frame.empty:
        return {"top1_symbol_share": 0.0, "best_month_share": 0.0}
    total = float(pd.to_numeric(frame["exit_return_pct"], errors="coerce").sum())
    if abs(total) <= 1e-12:
        return {"top1_symbol_share": 0.0, "best_month_share": 0.0}
    by_symbol = (
        frame.groupby("symbol", as_index=False)["exit_return_pct"]
        .sum()
        .sort_values("exit_return_pct", ascending=False)
        .reset_index(drop=True)
    )
    by_month = (
        frame.groupby("month_utc", as_index=False)["exit_return_pct"]
        .sum()
        .sort_values("exit_return_pct", ascending=False)
        .reset_index(drop=True)
    )
    return {
        "top1_symbol_share": float(by_symbol["exit_return_pct"].iloc[0] / total),
        "best_month_share": float(by_month["exit_return_pct"].iloc[0] / total),
    }


def _metrics(frame: pd.DataFrame) -> dict[str, float | int]:
    if frame.empty:
        return {
            "trades": 0,
            "mean_return_pct": 0.0,
            "win_rate": 0.0,
            "annualized_unit_pnl_pct": 0.0,
            "equity_annualized_return_pct_5": 0.0,
            "equity_max_drawdown_pct_5": 0.0,
            "positive_months_count": 0,
            "stable_positive_months_count": 0,
            "top1_symbol_share": 0.0,
            "best_month_share": 0.0,
        }
    calendar_months = _calendar_months_from_frames(frame)
    summary = _summarize_events(frame, calendar_months=calendar_months)
    equity, _ = _simulate_equity_risk_metrics(frame, calendar_months=calendar_months, risk_fraction=0.05)
    result = {
        "trades": int(len(frame)),
        "mean_return_pct": float(summary["mean_return_pct"]),
        "win_rate": float(summary["win_rate"]),
        "annualized_unit_pnl_pct": float(summary["annualized_unit_pnl_pct"]),
        "equity_annualized_return_pct_5": float(equity["equity_annualized_return_pct"]),
        "equity_max_drawdown_pct_5": float(equity["equity_max_drawdown_pct"]),
        "positive_months_count": int(equity["equity_positive_months_count"]),
        "stable_positive_months_count": int(equity["equity_stable_positive_months_count"]),
    }
    result.update(_concentration_metrics(frame))
    return result


def _event_state(row: pd.Series, cache: dict[tuple[str, str], pd.DataFrame]) -> dict[str, object] | None:
    cache_scope = str(row["cache_scope"])
    symbol = str(row["symbol"])
    m1 = _load_m1(cache_scope, symbol, cache)
    if m1.empty:
        return None

    timestamps = pd.to_numeric(m1["timestamp"], errors="coerce").astype("int64").tolist()
    opens = pd.to_numeric(m1["open"], errors="coerce").astype(float).tolist()
    highs = pd.to_numeric(m1["high"], errors="coerce").astype(float).tolist()
    lows = pd.to_numeric(m1["low"], errors="coerce").astype(float).tolist()
    closes = pd.to_numeric(m1["close"], errors="coerce").astype(float).tolist()

    start_ts = int(row["timestamp_ms"]) + FIVE_MINUTES_MS
    signal_delay_min = int(row["start_delay_min"])
    signal_ts = start_ts + (signal_delay_min - 1) * ONE_MINUTE_MS
    try:
        signal_idx = timestamps.index(signal_ts)
    except ValueError:
        return None
    if signal_idx + 1 >= len(opens):
        return None

    signal_open = float(opens[signal_idx])
    signal_high = float(highs[signal_idx])
    signal_low = float(lows[signal_idx])
    signal_close = float(closes[signal_idx])
    trigger_high = float(row["trigger_high"])
    trigger_low = float(row["trigger_low"])
    trigger_range = max(1e-12, trigger_high - trigger_low)
    signal_range = max(1e-12, signal_high - signal_low)

    return {
        "timestamps": timestamps,
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "closes": closes,
        "signal_idx": signal_idx,
        "signal_open": signal_open,
        "signal_high": signal_high,
        "signal_low": signal_low,
        "signal_close": signal_close,
        "signal_range": signal_range,
        "trigger_high": trigger_high,
        "trigger_low": trigger_low,
        "trigger_range": trigger_range,
    }


def _match_long_rule(row: pd.Series, rule: LongRule) -> bool:
    return bool(
        str(row["context_archetype"]) == "context_warm"
        and float(row["break_body_ratio"]) >= rule.min_body_ratio
        and float(row["break_volume_ratio"]) >= rule.min_volume_ratio
        and float(row["buyer_break_score"]) >= rule.min_buyer_score
        and float(row["break_extension_frac"]) >= rule.min_extension_frac
        and float(row["seller_pressure_score"]) <= rule.max_seller_pressure
        and float(row["pre_break_pullback_frac"]) <= rule.max_pullback_frac
    )


def _match_short_rule(row: pd.Series, rule: ShortRule) -> bool:
    return bool(
        str(row["context_archetype"]) == rule.context_archetype
        and float(row["break_body_ratio"]) <= rule.max_body_ratio
        and float(row["break_volume_ratio"]) <= rule.max_volume_ratio
        and float(row["buyer_break_score"]) <= rule.max_buyer_score
        and float(row["break_extension_frac"]) <= rule.max_extension_frac
        and float(row["seller_pressure_score"]) >= rule.min_seller_pressure
        and float(row["start_delay_min"]) <= rule.max_start_delay_min
    )


def _simulate_long_event(row: pd.Series, state: dict[str, object], model: LongModel) -> dict[str, object] | None:
    if not _match_long_rule(row, model.rule):
        return None
    entry_idx = int(state["signal_idx"]) + 1
    entry_price = float(state["opens"][entry_idx])
    stop_price = float(state["signal_low"])
    if stop_price >= entry_price:
        return None
    exit_idx, exit_return_pct, exit_reason, partial_taken = _simulate_managed_exit(
        highs=state["highs"],
        lows=state["lows"],
        closes=state["closes"],
        entry_idx=entry_idx,
        entry_price=entry_price,
        stop_price=stop_price,
        management_spec=model.management,
    )
    return {
        "strategy_family": "warm_long_strong_break",
        "context_archetype": "context_warm",
        "model_id": model.model_id,
        "rule_id": model.rule.rule_id,
        "entry_id": "next_open",
        "stop_id": "signal_low",
        "management_id": model.management.management_id,
        "dataset": row["dataset"],
        "symbol": row["symbol"],
        "timestamp_ms": int(row["timestamp_ms"]),
        "month_utc": row["month_utc"],
        "date_utc": row["date_utc"],
        "entry_timestamp_ms": int(state["timestamps"][entry_idx] + ONE_MINUTE_MS),
        "entry_price": float(entry_price),
        "initial_stop_price": float(stop_price),
        "initial_risk_pct": float((entry_price - stop_price) / entry_price),
        "exit_timestamp_ms": int(state["timestamps"][exit_idx] + ONE_MINUTE_MS),
        "exit_price": float(state["closes"][exit_idx]),
        "exit_reason": exit_reason,
        "exit_return_pct": float(exit_return_pct),
        "partial_taken": int(partial_taken),
        "start_delay_min": int(row["start_delay_min"]),
        "break_body_ratio": float(row["break_body_ratio"]),
        "break_volume_ratio": float(row["break_volume_ratio"]),
        "buyer_break_score": float(row["buyer_break_score"]),
        "seller_pressure_score": float(row["seller_pressure_score"]),
        "break_extension_frac": float(row["break_extension_frac"]),
        "pre_break_pullback_frac": float(row["pre_break_pullback_frac"]),
    }


def _find_short_entry(row: pd.Series, state: dict[str, object], entry: ShortEntrySpec) -> tuple[int, float, str] | None:
    signal_idx = int(state["signal_idx"])
    opens = state["opens"]
    closes = state["closes"]
    trigger_high = float(state["trigger_high"])
    signal_low = float(state["signal_low"])

    if entry.style == "next_open":
        entry_idx = signal_idx + 1
        if entry_idx >= len(opens):
            return None
        return entry_idx, float(opens[entry_idx]), "next_open"

    if entry.style == "below_trigger_next_open":
        for idx in range(signal_idx + 1, min(len(closes) - 1, signal_idx + 1 + entry.confirm_minutes)):
            if float(closes[idx]) < trigger_high:
                return idx + 1, float(opens[idx + 1]), "below_trigger_next_open"
        return None

    if entry.style == "below_signal_low_next_open":
        for idx in range(signal_idx + 1, min(len(closes) - 1, signal_idx + 1 + entry.confirm_minutes)):
            if float(closes[idx]) < signal_low:
                return idx + 1, float(opens[idx + 1]), "below_signal_low_next_open"
        return None

    return None


def _short_stop_price(state: dict[str, object], stop: ShortStopSpec) -> float:
    signal_high = float(state["signal_high"])
    signal_range = float(state["signal_range"])
    if stop.style == "signal_high":
        return signal_high
    if stop.style == "signal_high_buf10":
        return signal_high + signal_range * 0.10
    raise ValueError(f"Unknown short stop style: {stop.style}")


def _simulate_short_event(row: pd.Series, state: dict[str, object], model: ShortModel) -> dict[str, object] | None:
    if not _match_short_rule(row, model.rule):
        return None
    entry_info = _find_short_entry(row, state, model.entry)
    if entry_info is None:
        return None
    entry_idx, entry_price, entry_detail = entry_info
    stop_price = _short_stop_price(state, model.stop)
    if stop_price <= entry_price:
        return None
    exit_idx, exit_return_pct, exit_reason = _simulate_short_exit(
        highs=state["highs"],
        lows=state["lows"],
        closes=state["closes"],
        entry_idx=entry_idx,
        entry_price=entry_price,
        stop_price=stop_price,
        management=model.management,
    )
    return {
        "strategy_family": "weak_break_short",
        "context_archetype": str(row["context_archetype"]),
        "model_id": model.model_id,
        "rule_id": model.rule.rule_id,
        "entry_id": model.entry.entry_id,
        "stop_id": model.stop.stop_id,
        "management_id": model.management.management_id,
        "dataset": row["dataset"],
        "symbol": row["symbol"],
        "timestamp_ms": int(row["timestamp_ms"]),
        "month_utc": row["month_utc"],
        "date_utc": row["date_utc"],
        "entry_timestamp_ms": int(state["timestamps"][entry_idx] + ONE_MINUTE_MS),
        "entry_price": float(entry_price),
        "initial_stop_price": float(stop_price),
        "initial_risk_pct": float((stop_price - entry_price) / entry_price),
        "exit_timestamp_ms": int(state["timestamps"][exit_idx] + ONE_MINUTE_MS),
        "exit_price": float(state["closes"][exit_idx]),
        "exit_reason": exit_reason,
        "exit_return_pct": float(exit_return_pct),
        "entry_detail": entry_detail,
        "start_delay_min": int(row["start_delay_min"]),
        "break_body_ratio": float(row["break_body_ratio"]),
        "break_volume_ratio": float(row["break_volume_ratio"]),
        "buyer_break_score": float(row["buyer_break_score"]),
        "seller_pressure_score": float(row["seller_pressure_score"]),
        "break_extension_frac": float(row["break_extension_frac"]),
        "pre_break_pullback_frac": float(row["pre_break_pullback_frac"]),
    }


def _build_events(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    long_models = _long_models()
    short_models = _short_models()
    cache: dict[tuple[str, str], pd.DataFrame] = {}
    long_records: list[dict[str, object]] = []
    short_records: list[dict[str, object]] = []

    grouped = frame.groupby(["cache_scope", "symbol"], sort=True)
    total = grouped.ngroups
    for index, ((cache_scope, symbol), scoped) in enumerate(grouped, start=1):
        if index == 1 or index % 25 == 0 or index == total:
            print(f"second-wave-signal-trade-lab: progress={index}/{total} cache={cache_scope} symbol={symbol} events={len(scoped)}")
        for _, row in scoped.iterrows():
            state = _event_state(row, cache)
            if state is None:
                continue
            for model in long_models:
                simulated = _simulate_long_event(row, state, model)
                if simulated is not None:
                    long_records.append(simulated)
            for model in short_models:
                simulated = _simulate_short_event(row, state, model)
                if simulated is not None:
                    short_records.append(simulated)

    return pd.DataFrame(long_records), pd.DataFrame(short_records)


def _summaries(events: pd.DataFrame, *, family: str) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    group_cols = ["context_archetype", "model_id", "rule_id", "entry_id", "stop_id", "management_id"]
    for group_key, scoped in events.groupby(group_cols, sort=True):
        current = scoped[scoped["dataset"].astype(str) == "current"].copy()
        old = scoped[scoped["dataset"].astype(str) == "old"].copy()
        combined = scoped.copy()
        row = {column: value for column, value in zip(group_cols, group_key)}
        row["strategy_family"] = family
        row["current_count"] = int(len(current))
        row["old_count"] = int(len(old))
        current_metrics = _metrics(current)
        old_metrics = _metrics(old)
        combined_metrics = _metrics(combined)
        for prefix, metrics in (("current", current_metrics), ("old", old_metrics), ("combined", combined_metrics)):
            for key, value in metrics.items():
                row[f"{prefix}_{key}"] = value
        row["score"] = (
            float(row["current_mean_return_pct"]) * 120.0
            + float(row["old_mean_return_pct"]) * 100.0
            + float(row["combined_equity_annualized_return_pct_5"]) * 2.5
            - float(row["combined_equity_max_drawdown_pct_5"]) * 12.0
            - float(row["combined_top1_symbol_share"]) * 8.0
            - float(row["combined_best_month_share"]) * 5.0
        )
        row["selection_status"] = "not_ready"
        if (
            row["current_count"] >= 15
            and row["old_count"] >= 8
            and float(row["current_mean_return_pct"]) > 0.0
            and float(row["old_mean_return_pct"]) > 0.0
            and float(row["combined_equity_annualized_return_pct_5"]) > 0.0
            and float(row["combined_equity_max_drawdown_pct_5"]) <= 0.20
        ):
            row["selection_status"] = "surviving"
        rows.append(row)
    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary
    return summary.sort_values(
        ["selection_status", "score", "combined_mean_return_pct", "combined_equity_annualized_return_pct_5"],
        ascending=[False, False, False, False],
    ).reset_index(drop=True)


def _report(long_summary: pd.DataFrame, short_summary: pd.DataFrame) -> str:
    def _table(frame: pd.DataFrame) -> str:
        if frame.empty:
            return "_empty_"
        return "```\n" + frame.to_string(index=False) + "\n```"

    lines: list[str] = []
    lines.append("# Second-Wave Signal Trade Lab")
    lines.append("")
    lines.append("Что проверялось:")
    lines.append("- `warm` long только по сильному `start-break` checklist;")
    lines.append("- вход строго `next open`;")
    lines.append("- стоп для long строго под low сигнальной `1m`;")
    lines.append("- для слабых break-кейсов отдельно проверен short в `warm` и `overheated`;")
    lines.append("- везде использовались только признаки, известные к моменту сигнала.")
    lines.append("")
    lines.append("## Warm Long Best")
    lines.append("")
    lines.append(_table(long_summary.head(15)))
    lines.append("")
    lines.append("## Weak-Break Short Best")
    lines.append("")
    lines.append(_table(short_summary.head(20)))
    return "\n".join(lines)


def run() -> dict[str, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    frame = _load_signal_events()
    long_events, short_events = _build_events(frame)
    long_summary = _summaries(long_events, family="warm_long_strong_break")
    short_summary = _summaries(short_events, family="weak_break_short")
    report = _report(long_summary, short_summary)

    paths = {
        "long_events": OUTPUT_DIR / "long_events.csv",
        "short_events": OUTPUT_DIR / "short_events.csv",
        "long_summary": OUTPUT_DIR / "long_summary.csv",
        "short_summary": OUTPUT_DIR / "short_summary.csv",
        "report": OUTPUT_DIR / "report.md",
    }
    long_events.to_csv(paths["long_events"], index=False)
    short_events.to_csv(paths["short_events"], index=False)
    long_summary.to_csv(paths["long_summary"], index=False)
    short_summary.to_csv(paths["short_summary"], index=False)
    paths["report"].write_text(report, encoding="utf-8")
    return paths


if __name__ == "__main__":
    output = run()
    for key, value in output.items():
        print(f"{key}={value}")
