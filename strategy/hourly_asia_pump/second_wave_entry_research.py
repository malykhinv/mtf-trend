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
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "second_wave_entry_research"
FIVE_MINUTES_MS = 300_000
ONE_MINUTE_MS = 60_000
FEE_RATE = 0.0004


@dataclass(frozen=True, slots=True)
class BaseCategory:
    category_id: str
    title: str
    context_archetype: str


@dataclass(frozen=True, slots=True)
class BreakoutSpec:
    breakout_id: str
    title: str
    max_wait_bars: int
    min_body_ratio: float
    min_volume_ratio: float
    min_close_pos: float


@dataclass(frozen=True, slots=True)
class EntryModel:
    model_id: str
    title: str
    family: str
    breakout_spec: BreakoutSpec
    pause_bars: int | None
    min_closes_above: int | None
    max_pullback_frac: float
    max_pause_range_frac: float | None
    max_entry_extension_frac: float
    stop_style: str
    rr_target: float
    max_hold_bars: int


def _load_anomalies() -> pd.DataFrame:
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
        "pre_base_range_pct_60m",
        "pre_base_drift_pct_60m",
    ]
    for column in numeric_columns:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_ms"], unit="ms", utc=True, errors="coerce")
    frame["month_utc"] = frame["timestamp_utc"].dt.strftime("%Y-%m")
    return frame


def _base_categories() -> tuple[BaseCategory, ...]:
    return (
        BaseCategory("warm_accum", "Азия: тёплый контекст и прогрев", "context_warm"),
        BaseCategory("overheated_accum", "Азия: перегретый контекст и прогрев", "context_overheated"),
    )


def _breakout_specs() -> tuple[BreakoutSpec, ...]:
    return (
        BreakoutSpec("b40_v40_c55", "умеренный старт", 4, 0.40, 0.40, 0.55),
        BreakoutSpec("b60_v60_c60", "сильный старт", 4, 0.60, 0.60, 0.60),
        BreakoutSpec("b80_v60_c65", "агрессивный старт", 5, 0.80, 0.60, 0.65),
    )


def _build_entry_models() -> tuple[EntryModel, ...]:
    models: list[EntryModel] = []
    for breakout in _breakout_specs():
        for pause_bars in (1, 2):
            for max_pullback_frac in (0.15, 0.25):
                for max_pause_range_frac in (0.10, 0.18):
                    for max_entry_extension_frac in (0.12, 0.20):
                        for stop_style in ("pause_low", "cluster_low"):
                            for rr_target in (1.5, 2.0):
                                model_id = (
                                    f"microbase_{breakout.breakout_id}_p{pause_bars}"
                                    f"_pb{int(max_pullback_frac * 100):02d}"
                                    f"_rg{int(max_pause_range_frac * 100):02d}"
                                    f"_ex{int(max_entry_extension_frac * 100):02d}"
                                    f"_{stop_style}_rr{int(rr_target * 10):02d}"
                                )
                                models.append(
                                    EntryModel(
                                        model_id=model_id,
                                        title=(
                                            f"Micro-base: {breakout.title}, пауза {pause_bars}м, "
                                            f"pullback <= {max_pullback_frac:.0%}, TP {rr_target:.1f}R"
                                        ),
                                        family="microbase_break",
                                        breakout_spec=breakout,
                                        pause_bars=pause_bars,
                                        min_closes_above=None,
                                        max_pullback_frac=max_pullback_frac,
                                        max_pause_range_frac=max_pause_range_frac,
                                        max_entry_extension_frac=max_entry_extension_frac,
                                        stop_style=stop_style,
                                        rr_target=rr_target,
                                        max_hold_bars=90,
                                    )
                                )
        for min_closes_above in (2, 3):
            for max_pullback_frac in (0.15, 0.25):
                for max_entry_extension_frac in (0.12, 0.20):
                    for stop_style in ("hold_low", "trigger_reclaim"):
                        for rr_target in (1.5, 2.0):
                            model_id = (
                                f"holdbreak_{breakout.breakout_id}_c{min_closes_above}"
                                f"_pb{int(max_pullback_frac * 100):02d}"
                                f"_ex{int(max_entry_extension_frac * 100):02d}"
                                f"_{stop_style}_rr{int(rr_target * 10):02d}"
                            )
                            models.append(
                                EntryModel(
                                    model_id=model_id,
                                    title=(
                                        f"Hold-break: {breakout.title}, {min_closes_above} closes above high, "
                                        f"pullback <= {max_pullback_frac:.0%}, TP {rr_target:.1f}R"
                                    ),
                                    family="hold_break",
                                    breakout_spec=breakout,
                                    pause_bars=None,
                                    min_closes_above=min_closes_above,
                                    max_pullback_frac=max_pullback_frac,
                                    max_pause_range_frac=None,
                                    max_entry_extension_frac=max_entry_extension_frac,
                                    stop_style=stop_style,
                                    rr_target=rr_target,
                                    max_hold_bars=90,
                                )
                            )
    return tuple(models)


