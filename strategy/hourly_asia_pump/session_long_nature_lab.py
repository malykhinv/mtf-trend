from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from domain.enums.timeframe import Timeframe
from strategy.hourly_asia_pump.anomaly_category_lab import (
    CURRENT_CACHE_DIR,
    PREV_CACHE_DIR,
    _assign_categories,
    _attach_symbol_age,
    _compute_forward_features,
    _frame_to_markdown,
    _harmonize_anomaly_features,
    _summarize_events,
)
from strategy.hourly_asia_pump.config import build_hourly_asia_pump_trade_models
from strategy.hourly_asia_pump.research import _build_trade_model_events_for_timeframe
from strategy.hourly_asia_pump.session_short_edge import _ALL_5M_MINUTES, _build_session_anomalies
from vectorbt_runner.data_preparer import DataPreparer

module_logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "session_long_nature_lab"
RAW_ANOMALIES_CACHE = OUTPUT_DIR / "raw_anomalies.csv"
ENRICHED_ANOMALIES_CACHE = OUTPUT_DIR / "enriched_anomalies.csv"


def _markdown(frame: pd.DataFrame, columns: list[str]) -> str:
    if frame.empty:
        return "_empty_"
    present = [column for column in columns if column in frame.columns]
    if not present:
        return "_no columns_"
    return _frame_to_markdown(frame[present], columns=present)


def _calendar_months_from_frame(frame: pd.DataFrame) -> list[str]:
    if frame.empty or "timestamp_ms" not in frame.columns:
        return []
    timestamps = pd.to_numeric(frame["timestamp_ms"], errors="coerce").dropna()
    if timestamps.empty:
        return []
    start = pd.to_datetime(int(timestamps.min()), unit="ms", utc=True).tz_localize(None).to_period("M")
    end = pd.to_datetime(int(timestamps.max()), unit="ms", utc=True).tz_localize(None).to_period("M")
    return [str(period) for period in pd.period_range(start=start, end=end, freq="M")]


def _signal_key(frame: pd.DataFrame) -> pd.Series:
    return (
        frame["dataset"].astype(str)
        + "|"
        + frame["session_id"].astype(str)
        + "|"
        + frame["symbol"].astype(str)
        + "|"
        + pd.to_numeric(frame["timestamp_ms"], errors="coerce").astype("Int64").astype(str)
    )


def _load_session_long_anomalies(*, session_id: str, dataset: str, logger: logging.Logger) -> pd.DataFrame:
    cache_dir = CURRENT_CACHE_DIR if dataset == "current" else PREV_CACHE_DIR
    preparer = DataPreparer(cache_dir)
    symbols = preparer.list_symbols(Timeframe.M5)
    anomalies = _build_session_anomalies(
        preparer=preparer,
        timeframe=Timeframe.M5,
        session_id=session_id,
        symbols=symbols,
        allowed_trigger_minutes=_ALL_5M_MINUTES,
        logger=logger,
    )
    if anomalies.empty:
        return anomalies
    anomalies = _harmonize_anomaly_features(anomalies)
    anomalies["session_id"] = session_id
    anomalies["market_side_hint"] = "long"
    anomalies["dataset"] = dataset
    anomalies["cache_scope"] = dataset
    anomalies["anomaly_key"] = _signal_key(anomalies)
    anomalies["signal_key"] = anomalies["anomaly_key"]
    return anomalies.reset_index(drop=True)


