from __future__ import annotations

import math
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
BASE_INPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "anomaly_category_lab"
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "online_second_wave_long"
FIVE_MINUTES_MS = 300_000
ONE_MINUTE_MS = 60_000


@dataclass(frozen=True, slots=True)
class CategorySpec:
    category_id: str
    title: str
    context_archetype: str
    accumulation_required: bool | None = None


@dataclass(frozen=True, slots=True)
class TriggerSpec:
    trigger_id: str
    title: str
    family: str
    max_wait_bars: int
    min_body_vs_trigger_avg: float
    min_volume_vs_trigger_avg: float
    min_close_pos: float
    min_close_break_pct: float


@dataclass(frozen=True, slots=True)
class EntrySpec:
    entry_id: str
    title: str
    style: str


@dataclass(frozen=True, slots=True)
class StopSpec:
    stop_id: str
    title: str
    style: str


@dataclass(frozen=True, slots=True)
class ExitSpec:
    exit_id: str
    title: str
    target_rr: float
    max_hold_bars: int


def _load_feature_database() -> pd.DataFrame:
    path = BASE_INPUT_DIR / "anomaly_feature_database_with_accumulation.csv"
    if not path.exists():
        raise FileNotFoundError(f"Не найдена БД аномалий с признаком прогрева: {path}")
    frame = pd.read_csv(path, low_memory=False)
    for column in (
        "timestamp_ms",
        "trigger_open",
        "trigger_high",
        "trigger_low",
        "trigger_close",
        "trigger_volume",
        "trigger_return_pct",
        "range_atr",
        "body_atr",
        "close_to_high_frac",
    ):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_ms"], unit="ms", utc=True, errors="coerce")
    frame["month_utc"] = frame["timestamp_utc"].dt.strftime("%Y-%m")
    return frame


def _build_category_specs() -> tuple[CategorySpec, ...]:
    return (
        CategorySpec("warm_bodydrive_accum", "Азия: тёплый контекст, телесный импульс, есть прогрев", "context_warm", True),
        CategorySpec("warm_bodydrive_plain", "Азия: тёплый контекст, телесный импульс, без прогрева", "context_warm", False),
        CategorySpec("overheated_bodydrive_accum", "Азия: перегретый контекст, телесный импульс, есть прогрев", "context_overheated", True),
        CategorySpec("overheated_bodydrive_plain", "Азия: перегретый контекст, телесный импульс, без прогрева", "context_overheated", False),
    )


def _build_trigger_specs() -> tuple[TriggerSpec, ...]:
    return (
        TriggerSpec("single_loose", "Одна 1m свеча с умеренной агрессией", "single", 3, 0.8, 0.8, 0.65, 0.0000),
        TriggerSpec("single_strict", "Одна 1m свеча с сильной агрессией", "single", 5, 1.0, 1.0, 0.70, 0.0010),
        TriggerSpec("stack2_loose", "Две 1m свечи с набором импульса", "stack2", 5, 0.8, 0.8, 0.60, 0.0000),
    )


def _build_entry_specs() -> tuple[EntrySpec, ...]:
    return (EntrySpec("signal_close", "Вход по закрытию сигнальной 1m", "signal_close"),)


def _build_stop_specs() -> tuple[StopSpec, ...]:
    return (
        StopSpec("wave_low", "Стоп под low инициативной 1m", "wave_low"),
        StopSpec("wave_prev2_low", "Стоп под минимумом двух последних 1m", "wave_prev2_low"),
    )


def _build_exit_specs() -> tuple[ExitSpec, ...]:
    return (
        ExitSpec("tp15_t60", "TP 1.5R, hold 60m", 1.5, 60),
        ExitSpec("tp20_t90", "TP 2.0R, hold 90m", 2.0, 90),
    )


