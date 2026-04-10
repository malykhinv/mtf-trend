from __future__ import annotations

from itertools import combinations
from pathlib import Path
import math

import pandas as pd

from strategy.hourly_asia_pump.category_adaptive_search import _calendar_months_from_ms, _score_candidate_on_window
from strategy.hourly_asia_pump.short_edge import _build_execution_models, _model_matches_row
from strategy.hourly_asia_pump.static_combo import _frame_to_markdown, _summarize_events
from strategy.hourly_asia_pump.unified_edge import _simulate_equity_risk_metrics


REPO_ROOT = Path(__file__).resolve().parents[2]
CURRENT_ROOT = REPO_ROOT / ".output" / "results" / "research" / "hourly_asia_pump_static_combo" / "latest_report"
PREV_ROOT = REPO_ROOT / ".output" / "results_prev_year_5m"
OUTPUT_DIR = PREV_ROOT / "deep_category_research"


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Не найден CSV: {path}")
    return pd.read_csv(path)


def _markdown(frame: pd.DataFrame, *, limit: int | None = None) -> str:
    return _frame_to_markdown(frame, columns=list(frame.columns), limit=limit)


def _harmonize_features(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    numeric_columns = [
        "timestamp_ms",
        "hour_utc",
        "trigger_open",
        "trigger_high",
        "trigger_low",
        "trigger_close",
        "trigger_return_pct",
        "trigger_range_pct",
        "range_atr",
        "body_atr",
        "volume_mult",
        "close_to_high_frac",
        "pre_base_range_pct_60m",
        "pre_base_drift_pct_60m",
        "pre_base_range_vs_trigger",
        "next_close_pos_in_bar",
        "next_close_from_high_frac",
        "next_extension_above_trigger_high_pct",
        "initial_risk_pct",
        "exit_return_pct",
        "bars_held_after_entry",
        "minutes_held_after_entry",
    ]
    enriched = frame.copy()
    for column in numeric_columns:
        if column in enriched.columns:
            enriched[column] = pd.to_numeric(enriched[column], errors="coerce")

    if "trigger_minute" not in enriched.columns and "timestamp_ms" in enriched.columns:
        enriched["trigger_minute"] = pd.to_datetime(enriched["timestamp_ms"], unit="ms", utc=True).dt.minute

    if {"trigger_open", "trigger_close", "trigger_high", "trigger_low"}.issubset(enriched.columns):
        candle_range = (enriched["trigger_high"] - enriched["trigger_low"]).replace(0.0, pd.NA)
        top = enriched[["trigger_open", "trigger_close"]].max(axis=1)
        bottom = enriched[["trigger_open", "trigger_close"]].min(axis=1)
        if "body_frac" not in enriched.columns:
            enriched["body_frac"] = (enriched["trigger_close"] - enriched["trigger_open"]).abs() / candle_range
        if "upper_wick_frac" not in enriched.columns:
            enriched["upper_wick_frac"] = (enriched["trigger_high"] - top) / candle_range
        if "lower_wick_frac" not in enriched.columns:
            enriched["lower_wick_frac"] = (bottom - enriched["trigger_low"]) / candle_range

    if "next_return_pct" not in enriched.columns and "next_bar_return_pct" in enriched.columns:
        enriched["next_return_pct"] = pd.to_numeric(enriched["next_bar_return_pct"], errors="coerce")
    if "next_pullback_frac" not in enriched.columns and "next_bar_pullback_frac" in enriched.columns:
        enriched["next_pullback_frac"] = pd.to_numeric(enriched["next_bar_pullback_frac"], errors="coerce")
    return enriched


def _normalize_events(
    frame: pd.DataFrame,
    *,
    candidate_key: str,
    candidate_title: str,
    session_id: str,
    side: str,
    dataset: str,
) -> pd.DataFrame:
    scoped = _harmonize_features(frame)
    if "trade_triggered" in scoped.columns:
        scoped = scoped[scoped["trade_triggered"].fillna(False).astype(bool)].copy()
    if scoped.empty:
        return scoped
    if "month_utc" not in scoped.columns:
        scoped["month_utc"] = pd.to_datetime(scoped["timestamp_ms"], unit="ms", utc=True).dt.strftime("%Y-%m")
    scoped["candidate_key"] = candidate_key
    scoped["candidate_title"] = candidate_title
    scoped["session_id"] = session_id
    scoped["side"] = side
    scoped["dataset"] = dataset
    scoped["signal_key"] = (
        scoped["session_id"].astype(str)
        + "|"
        + scoped["side"].astype(str)
        + "|"
        + scoped["symbol"].astype(str)
        + "|"
        + pd.to_numeric(scoped["timestamp_ms"], errors="coerce").astype("Int64").astype(str)
    )
    return scoped


def _minute_filter_to_minutes(filter_id: str) -> set[int]:
    standard_filters = {
        "top_of_hour_only": {0},
        "quarter_hours": {0, 15, 30, 45},
        "half_hours": {0, 30},
        "non_quarter_hours": {5, 10, 20, 25, 35, 40, 50, 55},
        "all_5m_minutes": {0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
    }
    if filter_id.startswith("minute_"):
        return {int(filter_id.replace("minute_", ""))}
    return standard_filters[filter_id]


def _build_expanded_pool() -> tuple[pd.DataFrame, pd.DataFrame]:
    frames: list[pd.DataFrame] = []

    asia_best = _load_csv(CURRENT_ROOT / "unified_edge_search" / "unified_edge_best_events.csv")
    frames.append(
        _normalize_events(
            asia_best,
            candidate_key="asia_C02_full",
            candidate_title="Азия current C02 full",
            session_id="asia",
            side="long",
            dataset="current",
        )
    )
    for component_id, scoped in asia_best.groupby("component_id", sort=True):
        frames.append(
            _normalize_events(
                scoped.copy(),
                candidate_key=f"asia_component_{component_id}",
                candidate_title=f"Азия current {component_id}",
                session_id="asia",
                side="long",
                dataset="current",
            )
        )

    asia_old_model_events = _harmonize_features(_load_csv(PREV_ROOT / "research" / "hourly_asia_pump_prev_year_5m" / "trade_model_events.csv"))
    feature_columns = [
        column
        for column in [
            "symbol",
            "timestamp_ms",
            "timestamp_utc",
            "month_utc",
            "hour_utc",
            "trigger_open",
            "trigger_high",
            "trigger_low",
            "trigger_close",
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
            "next_return_pct",
            "next_pullback_frac",
            "next_close_from_high_frac",
            "next_close_pos_in_bar",
            "next_extension_above_trigger_high_pct",
            "initial_risk_pct",
            "bars_held_after_entry",
            "minutes_held_after_entry",
            "trigger_minute",
        ]
        if column in asia_old_model_events.columns
    ]
    asia_old_feature_source = (
        asia_old_model_events[asia_old_model_events["trade_triggered"].fillna(False).astype(bool)]
        .sort_values(["symbol", "timestamp_ms"])
        .drop_duplicates(["symbol", "timestamp_ms"])[feature_columns]
    )
    asia_old_portfolios = _load_csv(PREV_ROOT / "research" / "hourly_asia_pump_prev_year_5m" / "trade_portfolio_events.csv")
    asia_old_portfolios = asia_old_portfolios.merge(asia_old_feature_source, on=["symbol", "timestamp_ms"], how="left")
    for portfolio_id, scoped in asia_old_portfolios.groupby("trade_portfolio_id", sort=True):
        frames.append(
            _normalize_events(
                scoped.copy(),
                candidate_key=f"asia_portfolio_{portfolio_id}",
                candidate_title=f"Азия old portfolio {portfolio_id}",
                session_id="asia",
                side="long",
                dataset="old",
            )
        )

    asia_old_model_summary = _load_csv(PREV_ROOT / "research" / "hourly_asia_pump_prev_year_5m" / "trade_model_summary.csv")
    selected_old_models = asia_old_model_summary[
        (pd.to_numeric(asia_old_model_summary["trades_count"], errors="coerce") >= 5)
        & (pd.to_numeric(asia_old_model_summary["mean_return_pct"], errors="coerce") > 0.0)
        & (pd.to_numeric(asia_old_model_summary["win_rate"], errors="coerce") >= 0.45)
    ].copy()
    for trade_model_id in sorted(selected_old_models["trade_model_id"].astype(str).unique()):
        scoped = asia_old_model_events[
            (asia_old_model_events["trade_model_id"].astype(str) == trade_model_id)
            & (asia_old_model_events["trade_triggered"].fillna(False).astype(bool))
        ].copy()
        if scoped.empty:
            continue
        frames.append(
            _normalize_events(
                scoped,
                candidate_key=f"asia_model_{trade_model_id}",
                candidate_title=f"Азия old model {trade_model_id}",
                session_id="asia",
                side="long",
                dataset="old",
            )
        )

    model_map = {model.label: model for model in _build_execution_models()}
    for session_id, portfolio_name in (("america", "C07"), ("europe", "C08")):
        current_portfolio = _load_csv(CURRENT_ROOT / f"{session_id}_short_portfolio_search" / f"{session_id}_short_portfolio_best_events.csv")
        frames.append(
            _normalize_events(
                current_portfolio,
                candidate_key=f"{session_id}_full",
                candidate_title=f"{session_id} current full",
                session_id=session_id,
                side="short",
                dataset="current",
            )
        )
        components = _load_csv(CURRENT_ROOT / f"{session_id}_short_portfolio_search" / f"{session_id}_short_portfolio_components.csv")
        geometry = _harmonize_features(_load_csv(CURRENT_ROOT / f"{session_id}_short_session_search" / f"{session_id}_short_geometry_events.csv"))
        geometry = geometry[geometry["trade_triggered"].fillna(False).astype(bool)].copy()
        for row in components.to_dict("records"):
            model = model_map.get(str(row["model_label"]))
            if model is None:
                continue
            scoped = geometry[geometry["trigger_minute"].isin(_minute_filter_to_minutes(str(row["minute_filter_id"])))].copy()
            if scoped.empty:
                continue
            scoped = scoped[scoped.apply(lambda event: _model_matches_row(model, event), axis=1)].copy()
            if scoped.empty:
                continue
            scoped["component_id"] = row["component_id"]
            frames.append(
                _normalize_events(
                    scoped,
                    candidate_key=f"{session_id}_{row['component_id']}",
                    candidate_title=f"{session_id} {row['component_id']}",
                    session_id=session_id,
                    side="short",
                    dataset="current",
                )
            )

        old_portfolio = _load_csv(PREV_ROOT / "fixed_candidate_validation_after_1m" / f"{session_id}_short_{portfolio_name}_prev_year_events.csv")
        frames.append(
            _normalize_events(
                old_portfolio,
                candidate_key=f"{session_id}_full",
                candidate_title=f"{session_id} old full",
                session_id=session_id,
                side="short",
                dataset="old",
            )
        )
        old_components = _load_csv(PREV_ROOT / "fixed_candidate_validation_after_1m" / f"{session_id}_short_{portfolio_name}_prev_year_component_events.csv")
        for component_id, scoped in old_components.groupby("component_id", sort=True):
            frames.append(
                _normalize_events(
                    scoped.copy(),
                    candidate_key=f"{session_id}_{component_id}",
                    candidate_title=f"{session_id} {component_id}",
                    session_id=session_id,
                    side="short",
                    dataset="old",
                )
            )

    events = pd.concat([frame for frame in frames if not frame.empty], ignore_index=True)
    catalog = (
        events.groupby(["candidate_key", "candidate_title", "session_id", "side", "dataset"], as_index=False)
        .agg(
            trades_count=("signal_key", "size"),
            months_count=("month_utc", "nunique"),
            first_month_utc=("month_utc", "min"),
            last_month_utc=("month_utc", "max"),
        )
        .sort_values(["session_id", "candidate_key", "dataset"])
        .reset_index(drop=True)
    )
    return events, catalog


def _attach_deep_categories(events: pd.DataFrame) -> pd.DataFrame:
    enriched = events.copy()
    first_seen = _load_csv(PREV_ROOT / "cross_period_meta_analysis" / "symbol_first_seen_union.csv")
    first_seen["first_seen_ms"] = pd.to_numeric(first_seen["first_seen_ms"], errors="coerce")
    enriched = enriched.merge(first_seen[["symbol", "first_seen_ms"]], on="symbol", how="left")
    enriched["age_days_at_event"] = (
        pd.to_numeric(enriched["timestamp_ms"], errors="coerce") - pd.to_numeric(enriched["first_seen_ms"], errors="coerce")
    ) / 86_400_000.0

    enriched["age_bucket"] = "age_366_plus"
    enriched.loc[enriched["age_days_at_event"] <= 30.0, "age_bucket"] = "age_00_30"
    enriched.loc[(enriched["age_days_at_event"] > 30.0) & (enriched["age_days_at_event"] <= 90.0), "age_bucket"] = "age_31_90"
    enriched.loc[(enriched["age_days_at_event"] > 90.0) & (enriched["age_days_at_event"] <= 180.0), "age_bucket"] = "age_91_180"
    enriched.loc[(enriched["age_days_at_event"] > 180.0) & (enriched["age_days_at_event"] <= 365.0), "age_bucket"] = "age_181_365"

    enriched["drift_bucket"] = "drift_hot"
    enriched.loc[enriched["pre_base_drift_pct_60m"] <= 0.015, "drift_bucket"] = "drift_cold"
    enriched.loc[(enriched["pre_base_drift_pct_60m"] > 0.015) & (enriched["pre_base_drift_pct_60m"] <= 0.04), "drift_bucket"] = "drift_warm"

    enriched["range_bucket"] = "range_wide"
    enriched.loc[enriched["pre_base_range_pct_60m"] <= 0.03, "range_bucket"] = "range_tight"
    enriched.loc[(enriched["pre_base_range_pct_60m"] > 0.03) & (enriched["pre_base_range_pct_60m"] <= 0.07), "range_bucket"] = "range_mid"

    enriched["anomaly_bucket"] = "anomaly_extreme"
    enriched.loc[enriched["trigger_return_pct"] <= 0.065, "anomaly_bucket"] = "anomaly_mid"
    enriched.loc[(enriched["trigger_return_pct"] > 0.065) & (enriched["trigger_return_pct"] <= 0.10), "anomaly_bucket"] = "anomaly_hot"

    enriched["volume_bucket"] = "volume_extreme"
    enriched.loc[enriched["volume_mult"] <= 10.0, "volume_bucket"] = "volume_normal"
    enriched.loc[(enriched["volume_mult"] > 10.0) & (enriched["volume_mult"] <= 30.0), "volume_bucket"] = "volume_hot"

    enriched["close_strength_bucket"] = "close_loose"
    enriched.loc[enriched["close_to_high_frac"] <= 0.08, "close_strength_bucket"] = "close_drive"
    enriched.loc[(enriched["close_to_high_frac"] > 0.08) & (enriched["close_to_high_frac"] <= 0.18), "close_strength_bucket"] = "close_good"

    enriched["wick_shape_bucket"] = "wick_mixed"
    enriched.loc[(enriched["upper_wick_frac"] >= 0.15) & (enriched["body_frac"] <= 0.75), "wick_shape_bucket"] = "wick_blowoff"
    enriched.loc[(enriched["lower_wick_frac"] >= 0.15) & (enriched["upper_wick_frac"] < 0.15), "wick_shape_bucket"] = "wick_shakeout"
    enriched.loc[(enriched["body_frac"] >= 0.80) & (enriched["close_strength_bucket"] == "close_drive"), "wick_shape_bucket"] = "wick_clean_drive"

    enriched["pullback_bucket"] = "pullback_deep"
    enriched.loc[enriched["next_pullback_frac"] <= 0.20, "pullback_bucket"] = "pullback_shallow"
    enriched.loc[(enriched["next_pullback_frac"] > 0.20) & (enriched["next_pullback_frac"] <= 0.40), "pullback_bucket"] = "pullback_mid"

    enriched["next_close_bucket"] = "next_close_weak"
    enriched.loc[enriched["next_close_pos_in_bar"] >= 0.70, "next_close_bucket"] = "next_close_strong"
    enriched.loc[(enriched["next_close_pos_in_bar"] >= 0.45) & (enriched["next_close_pos_in_bar"] < 0.70), "next_close_bucket"] = "next_close_mid"

    enriched["next_extension_bucket"] = "next_ext_none"
    enriched.loc[enriched["next_extension_above_trigger_high_pct"] >= 0.02, "next_extension_bucket"] = "next_ext_hot"
    enriched.loc[
        (enriched["next_extension_above_trigger_high_pct"] >= 0.005)
        & (enriched["next_extension_above_trigger_high_pct"] < 0.02),
        "next_extension_bucket",
    ] = "next_ext_yes"

    enriched["next_ret_bucket"] = "next_ret_red"
    enriched.loc[enriched["next_return_pct"] >= 0.005, "next_ret_bucket"] = "next_ret_green"
    enriched.loc[(enriched["next_return_pct"] > -0.005) & (enriched["next_return_pct"] < 0.005), "next_ret_bucket"] = "next_ret_flat"

    enriched["context_archetype"] = "context_active"
    enriched.loc[(enriched["drift_bucket"] == "drift_cold") & (enriched["range_bucket"] == "range_tight"), "context_archetype"] = "context_coiled"
    enriched.loc[(enriched["drift_bucket"] == "drift_warm") & (enriched["range_bucket"] == "range_mid"), "context_archetype"] = "context_warming"
    enriched.loc[(enriched["drift_bucket"] == "drift_hot") | (enriched["range_bucket"] == "range_wide"), "context_archetype"] = "context_overheated"

    enriched["impulse_archetype"] = "impulse_balanced"
    enriched.loc[(enriched["close_strength_bucket"] == "close_drive") & (enriched["wick_shape_bucket"] == "wick_clean_drive"), "impulse_archetype"] = "impulse_clean_drive"
    enriched.loc[
        (enriched["wick_shape_bucket"] == "wick_blowoff")
        | (enriched["volume_bucket"] == "volume_extreme")
        | (enriched["anomaly_bucket"] == "anomaly_extreme"),
        "impulse_archetype",
    ] = "impulse_mania"
    enriched.loc[(enriched["anomaly_bucket"] == "anomaly_hot") & (enriched["close_strength_bucket"].isin(["close_drive", "close_good"])), "impulse_archetype"] = "impulse_hot_clean"
    enriched.loc[enriched["wick_shape_bucket"] == "wick_shakeout", "impulse_archetype"] = "impulse_shakeout"

    enriched["confirmation_archetype"] = "confirm_mixed"
    enriched.loc[
        (enriched["pullback_bucket"] == "pullback_shallow")
        & (enriched["next_close_bucket"] == "next_close_strong")
        & (enriched["next_extension_bucket"].isin(["next_ext_yes", "next_ext_hot"]))
        & (enriched["next_ret_bucket"] != "next_ret_red"),
        "confirmation_archetype",
    ] = "confirm_clean_hold"
    enriched.loc[
        (enriched["pullback_bucket"].isin(["pullback_shallow", "pullback_mid"]))
        & (enriched["next_close_bucket"].isin(["next_close_mid", "next_close_strong"]))
        & (enriched["next_extension_bucket"] != "next_ext_none"),
        "confirmation_archetype",
    ] = "confirm_holding"
    enriched.loc[
        (enriched["pullback_bucket"] == "pullback_deep")
        & ((enriched["next_close_bucket"] == "next_close_weak") | (enriched["next_ret_bucket"] == "next_ret_red")),
        "confirmation_archetype",
    ] = "confirm_failed"
    enriched.loc[
        (enriched["next_extension_bucket"] == "next_ext_hot") & (enriched["next_close_from_high_frac"] >= 0.25),
        "confirmation_archetype",
    ] = "confirm_exhaustion_pop"

    enriched["canonical_pump_type"] = (
        enriched["context_archetype"].astype(str)
        + "|"
        + enriched["impulse_archetype"].astype(str)
        + "|"
        + enriched["confirmation_archetype"].astype(str)
    )
    return enriched


def _build_stable_groups(events: pd.DataFrame) -> pd.DataFrame:
    features = [
        "age_bucket",
        "context_archetype",
        "impulse_archetype",
        "confirmation_archetype",
        "canonical_pump_type",
        "drift_bucket",
        "range_bucket",
        "anomaly_bucket",
        "volume_bucket",
    ]
    rows: list[dict[str, object]] = []
    for candidate_key, candidate_group in events.groupby("candidate_key", sort=True):
        for feature_count in (1, 2, 3):
            for grouping in combinations(features, feature_count):
                grouped = candidate_group.groupby(list(grouping), dropna=False, sort=True)
                for values, scoped in grouped:
                    if not isinstance(values, tuple):
                        values = (values,)
                    current = scoped[scoped["dataset"] == "current"].copy()
                    old = scoped[scoped["dataset"] == "old"].copy()
                    if len(current) < 4 or len(old) < 3:
                        continue
                    current_mean = float(pd.to_numeric(current["exit_return_pct"], errors="coerce").mean())
                    old_mean = float(pd.to_numeric(old["exit_return_pct"], errors="coerce").mean())
                    current_wr = float((pd.to_numeric(current["exit_return_pct"], errors="coerce") > 0.0).mean())
                    old_wr = float((pd.to_numeric(old["exit_return_pct"], errors="coerce") > 0.0).mean())
                    if current_mean <= 0.0 or old_mean <= 0.0:
                        continue
                    if current_wr < 0.50 or old_wr < 0.50:
                        continue
                    min_trades = min(len(current), len(old))
                    stability_score = (
                        min(current_mean, old_mean) * 140.0
                        + min(current_wr, old_wr) * 45.0
                        + math.log1p(min_trades) * 4.0
                        - abs(current_mean - old_mean) * 90.0
                        - abs(current_wr - old_wr) * 12.0
                    )
                    row = {
                        "candidate_key": candidate_key,
                        "grouping": "|".join(grouping),
                        "stable_group_id": f"{candidate_key}::{'|'.join(grouping)}::{'|'.join(str(value) for value in values)}",
                        "stability_score": stability_score,
                        "current_trades": int(len(current)),
                        "old_trades": int(len(old)),
                        "current_mean_return_pct": current_mean,
                        "old_mean_return_pct": old_mean,
                        "current_win_rate": current_wr,
                        "old_win_rate": old_wr,
                    }
                    for feature_name, value in zip(grouping, values, strict=False):
                        row[feature_name] = value
                    rows.append(row)
    return pd.DataFrame(rows).sort_values(
        ["candidate_key", "stability_score", "current_trades", "old_trades"],
        ascending=[True, False, False, False],
    ).reset_index(drop=True)


def _attach_best_group(events: pd.DataFrame, stable_groups: pd.DataFrame) -> pd.DataFrame:
    grouped = {
        candidate_key: group.sort_values("stability_score", ascending=False).to_dict("records")
        for candidate_key, group in stable_groups.groupby("candidate_key", sort=True)
    }
    rows: list[dict[str, object]] = []
    for _, event in events.iterrows():
        best_match: dict[str, object] | None = None
        for group in grouped.get(str(event["candidate_key"]), []):
            matches = True
            for feature in str(group["grouping"]).split("|"):
                if str(event.get(feature)) != str(group.get(feature)):
                    matches = False
                    break
            if matches:
                best_match = group
                break
        row = event.to_dict()
        row["matched_group_id"] = None if best_match is None else best_match["stable_group_id"]
        row["matched_grouping"] = None if best_match is None else best_match["grouping"]
        row["matched_group_score"] = None if best_match is None else best_match["stability_score"]
        rows.append(row)
    return pd.DataFrame(rows)


def _run_session_adaptive(events: pd.DataFrame, *, lookback_months: int, calendar_months: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    picks: list[dict[str, object]] = []
    selected_frames: list[pd.DataFrame] = []
    for month_index, month in enumerate(calendar_months):
        if month_index < lookback_months:
            continue
        train_months = calendar_months[month_index - lookback_months : month_index]
        month_events = events[events["month_utc"].astype(str) == str(month)].copy()
        if month_events.empty:
            continue
        for session_id, session_events in month_events.groupby("session_id", sort=True):
            candidate_scores: list[dict[str, object]] = []
            history_session = events[events["session_id"].astype(str) == str(session_id)].copy()
            for candidate_key, candidate_events in history_session.groupby("candidate_key", sort=True):
                trailing = _score_candidate_on_window(candidate_events, train_months)
                if trailing is None:
                    continue
                if float(trailing.get("mean_return_pct", 0.0) or 0.0) <= 0.0:
                    continue
                if float(trailing.get("annualized_unit_pnl_pct", 0.0) or 0.0) <= 0.0:
                    continue
                candidate_scores.append({"candidate_key": candidate_key, **trailing})
            if not candidate_scores:
                continue
            scores_frame = pd.DataFrame(candidate_scores).sort_values(
                ["selection_score", "mean_return_pct", "win_rate", "trades_count"],
                ascending=[False, False, False, False],
            )
            chosen = scores_frame.iloc[0].to_dict()
            chosen_events = session_events[session_events["candidate_key"].astype(str) == str(chosen["candidate_key"])].copy()
            if chosen_events.empty:
                continue
            chosen_events["selection_score"] = chosen["selection_score"]
            chosen_events["lookback_months"] = lookback_months
            selected_frames.append(chosen_events)
            picks.append(
                {
                    "month_utc": month,
                    "lookback_months": lookback_months,
                    "session_id": session_id,
                    "candidate_key": chosen["candidate_key"],
                    "selection_score": chosen["selection_score"],
                }
            )
    selected_events = pd.concat(selected_frames, ignore_index=True) if selected_frames else pd.DataFrame()
    picks_frame = pd.DataFrame(picks)
    if selected_events.empty:
        return picks_frame, selected_events, pd.DataFrame(), pd.DataFrame()
    selected_events = selected_events.sort_values(["signal_key", "selection_score", "exit_return_pct"], ascending=[True, False, False])
    selected_events = selected_events.groupby("signal_key", as_index=False, sort=False).head(1).copy()
    summary = _summarize_events(selected_events, calendar_months=calendar_months) or {}
    equity_5, monthly_5 = _simulate_equity_risk_metrics(selected_events, calendar_months=calendar_months, risk_fraction=0.05)
    equity_9, monthly_9 = _simulate_equity_risk_metrics(selected_events, calendar_months=calendar_months, risk_fraction=0.09)
    summary_frame = pd.DataFrame(
        [
            {
                "lookback_months": lookback_months,
                **summary,
                "selected_trades_count": int(len(selected_events)),
                "selected_candidates_count": int(selected_events["candidate_key"].nunique()),
                "selected_sessions_count": int(selected_events["session_id"].nunique()),
                "equity_5_annualized_return_pct": equity_5.get("equity_annualized_return_pct"),
                "equity_5_max_drawdown_pct": equity_5.get("equity_max_drawdown_pct"),
                "equity_5_positive_months_count": equity_5.get("equity_positive_months_count"),
                "equity_5_stable_positive_months_count": equity_5.get("equity_stable_positive_months_count"),
                "equity_9_annualized_return_pct": equity_9.get("equity_annualized_return_pct"),
                "equity_9_max_drawdown_pct": equity_9.get("equity_max_drawdown_pct"),
                "equity_9_positive_months_count": equity_9.get("equity_positive_months_count"),
                "equity_9_stable_positive_months_count": equity_9.get("equity_stable_positive_months_count"),
            }
        ]
    )
    monthly_5["lookback_months"] = lookback_months
    monthly_9["lookback_months"] = lookback_months
    monthly = monthly_5.merge(monthly_9, on=["month_utc", "lookback_months"], suffixes=("_5", "_9"))
    return picks_frame, selected_events, summary_frame, monthly


def _run_category_adaptive(events: pd.DataFrame, *, lookback_months: int, calendar_months: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    eligible = events[events["matched_group_id"].notna()].copy()
    if eligible.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    picks: list[dict[str, object]] = []
    selected_frames: list[pd.DataFrame] = []
    for month_index, month in enumerate(calendar_months):
        if month_index < lookback_months:
            continue
        train_months = calendar_months[month_index - lookback_months : month_index]
        month_events = eligible[eligible["month_utc"].astype(str) == str(month)].copy()
        if month_events.empty:
            continue
        chosen_by_group: dict[str, dict[str, object]] = {}
        for group_id, group_events in eligible.groupby("matched_group_id", sort=True):
            candidate_scores: list[dict[str, object]] = []
            for candidate_key, candidate_events in group_events.groupby("candidate_key", sort=True):
                trailing = _score_candidate_on_window(candidate_events, train_months)
                if trailing is None:
                    continue
                if float(trailing.get("mean_return_pct", 0.0) or 0.0) <= 0.0:
                    continue
                if float(trailing.get("annualized_unit_pnl_pct", 0.0) or 0.0) <= 0.0:
                    continue
                candidate_scores.append({"candidate_key": candidate_key, **trailing})
            if not candidate_scores:
                continue
            scores_frame = pd.DataFrame(candidate_scores).sort_values(
                ["selection_score", "mean_return_pct", "win_rate", "trades_count"],
                ascending=[False, False, False, False],
            )
            chosen_by_group[str(group_id)] = scores_frame.iloc[0].to_dict()
        if not chosen_by_group:
            continue
        month_rows: list[pd.Series] = []
        for _, event in month_events.iterrows():
            chosen = chosen_by_group.get(str(event["matched_group_id"]))
            if chosen is None or str(chosen["candidate_key"]) != str(event["candidate_key"]):
                continue
            row = event.copy()
            row["selection_score"] = chosen["selection_score"]
            row["lookback_months"] = lookback_months
            month_rows.append(row)
            picks.append(
                {
                    "month_utc": month,
                    "lookback_months": lookback_months,
                    "matched_group_id": event["matched_group_id"],
                    "candidate_key": event["candidate_key"],
                    "selection_score": chosen["selection_score"],
                }
            )
        if month_rows:
            month_frame = pd.DataFrame(month_rows)
            month_frame = month_frame.sort_values(
                ["signal_key", "selection_score", "matched_group_score", "exit_return_pct"],
                ascending=[True, False, False, False],
            )
            month_frame = month_frame.groupby("signal_key", as_index=False, sort=False).head(1).copy()
            selected_frames.append(month_frame)
    selected_events = pd.concat(selected_frames, ignore_index=True) if selected_frames else pd.DataFrame()
    picks_frame = pd.DataFrame(picks)
    if selected_events.empty:
        return picks_frame, selected_events, pd.DataFrame(), pd.DataFrame()
    summary = _summarize_events(selected_events, calendar_months=calendar_months) or {}
    equity_5, monthly_5 = _simulate_equity_risk_metrics(selected_events, calendar_months=calendar_months, risk_fraction=0.05)
    equity_9, monthly_9 = _simulate_equity_risk_metrics(selected_events, calendar_months=calendar_months, risk_fraction=0.09)
    summary_frame = pd.DataFrame(
        [
            {
                "lookback_months": lookback_months,
                **summary,
                "selected_trades_count": int(len(selected_events)),
                "selected_candidates_count": int(selected_events["candidate_key"].nunique()),
                "selected_groups_count": int(selected_events["matched_group_id"].nunique()),
                "equity_5_annualized_return_pct": equity_5.get("equity_annualized_return_pct"),
                "equity_5_max_drawdown_pct": equity_5.get("equity_max_drawdown_pct"),
                "equity_5_positive_months_count": equity_5.get("equity_positive_months_count"),
                "equity_5_stable_positive_months_count": equity_5.get("equity_stable_positive_months_count"),
                "equity_9_annualized_return_pct": equity_9.get("equity_annualized_return_pct"),
                "equity_9_max_drawdown_pct": equity_9.get("equity_max_drawdown_pct"),
                "equity_9_positive_months_count": equity_9.get("equity_positive_months_count"),
                "equity_9_stable_positive_months_count": equity_9.get("equity_stable_positive_months_count"),
            }
        ]
    )
    monthly_5["lookback_months"] = lookback_months
    monthly_9["lookback_months"] = lookback_months
    monthly = monthly_5.merge(monthly_9, on=["month_utc", "lookback_months"], suffixes=("_5", "_9"))
    return picks_frame, selected_events, summary_frame, monthly


def run_deep_category_research() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    events, catalog = _build_expanded_pool()
    events.to_csv(OUTPUT_DIR / "deep_category_events.csv", index=False)
    catalog.to_csv(OUTPUT_DIR / "deep_category_candidate_catalog.csv", index=False)

    enriched = _attach_deep_categories(events)
    enriched.to_csv(OUTPUT_DIR / "deep_category_events_with_features.csv", index=False)
    stable_groups = _build_stable_groups(enriched)
    stable_groups.to_csv(OUTPUT_DIR / "deep_category_stable_groups.csv", index=False)
    attached = _attach_best_group(enriched, stable_groups)
    attached.to_csv(OUTPUT_DIR / "deep_category_events_with_groups.csv", index=False)

    calendar_months = _calendar_months_from_ms(
        int(pd.to_numeric(events["timestamp_ms"], errors="coerce").min()),
        int(pd.to_numeric(events["timestamp_ms"], errors="coerce").max()),
    )

    session_summary_frames: list[pd.DataFrame] = []
    session_pick_frames: list[pd.DataFrame] = []
    for lookback in (3, 6, 9, 12):
        picks, selected, summary_frame, monthly = _run_session_adaptive(events, lookback_months=lookback, calendar_months=calendar_months)
        picks.to_csv(OUTPUT_DIR / f"expanded_session_adaptive_{lookback}m_picks.csv", index=False)
        selected.to_csv(OUTPUT_DIR / f"expanded_session_adaptive_{lookback}m_events.csv", index=False)
        summary_frame.to_csv(OUTPUT_DIR / f"expanded_session_adaptive_{lookback}m_summary.csv", index=False)
        monthly.to_csv(OUTPUT_DIR / f"expanded_session_adaptive_{lookback}m_monthly.csv", index=False)
        session_summary_frames.append(summary_frame)
        session_pick_frames.append(picks)
    expanded_session_summary = pd.concat(session_summary_frames, ignore_index=True)
    expanded_session_summary.to_csv(OUTPUT_DIR / "expanded_session_adaptive_summary.csv", index=False)

    session_pick_counts = (
        pd.concat(session_pick_frames, ignore_index=True)
        .groupby(["lookback_months", "session_id", "candidate_key"], as_index=False)
        .size()
        .rename(columns={"size": "months_selected"})
        .sort_values(["lookback_months", "session_id", "months_selected"], ascending=[True, True, False])
    )
    session_pick_counts.to_csv(OUTPUT_DIR / "expanded_session_pick_counts.csv", index=False)

    category_summary_frames: list[pd.DataFrame] = []
    for lookback in (3, 6, 9, 12):
        picks, selected, summary_frame, monthly = _run_category_adaptive(attached, lookback_months=lookback, calendar_months=calendar_months)
        picks.to_csv(OUTPUT_DIR / f"deep_category_adaptive_{lookback}m_picks.csv", index=False)
        selected.to_csv(OUTPUT_DIR / f"deep_category_adaptive_{lookback}m_events.csv", index=False)
        summary_frame.to_csv(OUTPUT_DIR / f"deep_category_adaptive_{lookback}m_summary.csv", index=False)
        monthly.to_csv(OUTPUT_DIR / f"deep_category_adaptive_{lookback}m_monthly.csv", index=False)
        category_summary_frames.append(summary_frame)
    deep_category_summary = pd.concat(category_summary_frames, ignore_index=True)
    deep_category_summary.to_csv(OUTPUT_DIR / "deep_category_adaptive_summary.csv", index=False)

    previous_session = _load_csv(PREV_ROOT / "cross_period_meta_analysis" / "adaptive_selection_tradelevel_summary.csv")
    previous_category = _load_csv(PREV_ROOT / "category_adaptive_pump_type_search" / "category_adaptive_summary.csv")
    compare = pd.concat(
        [
            previous_session.assign(source="previous_session"),
            previous_category.assign(source="previous_category"),
            expanded_session_summary.assign(source="expanded_session"),
            deep_category_summary.assign(source="deep_category"),
        ],
        ignore_index=True,
    )
    compare.to_csv(OUTPUT_DIR / "deep_category_vs_baselines.csv", index=False)

    report_lines = [
        "# Глубокая категоризация пампов",
        "",
        "## Что изменено",
        "",
        "- Категории теперь строятся не из грубых tercile-бакетов, а из двух слоёв: примитивных market-state признаков и семантических archetype-признаков.",
        "- Примитивные признаки: возраст монеты, разогрев до аномалии, ширина базы, размер аномалии, объём, качество закрытия, форма теней, глубина pullback, качество следующей свечи.",
        "- Семантические archetypes: `context_archetype`, `impulse_archetype`, `confirmation_archetype` и их канонический тип `canonical_pump_type`.",
        "- Устойчивые группы ищутся только если они положительны и на current, и на old периоде.",
        "",
        "## Размер пула",
        "",
        _markdown(
            catalog.groupby(["session_id", "dataset"], as_index=False).agg(
                candidates_count=("candidate_key", "nunique"),
                trades_count=("trades_count", "sum"),
                first_month_utc=("first_month_utc", "min"),
                last_month_utc=("last_month_utc", "max"),
            )
        ),
        "",
        "## Лучшие устойчивые группы",
        "",
        _markdown(
            stable_groups[
                [
                    "candidate_key",
                    "grouping",
                    "stability_score",
                    "current_trades",
                    "old_trades",
                    "current_mean_return_pct",
                    "old_mean_return_pct",
                    "current_win_rate",
                    "old_win_rate",
                ]
            ].head(30)
        ),
        "",
        "## Expanded Session Adaptive",
        "",
        _markdown(expanded_session_summary),
        "",
        "## Deep Category Adaptive",
        "",
        _markdown(deep_category_summary),
        "",
        "## Сравнение с предыдущими baseline",
        "",
        _markdown(
            compare[
                [
                    "source",
                    "lookback_months",
                    "selected_trades_count",
                    "trades_per_year",
                    "mean_return_pct",
                    "win_rate",
                    "annualized_unit_pnl_pct",
                    "equity_5_annualized_return_pct",
                    "equity_5_max_drawdown_pct",
                ]
            ]
        ),
        "",
        "## Какие кандидаты чаще всего выбирались",
        "",
        _markdown(session_pick_counts.head(30)),
        "",
    ]
    (OUTPUT_DIR / "deep_category_report.md").write_text("\n".join(report_lines), encoding="utf-8")
    return OUTPUT_DIR


__all__ = ["run_deep_category_research"]
