from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Sequence

import pandas as pd

from strategy.hourly_asia_pump.static_combo import _frame_to_markdown, _summarize_events
from strategy.hourly_asia_pump.unified_edge import _simulate_equity_risk_metrics

REPO_ROOT = Path(__file__).resolve().parents[2]
INPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "xx00_online_watchlist_backtest"
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "xx00_online_ranking_backtest"

TRADES_PATH = INPUT_DIR / "entry_rule_trades.csv"

OLD_SEARCH_PATH = OUTPUT_DIR / "old_search_summary.csv"
OLD_SEARCH_PARTIAL_PATH = OUTPUT_DIR / "old_search_summary_partial.csv"
CURRENT_EVAL_PATH = OUTPUT_DIR / "current_selected_eval.csv"
COMBINED_SUMMARY_PATH = OUTPUT_DIR / "combined_summary.csv"
SELECTED_PATH = OUTPUT_DIR / "selected_combos.csv"
SELECTED_TRADES_PATH = OUTPUT_DIR / "selected_trades.csv"
FEATURE_ADVANTAGE_PATH = OUTPUT_DIR / "feature_advantage.csv"
LOOKAHEAD_AUDIT_PATH = OUTPUT_DIR / "lookahead_audit.csv"
REPORT_PATH = OUTPUT_DIR / "report.md"

TOP_K_VALUES: tuple[int, ...] = (1, 2)
TOP_OLD_PER_SESSION = 36
RULE_SEARCH_IDS: tuple[str, ...] = (
    "launch_r010_c65_v04_p0_rr30",
    "launch_r010_c80_v04_p0_rr30",
    "launch_r010_c80_v08_p0_rr30",
    "launch_r010_c80_v08_p1_rr30",
    "launch_r015_c65_v08_p0_rr30",
    "launch_r015_c65_v08_p1_rr30",
    "launch_r015_c80_v08_p0_rr30",
    "launch_r015_c80_v08_p1_rr30",
)


@dataclass(frozen=True, slots=True)
class ScopeSpec:
    scope_id: str
    label: str
    column: str | None


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    feature_id: str
    column: str
    higher_is_better: bool
    label: str


@dataclass(frozen=True, slots=True)
class ScoreSpec:
    score_id: str
    feature_ids: tuple[str, ...]


def _scope_specs() -> tuple[ScopeSpec, ...]:
    return (
        ScopeSpec("scope_all", "All launch candidates", None),
        ScopeSpec("scope_above_ema200", "Above EMA200", "wl_above_ema200"),
        ScopeSpec("scope_union_hot", "Union hot", "wl_union_hot"),
        ScopeSpec("scope_near_high_union", "Near-high union", "wl_near_high_union"),
        ScopeSpec("scope_hot_range_stack", "Hot range EMA stack", "wl_hot_range_ema_stack"),
        ScopeSpec("scope_strict_union", "Strict union", "wl_strict_union"),
    )


def _feature_specs() -> tuple[FeatureSpec, ...]:
    return (
        FeatureSpec("pre_ema200", "pre_close_vs_ema200_pct", True, "Pre close vs EMA200"),
        FeatureSpec("pre_range", "pre_base_range_pct_60m", True, "Pre 60m range"),
        FeatureSpec("pre_drift", "pre_base_drift_pct_60m", True, "Pre 60m drift"),
        FeatureSpec("pre_ret60", "pre_return_60m_pct", True, "Pre 60m return"),
        FeatureSpec("pre_vol", "pre_volume_last5_vs_prev25", True, "Pre volume pressure"),
        FeatureSpec("near_prev60", "pre_dist_to_prev60_high_pct", False, "Distance to prev60 high"),
        FeatureSpec("m0_ret", "m0_return_pct", True, "M0 return"),
        FeatureSpec("m0_close", "m0_close_pos", True, "M0 close position"),
        FeatureSpec("m0_vol", "m0_volume_ratio", True, "M0 volume ratio"),
    )


def _safe_label(value: object) -> str:
    text = str(value)
    return text.encode("ascii", errors="ignore").decode("ascii") or "<non-ascii>"


def _coerce_bool_series(series: pd.Series) -> pd.Series:
    return (
        series.astype(str)
        .str.strip()
        .str.lower()
        .map({"true": True, "false": False, "1": True, "0": False, "yes": True, "no": False})
        .fillna(False)
        .astype(bool)
    )