def _prepare_scope(frame: pd.DataFrame) -> pd.DataFrame:
    scoped = frame[
        (frame["session_id"].astype(str) == "asia")
        & (frame["market_side_hint"].astype(str) == "long")
        & (frame["impulse_archetype"].astype(str) == "impulse_body_drive")
        & (frame["pre_accumulation_type"].astype(str).isin({"accumulation_clean", "accumulation_warm"}))
    ].copy()
    scoped["base_category_id"] = None
    scoped["base_category_title"] = None
    for category in _base_categories():
        mask = scoped["context_archetype"].astype(str) == category.context_archetype
        scoped.loc[mask, "base_category_id"] = category.category_id
        scoped.loc[mask, "base_category_title"] = category.title
    scoped = scoped.dropna(subset=["base_category_id"]).copy()
    scoped["cache_scope"] = scoped["dataset"].map({"current": "current", "old": "old"})
    return scoped.reset_index(drop=True)


def _apply_m1_coverage(frame: pd.DataFrame) -> pd.DataFrame:
    current_symbols = set(DataPreparer(CURRENT_CACHE_DIR).list_symbols(Timeframe.M1))
    old_symbols = set(DataPreparer(PREV_CACHE_DIR).list_symbols(Timeframe.M1))
    scoped = frame.copy()
    scoped["has_m1"] = False
    current_mask = scoped["dataset"].astype(str) == "current"
    old_mask = scoped["dataset"].astype(str) == "old"
    scoped.loc[current_mask, "has_m1"] = scoped.loc[current_mask, "symbol"].astype(str).isin(current_symbols)
    scoped.loc[old_mask, "has_m1"] = scoped.loc[old_mask, "symbol"].astype(str).isin(old_symbols)
    return scoped[scoped["has_m1"].astype(bool)].copy().reset_index(drop=True)


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


def _bar_close_pos(high_price: float, low_price: float, close_price: float) -> float:
    bar_range = high_price - low_price
    if bar_range <= 0.0:
        return 0.5
    return float((close_price - low_price) / bar_range)


def _net_long_return(entry_price: float, exit_price: float) -> float:
    if entry_price <= 0.0 or exit_price <= 0.0:
        return 0.0
    return float((exit_price * (1.0 - FEE_RATE) / (entry_price * (1.0 + FEE_RATE))) - 1.0)


def _first_breakout_index(
    *,
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    start_idx: int,
    anomaly_high: float,
    trigger_body: float,
    trigger_volume: float,
    spec: BreakoutSpec,
) -> int | None:
    avg_trigger_body_1m = max(1e-12, trigger_body / 5.0)
    avg_trigger_volume_1m = max(1e-12, trigger_volume / 5.0)
    for idx in range(start_idx, min(len(closes), start_idx + spec.max_wait_bars)):
        body = closes[idx] - opens[idx]
        if body <= 0.0:
            continue
        if closes[idx] <= anomaly_high:
            continue
        if (body / avg_trigger_body_1m) < spec.min_body_ratio:
            continue
        if (volumes[idx] / avg_trigger_volume_1m) < spec.min_volume_ratio:
            continue
        if _bar_close_pos(highs[idx], lows[idx], closes[idx]) < spec.min_close_pos:
            continue
        return idx
    return None


