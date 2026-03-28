from __future__ import annotations

import hashlib
import itertools
import json
import logging
import math
from pathlib import Path
from typing import Any

import pandas as pd

_STATIC_TOP_K_VALUES: tuple[int, ...] = (99, 1, 2)
_STATIC_MIN_COMPONENTS = 2
_STATIC_MAX_COMPONENTS = 5
_STATIC_MIN_CANDIDATE_EVENTS = 6
_STATIC_MIN_TRADES_PER_YEAR = 50.0
_STATIC_MIN_MEAN_RETURN_PCT = 0.02
_STATIC_MIN_WIN_RATE = 0.40
_STATIC_MIN_ANNUALIZED_SUM_RETURN_PCT = 1.0
_STATIC_MAX_DRAWDOWN_PCT = 0.30
_STATIC_MIN_POSITIVE_MONTHS = 9
_STATIC_HOLDOUT_SPLITS: tuple[tuple[str, int], ...] = (
    ("train8_test4", 8),
    ("train9_test3", 9),
)
_STATIC_PRIORITY_TOP_N_VALUES: tuple[int, ...] = (1, 3, 5, 10, 20, 50, 100)
_STATIC_LOCAL_COMPONENT_DISTANCE_MAX = 2


def _safe_numeric(value: object) -> float | None:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, bool):
        return float(value)
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric):
        return None
    return numeric


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _profit_factor(returns: pd.Series) -> float | None:
    if returns.empty:
        return None
    wins = float(returns[returns > 0].sum())
    losses = float(returns[returns < 0].sum())
    if losses == 0.0:
        return math.inf if wins > 0.0 else None
    return wins / abs(losses)