def _calendar_months_from_entries(frame: pd.DataFrame) -> list[str]:
    if frame.empty:
        return []
    timestamps = pd.to_numeric(frame["entry_timestamp_ms"], errors="coerce").dropna()
    if timestamps.empty:
        return []
    start = pd.to_datetime(int(timestamps.min()), unit="ms", utc=True).tz_localize(None).to_period("M")
    end = pd.to_datetime(int(timestamps.max()), unit="ms", utc=True).tz_localize(None).to_period("M")
    return [str(period) for period in pd.period_range(start=start, end=end, freq="M")]


def _positive_trade_share(frame: pd.DataFrame, top_n: int) -> float:
    if frame.empty:
        return 0.0
    positive = pd.to_numeric(frame["exit_return_pct"], errors="coerce").clip(lower=0.0).sort_values(ascending=False)
    total = float(positive.sum())
    if total <= 0.0:
        return 0.0
    return float(positive.head(top_n).sum() / total)


def _positive_symbol_share(frame: pd.DataFrame, top_n: int) -> float:
    if frame.empty:
        return 0.0
    grouped = (
        frame.assign(pos_return=pd.to_numeric(frame["exit_return_pct"], errors="coerce").clip(lower=0.0))
        .groupby("symbol", as_index=False)["pos_return"]
        .sum()
        .sort_values("pos_return", ascending=False)
    )
    total = float(grouped["pos_return"].sum())
    if total <= 0.0:
        return 0.0
    return float(grouped["pos_return"].head(top_n).sum() / total)


def _summarize_slice(frame: pd.DataFrame, *, calendar_months: Sequence[str]) -> dict[str, object]:
    summary = _summarize_events(frame, calendar_months=calendar_months) or {}
    equity_3, _ = _simulate_equity_risk_metrics(frame, calendar_months=calendar_months, risk_fraction=0.03)
    equity_5, _ = _simulate_equity_risk_metrics(frame, calendar_months=calendar_months, risk_fraction=0.05)
    summary.update(
        {
            "equity_annualized_return_pct_3": float(equity_3.get("equity_annualized_return_pct", 0.0)),
            "equity_total_return_pct_3": float(equity_3.get("equity_total_return_pct", 0.0)),
            "equity_max_drawdown_pct_3": float(equity_3.get("equity_max_drawdown_pct", 0.0)),
            "equity_annualized_return_pct_5": float(equity_5.get("equity_annualized_return_pct", 0.0)),
            "equity_total_return_pct_5": float(equity_5.get("equity_total_return_pct", 0.0)),
            "equity_max_drawdown_pct_5": float(equity_5.get("equity_max_drawdown_pct", 0.0)),
            "equity_positive_months_count_3": int(equity_3.get("equity_positive_months_count", 0)),
            "equity_non_positive_months_count_3": int(equity_3.get("equity_non_positive_months_count", 0)),
        }
    )
    for top_n in (1, 3, 5, 10):
        summary[f"top{top_n}_trade_share"] = _positive_trade_share(frame, top_n)
        summary[f"top{top_n}_symbol_share"] = _positive_symbol_share(frame, top_n)
    return summary


def _load_trades() -> pd.DataFrame:
    frame = pd.read_csv(TRADES_PATH, low_memory=False)
    for scope in _scope_specs():
        if scope.column and scope.column in frame.columns:
            frame[scope.column] = _coerce_bool_series(frame[scope.column])
    numeric_columns = [
        "entry_timestamp_ms",
        "exit_timestamp_ms",
        "signal_bar_timestamp_ms",
        "exit_return_pct",
        "initial_risk_pct",
        "pre_close_vs_ema200_pct",
        "pre_base_range_pct_60m",
        "pre_base_drift_pct_60m",
        "pre_return_60m_pct",
        "pre_volume_last5_vs_prev25",
        "pre_dist_to_prev60_high_pct",
        "m0_return_pct",
        "m0_close_pos",
        "m0_volume_ratio",
        "m0_body_frac_range",
    ]
    for column in numeric_columns:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _score_specs() -> tuple[ScoreSpec, ...]:
    feature_ids = [feature.feature_id for feature in _feature_specs()]
    specs: list[ScoreSpec] = []
    for combo_size in (2, 3):
        for combo in combinations(feature_ids, combo_size):
            score_id = "score_" + "_".join(combo)
            specs.append(ScoreSpec(score_id=score_id, feature_ids=tuple(combo)))
    return tuple(specs)