def _resolve_exit(
    *,
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    timestamps: list[int],
    entry_idx: int,
    entry_price: float,
    stop_price: float,
    rr_target: float,
    max_hold_bars: int,
) -> tuple[int, float, str]:
    risk = entry_price - stop_price
    if risk <= 0.0:
        return entry_idx, entry_price, "invalid"
    target_price = entry_price + (risk * rr_target)
    last_idx = min(len(closes) - 1, entry_idx + max_hold_bars)
    for idx in range(entry_idx + 1, last_idx + 1):
        low_price = lows[idx]
        high_price = highs[idx]
        if low_price <= stop_price:
            return idx, stop_price, "stop"
        if high_price >= target_price:
            return idx, target_price, "tp"
    return last_idx, closes[last_idx], "time_exit"


def _simulate_microbase_break(
    *,
    model: EntryModel,
    timestamps: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    start_idx: int,
    anomaly_high: float,
    anomaly_low: float,
    anomaly_range: float,
    trigger_body: float,
    trigger_volume: float,
) -> dict[str, object] | None:
    breakout_idx = _first_breakout_index(
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
        start_idx=start_idx,
        anomaly_high=anomaly_high,
        trigger_body=trigger_body,
        trigger_volume=trigger_volume,
        spec=model.breakout_spec,
    )
    if breakout_idx is None or model.pause_bars is None:
        return None
    pause_start = breakout_idx + 1
    pause_end = breakout_idx + model.pause_bars
    entry_idx = pause_end + 1
    if entry_idx >= len(closes):
        return None
    cluster_end = pause_end
    cluster_lows = lows[breakout_idx : cluster_end + 1]
    cluster_highs = highs[breakout_idx : cluster_end + 1]
    cluster_range = max(cluster_highs) - min(cluster_lows)
    if anomaly_range <= 0.0:
        return None
    if cluster_range > (anomaly_range * float(model.max_pause_range_frac or 1.0)):
        return None
    reclaim_floor = anomaly_high - (anomaly_range * model.max_pullback_frac)
    for idx in range(pause_start, pause_end + 1):
        if lows[idx] < reclaim_floor:
            return None
        if closes[idx] <= anomaly_high:
            return None
    pattern_high = max(highs[breakout_idx : pause_end + 1])
    if closes[entry_idx] <= pattern_high:
        return None
    if ((closes[entry_idx] - anomaly_high) / anomaly_range) > model.max_entry_extension_frac:
        return None
    entry_price = closes[entry_idx]
    if model.stop_style == "cluster_low":
        stop_price = min(cluster_lows)
    else:
        stop_price = min(lows[pause_start : pause_end + 1])
    if stop_price >= entry_price:
        return None
    exit_idx, exit_price, exit_reason = _resolve_exit(
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        timestamps=timestamps,
        entry_idx=entry_idx,
        entry_price=entry_price,
        stop_price=stop_price,
        rr_target=model.rr_target,
        max_hold_bars=model.max_hold_bars,
    )
    return {
        "entry_idx": entry_idx,
        "entry_timestamp_ms": int(timestamps[entry_idx] + ONE_MINUTE_MS),
        "entry_price": float(entry_price),
        "stop_price": float(stop_price),
        "initial_risk_pct": float((entry_price - stop_price) / entry_price),
        "exit_idx": exit_idx,
        "exit_timestamp_ms": int(timestamps[exit_idx] + ONE_MINUTE_MS),
        "exit_price": float(exit_price),
        "exit_reason": exit_reason,
        "exit_return_pct": _net_long_return(entry_price, exit_price),
        "pattern_family": model.family,
        "breakout_idx": breakout_idx,
    }


