from __future__ import annotations

import logging
from itertools import combinations
from pathlib import Path

import pandas as pd

from strategy.hourly_asia_pump.anomaly_category_lab import _frame_to_markdown
from strategy.hourly_asia_pump.session_long_nature_lab import _attach_pre_accumulation_type
from strategy.hourly_asia_pump.session_long_portfolio_lab import _portfolio_row

module_logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
CURRENT_TRADE_EVENTS = REPO_ROOT / ".output" / "results" / "research" / "hourly_asia_pump_static_combo" / "latest_report" / "source_inputs" / "trade_model_events.csv"
OLD_TRADE_EVENTS = REPO_ROOT / ".output" / "results_prev_year_5m" / "research" / "hourly_asia_pump_prev_year_5m" / "trade_model_events.csv"
ANOMALY_DB = REPO_ROOT / ".output" / "results_prev_year_5m" / "anomaly_category_lab" / "anomaly_feature_database.csv"
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "asia_long_online_tradeable_lab"

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


def _normalize_trade_events(frame: pd.DataFrame, *, dataset: str) -> pd.DataFrame:
    enriched = frame.copy()
    if dataset == "current":
        enriched = enriched[enriched["timeframe"].astype(str) == "5m"].copy()
    else:
        enriched = enriched[enriched["timeframe"].astype(str) == "5m"].copy()

    if "upper_wick_frac" not in enriched.columns:
        trigger_open = pd.to_numeric(enriched["trigger_open"], errors="coerce")
        trigger_high = pd.to_numeric(enriched["trigger_high"], errors="coerce")
        trigger_low = pd.to_numeric(enriched["trigger_low"], errors="coerce")
        trigger_close = pd.to_numeric(enriched["trigger_close"], errors="coerce")
        trigger_range = (trigger_high - trigger_low).replace(0.0, pd.NA)
        top = pd.concat([trigger_open, trigger_close], axis=1).max(axis=1)
        bottom = pd.concat([trigger_open, trigger_close], axis=1).min(axis=1)
        enriched["upper_wick_frac"] = (trigger_high - top) / trigger_range
        enriched["lower_wick_frac"] = (bottom - trigger_low) / trigger_range
        enriched["body_frac"] = (trigger_close - trigger_open).abs() / trigger_range

    rename_map = {}
    if "next_bar_open_price" in enriched.columns:
        rename_map["next_bar_open_price"] = "next_open"
    if "next_bar_high_price" in enriched.columns:
        rename_map["next_bar_high_price"] = "next_high"
    if "next_bar_low_price" in enriched.columns:
        rename_map["next_bar_low_price"] = "next_low"
    if "next_bar_close_price" in enriched.columns:
        rename_map["next_bar_close_price"] = "next_close"
    if "next_bar_return_pct" in enriched.columns:
        rename_map["next_bar_return_pct"] = "next_return_pct"
    if "next_bar_pullback_frac" in enriched.columns:
        rename_map["next_bar_pullback_frac"] = "next_pullback_frac"
    if rename_map:
        enriched = enriched.rename(columns=rename_map)

    if "timestamp_utc" not in enriched.columns:
        timestamp_ms = pd.to_numeric(enriched["timestamp_ms"], errors="coerce")
        enriched["timestamp_utc"] = pd.to_datetime(timestamp_ms, unit="ms", utc=True, errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
    if "month_utc" not in enriched.columns:
        timestamp_ms = pd.to_numeric(enriched["timestamp_ms"], errors="coerce")
        enriched["month_utc"] = pd.to_datetime(timestamp_ms, unit="ms", utc=True, errors="coerce").dt.strftime("%Y-%m")

    enriched["dataset"] = dataset
    enriched["session_id"] = "asia"
    enriched["side"] = "long"
    enriched["signal_key"] = (
        enriched["dataset"].astype(str)
        + "|asia|"
        + enriched["symbol"].astype(str)
        + "|"
        + pd.to_numeric(enriched["timestamp_ms"], errors="coerce").astype("Int64").astype(str)
    )
    return enriched


def _merge_pre_features(events: pd.DataFrame) -> pd.DataFrame:
    anomaly = _load_csv(ANOMALY_DB)
    anomaly = anomaly[
        (anomaly["session_id"].astype(str) == "asia")
        & (anomaly["market_side_hint"].astype(str) == "long")
    ].copy()
    keep = [
        "dataset",
        "symbol",
        "timestamp_ms",
        "pre_green_count_30m",
        "pre_red_count_30m",
        "pre_dir_body_sum_pct_30m",
        "pre_abs_body_sum_pct_30m",
        "pre_max_range_pct_30m",
        "pre_avg_volume_ratio_30m",
    ]
    merged = events.merge(
        anomaly[keep].drop_duplicates(["dataset", "symbol", "timestamp_ms"]),
        on=["dataset", "symbol", "timestamp_ms"],
        how="left",
    )
    merged = _attach_pre_accumulation_type(merged)
    return merged


def _context_archetype(frame: pd.DataFrame) -> pd.Series:
    drift = pd.to_numeric(frame["pre_base_drift_pct_60m"], errors="coerce").fillna(0.0)
    base_range = pd.to_numeric(frame["pre_base_range_pct_60m"], errors="coerce").fillna(0.0)
    result = pd.Series("context_warm", index=frame.index, dtype="object")
    result.loc[(drift <= 0.015) & (base_range <= 0.03)] = "context_coiled"
    result.loc[(drift >= 0.04) | (base_range >= 0.08)] = "context_overheated"
    return result


def _impulse_archetype(frame: pd.DataFrame) -> pd.Series:
    body_frac = pd.to_numeric(frame["body_frac"], errors="coerce").fillna(0.0)
    upper_wick_frac = pd.to_numeric(frame["upper_wick_frac"], errors="coerce").fillna(0.0)
    close_to_high = pd.to_numeric(frame["close_to_high_frac"], errors="coerce").fillna(1.0)
    result = pd.Series("impulse_balanced", index=frame.index, dtype="object")
    result.loc[(body_frac >= 0.65) & (upper_wick_frac <= 0.15)] = "impulse_body_drive"
    result.loc[(upper_wick_frac >= 0.25) | (close_to_high >= 0.15)] = "impulse_wicky_spike"
    return result


def _bucket(series: pd.Series, low_cut: float, high_cut: float, low_label: str, mid_label: str, high_label: str) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce").fillna(0.0)
    result = pd.Series(mid_label, index=series.index, dtype="object")
    result.loc[numeric <= low_cut] = low_label
    result.loc[numeric > high_cut] = high_label
    return result


def _entry_pullback_bucket(frame: pd.DataFrame) -> pd.Series:
    return _bucket(frame["pre_entry_pullback_frac"], 0.10, 0.30, "pullback_tight", "pullback_mid", "pullback_deep")


def _entry_red_volume_bucket(frame: pd.DataFrame) -> pd.Series:
    return _bucket(frame["pre_entry_red_volume_frac"], 0.35, 0.80, "seller_light", "seller_mid", "seller_heavy")


def _entry_hold_bucket(frame: pd.DataFrame) -> pd.Series:
    numeric = pd.to_numeric(frame["pre_entry_low_frac_of_trigger_range"], errors="coerce").fillna(1.0)
    result = pd.Series("hold_mid", index=frame.index, dtype="object")
    result.loc[numeric >= 0.75] = "hold_high"
    result.loc[numeric < 0.40] = "hold_deep"
    return result


def _add_online_features(frame: pd.DataFrame) -> pd.DataFrame:
    enriched = frame.copy()
    enriched["context_archetype"] = _context_archetype(enriched)
    enriched["impulse_archetype"] = _impulse_archetype(enriched)
    enriched["anomaly_bucket"] = _bucket(enriched["trigger_return_pct"], 0.05, 0.08, "anomaly_small", "anomaly_mid", "anomaly_hot")
    enriched["volume_bucket"] = _bucket(enriched["volume_mult"], 8.0, 20.0, "volume_calm", "volume_mid", "volume_hot")
    enriched["acc_group"] = (
        enriched["pre_accumulation_type"]
        .astype(str)
        .map(
            {
                "accumulation_warm": "accumulating",
                "accumulation_clean": "accumulating",
                "distribution_like": "distribution",
                "volume_ramp_hot": "hot_ramp",
            }
        )
        .fillna("neutral")
    )
    enriched["entry_pullback_bucket"] = _entry_pullback_bucket(enriched)
    enriched["entry_red_volume_bucket"] = _entry_red_volume_bucket(enriched)
    enriched["entry_hold_bucket"] = _entry_hold_bucket(enriched)

    enriched["base_proxy_id"] = (
        enriched["context_archetype"].astype(str)
        + "|"
        + enriched["impulse_archetype"].astype(str)
        + "|"
        + enriched["acc_group"].astype(str)
        + "|"
        + enriched["anomaly_bucket"].astype(str)
        + "|"
        + enriched["volume_bucket"].astype(str)
    )
    enriched["entry_proxy_id"] = (
        enriched["base_proxy_id"].astype(str)
        + "|"
        + enriched["entry_pullback_bucket"].astype(str)
        + "|"
        + enriched["entry_red_volume_bucket"].astype(str)
        + "|"
        + enriched["entry_hold_bucket"].astype(str)
    )
    return enriched


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


def _build_train_summary(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for level_name, proxy_column in (("base", "base_proxy_id"), ("entry", "entry_proxy_id")):
        group_cols = [proxy_column, "trade_model_label"]
        for keys, scoped in frame.groupby(group_cols, sort=True):
            monthly = _monthly(scoped)
            total_return = float(pd.to_numeric(scoped["exit_return_pct"], errors="coerce").sum())
            best_month_return = float(pd.to_numeric(monthly["month_return_pct"], errors="coerce").max()) if not monthly.empty else 0.0
            max_month_share = 1.0
            if total_return > 0.0 and best_month_return > 0.0:
                max_month_share = best_month_return / total_return
            rows.append(
                {
                    "level_name": level_name,
                    "proxy_id": keys[0],
                    "trade_model_label": keys[1],
                    "candidate_id": f"{level_name}|{keys[0]}|{keys[1]}",
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
        pd.to_numeric(summary["train_mean_return_pct"], errors="coerce").fillna(-1.0) * 160.0
        + pd.to_numeric(summary["train_trades"], errors="coerce").fillna(0.0) * 0.08
        + pd.to_numeric(summary["train_months_with_trades"], errors="coerce").fillna(0.0) * 1.2
        + pd.to_numeric(summary["train_positive_months"], errors="coerce").fillna(0.0) * 0.8
        - pd.to_numeric(summary["train_max_month_share"], errors="coerce").fillna(1.0) * 18.0
    )
    return summary


def _candidate_pool(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return summary
    scoped = summary[
        (pd.to_numeric(summary["train_trades"], errors="coerce").fillna(0.0) >= MIN_TRAIN_TRADES)
        & (pd.to_numeric(summary["train_months_with_trades"], errors="coerce").fillna(0.0) >= MIN_TRAIN_MONTHS)
        & (pd.to_numeric(summary["train_positive_months"], errors="coerce").fillna(0.0) >= MIN_TRAIN_POSITIVE_MONTHS)
        & (pd.to_numeric(summary["train_max_month_share"], errors="coerce").fillna(1.0) <= MAX_TRAIN_MONTH_SHARE)
        & (pd.to_numeric(summary["train_mean_return_pct"], errors="coerce").fillna(-1.0) > 0.0)
    ].copy()
    if scoped.empty:
        return scoped
    scoped = scoped.sort_values(
        ["pool_score", "train_mean_return_pct", "train_trades", "train_positive_months"],
        ascending=[False, False, False, False],
    )
    return scoped.head(MAX_CANDIDATES).reset_index(drop=True)


def _select_events(events: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    if events.empty or candidates.empty:
        return pd.DataFrame()
    frames: list[pd.DataFrame] = []
    for rank, candidate in enumerate(candidates.to_dict("records"), start=1):
        proxy_column = "base_proxy_id" if str(candidate["level_name"]) == "base" else "entry_proxy_id"
        mask = (
            (events[proxy_column].astype(str) == str(candidate["proxy_id"]))
            & (events["trade_model_label"].astype(str) == str(candidate["trade_model_label"]))
            & events["trade_triggered"].fillna(False).astype(bool)
        )
        scoped = events[mask].copy()
        if scoped.empty:
            continue
        scoped["candidate_rank"] = int(rank)
        scoped["candidate_id"] = str(candidate["candidate_id"])
        scoped["family_id"] = str(candidate["level_name"]) + "|" + str(candidate["proxy_id"])
        frames.append(scoped)
    if not frames:
        return pd.DataFrame()
    joined = pd.concat(frames, ignore_index=True)
    joined = (
        joined.sort_values(["dataset", "symbol", "timestamp_ms", "candidate_rank", "trade_model_label"], ascending=[True, True, True, True, True])
        .drop_duplicates(["dataset", "symbol", "timestamp_ms"], keep="first")
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
            if selected["family_id"].astype(str).nunique() != len(selected):
                continue
            combos.append(combo)
    return combos


def _prefix_row(row: dict[str, object] | None, prefix: str) -> dict[str, object]:
    if not row:
        return {}
    return {f"{prefix}_{key}": value for key, value in row.items() if key not in {"session_id", "components_count", "component_ids"}}


def _oos_split(train_events: pd.DataFrame, test_events: pd.DataFrame, *, train_name: str, test_name: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    train_summary = _build_train_summary(train_events)
    pool = _candidate_pool(train_summary)
    if not pool.empty:
        pool = pool.copy()
        pool["family_id"] = pool["level_name"].astype(str) + "|" + pool["proxy_id"].astype(str)
    rows: list[dict[str, object]] = []
    if pool.empty:
        return pool, pd.DataFrame()
    for combo_idx, combo in enumerate(_build_combo_grid(pool), start=1):
        selected = pool.loc[list(combo)].copy()
        component_ids = selected["candidate_id"].astype(str).tolist()
        combo_train = _select_events(train_events, selected)
        if combo_train.empty:
            continue
        combo_test = _select_events(test_events, selected)
        train_row = _portfolio_row(combo_train, session_id="asia", component_ids=component_ids)
        test_row = _portfolio_row(combo_test, session_id="asia", component_ids=component_ids) if not combo_test.empty else None
        if train_row is None:
            continue
        rows.append(
            {
                "train_period": train_name,
                "test_period": test_name,
                "combo_id": f"asia_{train_name}_to_{test_name}_{combo_idx}",
                "components_count": int(len(component_ids)),
                "component_ids": " > ".join(component_ids),
                **_prefix_row(train_row, "train"),
                **_prefix_row(test_row, "test"),
            }
        )
    summary = pd.DataFrame(rows)
    if not summary.empty:
        summary = summary.sort_values(
            ["train_selection_score", "train_equity_annualized_return_pct", "train_mean_return_pct"],
            ascending=[False, False, False],
        ).reset_index(drop=True)
    return pool, summary


def _build_report(old_pool: pd.DataFrame, old_summary: pd.DataFrame, current_pool: pd.DataFrame, current_summary: pd.DataFrame, report_path: Path) -> None:
    lines = [
        "# Asia Long Online Tradeable Lab",
        "",
        "This search uses only information available by entry time.",
        "",
        "Online families combine:",
        "- anomaly-close context",
        "- anomaly-close impulse shape",
        "- pre-pump accumulation state",
        "- anomaly size and volume regime",
        "- pre-entry pullback / seller-pressure / hold buckets",
        "",
        "## Old -> Current Candidate Pool",
        "",
        _frame_to_markdown(
            old_pool,
            columns=[
                "level_name",
                "proxy_id",
                "trade_model_label",
                "train_trades",
                "train_mean_return_pct",
                "train_months_with_trades",
                "train_positive_months",
                "train_max_month_share",
            ],
            limit=18,
        )
        if not old_pool.empty
        else "_empty_",
        "",
        "## Old -> Current Best Combos",
        "",
        _frame_to_markdown(
            old_summary,
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
        if not old_summary.empty
        else "_empty_",
        "",
        "## Current -> Old Candidate Pool",
        "",
        _frame_to_markdown(
            current_pool,
            columns=[
                "level_name",
                "proxy_id",
                "trade_model_label",
                "train_trades",
                "train_mean_return_pct",
                "train_months_with_trades",
                "train_positive_months",
                "train_max_month_share",
            ],
            limit=18,
        )
        if not current_pool.empty
        else "_empty_",
        "",
        "## Current -> Old Best Combos",
        "",
        _frame_to_markdown(
            current_summary,
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
        if not current_summary.empty
        else "_empty_",
        "",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")


def run(*, logger: logging.Logger | None = None) -> dict[str, Path]:
    active_logger = logger or module_logger
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    current = _normalize_trade_events(_load_csv(CURRENT_TRADE_EVENTS), dataset="current")
    old = _normalize_trade_events(_load_csv(OLD_TRADE_EVENTS), dataset="old")
    combined = pd.concat([current, old], ignore_index=True)
    combined = _merge_pre_features(combined)
    combined = _add_online_features(combined)
    triggered = combined[combined["trade_triggered"].fillna(False).astype(bool)].copy()

    current_triggered = triggered[triggered["dataset"].astype(str) == "current"].copy()
    old_triggered = triggered[triggered["dataset"].astype(str) == "old"].copy()
    active_logger.info("asia-online-tradeable: current=%s old=%s", len(current_triggered), len(old_triggered))

    old_pool, old_summary = _oos_split(old_triggered, current_triggered, train_name="old", test_name="current")
    current_pool, current_summary = _oos_split(current_triggered, old_triggered, train_name="current", test_name="old")

    outputs = {
        "old_pool": OUTPUT_DIR / "old_to_current_candidate_pool.csv",
        "old_summary": OUTPUT_DIR / "old_to_current_summary.csv",
        "current_pool": OUTPUT_DIR / "current_to_old_candidate_pool.csv",
        "current_summary": OUTPUT_DIR / "current_to_old_summary.csv",
        "report": OUTPUT_DIR / "report.md",
    }
    old_pool.to_csv(outputs["old_pool"], index=False)
    old_summary.to_csv(outputs["old_summary"], index=False)
    current_pool.to_csv(outputs["current_pool"], index=False)
    current_summary.to_csv(outputs["current_summary"], index=False)
    _build_report(old_pool, old_summary, current_pool, current_summary, outputs["report"])
    return outputs


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    print(run())
