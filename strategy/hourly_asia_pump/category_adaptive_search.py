from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import math
from pathlib import Path
from typing import Sequence

import pandas as pd

from strategy.hourly_asia_pump.static_combo import _frame_to_markdown, _summarize_events
from strategy.hourly_asia_pump.unified_edge import _simulate_equity_risk_metrics


REPO_ROOT = Path(__file__).resolve().parents[2]
CURRENT_ROOT = REPO_ROOT / ".output" / "results" / "research" / "hourly_asia_pump_static_combo" / "latest_report"
PREV_ROOT = REPO_ROOT / ".output" / "results_prev_year_5m"
OUTPUT_DIR = PREV_ROOT / "category_adaptive_pump_type_search"

SOURCE_FIRST_SEEN = PREV_ROOT / "cross_period_meta_analysis" / "symbol_first_seen_union.csv"
SOURCE_SESSION_ADAPTIVE = PREV_ROOT / "cross_period_meta_analysis" / "adaptive_selection_tradelevel_summary.csv"


@dataclass(frozen=True, slots=True)
class CandidateSource:
    candidate_key: str
    title: str
    session_id: str
    side: str
    current_path: Path
    old_path: Path | None
    current_filter_component_id: str | None = None
    old_filter_component_id: str | None = None
    exploratory: bool = False


CANDIDATE_SOURCES: tuple[CandidateSource, ...] = (
    CandidateSource(
        candidate_key="asia_C02",
        title="Азия long C02",
        session_id="asia",
        side="long",
        current_path=CURRENT_ROOT / "unified_edge_search" / "unified_edge_best_events.csv",
        old_path=PREV_ROOT / "fixed_candidate_validation" / "asia_long_C02_prev_year_events.csv",
    ),
    CandidateSource(
        candidate_key="america_B1",
        title="Америка short B1",
        session_id="america",
        side="short",
        current_path=CURRENT_ROOT / "america_short_portfolio_search" / "america_short_portfolio_best_events.csv",
        old_path=PREV_ROOT / "fixed_candidate_validation_after_1m" / "america_short_C07_prev_year_component_events.csv",
        current_filter_component_id="B1_nonq_mid_limit",
        old_filter_component_id="B1_nonq_mid_limit",
    ),
    CandidateSource(
        candidate_key="america_Q1",
        title="Америка short Q1",
        session_id="america",
        side="short",
        current_path=CURRENT_ROOT / "america_short_portfolio_search" / "america_short_portfolio_best_events.csv",
        old_path=PREV_ROOT / "fixed_candidate_validation_after_1m" / "america_short_C07_prev_year_component_events.csv",
        current_filter_component_id="Q1_quarter_soft_warm",
        old_filter_component_id="Q1_quarter_soft_warm",
    ),
    CandidateSource(
        candidate_key="america_Q2",
        title="Америка short Q2",
        session_id="america",
        side="short",
        current_path=CURRENT_ROOT / "america_short_portfolio_search" / "america_short_portfolio_best_events.csv",
        old_path=PREV_ROOT / "fixed_candidate_validation_after_1m" / "america_short_C07_prev_year_component_events.csv",
        current_filter_component_id="Q2_quarter_mid_warm",
        old_filter_component_id="Q2_quarter_mid_warm",
    ),
    CandidateSource(
        candidate_key="europe_E2",
        title="Европа short E2",
        session_id="europe",
        side="short",
        current_path=CURRENT_ROOT / "europe_short_portfolio_search" / "europe_short_portfolio_best_events.csv",
        old_path=PREV_ROOT / "fixed_candidate_validation_after_1m" / "europe_short_C08_prev_year_component_events.csv",
        current_filter_component_id="E2_nonq_wick_soft",
        old_filter_component_id="E2_nonq_wick_soft",
    ),
)


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Не найден CSV: {path}")
    return pd.read_csv(path)


def _calendar_months_from_ms(min_ms: int, max_ms: int) -> list[str]:
    start = pd.to_datetime(min_ms, unit="ms", utc=True).to_period("M")
    end = pd.to_datetime(max_ms, unit="ms", utc=True).to_period("M")
    return [str(month) for month in pd.period_range(start=start, end=end, freq="M")]