def _simulate_hold_break(
    *,
    model: EntryModel,
    timestamps: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    start_idx: int,
    anomaly_high: float,
    anomaly_low: float,
    anomaly_range: float,
    trigger_body: float,
    trigger_volume: float,
) -> dict[str, object] | None:
    breakout_idx = _first_breakout_index(
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
        start_idx=start_idx,
        anomaly_high=anomaly_high,
        trigger_body=trigger_body,
        trigger_volume=trigger_volume,
        spec=model.breakout_spec,
    )
    if breakout_idx is None or model.min_closes_above is None or anomaly_range <= 0.0:
        return None
    reclaim_floor = anomaly_high - (anomaly_range * model.max_pullback_frac)
    closes_above = 1 if closes[breakout_idx] > anomaly_high else 0
    hold_low = lows[breakout_idx]
    hold_high = highs[breakout_idx]
    for idx in range(breakout_idx + 1, min(len(closes), breakout_idx + 6)):
        hold_low = min(hold_low, lows[idx])
        hold_high = max(hold_high, highs[idx])
        if lows[idx] < reclaim_floor:
            return None
        if closes[idx] > anomaly_high:
            closes_above += 1
        if closes[idx] > hold_high:
            hold_high = closes[idx]
        if closes_above >= model.min_closes_above and closes[idx] > highs[idx - 1]:
            if ((closes[idx] - anomaly_high) / anomaly_range) > model.max_entry_extension_frac:
                return None
            entry_price = closes[idx]
            if model.stop_style == "trigger_reclaim":
                stop_price = reclaim_floor
            else:
                stop_price = hold_low
            if stop_price >= entry_price:
                return None
            exit_idx, exit_price, exit_reason = _resolve_exit(
                opens=opens,
                highs=highs,
                lows=lows,
                closes=closes,
                timestamps=timestamps,
                entry_idx=idx,
                entry_price=entry_price,
                stop_price=stop_price,
                rr_target=model.rr_target,
                max_hold_bars=model.max_hold_bars,
            )
            return {
                "entry_idx": idx,
                "entry_timestamp_ms": int(timestamps[idx] + ONE_MINUTE_MS),
                "entry_price": float(entry_price),
                "stop_price": float(stop_price),
                "initial_risk_pct": float((entry_price - stop_price) / entry_price),
                "exit_idx": exit_idx,
                "exit_timestamp_ms": int(timestamps[exit_idx] + ONE_MINUTE_MS),
                "exit_price": float(exit_price),
                "exit_reason": exit_reason,
                "exit_return_pct": _net_long_return(entry_price, exit_price),
                "pattern_family": model.family,
                "breakout_idx": breakout_idx,
            }
    return None


def _simulate_event(event_row: pd.Series, candles_1m: pd.DataFrame, model: EntryModel) -> dict[str, object] | None:
    anomaly_ts = int(event_row["timestamp_ms"])
    anomaly_close_ts = anomaly_ts + FIVE_MINUTES_MS
    timestamps = pd.to_numeric(candles_1m["timestamp"], errors="coerce").astype("int64").tolist()
    try:
        start_idx = timestamps.index(anomaly_close_ts)
    except ValueError:
        return None
    opens = pd.to_numeric(candles_1m["open"], errors="coerce").astype(float).tolist()
    highs = pd.to_numeric(candles_1m["high"], errors="coerce").astype(float).tolist()
    lows = pd.to_numeric(candles_1m["low"], errors="coerce").astype(float).tolist()
    closes = pd.to_numeric(candles_1m["close"], errors="coerce").astype(float).tolist()
    volumes = pd.to_numeric(candles_1m["volume"], errors="coerce").astype(float).tolist()

    anomaly_high = float(event_row["trigger_high"])
    anomaly_low = float(event_row["trigger_low"])
    anomaly_range = max(1e-12, anomaly_high - anomaly_low)
    trigger_body = max(1e-12, abs(float(event_row["trigger_close"]) - float(event_row["trigger_open"])))
    trigger_volume = max(1e-12, float(event_row["trigger_volume"]))
    if model.family == "microbase_break":
        return _simulate_microbase_break(
            model=model,
            timestamps=timestamps,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
            start_idx=start_idx,
            anomaly_high=anomaly_high,
            anomaly_low=anomaly_low,
            anomaly_range=anomaly_range,
            trigger_body=trigger_body,
            trigger_volume=trigger_volume,
        )
    return _simulate_hold_break(
        model=model,
        timestamps=timestamps,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
        start_idx=start_idx,
        anomaly_high=anomaly_high,
        anomaly_low=anomaly_low,
        anomaly_range=anomaly_range,
        trigger_body=trigger_body,
        trigger_volume=trigger_volume,
    )