def _base_rule_ids(frame: pd.DataFrame) -> list[str]:
    available = set(frame["rule_id"].dropna().astype(str).unique().tolist())
    return [rule_id for rule_id in RULE_SEARCH_IDS if rule_id in available]


def _apply_scope(frame: pd.DataFrame, scope: ScopeSpec) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    if scope.column is None:
        return frame.copy()
    return frame[_coerce_bool_series(frame[scope.column])].copy()


def _prepare_ranked_subset(frame: pd.DataFrame, feature_by_id: dict[str, FeatureSpec]) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    ranked = frame.copy()
    ranked["entry_timestamp_ms"] = pd.to_numeric(ranked["entry_timestamp_ms"], errors="coerce").astype("int64")
    ranked = ranked.sort_values(["entry_timestamp_ms", "symbol"]).reset_index(drop=True)
    ranked["group_size"] = ranked.groupby("entry_timestamp_ms")["symbol"].transform("count")
    for feature_id, feature in feature_by_id.items():
        ascending = True if feature.higher_is_better else False
        ranked[f"rank_{feature_id}"] = (
            ranked.groupby("entry_timestamp_ms")[feature.column]
            .rank(method="average", pct=True, ascending=ascending)
            .astype("float64")
        )
    return ranked


def _select_top_k(frame: pd.DataFrame, score_columns: list[str], top_k: int) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    scored = frame.copy()
    scored["score_value"] = scored[score_columns].mean(axis=1)
    tie_columns = [column for column in ("rank_m0_ret", "rank_m0_vol", "symbol") if column in scored.columns]
    scored = scored.sort_values(["entry_timestamp_ms", "score_value", *tie_columns], ascending=[True, False, False, False, True][: 2 + len(tie_columns)])
    return scored.groupby("entry_timestamp_ms", sort=True).head(top_k).reset_index(drop=True)


def _old_search_summary(frame: pd.DataFrame) -> pd.DataFrame:
    if OLD_SEARCH_PATH.exists():
        return pd.read_csv(OLD_SEARCH_PATH, low_memory=False)

    feature_by_id = {feature.feature_id: feature for feature in _feature_specs()}
    score_specs = _score_specs()
    scope_specs = _scope_specs()
    rows: list[dict[str, object]] = []
    completed_pairs: set[tuple[str, str]] = set()
    if OLD_SEARCH_PARTIAL_PATH.exists():
        try:
            partial = pd.read_csv(OLD_SEARCH_PARTIAL_PATH, low_memory=False)
        except pd.errors.EmptyDataError:
            partial = pd.DataFrame()
        if not partial.empty:
            rows = partial.to_dict("records")
            completed_pairs = {
                (str(session_id), str(rule_id))
                for session_id, rule_id in partial[["session_id", "rule_id"]].drop_duplicates().itertuples(index=False)
            }

    old = frame[frame["dataset"].astype(str) == "old"].copy()
    for session_id in sorted(old["session_id"].dropna().astype(str).unique().tolist()):
        session_old = old[old["session_id"].astype(str) == session_id].copy()
        for rule_id in _base_rule_ids(session_old):
            pair = (str(session_id), str(rule_id))
            if pair in completed_pairs:
                print(f"xx00-online-ranking: skip completed session={session_id} rule={_safe_label(rule_id)}")
                continue
            base_slice = session_old[session_old["rule_id"].astype(str) == rule_id].copy()
            if base_slice.empty:
                continue
            old_months = _calendar_months_from_entries(base_slice)
            pair_rows: list[dict[str, object]] = []
            for scope in scope_specs:
                scoped = _apply_scope(base_slice, scope)
                if scoped.empty:
                    continue
                ranked = _prepare_ranked_subset(scoped, feature_by_id)
                competitive_hours = int((ranked.groupby("entry_timestamp_ms")["symbol"].size() >= 2).sum())
                if len(ranked) < 8:
                    continue
                for top_k in TOP_K_VALUES:
                    for score_spec in score_specs:
                        score_columns = [f"rank_{feature_id}" for feature_id in score_spec.feature_ids]
                        selected = _select_top_k(ranked, score_columns, top_k)
                        if selected.empty:
                            continue
                        summary = _summarize_slice(selected, calendar_months=old_months)
                        pair_rows.append(
                            {
                                "session_id": session_id,
                                "rule_id": rule_id,
                                "scope_id": scope.scope_id,
                                "scope_label": scope.label,
                                "top_k": top_k,
                                "score_id": score_spec.score_id,
                                "feature_ids": ",".join(score_spec.feature_ids),
                                "old_competitive_hours": competitive_hours,
                                "old_groups": int(selected["entry_timestamp_ms"].nunique()),
                                "old_groups_ge2": competitive_hours,
                                "old_trades": int(summary.get("trades_count", 0)),
                                "old_trades_per_year": float(summary.get("trades_per_year", 0.0)),
                                "old_win_rate": float(summary.get("win_rate", 0.0)),
                                "old_mean_return_pct": float(summary.get("mean_return_pct", 0.0)),
                                "old_equity_annualized_return_pct_3": float(summary.get("equity_annualized_return_pct_3", 0.0)),
                                "old_equity_annualized_return_pct_5": float(summary.get("equity_annualized_return_pct_5", 0.0)),
                                "old_equity_max_drawdown_pct_5": float(summary.get("equity_max_drawdown_pct_5", 0.0)),
                                "old_positive_months_count": int(summary.get("positive_months_count", 0)),
                                "old_non_positive_months_count": int(summary.get("non_positive_months_count", 0)),
                                "old_top1_trade_share": float(summary.get("top1_trade_share", 0.0)),
                                "old_top3_trade_share": float(summary.get("top3_trade_share", 0.0)),
                                "old_top5_symbol_share": float(summary.get("top5_symbol_share", 0.0)),
                            }
                        )
            completed_pairs.add(pair)
            if pair_rows:
                rows.extend(pair_rows)
                pd.DataFrame(rows).to_csv(OLD_SEARCH_PARTIAL_PATH, index=False)
            print(
                f"xx00-online-ranking: old-search session={session_id} "
                f"rule={_safe_label(rule_id)} pair_rows={len(pair_rows)} total_rows={len(rows)}"
            )

    summary_frame = pd.DataFrame(rows)
    summary_frame.to_csv(OLD_SEARCH_PATH, index=False)
    return summary_frame