def _attach_next_bar_features(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    enriched = frame.copy()
    for cache_scope, scoped in enriched.groupby("cache_scope", sort=True):
        cache_dir = CURRENT_CACHE_DIR if str(cache_scope) == "current" else PREV_CACHE_DIR
        preparer = DataPreparer(cache_dir)
        for symbol, symbol_rows in scoped.groupby("symbol", sort=True):
            candles = preparer.load_symbol_data(str(symbol), Timeframe.M5)
            if candles.empty:
                continue
            opens = pd.to_numeric(candles["open"], errors="coerce").to_numpy(dtype="float64")
            highs = pd.to_numeric(candles["high"], errors="coerce").to_numpy(dtype="float64")
            lows = pd.to_numeric(candles["low"], errors="coerce").to_numpy(dtype="float64")
            closes = pd.to_numeric(candles["close"], errors="coerce").to_numpy(dtype="float64")
            updates: list[dict[str, object]] = []
            for idx, row in symbol_rows.iterrows():
                row_index = int(pd.to_numeric(row["row_index"], errors="coerce"))
                next_idx = row_index + 1
                if next_idx >= len(opens):
                    continue
                trigger_high = float(row["trigger_high"])
                trigger_low = float(row["trigger_low"])
                trigger_range = max(1e-12, trigger_high - trigger_low)
                next_open = float(opens[next_idx])
                next_high = float(highs[next_idx])
                next_low = float(lows[next_idx])
                next_close = float(closes[next_idx])
                next_range = max(1e-12, next_high - next_low)
                updates.append(
                    {
                        "index": idx,
                        "next_open": next_open,
                        "next_high": next_high,
                        "next_low": next_low,
                        "next_close": next_close,
                        "next_return_pct": (next_close / next_open - 1.0) if next_open > 0 else None,
                        "next_pullback_frac": max(0.0, (trigger_high - next_low) / trigger_range),
                        "next_close_from_high_frac": (next_high - next_close) / next_range,
                        "next_close_pos_in_bar": (next_close - next_low) / next_range,
                        "next_extension_above_trigger_high_pct": ((next_high / trigger_high) - 1.0)
                        if trigger_high > 0
                        else None,
                    }
                )
            if updates:
                update_frame = pd.DataFrame(updates).set_index("index")
                for column in update_frame.columns:
                    enriched.loc[update_frame.index, column] = update_frame[column]
    return enriched


def _ensure_trade_model_features(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    enriched = frame.copy()
    trigger_range_pct = pd.to_numeric(enriched.get("trigger_range_pct"), errors="coerce")
    range_atr = pd.to_numeric(enriched.get("range_atr"), errors="coerce")
    body_return_pct = pd.to_numeric(enriched.get("body_return_pct"), errors="coerce")
    body_atr_missing = "body_atr" not in enriched.columns or pd.to_numeric(enriched["body_atr"], errors="coerce").isna().all()
    if body_atr_missing:
        denom = trigger_range_pct.where(trigger_range_pct > 0)
        enriched["body_atr"] = pd.to_numeric((body_return_pct / denom) * range_atr, errors="coerce")
    pre_base_missing = "pre_base_range_vs_trigger" not in enriched.columns or pd.to_numeric(enriched["pre_base_range_vs_trigger"], errors="coerce").isna().all()
    if pre_base_missing:
        pre_base_range_pct_60m = pd.to_numeric(enriched.get("pre_base_range_pct_60m"), errors="coerce")
        denom = trigger_range_pct.where(trigger_range_pct > 0)
        enriched["pre_base_range_vs_trigger"] = pd.to_numeric(pre_base_range_pct_60m / denom, errors="coerce")
    return enriched


def _attach_pre_accumulation_type(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    enriched = frame.copy()
    if "pre_accumulation_type" in enriched.columns and enriched["pre_accumulation_type"].notna().any():
        return enriched
    vol = pd.to_numeric(enriched.get("pre_avg_volume_ratio_30m"), errors="coerce")
    drift = pd.to_numeric(enriched.get("pre_dir_body_sum_pct_30m"), errors="coerce")
    abs_body = pd.to_numeric(enriched.get("pre_abs_body_sum_pct_30m"), errors="coerce")
    max_range = pd.to_numeric(enriched.get("pre_max_range_pct_30m"), errors="coerce")
    green = pd.to_numeric(enriched.get("pre_green_count_30m"), errors="coerce")
    red = pd.to_numeric(enriched.get("pre_red_count_30m"), errors="coerce")
    acc_type = pd.Series("none", index=enriched.index, dtype="object")
    distribution_mask = drift.le(-0.02) & abs_body.ge(0.08) & max_range.ge(0.05) & red.gt(green)
    hot_mask = drift.ge(0.04) & abs_body.ge(0.05) & max_range.ge(0.03) & vol.ge(0.80) & green.ge(red)
    warm_mask = drift.ge(0.012) & drift.lt(0.04) & abs_body.le(0.05) & max_range.le(0.03) & green.ge(red)
    clean_mask = drift.ge(0.002) & drift.lt(0.012) & abs_body.le(0.025) & max_range.le(0.012) & green.ge(red)
    acc_type.loc[distribution_mask] = "distribution_like"
    acc_type.loc[hot_mask] = "volume_ramp_hot"
    acc_type.loc[warm_mask] = "accumulation_warm"
    acc_type.loc[clean_mask] = "accumulation_clean"
    enriched["pre_accumulation_type"] = acc_type
    return enriched


def _build_long_scenarios(*, anomalies: pd.DataFrame, dataset: str, logger: logging.Logger) -> pd.DataFrame:
    if anomalies.empty:
        return pd.DataFrame()
    preparer = DataPreparer(CURRENT_CACHE_DIR if dataset == "current" else PREV_CACHE_DIR)
    models = build_hourly_asia_pump_trade_models()
    raw_events = _build_trade_model_events_for_timeframe(
        preparer=preparer,
        timeframe=Timeframe.M5,
        selected_events=anomalies,
        trade_models=models,
        commission_rate=0.0004,
        logger=logger,
    )
    if raw_events.empty:
        return raw_events
    raw_events = raw_events.copy()
    raw_events["side"] = "long"
    raw_events["scenario_group"] = raw_events["session_id"].astype(str) + "_long_trade_model"
    raw_events["scenario_key"] = raw_events["session_id"].astype(str) + "_long::" + raw_events["trade_model_id"].astype(str)
    raw_events["scenario_title"] = raw_events["trade_model_label"].astype(str)
    raw_events["signal_key"] = _signal_key(raw_events)
    return raw_events.reset_index(drop=True)


def _summarize_category_models(anomalies: pd.DataFrame, scenario_events: pd.DataFrame) -> pd.DataFrame:
    if anomalies.empty or scenario_events.empty:
        return pd.DataFrame()
    triggered = scenario_events[scenario_events["trade_triggered"].fillna(False).astype(bool)].copy()
    if triggered.empty:
        return pd.DataFrame()
    category_columns = [
        "canonical_category",
        "context_archetype",
        "impulse_archetype",
        "confirmation_archetype",
        "wave_archetype",
        "pre_accumulation_type",
    ]
    if not set(category_columns).issubset(triggered.columns):
        joined = triggered.merge(
            anomalies[
                [
                    "signal_key",
                    "dataset",
                    "session_id",
                    *category_columns,
                ]
            ],
            on=["signal_key", "dataset", "session_id"],
            how="inner",
        )
    else:
        joined = triggered.copy()
    rows: list[dict[str, object]] = []
    group_cols = [
        "session_id",
        "canonical_category",
        "context_archetype",
        "impulse_archetype",
        "confirmation_archetype",
        "wave_archetype",
        "pre_accumulation_type",
        "trade_model_label",
    ]
    for keys, scoped in joined.groupby(group_cols, sort=True):
        current = scoped[scoped["dataset"].astype(str) == "current"].copy()
        old = scoped[scoped["dataset"].astype(str) == "old"].copy()
        current_summary = _summarize_events(current, calendar_months=_calendar_months_from_frame(current)) or {}
        old_summary = _summarize_events(old, calendar_months=_calendar_months_from_frame(old)) or {}
        rows.append(
            {
                "session_id": keys[0],
                "canonical_category": keys[1],
                "context_archetype": keys[2],
                "impulse_archetype": keys[3],
                "confirmation_archetype": keys[4],
                "wave_archetype": keys[5],
                "pre_accumulation_type": keys[6],
                "trade_model_label": keys[7],
                "current_trades": int(len(current)),
                "old_trades": int(len(old)),
                "combined_trades": int(len(scoped)),
                "current_mean_return_pct": current_summary.get("mean_return_pct"),
                "old_mean_return_pct": old_summary.get("mean_return_pct"),
                "combined_mean_return_pct": pd.to_numeric(scoped["exit_return_pct"], errors="coerce").mean(),
                "current_win_rate": current_summary.get("win_rate"),
                "old_win_rate": old_summary.get("win_rate"),
            }
        )
    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary
    summary["both_positive"] = (
        (pd.to_numeric(summary["current_mean_return_pct"], errors="coerce") > 0)
        & (pd.to_numeric(summary["old_mean_return_pct"], errors="coerce") > 0)
    )
    summary["score"] = (
        pd.to_numeric(summary["combined_mean_return_pct"], errors="coerce").fillna(-999.0) * 100.0
        + pd.to_numeric(summary["current_trades"], errors="coerce").fillna(0.0) * 0.25
        + pd.to_numeric(summary["old_trades"], errors="coerce").fillna(0.0) * 0.50
    )
    return summary.sort_values(["score", "combined_trades"], ascending=[False, False]).reset_index(drop=True)


def _build_report(anomalies: pd.DataFrame, scenario_events: pd.DataFrame, summary: pd.DataFrame) -> str:
    lines = [
        "# Europe/America Long Universe By Nature",
        "",
        "Отдельная лаборатория для `long` вне Азии.",
        "",
        f"- аномалий в universe: `{len(anomalies)}`",
        f"- строк trade-model backtest: `{len(scenario_events)}`",
        f"- реально triggered long-сделок: `{int(scenario_events['trade_triggered'].fillna(False).astype(bool).sum()) if not scenario_events.empty else 0}`",
        "",
    ]
    if anomalies.empty:
        lines.append("Аномалий не найдено.")
        return "\n".join(lines)
    anomaly_summary = (
        anomalies.groupby(["session_id", "dataset"])
        .size()
        .reset_index(name="anomalies_count")
        .sort_values(["session_id", "dataset"])
    )
    lines.extend(["## Аномалии", _markdown(anomaly_summary, ["session_id", "dataset", "anomalies_count"]), ""])
    if scenario_events.empty:
        lines.append("Trade-модели не вернули даже диагностических строк.")
        return "\n".join(lines)
    trigger_summary = (
        scenario_events.groupby(["session_id", "trade_model_label", "trade_triggered", "entry_reason"], dropna=False)
        .size()
        .reset_index(name="rows")
        .sort_values(["session_id", "rows"], ascending=[True, False])
    )
    lines.extend(
        [
            "## Диагностика по моделям",
            _markdown(trigger_summary.head(40), ["session_id", "trade_model_label", "trade_triggered", "entry_reason", "rows"]),
            "",
        ]
    )
    if summary.empty:
        lines.append("Строгих natural long-категорий с triggered-сделками пока не найдено.")
        return "\n".join(lines)
    supported = summary[(summary["current_trades"] >= 8) & (summary["old_trades"] >= 4)].copy()
    promising = supported[supported["both_positive"].fillna(False)].copy()
    lines.extend(
        [
            "## Лучшие поддержанные категории",
            _markdown(
                supported.head(20),
                [
                    "session_id",
                    "canonical_category",
                    "context_archetype",
                    "impulse_archetype",
                    "confirmation_archetype",
                    "wave_archetype",
                    "pre_accumulation_type",
                    "trade_model_label",
                    "current_trades",
                    "old_trades",
                    "combined_mean_return_pct",
                    "current_mean_return_pct",
                    "old_mean_return_pct",
                    "current_win_rate",
                    "old_win_rate",
                ],
            ),
            "",
        ]
    )
    if not promising.empty:
        lines.extend(
            [
                "## Позитивные на обоих периодах",
                _markdown(
                    promising.head(20),
                    [
                        "session_id",
                        "canonical_category",
                        "context_archetype",
                        "impulse_archetype",
                        "confirmation_archetype",
                        "wave_archetype",
                        "pre_accumulation_type",
                        "trade_model_label",
                        "current_trades",
                        "old_trades",
                        "combined_mean_return_pct",
                        "current_mean_return_pct",
                        "old_mean_return_pct",
                        "current_win_rate",
                        "old_win_rate",
                    ],
                ),
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## Позитивные на обоих периодах",
                "Строгих природных long-категорий с положительным результатом и на новом, и на старом периоде пока не нашлось.",
            ]
        )
    return "\n".join(lines)


def run() -> dict[str, Path]:
    logger = module_logger
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if RAW_ANOMALIES_CACHE.exists():
        anomalies = pd.read_csv(RAW_ANOMALIES_CACHE, low_memory=False)
    else:
        anomaly_frames = [
            _load_session_long_anomalies(session_id="europe", dataset="current", logger=logger),
            _load_session_long_anomalies(session_id="europe", dataset="old", logger=logger),
            _load_session_long_anomalies(session_id="america", dataset="current", logger=logger),
            _load_session_long_anomalies(session_id="america", dataset="old", logger=logger),
        ]
        anomalies = (
            pd.concat([frame for frame in anomaly_frames if not frame.empty], ignore_index=True)
            if any(not frame.empty for frame in anomaly_frames)
            else pd.DataFrame()
        )
        if not anomalies.empty:
            anomalies.to_csv(RAW_ANOMALIES_CACHE, index=False)

    if ENRICHED_ANOMALIES_CACHE.exists():
        anomalies = pd.read_csv(ENRICHED_ANOMALIES_CACHE, low_memory=False)
    elif not anomalies.empty:
        anomalies = _attach_symbol_age(anomalies)
        anomalies = _attach_next_bar_features(anomalies)
        anomalies = _compute_forward_features(anomalies, logger=logger)
        anomalies = _ensure_trade_model_features(anomalies)
        anomalies = _attach_pre_accumulation_type(anomalies)
        anomalies = _assign_categories(anomalies)
        anomalies.to_csv(ENRICHED_ANOMALIES_CACHE, index=False)

    if not anomalies.empty:
        anomalies = _ensure_trade_model_features(anomalies)
        anomalies = _attach_pre_accumulation_type(anomalies)
        anomalies.to_csv(ENRICHED_ANOMALIES_CACHE, index=False)

    scenario_frames = []
    for dataset in ("current", "old"):
        scoped = anomalies[anomalies["dataset"].astype(str) == dataset].copy()
        if scoped.empty:
            continue
        scenario_frames.append(_build_long_scenarios(anomalies=scoped, dataset=dataset, logger=logger))
    scenario_events = (
        pd.concat([frame for frame in scenario_frames if not frame.empty], ignore_index=True)
        if any(not frame.empty for frame in scenario_frames)
        else pd.DataFrame()
    )
    summary = _summarize_category_models(anomalies, scenario_events)

    paths = {
        "anomalies": OUTPUT_DIR / "anomalies.csv",
        "scenario_events": OUTPUT_DIR / "scenario_events.csv",
        "summary": OUTPUT_DIR / "category_model_summary.csv",
        "report": OUTPUT_DIR / "report.md",
    }
    anomalies.to_csv(paths["anomalies"], index=False)
    scenario_events.to_csv(paths["scenario_events"], index=False)
    summary.to_csv(paths["summary"], index=False)
    paths["report"].write_text(_build_report(anomalies, scenario_events, summary), encoding="utf-8")
    return paths


if __name__ == "__main__":
    run()
