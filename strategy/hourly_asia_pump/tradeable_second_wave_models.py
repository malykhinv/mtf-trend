from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from domain.enums.timeframe import Timeframe
from strategy.hourly_asia_pump.static_combo import (
    _build_monthly_returns_frame,
    _calendar_months_from_frames,
    _frame_to_markdown,
    _summarize_events,
)
from strategy.hourly_asia_pump.unified_edge import _simulate_equity_risk_metrics
from vectorbt_runner.data_preparer import DataPreparer

REPO_ROOT = Path(__file__).resolve().parents[2]
CURRENT_CACHE_DIR = REPO_ROOT / ".output" / "cache"
PREV_CACHE_DIR = REPO_ROOT / ".output" / "cache_prev_year_5m"
BASE_INPUT_PATH = (
    REPO_ROOT
    / ".output"
    / "results_prev_year_5m"
    / "anomaly_category_lab"
    / "anomaly_feature_database_with_accumulation.csv"
)
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "tradeable_second_wave_models"
FIVE_MINUTES_MS = 300_000
ONE_MINUTE_MS = 60_000
FEE_RATE = 0.0004


@dataclass(frozen=True, slots=True)
class CategorySpec:
    category_id: str
    title: str
    context_archetype: str
    style: str


@dataclass(frozen=True, slots=True)
class WarmContinuationModel:
    model_id: str
    max_wait_minutes: int
    min_break_body_ratio: float
    min_break_volume_ratio: float
    min_close_pos: float
    max_pullback_frac: float
    max_seller_pressure: float
    min_buyer_seller_ratio: float
    max_entry_extension_frac: float
    stop_style: str
    rr_target: float
    fast_fail_minutes: int
    fast_fail_r: float
    max_hold_minutes: int


@dataclass(frozen=True, slots=True)
class OverheatedRetestModel:
    model_id: str
    max_wait_minutes: int
    min_break_body_ratio: float
    min_break_volume_ratio: float
    min_close_pos: float
    min_retest_depth_frac: float
    max_retest_depth_frac: float
    stabilization_bars: int
    min_buyer_seller_ratio: float
    max_entry_extension_frac: float
    rr_target: float
    fast_fail_minutes: int
    fast_fail_r: float
    max_hold_minutes: int


def _load_feature_db() -> pd.DataFrame:
    if not BASE_INPUT_PATH.exists():
        raise FileNotFoundError(f"Не найдена база аномалий: {BASE_INPUT_PATH}")
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


def _category_specs() -> tuple[CategorySpec, ...]:
    return (
        CategorySpec("warm_continuation", "Азия: тёплый контекст, прогрев, ранний continuation", "context_warm", "warm"),
        CategorySpec("overheated_retest", "Азия: перегретый контекст, прогрев, retest и возврат инициативы", "context_overheated", "overheated"),
    )


def _prepare_scope(frame: pd.DataFrame) -> pd.DataFrame:
    scoped = frame[
        (frame["session_id"].astype(str) == "asia")
        & (frame["market_side_hint"].astype(str) == "long")
        & (frame["impulse_archetype"].astype(str) == "impulse_body_drive")
        & (frame["pre_accumulation_type"].astype(str).isin({"accumulation_clean", "accumulation_warm"}))
    ].copy()
    scoped["category_id"] = None
    scoped["category_title"] = None
    scoped["category_style"] = None
    for spec in _category_specs():
        mask = scoped["context_archetype"].astype(str) == spec.context_archetype
        scoped.loc[mask, "category_id"] = spec.category_id
        scoped.loc[mask, "category_title"] = spec.title
        scoped.loc[mask, "category_style"] = spec.style
    scoped = scoped.dropna(subset=["category_id"]).copy()
    scoped["cache_scope"] = scoped["dataset"].map({"current": "current", "old": "old"})
    current_symbols = set(DataPreparer(CURRENT_CACHE_DIR).list_symbols(Timeframe.M1))
    old_symbols = set(DataPreparer(PREV_CACHE_DIR).list_symbols(Timeframe.M1))
    scoped["has_m1"] = False
    current_mask = scoped["dataset"].astype(str) == "current"
    old_mask = scoped["dataset"].astype(str) == "old"
    scoped.loc[current_mask, "has_m1"] = scoped.loc[current_mask, "symbol"].astype(str).isin(current_symbols)
    scoped.loc[old_mask, "has_m1"] = scoped.loc[old_mask, "symbol"].astype(str).isin(old_symbols)
    return scoped[scoped["has_m1"].astype(bool)].copy().reset_index(drop=True)


