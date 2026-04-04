from __future__ import annotations

from pathlib import Path
from statistics import median

import pandas as pd

from strategy.hourly_asia_pump.static_combo import _frame_to_markdown
from strategy.hourly_asia_pump.tradeable_second_wave_models import _close_pos, _load_feature_db, _load_m1

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "xx00_pump_nature_research"

STRONG_CONTINUATION_THRESHOLD = 0.025
DEAD_PUMP_THRESHOLD = 0.01

BASE_DB_NUMERIC_FEATURES = [
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
    "body_return_pct",
    "trigger_close_pos_in_bar",
    "pre_green_count_30m",
    "pre_red_count_30m",
    "pre_dir_body_sum_pct_30m",
    "pre_abs_body_sum_pct_30m",
    "pre_max_range_pct_30m",
    "pre_avg_volume_ratio_30m",
    "pre_volume_slope_norm",
    "pre_volume_up_ratio",
    "pre_price_return",
    "pre_price_range",
]

CATEGORICAL_FEATURES = [
    "context_archetype",
    "pre_accumulation_type",
    "context_drift_bucket",
    "context_range_bucket",
    "volume_bucket",
    "cat_pre_above_ema200",
    "cat_ema_stack_bullish",
    "cat_peak_minute_bucket",
    "cat_volume_peak_minute_bucket",
    "cat_break_prev60_high",
    "cat_close_above_prev60_high",
    "cat_break_prev240_high",
    "cat_close_above_prev240_high",
]


def _base_scope(frame: pd.DataFrame) -> pd.DataFrame:
    scoped = frame[
        (frame["timeframe"].astype(str) == "5m")
        & (frame["session_id"].astype(str) == "asia")
        & (frame["market_side_hint"].astype(str) == "long")
        & (pd.to_numeric(frame["trigger_minute"], errors="coerce") == 0)
        & (frame["impulse_archetype"].astype(str) == "impulse_wicky_spike")
        & (frame["context_archetype"].astype(str).isin({"context_warm", "context_overheated"}))
        & (pd.to_numeric(frame["trigger_return_pct"], errors="coerce") >= 0.02)
        & (pd.to_numeric(frame["volume_mult"], errors="coerce") >= 10.0)
    ].copy()
    scoped["timestamp_utc"] = pd.to_datetime(pd.to_numeric(scoped["timestamp_ms"], errors="coerce"), unit="ms", utc=True, errors="coerce")
    scoped["cache_scope"] = scoped["dataset"].map({"current": "current", "old": "old"})
    last_timestamp = scoped["timestamp_utc"].max()
    if pd.notna(last_timestamp):
        cutoff = last_timestamp - pd.Timedelta(days=365)
        current_mask = scoped["dataset"].astype(str) == "current"
        scoped = scoped[(~current_mask) | (scoped["timestamp_utc"] >= cutoff)].copy()
    return scoped.reset_index(drop=True)


def _label_scope(frame: pd.DataFrame) -> pd.DataFrame:
    labeled = frame.copy()
    continuation = pd.to_numeric(labeled["post_max_up_extension_pct_30m"], errors="coerce")
    labeled["post_max_up_extension_pct_30m"] = continuation
    labeled["continuation_label"] = "middle"
    labeled.loc[continuation >= STRONG_CONTINUATION_THRESHOLD, "continuation_label"] = "strong"
    labeled.loc[continuation <= DEAD_PUMP_THRESHOLD, "continuation_label"] = "dead"
    labeled["is_labeled"] = labeled["continuation_label"].isin({"strong", "dead"})
    return labeled.reset_index(drop=True)


