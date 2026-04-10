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
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "second_wave_confirm5m_research"
FIVE_MINUTES_MS = 300_000
FEE_RATE = 0.0004


@dataclass(frozen=True, slots=True)
class BaseCategory:
    category_id: str
    title: str
    context_archetype: str


@dataclass(frozen=True, slots=True)
class ConfirmModel:
    model_id: str
    title: str
    min_next_extension_pct: float
    min_next_close_pos: float
    max_next_pullback_frac: float
    min_next_return_pct: float
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
        "next_open",
        "next_high",
        "next_low",
        "next_close",
        "next_return_pct",
        "next_pullback_frac",
        "next_close_pos_in_bar",
        "next_extension_above_trigger_high_pct",
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


def _build_models() -> tuple[ConfirmModel, ...]:
    models: list[ConfirmModel] = []
    for min_next_extension_pct in (0.0, 0.005):
        for min_next_close_pos in (0.65, 0.75):
            for max_next_pullback_frac in (0.25,):
                for min_next_return_pct in (0.003, 0.005):
                    for stop_style in ("confirm_low", "confirm_body_low"):
                        for rr_target in (1.5, 2.0):
                            model_id = (
                                f"c5m_ex{int(min_next_extension_pct * 1000):03d}"
                                f"_cp{int(min_next_close_pos * 100):02d}"
                                f"_pb{int(max_next_pullback_frac * 100):02d}"
                                f"_rt{int(min_next_return_pct * 1000):03d}"
                                f"_{stop_style}_rr{int(rr_target * 10):02d}"
                            )
                            models.append(
                                ConfirmModel(
                                    model_id=model_id,
                                    title=(
                                        f"Вторая 5m волна: ext>={min_next_extension_pct:.1%}, "
                                        f"close_pos>={min_next_close_pos:.0%}, pullback<={max_next_pullback_frac:.0%}, "
                                        f"ret>={min_next_return_pct:.1%}, {stop_style}, TP {rr_target:.1f}R"
                                    ),
                                    min_next_extension_pct=min_next_extension_pct,
                                    min_next_close_pos=min_next_close_pos,
                                    max_next_pullback_frac=max_next_pullback_frac,
                                    min_next_return_pct=min_next_return_pct,
                                    stop_style=stop_style,
                                    rr_target=rr_target,
                                    max_hold_bars=48,
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
    for category in _base_categories():
        mask = scoped["context_archetype"].astype(str) == category.context_archetype
        scoped.loc[mask, "base_category_id"] = category.category_id
    scoped = scoped.dropna(subset=["base_category_id"]).copy()
    scoped["cache_scope"] = scoped["dataset"].map({"current": "current", "old": "old"})
    return scoped.reset_index(drop=True)


def _load_m5(cache_scope: str, symbol: str, cache: dict[tuple[str, str], pd.DataFrame]) -> pd.DataFrame:
    key = (cache_scope, symbol)
    if key in cache:
        return cache[key]
    preparer = DataPreparer(CURRENT_CACHE_DIR if cache_scope == "current" else PREV_CACHE_DIR)
    frame = preparer.load_symbol_data(symbol, Timeframe.M5)
    if frame.empty:
        cache[key] = frame
        return frame
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    cache[key] = frame
    return frame


def _prepare_symbol_payload(candles_5m: pd.DataFrame) -> dict[str, object]:
    timestamps = pd.to_numeric(candles_5m["timestamp"], errors="coerce").astype("int64").tolist()
    return {
        "timestamps": timestamps,
        "timestamp_index": {int(ts): idx for idx, ts in enumerate(timestamps)},
        "opens": pd.to_numeric(candles_5m["open"], errors="coerce").astype(float).tolist(),
        "highs": pd.to_numeric(candles_5m["high"], errors="coerce").astype(float).tolist(),
        "lows": pd.to_numeric(candles_5m["low"], errors="coerce").astype(float).tolist(),
        "closes": pd.to_numeric(candles_5m["close"], errors="coerce").astype(float).tolist(),
    }


def _net_long_return(entry_price: float, exit_price: float) -> float:
    if entry_price <= 0.0 or exit_price <= 0.0:
        return 0.0
    return float((exit_price * (1.0 - FEE_RATE) / (entry_price * (1.0 + FEE_RATE))) - 1.0)


def _resolve_exit(
    *,
    timestamps: list[int],
    highs: list[float],
    lows: list[float],
    closes: list[float],
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
        if lows[idx] <= stop_price:
            return idx, stop_price, "stop"
        if highs[idx] >= target_price:
            return idx, target_price, "tp"
    return last_idx, closes[last_idx], "time_exit"


def _simulate_event(event_row: pd.Series, payload: dict[str, object], model: ConfirmModel) -> dict[str, object] | None:
    if float(event_row["next_close"]) <= float(event_row["trigger_high"]):
        return None
    if float(event_row["next_extension_above_trigger_high_pct"]) < model.min_next_extension_pct:
        return None
    if float(event_row["next_close_pos_in_bar"]) < model.min_next_close_pos:
        return None
    if float(event_row["next_pullback_frac"]) > model.max_next_pullback_frac:
        return None
    if float(event_row["next_return_pct"]) < model.min_next_return_pct:
        return None

    timestamp_index = payload["timestamp_index"]
    anomaly_idx = timestamp_index.get(int(event_row["timestamp_ms"]))
    if anomaly_idx is None:
        return None
    confirm_idx = anomaly_idx + 1
    timestamps = payload["timestamps"]
    if confirm_idx >= len(timestamps):
        return None
    opens = payload["opens"]
    highs = payload["highs"]
    lows = payload["lows"]
    closes = payload["closes"]
    confirm_open = opens[confirm_idx]
    confirm_close = closes[confirm_idx]
    confirm_low = lows[confirm_idx]
    entry_price = confirm_close
    if model.stop_style == "confirm_body_low":
        stop_price = min(confirm_open, confirm_close)
    else:
        stop_price = confirm_low
    if stop_price >= entry_price:
        return None
    exit_idx, exit_price, exit_reason = _resolve_exit(
        timestamps=timestamps,
        highs=highs,
        lows=lows,
        closes=closes,
        entry_idx=confirm_idx,
        entry_price=entry_price,
        stop_price=stop_price,
        rr_target=model.rr_target,
        max_hold_bars=model.max_hold_bars,
    )
    return {
        "entry_timestamp_ms": int(timestamps[confirm_idx] + FIVE_MINUTES_MS),
        "entry_price": float(entry_price),
        "stop_price": float(stop_price),
        "initial_risk_pct": float((entry_price - stop_price) / entry_price),
        "exit_timestamp_ms": int(timestamps[exit_idx] + FIVE_MINUTES_MS),
        "exit_price": float(exit_price),
        "exit_reason": exit_reason,
        "exit_return_pct": _net_long_return(entry_price, exit_price),
    }


def _build_events(frame: pd.DataFrame, models: tuple[ConfirmModel, ...]) -> pd.DataFrame:
    cache: dict[tuple[str, str], pd.DataFrame] = {}
    records: list[dict[str, object]] = []
    for (cache_scope, symbol), scoped in frame.groupby(["cache_scope", "symbol"], sort=True):
        candles_5m = _load_m5(str(cache_scope), str(symbol), cache)
        if candles_5m.empty:
            continue
        payload = _prepare_symbol_payload(candles_5m)
        for _, row in scoped.iterrows():
            base = {
                "base_category_id": row["base_category_id"],
                "dataset": row["dataset"],
                "symbol": row["symbol"],
                "timestamp_ms": int(row["timestamp_ms"]),
                "month_utc": row["month_utc"],
                "pre_accumulation_type": row["pre_accumulation_type"],
                "trigger_return_pct": float(row["trigger_return_pct"]),
                "range_atr": float(row["range_atr"]),
                "volume_mult": float(row["volume_mult"]),
            }
            for model in models:
                simulated = _simulate_event(row, payload, model)
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
        combined_summary = _summarize_events(combined)
        combined_calendar = _calendar_months_from_frames(combined)
        combined_eq, _ = _simulate_equity_risk_metrics(combined, calendar_months=combined_calendar, risk_fraction=0.05)
        combined_months = _build_monthly_returns_frame(combined)
        rows.append(
            {
                "base_category_id": category_id,
                "model_id": model_id,
                "current_trades": int(len(current)),
                "old_trades": int(len(old)),
                "combined_trades_per_year": float(combined_summary["trades_per_year"]),
                "combined_mean_return_pct": float(combined_summary["mean_return_pct"]),
                "combined_win_rate": float(combined_summary["win_rate"]),
                "combined_annualized_unit_pnl_pct": float(combined_summary["annualized_unit_pnl_pct"]),
                "equity_annualized_return_pct_5": float(combined_eq["equity_annualized_return_pct"]),
                "equity_max_drawdown_pct_5": float(combined_eq["equity_max_drawdown_pct"]),
                "equity_stable_positive_months_count": int(combined_eq["equity_stable_positive_months_count"]),
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
        ["score", "equity_annualized_return_pct_5", "combined_mean_return_pct", "combined_trades_per_year"],
        ascending=[False, False, False, False],
    ).reset_index(drop=True)


def _build_report(summary: pd.DataFrame) -> str:
    lines = [
        "# Подтверждённая вторая 5m волна",
        "",
        "Тест максимально близок к торговым условиям:",
        "- сначала закрывается аномальная `5m` свеча;",
        "- потом полностью закрывается следующая `5m` свеча;",
        "- только после её закрытия принимается решение о входе;",
        "- стоп ставится под подтверждающую `5m` свечу или под её тело.",
        "",
    ]
    if summary.empty:
        lines.append("Подходящих моделей не найдено.")
        return "\n".join(lines)
    top = summary.iloc[0]
    lines.extend(
        [
            "## Главный вывод",
            (
                f"Лучший онлайн-подход сейчас: `{top['model_id']}` в категории `{top['base_category_id']}`."
            ),
            (
                f"- сделки/год: `{top['combined_trades_per_year']:.1f}`; "
                f"mean trade: `{top['combined_mean_return_pct'] * 100:.2f}%`; "
                f"WR: `{top['combined_win_rate'] * 100:.1f}%`."
            ),
            (
                f"- equity @5%: `{top['equity_annualized_return_pct_5'] * 100:.1f}%`, "
                f"DD `{top['equity_max_drawdown_pct_5'] * 100:.1f}%`, "
                f"устойчивых месяцев `{int(top['equity_stable_positive_months_count'])}`."
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
        ]
    )
    return "\n".join(lines)


def run() -> dict[str, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    frame = _prepare_scope(_load_anomalies())
    models = _build_models()
    events = _build_events(frame, models)
    summary = _summarize_models(events)
    events_path = OUTPUT_DIR / "events.csv"
    summary_path = OUTPUT_DIR / "summary.csv"
    report_path = OUTPUT_DIR / "report.md"
    events.to_csv(events_path, index=False)
    summary.to_csv(summary_path, index=False)
    report_path.write_text(_build_report(summary), encoding="utf-8")
    return {"events": events_path, "summary": summary_path, "report": report_path}


if __name__ == "__main__":
    run()
