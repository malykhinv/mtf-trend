from __future__ import annotations

import logging
from itertools import combinations
from pathlib import Path

import pandas as pd

from strategy.hourly_asia_pump.anomaly_category_lab import _calendar_months_from_frame, _frame_to_markdown
from strategy.hourly_asia_pump.session_long_portfolio_lab import (
    _candidate_signature,
    _category_signature,
    _portfolio_row,
    _select_events_for_candidates,
)

module_logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
INPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "session_long_nature_lab"
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "session_long_oos_validation"

SESSIONS = ("europe", "america")
MAX_COMPONENTS = 3
MAX_CANDIDATES_PER_SESSION = 15

MIN_TRAIN_TRADES = 6
MIN_TRAIN_MONTHS = 6
MIN_TRAIN_POSITIVE_MONTHS = 5
MAX_TRAIN_MONTH_SHARE = 0.50


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, low_memory=False)


def _triggered_events(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    triggered = frame[frame["trade_triggered"].fillna(False).astype(bool)].copy()
    if triggered.empty:
        return triggered
    triggered["candidate_id"] = _candidate_signature(triggered)
    triggered["category_id"] = _category_signature(triggered)
    return triggered.reset_index(drop=True)


def _monthly(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["month_utc", "trades_count", "month_return_pct", "win_rate", "mean_return_pct"])
    return (
        frame.groupby("month_utc", as_index=False)
        .agg(
            trades_count=("exit_return_pct", "size"),
            month_return_pct=("exit_return_pct", "sum"),
            win_rate=(
                "exit_return_pct",
                lambda values: float((pd.to_numeric(values, errors="coerce") > 0.0).mean()) if len(values) else 0.0,
            ),
            mean_return_pct=("exit_return_pct", "mean"),
        )
        .sort_values("month_utc")
        .reset_index(drop=True)
    )


def _candidate_train_summary(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
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
    for keys, scoped in frame.groupby(group_cols, sort=True):
        monthly = _monthly(scoped)
        total_return = float(pd.to_numeric(scoped["exit_return_pct"], errors="coerce").sum())
        best_month_return = float(pd.to_numeric(monthly["month_return_pct"], errors="coerce").max()) if not monthly.empty else 0.0
        max_month_share = 1.0
        if total_return > 0.0 and best_month_return > 0.0:
            max_month_share = best_month_return / total_return
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
                "train_trades": int(len(scoped)),
                "train_mean_return_pct": float(pd.to_numeric(scoped["exit_return_pct"], errors="coerce").mean()),
                "train_months_with_trades": int(monthly["month_utc"].nunique()) if not monthly.empty else 0,
                "train_positive_months": int((pd.to_numeric(monthly["month_return_pct"], errors="coerce") > 0.0).sum()) if not monthly.empty else 0,
                "train_max_month_share": float(max_month_share),
            }
        )
    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary
    summary["candidate_id"] = _candidate_signature(summary)
    summary["category_id"] = _category_signature(summary)
    summary["pool_score"] = (
        pd.to_numeric(summary["train_mean_return_pct"], errors="coerce").fillna(-1.0) * 140.0
        + pd.to_numeric(summary["train_trades"], errors="coerce").fillna(0.0) * 0.12
        + pd.to_numeric(summary["train_months_with_trades"], errors="coerce").fillna(0.0) * 1.0
        + pd.to_numeric(summary["train_positive_months"], errors="coerce").fillna(0.0) * 0.75
        - pd.to_numeric(summary["train_max_month_share"], errors="coerce").fillna(1.0) * 20.0
    )
    return summary