def _build_warm_models() -> tuple[WarmContinuationModel, ...]:
    models: list[WarmContinuationModel] = []
    for min_break_body_ratio in (0.50, 0.70):
        for max_pullback_frac in (0.12, 0.20):
            for max_seller_pressure in (0.20, 0.35):
                for stop_style in ("signal_low", "prebreak_low"):
                    for rr_target in (1.5, 2.0):
                        model_id = (
                            f"warm_b{int(min_break_body_ratio*100):02d}"
                            f"_pb{int(max_pullback_frac*100):02d}"
                            f"_sp{int(max_seller_pressure*100):02d}"
                            f"_{stop_style}_rr{int(rr_target*10):02d}"
                        )
                        models.append(
                            WarmContinuationModel(
                                model_id=model_id,
                                max_wait_minutes=5,
                                min_break_body_ratio=min_break_body_ratio,
                                min_break_volume_ratio=0.50,
                                min_close_pos=0.60,
                                max_pullback_frac=max_pullback_frac,
                                max_seller_pressure=max_seller_pressure,
                                min_buyer_seller_ratio=1.00,
                                max_entry_extension_frac=0.18,
                                stop_style=stop_style,
                                rr_target=rr_target,
                                fast_fail_minutes=10,
                                fast_fail_r=0.25,
                                max_hold_minutes=90,
                            )
                        )
    return tuple(models)


def _build_overheated_models() -> tuple[OverheatedRetestModel, ...]:
    models: list[OverheatedRetestModel] = []
    for min_retest_depth_frac in (0.08, 0.15):
        for max_retest_depth_frac in (0.35, 0.50):
            for stabilization_bars in (1, 2):
                for min_buyer_seller_ratio in (1.00, 1.50):
                    for rr_target in (1.5, 2.0):
                        model_id = (
                            f"over_r{int(min_retest_depth_frac*100):02d}_{int(max_retest_depth_frac*100):02d}"
                            f"_s{stabilization_bars}"
                            f"_bs{int(min_buyer_seller_ratio*100):03d}"
                            f"_rr{int(rr_target*10):02d}"
                        )
                        models.append(
                            OverheatedRetestModel(
                                model_id=model_id,
                                max_wait_minutes=15,
                                min_break_body_ratio=0.50,
                                min_break_volume_ratio=0.50,
                                min_close_pos=0.55,
                                min_retest_depth_frac=min_retest_depth_frac,
                                max_retest_depth_frac=max_retest_depth_frac,
                                stabilization_bars=stabilization_bars,
                                min_buyer_seller_ratio=min_buyer_seller_ratio,
                                max_entry_extension_frac=0.20,
                                rr_target=rr_target,
                                fast_fail_minutes=15,
                                fast_fail_r=0.20,
                                max_hold_minutes=120,
                            )
                        )
    return tuple(models)


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


def _pressure_score(open_price: float, close_price: float, volume: float, trigger_range: float, avg_trigger_vol_1m: float) -> float:
    if trigger_range <= 0.0 or avg_trigger_vol_1m <= 0.0:
        return 0.0
    body = abs(close_price - open_price)
    volume_ratio = min(3.0, volume / avg_trigger_vol_1m)
    return float((body / trigger_range) * volume_ratio)


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
        low_price = lows[idx]
        high_price = highs[idx]
        if low_price <= stop_price:
            return idx, stop_price, "stop"
        highest_high = max(highest_high, high_price)
        if high_price >= target_price:
            return idx, target_price, "tp"
        if idx >= fast_fail_idx and highest_high < (entry_price + risk * fast_fail_r):
            return idx, closes[idx], "fast_fail"
    return last_idx, closes[last_idx], "time_exit"


