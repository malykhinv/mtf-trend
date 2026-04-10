from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from domain.enums.timeframe import Timeframe
from strategy.hourly_asia_pump.short_edge import (
    _build_execution_models as _build_short_execution_models,
    _build_geometries as _build_short_geometries,
    _build_geometry_events as _build_short_geometry_events,
    _prepare_anomaly_events as _prepare_short_anomaly_events,
    _summarize_atomic_models as _summarize_short_atomic_models,
)
from strategy.hourly_asia_pump.static_combo import _frame_to_markdown, _summarize_events
from strategy.hourly_asia_pump.unified_edge import _simulate_equity_risk_metrics
from vectorbt_runner.data_preparer import DataPreparer

module_logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
CURRENT_ROOT = REPO_ROOT / ".output" / "results" / "research" / "hourly_asia_pump_static_combo" / "latest_report"
PREV_ROOT = REPO_ROOT / ".output" / "results_prev_year_5m"
CURRENT_CACHE_DIR = REPO_ROOT / ".output" / "cache"
PREV_CACHE_DIR = REPO_ROOT / ".output" / "cache_prev_year_5m"
OUTPUT_DIR = PREV_ROOT / "anomaly_category_lab"
AGE_SOURCE_PATH = PREV_ROOT / "cross_period_meta_analysis" / "symbol_first_seen_union.csv"


@dataclass(frozen=True, slots=True)
class CategoryScenarioPick:
    category_id: str
    category_title: str
    scenario_key: str
    scenario_title: str
    session_id: str
    side: str


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Не найден CSV: {path}")
    return pd.read_csv(path)


def _safe_float(value: object) -> float | None:
    if value is None or value is pd.NA:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric):
        return None
    return numeric


def _calendar_months_from_frame(frame: pd.DataFrame) -> list[str]:
    if frame.empty or "timestamp_ms" not in frame.columns:
        return []
    timestamps = pd.to_numeric(frame["timestamp_ms"], errors="coerce").dropna()
    if timestamps.empty:
        return []
    start = pd.to_datetime(int(timestamps.min()), unit="ms", utc=True).tz_localize(None).to_period("M")
    end = pd.to_datetime(int(timestamps.max()), unit="ms", utc=True).tz_localize(None).to_period("M")
    return [str(period) for period in pd.period_range(start=start, end=end, freq="M")]