def _candidate_pool(train_summary: pd.DataFrame, session_id: str) -> pd.DataFrame:
    scoped = train_summary[train_summary["session_id"].astype(str) == str(session_id)].copy()
    if scoped.empty:
        return scoped
    scoped = scoped[
        (pd.to_numeric(scoped["train_trades"], errors="coerce").fillna(0.0) >= MIN_TRAIN_TRADES)
        & (pd.to_numeric(scoped["train_months_with_trades"], errors="coerce").fillna(0.0) >= MIN_TRAIN_MONTHS)
        & (pd.to_numeric(scoped["train_positive_months"], errors="coerce").fillna(0.0) >= MIN_TRAIN_POSITIVE_MONTHS)
        & (pd.to_numeric(scoped["train_max_month_share"], errors="coerce").fillna(1.0) <= MAX_TRAIN_MONTH_SHARE)
        & (pd.to_numeric(scoped["train_mean_return_pct"], errors="coerce").fillna(-1.0) > 0.0)
    ].copy()
    if scoped.empty:
        return scoped
    scoped = scoped.sort_values(
        ["pool_score", "train_mean_return_pct", "train_trades", "train_positive_months"],
        ascending=[False, False, False, False],
    )
    return scoped.head(MAX_CANDIDATES_PER_SESSION).reset_index(drop=True)


def _build_combo_grid(pool: pd.DataFrame) -> list[tuple[int, ...]]:
    if pool.empty:
        return []
    combos: list[tuple[int, ...]] = []
    indices = list(pool.index)
    for size in range(1, min(MAX_COMPONENTS, len(indices)) + 1):
        for combo in combinations(indices, size):
            selected = pool.loc[list(combo)]
            if selected["category_id"].astype(str).nunique() != len(selected):
                continue
            combos.append(combo)
    return combos


def _prefix_row(row: dict[str, object] | None, prefix: str) -> dict[str, object]:
    if not row:
        return {}
    return {f"{prefix}_{key}": value for key, value in row.items() if key not in {"session_id", "components_count", "component_ids"}}