def _safe_div(numerator: float | int | None, denominator: float | int | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    numerator_float = float(numerator)
    denominator_float = float(denominator)
    if denominator_float == 0.0:
        return None
    return numerator_float / denominator_float


def _safe_pct(current: float | None, reference: float | None) -> float | None:
    if current is None or reference is None:
        return None
    current_float = float(current)
    reference_float = float(reference)
    if reference_float == 0.0:
        return None
    return (current_float / reference_float) - 1.0


def _slice_median(values: list[float], start: int, end: int) -> float | None:
    scoped = [float(value) for value in values[start:end] if pd.notna(value)]
    if not scoped:
        return None
    return float(median(scoped))


def _window_return(closes: list[float], start: int, end: int) -> float | None:
    if start < 0 or end > len(closes) or (end - start) < 2:
        return None
    return _safe_pct(float(closes[end - 1]), float(closes[start]))


def _window_range_pct(highs: list[float], lows: list[float], start: int, end: int) -> float | None:
    if start < 0 or end > len(highs) or end <= start:
        return None
    highest = max(float(value) for value in highs[start:end])
    lowest = min(float(value) for value in lows[start:end])
    if lowest <= 0.0:
        return None
    return (highest / lowest) - 1.0


def _window_realized_vol(closes: list[float], start: int, end: int) -> float | None:
    if start < 0 or end > len(closes) or (end - start) < 3:
        return None
    series = pd.Series(closes[start:end], dtype="float64").pct_change().dropna()
    if series.empty:
        return None
    return float(series.std(ddof=0))


def _window_green_ratio(opens: list[float], closes: list[float], start: int, end: int) -> float | None:
    if start < 0 or end > len(opens) or end <= start:
        return None
    greens = [(float(closes[idx]) > float(opens[idx])) for idx in range(start, end)]
    if not greens:
        return None
    return float(sum(greens) / len(greens))


def _window_up_volume_share(opens: list[float], closes: list[float], volumes: list[float], start: int, end: int) -> float | None:
    if start < 0 or end > len(opens) or end <= start:
        return None
    total_volume = float(sum(float(volumes[idx]) for idx in range(start, end)))
    if total_volume <= 0.0:
        return None
    up_volume = float(sum(float(volumes[idx]) for idx in range(start, end) if float(closes[idx]) > float(opens[idx])))
    return up_volume / total_volume


def _argmax_offset(values: list[float]) -> int | None:
    if not values:
        return None
    return int(max(range(len(values)), key=lambda idx: values[idx]))


def _peak_minute_bucket(offset: int | None) -> str | None:
    if offset is None:
        return None
    if offset <= 1:
        return "early_0_1"
    if offset == 2:
        return "mid_2"
    return "late_3_4"


def _derive_event_features(
    row: pd.Series,
    *,
    timestamp_to_idx: dict[int, int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    ema50: list[float],
    ema200: list[float],
) -> dict[str, object] | None:
    start_ts = int(row["timestamp_ms"])
    start_idx = timestamp_to_idx.get(start_ts)
    if start_idx is None:
        return None
    if start_idx < 60 or (start_idx + 4) >= len(closes):
        return None

    pre_idx = start_idx - 1
    pre_close = float(closes[pre_idx])
    prev60_high = max(float(value) for value in highs[start_idx - 60 : start_idx])
    prev240_high = max(float(value) for value in highs[max(0, start_idx - 240) : start_idx])

    event_opens = [float(value) for value in opens[start_idx : start_idx + 5]]
    event_highs = [float(value) for value in highs[start_idx : start_idx + 5]]
    event_lows = [float(value) for value in lows[start_idx : start_idx + 5]]
    event_closes = [float(value) for value in closes[start_idx : start_idx + 5]]
    event_volumes = [float(value) for value in volumes[start_idx : start_idx + 5]]
    event_total_volume = float(sum(event_volumes))
    event_high = max(event_highs)
    event_low = min(event_lows)
    event_close = event_closes[-1]
    event_range = max(1e-12, event_high - event_low)
    pre_median_volume_30 = _slice_median(volumes, start_idx - 30, start_idx)

    peak_minute = _argmax_offset(event_highs)
    volume_peak_minute = _argmax_offset(event_volumes)
    first_break_prev60 = next((offset for offset, value in enumerate(event_highs) if value > prev60_high), None)
    first_break_prev240 = next((offset for offset, value in enumerate(event_highs) if value > prev240_high), None)
    last2_volume_share = _safe_div(sum(event_volumes[3:5]), event_total_volume)
    positive_bodies = [max(0.0, event_closes[idx] - event_opens[idx]) for idx in range(5)]
    total_positive_body = float(sum(positive_bodies))
    last2_body_share = _safe_div(sum(positive_bodies[3:5]), total_positive_body) if total_positive_body > 0.0 else None
    pump_m0_volume_ratio_30 = _safe_div(event_volumes[0], pre_median_volume_30)
    pump_followthrough_after_m0_pct = _safe_pct(max(event_highs[1:5]), event_closes[0]) if len(event_highs) >= 5 else None
    pump_pullback_after_m0_pct = _safe_pct(event_closes[0], min(event_lows[1:5])) if len(event_lows) >= 5 else None

    return {
        "pre_close_vs_ema50_pct": _safe_pct(pre_close, float(ema50[pre_idx])) if pre_idx < len(ema50) else None,
        "pre_close_vs_ema200_pct": _safe_pct(pre_close, float(ema200[pre_idx])) if pre_idx < len(ema200) else None,
        "pre_ema50_vs_ema200_pct": _safe_pct(float(ema50[pre_idx]), float(ema200[pre_idx])) if pre_idx < len(ema50) and pre_idx < len(ema200) else None,
        "pre_ema200_slope_60m_pct": _safe_pct(float(ema200[pre_idx]), float(ema200[start_idx - 60])) if start_idx >= 60 else None,
        "pre_return_15m_pct": _window_return(closes, start_idx - 15, start_idx),
        "pre_return_30m_pct": _window_return(closes, start_idx - 30, start_idx),
        "pre_return_60m_pct": _window_return(closes, start_idx - 60, start_idx),
        "pre_range_15m_pct": _window_range_pct(highs, lows, start_idx - 15, start_idx),
        "pre_range_30m_pct": _window_range_pct(highs, lows, start_idx - 30, start_idx),
        "pre_range_60m_pct": _window_range_pct(highs, lows, start_idx - 60, start_idx),
        "pre_realized_vol_15m": _window_realized_vol(closes, start_idx - 15, start_idx),
        "pre_realized_vol_30m": _window_realized_vol(closes, start_idx - 30, start_idx),
        "pre_green_ratio_15m": _window_green_ratio(opens, closes, start_idx - 15, start_idx),
        "pre_green_ratio_30m": _window_green_ratio(opens, closes, start_idx - 30, start_idx),
        "pre_up_volume_share_30m": _window_up_volume_share(opens, closes, volumes, start_idx - 30, start_idx),
        "pre_volume_last5_vs_prev25": _safe_div(_slice_median(volumes, start_idx - 5, start_idx), _slice_median(volumes, start_idx - 30, start_idx - 5)),
        "pre_volume_last10_vs_prev20": _safe_div(_slice_median(volumes, start_idx - 10, start_idx), _slice_median(volumes, start_idx - 30, start_idx - 10)),
        "pre_dist_to_prev60_high_pct": _safe_pct(prev60_high, pre_close),
        "pre_dist_to_prev240_high_pct": _safe_pct(prev240_high, pre_close),
        "pump_m0_return_pct": _safe_pct(event_closes[0], event_opens[0]),
        "pump_m0_close_pos": _close_pos(event_highs[0], event_lows[0], event_closes[0]),
        "pump_m0_body_frac_range": _safe_div(max(0.0, event_closes[0] - event_opens[0]), max(1e-12, event_highs[0] - event_lows[0])),
        "pump_m0_volume_ratio_30": pump_m0_volume_ratio_30,
        "pump_total_green_ratio_5m": _window_green_ratio(event_opens, event_closes, 0, 5),
        "pump_close_vs_m0close_pct": _safe_pct(event_close, event_closes[0]),
        "pump_followthrough_after_m0_pct": pump_followthrough_after_m0_pct,
        "pump_pullback_after_m0_pct": pump_pullback_after_m0_pct,
        "pump_last2_volume_share": last2_volume_share,
        "pump_last2_body_share": last2_body_share,
        "pump_peak_minute": peak_minute,
        "pump_volume_peak_minute": volume_peak_minute,
        "pump_event_high_vs_prev60_high_pct": _safe_pct(event_high, prev60_high),
        "pump_event_close_vs_prev60_high_pct": _safe_pct(event_close, prev60_high),
        "pump_event_high_vs_prev240_high_pct": _safe_pct(event_high, prev240_high),
        "pump_event_close_vs_prev240_high_pct": _safe_pct(event_close, prev240_high),
        "pump_break_prev60_minute": first_break_prev60,
        "pump_break_prev240_minute": first_break_prev240,
        "cat_pre_above_ema200": "yes" if (_safe_pct(pre_close, float(ema200[pre_idx])) or 0.0) > 0.0 else "no",
        "cat_ema_stack_bullish": "yes" if ((_safe_pct(float(ema50[pre_idx]), float(ema200[pre_idx])) or -1.0) > 0.0) else "no",
        "cat_peak_minute_bucket": _peak_minute_bucket(peak_minute),
        "cat_volume_peak_minute_bucket": _peak_minute_bucket(volume_peak_minute),
        "cat_break_prev60_high": "yes" if first_break_prev60 is not None else "no",
        "cat_close_above_prev60_high": "yes" if event_close > prev60_high else "no",
        "cat_break_prev240_high": "yes" if first_break_prev240 is not None else "no",
        "cat_close_above_prev240_high": "yes" if event_close > prev240_high else "no",
    }


def _build_feature_frame(scope: pd.DataFrame) -> pd.DataFrame:
    cache: dict[tuple[str, str], pd.DataFrame] = {}
    rows: list[dict[str, object]] = []
    grouped = scope.groupby(["cache_scope", "symbol"], sort=True)
    total_groups = grouped.ngroups
    for group_index, ((cache_scope, symbol), scoped) in enumerate(grouped, start=1):
        candles = _load_m1(str(cache_scope), str(symbol), cache)
        if candles.empty:
            continue
        timestamps = pd.to_numeric(candles["timestamp"], errors="coerce").astype("int64").tolist()
        timestamp_to_idx = {int(timestamp): idx for idx, timestamp in enumerate(timestamps)}
        opens = pd.to_numeric(candles["open"], errors="coerce").astype(float).tolist()
        highs = pd.to_numeric(candles["high"], errors="coerce").astype(float).tolist()
        lows = pd.to_numeric(candles["low"], errors="coerce").astype(float).tolist()
        closes = pd.to_numeric(candles["close"], errors="coerce").astype(float).tolist()
        volumes = pd.to_numeric(candles["volume"], errors="coerce").astype(float).tolist()
        ema50 = pd.Series(closes, dtype="float64").ewm(span=50, adjust=False).mean().tolist()
        ema200 = pd.Series(closes, dtype="float64").ewm(span=200, adjust=False).mean().tolist()
        if group_index == 1 or group_index % 25 == 0 or group_index == total_groups:
            print(
                f"xx00-pump-nature: progress={group_index}/{total_groups} "
                f"cache={cache_scope} symbol={symbol} events={len(scoped)}"
            )
        for _, row in scoped.iterrows():
            derived = _derive_event_features(
                row,
                timestamp_to_idx=timestamp_to_idx,
                opens=opens,
                highs=highs,
                lows=lows,
                closes=closes,
                volumes=volumes,
                ema50=ema50,
                ema200=ema200,
            )
            if derived is None:
                continue
            base = row.to_dict()
            rows.append({**base, **derived})
    feature_frame = pd.DataFrame(rows)
    if feature_frame.empty:
        return feature_frame
    feature_frame["timestamp_utc"] = pd.to_datetime(pd.to_numeric(feature_frame["timestamp_ms"], errors="coerce"), unit="ms", utc=True, errors="coerce")
    return feature_frame.reset_index(drop=True)


def _pooled_std(left: pd.Series, right: pd.Series) -> float | None:
    if len(left) < 2 or len(right) < 2:
        return None
    left_std = float(left.std(ddof=1))
    right_std = float(right.std(ddof=1))
    if pd.isna(left_std) or pd.isna(right_std):
        return None
    pooled = (((len(left) - 1) * (left_std**2)) + ((len(right) - 1) * (right_std**2))) / (len(left) + len(right) - 2)
    if pooled <= 0.0:
        return None
    return pooled ** 0.5


def _numeric_feature_ranking(frame: pd.DataFrame, feature_columns: list[str]) -> pd.DataFrame:
    labeled = frame[frame["continuation_label"].astype(str).isin({"strong", "dead"})].copy()
    rows: list[dict[str, object]] = []
    for feature in feature_columns:
        row: dict[str, object] = {"feature": feature}
        current_sign = 0
        old_sign = 0
        for dataset in ("current", "old"):
            scoped = labeled[labeled["dataset"].astype(str) == dataset].copy()
            series = pd.to_numeric(scoped.get(feature), errors="coerce")
            scoped = scoped.assign(feature_value=series).dropna(subset=["feature_value"]).copy()
            strong = scoped[scoped["continuation_label"].astype(str) == "strong"]["feature_value"]
            dead = scoped[scoped["continuation_label"].astype(str) == "dead"]["feature_value"]
            row[f"{dataset}_coverage"] = int(len(scoped))
            row[f"{dataset}_strong_n"] = int(len(strong))
            row[f"{dataset}_dead_n"] = int(len(dead))
            row[f"{dataset}_strong_mean"] = float(strong.mean()) if not strong.empty else None
            row[f"{dataset}_dead_mean"] = float(dead.mean()) if not dead.empty else None
            row[f"{dataset}_strong_median"] = float(strong.median()) if not strong.empty else None
            row[f"{dataset}_dead_median"] = float(dead.median()) if not dead.empty else None
            diff = (float(strong.mean()) - float(dead.mean())) if not strong.empty and not dead.empty else None
            row[f"{dataset}_mean_diff"] = diff
            pooled = _pooled_std(strong, dead) if not strong.empty and not dead.empty else None
            effect = (diff / pooled) if (diff is not None and pooled not in {None, 0.0}) else None
            row[f"{dataset}_effect_size"] = effect
            if diff is not None:
                if diff > 0:
                    if dataset == "current":
                        current_sign = 1
                    else:
                        old_sign = 1
                elif diff < 0:
                    if dataset == "current":
                        current_sign = -1
                    else:
                        old_sign = -1
            if len(scoped) >= 12 and strong.nunique() > 0 and dead.nunique() > 0:
                q_low = float(scoped["feature_value"].quantile(0.30))
                q_high = float(scoped["feature_value"].quantile(0.70))
                high_slice = scoped[scoped["feature_value"] >= q_high]
                low_slice = scoped[scoped["feature_value"] <= q_low]
                high_strong_rate = float((high_slice["continuation_label"].astype(str) == "strong").mean()) if not high_slice.empty else None
                low_strong_rate = float((low_slice["continuation_label"].astype(str) == "strong").mean()) if not low_slice.empty else None
                row[f"{dataset}_high_strong_rate"] = high_strong_rate
                row[f"{dataset}_low_strong_rate"] = low_strong_rate
                row[f"{dataset}_bin_lift"] = (high_strong_rate - low_strong_rate) if high_strong_rate is not None and low_strong_rate is not None else None
            else:
                row[f"{dataset}_high_strong_rate"] = None
                row[f"{dataset}_low_strong_rate"] = None
                row[f"{dataset}_bin_lift"] = None
        row["same_direction"] = bool(current_sign != 0 and current_sign == old_sign)
        row["direction"] = "higher_is_better" if current_sign > 0 else ("lower_is_better" if current_sign < 0 else "mixed")
        current_effect = abs(float(row["current_effect_size"])) if row.get("current_effect_size") is not None else 0.0
        old_effect = abs(float(row["old_effect_size"])) if row.get("old_effect_size") is not None else 0.0
        row["robust_strength"] = min(current_effect, old_effect) if bool(row["same_direction"]) else -max(current_effect, old_effect)
        rows.append(row)
    ranking = pd.DataFrame(rows)
    if ranking.empty:
        return ranking
    return ranking.sort_values(["robust_strength", "current_effect_size"], ascending=[False, False]).reset_index(drop=True)


def _categorical_summary(frame: pd.DataFrame, feature_columns: list[str]) -> pd.DataFrame:
    labeled = frame[frame["continuation_label"].astype(str).isin({"strong", "dead"})].copy()
    rows: list[dict[str, object]] = []
    for feature in feature_columns:
        scoped_feature = labeled[labeled[feature].notna()].copy() if feature in labeled.columns else pd.DataFrame()
        if scoped_feature.empty:
            continue
        for value, scoped_value in scoped_feature.groupby(feature, sort=True):
            row: dict[str, object] = {"feature": feature, "value": value}
            current_rate = None
            old_rate = None
            for dataset in ("current", "old"):
                subset = scoped_value[scoped_value["dataset"].astype(str) == dataset].copy()
                strong_n = int((subset["continuation_label"].astype(str) == "strong").sum())
                dead_n = int((subset["continuation_label"].astype(str) == "dead").sum())
                total = strong_n + dead_n
                rate = (strong_n / total) if total > 0 else None
                row[f"{dataset}_strong_n"] = strong_n
                row[f"{dataset}_dead_n"] = dead_n
                row[f"{dataset}_labeled_n"] = total
                row[f"{dataset}_strong_rate"] = rate
                if dataset == "current":
                    current_rate = rate
                else:
                    old_rate = rate
            row["same_direction"] = (
                current_rate is not None
                and old_rate is not None
                and ((current_rate >= 0.5 and old_rate >= 0.5) or (current_rate < 0.5 and old_rate < 0.5))
            )
            row["robust_edge"] = min(current_rate, old_rate) if bool(row["same_direction"]) and current_rate is not None and old_rate is not None else -1.0
            rows.append(row)
    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary
    return summary.sort_values(["robust_edge", "current_strong_rate", "current_labeled_n"], ascending=[False, False, False]).reset_index(drop=True)


def _selected_examples(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    strong = frame[frame["continuation_label"].astype(str) == "strong"].copy()
    dead = frame[frame["continuation_label"].astype(str) == "dead"].copy()
    strong = strong.sort_values(["post_max_up_extension_pct_30m", "volume_mult"], ascending=[False, False]).head(20)
    dead = dead.sort_values(["post_max_up_extension_pct_30m", "volume_mult"], ascending=[True, False]).head(20)
    return strong.reset_index(drop=True), dead.reset_index(drop=True)


def _label_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for dataset, scoped in frame.groupby("dataset", sort=True):
        counts = scoped["continuation_label"].astype(str).value_counts()
        total = int(len(scoped))
        labeled_total = int(counts.get("strong", 0) + counts.get("dead", 0))
        rows.append(
            {
                "dataset": dataset,
                "total_cases": total,
                "strong_cases": int(counts.get("strong", 0)),
                "dead_cases": int(counts.get("dead", 0)),
                "middle_cases": int(counts.get("middle", 0)),
                "strong_rate_among_labeled": (int(counts.get("strong", 0)) / labeled_total) if labeled_total > 0 else None,
            }
        )
    return pd.DataFrame(rows)


def _regime_combo_summary(frame: pd.DataFrame) -> pd.DataFrame:
    labeled = frame[frame["continuation_label"].astype(str).isin({"strong", "dead"})].copy()
    if labeled.empty:
        return pd.DataFrame()
    labeled["flag_high_pre_range"] = pd.to_numeric(labeled.get("pre_base_range_pct_60m"), errors="coerce") >= 0.05
    labeled["flag_above_ema200"] = pd.to_numeric(labeled.get("pre_close_vs_ema200_pct"), errors="coerce") > 0.0
    labeled["flag_late_peak"] = labeled.get("cat_peak_minute_bucket", pd.Series(index=labeled.index)).astype(str) == "late_3_4"
    labeled["flag_break240"] = labeled.get("cat_break_prev240_high", pd.Series(index=labeled.index)).astype(str) == "yes"
    combos = [
        (
            "overheated + ema200 + high_pre_range",
            (labeled["context_archetype"].astype(str) == "context_overheated")
            & labeled["flag_above_ema200"].astype(bool)
            & labeled["flag_high_pre_range"].astype(bool),
        ),
        (
            "overheated + late_peak + break240",
            (labeled["context_archetype"].astype(str) == "context_overheated")
            & labeled["flag_late_peak"].astype(bool)
            & labeled["flag_break240"].astype(bool),
        ),
        (
            "drift_hot + ema200 + late_peak",
            (labeled["context_drift_bucket"].astype(str) == "drift_hot")
            & labeled["flag_above_ema200"].astype(bool)
            & labeled["flag_late_peak"].astype(bool),
        ),
        (
            "vol_ramp_hot + ema200",
            (labeled["pre_accumulation_type"].astype(str) == "volume_ramp_hot")
            & labeled["flag_above_ema200"].astype(bool),
        ),
        (
            "warm + drift_warm",
            (labeled["context_archetype"].astype(str) == "context_warm")
            & (labeled["context_drift_bucket"].astype(str) == "drift_warm"),
        ),
        (
            "warm + below_ema200",
            (labeled["context_archetype"].astype(str) == "context_warm")
            & (~labeled["flag_above_ema200"].astype(bool)),
        ),
        (
            "warm + below_ema200 + early_peak",
            (labeled["context_archetype"].astype(str) == "context_warm")
            & (~labeled["flag_above_ema200"].astype(bool))
            & (~labeled["flag_late_peak"].astype(bool)),
        ),
    ]
    rows: list[dict[str, object]] = []
    for combo_id, mask in combos:
        for dataset in ("current", "old"):
            subset = labeled[(labeled["dataset"].astype(str) == dataset) & mask].copy()
            strong_n = int((subset["continuation_label"].astype(str) == "strong").sum())
            dead_n = int((subset["continuation_label"].astype(str) == "dead").sum())
            total = strong_n + dead_n
            rows.append(
                {
                    "combo_id": combo_id,
                    "dataset": dataset,
                    "labeled_n": total,
                    "strong_n": strong_n,
                    "dead_n": dead_n,
                    "strong_rate": (strong_n / total) if total > 0 else None,
                }
            )
    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary
    return summary.sort_values(["combo_id", "dataset"]).reset_index(drop=True)


def _build_report(
    label_summary: pd.DataFrame,
    numeric_ranking: pd.DataFrame,
    categorical_summary: pd.DataFrame,
    regime_summary: pd.DataFrame,
    strong_examples: pd.DataFrame,
    dead_examples: pd.DataFrame,
) -> str:
    robust_numeric = numeric_ranking[numeric_ranking["same_direction"].astype(bool)].copy() if not numeric_ranking.empty else pd.DataFrame()
    robust_categories = categorical_summary[
        categorical_summary["same_direction"].astype(bool)
        & (pd.to_numeric(categorical_summary["current_labeled_n"], errors="coerce") >= 8)
        & (pd.to_numeric(categorical_summary["old_labeled_n"], errors="coerce") >= 4)
    ].copy() if not categorical_summary.empty else pd.DataFrame()
    lines = [
        "# XX:00 Pump Nature Research",
        "",
        "Universe:",
        "- Asia long, trigger at `00` minute.",
        "- `impulse_wicky_spike` only.",
        "- `context_warm` or `context_overheated`.",
        "- `trigger_return_pct >= 2%` and `volume_mult >= 10`.",
        "",
        "Labels:",
        f"- `strong continuation`: post max extension in next 30m >= `{STRONG_CONTINUATION_THRESHOLD:.1%}`.",
        f"- `dead pump`: post max extension in next 30m <= `{DEAD_PUMP_THRESHOLD:.1%}`.",
        "- middle zone is ignored for separation so the comparison stays clean.",
        "",
        "## Label Split",
        _frame_to_markdown(
            label_summary,
            columns=["dataset", "total_cases", "strong_cases", "dead_cases", "middle_cases", "strong_rate_among_labeled"],
        )
        if not label_summary.empty
        else "_Нет данных._",
        "",
        "## Robust Numeric Separators",
        _frame_to_markdown(
            robust_numeric.head(20),
            columns=[
                "feature",
                "direction",
                "current_effect_size",
                "old_effect_size",
                "current_strong_mean",
                "current_dead_mean",
                "old_strong_mean",
                "old_dead_mean",
                "current_bin_lift",
                "old_bin_lift",
                "robust_strength",
            ],
        )
        if not robust_numeric.empty
        else "_Пока нет числовых признаков с одинаковым направлением в current и old._",
        "",
        "## Robust Categorical Separators",
        _frame_to_markdown(
            robust_categories.head(20),
            columns=[
                "feature",
                "value",
                "current_labeled_n",
                "current_strong_rate",
                "old_labeled_n",
                "old_strong_rate",
            ],
        )
        if not robust_categories.empty
        else "_Пока нет категориальных признаков с достаточной current/old устойчивостью._",
        "",
        "## Regime Combos",
        _frame_to_markdown(
            regime_summary,
            columns=["combo_id", "dataset", "labeled_n", "strong_n", "dead_n", "strong_rate"],
        )
        if not regime_summary.empty
        else "_Нет regime-комбинаций._",
        "",
        "## Strong Examples",
        _frame_to_markdown(
            strong_examples,
            columns=[
                "dataset",
                "timestamp_utc",
                "symbol",
                "context_archetype",
                "pre_accumulation_type",
                "trigger_return_pct",
                "volume_mult",
                "post_max_up_extension_pct_30m",
                "pump_m0_return_pct",
                "pump_peak_minute",
                "pump_last2_volume_share",
            ],
            limit=15,
        )
        if not strong_examples.empty
        else "_Нет strong-примеров._",
        "",
        "## Dead Examples",
        _frame_to_markdown(
            dead_examples,
            columns=[
                "dataset",
                "timestamp_utc",
                "symbol",
                "context_archetype",
                "pre_accumulation_type",
                "trigger_return_pct",
                "volume_mult",
                "post_max_up_extension_pct_30m",
                "pump_m0_return_pct",
                "pump_peak_minute",
                "pump_last2_volume_share",
            ],
            limit=15,
        )
        if not dead_examples.empty
        else "_Нет dead-примеров._",
        "",
        "## Reading Guide",
        "- `direction = higher_is_better` means stronger continuations tend to have larger feature values.",
        "- `current_bin_lift` compares strong-continuation rate in the top 30% of feature values against the bottom 30%.",
        "- Only features with the same sign in `current` and `old` should be treated as nature clues rather than sample noise.",
    ]
    return "\n".join(lines)


def run() -> dict[str, Path]:
    print("xx00-pump-nature: start")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    base = _label_scope(_base_scope(_load_feature_db()))
    print(f"xx00-pump-nature: scoped_cases={len(base)}")
    feature_frame = _build_feature_frame(base)
    print(f"xx00-pump-nature: feature_rows={len(feature_frame)}")

    numeric_features = [column for column in BASE_DB_NUMERIC_FEATURES if column in feature_frame.columns]
    numeric_features.extend(
        column
        for column in feature_frame.columns
        if column.startswith("pre_") or column.startswith("pump_")
    )
    numeric_features = sorted(dict.fromkeys(numeric_features))

    label_summary = _label_summary(feature_frame)
    numeric_ranking = _numeric_feature_ranking(feature_frame, numeric_features)
    categorical_summary = _categorical_summary(feature_frame, [column for column in CATEGORICAL_FEATURES if column in feature_frame.columns])
    regime_summary = _regime_combo_summary(feature_frame)
    strong_examples, dead_examples = _selected_examples(feature_frame)
    report = _build_report(label_summary, numeric_ranking, categorical_summary, regime_summary, strong_examples, dead_examples)

    paths = {
        "feature_frame": OUTPUT_DIR / "feature_frame.csv",
        "label_summary": OUTPUT_DIR / "label_summary.csv",
        "numeric_ranking": OUTPUT_DIR / "numeric_ranking.csv",
        "categorical_summary": OUTPUT_DIR / "categorical_summary.csv",
        "regime_summary": OUTPUT_DIR / "regime_summary.csv",
        "strong_examples": OUTPUT_DIR / "strong_examples.csv",
        "dead_examples": OUTPUT_DIR / "dead_examples.csv",
        "report": OUTPUT_DIR / "report.md",
    }
    feature_frame.to_csv(paths["feature_frame"], index=False)
    label_summary.to_csv(paths["label_summary"], index=False)
    numeric_ranking.to_csv(paths["numeric_ranking"], index=False)
    categorical_summary.to_csv(paths["categorical_summary"], index=False)
    regime_summary.to_csv(paths["regime_summary"], index=False)
    strong_examples.to_csv(paths["strong_examples"], index=False)
    dead_examples.to_csv(paths["dead_examples"], index=False)
    paths["report"].write_text(report, encoding="utf-8")
    print("xx00-pump-nature: done")
    return paths


if __name__ == "__main__":
    output_paths = run()
    for key, value in output_paths.items():
        print(f"{key}: {value}")