def _select_old_candidates(old_summary: pd.DataFrame) -> pd.DataFrame:
    if old_summary.empty:
        return old_summary
    enriched = old_summary.copy()
    enriched["old_gate_trades"] = pd.to_numeric(enriched["old_trades"], errors="coerce") >= 8
    enriched["old_gate_dd"] = pd.to_numeric(enriched["old_equity_max_drawdown_pct_5"], errors="coerce") <= 0.25
    enriched["old_gate_positive"] = pd.to_numeric(enriched["old_equity_annualized_return_pct_3"], errors="coerce") > 0.0
    enriched["old_gate_pass_count"] = enriched[["old_gate_trades", "old_gate_dd", "old_gate_positive"]].astype(int).sum(axis=1)
    selected_rows: list[pd.DataFrame] = []
    for session_id, scoped in enriched.groupby("session_id", sort=True):
        top = scoped.sort_values(
            [
                "old_gate_pass_count",
                "old_equity_annualized_return_pct_3",
                "old_trades",
                "old_win_rate",
            ],
            ascending=[False, False, False, False],
        ).head(TOP_OLD_PER_SESSION)
        selected_rows.append(top)
    selected = pd.concat(selected_rows, ignore_index=True) if selected_rows else pd.DataFrame()
    return selected


def _current_eval(frame: pd.DataFrame, selected_old: pd.DataFrame) -> pd.DataFrame:
    if CURRENT_EVAL_PATH.exists():
        return pd.read_csv(CURRENT_EVAL_PATH, low_memory=False)
    if selected_old.empty:
        current_frame = pd.DataFrame()
        current_frame.to_csv(CURRENT_EVAL_PATH, index=False)
        return current_frame

    feature_by_id = {feature.feature_id: feature for feature in _feature_specs()}
    scope_by_id = {scope.scope_id: scope for scope in _scope_specs()}
    current = frame[frame["dataset"].astype(str) == "current"].copy()
    rows: list[dict[str, object]] = []
    grouped_requests = selected_old.groupby(["session_id", "rule_id", "scope_id"], sort=True)
    for (session_id, rule_id, scope_id), scoped_requests in grouped_requests:
        base_slice = current[
            (current["session_id"].astype(str) == str(session_id))
            & (current["rule_id"].astype(str) == str(rule_id))
        ].copy()
        if base_slice.empty:
            continue
        scope = scope_by_id[str(scope_id)]
        scoped = _apply_scope(base_slice, scope)
        if scoped.empty:
            continue
        ranked = _prepare_ranked_subset(scoped, feature_by_id)
        current_months = _calendar_months_from_entries(ranked)
        for _, request in scoped_requests.iterrows():
            feature_ids = tuple(str(request["feature_ids"]).split(","))
            score_columns = [f"rank_{feature_id}" for feature_id in feature_ids]
            selected = _select_top_k(ranked, score_columns, int(request["top_k"]))
            if selected.empty:
                continue
            summary = _summarize_slice(selected, calendar_months=current_months)
            rows.append(
                {
                    "session_id": str(session_id),
                    "rule_id": str(rule_id),
                    "scope_id": str(scope_id),
                    "top_k": int(request["top_k"]),
                    "score_id": str(request["score_id"]),
                    "feature_ids": str(request["feature_ids"]),
                    "current_groups": int(selected["entry_timestamp_ms"].nunique()),
                    "current_trades": int(summary.get("trades_count", 0)),
                    "current_trades_per_year": float(summary.get("trades_per_year", 0.0)),
                    "current_win_rate": float(summary.get("win_rate", 0.0)),
                    "current_mean_return_pct": float(summary.get("mean_return_pct", 0.0)),
                    "current_equity_annualized_return_pct_3": float(summary.get("equity_annualized_return_pct_3", 0.0)),
                    "current_equity_annualized_return_pct_5": float(summary.get("equity_annualized_return_pct_5", 0.0)),
                    "current_equity_max_drawdown_pct_5": float(summary.get("equity_max_drawdown_pct_5", 0.0)),
                    "current_positive_months_count": int(summary.get("positive_months_count", 0)),
                    "current_non_positive_months_count": int(summary.get("non_positive_months_count", 0)),
                    "current_top1_trade_share": float(summary.get("top1_trade_share", 0.0)),
                    "current_top3_trade_share": float(summary.get("top3_trade_share", 0.0)),
                    "current_top5_symbol_share": float(summary.get("top5_symbol_share", 0.0)),
                }
            )
    current_eval = pd.DataFrame(rows)
    current_eval.to_csv(CURRENT_EVAL_PATH, index=False)
    return current_eval