def _dedupe_source_events(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    ordered = frame.sort_values(["symbol", "timestamp_ms", "exit_return_pct"], ascending=[True, True, False]).copy()
    return ordered.drop_duplicates(["symbol", "timestamp_ms"]).reset_index(drop=True)


def _prepare_base_events(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if frame.empty:
        return frame
    frame = frame[frame.get("trade_triggered", pd.Series(False, index=frame.index)).astype(str).str.lower() == "true"].copy()
    frame["entry_timestamp_ms"] = pd.to_numeric(frame.get("entry_timestamp_ms"), errors="coerce")
    frame["entry_timestamp_utc"] = pd.to_datetime(frame.get("entry_timestamp_utc"), utc=True, errors="coerce")
    frame["month_utc"] = frame["entry_timestamp_utc"].dt.strftime("%Y-%m")
    frame["date_utc"] = frame["entry_timestamp_utc"].dt.strftime("%Y-%m-%d")
    return frame


def _prepare_confirmed_events(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if frame.empty:
        return frame
    frame = frame[frame.get("trade_triggered", pd.Series(False, index=frame.index)).astype(str).str.lower() == "true"].copy()
    frame["entry_timestamp_ms"] = pd.to_numeric(frame.get("entry_timestamp_ms"), errors="coerce")
    frame["entry_timestamp_utc"] = pd.to_datetime(frame.get("entry_timestamp_utc"), utc=True, errors="coerce")
    frame["month_utc"] = frame["entry_timestamp_utc"].dt.strftime("%Y-%m")
    frame["date_utc"] = frame["entry_timestamp_utc"].dt.strftime("%Y-%m-%d")
    next_high = pd.to_numeric(frame.get("next_bar_high_price"), errors="coerce")
    next_low = pd.to_numeric(frame.get("next_bar_low_price"), errors="coerce")
    next_close = pd.to_numeric(frame.get("next_bar_close_price"), errors="coerce")
    trigger_high = pd.to_numeric(frame.get("trigger_high"), errors="coerce")
    trigger_low = pd.to_numeric(frame.get("trigger_low"), errors="coerce")
    next_range = (next_high - next_low).replace(0, pd.NA)
    trigger_range = (trigger_high - trigger_low).replace(0, pd.NA)
    frame["next_close_to_high_frac"] = (next_high - next_close) / next_range
    frame["next_range_vs_trigger"] = next_range / trigger_range
    return frame


def _build_static_candidate_frames(
    *,
    base_events: pd.DataFrame,
    confirmed_events: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    candidates: dict[str, pd.DataFrame] = {}

    def _add_candidate(candidate_id: str, frame: pd.DataFrame) -> None:
        deduped = _dedupe_source_events(frame)
        if len(deduped) >= _STATIC_MIN_CANDIDATE_EVENTS:
            candidates[candidate_id] = deduped

    base = base_events.copy()
    confirmed = confirmed_events.copy()

    mb5_01 = base[
        (base.get("trade_model_id", pd.Series(dtype="object")).astype(str) == "monster_break_5pct")
        & (pd.to_numeric(base.get("hour_utc"), errors="coerce") == 1)
    ].copy()
    mb5_01_pullback = _numeric_series(mb5_01, "pre_entry_pullback_frac")
    _add_candidate("mb5_01_shallow", mb5_01[mb5_01_pullback <= mb5_01_pullback.quantile(0.5)])

    mb3_03 = base[
        (base.get("trade_model_id", pd.Series(dtype="object")).astype(str) == "monster_break_3pct")
        & (pd.to_numeric(base.get("hour_utc"), errors="coerce") == 3)
    ].copy()
    mb3_03_trigger = _numeric_series(mb3_03, "trigger_return_pct")
    _add_candidate("mb3_03_trg_q75", mb3_03[mb3_03_trigger >= mb3_03_trigger.quantile(0.75)])
    _add_candidate("mb3_03_raw", mb3_03)

    sm75_00 = base[
        (base.get("trade_model_id", pd.Series(dtype="object")).astype(str) == "super_monster_7p5pct")
        & (pd.to_numeric(base.get("hour_utc"), errors="coerce") == 0)
    ].copy()
    sm75_risk = _numeric_series(sm75_00, "initial_risk_pct")
    _add_candidate("sm75_00_risk_q25", sm75_00[sm75_risk <= sm75_risk.quantile(0.25)])

    mb5_06 = base[
        (base.get("trade_model_id", pd.Series(dtype="object")).astype(str) == "monster_break_5pct")
        & (pd.to_numeric(base.get("hour_utc"), errors="coerce") == 6)
    ].copy()
    mb5_06_pullback = _numeric_series(mb5_06, "pre_entry_pullback_frac")
    _add_candidate("mb5_06_shallow", mb5_06[mb5_06_pullback <= mb5_06_pullback.quantile(0.5)])

    _add_candidate(
        "mb5_03_raw",
        base[
            (base.get("trade_model_id", pd.Series(dtype="object")).astype(str) == "monster_break_5pct")
            & (pd.to_numeric(base.get("hour_utc"), errors="coerce") == 3)
        ].copy(),
    )
    _add_candidate(
        "tf7_raw",
        base[
            (base.get("trade_model_id", pd.Series(dtype="object")).astype(str) == "tight_flag_runner")
            & (pd.to_numeric(base.get("hour_utc"), errors="coerce") == 7)
        ].copy(),
    )

    confirmed = confirmed[pd.to_numeric(confirmed.get("hour_utc"), errors="coerce") == 0].copy()
    shared_confirmed_filter = (
        (confirmed.get("stop_style", pd.Series(dtype="object")).astype(str) == "next_low")
        & (pd.to_numeric(confirmed.get("next_close_to_high_frac"), errors="coerce") <= 0.5)
        & (pd.to_numeric(confirmed.get("pre_base_range_pct_60m"), errors="coerce") <= 0.06)
    )
    _add_candidate(
        "conf00_trg65_pb50",
        confirmed[
            shared_confirmed_filter
            & (pd.to_numeric(confirmed.get("trigger_return_pct"), errors="coerce") >= 0.065)
            & (pd.to_numeric(confirmed.get("next_bar_pullback_frac"), errors="coerce") <= 0.5)
            & (pd.to_numeric(confirmed.get("next_range_vs_trigger"), errors="coerce") <= 2.0)
        ].copy(),
    )
    _add_candidate(
        "conf00_trg75_pb50",
        confirmed[
            shared_confirmed_filter
            & (pd.to_numeric(confirmed.get("trigger_return_pct"), errors="coerce") >= 0.075)
            & (pd.to_numeric(confirmed.get("next_bar_pullback_frac"), errors="coerce") <= 0.5)
            & (pd.to_numeric(confirmed.get("next_range_vs_trigger"), errors="coerce") <= 2.0)
        ].copy(),
    )
    _add_candidate(
        "conf00_trg65_pb25_rng12",
        confirmed[
            shared_confirmed_filter
            & (pd.to_numeric(confirmed.get("trigger_return_pct"), errors="coerce") >= 0.065)
            & (pd.to_numeric(confirmed.get("next_bar_pullback_frac"), errors="coerce") <= 0.25)
            & (pd.to_numeric(confirmed.get("next_range_vs_trigger"), errors="coerce") <= 1.2)
        ].copy(),
    )

    return candidates


def _summarize_events(frame: pd.DataFrame) -> dict[str, object] | None:
    if frame.empty:
        return None
    ordered = frame.sort_values("entry_timestamp_ms").copy()
    returns = pd.to_numeric(ordered.get("exit_return_pct"), errors="coerce").dropna()
    if returns.empty:
        return None

    entry_timestamps = pd.to_datetime(ordered.get("entry_timestamp_ms"), unit="ms", utc=True, errors="coerce").dropna()
    if len(entry_timestamps) >= 2:
        span_days = max(1.0, (entry_timestamps.max() - entry_timestamps.min()).total_seconds() / 86_400)
    else:
        span_days = float(max(1, ordered.get("date_utc", pd.Series(dtype="object")).nunique()))

    monthly_returns = ordered.groupby("month_utc", sort=True)["exit_return_pct"].sum()
    equity_curve = 1.0 + returns.cumsum()
    drawdown = ((equity_curve.cummax() - equity_curve) / equity_curve.cummax().replace(0, pd.NA)).fillna(0.0)

    return {
        "trades_count": int(len(ordered)),
        "trade_days_count": int(ordered.get("date_utc", pd.Series(dtype="object")).nunique()),
        "trades_per_year": float((len(ordered) / span_days) * 365.0),
        "mean_return_pct": float(returns.mean()),
        "median_return_pct": float(returns.median()),
        "win_rate": float((returns > 0).mean()),
        "profit_factor": _profit_factor(returns),
        "annual_sum_return_pct": float(returns.sum()),
        "annualized_sum_return_pct": float(returns.sum() * (12.0 / max(1, len(monthly_returns)))),
        "mean_pos_trade_pct": float(returns[returns > 0].mean()) if (returns > 0).any() else None,
        "mean_neg_trade_pct": float(returns[returns < 0].mean()) if (returns < 0).any() else None,
        "positive_months_count": int((monthly_returns > 0).sum()),
        "non_positive_months_count": int((monthly_returns <= 0).sum()),
        "all_active_months_positive": bool((monthly_returns > 0).all()) if not monthly_returns.empty else False,
        "best_month_return_pct": float(monthly_returns.max()) if not monthly_returns.empty else None,
        "worst_month_return_pct": float(monthly_returns.min()) if not monthly_returns.empty else None,
        "max_drawdown_pct": float(drawdown.max()) if not drawdown.empty else None,
    }


def _sort_summary_frame(
    frame: pd.DataFrame,
    *,
    sort_columns: list[str],
    ascending: list[bool],
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    available_columns = [column for column in sort_columns if column in frame.columns]
    if not available_columns:
        return frame.reset_index(drop=True)
    available_ascending = [ascending[sort_columns.index(column)] for column in available_columns]
    return frame.sort_values(available_columns, ascending=available_ascending, na_position="last").reset_index(drop=True)


def _build_combo_id(component_ids: list[str], top_k_per_timestamp: int) -> str:
    return f"{'|'.join(component_ids)}__top{top_k_per_timestamp}"


def _build_combo_events(
    *,
    candidate_frames: dict[str, pd.DataFrame],
    component_ids: list[str],
    top_k_per_timestamp: int,
) -> pd.DataFrame:
    component_parts: list[pd.DataFrame] = []
    for component_id in component_ids:
        part = candidate_frames[component_id].copy()
        part["combo_component_id"] = component_id
        component_parts.append(part)
    if not component_parts:
        return pd.DataFrame()

    combined = pd.concat(component_parts, ignore_index=True)
    rows: list[dict[str, object]] = []
    grouped = combined.groupby(["symbol", "timestamp_ms"], sort=True)
    for (symbol, timestamp_ms), group in grouped:
        sorted_group = group.sort_values(["entry_timestamp_ms", "combo_component_id"], na_position="last").copy()
        exit_returns = pd.to_numeric(sorted_group.get("exit_return_pct"), errors="coerce").dropna()
        if exit_returns.empty:
            continue
        entry_timestamp_ms = pd.to_numeric(sorted_group.get("entry_timestamp_ms"), errors="coerce").dropna()
        first = sorted_group.iloc[0]
        rows.append(
            {
                "symbol": str(symbol),
                "timestamp_ms": int(timestamp_ms),
                "entry_timestamp_ms": int(entry_timestamp_ms.min()) if not entry_timestamp_ms.empty else None,
                "entry_timestamp_utc": first.get("entry_timestamp_utc"),
                "date_utc": first.get("date_utc"),
                "month_utc": first.get("month_utc"),
                "hour_utc": first.get("hour_utc"),
                "combo_matched_component_count": int(sorted_group["combo_component_id"].nunique()),
                "combo_matched_component_ids": ",".join(sorted(sorted_group["combo_component_id"].astype(str).unique().tolist())),
                "exit_return_pct": float(exit_returns.mean()),
                "trigger_return_pct": float(pd.to_numeric(sorted_group.get("trigger_return_pct"), errors="coerce").max()),
                "volume_mult": float(pd.to_numeric(sorted_group.get("volume_mult"), errors="coerce").max()),
                "range_atr": float(pd.to_numeric(sorted_group.get("range_atr"), errors="coerce").max()),
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    if top_k_per_timestamp != 99:
        ranked = frame.sort_values(["timestamp_ms", "trigger_return_pct", "volume_mult", "range_atr"], ascending=[True, False, False, False]).copy()
        ranked["rank_in_timestamp"] = ranked.groupby("timestamp_ms").cumcount() + 1
        frame = ranked[ranked["rank_in_timestamp"] <= top_k_per_timestamp].copy()
    frame["combo_id"] = _build_combo_id(component_ids, top_k_per_timestamp)
    frame["component_ids"] = ",".join(component_ids)
    frame["component_count"] = len(component_ids)
    frame["top_k_per_timestamp"] = top_k_per_timestamp
    return frame.reset_index(drop=True)


def _build_event_signature(frame: pd.DataFrame) -> str:
    if frame.empty:
        return ""
    payload = "\n".join(
        sorted(
            f"{row.symbol}|{int(row.timestamp_ms)}|{round(float(row.exit_return_pct), 8)}"
            for row in frame[["symbol", "timestamp_ms", "exit_return_pct"]].itertuples(index=False)
        )
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _meets_static_goal(summary_row: pd.Series) -> bool:
    return bool(
        pd.to_numeric(summary_row.get("trades_per_year"), errors="coerce") >= _STATIC_MIN_TRADES_PER_YEAR
        and pd.to_numeric(summary_row.get("mean_return_pct"), errors="coerce") >= _STATIC_MIN_MEAN_RETURN_PCT
        and pd.to_numeric(summary_row.get("win_rate"), errors="coerce") > _STATIC_MIN_WIN_RATE
        and pd.to_numeric(summary_row.get("annualized_sum_return_pct"), errors="coerce") >= _STATIC_MIN_ANNUALIZED_SUM_RETURN_PCT
        and pd.to_numeric(summary_row.get("max_drawdown_pct"), errors="coerce") <= _STATIC_MAX_DRAWDOWN_PCT
        and pd.to_numeric(summary_row.get("positive_months_count"), errors="coerce") >= _STATIC_MIN_POSITIVE_MONTHS
    )


def _priority_score(summary_row: pd.Series) -> float:
    mean_return = float(pd.to_numeric(summary_row.get("mean_return_pct"), errors="coerce") or 0.0)
    win_rate = float(pd.to_numeric(summary_row.get("win_rate"), errors="coerce") or 0.0)
    trades_per_year = float(pd.to_numeric(summary_row.get("trades_per_year"), errors="coerce") or 0.0)
    annualized_sum = float(pd.to_numeric(summary_row.get("annualized_sum_return_pct"), errors="coerce") or 0.0)
    max_drawdown = float(pd.to_numeric(summary_row.get("max_drawdown_pct"), errors="coerce") or 1.0)
    positive_months = float(pd.to_numeric(summary_row.get("positive_months_count"), errors="coerce") or 0.0)
    return (
        mean_return * 4.0
        + win_rate * 1.5
        + min(trades_per_year, 100.0) * 0.01
        + annualized_sum * 0.5
        + positive_months * 0.05
        - max_drawdown * 1.25
    )


def _rank_combo_summary(combo_summary: pd.DataFrame) -> pd.DataFrame:
    if combo_summary.empty:
        return combo_summary.copy()
    ranked = combo_summary.copy()
    ranked["meets_goal"] = ranked.apply(_meets_static_goal, axis=1)
    ranked["priority_score"] = ranked.apply(_priority_score, axis=1)
    ranked = ranked.sort_values(
        [
            "meets_goal",
            "priority_score",
            "mean_return_pct",
            "annualized_sum_return_pct",
            "win_rate",
            "trades_per_year",
        ],
        ascending=[False, False, False, False, False, False],
        na_position="last",
    ).reset_index(drop=True)
    return ranked


def _index_to_variant_label(index: int) -> str:
    if index < 0:
        raise ValueError("Variant index must be non-negative")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    label = ""
    current = index
    while True:
        current, remainder = divmod(current, len(alphabet))
        label = alphabet[remainder] + label
        if current == 0:
            return label
        current -= 1


def _assign_combo_variants(great_combos: pd.DataFrame) -> pd.DataFrame:
    if great_combos.empty:
        return great_combos.copy()
    labeled = great_combos.copy().reset_index(drop=True)
    labeled["combo_variant"] = [_index_to_variant_label(idx) for idx in range(len(labeled))]
    labeled["combo_priority"] = labeled.index + 1
    return labeled


def _build_priority_selected_events(
    *,
    combo_events: pd.DataFrame,
    combo_catalog: pd.DataFrame,
) -> pd.DataFrame:
    if combo_events.empty or combo_catalog.empty:
        return pd.DataFrame()
    priority_map = {
        str(row["combo_id"]): {
            "combo_variant": str(row["combo_variant"]),
            "combo_priority": int(row["combo_priority"]),
            "priority_score": float(row["priority_score"]),
        }
        for row in combo_catalog.to_dict("records")
    }
    scoped = combo_events[combo_events["combo_id"].astype(str).isin(priority_map)].copy()
    if scoped.empty:
        return scoped
    scoped["combo_priority"] = scoped["combo_id"].astype(str).map(lambda combo_id: priority_map[combo_id]["combo_priority"])
    scoped["combo_variant"] = scoped["combo_id"].astype(str).map(lambda combo_id: priority_map[combo_id]["combo_variant"])
    scoped["combo_priority_score"] = scoped["combo_id"].astype(str).map(lambda combo_id: priority_map[combo_id]["priority_score"])
    grouped = scoped.groupby(["symbol", "timestamp_ms"], sort=True)
    rows: list[dict[str, object]] = []
    for (_, _), group in grouped:
        sorted_group = group.sort_values(["combo_priority", "combo_priority_score"], ascending=[True, False]).copy()
        chosen = sorted_group.iloc[0]
        matched_combo_ids = sorted_group["combo_id"].astype(str).tolist()
        matched_combo_variants = sorted_group["combo_variant"].astype(str).tolist()
        row = chosen.to_dict()
        row["matched_combo_count"] = int(len(sorted_group))
        row["matched_combo_ids"] = ",".join(matched_combo_ids)
        row["matched_combo_variants"] = ",".join(matched_combo_variants)
        rows.append(row)
    return pd.DataFrame(rows)


def _build_holdout_sanity_rows(
    *,
    combo_summary: pd.DataFrame,
    combo_events_by_id: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if combo_summary.empty:
        return pd.DataFrame()
    for combo in combo_summary.to_dict("records"):
        combo_id = str(combo["combo_id"])
        events = combo_events_by_id.get(combo_id)
        if events is None or events.empty:
            continue
        months = sorted(events["month_utc"].dropna().astype(str).unique().tolist())
        for split_id, train_months_count in _STATIC_HOLDOUT_SPLITS:
            if len(months) <= train_months_count:
                continue
            train_months = months[:train_months_count]
            test_months = months[train_months_count:]
            test_events = events[events["month_utc"].astype(str).isin(test_months)].copy()
            summary = _summarize_events(test_events)
            if summary is None:
                continue
            rows.append(
                {
                    "combo_id": combo_id,
                    "combo_variant": combo.get("combo_variant"),
                    "split_id": split_id,
                    "train_start_month_utc": train_months[0],
                    "train_end_month_utc": train_months[-1],
                    "test_start_month_utc": test_months[0],
                    "test_end_month_utc": test_months[-1],
                    **summary,
                }
            )
    return pd.DataFrame(rows)


def _empty_priority_summary() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "trades_count",
            "trade_days_count",
            "trades_per_year",
            "mean_return_pct",
            "median_return_pct",
            "win_rate",
            "profit_factor",
            "annual_sum_return_pct",
            "annualized_sum_return_pct",
            "mean_pos_trade_pct",
            "mean_neg_trade_pct",
            "positive_months_count",
            "non_positive_months_count",
            "all_active_months_positive",
            "best_month_return_pct",
            "worst_month_return_pct",
            "max_drawdown_pct",
            "matched_combo_variants_count",
        ]
    )


def _parse_component_ids(raw_value: object) -> tuple[str, ...]:
    if raw_value is None or raw_value is pd.NA:
        return tuple()
    parts = [part.strip() for part in str(raw_value).split(",")]
    return tuple(part for part in parts if part)


def _top_k_rank(top_k_value: object) -> int:
    try:
        parsed = int(top_k_value)
    except (TypeError, ValueError):
        return 99
    order = {1: 0, 2: 1, 99: 2}
    return order.get(parsed, 99)


def _build_priority_topn_summary(
    *,
    great_combos: pd.DataFrame,
    combo_events: pd.DataFrame,
) -> pd.DataFrame:
    if great_combos.empty or combo_events.empty:
        return pd.DataFrame()

    top_n_values = [value for value in _STATIC_PRIORITY_TOP_N_VALUES if value < len(great_combos)]
    top_n_values.append(len(great_combos))
    rows: list[dict[str, object]] = []
    for top_n in sorted(set(top_n_values)):
        scoped_catalog = great_combos.head(top_n).copy()
        priority_events = _build_priority_selected_events(combo_events=combo_events, combo_catalog=scoped_catalog)
        summary = _summarize_events(priority_events)
        if summary is None:
            continue
        rows.append(
            {
                "top_n_variants": int(top_n),
                "top_variant": str(scoped_catalog.iloc[0]["combo_variant"]),
                "last_variant": str(scoped_catalog.iloc[-1]["combo_variant"]),
                "selected_event_count": int(len(priority_events)),
                **summary,
            }
        )
    return pd.DataFrame(rows)


def _quantile_value(series: pd.Series, quantile: float) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return float(clean.quantile(quantile))


def _build_combo_robustness_summary(
    *,
    selected_combos: pd.DataFrame,
    all_unique_combos: pd.DataFrame,
) -> pd.DataFrame:
    if selected_combos.empty or all_unique_combos.empty:
        return pd.DataFrame()

    universe = all_unique_combos.copy().reset_index(drop=True)
    universe["component_tuple"] = universe.get("component_ids", pd.Series(dtype="object")).apply(_parse_component_ids)
    universe["component_set"] = universe["component_tuple"].apply(set)
    universe["top_k_rank"] = universe.get("top_k_per_timestamp", pd.Series(dtype="float64")).apply(_top_k_rank)

    rows: list[dict[str, object]] = []
    for combo in selected_combos.to_dict("records"):
        combo_id = str(combo.get("combo_id", ""))
        component_tuple = _parse_component_ids(combo.get("component_ids"))
        component_set = set(component_tuple)
        if not component_set:
            continue
        top_k_rank = _top_k_rank(combo.get("top_k_per_timestamp"))
        neighbors = universe[universe.get("combo_id", pd.Series(dtype="object")).astype(str) != combo_id].copy()
        neighbors["component_distance"] = neighbors["component_set"].apply(
            lambda other_set: len(component_set.symmetric_difference(other_set))
        )
        neighbors["same_components"] = neighbors["component_tuple"].apply(lambda other_tuple: other_tuple == component_tuple)
        neighbors["minor_component_change"] = (~neighbors["same_components"]) & (
            neighbors["component_distance"] <= _STATIC_LOCAL_COMPONENT_DISTANCE_MAX
        )
        neighbors["top_k_step_distance"] = (neighbors["top_k_rank"] - top_k_rank).abs()

        local_neighbors = neighbors[neighbors["same_components"] | neighbors["minor_component_change"]].copy()
        same_component_neighbors = local_neighbors[local_neighbors["same_components"]].copy()
        minor_change_neighbors = local_neighbors[local_neighbors["minor_component_change"]].copy()

        local_goal_rate = float(pd.to_numeric(local_neighbors.get("meets_goal"), errors="coerce").fillna(0.0).mean()) if not local_neighbors.empty else None
        minor_change_goal_rate = float(pd.to_numeric(minor_change_neighbors.get("meets_goal"), errors="coerce").fillna(0.0).mean()) if not minor_change_neighbors.empty else None
        local_mean_p25 = _quantile_value(local_neighbors.get("mean_return_pct", pd.Series(dtype="float64")), 0.25)
        local_dd_p75 = _quantile_value(local_neighbors.get("max_drawdown_pct", pd.Series(dtype="float64")), 0.75)
        local_ann_median = _quantile_value(local_neighbors.get("annualized_sum_return_pct", pd.Series(dtype="float64")), 0.50)

        robustness_label = "fragile"
        if (
            local_goal_rate is not None
            and local_mean_p25 is not None
            and local_dd_p75 is not None
            and local_ann_median is not None
        ):
            if local_goal_rate >= 0.60 and local_mean_p25 >= 0.02 and local_dd_p75 <= 0.30 and local_ann_median >= 1.0:
                robustness_label = "strong"
            elif local_goal_rate >= 0.35 and local_mean_p25 >= 0.015 and local_dd_p75 <= 0.35 and local_ann_median >= 0.75:
                robustness_label = "mixed"

        rows.append(
            {
                "combo_variant": combo.get("combo_variant"),
                "combo_priority": combo.get("combo_priority"),
                "combo_id": combo_id,
                "component_ids": combo.get("component_ids"),
                "top_k_per_timestamp": combo.get("top_k_per_timestamp"),
                "local_neighbors_count": int(len(local_neighbors)),
                "same_components_other_topk_count": int(len(same_component_neighbors)),
                "minor_component_change_count": int(len(minor_change_neighbors)),
                "local_goal_rate": local_goal_rate,
                "minor_change_goal_rate": minor_change_goal_rate,
                "local_mean_return_min_pct": _quantile_value(local_neighbors.get("mean_return_pct", pd.Series(dtype="float64")), 0.0),
                "local_mean_return_p25_pct": local_mean_p25,
                "local_mean_return_median_pct": _quantile_value(local_neighbors.get("mean_return_pct", pd.Series(dtype="float64")), 0.50),
                "local_mean_return_max_pct": _quantile_value(local_neighbors.get("mean_return_pct", pd.Series(dtype="float64")), 1.0),
                "local_win_rate_min": _quantile_value(local_neighbors.get("win_rate", pd.Series(dtype="float64")), 0.0),
                "local_win_rate_median": _quantile_value(local_neighbors.get("win_rate", pd.Series(dtype="float64")), 0.50),
                "local_win_rate_max": _quantile_value(local_neighbors.get("win_rate", pd.Series(dtype="float64")), 1.0),
                "local_annualized_min_pct": _quantile_value(local_neighbors.get("annualized_sum_return_pct", pd.Series(dtype="float64")), 0.0),
                "local_annualized_median_pct": local_ann_median,
                "local_annualized_max_pct": _quantile_value(local_neighbors.get("annualized_sum_return_pct", pd.Series(dtype="float64")), 1.0),
                "local_dd_min_pct": _quantile_value(local_neighbors.get("max_drawdown_pct", pd.Series(dtype="float64")), 0.0),
                "local_dd_p75_pct": local_dd_p75,
                "local_dd_max_pct": _quantile_value(local_neighbors.get("max_drawdown_pct", pd.Series(dtype="float64")), 1.0),
                "robustness_label": robustness_label,
            }
        )

    return pd.DataFrame(rows)


def build_hourly_asia_pump_static_combo_artifacts(
    *,
    base_events_path: Path | str,
    confirmed_events_path: Path | str,
    output_dir: Path | str,
    logger: logging.Logger | None = None,
) -> dict[str, Path]:
    active_logger = logger or logging.getLogger("hourly-asia-pump-static-combo")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    base_events = _prepare_base_events(Path(base_events_path))
    confirmed_events = _prepare_confirmed_events(Path(confirmed_events_path))
    candidate_frames = _build_static_candidate_frames(base_events=base_events, confirmed_events=confirmed_events)
    active_logger.info(
        "hourly-asia-pump-static-combo: candidates=%s base_events=%s confirmed_events=%s",
        len(candidate_frames),
        len(base_events),
        len(confirmed_events),
    )

    candidate_rows: list[dict[str, object]] = []
    for candidate_id, frame in candidate_frames.items():
        summary = _summarize_events(frame)
        if summary is None:
            continue
        summary["candidate_id"] = candidate_id
        candidate_rows.append(summary)
    candidate_summary = _sort_summary_frame(
        pd.DataFrame(candidate_rows),
        sort_columns=["mean_return_pct", "annualized_sum_return_pct", "win_rate"],
        ascending=[False, False, False],
    )

    combo_summary_rows: list[dict[str, object]] = []
    combo_event_frames: list[pd.DataFrame] = []
    combo_events_by_id: dict[str, pd.DataFrame] = {}
    candidate_ids = list(candidate_frames.keys())
    for combo_size in range(_STATIC_MIN_COMPONENTS, min(_STATIC_MAX_COMPONENTS, len(candidate_ids)) + 1):
        for component_ids in itertools.combinations(candidate_ids, combo_size):
            component_id_list = list(component_ids)
            for top_k_per_timestamp in _STATIC_TOP_K_VALUES:
                combo_events = _build_combo_events(
                    candidate_frames=candidate_frames,
                    component_ids=component_id_list,
                    top_k_per_timestamp=top_k_per_timestamp,
                )
                if combo_events.empty:
                    continue
                combo_id = str(combo_events.iloc[0]["combo_id"])
                combo_events_by_id[combo_id] = combo_events
                combo_event_frames.append(combo_events)
                summary = _summarize_events(combo_events)
                if summary is None:
                    continue
                summary["combo_id"] = combo_id
                summary["component_ids"] = ",".join(component_id_list)
                summary["component_count"] = len(component_id_list)
                summary["top_k_per_timestamp"] = top_k_per_timestamp
                summary["event_signature"] = _build_event_signature(combo_events)
                combo_summary_rows.append(summary)

    combo_events_all = pd.concat(combo_event_frames, ignore_index=True) if combo_event_frames else pd.DataFrame()
    combo_summary = _rank_combo_summary(pd.DataFrame(combo_summary_rows))

    if not combo_summary.empty and "event_signature" in combo_summary.columns:
        unique_combo_summary = combo_summary.drop_duplicates(subset=["event_signature"], keep="first").reset_index(drop=True)
    else:
        unique_combo_summary = combo_summary.copy()
    if not unique_combo_summary.empty and "meets_goal" in unique_combo_summary.columns:
        great_combos = _assign_combo_variants(unique_combo_summary[unique_combo_summary["meets_goal"].astype(bool)].copy())
    else:
        great_combos = unique_combo_summary.iloc[0:0].copy()
    priority_events = _build_priority_selected_events(combo_events=combo_events_all, combo_catalog=great_combos)
    if not priority_events.empty:
        priority_summary_payload = _summarize_events(priority_events)
        priority_summary = pd.DataFrame([priority_summary_payload]) if priority_summary_payload is not None else _empty_priority_summary()
    else:
        priority_summary = _empty_priority_summary()
    priority_summary["matched_combo_variants_count"] = int(great_combos["combo_variant"].nunique()) if not great_combos.empty and "combo_variant" in great_combos.columns else 0

    holdout_sanity = _build_holdout_sanity_rows(combo_summary=great_combos, combo_events_by_id=combo_events_by_id)
    priority_topn_summary = _build_priority_topn_summary(great_combos=great_combos, combo_events=combo_events_all)
    combo_robustness_summary = _build_combo_robustness_summary(
        selected_combos=great_combos,
        all_unique_combos=unique_combo_summary,
    )
    priority_holdout_sanity_rows: list[dict[str, object]] = []
    if not priority_events.empty:
        months = sorted(priority_events["month_utc"].dropna().astype(str).unique().tolist())
        for split_id, train_months_count in _STATIC_HOLDOUT_SPLITS:
            if len(months) <= train_months_count:
                continue
            train_months = months[:train_months_count]
            test_months = months[train_months_count:]
            test_events = priority_events[priority_events["month_utc"].astype(str).isin(test_months)].copy()
            summary = _summarize_events(test_events)
            if summary is None:
                continue
            priority_holdout_sanity_rows.append(
                {
                    "portfolio_id": "priority_selected_static_year",
                    "split_id": split_id,
                    "train_start_month_utc": train_months[0],
                    "train_end_month_utc": train_months[-1],
                    "test_start_month_utc": test_months[0],
                    "test_end_month_utc": test_months[-1],
                    **summary,
                }
            )
    priority_holdout_sanity = pd.DataFrame(priority_holdout_sanity_rows)

    candidate_summary_path = output_path / "candidate_component_summary.csv"
    candidate_summary.to_csv(candidate_summary_path, index=False)
    combo_summary_path = output_path / "combo_summary.csv"
    combo_summary.to_csv(combo_summary_path, index=False)
    unique_combo_summary_path = output_path / "combo_summary_unique.csv"
    unique_combo_summary.to_csv(unique_combo_summary_path, index=False)
    great_combo_catalog_path = output_path / "great_combo_catalog.csv"
    great_combos.to_csv(great_combo_catalog_path, index=False)
    combo_events_path = output_path / "combo_events.csv"
    combo_events_all.to_csv(combo_events_path, index=False)
    priority_events_path = output_path / "priority_selected_events.csv"
    priority_events.to_csv(priority_events_path, index=False)
    priority_summary_path = output_path / "priority_selected_summary.csv"
    priority_summary.to_csv(priority_summary_path, index=False)
    holdout_sanity_path = output_path / "great_combo_holdout_sanity.csv"
    holdout_sanity.to_csv(holdout_sanity_path, index=False)
    priority_holdout_sanity_path = output_path / "priority_selected_holdout_sanity.csv"
    priority_holdout_sanity.to_csv(priority_holdout_sanity_path, index=False)
    priority_topn_summary_path = output_path / "priority_topn_summary.csv"
    priority_topn_summary.to_csv(priority_topn_summary_path, index=False)
    combo_robustness_summary_path = output_path / "combo_robustness_summary.csv"
    combo_robustness_summary.to_csv(combo_robustness_summary_path, index=False)

    context = {
        "base_events_path": str(Path(base_events_path)),
        "confirmed_events_path": str(Path(confirmed_events_path)),
        "candidate_ids": list(candidate_frames.keys()),
        "great_combo_count": int(len(great_combos)),
    }
    context_path = output_path / "static_combo_context.json"
    context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")

    active_logger.info(
        "hourly-asia-pump-static-combo artifacts saved: output_dir=%s great_combos=%s priority_trades=%s",
        output_path,
        len(great_combos),
        len(priority_events),
    )
    return {
        "candidate_component_summary": candidate_summary_path,
        "combo_summary": combo_summary_path,
        "combo_summary_unique": unique_combo_summary_path,
        "great_combo_catalog": great_combo_catalog_path,
        "combo_events": combo_events_path,
        "priority_selected_events": priority_events_path,
        "priority_selected_summary": priority_summary_path,
        "great_combo_holdout_sanity": holdout_sanity_path,
        "priority_selected_holdout_sanity": priority_holdout_sanity_path,
        "priority_topn_summary": priority_topn_summary_path,
        "combo_robustness_summary": combo_robustness_summary_path,
        "static_combo_context": context_path,
    }


__all__ = [
    "build_hourly_asia_pump_static_combo_artifacts",
    "_assign_combo_variants",
    "_build_priority_selected_events",
    "_build_static_candidate_frames",
    "_index_to_variant_label",
]