def _simulate_warm_model(
    *,
    event_row: pd.Series,
    timestamps: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    model: WarmContinuationModel,
) -> dict[str, object] | None:
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
    prebreak_low = float("inf")
    seller_pressure = 0.0
    for idx in range(start_idx, min(len(closes), start_idx + model.max_wait_minutes)):
        prebreak_low = min(prebreak_low, lows[idx])
        if closes[idx] <= trigger_high:
            if closes[idx] < opens[idx]:
                seller_pressure += _pressure_score(opens[idx], closes[idx], volumes[idx], trigger_range, avg_trigger_vol_1m)
            continue
        body = closes[idx] - opens[idx]
        if body <= 0.0:
            continue
        body_ratio = body / avg_trigger_body_1m if avg_trigger_body_1m > 0 else 0.0
        volume_ratio = volumes[idx] / avg_trigger_vol_1m if avg_trigger_vol_1m > 0 else 0.0
        close_pos = _close_pos(highs[idx], lows[idx], closes[idx])
        buyer_score = _pressure_score(opens[idx], closes[idx], volumes[idx], trigger_range, avg_trigger_vol_1m)
        pullback_frac = max(0.0, (trigger_high - prebreak_low) / trigger_range)
        extension_frac = max(0.0, (closes[idx] - trigger_high) / trigger_range)
        if body_ratio < model.min_break_body_ratio:
            continue
        if volume_ratio < model.min_break_volume_ratio:
            continue
        if close_pos < model.min_close_pos:
            continue
        if pullback_frac > model.max_pullback_frac:
            continue
        if seller_pressure > model.max_seller_pressure:
            continue
        if buyer_score < (seller_pressure * model.min_buyer_seller_ratio):
            continue
        if extension_frac > model.max_entry_extension_frac:
            continue
        entry_price = closes[idx]
        stop_price = lows[idx] if model.stop_style == "signal_low" else prebreak_low
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
            rr_target=model.rr_target,
            max_hold_minutes=model.max_hold_minutes,
            fast_fail_minutes=model.fast_fail_minutes,
            fast_fail_r=model.fast_fail_r,
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
            "entry_detail": "warm_break",
            "seller_pressure_score": float(seller_pressure),
            "buyer_break_score": float(buyer_score),
            "pre_entry_pullback_frac": float(pullback_frac),
        }
    return None


def _simulate_overheated_model(
    *,
    event_row: pd.Series,
    timestamps: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    model: OverheatedRetestModel,
) -> dict[str, object] | None:
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
    end_idx = min(len(closes), start_idx + model.max_wait_minutes)
    for idx in range(start_idx + model.stabilization_bars + 1, end_idx):
        body = closes[idx] - opens[idx]
        if body <= 0.0 or closes[idx] <= trigger_high:
            continue
        body_ratio = body / avg_trigger_body_1m if avg_trigger_body_1m > 0 else 0.0
        volume_ratio = volumes[idx] / avg_trigger_vol_1m if avg_trigger_vol_1m > 0 else 0.0
        close_pos = _close_pos(highs[idx], lows[idx], closes[idx])
        if body_ratio < model.min_break_body_ratio:
            continue
        if volume_ratio < model.min_break_volume_ratio:
            continue
        if close_pos < model.min_close_pos:
            continue
        lookback_start = max(start_idx, idx - (model.stabilization_bars + 3))
        cluster_lows = lows[lookback_start:idx]
        cluster_highs = highs[max(start_idx, idx - model.stabilization_bars):idx]
        if not cluster_lows or not cluster_highs:
            continue
        cluster_low = min(cluster_lows)
        retest_depth = max(0.0, (trigger_high - cluster_low) / trigger_range)
        if retest_depth < model.min_retest_depth_frac or retest_depth > model.max_retest_depth_frac:
            continue
        lowest_idx = lookback_start + cluster_lows.index(cluster_low)
        if (idx - lowest_idx) < model.stabilization_bars:
            continue
        structure_high = max(cluster_highs)
        if closes[idx] <= structure_high:
            continue
        extension_frac = max(0.0, (closes[idx] - trigger_high) / trigger_range)
        if extension_frac > model.max_entry_extension_frac:
            continue
        seller_pressure = 0.0
        for j in range(lowest_idx, idx):
            if closes[j] < opens[j]:
                seller_pressure += _pressure_score(opens[j], closes[j], volumes[j], trigger_range, avg_trigger_vol_1m)
        buyer_score = _pressure_score(opens[idx], closes[idx], volumes[idx], trigger_range, avg_trigger_vol_1m)
        if buyer_score < (seller_pressure * model.min_buyer_seller_ratio):
            continue
        entry_price = closes[idx]
        stop_price = cluster_low
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
            rr_target=model.rr_target,
            max_hold_minutes=model.max_hold_minutes,
            fast_fail_minutes=model.fast_fail_minutes,
            fast_fail_r=model.fast_fail_r,
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
            "entry_detail": "overheated_retest_break",
            "seller_pressure_score": float(seller_pressure),
            "buyer_break_score": float(buyer_score),
            "retest_depth_frac": float(retest_depth),
        }
    return None