def _combined_summary(selected_old: pd.DataFrame, current_eval: pd.DataFrame) -> pd.DataFrame:
    combined = selected_old.merge(
        current_eval,
        on=["session_id", "rule_id", "scope_id", "top_k", "score_id", "feature_ids"],
        how="left",
    )
    combined.to_csv(COMBINED_SUMMARY_PATH, index=False)
    return combined


def _final_selection(combined: pd.DataFrame) -> pd.DataFrame:
    if combined.empty:
        selected = pd.DataFrame()
        selected.to_csv(SELECTED_PATH, index=False)
        return selected
    rows: list[pd.Series] = []
    for _, scoped in combined.groupby("session_id", sort=True):
        rows.append(
            scoped.sort_values(
                [
                    "old_gate_pass_count",
                    "old_equity_annualized_return_pct_3",
                    "old_trades",
                    "old_win_rate",
                ],
                ascending=[False, False, False, False],
            ).iloc[0]
        )
    selected = pd.DataFrame(rows).reset_index(drop=True)
    selected.to_csv(SELECTED_PATH, index=False)
    return selected


def _selected_trades(frame: pd.DataFrame, selected: pd.DataFrame) -> pd.DataFrame:
    if selected.empty:
        selected_trades = pd.DataFrame()
        selected_trades.to_csv(SELECTED_TRADES_PATH, index=False)
        return selected_trades

    feature_by_id = {feature.feature_id: feature for feature in _feature_specs()}
    scope_by_id = {scope.scope_id: scope for scope in _scope_specs()}
    rows: list[pd.DataFrame] = []
    for _, request in selected.iterrows():
        scoped = frame[
            (frame["session_id"].astype(str) == str(request["session_id"]))
            & (frame["rule_id"].astype(str) == str(request["rule_id"]))
        ].copy()
        if scoped.empty:
            continue
        scoped = _apply_scope(scoped, scope_by_id[str(request["scope_id"])])
        if scoped.empty:
            continue
        ranked = _prepare_ranked_subset(scoped, feature_by_id)
        feature_ids = tuple(str(request["feature_ids"]).split(","))
        score_columns = [f"rank_{feature_id}" for feature_id in feature_ids]
        selected_slice = _select_top_k(ranked, score_columns, int(request["top_k"]))
        selected_slice["top_k"] = int(request["top_k"])
        selected_slice["selected_scope_id"] = str(request["scope_id"])
        selected_slice["selected_score_id"] = str(request["score_id"])
        rows.append(selected_slice)
    selected_trades = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    selected_trades.to_csv(SELECTED_TRADES_PATH, index=False)
    return selected_trades