def _harmonize_anomaly_features(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    enriched = frame.copy()
    numeric_columns = [
        "row_index",
        "timestamp_ms",
        "hour_utc",
        "trigger_minute",
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
        "pre_base_range_vs_trigger",
        "upper_wick_frac",
        "lower_wick_frac",
        "body_frac",
        "next_open",
        "next_high",
        "next_low",
        "next_close",
        "next_return_pct",
        "next_pullback_frac",
        "next_close_from_high_frac",
        "next_close_pos_in_bar",
        "next_extension_above_trigger_high_pct",
    ]
    for column in numeric_columns:
        if column in enriched.columns:
            enriched[column] = pd.to_numeric(enriched[column], errors="coerce")
    if "timestamp_utc" in enriched.columns:
        enriched["timestamp_utc"] = pd.to_datetime(enriched["timestamp_utc"], utc=True, errors="coerce")
    if "month_utc" not in enriched.columns and "timestamp_utc" in enriched.columns:
        enriched["month_utc"] = enriched["timestamp_utc"].dt.strftime("%Y-%m")
    if "trigger_minute" not in enriched.columns and "timestamp_utc" in enriched.columns:
        enriched["trigger_minute"] = enriched["timestamp_utc"].dt.minute
    if {"trigger_open", "trigger_high", "trigger_low", "trigger_close"}.issubset(enriched.columns):
        trigger_range = (enriched["trigger_high"] - enriched["trigger_low"]).replace(0.0, pd.NA)
        top = enriched[["trigger_open", "trigger_close"]].max(axis=1)
        bottom = enriched[["trigger_open", "trigger_close"]].min(axis=1)
        if "upper_wick_frac" not in enriched.columns:
            enriched["upper_wick_frac"] = (enriched["trigger_high"] - top) / trigger_range
        if "lower_wick_frac" not in enriched.columns:
            enriched["lower_wick_frac"] = (bottom - enriched["trigger_low"]) / trigger_range
        if "body_frac" not in enriched.columns:
            enriched["body_frac"] = (enriched["trigger_close"] - enriched["trigger_open"]).abs() / trigger_range
        if "body_return_pct" not in enriched.columns:
            enriched["body_return_pct"] = (enriched["trigger_close"] - enriched["trigger_open"]).abs() / enriched["trigger_open"]
        if "trigger_close_pos_in_bar" not in enriched.columns:
            enriched["trigger_close_pos_in_bar"] = (enriched["trigger_close"] - enriched["trigger_low"]) / trigger_range
    return enriched


def _load_current_asia_anomalies() -> pd.DataFrame:
    frame = _harmonize_anomaly_features(_load_csv(CURRENT_ROOT / "source_inputs" / "trade_model_events.csv"))
    frame = frame[frame["timeframe"].astype(str) == Timeframe.M5.value].copy()
    base_columns = [
        "symbol", "timeframe", "row_index", "timestamp_ms", "timestamp_utc", "month_utc", "hour_utc", "trigger_minute",
        "trigger_open", "trigger_high", "trigger_low", "trigger_close", "trigger_volume", "trigger_return_pct",
        "trigger_range_pct", "range_atr", "body_atr", "volume_mult", "close_to_high_frac", "pre_base_range_pct_60m",
        "pre_base_drift_pct_60m", "pre_base_range_vs_trigger", "upper_wick_frac", "lower_wick_frac", "body_frac",
        "body_return_pct", "trigger_close_pos_in_bar", "next_open", "next_high", "next_low", "next_close",
        "next_return_pct", "next_pullback_frac", "next_close_from_high_frac", "next_close_pos_in_bar",
        "next_extension_above_trigger_high_pct",
    ]
    scoped = frame[[column for column in base_columns if column in frame.columns]].copy()
    scoped = scoped.sort_values(["symbol", "timestamp_ms"]).drop_duplicates(["symbol", "timestamp_ms"], keep="first")
    scoped["session_id"] = "asia"
    scoped["market_side_hint"] = "long"
    scoped["dataset"] = "current"
    scoped["cache_scope"] = "current"
    return scoped.reset_index(drop=True)


def _load_old_asia_anomalies() -> pd.DataFrame:
    frame = _harmonize_anomaly_features(_load_csv(PREV_ROOT / "research" / "hourly_asia_pump_prev_year_5m" / "trade_model_events.csv"))
    frame = frame[frame["timeframe"].astype(str) == Timeframe.M5.value].copy()
    keep = [
        "symbol", "timeframe", "row_index", "timestamp_ms", "timestamp_utc", "month_utc", "hour_utc", "trigger_minute",
        "trigger_open", "trigger_high", "trigger_low", "trigger_close", "trigger_volume", "trigger_return_pct",
        "trigger_range_pct", "range_atr", "body_atr", "volume_mult", "close_to_high_frac", "pre_base_range_pct_60m",
        "pre_base_drift_pct_60m", "pre_base_range_vs_trigger", "upper_wick_frac", "lower_wick_frac", "body_frac",
        "body_return_pct", "trigger_close_pos_in_bar", "next_open_price", "next_bar_high_price", "next_bar_low_price",
        "next_bar_close_price", "next_bar_return_pct", "next_bar_pullback_frac", "next_close_from_high_frac",
        "next_close_pos_in_bar", "next_extension_above_trigger_high_pct",
    ]
    scoped = frame[[column for column in keep if column in frame.columns]].copy()
    scoped = scoped.rename(
        columns={
            "next_open_price": "next_open",
            "next_bar_high_price": "next_high",
            "next_bar_low_price": "next_low",
            "next_bar_close_price": "next_close",
            "next_bar_return_pct": "next_return_pct",
            "next_bar_pullback_frac": "next_pullback_frac",
        }
    )
    scoped = scoped.sort_values(["symbol", "timestamp_ms"]).drop_duplicates(["symbol", "timestamp_ms"], keep="first")
    scoped["session_id"] = "asia"
    scoped["market_side_hint"] = "long"
    scoped["dataset"] = "old"
    scoped["cache_scope"] = "old"
    return scoped.reset_index(drop=True)


def _load_short_base_events(session_id: str, dataset: str) -> pd.DataFrame:
    if dataset == "current":
        path = CURRENT_ROOT / f"{session_id}_short_session_search" / f"{session_id}_short_geometry_events.csv"
        frame = _harmonize_anomaly_features(_load_csv(path))
        frame = frame.sort_values(["symbol", "timestamp_ms"]).drop_duplicates(["symbol", "timestamp_ms"], keep="first")
    else:
        path = PREV_ROOT / "readiness" / f"{session_id}_old_period_enriched_anomalies.csv"
        frame = _harmonize_anomaly_features(_load_csv(path))
        frame = frame.sort_values(["symbol", "timestamp_ms"]).drop_duplicates(["symbol", "timestamp_ms"], keep="first")
    frame["session_id"] = session_id
    frame["market_side_hint"] = "short"
    frame["dataset"] = dataset
    frame["cache_scope"] = "current" if dataset == "current" else "old"
    return frame.reset_index(drop=True)


def _attach_symbol_age(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    age_source = _load_csv(AGE_SOURCE_PATH)
    merged = frame.merge(age_source[["symbol", "first_seen_ms", "first_seen_utc"]], on="symbol", how="left")
    merged["first_seen_ms"] = pd.to_numeric(merged["first_seen_ms"], errors="coerce")
    merged["age_days_at_event"] = (pd.to_numeric(merged["timestamp_ms"], errors="coerce") - merged["first_seen_ms"]) / 86_400_000.0
    merged["age_bucket"] = "age_unknown"
    merged.loc[merged["age_days_at_event"] <= 30.0, "age_bucket"] = "age_00_30d"
    merged.loc[(merged["age_days_at_event"] > 30.0) & (merged["age_days_at_event"] <= 90.0), "age_bucket"] = "age_31_90d"
    merged.loc[(merged["age_days_at_event"] > 90.0) & (merged["age_days_at_event"] <= 180.0), "age_bucket"] = "age_91_180d"
    merged.loc[(merged["age_days_at_event"] > 180.0) & (merged["age_days_at_event"] <= 365.0), "age_bucket"] = "age_181_365d"
    merged.loc[merged["age_days_at_event"] > 365.0, "age_bucket"] = "age_366d_plus"
    return merged


def _build_anomaly_database() -> pd.DataFrame:
    frames = [
        _load_current_asia_anomalies(),
        _load_old_asia_anomalies(),
        _load_short_base_events("america", "current"),
        _load_short_base_events("america", "old"),
        _load_short_base_events("europe", "current"),
        _load_short_base_events("europe", "old"),
    ]
    frame = pd.concat(frames, ignore_index=True)
    frame = _attach_symbol_age(frame)
    frame["anomaly_key"] = (
        frame["dataset"].astype(str)
        + "|"
        + frame["session_id"].astype(str)
        + "|"
        + frame["symbol"].astype(str)
        + "|"
        + pd.to_numeric(frame["timestamp_ms"], errors="coerce").astype("Int64").astype(str)
    )
    frame["signal_key"] = frame["anomaly_key"]
    return frame.reset_index(drop=True)


def _compute_forward_features(anomalies: pd.DataFrame, *, logger: logging.Logger) -> pd.DataFrame:
    if anomalies.empty:
        return anomalies
    result = anomalies.copy()
    feature_rows: list[dict[str, object]] = []
    preparers = {
        "current": DataPreparer(CURRENT_CACHE_DIR),
        "old": DataPreparer(PREV_CACHE_DIR),
    }
    groups = result.groupby(["cache_scope", "symbol"], sort=True)
    total_groups = groups.ngroups
    for index, ((cache_scope, symbol), scoped) in enumerate(groups, start=1):
        candles = preparers[str(cache_scope)].load_symbol_data(str(symbol), Timeframe.M5)
        if candles.empty:
            continue
        timestamps = pd.to_numeric(candles["timestamp"], errors="coerce").fillna(0).astype("int64").tolist()
        opens = pd.to_numeric(candles["open"], errors="coerce").fillna(0.0).astype("float64").tolist()
        highs = pd.to_numeric(candles["high"], errors="coerce").fillna(0.0).astype("float64").tolist()
        lows = pd.to_numeric(candles["low"], errors="coerce").fillna(0.0).astype("float64").tolist()
        closes = pd.to_numeric(candles["close"], errors="coerce").fillna(0.0).astype("float64").tolist()
        volumes = pd.to_numeric(candles["volume"], errors="coerce").fillna(0.0).astype("float64").tolist()
        timestamp_index = pd.Index(timestamps)
        for _, row in scoped.iterrows():
            event_ts = int(row["timestamp_ms"])
            position = timestamp_index.searchsorted(event_ts, side="left")
            if position >= len(timestamps) or int(timestamps[position]) != event_ts:
                continue
            trigger_open = float(row["trigger_open"])
            trigger_high = float(row["trigger_high"])
            trigger_low = float(row["trigger_low"])
            trigger_close = float(row["trigger_close"])
            trigger_volume = max(1e-12, float(row["trigger_volume"]))
            trigger_body = max(1e-12, abs(trigger_close - trigger_open))

            pre_slice = range(max(0, position - 6), position)
            post_3 = range(position + 1, min(len(timestamps), position + 4))
            post_6 = range(position + 1, min(len(timestamps), position + 7))

            pre_green_count = 0
            pre_red_count = 0
            pre_dir_body_sum_pct = 0.0
            pre_abs_body_sum_pct = 0.0
            pre_max_range_pct = 0.0
            pre_volume_values: list[float] = []
            for item in pre_slice:
                body = closes[item] - opens[item]
                if body > 0:
                    pre_green_count += 1
                elif body < 0:
                    pre_red_count += 1
                pre_dir_body_sum_pct += body / opens[item] if opens[item] > 0 else 0.0
                pre_abs_body_sum_pct += abs(body) / opens[item] if opens[item] > 0 else 0.0
                pre_max_range_pct = max(pre_max_range_pct, (highs[item] - lows[item]) / opens[item] if opens[item] > 0 else 0.0)
                pre_volume_values.append(volumes[item] / trigger_volume)
            pre_avg_volume_ratio = float(sum(pre_volume_values) / len(pre_volume_values)) if pre_volume_values else 0.0

            def _post_features(indices: range) -> dict[str, object]:
                green_count = 0
                red_count = 0
                max_green_body_ratio = 0.0
                max_red_body_ratio = 0.0
                max_green_volume_ratio = 0.0
                max_red_volume_ratio = 0.0
                max_up_extension_pct = 0.0
                max_down_extension_pct = 0.0
                first_green_reaccel_bar = None
                first_red_reaccel_bar = None
                for bar_offset, item in enumerate(indices, start=1):
                    body = closes[item] - opens[item]
                    volume_ratio = volumes[item] / trigger_volume if trigger_volume > 0 else 0.0
                    if body > 0:
                        green_count += 1
                        green_body_ratio = abs(body) / trigger_body
                        max_green_body_ratio = max(max_green_body_ratio, green_body_ratio)
                        max_green_volume_ratio = max(max_green_volume_ratio, volume_ratio)
                        if (
                            first_green_reaccel_bar is None
                            and green_body_ratio >= 0.60
                            and volume_ratio >= 0.60
                            and highs[item] > trigger_high
                        ):
                            first_green_reaccel_bar = bar_offset
                    elif body < 0:
                        red_count += 1
                        red_body_ratio = abs(body) / trigger_body
                        max_red_body_ratio = max(max_red_body_ratio, red_body_ratio)
                        max_red_volume_ratio = max(max_red_volume_ratio, volume_ratio)
                        if (
                            first_red_reaccel_bar is None
                            and red_body_ratio >= 0.60
                            and volume_ratio >= 0.60
                            and lows[item] < trigger_low
                        ):
                            first_red_reaccel_bar = bar_offset
                    max_up_extension_pct = max(max_up_extension_pct, (highs[item] - trigger_high) / trigger_high if trigger_high > 0 else 0.0)
                    max_down_extension_pct = max(max_down_extension_pct, (trigger_low - lows[item]) / trigger_low if trigger_low > 0 else 0.0)
                return {
                    "green_count": green_count,
                    "red_count": red_count,
                    "max_green_body_ratio": max_green_body_ratio,
                    "max_red_body_ratio": max_red_body_ratio,
                    "max_green_volume_ratio": max_green_volume_ratio,
                    "max_red_volume_ratio": max_red_volume_ratio,
                    "max_up_extension_pct": max_up_extension_pct,
                    "max_down_extension_pct": max_down_extension_pct,
                    "buyer_wave_match_score": min(max_green_body_ratio, max_green_volume_ratio),
                    "seller_wave_match_score": min(max_red_body_ratio, max_red_volume_ratio),
                    "first_green_reaccel_bar": first_green_reaccel_bar,
                    "first_red_reaccel_bar": first_red_reaccel_bar,
                }

            post3 = _post_features(post_3)
            post6 = _post_features(post_6)
            feature_rows.append(
                {
                    "anomaly_key": row["anomaly_key"],
                    "pre_green_count_30m": pre_green_count,
                    "pre_red_count_30m": pre_red_count,
                    "pre_dir_body_sum_pct_30m": pre_dir_body_sum_pct,
                    "pre_abs_body_sum_pct_30m": pre_abs_body_sum_pct,
                    "pre_max_range_pct_30m": pre_max_range_pct,
                    "pre_avg_volume_ratio_30m": pre_avg_volume_ratio,
                    "post_green_count_15m": post3["green_count"],
                    "post_red_count_15m": post3["red_count"],
                    "post_max_green_body_ratio_15m": post3["max_green_body_ratio"],
                    "post_max_red_body_ratio_15m": post3["max_red_body_ratio"],
                    "post_max_green_volume_ratio_15m": post3["max_green_volume_ratio"],
                    "post_max_red_volume_ratio_15m": post3["max_red_volume_ratio"],
                    "post_max_up_extension_pct_15m": post3["max_up_extension_pct"],
                    "post_max_down_extension_pct_15m": post3["max_down_extension_pct"],
                    "buyer_wave_match_score_15m": post3["buyer_wave_match_score"],
                    "seller_wave_match_score_15m": post3["seller_wave_match_score"],
                    "first_green_reaccel_bar_15m": post3["first_green_reaccel_bar"],
                    "first_red_reaccel_bar_15m": post3["first_red_reaccel_bar"],
                    "post_green_count_30m": post6["green_count"],
                    "post_red_count_30m": post6["red_count"],
                    "post_max_green_body_ratio_30m": post6["max_green_body_ratio"],
                    "post_max_red_body_ratio_30m": post6["max_red_body_ratio"],
                    "post_max_green_volume_ratio_30m": post6["max_green_volume_ratio"],
                    "post_max_red_volume_ratio_30m": post6["max_red_volume_ratio"],
                    "post_max_up_extension_pct_30m": post6["max_up_extension_pct"],
                    "post_max_down_extension_pct_30m": post6["max_down_extension_pct"],
                    "buyer_wave_match_score_30m": post6["buyer_wave_match_score"],
                    "seller_wave_match_score_30m": post6["seller_wave_match_score"],
                    "first_green_reaccel_bar_30m": post6["first_green_reaccel_bar"],
                    "first_red_reaccel_bar_30m": post6["first_red_reaccel_bar"],
                }
            )
        if index == 1 or index == total_groups or index % 50 == 0:
            logger.info("anomaly-category-lab: stage=feature-enrich groups=%s/%s", index, total_groups)
    features = pd.DataFrame(feature_rows)
    merged = result.merge(features, on="anomaly_key", how="left")
    for column in features.columns:
        if column == "anomaly_key":
            continue
        merged[column] = pd.to_numeric(merged[column], errors="coerce")
    return merged


def _bucket_three_way(series: pd.Series, low_label: str, mid_label: str, high_label: str) -> pd.Series:
    clean = pd.to_numeric(series, errors="coerce")
    q1 = float(clean.quantile(1.0 / 3.0))
    q2 = float(clean.quantile(2.0 / 3.0))
    bucket = pd.Series(index=series.index, dtype="object")
    bucket.loc[clean <= q1] = low_label
    bucket.loc[(clean > q1) & (clean <= q2)] = mid_label
    bucket.loc[clean > q2] = high_label
    return bucket.fillna(mid_label)


def _assign_categories(frame: pd.DataFrame) -> pd.DataFrame:
    enriched = frame.copy()
    enriched["context_drift_bucket"] = _bucket_three_way(enriched["pre_base_drift_pct_60m"], "drift_cold", "drift_warm", "drift_hot")
    enriched["context_range_bucket"] = _bucket_three_way(enriched["pre_base_range_pct_60m"], "range_tight", "range_mid", "range_wide")
    enriched["anomaly_bucket"] = _bucket_three_way(enriched["trigger_return_pct"], "anomaly_small", "anomaly_mid", "anomaly_hot")
    enriched["volume_bucket"] = _bucket_three_way(enriched["volume_mult"], "volume_calm", "volume_mid", "volume_hot")
    enriched["context_archetype"] = "context_warm"
    enriched.loc[
        (enriched["context_drift_bucket"] == "drift_cold") & (enriched["context_range_bucket"] == "range_tight"),
        "context_archetype",
    ] = "context_coiled"
    enriched.loc[
        (enriched["context_drift_bucket"] == "drift_hot") | (enriched["context_range_bucket"] == "range_wide"),
        "context_archetype",
    ] = "context_overheated"
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
    enriched["confirmation_archetype"] = "confirm_holding"
    enriched.loc[
        (pd.to_numeric(enriched["next_pullback_frac"], errors="coerce") <= 0.20)
        & (pd.to_numeric(enriched["next_close_pos_in_bar"], errors="coerce") >= 0.65)
        & (pd.to_numeric(enriched["next_extension_above_trigger_high_pct"], errors="coerce") >= 0.005),
        "confirmation_archetype",
    ] = "confirm_clean"
    enriched.loc[
        (pd.to_numeric(enriched["next_pullback_frac"], errors="coerce") >= 0.45)
        | (pd.to_numeric(enriched["next_close_pos_in_bar"], errors="coerce") <= 0.40),
        "confirmation_archetype",
    ] = "confirm_failed"
    enriched["wave_archetype"] = "wave_none"
    enriched.loc[
        (pd.to_numeric(enriched["buyer_wave_match_score_30m"], errors="coerce") >= 0.80)
        & (pd.to_numeric(enriched["seller_wave_match_score_30m"], errors="coerce") < 0.80),
        "wave_archetype",
    ] = "wave_buyer_match"
    enriched.loc[
        (pd.to_numeric(enriched["seller_wave_match_score_30m"], errors="coerce") >= 0.80)
        & (pd.to_numeric(enriched["buyer_wave_match_score_30m"], errors="coerce") < 0.80),
        "wave_archetype",
    ] = "wave_seller_match"
    enriched.loc[
        (pd.to_numeric(enriched["seller_wave_match_score_30m"], errors="coerce") >= 0.80)
        & (pd.to_numeric(enriched["buyer_wave_match_score_30m"], errors="coerce") >= 0.80),
        "wave_archetype",
    ] = "wave_contested"
    category = pd.Series("mixed_transition", index=enriched.index, dtype="object")
    category.loc[(enriched["context_archetype"] == "context_coiled") & (enriched["confirmation_archetype"] == "confirm_clean") & (enriched["wave_archetype"] == "wave_buyer_match")] = "ignition_reaccel"
    category.loc[(enriched["context_archetype"] == "context_coiled") & (enriched["confirmation_archetype"].isin(["confirm_clean", "confirm_holding"])) & (category == "mixed_transition")] = "clean_ignition"
    category.loc[(enriched["context_archetype"] == "context_warm") & (enriched["confirmation_archetype"].isin(["confirm_clean", "confirm_holding"])) & (enriched["wave_archetype"] == "wave_buyer_match")] = "warm_continuation"
    category.loc[(enriched["confirmation_archetype"] == "confirm_failed") & (enriched["wave_archetype"] == "wave_seller_match") & (enriched["context_archetype"] == "context_overheated")] = "blowoff_reversal"
    category.loc[(enriched["confirmation_archetype"] == "confirm_failed") & (enriched["wave_archetype"] == "wave_seller_match") & (category == "mixed_transition")] = "failed_continuation"
    category.loc[(enriched["impulse_archetype"] == "impulse_wicky_spike") & (enriched["context_archetype"] == "context_overheated") & (category == "mixed_transition")] = "wicky_exhaustion"
    category.loc[(enriched["confirmation_archetype"] == "confirm_holding") & (enriched["wave_archetype"] == "wave_contested") & (category == "mixed_transition")] = "contested_expansion"
    category.loc[(enriched["confirmation_archetype"] == "confirm_failed") & (enriched["context_archetype"] == "context_coiled") & (category == "mixed_transition")] = "false_start"
    enriched["canonical_category"] = category
    return enriched


def _build_category_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for category_id, scoped in frame.groupby("canonical_category", sort=True):
        current = scoped[scoped["dataset"] == "current"].copy()
        old = scoped[scoped["dataset"] == "old"].copy()
        current_summary = _summarize_events(current, calendar_months=_calendar_months_from_frame(current)) or {}
        old_summary = _summarize_events(old, calendar_months=_calendar_months_from_frame(old)) or {}
        rows.append(
            {
                "category_id": category_id,
                "events_current": int(len(current)),
                "events_old": int(len(old)),
                "sessions": ",".join(sorted(scoped["session_id"].astype(str).unique())),
                "dominant_session": scoped["session_id"].astype(str).value_counts().idxmax(),
                "dominant_side_hint": scoped["market_side_hint"].astype(str).value_counts().idxmax(),
                "current_mean_follow_pct": current_summary.get("mean_return_pct"),
                "old_mean_follow_pct": old_summary.get("mean_return_pct"),
                "current_positive_months": current_summary.get("positive_months_count"),
                "old_positive_months": old_summary.get("positive_months_count"),
                "buyer_wave_match_mean": float(pd.to_numeric(scoped["buyer_wave_match_score_30m"], errors="coerce").mean()),
                "seller_wave_match_mean": float(pd.to_numeric(scoped["seller_wave_match_score_30m"], errors="coerce").mean()),
            }
        )
    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary
    return summary.sort_values(["events_current", "events_old"], ascending=[False, False]).reset_index(drop=True)


def _normalize_long_scenarios() -> tuple[pd.DataFrame, pd.DataFrame]:
    current = _harmonize_anomaly_features(_load_csv(CURRENT_ROOT / "source_inputs" / "trade_model_events.csv"))
    old = _harmonize_anomaly_features(_load_csv(PREV_ROOT / "research" / "hourly_asia_pump_prev_year_5m" / "trade_model_events.csv"))
    frames = []
    for dataset, frame in (("current", current), ("old", old)):
        scoped = frame[frame["trade_triggered"].fillna(False).astype(bool)].copy()
        if scoped.empty:
            continue
        scoped["session_id"] = "asia"
        scoped["side"] = "long"
        scoped["dataset"] = dataset
        scoped["scenario_key"] = "asia_long::" + scoped["trade_model_id"].astype(str)
        scoped["scenario_title"] = scoped["trade_model_label"].astype(str)
        scoped["scenario_group"] = "asia_long_trade_model"
        scoped["signal_key"] = (
            scoped["dataset"].astype(str)
            + "|asia|"
            + scoped["symbol"].astype(str)
            + "|"
            + pd.to_numeric(scoped["timestamp_ms"], errors="coerce").astype("Int64").astype(str)
        )
        keep = [
            "scenario_key", "scenario_title", "scenario_group", "session_id", "side", "dataset", "symbol", "month_utc",
            "timestamp_ms", "signal_key", "entry_timestamp_ms", "exit_timestamp_ms", "exit_return_pct",
            "initial_risk_pct", "exit_reason", "trade_model_id", "trade_model_label", "human_description",
            "initial_stop_reason", "initial_stop_price", "entry_reason",
        ]
        frames.append(scoped[[column for column in keep if column in scoped.columns]].copy())
    events = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if events.empty:
        return events, pd.DataFrame()
    meta_source = events.sort_values(["scenario_key", "dataset"]).drop_duplicates(["scenario_key", "dataset"])
    agg_spec: dict[str, tuple[str, str]] = {
        "scenario_title": ("scenario_title", "first"),
        "session_id": ("session_id", "first"),
        "side": ("side", "first"),
        "scenario_group": ("scenario_group", "first"),
        "trade_model_label": ("trade_model_label", "first"),
    }
    if "human_description" in meta_source.columns:
        agg_spec["human_description"] = ("human_description", "first")
    if "initial_stop_reason" in meta_source.columns:
        agg_spec["initial_stop_reason"] = ("initial_stop_reason", "first")
    meta = meta_source.groupby("scenario_key", as_index=False).agg(**agg_spec)
    return events, meta


def _build_short_model_events(*, session_id: str, dataset: str, logger: logging.Logger) -> tuple[pd.DataFrame, pd.DataFrame]:
    models = _build_short_execution_models()
    if dataset == "current":
        geometry_events = _harmonize_anomaly_features(_load_csv(CURRENT_ROOT / f"{session_id}_short_session_search" / f"{session_id}_short_geometry_events.csv"))
    else:
        selected_events = _prepare_short_anomaly_events(PREV_ROOT / "readiness" / f"{session_id}_old_period_enriched_anomalies.csv")
        geometry_events = _build_short_geometry_events(
            preparer=DataPreparer(PREV_CACHE_DIR),
            selected_events=selected_events,
            geometries=_build_short_geometries(),
            commission_rate=0.0004,
            logger=logger,
        )
        geometry_events["timestamp_utc"] = pd.to_datetime(pd.to_numeric(geometry_events["timestamp_ms"], errors="coerce"), unit="ms", utc=True, errors="coerce")
        geometry_events["month_utc"] = geometry_events["timestamp_utc"].dt.strftime("%Y-%m")
    calendar_months = _calendar_months_from_frame(geometry_events)
    summary, model_events = _summarize_short_atomic_models(
        geometry_events=geometry_events,
        models=models,
        calendar_months=calendar_months,
        logger=logger,
    )
    frames: list[pd.DataFrame] = []
    for model_id, scoped in model_events.items():
        if scoped.empty:
            continue
        scoped = scoped.copy()
        scoped["session_id"] = session_id
        scoped["side"] = "short"
        scoped["dataset"] = dataset
        scoped["scenario_key"] = f"{session_id}_short::{model_id}"
        scoped["scenario_title"] = str(summary.loc[summary["model_id"].astype(str) == str(model_id), "model_label"].iloc[0])
        scoped["scenario_group"] = f"{session_id}_short_model"
        scoped["signal_key"] = (
            scoped["dataset"].astype(str)
            + "|"
            + scoped["session_id"].astype(str)
            + "|"
            + scoped["symbol"].astype(str)
            + "|"
            + pd.to_numeric(scoped["timestamp_ms"], errors="coerce").astype("Int64").astype(str)
        )
        keep = [
            "scenario_key", "scenario_title", "scenario_group", "session_id", "side", "dataset", "symbol", "month_utc",
            "timestamp_ms", "signal_key", "entry_timestamp_ms", "exit_timestamp_ms", "exit_return_pct",
            "initial_risk_pct", "exit_reason", "geometry_id", "target_rr", "initial_stop_price", "entry_style",
            "model_id",
        ]
        frames.append(scoped[[column for column in keep if column in scoped.columns]].copy())
    events = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    meta = summary.copy()
    if not meta.empty:
        meta["scenario_key"] = f"{session_id}_short::" + meta["model_id"].astype(str)
        meta["scenario_title"] = meta["model_label"].astype(str)
        meta["session_id"] = session_id
        meta["side"] = "short"
        meta["scenario_group"] = f"{session_id}_short_model"
    return events, meta


def _build_scenario_universe(logger: logging.Logger) -> tuple[pd.DataFrame, pd.DataFrame]:
    cache_events_path = OUTPUT_DIR / "scenario_events_cache.csv"
    cache_meta_path = OUTPUT_DIR / "scenario_meta_cache.csv"
    if cache_events_path.exists() and cache_meta_path.exists():
        logger.info("anomaly-category-lab: stage=scenarios-cache-load")
        return _load_csv(cache_events_path), _load_csv(cache_meta_path)

    long_events, long_meta = _normalize_long_scenarios()
    america_current_events, america_current_meta = _build_short_model_events(session_id="america", dataset="current", logger=logger)
    america_old_events, america_old_meta = _build_short_model_events(session_id="america", dataset="old", logger=logger)
    europe_current_events, europe_current_meta = _build_short_model_events(session_id="europe", dataset="current", logger=logger)
    europe_old_events, europe_old_meta = _build_short_model_events(session_id="europe", dataset="old", logger=logger)
    scenario_events = pd.concat(
        [long_events, america_current_events, america_old_events, europe_current_events, europe_old_events],
        ignore_index=True,
    )
    meta_frames = [long_meta]
    for meta in (america_current_meta, america_old_meta, europe_current_meta, europe_old_meta):
        if meta.empty:
            continue
        available = [column for column in ["scenario_key", "scenario_title", "session_id", "side", "scenario_group", "rule_text", "geometry_id", "signal_profile_id", "next_pressure_id", "context_id"] if column in meta.columns]
        meta_frames.append(meta[available].copy())
    scenario_meta = pd.concat(meta_frames, ignore_index=True).sort_values("scenario_key").drop_duplicates("scenario_key", keep="first").reset_index(drop=True)
    scenario_events.to_csv(cache_events_path, index=False)
    scenario_meta.to_csv(cache_meta_path, index=False)
    return scenario_events, scenario_meta


def _summarize_category_scenarios(anomalies: pd.DataFrame, scenario_events: pd.DataFrame, scenario_meta: pd.DataFrame) -> pd.DataFrame:
    joined = scenario_events.merge(
        anomalies[["anomaly_key", "signal_key", "canonical_category", "dataset", "session_id"]],
        on=["signal_key", "dataset", "session_id"],
        how="inner",
    )
    rows: list[dict[str, object]] = []
    for (category_id, scenario_key), scoped in joined.groupby(["canonical_category", "scenario_key"], sort=True):
        current = scoped[scoped["dataset"] == "current"].copy()
        old = scoped[scoped["dataset"] == "old"].copy()
        if len(current) < 4 or len(old) < 3:
            continue
        current_summary = _summarize_events(current, calendar_months=_calendar_months_from_frame(current)) or {}
        old_summary = _summarize_events(old, calendar_months=_calendar_months_from_frame(old)) or {}
        current_mean = _safe_float(current_summary.get("mean_return_pct"))
        old_mean = _safe_float(old_summary.get("mean_return_pct"))
        current_wr = _safe_float(current_summary.get("win_rate"))
        old_wr = _safe_float(old_summary.get("win_rate"))
        if current_mean is None or old_mean is None or current_wr is None or old_wr is None:
            continue
        stable_positive = current_mean > 0.0 and old_mean > 0.0 and current_wr >= 0.50 and old_wr >= 0.50
        stability_score = (
            min(current_mean, old_mean) * 100.0
            + min(current_wr, old_wr) * 25.0
            + min(len(current), len(old)) * 0.35
            - abs(current_mean - old_mean) * 35.0
            - abs(current_wr - old_wr) * 10.0
        )
        meta_row = scenario_meta[scenario_meta["scenario_key"].astype(str) == str(scenario_key)]
        rows.append(
            {
                "category_id": category_id,
                "scenario_key": scenario_key,
                "scenario_title": meta_row.iloc[0]["scenario_title"] if not meta_row.empty else scenario_key,
                "session_id": meta_row.iloc[0]["session_id"] if not meta_row.empty else scoped["session_id"].iloc[0],
                "side": meta_row.iloc[0]["side"] if not meta_row.empty else scoped["side"].iloc[0],
                "trades_current": int(len(current)),
                "trades_old": int(len(old)),
                "current_mean_return_pct": current_mean,
                "old_mean_return_pct": old_mean,
                "current_win_rate": current_wr,
                "old_win_rate": old_wr,
                "current_annualized_unit_pnl_pct": _safe_float(current_summary.get("annualized_unit_pnl_pct")),
                "old_annualized_unit_pnl_pct": _safe_float(old_summary.get("annualized_unit_pnl_pct")),
                "current_positive_months_count": _safe_float(current_summary.get("positive_months_count")),
                "old_positive_months_count": _safe_float(old_summary.get("positive_months_count")),
                "stable_positive": stable_positive,
                "stability_score": stability_score,
            }
        )
    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary
    return summary.sort_values(["stable_positive", "stability_score"], ascending=[False, False]).reset_index(drop=True)


def _select_top_categories(category_summary: pd.DataFrame, category_scenarios: pd.DataFrame, limit: int = 6) -> pd.DataFrame:
    if category_summary.empty or category_scenarios.empty:
        return pd.DataFrame()
    best = category_scenarios.sort_values(["category_id", "stable_positive", "stability_score"], ascending=[True, False, False]).drop_duplicates("category_id", keep="first")
    merged = category_summary.merge(best, on="category_id", how="inner")
    merged = merged[(merged["events_current"] >= 20) & (merged["events_old"] >= 12) & merged["stable_positive"].fillna(False)].copy()
    if merged.empty:
        return merged
    merged["category_rank_score"] = merged["stability_score"].fillna(0.0) + merged["events_old"].fillna(0.0) * 0.15 + merged["events_current"].fillna(0.0) * 0.10
    return merged.sort_values("category_rank_score", ascending=False).head(limit).reset_index(drop=True)


def _build_portfolio_from_categories(selected: pd.DataFrame, anomalies: pd.DataFrame, scenario_events: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if selected.empty:
        return pd.DataFrame(), pd.DataFrame()
    category_map = selected[["category_id", "scenario_key"]].copy()
    category_map["category_id"] = category_map["category_id"].astype(str)
    category_map["scenario_key"] = category_map["scenario_key"].astype(str)
    joined = scenario_events.merge(
        anomalies[["signal_key", "dataset", "session_id", "canonical_category"]],
        on=["signal_key", "dataset", "session_id"],
        how="inner",
    )
    joined["canonical_category"] = joined["canonical_category"].astype(str)
    joined["scenario_key"] = joined["scenario_key"].astype(str)
    joined = joined.merge(category_map, left_on=["canonical_category", "scenario_key"], right_on=["category_id", "scenario_key"], how="inner")
    if joined.empty:
        return pd.DataFrame(), pd.DataFrame()
    selected_events = (
        joined.sort_values(["dataset", "session_id", "symbol", "timestamp_ms", "scenario_key"])
        .drop_duplicates(["dataset", "session_id", "symbol", "timestamp_ms"], keep="first")
        .reset_index(drop=True)
    )
    monthly = (
        selected_events.groupby("month_utc", as_index=False)
        .agg(
            trades_count=("exit_return_pct", "size"),
            month_return_pct=("exit_return_pct", "sum"),
            win_rate=("exit_return_pct", lambda values: float((pd.to_numeric(values, errors="coerce") > 0.0).mean()) if len(values) else 0.0),
            mean_return_pct=("exit_return_pct", "mean"),
        )
        .sort_values("month_utc")
        .reset_index(drop=True)
    )
    return selected_events, monthly


def _build_wave_match_summary(anomalies: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for feature in ("buyer_wave_match_score_30m", "seller_wave_match_score_30m"):
        clean = pd.to_numeric(anomalies[feature], errors="coerce")
        q1 = float(clean.quantile(1.0 / 3.0))
        q2 = float(clean.quantile(2.0 / 3.0))
        for label, mask in (
            ("low", clean <= q1),
            ("mid", (clean > q1) & (clean <= q2)),
            ("high", clean > q2),
        ):
            scoped = anomalies[mask.fillna(False)].copy()
            if scoped.empty:
                continue
            rows.append(
                {
                    "feature_name": feature,
                    "bucket": label,
                    "events_count": int(len(scoped)),
                    "asia_share": float((scoped["session_id"].astype(str) == "asia").mean()),
                    "america_share": float((scoped["session_id"].astype(str) == "america").mean()),
                    "europe_share": float((scoped["session_id"].astype(str) == "europe").mean()),
                    "coiled_share": float((scoped["context_archetype"].astype(str) == "context_coiled").mean()),
                    "clean_share": float((scoped["confirmation_archetype"].astype(str) == "confirm_clean").mean()),
                    "failed_share": float((scoped["confirmation_archetype"].astype(str) == "confirm_failed").mean()),
                }
            )
    return pd.DataFrame(rows)


def _feature_dictionary() -> pd.DataFrame:
    rows = [
        ("anomaly_key", "Уникальный ключ аномалии", "dataset|session|symbol|timestamp_ms"),
        ("session_id", "Сессия рынка", "asia / europe / america"),
        ("market_side_hint", "Естественное направление исследования", "long / short"),
        ("pre_base_range_pct_60m", "Ширина базы до аномалии за 60 минут", "Доля цены"),
        ("pre_base_drift_pct_60m", "Направленный дрейф до аномалии", "Доля цены"),
        ("trigger_return_pct", "Размер аномальной свечи", "Доля цены"),
        ("range_atr", "Размер аномалии в ATR", "Безразмерно"),
        ("volume_mult", "Всплеск объёма", "Множитель"),
        ("close_to_high_frac", "Насколько close далёк от high", "Доля диапазона"),
        ("upper_wick_frac", "Верхняя тень", "Доля диапазона"),
        ("body_frac", "Тело свечи", "Доля диапазона"),
        ("next_pullback_frac", "Откат следующей свечи", "Доля диапазона аномалии"),
        ("next_close_pos_in_bar", "Позиция close следующей свечи", "0=низ, 1=верх"),
        ("buyer_wave_match_score_30m", "Насколько быстро появилась повторная волна покупателя, сравнимая с первой", "min(max_green_body_ratio, max_green_volume_ratio)"),
        ("seller_wave_match_score_30m", "Насколько быстро появилась повторная волна продавца, сравнимая с первой", "min(max_red_body_ratio, max_red_volume_ratio)"),
        ("context_archetype", "Тип контекста до пампа", "coiled / warm / overheated"),
        ("impulse_archetype", "Форма аномальной свечи", "body_drive / balanced / wicky_spike"),
        ("confirmation_archetype", "Качество следующей свечи", "clean / holding / failed"),
        ("wave_archetype", "Тип продолжения после аномалии", "buyer_match / seller_match / contested / none"),
        ("canonical_category", "Широкая природная категория пампа", "Итоговая неузкая категория"),
    ]
    return pd.DataFrame(rows, columns=["column_name", "description_ru", "meaning"])


def _summary_frame(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    calendar_months = _calendar_months_from_frame(events)
    base = _summarize_events(events, calendar_months=calendar_months) or {}
    eq5, _ = _simulate_equity_risk_metrics(events, calendar_months=calendar_months, risk_fraction=0.05)
    eq9, _ = _simulate_equity_risk_metrics(events, calendar_months=calendar_months, risk_fraction=0.09)
    row = {**base, **eq5}
    row["equity_9_annualized_return_pct"] = eq9.get("equity_annualized_return_pct")
    row["equity_9_max_drawdown_pct"] = eq9.get("equity_max_drawdown_pct")
    return pd.DataFrame([row])


def _build_report(
    *,
    anomaly_db: pd.DataFrame,
    feature_dictionary: pd.DataFrame,
    category_summary: pd.DataFrame,
    category_scenarios: pd.DataFrame,
    selected_categories: pd.DataFrame,
    portfolio_summary_current: pd.DataFrame,
    portfolio_summary_old: pd.DataFrame,
    portfolio_summary_combined: pd.DataFrame,
    portfolio_monthly_combined: pd.DataFrame,
    wave_match_summary: pd.DataFrame,
    report_path: Path,
) -> None:
    lines = [
        "# Лаборатория природных категорий пампов",
        "",
        "Этот прогон строит широкую БД аномалий по старому и новому периоду, добавляет follow-through признаки после аномалии и ищет устойчивые сценарии торговли не по часу, а по природе самого пампа.",
        "",
        "## Что вошло в БД",
        "",
        _frame_to_markdown(
            pd.DataFrame(
                [{
                    "всего_аномалий": int(len(anomaly_db)),
                    "текущий_период": int((anomaly_db['dataset'].astype(str) == 'current').sum()),
                    "старый_период": int((anomaly_db['dataset'].astype(str) == 'old').sum()),
                    "азия": int((anomaly_db['session_id'].astype(str) == 'asia').sum()),
                    "европа": int((anomaly_db['session_id'].astype(str) == 'europe').sum()),
                    "америка": int((anomaly_db['session_id'].astype(str) == 'america').sum()),
                }]
            ),
            columns=["всего_аномалий", "текущий_период", "старый_период", "азия", "европа", "америка"],
        ),
        "",
        "## Словарь ключевых признаков",
        "",
        _frame_to_markdown(feature_dictionary, columns=list(feature_dictionary.columns), limit=24),
        "",
        "## Широкие природные категории",
        "",
        _frame_to_markdown(category_summary, columns=["category_id", "events_current", "events_old", "dominant_session", "dominant_side_hint", "buyer_wave_match_mean", "seller_wave_match_mean"], limit=16),
        "",
        "## Лучшие устойчивые сценарии по категориям",
        "",
        _frame_to_markdown(category_scenarios.head(20), columns=["category_id", "scenario_key", "session_id", "side", "trades_current", "trades_old", "current_mean_return_pct", "old_mean_return_pct", "current_win_rate", "old_win_rate", "stable_positive", "stability_score"]),
        "",
        "## Отобранные категории для портфеля",
        "",
        _frame_to_markdown(selected_categories, columns=["category_id", "dominant_session", "dominant_side_hint", "events_current", "events_old", "scenario_key", "scenario_title", "trades_current", "trades_old", "current_mean_return_pct", "old_mean_return_pct", "current_win_rate", "old_win_rate", "stability_score"]),
        "",
        "## Портфель выбранных категорий: новый период",
        "",
        _frame_to_markdown(portfolio_summary_current, columns=list(portfolio_summary_current.columns), limit=1) if not portfolio_summary_current.empty else "_Нет данных_",
        "",
        "## Портфель выбранных категорий: старый период",
        "",
        _frame_to_markdown(portfolio_summary_old, columns=list(portfolio_summary_old.columns), limit=1) if not portfolio_summary_old.empty else "_Нет данных_",
        "",
        "## Портфель выбранных категорий: оба периода вместе",
        "",
        _frame_to_markdown(portfolio_summary_combined, columns=list(portfolio_summary_combined.columns), limit=1) if not portfolio_summary_combined.empty else "_Нет данных_",
        "",
        "## Месяцы объединённого портфеля",
        "",
        _frame_to_markdown(portfolio_monthly_combined, columns=["month_utc", "trades_count", "month_return_pct", "win_rate", "mean_return_pct"]) if not portfolio_monthly_combined.empty else "_Нет данных_",
        "",
        "## Что говорит идея про повторную волну покупателя/продавца",
        "",
        _frame_to_markdown(wave_match_summary, columns=["feature_name", "bucket", "events_count", "asia_share", "america_share", "europe_share", "coiled_share", "clean_share", "failed_share"]),
        "",
        "## Короткий вывод",
        "",
        "Если природная категоризация полезна, то одна и та же широкая категория пампа должна получать один и тот же рабочий сценарий и на старом, и на новом периоде. Этот отчёт показывает именно такие совпадения и сразу отсеивает красивые, но режимные решения.",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")


def run_anomaly_category_lab(*, logger: logging.Logger | None = None) -> dict[str, Path]:
    active_logger = logger or module_logger
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    active_logger.info("anomaly-category-lab: stage=load")
    anomaly_db = _assign_categories(_compute_forward_features(_build_anomaly_database(), logger=active_logger))
    feature_dictionary = _feature_dictionary()
    category_summary = _build_category_summary(anomaly_db)
    active_logger.info("anomaly-category-lab: stage=scenarios")
    scenario_events, scenario_meta = _build_scenario_universe(active_logger)
    category_scenarios = _summarize_category_scenarios(anomaly_db, scenario_events, scenario_meta)
    selected_categories = _select_top_categories(category_summary, category_scenarios, limit=6)
    portfolio_events, portfolio_monthly = _build_portfolio_from_categories(selected_categories, anomaly_db, scenario_events)
    portfolio_summary_current = _summary_frame(portfolio_events[portfolio_events["dataset"].astype(str) == "current"].copy())
    portfolio_summary_old = _summary_frame(portfolio_events[portfolio_events["dataset"].astype(str) == "old"].copy())
    portfolio_summary_combined = _summary_frame(portfolio_events)
    wave_match_summary = _build_wave_match_summary(anomaly_db)
    artifacts = {
        "anomaly_db_parquet": OUTPUT_DIR / "anomaly_feature_database.parquet",
        "anomaly_db_csv": OUTPUT_DIR / "anomaly_feature_database.csv",
        "feature_dictionary": OUTPUT_DIR / "anomaly_feature_dictionary.csv",
        "category_summary": OUTPUT_DIR / "category_summary.csv",
        "category_scenarios": OUTPUT_DIR / "category_scenario_summary.csv",
        "selected_categories": OUTPUT_DIR / "selected_categories.csv",
        "portfolio_events": OUTPUT_DIR / "selected_category_portfolio_events.csv",
        "portfolio_monthly": OUTPUT_DIR / "selected_category_portfolio_monthly.csv",
        "portfolio_summary_current": OUTPUT_DIR / "selected_category_portfolio_summary_current.csv",
        "portfolio_summary_old": OUTPUT_DIR / "selected_category_portfolio_summary_old.csv",
        "portfolio_summary_combined": OUTPUT_DIR / "selected_category_portfolio_summary_combined.csv",
        "wave_match_summary": OUTPUT_DIR / "wave_match_summary.csv",
        "scenario_catalog": OUTPUT_DIR / "scenario_catalog.csv",
        "report": OUTPUT_DIR / "anomaly_category_lab_report.md",
    }
    anomaly_db.to_parquet(artifacts["anomaly_db_parquet"], index=False)
    anomaly_db.to_csv(artifacts["anomaly_db_csv"], index=False)
    feature_dictionary.to_csv(artifacts["feature_dictionary"], index=False)
    category_summary.to_csv(artifacts["category_summary"], index=False)
    category_scenarios.to_csv(artifacts["category_scenarios"], index=False)
    selected_categories.to_csv(artifacts["selected_categories"], index=False)
    portfolio_events.to_csv(artifacts["portfolio_events"], index=False)
    portfolio_monthly.to_csv(artifacts["portfolio_monthly"], index=False)
    portfolio_summary_current.to_csv(artifacts["portfolio_summary_current"], index=False)
    portfolio_summary_old.to_csv(artifacts["portfolio_summary_old"], index=False)
    portfolio_summary_combined.to_csv(artifacts["portfolio_summary_combined"], index=False)
    wave_match_summary.to_csv(artifacts["wave_match_summary"], index=False)
    scenario_meta.to_csv(artifacts["scenario_catalog"], index=False)
    _build_report(
        anomaly_db=anomaly_db,
        feature_dictionary=feature_dictionary,
        category_summary=category_summary,
        category_scenarios=category_scenarios,
        selected_categories=selected_categories,
        portfolio_summary_current=portfolio_summary_current,
        portfolio_summary_old=portfolio_summary_old,
        portfolio_summary_combined=portfolio_summary_combined,
        portfolio_monthly_combined=portfolio_monthly,
        wave_match_summary=wave_match_summary,
        report_path=artifacts["report"],
    )
    active_logger.info("anomaly-category-lab: stage=done anomalies=%s categories=%s selected=%s", len(anomaly_db), len(category_summary), len(selected_categories))
    return artifacts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run_anomaly_category_lab()
    print(json.dumps({key: str(value) for key, value in result.items()}, ensure_ascii=False, indent=2))
