from __future__ import annotations

import logging
from itertools import combinations
from pathlib import Path

import pandas as pd

from strategy.hourly_asia_pump.anomaly_category_lab import _calendar_months_from_frame, _frame_to_markdown, _summarize_events
from strategy.hourly_asia_pump.unified_edge import (
    _build_concentration_metrics,
    _build_month_stability_metrics,
    _build_symbol_concentration_metrics,
    _candidate_score,
    _simulate_equity_risk_metrics,
)

module_logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
INPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "session_long_nature_lab"
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "session_long_portfolio_lab"

SESSIONS = ("europe", "america")
MIN_CURRENT_TRADES = 10
MIN_OLD_TRADES = 5
MIN_MONTHS_WITH_TRADES = 12
MAX_MONTH_SHARE = 0.35
MAX_COMPONENTS = 3
MAX_CANDIDATES_PER_SESSION = 18


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, low_memory=False)


def _candidate_signature(frame: pd.DataFrame) -> pd.Series:
    return (
        frame["session_id"].astype(str)
        + "|"
        + frame["canonical_category"].astype(str)
        + "|"
        + frame["context_archetype"].astype(str)
        + "|"
        + frame["impulse_archetype"].astype(str)
        + "|"
        + frame["confirmation_archetype"].astype(str)
        + "|"
        + frame["wave_archetype"].astype(str)
        + "|"
        + frame["pre_accumulation_type"].astype(str)
        + "|"
        + frame["trade_model_label"].astype(str)
    )


def _category_signature(frame: pd.DataFrame) -> pd.Series:
    return (
        frame["session_id"].astype(str)
        + "|"
        + frame["canonical_category"].astype(str)
        + "|"
        + frame["context_archetype"].astype(str)
        + "|"
        + frame["impulse_archetype"].astype(str)
        + "|"
        + frame["confirmation_archetype"].astype(str)
        + "|"
        + frame["wave_archetype"].astype(str)
        + "|"
        + frame["pre_accumulation_type"].astype(str)
    )


def _candidate_pool(summary: pd.DataFrame, session_id: str) -> pd.DataFrame:
    scoped = summary[summary["session_id"].astype(str) == str(session_id)].copy()
    if scoped.empty:
        return scoped
    scoped = scoped[
        (pd.to_numeric(scoped["current_trades"], errors="coerce").fillna(0.0) >= MIN_CURRENT_TRADES)
        & (pd.to_numeric(scoped["old_trades"], errors="coerce").fillna(0.0) >= MIN_OLD_TRADES)
        & (pd.to_numeric(scoped["months_with_trades"], errors="coerce").fillna(0.0) >= MIN_MONTHS_WITH_TRADES)
        & (pd.to_numeric(scoped["max_month_share"], errors="coerce").fillna(1.0) <= MAX_MONTH_SHARE)
    ].copy()
    if scoped.empty:
        return scoped
    scoped["candidate_id"] = _candidate_signature(scoped)
    scoped["category_id"] = _category_signature(scoped)
    scoped["pool_score"] = (
        pd.to_numeric(scoped["combined_mean_return_pct"], errors="coerce").fillna(-1.0) * 120.0
        + pd.to_numeric(scoped["months_with_trades"], errors="coerce").fillna(0.0) * 0.8
        + pd.to_numeric(scoped["positive_months"], errors="coerce").fillna(0.0) * 0.5
        + pd.to_numeric(scoped["current_trades"], errors="coerce").fillna(0.0) * 0.08
        + pd.to_numeric(scoped["old_trades"], errors="coerce").fillna(0.0) * 0.12
        - pd.to_numeric(scoped["max_month_share"], errors="coerce").fillna(1.0) * 18.0
    )
    scoped = scoped.sort_values(
        ["pool_score", "combined_mean_return_pct", "months_with_trades", "current_trades", "old_trades"],
        ascending=[False, False, False, False, False],
    )
    return scoped.head(MAX_CANDIDATES_PER_SESSION).reset_index(drop=True)