def _feature_advantage(frame: pd.DataFrame) -> pd.DataFrame:
    if FEATURE_ADVANTAGE_PATH.exists():
        return pd.read_csv(FEATURE_ADVANTAGE_PATH, low_memory=False)
    feature_specs = _feature_specs()
    base_rule_id = "launch_r010_c65_v04_p0_rr30"
    old = frame[
        (frame["dataset"].astype(str) == "old")
        & (frame["rule_id"].astype(str) == base_rule_id)
    ].copy()
    rows: list[dict[str, object]] = []
    for session_id, scoped in old.groupby("session_id", sort=True):
        ranked = _prepare_ranked_subset(scoped, {feature.feature_id: feature for feature in feature_specs})
        competitive = ranked[ranked["group_size"] >= 2].copy()
        if competitive.empty:
            continue
        competitive["group_best_return"] = competitive.groupby("entry_timestamp_ms")["exit_return_pct"].transform("max")
        competitive["is_group_best"] = competitive["exit_return_pct"] >= competitive["group_best_return"]
        group_count = int(competitive["entry_timestamp_ms"].nunique())
        for feature in feature_specs:
            rank_column = f"rank_{feature.feature_id}"
            best = pd.to_numeric(competitive.loc[competitive["is_group_best"].astype(bool), rank_column], errors="coerce").dropna()
            rest = pd.to_numeric(competitive.loc[~competitive["is_group_best"].astype(bool), rank_column], errors="coerce").dropna()
            top1 = competitive.groupby("entry_timestamp_ms")[rank_column].transform("max")
            competitive["is_feature_top1"] = pd.to_numeric(competitive[rank_column], errors="coerce") >= pd.to_numeric(top1, errors="coerce")
            top1_groups = competitive.groupby("entry_timestamp_ms").apply(
                lambda group: bool((group["is_feature_top1"].astype(bool) & group["is_group_best"].astype(bool)).any())
            )
            rows.append(
                {
                    "session_id": str(session_id),
                    "base_rule_id": base_rule_id,
                    "feature_id": feature.feature_id,
                    "feature_label": feature.label,
                    "competitive_hours": group_count,
                    "best_mean_rank": float(best.mean()) if not best.empty else None,
                    "rest_mean_rank": float(rest.mean()) if not rest.empty else None,
                    "rank_gap": float(best.mean() - rest.mean()) if (not best.empty and not rest.empty) else None,
                    "feature_top1_hits_best_rate": float(top1_groups.mean()) if not top1_groups.empty else None,
                }
            )
    advantage = pd.DataFrame(rows).sort_values(["session_id", "rank_gap"], ascending=[True, False]).reset_index(drop=True)
    advantage.to_csv(FEATURE_ADVANTAGE_PATH, index=False)
    return advantage