def _build_events(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    warm_models = _build_warm_models()
    overheated_models = _build_overheated_models()
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
                f"tradeable-second-wave: progress={group_index}/{total_groups} "
                f"cache={cache_scope} symbol={symbol} events={len(scoped)}"
            )
        for _, row in scoped.iterrows():
            base = {
                "category_id": row["category_id"],
                "category_title": row["category_title"],
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
                    "category_id": row["category_id"],
                    "dataset": row["dataset"],
                    "symbol": row["symbol"],
                    "timestamp_ms": int(row["timestamp_ms"]),
                }
            )
            if row["category_style"] == "warm":
                for model in warm_models:
                    simulated = _simulate_warm_model(
                        event_row=row,
                        timestamps=timestamps,
                        opens=opens,
                        highs=highs,
                        lows=lows,
                        closes=closes,
                        volumes=volumes,
                        model=model,
                    )
                    if simulated is None:
                        continue
                    records.append({**base, **simulated, "model_id": model.model_id, "model_family": "warm_continuation"})
            else:
                for model in overheated_models:
                    simulated = _simulate_overheated_model(
                        event_row=row,
                        timestamps=timestamps,
                        opens=opens,
                        highs=highs,
                        lows=lows,
                        closes=closes,
                        volumes=volumes,
                        model=model,
                    )
                    if simulated is None:
                        continue
                    records.append({**base, **simulated, "model_id": model.model_id, "model_family": "overheated_retest"})
    coverage = pd.DataFrame(coverage_rows).drop_duplicates()
    events = (
        pd.DataFrame(records)
        .sort_values(["category_id", "model_id", "dataset", "timestamp_ms", "symbol"])
        .reset_index(drop=True)
        if records
        else pd.DataFrame()
    )
    return events, coverage