def _select_events_for_candidates(scenario_events: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    if scenario_events.empty or candidates.empty:
        return pd.DataFrame()
    selected_frames: list[pd.DataFrame] = []
    for rank, candidate in enumerate(candidates.to_dict("records"), start=1):
        mask = (
            (scenario_events["session_id"].astype(str) == str(candidate["session_id"]))
            & (scenario_events["canonical_category"].astype(str) == str(candidate["canonical_category"]))
            & (scenario_events["context_archetype"].astype(str) == str(candidate["context_archetype"]))
            & (scenario_events["impulse_archetype"].astype(str) == str(candidate["impulse_archetype"]))
            & (scenario_events["confirmation_archetype"].astype(str) == str(candidate["confirmation_archetype"]))
            & (scenario_events["wave_archetype"].astype(str) == str(candidate["wave_archetype"]))
            & (scenario_events["pre_accumulation_type"].astype(str) == str(candidate["pre_accumulation_type"]))
            & (scenario_events["trade_model_label"].astype(str) == str(candidate["trade_model_label"]))
            & scenario_events["trade_triggered"].fillna(False).astype(bool)
        )
        scoped = scenario_events[mask].copy()
        if scoped.empty:
            continue
        scoped["candidate_rank"] = int(rank)
        scoped["candidate_id"] = str(candidate["candidate_id"])
        scoped["category_id"] = str(candidate["category_id"])
        selected_frames.append(scoped)
    if not selected_frames:
        return pd.DataFrame()
    joined = pd.concat(selected_frames, ignore_index=True)
    joined = (
        joined.sort_values(
            ["dataset", "session_id", "symbol", "timestamp_ms", "candidate_rank", "trade_model_label"],
            ascending=[True, True, True, True, True, True],
        )
        .drop_duplicates(["dataset", "session_id", "symbol", "timestamp_ms"], keep="first")
        .reset_index(drop=True)
    )
    return joined


def _monthly(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["month_utc", "trades_count", "month_return_pct", "win_rate", "mean_return_pct"])
    return (
        frame.groupby("month_utc", as_index=False)
        .agg(
            trades_count=("exit_return_pct", "size"),
            month_return_pct=("exit_return_pct", "sum"),
            win_rate=("exit_return_pct", lambda values: float((pd.to_numeric(values, errors="coerce") > 0.0).mean()) if len(values) else 0.0),
            mean_return_pct=("exit_return_pct", "mean"),
        )
        .sort_values("month_utc")
        .reset_index(drop=True)
    )


def _portfolio_row(frame: pd.DataFrame, *, session_id: str, component_ids: list[str]) -> dict[str, object] | None:
    if frame.empty:
        return None
    calendar_months = _calendar_months_from_frame(frame)
    summary = _summarize_events(frame, calendar_months=calendar_months)
    if summary is None:
        return None
    concentration = _build_concentration_metrics(frame, calendar_months=calendar_months)
    symbol_concentration = _build_symbol_concentration_metrics(frame, calendar_months=calendar_months)
    month_stability = _build_month_stability_metrics(frame, calendar_months=calendar_months)
    equity5, _ = _simulate_equity_risk_metrics(frame, calendar_months=calendar_months, risk_fraction=0.05)
    equity9, _ = _simulate_equity_risk_metrics(frame, calendar_months=calendar_months, risk_fraction=0.09)
    row = {
        "session_id": str(session_id),
        "components_count": int(len(component_ids)),
        "component_ids": " > ".join(component_ids),
        **summary,
        **concentration,
        **symbol_concentration,
        **month_stability,
        **equity5,
        "equity_9_annualized_return_pct": equity9.get("equity_annualized_return_pct"),
        "equity_9_max_drawdown_pct": equity9.get("equity_max_drawdown_pct"),
        "equity_9_positive_months_count": equity9.get("equity_positive_months_count"),
        "equity_9_stable_positive_months_count": equity9.get("equity_stable_positive_months_count"),
    }
    row["selection_score"] = _candidate_score(row)
    return row


def _search_session_portfolios(summary: pd.DataFrame, scenario_events: pd.DataFrame, session_id: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    pool = _candidate_pool(summary, session_id)
    combo_rows: list[dict[str, object]] = []
    monthly_frames: list[pd.DataFrame] = []
    event_frames: list[pd.DataFrame] = []
    if pool.empty:
        return pool, pd.DataFrame(), pd.DataFrame()
    indices = list(pool.index)
    for size in range(1, min(MAX_COMPONENTS, len(indices)) + 1):
        for combo in combinations(indices, size):
            selected = pool.loc[list(combo)].copy()
            if selected["category_id"].astype(str).nunique() != len(selected):
                continue
            events = _select_events_for_candidates(scenario_events, selected)
            if events.empty:
                continue
            component_ids = selected["candidate_id"].astype(str).tolist()
            row = _portfolio_row(events, session_id=session_id, component_ids=component_ids)
            if row is None:
                continue
            combo_id = f"{session_id}_L{size}_{len(combo_rows)+1}"
            row["combo_id"] = combo_id
            combo_rows.append(row)
            monthly = _monthly(events)
            if not monthly.empty:
                monthly["combo_id"] = combo_id
                monthly_frames.append(monthly)
            events = events.copy()
            events["combo_id"] = combo_id
            event_frames.append(events)
    combo_summary = pd.DataFrame(combo_rows)
    if combo_summary.empty:
        return pool, combo_summary, pd.DataFrame()
    combo_summary = combo_summary.sort_values(
        [
            "selection_score",
            "equity_annualized_return_pct",
            "mean_return_pct",
            "stable_positive_months_count",
            "top3_symbol_pnl_share",
        ],
        ascending=[False, False, False, False, True],
    ).reset_index(drop=True)
    top_ids = combo_summary["combo_id"].astype(str).head(10).tolist()
    monthly_summary = pd.concat(monthly_frames, ignore_index=True) if monthly_frames else pd.DataFrame()
    if not monthly_summary.empty:
        monthly_summary = monthly_summary[monthly_summary["combo_id"].astype(str).isin(top_ids)].copy()
    events_summary = pd.concat(event_frames, ignore_index=True) if event_frames else pd.DataFrame()
    if not events_summary.empty:
        events_summary = events_summary[events_summary["combo_id"].astype(str).isin(top_ids)].copy()
    return pool, combo_summary, monthly_summary if not monthly_summary.empty else events_summary


def _build_report(
    *,
    candidate_pools: dict[str, pd.DataFrame],
    combo_summaries: dict[str, pd.DataFrame],
    report_path: Path,
) -> None:
    lines = [
        "# Europe/America Long Portfolio Lab",
        "",
        "Комбинаторный поиск портфелей из 1-3 natural long-категорий вне Азии.",
        "",
    ]
    for session_id in SESSIONS:
        lines.extend([f"## {session_id.title()} Candidate Pool", ""])
        pool = candidate_pools.get(session_id, pd.DataFrame())
        lines.append(
            _frame_to_markdown(
                pool,
                columns=[
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
                    "months_with_trades",
                    "positive_months",
                    "max_month_share",
                ],
                limit=18,
            )
            if not pool.empty
            else "_empty_"
        )
        lines.extend(["", f"## {session_id.title()} Best Portfolios", ""])
        combos = combo_summaries.get(session_id, pd.DataFrame())
        lines.append(
            _frame_to_markdown(
                combos,
                columns=[
                    "combo_id",
                    "components_count",
                    "component_ids",
                    "trades_per_year",
                    "mean_return_pct",
                    "win_rate",
                    "annualized_unit_pnl_pct",
                    "max_drawdown_pct",
                    "positive_months_count",
                    "stable_positive_months_count",
                    "equity_annualized_return_pct",
                    "equity_max_drawdown_pct",
                    "equity_9_annualized_return_pct",
                    "equity_9_max_drawdown_pct",
                    "top3_trade_pnl_share",
                    "top3_symbol_pnl_share",
                    "best_month_pnl_share",
                    "selection_score",
                ],
                limit=10,
            )
            if not combos.empty
            else "_empty_"
        )
        lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")


def run(*, logger: logging.Logger | None = None) -> dict[str, Path]:
    active_logger = logger or module_logger
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = _load_csv(INPUT_DIR / "supported_positive_with_months.csv")
    scenario_events = _load_csv(INPUT_DIR / "scenario_events.csv")

    candidate_pools: dict[str, pd.DataFrame] = {}
    combo_summaries: dict[str, pd.DataFrame] = {}
    artifacts: dict[str, Path] = {}

    for session_id in SESSIONS:
        active_logger.info("session-long-portfolio-lab: session=%s", session_id)
        pool, combos, monthly_or_events = _search_session_portfolios(summary, scenario_events, session_id)
        candidate_pools[session_id] = pool
        combo_summaries[session_id] = combos
        pool_path = OUTPUT_DIR / f"{session_id}_candidate_pool.csv"
        combos_path = OUTPUT_DIR / f"{session_id}_portfolio_summary.csv"
        monthly_path = OUTPUT_DIR / f"{session_id}_portfolio_monthly_top10.csv"
        pool.to_csv(pool_path, index=False)
        combos.to_csv(combos_path, index=False)
        monthly_or_events.to_csv(monthly_path, index=False)
        artifacts[f"{session_id}_candidate_pool"] = pool_path
        artifacts[f"{session_id}_portfolio_summary"] = combos_path
        artifacts[f"{session_id}_portfolio_monthly"] = monthly_path

    report_path = OUTPUT_DIR / "report.md"
    _build_report(candidate_pools=candidate_pools, combo_summaries=combo_summaries, report_path=report_path)
    artifacts["report"] = report_path
    return artifacts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
