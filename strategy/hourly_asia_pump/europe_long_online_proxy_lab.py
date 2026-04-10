from __future__ import annotations

import logging
from itertools import combinations
from pathlib import Path

import pandas as pd

from strategy.hourly_asia_pump.anomaly_category_lab import _frame_to_markdown
from strategy.hourly_asia_pump.session_long_portfolio_lab import _portfolio_row

module_logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
INPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "session_long_nature_lab"
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "europe_long_online_proxy_lab"

SESSION_ID = "europe"
MAX_COMPONENTS = 3
MAX_CANDIDATES = 18

MIN_TRAIN_TRADES = 8
MIN_TRAIN_MONTHS = 6
MIN_TRAIN_POSITIVE_MONTHS = 4
MAX_TRAIN_MONTH_SHARE = 0.60


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, low_memory=False)


def _triggered_events() -> pd.DataFrame:
    frame = _load_csv(INPUT_DIR / "scenario_events.csv")
    frame = frame[
        (frame["session_id"].astype(str) == SESSION_ID)
        & frame["trade_triggered"].fillna(False).astype(bool)
    ].copy()
    return frame.reset_index(drop=True)


def _acc_group(value: object) -> str:
    key = str(value).strip().lower()
    if key in {"accumulation_warm", "accumulation_clean"}:
        return "accumulating"
    if key in {"distribution_like"}:
        return "distribution"
    if key in {"volume_ramp_hot"}:
        return "hot_ramp"
    return "neutral"


def _add_online_proxy_columns(frame: pd.DataFrame) -> pd.DataFrame:
    enriched = frame.copy()
    enriched["online_acc_group"] = enriched["pre_accumulation_type"].map(_acc_group)
    enriched["online_proxy_id"] = (
        enriched["session_id"].astype(str)
        + "|"
        + enriched["context_archetype"].astype(str)
        + "|"
        + enriched["impulse_archetype"].astype(str)
        + "|"
        + enriched["online_acc_group"].astype(str)
        + "|"
        + enriched["anomaly_bucket"].astype(str)
        + "|"
        + enriched["volume_bucket"].astype(str)
    )
    enriched["candidate_id"] = enriched["online_proxy_id"].astype(str) + "|" + enriched["trade_model_label"].astype(str)
    return enriched


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