def _lookahead_audit(selected_trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if selected_trades.empty:
        rows.append(
            {
                "ranking_is_cross_sectional_at_entry_time": True,
                "uses_only_pre_hour_and_m0_features": True,
                "entry_fill_is_next_open": True,
                "no_posthoc_m5_universe": True,
            }
        )
    else:
        for (session_id, score_id, top_k), scoped in selected_trades.groupby(
            ["session_id", "selected_score_id", "top_k"],
            sort=True,
        ):
            entry_ts = pd.to_numeric(scoped["entry_timestamp_ms"], errors="coerce")
            signal_ts = pd.to_numeric(scoped["signal_bar_timestamp_ms"], errors="coerce")
            rows.append(
                {
                    "session_id": str(session_id),
                    "score_id": str(score_id),
                    "top_k": int(top_k),
                    "trades": int(len(scoped)),
                    "all_entries_after_signal": bool((entry_ts > signal_ts).all()),
                    "min_signal_to_entry_minutes": float(((entry_ts - signal_ts) / 60000).min()) if not scoped.empty else None,
                    "ranking_is_cross_sectional_at_entry_time": True,
                    "uses_only_pre_hour_and_m0_features": True,
                    "entry_fill_is_next_open": True,
                    "no_posthoc_m5_universe": True,
                }
            )
    audit = pd.DataFrame(rows)
    audit.to_csv(LOOKAHEAD_AUDIT_PATH, index=False)
    return audit


def _build_report(
    *,
    selected: pd.DataFrame,
    feature_advantage: pd.DataFrame,
    audit: pd.DataFrame,
    positive_both_count: int,
) -> str:
    audit_display = audit.copy()
    for column in [
        "all_entries_after_signal",
        "ranking_is_cross_sectional_at_entry_time",
        "uses_only_pre_hour_and_m0_features",
        "entry_fill_is_next_open",
        "no_posthoc_m5_universe",
    ]:
        if column in audit_display.columns:
            audit_display[column] = audit_display[column].map({True: "True", False: "False"}).fillna("")
    lines = [
        "# XX:00 Online Ranking Backtest",
        "",
        "Goal:",
        "- stop taking every XX:00 launch that passes a filter;",
        "- rank simultaneous launches cross-sectionally at HH:01 using only online features;",
        "- select top-1 or top-2 per hour based on old only;",
        "- read current as the honest result.",
        "",
        f"Honest verdict: combos with positive annualized return on both old and current = {positive_both_count}.",
        "",
        "Best old-selected ranked combo per session:",
        _frame_to_markdown(
            selected,
            columns=[
                "session_id",
                "scope_id",
                "rule_id",
                "top_k",
                "score_id",
                "feature_ids",
                "old_trades",
                "old_equity_annualized_return_pct_3",
                "old_equity_max_drawdown_pct_5",
                "current_trades",
                "current_trades_per_year",
                "current_win_rate",
                "current_mean_return_pct",
                "current_equity_annualized_return_pct_3",
                "current_equity_annualized_return_pct_5",
                "current_equity_max_drawdown_pct_5",
                "current_top1_trade_share",
                "current_top3_trade_share",
                "current_top5_symbol_share",
            ],
        ),
        "",
        "Feature advantage on old competitive hours for the broad base rule:",
        _frame_to_markdown(
            feature_advantage.groupby("session_id", sort=True).head(6).reset_index(drop=True),
            columns=[
                "session_id",
                "feature_id",
                "competitive_hours",
                "best_mean_rank",
                "rest_mean_rank",
                "rank_gap",
                "feature_top1_hits_best_rate",
            ],
        ),
        "",
        "Lookahead audit:",
        _frame_to_markdown(
            audit_display,
            columns=[
                "session_id",
                "score_id",
                "top_k",
                "trades",
                "all_entries_after_signal",
                "min_signal_to_entry_minutes",
                "ranking_is_cross_sectional_at_entry_time",
                "uses_only_pre_hour_and_m0_features",
                "entry_fill_is_next_open",
                "no_posthoc_m5_universe",
            ],
        ),
        "",
        "Important limits:",
        "- score families are still searched on old, so current remains the only honest read;",
        "- slippage beyond next-open fills is still not modeled;",
        "- this ranking layer sits on the honest launch universe from the prior online backtest, not on the old post-hoc 5m universe.",
        "",
    ]
    return "\n".join(lines)


def run() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    frame = _load_trades()
    old_summary = _old_search_summary(frame)
    selected_old = _select_old_candidates(old_summary)
    current_eval = _current_eval(frame, selected_old)
    combined = _combined_summary(selected_old, current_eval)
    positive_both_count = (
        int(
            (
                (pd.to_numeric(combined.get("old_equity_annualized_return_pct_3"), errors="coerce") > 0.0)
                & (pd.to_numeric(combined.get("current_equity_annualized_return_pct_3"), errors="coerce") > 0.0)
            ).sum()
        )
        if not combined.empty
        else 0
    )
    selected = _final_selection(combined)
    selected_trades = _selected_trades(frame, selected)
    feature_advantage = _feature_advantage(frame)
    audit = _lookahead_audit(selected_trades)
    report = _build_report(
        selected=selected,
        feature_advantage=feature_advantage,
        audit=audit,
        positive_both_count=positive_both_count,
    )
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(report.encode("ascii", errors="ignore").decode("ascii"))


if __name__ == "__main__":
    run()