def _build_events(frame: pd.DataFrame, models: tuple[EntryModel, ...]) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    cache: dict[tuple[str, str], pd.DataFrame] = {}
    grouped = frame.groupby(["cache_scope", "symbol"], sort=True)
    for (cache_scope, symbol), scoped in grouped:
        candles_1m = _load_m1(str(cache_scope), str(symbol), cache)
        if candles_1m.empty:
            continue
        for _, row in scoped.iterrows():
            base = {
                "base_category_id": row["base_category_id"],
                "base_category_title": row["base_category_title"],
                "dataset": row["dataset"],
                "symbol": row["symbol"],
                "timestamp_ms": int(row["timestamp_ms"]),
                "month_utc": row["month_utc"],
                "trigger_return_pct": float(row["trigger_return_pct"]),
                "range_atr": float(row["range_atr"]),
                "volume_mult": float(row["volume_mult"]),
                "pre_base_range_pct_60m": float(row["pre_base_range_pct_60m"]),
                "pre_base_drift_pct_60m": float(row["pre_base_drift_pct_60m"]),
                "pre_accumulation_type": row["pre_accumulation_type"],
            }
            for model in models:
                simulated = _simulate_event(row, candles_1m, model)
                if simulated is None:
                    continue
                records.append(
                    {
                        **base,
                        **simulated,
                        "model_id": model.model_id,
                        "model_title": model.title,
                    }
                )
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records).sort_values(
        ["base_category_id", "model_id", "dataset", "timestamp_ms", "symbol"]
    ).reset_index(drop=True)