def _train_summary(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    group_cols = [
        "session_id",
        "context_archetype",
        "impulse_archetype",
        "online_acc_group",
        "anomaly_bucket",
        "volume_bucket",
        "trade_model_label",
        "online_proxy_id",
        "candidate_id",
    ]
    rows: list[dict[str, object]] = []
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
                "context_archetype": keys[1],
                "impulse_archetype": keys[2],
                "online_acc_group": keys[3],
                "anomaly_bucket": keys[4],
                "volume_bucket": keys[5],
                "trade_model_label": keys[6],
                "online_proxy_id": keys[7],
                "candidate_id": keys[8],
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
    summary["pool_score"] = (
        pd.to_numeric(summary["train_mean_return_pct"], errors="coerce").fillna(-1.0) * 140.0
        + pd.to_numeric(summary["train_trades"], errors="coerce").fillna(0.0) * 0.10
        + pd.to_numeric(summary["train_months_with_trades"], errors="coerce").fillna(0.0) * 1.0
        + pd.to_numeric(summary["train_positive_months"], errors="coerce").fillna(0.0) * 0.75
        - pd.to_numeric(summary["train_max_month_share"], errors="coerce").fillna(1.0) * 16.0
    )
    return summary


def _candidate_pool(train_summary: pd.DataFrame) -> pd.DataFrame:
    if train_summary.empty:
        return train_summary
    scoped = train_summary[
        (pd.to_numeric(train_summary["train_trades"], errors="coerce").fillna(0.0) >= MIN_TRAIN_TRADES)
        & (pd.to_numeric(train_summary["train_months_with_trades"], errors="coerce").fillna(0.0) >= MIN_TRAIN_MONTHS)
        & (pd.to_numeric(train_summary["train_positive_months"], errors="coerce").fillna(0.0) >= MIN_TRAIN_POSITIVE_MONTHS)
        & (pd.to_numeric(train_summary["train_max_month_share"], errors="coerce").fillna(1.0) <= MAX_TRAIN_MONTH_SHARE)
        & (pd.to_numeric(train_summary["train_mean_return_pct"], errors="coerce").fillna(-1.0) > 0.0)
    ].copy()
    if scoped.empty:
        return scoped
    scoped = scoped.sort_values(
        ["pool_score", "train_mean_return_pct", "train_trades", "train_positive_months"],
        ascending=[False, False, False, False],
    )
    return scoped.head(MAX_CANDIDATES).reset_index(drop=True)


def _select_events_for_candidates(events: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    if events.empty or candidates.empty:
        return pd.DataFrame()
    selected_frames: list[pd.DataFrame] = []
    for rank, candidate in enumerate(candidates.to_dict("records"), start=1):
        mask = (
            (events["online_proxy_id"].astype(str) == str(candidate["online_proxy_id"]))
            & (events["trade_model_label"].astype(str) == str(candidate["trade_model_label"]))
        )
        scoped = events[mask].copy()
        if scoped.empty:
            continue
        scoped["candidate_rank"] = int(rank)
        scoped["candidate_id"] = str(candidate["candidate_id"])
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


def _build_combo_grid(pool: pd.DataFrame) -> list[tuple[int, ...]]:
    if pool.empty:
        return []
    combos: list[tuple[int, ...]] = []
    indices = list(pool.index)
    for size in range(1, min(MAX_COMPONENTS, len(indices)) + 1):
        for combo in combinations(indices, size):
            selected = pool.loc[list(combo)]
            if selected["online_proxy_id"].astype(str).nunique() != len(selected):
                continue
            combos.append(combo)
    return combos


def _prefix_row(row: dict[str, object] | None, prefix: str) -> dict[str, object]:
    if not row:
        return {}
    return {f"{prefix}_{key}": value for key, value in row.items() if key not in {"session_id", "components_count", "component_ids"}}


def _oos_split(train_events: pd.DataFrame, test_events: pd.DataFrame, *, train_name: str, test_name: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    train_summary = _train_summary(train_events)
    pool = _candidate_pool(train_summary)
    rows: list[dict[str, object]] = []
    monthly_frames: list[pd.DataFrame] = []
    if pool.empty:
        return pool, pd.DataFrame(), pd.DataFrame()
    for combo_idx, combo in enumerate(_build_combo_grid(pool), start=1):
        selected = pool.loc[list(combo)].copy()
        component_ids = selected["candidate_id"].astype(str).tolist()

        combo_train = _select_events_for_candidates(train_events, selected)
        if combo_train.empty:
            continue
        combo_test = _select_events_for_candidates(test_events, selected)

        train_row = _portfolio_row(combo_train, session_id=SESSION_ID, component_ids=component_ids)
        test_row = _portfolio_row(combo_test, session_id=SESSION_ID, component_ids=component_ids) if not combo_test.empty else None
        if train_row is None:
            continue

        combo_id = f"{SESSION_ID}_{train_name}_to_{test_name}_{combo_idx}"
        rows.append(
            {
                "session_id": SESSION_ID,
                "train_period": train_name,
                "test_period": test_name,
                "combo_id": combo_id,
                "components_count": int(len(component_ids)),
                "component_ids": " > ".join(component_ids),
                **_prefix_row(train_row, "train"),
                **_prefix_row(test_row, "test"),
            }
        )
        if not combo_test.empty:
            monthly = _monthly(combo_test)
            if not monthly.empty:
                monthly["combo_id"] = combo_id
                monthly_frames.append(monthly)
    summary = pd.DataFrame(rows)
    monthly = pd.concat(monthly_frames, ignore_index=True) if monthly_frames else pd.DataFrame()
    if not summary.empty:
        summary = summary.sort_values(
            ["train_selection_score", "train_equity_annualized_return_pct", "train_mean_return_pct"],
            ascending=[False, False, False],
        ).reset_index(drop=True)
    return pool, summary, monthly


def _build_report(
    *,
    old_to_current_pool: pd.DataFrame,
    old_to_current_summary: pd.DataFrame,
    current_to_old_pool: pd.DataFrame,
    current_to_old_summary: pd.DataFrame,
    report_path: Path,
) -> None:
    lines = [
        "# Europe Long Online Proxy Lab",
        "",
        "This lab replaces post-event category fields with online-only proxy groups built from anomaly-close information:",
        "",
        "- `context_archetype`",
        "- `impulse_archetype`",
        "- `pre_accumulation_type` collapsed into `online_acc_group`",
        "- `anomaly_bucket`",
        "- `volume_bucket`",
        "",
        "No `confirmation_*`, `wave_*`, or post-15m/post-30m features are used for the proxy grouping.",
        "",
        "## Old -> Current Candidate Pool",
        "",
        _frame_to_markdown(
            old_to_current_pool,
            columns=[
                "context_archetype",
                "impulse_archetype",
                "online_acc_group",
                "anomaly_bucket",
                "volume_bucket",
                "trade_model_label",
                "train_trades",
                "train_mean_return_pct",
                "train_months_with_trades",
                "train_positive_months",
                "train_max_month_share",
            ],
            limit=18,
        )
        if not old_to_current_pool.empty
        else "_empty_",
        "",
        "## Old -> Current Best Combos",
        "",
        _frame_to_markdown(
            old_to_current_summary,
            columns=[
                "combo_id",
                "components_count",
                "component_ids",
                "train_trades_per_year",
                "train_mean_return_pct",
                "train_win_rate",
                "train_annualized_unit_pnl_pct",
                "test_trades_per_year",
                "test_mean_return_pct",
                "test_win_rate",
                "test_annualized_unit_pnl_pct",
                "test_equity_annualized_return_pct",
                "test_equity_max_drawdown_pct",
                "test_top3_symbol_pnl_share",
                "test_best_month_pnl_share",
            ],
            limit=12,
        )
        if not old_to_current_summary.empty
        else "_empty_",
        "",
        "## Current -> Old Candidate Pool",
        "",
        _frame_to_markdown(
            current_to_old_pool,
            columns=[
                "context_archetype",
                "impulse_archetype",
                "online_acc_group",
                "anomaly_bucket",
                "volume_bucket",
                "trade_model_label",
                "train_trades",
                "train_mean_return_pct",
                "train_months_with_trades",
                "train_positive_months",
                "train_max_month_share",
            ],
            limit=18,
        )
        if not current_to_old_pool.empty
        else "_empty_",
        "",
        "## Current -> Old Best Combos",
        "",
        _frame_to_markdown(
            current_to_old_summary,
            columns=[
                "combo_id",
                "components_count",
                "component_ids",
                "train_trades_per_year",
                "train_mean_return_pct",
                "train_win_rate",
                "train_annualized_unit_pnl_pct",
                "test_trades_per_year",
                "test_mean_return_pct",
                "test_win_rate",
                "test_annualized_unit_pnl_pct",
                "test_equity_annualized_return_pct",
                "test_equity_max_drawdown_pct",
                "test_top3_symbol_pnl_share",
                "test_best_month_pnl_share",
            ],
            limit=12,
        )
        if not current_to_old_summary.empty
        else "_empty_",
        "",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")


def run(*, logger: logging.Logger | None = None) -> dict[str, Path]:
    active_logger = logger or module_logger
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    events = _add_online_proxy_columns(_triggered_events())
    current = events[events["dataset"].astype(str) == "current"].copy()
    old = events[events["dataset"].astype(str) == "old"].copy()
    active_logger.info("europe-long-online-proxy: current=%s old=%s", len(current), len(old))

    old_pool, old_summary, old_monthly = _oos_split(old, current, train_name="old", test_name="current")
    current_pool, current_summary, current_monthly = _oos_split(current, old, train_name="current", test_name="old")

    outputs = {
        "old_pool": OUTPUT_DIR / "old_to_current_candidate_pool.csv",
        "old_summary": OUTPUT_DIR / "old_to_current_summary.csv",
        "old_monthly": OUTPUT_DIR / "old_to_current_monthly_top.csv",
        "current_pool": OUTPUT_DIR / "current_to_old_candidate_pool.csv",
        "current_summary": OUTPUT_DIR / "current_to_old_summary.csv",
        "current_monthly": OUTPUT_DIR / "current_to_old_monthly_top.csv",
        "report": OUTPUT_DIR / "report.md",
    }
    old_pool.to_csv(outputs["old_pool"], index=False)
    old_summary.to_csv(outputs["old_summary"], index=False)
    old_monthly.to_csv(outputs["old_monthly"], index=False)
    current_pool.to_csv(outputs["current_pool"], index=False)
    current_summary.to_csv(outputs["current_summary"], index=False)
    current_monthly.to_csv(outputs["current_monthly"], index=False)
    _build_report(
        old_to_current_pool=old_pool,
        old_to_current_summary=old_summary,
        current_to_old_pool=current_pool,
        current_to_old_summary=current_summary,
        report_path=outputs["report"],
    )
    return outputs


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    print(run())