def _prepare_base_anomalies(frame: pd.DataFrame) -> pd.DataFrame:
    scoped = frame[
        (frame["session_id"].astype(str) == "asia")
        & (frame["market_side_hint"].astype(str) == "long")
        & (frame["impulse_archetype"].astype(str) == "impulse_body_drive")
    ].copy()
    scoped["has_accumulation"] = scoped["pre_accumulation_type"].astype(str).isin({"accumulation_clean", "accumulation_warm"})
    scoped["category_id"] = None
    scoped["category_title"] = None
    for spec in _build_category_specs():
        mask = scoped["context_archetype"].astype(str) == spec.context_archetype
        if spec.accumulation_required is True:
            mask &= scoped["has_accumulation"].astype(bool)
        elif spec.accumulation_required is False:
            mask &= ~scoped["has_accumulation"].astype(bool)
        scoped.loc[mask, "category_id"] = spec.category_id
        scoped.loc[mask, "category_title"] = spec.title
    scoped = scoped.dropna(subset=["category_id"]).copy()
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


def _build_coverage_summary(frame: pd.DataFrame, full_frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for category_id, category_title in full_frame[["category_id", "category_title"]].drop_duplicates().itertuples(index=False):
        total_current = int(len(full_frame[(full_frame["category_id"] == category_id) & (full_frame["dataset"] == "current")]))
        total_old = int(len(full_frame[(full_frame["category_id"] == category_id) & (full_frame["dataset"] == "old")]))
        covered_current = int(len(frame[(frame["category_id"] == category_id) & (frame["dataset"] == "current")]))
        covered_old = int(len(frame[(frame["category_id"] == category_id) & (frame["dataset"] == "old")]))
        rows.append(
            {
                "category_id": category_id,
                "category_title": category_title,
                "current_total": total_current,
                "current_with_m1": covered_current,
                "current_m1_coverage": float(covered_current / total_current) if total_current > 0 else 0.0,
                "old_total": total_old,
                "old_with_m1": covered_old,
                "old_m1_coverage": float(covered_old / total_old) if total_old > 0 else 0.0,
            }
        )
    return pd.DataFrame(rows).sort_values("category_id").reset_index(drop=True)


def _load_m1_by_symbol(cache_scope: str, symbol: str, cache: dict[tuple[str, str], pd.DataFrame]) -> pd.DataFrame:
    key = (cache_scope, symbol)
    existing = cache.get(key)
    if existing is not None:
        return existing
    preparer = DataPreparer(CURRENT_CACHE_DIR if cache_scope == "current" else PREV_CACHE_DIR)
    loaded = preparer.load_symbol_data(symbol, Timeframe.M1)
    if loaded.empty:
        cache[key] = loaded
        return loaded
    loaded = loaded.sort_values("timestamp").reset_index(drop=True)
    cache[key] = loaded
    return loaded


def _bar_close_position(high_price: float, low_price: float, close_price: float) -> float:
    bar_range = high_price - low_price
    if bar_range <= 0.0:
        return 0.5
    return float((close_price - low_price) / bar_range)


def _net_long_return(entry_price: float, exit_price: float) -> float:
    if entry_price <= 0.0 or exit_price <= 0.0:
        return 0.0
    return float((exit_price * (1.0 - 0.0004) / (entry_price * (1.0 + 0.0004))) - 1.0)


def _find_second_wave_signal(
    *,
    candles_1m: pd.DataFrame,
    start_idx: int,
    trigger_spec: TriggerSpec,
    trigger_high: float,
    trigger_body: float,
    trigger_volume: float,
) -> dict[str, object] | None:
    if trigger_body <= 0.0 or trigger_volume <= 0.0:
        return None
    avg_trigger_body_1m = trigger_body / 5.0
    avg_trigger_volume_1m = trigger_volume / 5.0
    highs = candles_1m["high"].astype(float).to_numpy()
    lows = candles_1m["low"].astype(float).to_numpy()
    opens = candles_1m["open"].astype(float).to_numpy()
    closes = candles_1m["close"].astype(float).to_numpy()
    volumes = candles_1m["volume"].astype(float).to_numpy()
    timestamps = candles_1m["timestamp"].astype("int64").to_numpy()
    end_idx = min(len(candles_1m), start_idx + trigger_spec.max_wait_bars)

    if trigger_spec.family == "single":
        for idx in range(start_idx, end_idx):
            body = closes[idx] - opens[idx]
            if body <= 0.0:
                continue
            body_ratio = body / avg_trigger_body_1m if avg_trigger_body_1m > 0 else 0.0
            volume_ratio = volumes[idx] / avg_trigger_volume_1m if avg_trigger_volume_1m > 0 else 0.0
            close_pos = _bar_close_position(highs[idx], lows[idx], closes[idx])
            close_break_pct = (closes[idx] - trigger_high) / trigger_high if trigger_high > 0 else 0.0
            if (
                body_ratio >= trigger_spec.min_body_vs_trigger_avg
                and volume_ratio >= trigger_spec.min_volume_vs_trigger_avg
                and close_pos >= trigger_spec.min_close_pos
                and close_break_pct >= trigger_spec.min_close_break_pct
            ):
                return {
                    "signal_bar_index": idx,
                    "signal_close_ts": int(timestamps[idx] + ONE_MINUTE_MS),
                    "signal_high": float(highs[idx]),
                    "signal_low": float(lows[idx]),
                    "signal_close": float(closes[idx]),
                    "body_ratio": float(body_ratio),
                    "volume_ratio": float(volume_ratio),
                    "close_pos": float(close_pos),
                }
        return None

    for idx in range(start_idx + 1, end_idx):
        first_idx = idx - 1
        if first_idx < start_idx:
            continue
        pair_open = float(opens[first_idx])
        pair_close = float(closes[idx])
        pair_body = pair_close - pair_open
        if pair_body <= 0.0:
            continue
        pair_volume = float(volumes[first_idx] + volumes[idx])
        body_ratio = pair_body / (avg_trigger_body_1m * 2.0) if avg_trigger_body_1m > 0 else 0.0
        volume_ratio = pair_volume / (avg_trigger_volume_1m * 2.0) if avg_trigger_volume_1m > 0 else 0.0
        close_pos = _bar_close_position(float(highs[idx]), float(lows[idx]), pair_close)
        close_break_pct = (pair_close - trigger_high) / trigger_high if trigger_high > 0 else 0.0
        if (
            body_ratio >= trigger_spec.min_body_vs_trigger_avg
            and volume_ratio >= trigger_spec.min_volume_vs_trigger_avg
            and close_pos >= trigger_spec.min_close_pos
            and close_break_pct >= trigger_spec.min_close_break_pct
        ):
            return {
                "signal_bar_index": idx,
                "signal_close_ts": int(timestamps[idx] + ONE_MINUTE_MS),
                "signal_high": float(max(highs[first_idx], highs[idx])),
                "signal_low": float(min(lows[first_idx], lows[idx])),
                "signal_close": pair_close,
                "body_ratio": float(body_ratio),
                "volume_ratio": float(volume_ratio),
                "close_pos": float(close_pos),
            }
    return None


def _resolve_entry(entry_spec: EntrySpec, signal: dict[str, object], candles_1m: pd.DataFrame) -> tuple[float, int, int] | None:
    idx = int(signal["signal_bar_index"])
    if entry_spec.style == "signal_close":
        return float(signal["signal_close"]), int(signal["signal_close_ts"]), idx + 1
    next_idx = idx + 1
    if next_idx >= len(candles_1m):
        return None
    next_bar = candles_1m.iloc[next_idx]
    return float(next_bar["open"]), int(next_bar["timestamp"]), next_idx


def _resolve_stop(stop_spec: StopSpec, signal: dict[str, object], candles_1m: pd.DataFrame, start_idx: int) -> float:
    idx = int(signal["signal_bar_index"])
    signal_low = float(signal["signal_low"])
    signal_high = float(signal["signal_high"])
    signal_range = max(0.0, signal_high - signal_low)
    if stop_spec.style == "wave_low":
        return signal_low
    if stop_spec.style == "wave_low_buf05":
        return signal_low - signal_range * 0.05
    prev_start = max(start_idx, idx - 1)
    window = candles_1m.iloc[prev_start : idx + 1]
    return float(pd.to_numeric(window["low"], errors="coerce").min())


def _simulate_trade(
    *,
    candles_1m: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    stop_price: float,
    exit_spec: ExitSpec,
) -> dict[str, object] | None:
    if entry_price <= 0.0 or stop_price <= 0.0 or stop_price >= entry_price:
        return None
    risk_pct = (entry_price - stop_price) / entry_price
    if risk_pct <= 0.0:
        return None
    target_price = entry_price * (1.0 + risk_pct * exit_spec.target_rr)
    future = candles_1m.iloc[entry_idx : min(len(candles_1m), entry_idx + exit_spec.max_hold_bars)].copy()
    if future.empty:
        return None
    for row in future.itertuples(index=False):
        low_price = float(row.low)
        high_price = float(row.high)
        ts = int(row.timestamp)
        if low_price <= stop_price and high_price >= target_price:
            exit_price = stop_price
            exit_reason = "stop_same_bar"
            exit_timestamp_ms = ts + ONE_MINUTE_MS
            return {"exit_price": exit_price, "exit_timestamp_ms": exit_timestamp_ms, "exit_reason": exit_reason, "exit_return_pct": _net_long_return(entry_price, exit_price), "initial_risk_pct": risk_pct, "target_rr": exit_spec.target_rr, "target_price": target_price}
        if low_price <= stop_price:
            exit_price = stop_price
            exit_reason = "stop"
            exit_timestamp_ms = ts + ONE_MINUTE_MS
            return {"exit_price": exit_price, "exit_timestamp_ms": exit_timestamp_ms, "exit_reason": exit_reason, "exit_return_pct": _net_long_return(entry_price, exit_price), "initial_risk_pct": risk_pct, "target_rr": exit_spec.target_rr, "target_price": target_price}
        if high_price >= target_price:
            exit_price = target_price
            exit_reason = "target"
            exit_timestamp_ms = ts + ONE_MINUTE_MS
            return {"exit_price": exit_price, "exit_timestamp_ms": exit_timestamp_ms, "exit_reason": exit_reason, "exit_return_pct": _net_long_return(entry_price, exit_price), "initial_risk_pct": risk_pct, "target_rr": exit_spec.target_rr, "target_price": target_price}
    last = future.iloc[-1]
    exit_price = float(last["close"])
    exit_timestamp_ms = int(last["timestamp"]) + ONE_MINUTE_MS
    return {"exit_price": exit_price, "exit_timestamp_ms": exit_timestamp_ms, "exit_reason": "time_exit", "exit_return_pct": _net_long_return(entry_price, exit_price), "initial_risk_pct": risk_pct, "target_rr": exit_spec.target_rr, "target_price": target_price}


def _simulate_online_events(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    m1_cache: dict[tuple[str, str], pd.DataFrame] = {}
    trigger_specs = _build_trigger_specs()
    entry_specs = _build_entry_specs()
    stop_specs = _build_stop_specs()
    exit_specs = _build_exit_specs()

    for row in frame.itertuples(index=False):
        candles_1m = _load_m1_by_symbol(str(row.dataset), str(row.symbol), m1_cache)
        if candles_1m.empty:
            continue
        anomaly_close_ts = int(row.timestamp_ms) + FIVE_MINUTES_MS
        timestamps = candles_1m["timestamp"].astype("int64")
        start_candidates = timestamps[timestamps >= anomaly_close_ts]
        if start_candidates.empty:
            continue
        start_idx = int(start_candidates.index[0])
        trigger_body = max(0.0, float(row.trigger_close) - float(row.trigger_open))
        trigger_volume = float(row.trigger_volume)
        trigger_high = float(row.trigger_high)
        for trigger_spec in trigger_specs:
            signal = _find_second_wave_signal(
                candles_1m=candles_1m,
                start_idx=start_idx,
                trigger_spec=trigger_spec,
                trigger_high=trigger_high,
                trigger_body=trigger_body,
                trigger_volume=trigger_volume,
            )
            if signal is None:
                continue
            for entry_spec in entry_specs:
                entry_resolved = _resolve_entry(entry_spec, signal, candles_1m)
                if entry_resolved is None:
                    continue
                entry_price, entry_timestamp_ms, sim_start_idx = entry_resolved
                for stop_spec in stop_specs:
                    stop_price = _resolve_stop(stop_spec, signal, candles_1m, start_idx)
                    for exit_spec in exit_specs:
                        trade = _simulate_trade(
                            candles_1m=candles_1m,
                            entry_idx=sim_start_idx,
                            entry_price=entry_price,
                            stop_price=stop_price,
                            exit_spec=exit_spec,
                        )
                        if trade is None:
                            continue
                        rows.append(
                            {
                                "category_id": row.category_id,
                                "category_title": row.category_title,
                                "dataset": row.dataset,
                                "symbol": row.symbol,
                                "timestamp_ms": int(row.timestamp_ms),
                                "month_utc": row.month_utc,
                                "signal_key": row.signal_key,
                                "entry_timestamp_ms": int(entry_timestamp_ms),
                                "entry_price": float(entry_price),
                                "exit_timestamp_ms": int(trade["exit_timestamp_ms"]),
                                "exit_price": float(trade["exit_price"]),
                                "exit_reason": trade["exit_reason"],
                                "exit_return_pct": float(trade["exit_return_pct"]),
                                "initial_risk_pct": float(trade["initial_risk_pct"]),
                                "target_rr": float(trade["target_rr"]),
                                "target_price": float(trade["target_price"]),
                                "trigger_id": trigger_spec.trigger_id,
                                "entry_id": entry_spec.entry_id,
                                "stop_id": stop_spec.stop_id,
                                "exit_id": exit_spec.exit_id,
                                "model_id": f"{trigger_spec.trigger_id}__{entry_spec.entry_id}__{stop_spec.stop_id}__{exit_spec.exit_id}",
                                "model_title": f"{trigger_spec.title} | {entry_spec.title} | {stop_spec.title} | {exit_spec.title}",
                                "signal_body_ratio": float(signal["body_ratio"]),
                                "signal_volume_ratio": float(signal["volume_ratio"]),
                                "signal_close_pos": float(signal["close_pos"]),
                                "signal_bar_delay": int(signal["signal_bar_index"] - start_idx + 1),
                                "has_accumulation": bool(row.has_accumulation),
                                "context_archetype": row.context_archetype,
                                "pre_accumulation_type": row.pre_accumulation_type,
                            }
                        )
    return pd.DataFrame(rows)


def _summarize_models(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for (category_id, model_id), scoped in events.groupby(["category_id", "model_id"], sort=True):
        current = scoped[scoped["dataset"] == "current"].copy()
        old = scoped[scoped["dataset"] == "old"].copy()
        current_summary = _summarize_events(current, calendar_months=_calendar_months_from_frames(current)) or {}
        old_summary = _summarize_events(old, calendar_months=_calendar_months_from_frames(old)) or {}
        combined_summary = _summarize_events(scoped, calendar_months=_calendar_months_from_frames(scoped)) or {}
        combined_equity, _ = _simulate_equity_risk_metrics(scoped, calendar_months=_calendar_months_from_frames(scoped), risk_fraction=0.05)
        current_mean = float(current_summary.get("mean_return_pct", 0.0))
        old_mean = float(old_summary.get("mean_return_pct", 0.0))
        current_wr = float(current_summary.get("win_rate", 0.0))
        old_wr = float(old_summary.get("win_rate", 0.0))
        current_trades = int(current_summary.get("trades_count", 0))
        old_trades = int(old_summary.get("trades_count", 0))
        stability_score = min(current_mean, old_mean) * 100.0 + min(current_wr, old_wr) * 10.0 + math.log1p(current_trades + old_trades) + float(combined_equity.get("equity_annualized_return_pct", 0.0)) * 0.10
        rows.append(
            {
                "category_id": category_id,
                "category_title": str(scoped.iloc[0]["category_title"]),
                "model_id": model_id,
                "model_title": str(scoped.iloc[0]["model_title"]),
                "current_trades": current_trades,
                "old_trades": old_trades,
                "current_mean_return_pct": current_mean,
                "old_mean_return_pct": old_mean,
                "current_win_rate": current_wr,
                "old_win_rate": old_wr,
                "combined_trades": int(combined_summary.get("trades_count", 0)),
                "combined_trades_per_year": float(combined_summary.get("trades_per_year", 0.0)),
                "combined_mean_return_pct": float(combined_summary.get("mean_return_pct", 0.0)),
                "combined_win_rate": float(combined_summary.get("win_rate", 0.0)),
                "combined_annualized_unit_pnl_pct": float(combined_summary.get("annualized_unit_pnl_pct", 0.0)),
                "combined_max_drawdown_pct": float(combined_summary.get("max_drawdown_pct", 0.0)),
                "combined_positive_months_count": int(combined_summary.get("positive_months_count", 0)),
                "combined_non_positive_months_count": int(combined_summary.get("non_positive_months_count", 0)),
                "combined_equity_annualized_return_pct": float(combined_equity.get("equity_annualized_return_pct", 0.0)),
                "combined_equity_max_drawdown_pct": float(combined_equity.get("equity_max_drawdown_pct", 0.0)),
                "combined_equity_stable_positive_months_count": int(combined_equity.get("equity_stable_positive_months_count", 0)),
                "stability_score": float(stability_score),
            }
        )
    return pd.DataFrame(rows).sort_values(["category_id", "stability_score", "combined_mean_return_pct", "combined_equity_annualized_return_pct"], ascending=[True, False, False, False]).reset_index(drop=True)


def _pick_best_models(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return summary
    eligible = summary[
        (summary["current_trades"] >= 8)
        & (summary["old_trades"] >= 4)
        & (summary["current_mean_return_pct"] > 0.0)
        & (summary["old_mean_return_pct"] > 0.0)
        & (summary["current_win_rate"] >= 0.45)
        & (summary["old_win_rate"] >= 0.45)
    ].copy()
    if eligible.empty:
        return eligible
    return eligible.groupby("category_id", sort=True).head(1).copy().reset_index(drop=True)


def _build_selected_portfolio(events: pd.DataFrame, picked: pd.DataFrame) -> pd.DataFrame:
    if events.empty or picked.empty:
        return pd.DataFrame(columns=events.columns)
    pairs = {(str(row.category_id), str(row.model_id)) for row in picked.itertuples(index=False)}
    mask = events.apply(lambda row: (str(row["category_id"]), str(row["model_id"])) in pairs, axis=1)
    return events[mask].copy().sort_values(["entry_timestamp_ms", "symbol"]).reset_index(drop=True)


def _build_report(coverage: pd.DataFrame, picked: pd.DataFrame, portfolio_current: dict[str, object], portfolio_old: dict[str, object], portfolio_combined: dict[str, object]) -> str:
    parts = [
        "# Онлайн-модель второй волны для азиатского long",
        "",
        "Здесь исследование максимально приближено к торговле. Используются только признаки, известные к моменту закрытия аномальной 5m, и закрывающиеся после неё 1m свечи.",
        "",
        "## Как определяется вход",
        "- сначала мы знаем только контекст, форму аномальной 5m и был ли прогрев объёма до неё",
        "- затем ждём уже закрытую 1m или стек из двух 1m, которые по телу и объёму сопоставимы со средней минутой первой волны",
        "- если такая инициатива закрылась выше high аномалии, это считается online-сигналом второй волны",
        "- вход либо по закрытию сигнальной 1m, либо на открытии следующей 1m",
        "",
        "## Покрытие 1m по категориям",
        _frame_to_markdown(coverage),
        "",
        "## Лучшие модели по категориям",
        _frame_to_markdown(picked),
        "",
        "## Портфель выбранных категорий: новый период",
        _frame_to_markdown(pd.DataFrame([portfolio_current])),
        "",
        "## Портфель выбранных категорий: старый период",
        _frame_to_markdown(pd.DataFrame([portfolio_old])),
        "",
        "## Портфель выбранных категорий: оба периода",
        _frame_to_markdown(pd.DataFrame([portfolio_combined])),
        "",
        "## Практический смысл",
        "- это уже попытка покупать не сам факт аномалии, а подтверждённый перезапуск спроса",
        "- стоп ставится под инициативную 1m или под минимум последних двух 1m, а не под весь 5m-памп",
        "- модель честная в том смысле, что не использует будущие 5m характеристики вроде buyer_wave_match как триггер входа",
    ]
    return "\n".join(parts)


def run_online_second_wave_long_research(output_dir: Path | None = None) -> dict[str, object]:
    target_dir = output_dir or OUTPUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    feature_db = _load_feature_database()
    prepared = _prepare_base_anomalies(feature_db)
    covered = _apply_m1_coverage(prepared)
    coverage = _build_coverage_summary(covered, prepared)
    coverage.to_csv(target_dir / "category_m1_coverage.csv", index=False)
    events = _simulate_online_events(covered)
    events.to_csv(target_dir / "online_second_wave_events.csv", index=False)
    summary = _summarize_models(events)
    summary.to_csv(target_dir / "online_second_wave_model_summary.csv", index=False)
    picked = _pick_best_models(summary)
    picked.to_csv(target_dir / "online_second_wave_best_by_category.csv", index=False)
    selected = _build_selected_portfolio(events, picked)
    selected.to_csv(target_dir / "online_second_wave_selected_portfolio_events.csv", index=False)

    current_events = selected[selected["dataset"] == "current"].copy()
    old_events = selected[selected["dataset"] == "old"].copy()
    current_months = _calendar_months_from_frames(current_events)
    old_months = _calendar_months_from_frames(old_events)
    combined_months = _calendar_months_from_frames(selected)
    portfolio_current = _summarize_events(current_events, calendar_months=current_months) or {}
    portfolio_old = _summarize_events(old_events, calendar_months=old_months) or {}
    portfolio_combined = _summarize_events(selected, calendar_months=combined_months) or {}
    portfolio_current.update(_simulate_equity_risk_metrics(current_events, calendar_months=current_months, risk_fraction=0.05)[0])
    portfolio_old.update(_simulate_equity_risk_metrics(old_events, calendar_months=old_months, risk_fraction=0.05)[0])
    portfolio_combined.update(_simulate_equity_risk_metrics(selected, calendar_months=combined_months, risk_fraction=0.05)[0])

    pd.DataFrame([portfolio_current]).to_csv(target_dir / "online_second_wave_selected_portfolio_summary_current.csv", index=False)
    pd.DataFrame([portfolio_old]).to_csv(target_dir / "online_second_wave_selected_portfolio_summary_old.csv", index=False)
    pd.DataFrame([portfolio_combined]).to_csv(target_dir / "online_second_wave_selected_portfolio_summary_combined.csv", index=False)
    _build_monthly_returns_frame(current_events, calendar_months=current_months).to_csv(target_dir / "online_second_wave_selected_monthly_current.csv", index=False)
    _build_monthly_returns_frame(old_events, calendar_months=old_months).to_csv(target_dir / "online_second_wave_selected_monthly_old.csv", index=False)
    _build_monthly_returns_frame(selected, calendar_months=combined_months).to_csv(target_dir / "online_second_wave_selected_monthly_combined.csv", index=False)
    report = _build_report(coverage, picked, portfolio_current, portfolio_old, portfolio_combined)
    (target_dir / "online_second_wave_long_report.md").write_text(report, encoding="utf-8")

    return {"coverage": coverage, "events": events, "summary": summary, "picked": picked, "selected": selected, "portfolio_current": portfolio_current, "portfolio_old": portfolio_old, "portfolio_combined": portfolio_combined}


if __name__ == "__main__":
    run_online_second_wave_long_research()