def _summarize_models(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for (category_id, model_id), scoped in events.groupby(["base_category_id", "model_id"], sort=True):
        current = scoped[scoped["dataset"] == "current"].copy()
        old = scoped[scoped["dataset"] == "old"].copy()
        combined = scoped.copy()
        current_summary = _summarize_events(current)
        old_summary = _summarize_events(old)
        combined_summary = _summarize_events(combined)
        current_months = _build_monthly_returns_frame(current)
        old_months = _build_monthly_returns_frame(old)
        combined_months = _build_monthly_returns_frame(combined)
        current_calendar = _calendar_months_from_frames(current)
        old_calendar = _calendar_months_from_frames(old)
        combined_calendar = _calendar_months_from_frames(combined)
        current_eq, _ = _simulate_equity_risk_metrics(current, calendar_months=current_calendar, risk_fraction=0.05)
        old_eq, _ = _simulate_equity_risk_metrics(old, calendar_months=old_calendar, risk_fraction=0.05)
        combined_eq, _ = _simulate_equity_risk_metrics(combined, calendar_months=combined_calendar, risk_fraction=0.05)
        rows.append(
            {
                "base_category_id": category_id,
                "model_id": model_id,
                "current_trades": int(current_summary["trade_count"]),
                "old_trades": int(old_summary["trade_count"]),
                "combined_trades_per_year": float(combined_summary["trades_per_year"]),
                "combined_mean_return_pct": float(combined_summary["mean_return_pct"]),
                "combined_median_return_pct": float(combined_summary["median_return_pct"]),
                "combined_win_rate": float(combined_summary["win_rate"]),
                "combined_annualized_unit_pnl_pct": float(combined_summary["annualized_unit_pnl_pct"]),
                "equity_annualized_return_pct_5": float(combined_eq["equity_annualized_return_pct"]),
                "equity_max_drawdown_pct_5": float(combined_eq["equity_max_drawdown_pct"]),
                "equity_stable_positive_months_count": int(combined_eq["equity_stable_positive_months_count"]),
                "current_mean_return_pct": float(current_summary["mean_return_pct"]),
                "old_mean_return_pct": float(old_summary["mean_return_pct"]),
                "current_win_rate": float(current_summary["win_rate"]),
                "old_win_rate": float(old_summary["win_rate"]),
                "current_equity_annualized_return_pct_5": float(current_eq["equity_annualized_return_pct"]),
                "old_equity_annualized_return_pct_5": float(old_eq["equity_annualized_return_pct"]),
                "current_positive_months": int(current_eq["equity_positive_months_count"]),
                "old_positive_months": int(old_eq["equity_positive_months_count"]),
                "current_calendar_months": len(current_calendar),
                "old_calendar_months": len(old_calendar),
                "combined_calendar_months": len(combined_calendar),
            }
        )
    summary = pd.DataFrame(rows)
    summary["score"] = (
        summary["equity_annualized_return_pct_5"]
        - (0.5 * summary["equity_max_drawdown_pct_5"])
        + (3.0 * summary["combined_mean_return_pct"])
        + (0.5 * summary["equity_stable_positive_months_count"])
    )
    return summary.sort_values(
        [
            "score",
            "equity_annualized_return_pct_5",
            "combined_mean_return_pct",
            "combined_trades_per_year",
        ],
        ascending=[False, False, False, False],
    ).reset_index(drop=True)


def _build_report(summary: pd.DataFrame) -> str:
    lines = [
        "# Онлайн-вход во вторую волну",
        "",
        "Исследование максимально приближено к торговым условиям:",
        "- сначала закрывается аномальная `5m` свеча;",
        "- дальше используется только `1m`, доступная после её закрытия;",
        "- вход разрешён только по структуре после аномалии, без знания будущего;",
        "- стоп и тейк считаются только из того, что уже видно на момент входа.",
        "",
    ]
    if summary.empty:
        lines.extend(["Подходящих моделей не найдено.", ""])
        return "\n".join(lines)

    best_overall = summary.iloc[0]
    lines.extend(
        [
            "## Главный вывод",
            (
                f"Лучший онлайн-подход сейчас: `{best_overall['model_id']}` в категории "
                f"`{best_overall['base_category_id']}`."
            ),
            (
                f"- сделки/год: `{best_overall['combined_trades_per_year']:.1f}`; "
                f"mean trade: `{best_overall['combined_mean_return_pct'] * 100:.2f}%`; "
                f"WR: `{best_overall['combined_win_rate'] * 100:.1f}%`."
            ),
            (
                f"- equity @5%: `{best_overall['equity_annualized_return_pct_5'] * 100:.1f}%`, "
                f"DD `{best_overall['equity_max_drawdown_pct_5'] * 100:.1f}%`, "
                f"устойчивых месяцев `{int(best_overall['equity_stable_positive_months_count'])}`."
            ),
            "",
            "## Топ моделей",
            _frame_to_markdown(
                summary[
                    [
                        "base_category_id",
                        "model_id",
                        "current_trades",
                        "old_trades",
                        "combined_trades_per_year",
                        "combined_mean_return_pct",
                        "combined_win_rate",
                        "equity_annualized_return_pct_5",
                        "equity_max_drawdown_pct_5",
                        "equity_stable_positive_months_count",
                    ]
                ].head(20)
            ),
            "",
            "## Что сравнивается",
            "- `microbase_break`: после первого пробоя high аномалии ждём короткую базу выше high и входим на её повторном пробое.",
            "- `hold_break`: ждём несколько закрытий выше high аномалии и входим на продолжении, если нет глубокого возврата вниз.",
            "",
        ]
    )
    for category_id, scoped in summary.groupby("base_category_id", sort=True):
        lines.extend(
            [
                f"## Категория `{category_id}`",
                _frame_to_markdown(
                    scoped[
                        [
                            "model_id",
                            "current_trades",
                            "old_trades",
                            "combined_trades_per_year",
                            "combined_mean_return_pct",
                            "combined_win_rate",
                            "equity_annualized_return_pct_5",
                            "equity_max_drawdown_pct_5",
                            "equity_stable_positive_months_count",
                        ]
                    ].head(10)
                ),
                "",
            ]
        )
    return "\n".join(lines)


def run() -> dict[str, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    anomalies = _load_anomalies()
    scoped = _apply_m1_coverage(_prepare_scope(anomalies))
    models = _build_entry_models()
    events = _build_events(scoped, models)
    summary = _summarize_models(events)
    coverage_rows = (
        scoped.groupby(["base_category_id", "dataset"], sort=True)
        .size()
        .rename("covered_events")
        .reset_index()
    )
    events_path = OUTPUT_DIR / "events.csv"
    summary_path = OUTPUT_DIR / "summary.csv"
    coverage_path = OUTPUT_DIR / "coverage.csv"
    report_path = OUTPUT_DIR / "report.md"
    events.to_csv(events_path, index=False)
    summary.to_csv(summary_path, index=False)
    coverage_rows.to_csv(coverage_path, index=False)
    report_path.write_text(_build_report(summary), encoding="utf-8")
    return {
        "events": events_path,
        "summary": summary_path,
        "coverage": coverage_path,
        "report": report_path,
    }


if __name__ == "__main__":
    run()