def _summarize_models(events: pd.DataFrame, coverage: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    coverage_counts = (
        coverage.groupby(["category_id", "dataset"]).size().unstack(fill_value=0).rename(columns={"current": "coverage_current", "old": "coverage_old"})
    )
    rows: list[dict[str, object]] = []
    for (category_id, model_id), scoped in events.groupby(["category_id", "model_id"], sort=True):
        current = scoped[scoped["dataset"] == "current"].copy()
        old = scoped[scoped["dataset"] == "old"].copy()
        combined = scoped.copy()
        calendar_months = _calendar_months_from_frames(combined)
        summary = _summarize_events(combined, calendar_months=calendar_months)
        equity, _ = _simulate_equity_risk_metrics(combined, calendar_months=calendar_months, risk_fraction=0.05)
        coverage_current = int(coverage_counts.loc[category_id, "coverage_current"]) if category_id in coverage_counts.index and "coverage_current" in coverage_counts.columns else 0
        coverage_old = int(coverage_counts.loc[category_id, "coverage_old"]) if category_id in coverage_counts.index and "coverage_old" in coverage_counts.columns else 0
        rows.append(
            {
                "category_id": category_id,
                "category_title": scoped["category_title"].iloc[0],
                "model_id": model_id,
                "current_trades": int(len(current)),
                "old_trades": int(len(old)),
                "coverage_current": coverage_current,
                "coverage_old": coverage_old,
                "coverage_rate_current": float(len(current) / coverage_current) if coverage_current > 0 else 0.0,
                "coverage_rate_old": float(len(old) / coverage_old) if coverage_old > 0 else 0.0,
                "combined_trades_per_year": float(summary["trades_per_year"]),
                "combined_mean_return_pct": float(summary["mean_return_pct"]),
                "combined_median_return_pct": float(summary["median_return_pct"]),
                "combined_win_rate": float(summary["win_rate"]),
                "combined_annualized_unit_pnl_pct": float(summary["annualized_unit_pnl_pct"]),
                "equity_annualized_return_pct_5": float(equity["equity_annualized_return_pct"]),
                "equity_max_drawdown_pct_5": float(equity["equity_max_drawdown_pct"]),
                "equity_stable_positive_months_count": int(equity["equity_stable_positive_months_count"]),
                "equity_positive_months_count": int(equity["equity_positive_months_count"]),
            }
        )
    summary_frame = pd.DataFrame(rows)
    summary_frame["score"] = (
        summary_frame["equity_annualized_return_pct_5"]
        - (0.45 * summary_frame["equity_max_drawdown_pct_5"])
        + (3.0 * summary_frame["combined_mean_return_pct"])
        + (0.35 * summary_frame["equity_stable_positive_months_count"])
    )
    return summary_frame.sort_values(
        ["category_id", "score", "equity_annualized_return_pct_5", "combined_mean_return_pct", "combined_trades_per_year"],
        ascending=[True, False, False, False, False],
    ).reset_index(drop=True)


def _select_best_models(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return summary
    selected_rows: list[pd.Series] = []
    for category_id, scoped in summary.groupby("category_id", sort=True):
        ready = scoped[
            (scoped["current_trades"] >= 20)
            & (scoped["old_trades"] >= 8)
            & (scoped["combined_mean_return_pct"] > 0.0)
            & (scoped["equity_annualized_return_pct_5"] > 0.0)
        ].copy()
        if not ready.empty:
            row = ready.iloc[0].copy()
            row["selection_status"] = "candidate_ready"
            selected_rows.append(row)
            continue
        thin_positive = scoped[
            (scoped["combined_mean_return_pct"] > 0.0)
            & (scoped["equity_annualized_return_pct_5"] > 0.0)
        ].copy()
        if not thin_positive.empty:
            row = thin_positive.iloc[0].copy()
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
    chosen = events.merge(selected[["category_id", "model_id", "selection_status"]], on=["category_id", "model_id"], how="inner").copy()
    chosen = chosen[chosen["selection_status"].astype(str) != "not_ready"].copy()
    monthly = _build_monthly_returns_frame(chosen)
    return chosen, monthly


def _build_report(summary: pd.DataFrame, selected: pd.DataFrame) -> str:
    lines = [
        "# Торгуемые модели второй волны",
        "",
        "Цель исследования: сделать две отдельные онлайн-модели long-входа, максимально приближённые к реальной торговле.",
        "",
        "Категории задаются не по времени, а по природе пампа:",
        "- `warm_continuation`: тёплый контекст, телесный импульс, прогрев до пампа, ранний continuation.",
        "- `overheated_retest`: перегретый контекст, телесный импульс, прогрев до пампа, retest и возврат инициативы.",
        "",
        "Во всех моделях используется только то, что известно после закрытия аномальной `5m`.",
        "",
    ]
    if summary.empty:
        lines.append("Подходящих моделей не найдено.")
        return "\n".join(lines)
    if not selected.empty:
        lines.extend(
            [
                "## Лучшие модели по категориям",
                _frame_to_markdown(
                    selected,
                    columns=[
                        "category_id",
                        "selection_status",
                        "model_id",
                        "current_trades",
                        "old_trades",
                        "coverage_current",
                        "coverage_old",
                        "combined_trades_per_year",
                        "combined_mean_return_pct",
                        "combined_win_rate",
                        "equity_annualized_return_pct_5",
                        "equity_max_drawdown_pct_5",
                        "equity_stable_positive_months_count",
                    ],
                ),
                "",
                "Статусы:",
                "- `candidate_ready`: есть рабочий кандидат с положительной экономикой и нормальной выборкой.",
                "- `thin_positive`: экономика положительная, но выборка ещё тонкая.",
                "- `not_ready`: модель пока не готова, несмотря на то что она лучшая внутри текущего семейства.",
                "",
                "### Как торговать warm_continuation",
                "- ждём ранний пробой high аномалии в ближайшие минуты;",
                "- перед пробоем проверяем, что продавец не накопил слишком сильное давление;",
                "- входим по закрытию инициативной `1m` выше high аномалии;",
                "- стоп либо под low этой `1m`, либо под минимальный low перед пробоем.",
                "",
                "### Как торговать overheated_retest",
                "- ждём retest после аномалии;",
                "- на пробое локальной `1m` структуры вверх оцениваем силу продавца в retest-кластере;",
                "- входим только если покупатель на пробое не слабее продавца и retest не слишком глубокий;",
                "- стоп под low retest-кластера.",
                "",
            ]
        )
    lines.extend(
        [
            "## Топ моделей",
            _frame_to_markdown(
                summary.head(20),
                columns=[
                    "category_id",
                    "model_id",
                    "current_trades",
                    "old_trades",
                    "coverage_current",
                    "coverage_old",
                    "combined_trades_per_year",
                    "combined_mean_return_pct",
                    "combined_win_rate",
                    "equity_annualized_return_pct_5",
                    "equity_max_drawdown_pct_5",
                    "equity_stable_positive_months_count",
                ],
            ),
            "",
        ]
    )
    return "\n".join(lines)


def run() -> dict[str, Path]:
    print("tradeable-second-wave: start")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    scoped = _prepare_scope(_load_feature_db())
    print(f"tradeable-second-wave: scoped_events={len(scoped)}")
    events, coverage = _build_events(scoped)
    print(f"tradeable-second-wave: built_events={len(events)}")
    summary = _summarize_models(events, coverage)
    selected = _select_best_models(summary)
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
    print("tradeable-second-wave: done")
    return paths


if __name__ == "__main__":
    run()