def _split_report_rows(
    *,
    session_id: str,
    train_name: str,
    test_name: str,
    train_events: pd.DataFrame,
    test_events: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    train_summary = _candidate_train_summary(train_events)
    pool = _candidate_pool(train_summary, session_id)
    rows: list[dict[str, object]] = []
    top_monthly_frames: list[pd.DataFrame] = []
    if pool.empty:
        return pool, pd.DataFrame()
    for combo_index, combo in enumerate(_build_combo_grid(pool), start=1):
        selected = pool.loc[list(combo)].copy()
        component_ids = selected["candidate_id"].astype(str).tolist()

        combo_train_events = _select_events_for_candidates(train_events, selected)
        if combo_train_events.empty:
            continue
        combo_test_events = _select_events_for_candidates(test_events, selected)

        train_row = _portfolio_row(combo_train_events, session_id=session_id, component_ids=component_ids)
        test_row = _portfolio_row(combo_test_events, session_id=session_id, component_ids=component_ids) if not combo_test_events.empty else None
        if train_row is None:
            continue

        row = {
            "session_id": str(session_id),
            "train_period": train_name,
            "test_period": test_name,
            "combo_id": f"{session_id}_{train_name}_to_{test_name}_{combo_index}",
            "components_count": int(len(component_ids)),
            "component_ids": " > ".join(component_ids),
            **_prefix_row(train_row, "train"),
            **_prefix_row(test_row, "test"),
        }
        rows.append(row)

    summary = pd.DataFrame(rows)
    if summary.empty:
        return pool, summary
    summary = summary.sort_values(
        ["train_selection_score", "train_equity_annualized_return_pct", "train_mean_return_pct"],
        ascending=[False, False, False],
    ).reset_index(drop=True)

    top_ids = summary["combo_id"].astype(str).head(5).tolist()
    for combo_id in top_ids:
        selected_ids = summary.loc[summary["combo_id"].astype(str) == combo_id, "component_ids"].iloc[0].split(" > ")
        selected = pool[pool["candidate_id"].astype(str).isin(selected_ids)].copy()
        monthly = _monthly(_select_events_for_candidates(test_events, selected))
        if monthly.empty:
            continue
        monthly["combo_id"] = combo_id
        top_monthly_frames.append(monthly)
    monthly_frame = pd.concat(top_monthly_frames, ignore_index=True) if top_monthly_frames else pd.DataFrame()
    return pool, summary, monthly_frame


def _build_report(
    rows_by_split: dict[str, pd.DataFrame],
    pools_by_split: dict[str, pd.DataFrame],
    report_path: Path,
) -> None:
    lines = [
        "# Session Long Untouched OOS Validation",
        "",
        "This report freezes natural category plus trade-model combinations on one period and evaluates them on the untouched other period.",
        "",
        "Important caveat: categories still use post-event descriptive features such as next-bar quality and 30m wave match. This is a fairness check for category stability, not a live-tradable online strategy.",
        "",
    ]
    for split_name, pool in pools_by_split.items():
        lines.extend([f"## {split_name} Candidate Pool", ""])
        lines.append(
            _frame_to_markdown(
                pool,
                columns=[
                    "session_id",
                    "canonical_category",
                    "context_archetype",
                    "impulse_archetype",
                    "confirmation_archetype",
                    "wave_archetype",
                    "pre_accumulation_type",
                    "trade_model_label",
                    "train_trades",
                    "train_mean_return_pct",
                    "train_months_with_trades",
                    "train_positive_months",
                    "train_max_month_share",
                ],
                limit=12,
            )
            if not pool.empty
            else "_empty_"
        )
        lines.extend(["", f"## {split_name} Best Combos", ""])
        combos = rows_by_split.get(split_name, pd.DataFrame())
        lines.append(
            _frame_to_markdown(
                combos,
                columns=[
                    "combo_id",
                    "components_count",
                    "component_ids",
                    "train_trades_per_year",
                    "train_mean_return_pct",
                    "train_win_rate",
                    "train_annualized_unit_pnl_pct",
                    "train_equity_annualized_return_pct",
                    "train_equity_max_drawdown_pct",
                    "test_trades_per_year",
                    "test_mean_return_pct",
                    "test_win_rate",
                    "test_annualized_unit_pnl_pct",
                    "test_equity_annualized_return_pct",
                    "test_equity_max_drawdown_pct",
                    "test_top3_symbol_pnl_share",
                    "test_best_month_pnl_share",
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

    all_events = _triggered_events(_load_csv(INPUT_DIR / "scenario_events.csv"))
    if all_events.empty:
        raise RuntimeError("No triggered long scenario events found.")

    outputs: dict[str, Path] = {}
    pools_by_split: dict[str, pd.DataFrame] = {}
    rows_by_split: dict[str, pd.DataFrame] = {}

    for session_id in SESSIONS:
        scoped = all_events[all_events["session_id"].astype(str) == session_id].copy()
        current = scoped[scoped["dataset"].astype(str) == "current"].copy()
        old = scoped[scoped["dataset"].astype(str) == "old"].copy()
        active_logger.info("session-long-oos: session=%s current=%s old=%s", session_id, len(current), len(old))

        old_pool, old_to_current, old_monthly = _split_report_rows(
            session_id=session_id,
            train_name="old",
            test_name="current",
            train_events=old,
            test_events=current,
        )
        current_pool, current_to_old, current_monthly = _split_report_rows(
            session_id=session_id,
            train_name="current",
            test_name="old",
            train_events=current,
            test_events=old,
        )

        pool = pd.concat([old_pool.assign(split="old_to_current"), current_pool.assign(split="current_to_old")], ignore_index=True)
        summary = pd.concat([old_to_current, current_to_old], ignore_index=True)
        monthly = pd.concat([old_monthly, current_monthly], ignore_index=True)

        pool_path = OUTPUT_DIR / f"{session_id}_oos_candidate_pool.csv"
        summary_path = OUTPUT_DIR / f"{session_id}_oos_summary.csv"
        monthly_path = OUTPUT_DIR / f"{session_id}_oos_monthly_top.csv"

        pool.to_csv(pool_path, index=False)
        summary.to_csv(summary_path, index=False)
        monthly.to_csv(monthly_path, index=False)

        outputs[f"{session_id}_pool"] = pool_path
        outputs[f"{session_id}_summary"] = summary_path
        outputs[f"{session_id}_monthly"] = monthly_path

        pools_by_split[f"{session_id} old->current"] = old_pool
        pools_by_split[f"{session_id} current->old"] = current_pool
        rows_by_split[f"{session_id} old->current"] = old_to_current
        rows_by_split[f"{session_id} current->old"] = current_to_old

    report_path = OUTPUT_DIR / "report.md"
    _build_report(rows_by_split=rows_by_split, pools_by_split=pools_by_split, report_path=report_path)
    outputs["report"] = report_path
    return outputs


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    result = run()
    print(result)