def _mean_or_none(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return float(clean.mean())


def _quantile_thresholds(series: pd.Series) -> tuple[float, float]:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return 0.0, 0.0
    return float(clean.quantile(1.0 / 3.0)), float(clean.quantile(2.0 / 3.0))


def _build_feature_thresholds(frame: pd.DataFrame) -> dict[str, tuple[float, float]]:
    return {
        "pre_base_drift_pct_60m": _quantile_thresholds(frame["pre_base_drift_pct_60m"]),
        "pre_base_range_pct_60m": _quantile_thresholds(frame["pre_base_range_pct_60m"]),
        "trigger_return_pct": _quantile_thresholds(frame["trigger_return_pct"]),
        "next_pullback_frac": _quantile_thresholds(frame["next_pullback_frac"]),
        "next_close_pos_in_bar": _quantile_thresholds(frame["next_close_pos_in_bar"]),
        "next_extension_above_trigger_high_pct": _quantile_thresholds(frame["next_extension_above_trigger_high_pct"]),
    }


def _apply_tercile_bucket(series: pd.Series, thresholds: tuple[float, float], labels: Sequence[str]) -> pd.Series:
    lower, upper = thresholds
    clean = pd.to_numeric(series, errors="coerce")
    bucket = pd.Series(index=clean.index, dtype="object")
    bucket.loc[clean <= lower] = labels[0]
    bucket.loc[(clean > lower) & (clean <= upper)] = labels[1]
    bucket.loc[clean > upper] = labels[2]
    return bucket.fillna("unknown")


def _normalize_events(frame: pd.DataFrame, source: CandidateSource, dataset: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    scoped = frame.copy()
    if source.current_filter_component_id and dataset == "current":
        scoped = scoped[scoped.get("component_id", pd.Series(dtype="object")).astype(str) == source.current_filter_component_id].copy()
    if source.old_filter_component_id and dataset == "old":
        scoped = scoped[scoped.get("component_id", pd.Series(dtype="object")).astype(str) == source.old_filter_component_id].copy()
    if scoped.empty:
        return scoped
    scoped["candidate_key"] = source.candidate_key
    scoped["candidate_title"] = source.title
    scoped["session_id"] = source.session_id
    scoped["side"] = source.side
    scoped["dataset"] = dataset
    if "month_utc" not in scoped.columns:
        scoped["month_utc"] = pd.to_datetime(scoped["timestamp_ms"], unit="ms", utc=True).dt.strftime("%Y-%m")
    scoped["signal_key"] = (
        scoped["session_id"].astype(str)
        + "|"
        + scoped["side"].astype(str)
        + "|"
        + scoped["symbol"].astype(str)
        + "|"
        + scoped["timestamp_ms"].astype(str)
    )
    return scoped


def _load_candidate_events() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for source in CANDIDATE_SOURCES:
        current = _normalize_events(_load_csv(source.current_path), source, "current")
        if not current.empty:
            frames.append(current)
        if source.old_path is not None:
            old = _normalize_events(_load_csv(source.old_path), source, "old")
            if not old.empty:
                frames.append(old)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    combined["timestamp_ms"] = pd.to_numeric(combined["timestamp_ms"], errors="coerce")
    combined["entry_timestamp_ms"] = pd.to_numeric(combined.get("entry_timestamp_ms"), errors="coerce")
    combined["exit_timestamp_ms"] = pd.to_numeric(combined.get("exit_timestamp_ms"), errors="coerce")
    combined["exit_return_pct"] = pd.to_numeric(combined.get("exit_return_pct"), errors="coerce")
    combined["initial_risk_pct"] = pd.to_numeric(combined.get("initial_risk_pct"), errors="coerce")
    return combined


def _attach_symbol_age(frame: pd.DataFrame) -> pd.DataFrame:
    first_seen = _load_csv(SOURCE_FIRST_SEEN)
    merged = frame.merge(first_seen[["symbol", "first_seen_ms", "first_seen_utc"]], on="symbol", how="left")
    merged["age_days_at_event"] = (
        pd.to_numeric(merged["timestamp_ms"], errors="coerce") - pd.to_numeric(merged["first_seen_ms"], errors="coerce")
    ) / 86_400_000.0
    merged["age_days_at_event"] = pd.to_numeric(merged["age_days_at_event"], errors="coerce")
    merged["age_coarse"] = "old"
    merged.loc[merged["age_days_at_event"] <= 90.0, "age_coarse"] = "new"
    merged.loc[(merged["age_days_at_event"] > 90.0) & (merged["age_days_at_event"] <= 365.0), "age_coarse"] = "mature"
    return merged


def _assign_archetype_buckets(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, tuple[float, float]]]:
    enriched = frame.copy()
    thresholds = _build_feature_thresholds(enriched)
    enriched["preheat_bucket"] = _apply_tercile_bucket(
        enriched["pre_base_drift_pct_60m"], thresholds["pre_base_drift_pct_60m"], ("drift_cold", "drift_warm", "drift_hot")
    )
    enriched["range_bucket"] = _apply_tercile_bucket(
        enriched["pre_base_range_pct_60m"], thresholds["pre_base_range_pct_60m"], ("range_tight", "range_mid", "range_wide")
    )
    enriched["anomaly_bucket"] = _apply_tercile_bucket(
        enriched["trigger_return_pct"], thresholds["trigger_return_pct"], ("anomaly_small", "anomaly_mid", "anomaly_hot")
    )
    enriched["pullback_bucket"] = _apply_tercile_bucket(
        enriched["next_pullback_frac"], thresholds["next_pullback_frac"], ("pullback_shallow", "pullback_mid", "pullback_deep")
    )
    enriched["close_bucket"] = _apply_tercile_bucket(
        enriched["next_close_pos_in_bar"], thresholds["next_close_pos_in_bar"], ("close_low", "close_mid", "close_high")
    )
    enriched["extension_bucket"] = _apply_tercile_bucket(
        enriched["next_extension_above_trigger_high_pct"],
        thresholds["next_extension_above_trigger_high_pct"],
        ("extension_weak", "extension_mid", "extension_strong"),
    )

    enriched["volatility_context_bucket"] = "context_warm"
    enriched.loc[
        (enriched["preheat_bucket"] == "drift_cold") & (enriched["range_bucket"] == "range_tight"),
        "volatility_context_bucket",
    ] = "context_coiled"
    enriched.loc[
        (enriched["preheat_bucket"] == "drift_hot") | (enriched["range_bucket"] == "range_wide"),
        "volatility_context_bucket",
    ] = "context_overheated"

    enriched["structure_bucket"] = "structure_mixed"
    enriched.loc[
        (enriched["pullback_bucket"] == "pullback_deep") | (enriched["close_bucket"] == "close_low"),
        "structure_bucket",
    ] = "structure_failed"
    enriched.loc[
        (enriched["pullback_bucket"] == "pullback_shallow")
        & (enriched["close_bucket"] == "close_high")
        & (enriched["extension_bucket"] != "extension_weak"),
        "structure_bucket",
    ] = "structure_clean"
    enriched.loc[
        (enriched["structure_bucket"] == "structure_mixed")
        & (enriched["pullback_bucket"].isin(["pullback_shallow", "pullback_mid"]))
        & (enriched["close_bucket"].isin(["close_mid", "close_high"])),
        "structure_bucket",
    ] = "structure_holding"
    return enriched, thresholds


def _candidate_summary(frame: pd.DataFrame, calendar_months: Sequence[str]) -> dict[str, object]:
    summary = _summarize_events(frame, calendar_months=calendar_months) or {}
    equity_5, _ = _simulate_equity_risk_metrics(frame, calendar_months=calendar_months, risk_fraction=0.05)
    return {**summary, **equity_5}


def _build_stable_archetypes(events: pd.DataFrame) -> pd.DataFrame:
    features = [
        "age_coarse",
        "preheat_bucket",
        "range_bucket",
        "anomaly_bucket",
        "volatility_context_bucket",
        "structure_bucket",
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
                    stability_score = (
                        min(current_mean, old_mean) * 100.0
                        + min(current_wr, old_wr) * 2.0
                        + math.log1p(min(len(current), len(old)))
                    )
                    row = {
                        "candidate_key": candidate_key,
                        "grouping": "|".join(grouping),
                        "archetype_id": f"{candidate_key}::{ '|'.join(grouping)}::{'|'.join(str(v) for v in values)}",
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
    archetypes = pd.DataFrame(rows)
    if archetypes.empty:
        return archetypes
    return archetypes.sort_values(
        ["candidate_key", "stability_score", "current_trades", "old_trades"],
        ascending=[True, False, False, False],
    ).reset_index(drop=True)


def _event_matches_archetype(event: pd.Series, archetype: pd.Series) -> bool:
    grouping = str(archetype["grouping"]).split("|")
    for feature in grouping:
        if pd.isna(archetype.get(feature)):
            continue
        if str(event.get(feature)) != str(archetype.get(feature)):
            return False
    return True


def _attach_best_archetype(events: pd.DataFrame, archetypes: pd.DataFrame) -> pd.DataFrame:
    if events.empty or archetypes.empty:
        return events
    rows: list[dict[str, object]] = []
    archetypes_by_candidate = {
        candidate_key: group.sort_values("stability_score", ascending=False).to_dict("records")
        for candidate_key, group in archetypes.groupby("candidate_key", sort=True)
    }
    for _, event in events.iterrows():
        candidate_archetypes = archetypes_by_candidate.get(str(event["candidate_key"]), [])
        best_match: dict[str, object] | None = None
        for archetype in candidate_archetypes:
            if _event_matches_archetype(event, pd.Series(archetype)):
                best_match = archetype
                break
        row = event.to_dict()
        row["matched_archetype_id"] = best_match.get("archetype_id") if best_match else None
        row["matched_archetype_grouping"] = best_match.get("grouping") if best_match else None
        row["matched_archetype_score"] = best_match.get("stability_score") if best_match else None
        rows.append(row)
    return pd.DataFrame(rows)


def _score_candidate_on_window(frame: pd.DataFrame, months: Sequence[str]) -> dict[str, object] | None:
    scoped = frame[frame["month_utc"].astype(str).isin([str(month) for month in months])].copy()
    if len(scoped) < 3:
        return None
    summary = _summarize_events(scoped, calendar_months=months) or {}
    equity_5, _ = _simulate_equity_risk_metrics(scoped, calendar_months=months, risk_fraction=0.05)
    mean_return = float(summary.get("mean_return_pct", 0.0) or 0.0)
    win_rate = float(summary.get("win_rate", 0.0) or 0.0)
    annualized = float(summary.get("annualized_unit_pnl_pct", 0.0) or 0.0)
    drawdown = float(summary.get("max_drawdown_pct", 0.0) or 0.0)
    stable_months = float(equity_5.get("equity_stable_positive_months_count", 0.0) or 0.0)
    score = annualized + mean_return * 4.0 + win_rate - drawdown * 1.5 + stable_months * 0.05
    return {
        **summary,
        **equity_5,
        "selection_score": score,
    }


def _run_category_adaptive_selection(
    events: pd.DataFrame,
    *,
    lookback_months: int,
    calendar_months: Sequence[str],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if events.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    months = [str(month) for month in calendar_months]
    eligible_events = events[events["matched_archetype_id"].notna()].copy()
    if eligible_events.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    pick_rows: list[dict[str, object]] = []
    selected_frames: list[pd.DataFrame] = []
    for month_index, month in enumerate(months):
        if month_index < lookback_months:
            continue
        train_months = months[month_index - lookback_months : month_index]
        month_events = eligible_events[eligible_events["month_utc"].astype(str) == str(month)].copy()
        if month_events.empty:
            continue

        chosen_for_archetype: dict[str, dict[str, object]] = {}
        for archetype_id, archetype_events in eligible_events.groupby("matched_archetype_id", sort=True):
            candidate_scores: list[dict[str, object]] = []
            for candidate_key, candidate_events in archetype_events.groupby("candidate_key", sort=True):
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
            chosen_for_archetype[str(archetype_id)] = scores_frame.iloc[0].to_dict()

        if not chosen_for_archetype:
            continue

        month_rows: list[pd.Series] = []
        for _, event in month_events.iterrows():
            chosen = chosen_for_archetype.get(str(event["matched_archetype_id"]))
            if chosen is None or str(chosen["candidate_key"]) != str(event["candidate_key"]):
                continue
            scoped = event.copy()
            scoped["selection_score"] = chosen["selection_score"]
            scoped["lookback_months"] = lookback_months
            month_rows.append(scoped)
            pick_rows.append(
                {
                    "month_utc": month,
                    "lookback_months": lookback_months,
                    "matched_archetype_id": event["matched_archetype_id"],
                    "candidate_key": event["candidate_key"],
                    "selection_score": chosen["selection_score"],
                    "train_trades_count": chosen.get("trades_count"),
                    "train_mean_return_pct": chosen.get("mean_return_pct"),
                    "train_win_rate": chosen.get("win_rate"),
                    "train_annualized_unit_pnl_pct": chosen.get("annualized_unit_pnl_pct"),
                    "train_equity_5_annualized_return_pct": chosen.get("equity_annualized_return_pct"),
                    "train_equity_5_max_drawdown_pct": chosen.get("equity_max_drawdown_pct"),
                }
            )

        if not month_rows:
            continue
        chosen_frame = pd.DataFrame(month_rows).sort_values(
            ["signal_key", "selection_score", "matched_archetype_score", "exit_return_pct"],
            ascending=[True, False, False, False],
        )
        chosen_frame = chosen_frame.groupby("signal_key", as_index=False, sort=False).head(1).copy()
        selected_frames.append(chosen_frame)

    selected_events = pd.concat(selected_frames, ignore_index=True) if selected_frames else pd.DataFrame()
    picks = pd.DataFrame(pick_rows)
    if selected_events.empty:
        return picks, selected_events, pd.DataFrame(), pd.DataFrame()

    summary = _summarize_events(selected_events, calendar_months=months) or {}
    equity_5, monthly_5 = _simulate_equity_risk_metrics(selected_events, calendar_months=months, risk_fraction=0.05)
    equity_9, monthly_9 = _simulate_equity_risk_metrics(selected_events, calendar_months=months, risk_fraction=0.09)
    summary_row = pd.DataFrame(
        [
            {
                "lookback_months": lookback_months,
                **summary,
                "selected_trades_count": len(selected_events),
                "selected_archetypes_count": int(selected_events["matched_archetype_id"].nunique()),
                "selected_candidates_count": int(selected_events["candidate_key"].nunique()),
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
    return picks, selected_events, summary_row, monthly


def _build_current_vs_old_by_archetype(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for archetype_id, group in events.groupby("matched_archetype_id", sort=True):
        if pd.isna(archetype_id):
            continue
        current = group[group["dataset"] == "current"].copy()
        old = group[group["dataset"] == "old"].copy()
        rows.append(
            {
                "matched_archetype_id": archetype_id,
                "candidate_key": group["candidate_key"].iloc[0],
                "current_trades": len(current),
                "old_trades": len(old),
                "current_mean_return_pct": _mean_or_none(current["exit_return_pct"]),
                "old_mean_return_pct": _mean_or_none(old["exit_return_pct"]),
                "current_win_rate": _mean_or_none((pd.to_numeric(current["exit_return_pct"], errors="coerce") > 0.0).astype("float64")),
                "old_win_rate": _mean_or_none((pd.to_numeric(old["exit_return_pct"], errors="coerce") > 0.0).astype("float64")),
            }
        )
    return pd.DataFrame(rows).sort_values(["candidate_key", "current_trades"], ascending=[True, False]).reset_index(drop=True)


def _write_report(
    *,
    thresholds: dict[str, tuple[float, float]],
    candidate_summary: pd.DataFrame,
    stable_archetypes: pd.DataFrame,
    adaptive_summary: pd.DataFrame,
    adaptive_vs_session: pd.DataFrame,
) -> None:
    lines = [
        "# Category-Based Adaptive Search",
        "",
        "## Что это за исследование",
        "",
        "- Берем фиксированный пул кандидатов по Азии, Америке и Европе.",
        "- Описываем каждый памп по природным признакам, а не по минуте или часу.",
        "- Для широких archetype-категорий проверяем, какой кандидат устойчивее одновременно на старом и новом периоде.",
        "- Затем запускаем rolling adaptive selection на всем двухлетнем окне.",
        "",
        "## Пороговые уровни buckets",
        "",
        f"- pre_base_drift_pct_60m terciles: {thresholds['pre_base_drift_pct_60m'][0]:.4f}, {thresholds['pre_base_drift_pct_60m'][1]:.4f}",
        f"- pre_base_range_pct_60m terciles: {thresholds['pre_base_range_pct_60m'][0]:.4f}, {thresholds['pre_base_range_pct_60m'][1]:.4f}",
        f"- trigger_return_pct terciles: {thresholds['trigger_return_pct'][0]:.4f}, {thresholds['trigger_return_pct'][1]:.4f}",
        f"- next_pullback_frac terciles: {thresholds['next_pullback_frac'][0]:.4f}, {thresholds['next_pullback_frac'][1]:.4f}",
        f"- next_close_pos_in_bar terciles: {thresholds['next_close_pos_in_bar'][0]:.4f}, {thresholds['next_close_pos_in_bar'][1]:.4f}",
        f"- next_extension_above_trigger_high_pct terciles: {thresholds['next_extension_above_trigger_high_pct'][0]:.4f}, {thresholds['next_extension_above_trigger_high_pct'][1]:.4f}",
        "",
        "## Пул кандидатов",
        "",
        _frame_to_markdown(
            candidate_summary,
            columns=(
                "candidate_key",
                "trades_count",
                "trades_per_year",
                "mean_return_pct",
                "win_rate",
                "annualized_unit_pnl_pct",
                "equity_5_annualized_return_pct",
                "equity_5_max_drawdown_pct",
                "positive_months_count",
                "stable_positive_months_count",
            ),
        ),
        "",
        "## Лучшие устойчивые archetypes",
        "",
        _frame_to_markdown(
            stable_archetypes,
            columns=(
                "candidate_key",
                "grouping",
                "age_coarse",
                "preheat_bucket",
                "range_bucket",
                "anomaly_bucket",
                "volatility_context_bucket",
                "structure_bucket",
                "current_trades",
                "old_trades",
                "current_mean_return_pct",
                "old_mean_return_pct",
                "current_win_rate",
                "old_win_rate",
                "stability_score",
            ),
            limit=36,
        ),
        "",
        "## Adaptive Summary",
        "",
        _frame_to_markdown(
            adaptive_summary,
            columns=(
                "lookback_months",
                "selected_trades_count",
                "trades_per_year",
                "mean_return_pct",
                "win_rate",
                "annualized_unit_pnl_pct",
                "max_drawdown_pct",
                "positive_months_count",
                "stable_positive_months_count",
                "equity_5_annualized_return_pct",
                "equity_5_max_drawdown_pct",
                "equity_9_annualized_return_pct",
                "equity_9_max_drawdown_pct",
                "selected_archetypes_count",
                "selected_candidates_count",
            ),
        ),
        "",
        "## Сравнение с прежней session-based адаптацией",
        "",
        _frame_to_markdown(
            adaptive_vs_session,
            columns=(
                "lookback_months",
                "mode",
                "selected_trades_count",
                "trades_per_year",
                "mean_return_pct",
                "win_rate",
                "annualized_unit_pnl_pct",
                "equity_5_annualized_return_pct",
                "equity_5_max_drawdown_pct",
            ),
        ),
        "",
        "## Как читать результат",
        "",
        "- Если category-based adaptive выигрывает у session-based, значит природа пампа действительно помогает выбирать тактику.",
        "- Если выигрыша нет, это тоже честный результат: значит типизация пока слабее, чем нам казалось, или пул кандидатов еще бедный.",
        "",
    ]
    (OUTPUT_DIR / "category_adaptive_report.md").write_text("\n".join(lines), encoding="utf-8")


def run_category_adaptive_search() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    events = _load_candidate_events()
    events = _attach_symbol_age(events)
    events, thresholds = _assign_archetype_buckets(events)
    events.to_csv(OUTPUT_DIR / "category_adaptive_events.csv", index=False)

    stable_archetypes = _build_stable_archetypes(events)
    stable_archetypes.to_csv(OUTPUT_DIR / "category_stable_archetypes.csv", index=False)

    events = _attach_best_archetype(events, stable_archetypes)
    events.to_csv(OUTPUT_DIR / "category_events_with_archetypes.csv", index=False)

    min_ms = int(pd.to_numeric(events["entry_timestamp_ms"], errors="coerce").dropna().min())
    max_ms = int(pd.to_numeric(events["entry_timestamp_ms"], errors="coerce").dropna().max())
    calendar_months = _calendar_months_from_ms(min_ms, max_ms)

    candidate_rows: list[dict[str, object]] = []
    for candidate_key, group in events.groupby("candidate_key", sort=True):
        summary = _candidate_summary(group, calendar_months)
        candidate_rows.append(
            {
                "candidate_key": candidate_key,
                **summary,
                "stable_positive_months_count": summary.get("equity_stable_positive_months_count"),
            }
        )
    candidate_summary = pd.DataFrame(candidate_rows).sort_values(
        ["annualized_unit_pnl_pct", "mean_return_pct"], ascending=[False, False]
    )
    candidate_summary.to_csv(OUTPUT_DIR / "category_candidate_summary.csv", index=False)

    adaptive_rows: list[pd.DataFrame] = []
    adaptive_vs_session_rows: list[pd.DataFrame] = []
    session_adaptive = _load_csv(SOURCE_SESSION_ADAPTIVE)
    for lookback_months in (3, 6, 9, 12):
        picks, selected_events, summary, monthly = _run_category_adaptive_selection(
            events,
            lookback_months=lookback_months,
            calendar_months=calendar_months,
        )
        picks.to_csv(OUTPUT_DIR / f"category_adaptive_{lookback_months}m_picks.csv", index=False)
        selected_events.to_csv(OUTPUT_DIR / f"category_adaptive_{lookback_months}m_events.csv", index=False)
        monthly.to_csv(OUTPUT_DIR / f"category_adaptive_{lookback_months}m_monthly.csv", index=False)
        summary.to_csv(OUTPUT_DIR / f"category_adaptive_{lookback_months}m_summary.csv", index=False)
        adaptive_rows.append(summary)

        if not summary.empty:
            summary_mode = summary.copy()
            summary_mode["mode"] = "category_adaptive"
            adaptive_vs_session_rows.append(summary_mode)
        baseline = session_adaptive[session_adaptive["lookback_months"] == lookback_months].copy()
        if not baseline.empty:
            baseline["mode"] = "session_adaptive"
            adaptive_vs_session_rows.append(baseline)

    adaptive_summary = pd.concat(adaptive_rows, ignore_index=True) if adaptive_rows else pd.DataFrame()
    adaptive_summary.to_csv(OUTPUT_DIR / "category_adaptive_summary.csv", index=False)

    adaptive_vs_session = pd.concat(adaptive_vs_session_rows, ignore_index=True) if adaptive_vs_session_rows else pd.DataFrame()
    adaptive_vs_session.to_csv(OUTPUT_DIR / "category_adaptive_vs_session_summary.csv", index=False)

    archetype_compare = _build_current_vs_old_by_archetype(events)
    archetype_compare.to_csv(OUTPUT_DIR / "category_archetype_current_vs_old.csv", index=False)

    _write_report(
        thresholds=thresholds,
        candidate_summary=candidate_summary,
        stable_archetypes=stable_archetypes,
        adaptive_summary=adaptive_summary,
        adaptive_vs_session=adaptive_vs_session,
    )
    return OUTPUT_DIR


if __name__ == "__main__":
    result = run_category_adaptive_search()
    print(result)
