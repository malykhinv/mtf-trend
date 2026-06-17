"""Build diversified short-fade frontier portfolios from existing artifacts.

This is a portfolio-construction pass, not a new 365d discovery run.  It uses
already generated IS/OOS candidate screens, reconstructs candidate ledgers, and
adds sleeves only by marginal OOS signal ids beyond the frozen core.

The second 180d half has already been inspected in prior research.  Results are
therefore research-frontier candidates, not fresh OOS proof.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_tools.short_is_oos_max_verifier import (
    _apply_entry_parts,
    _apply_management_parts,
    _extra_cost_r,
    _feature_maps,
    _load_full_enriched,
    _split_dates,
)
from research_tools.short_is_feature_combo_search import _candidate_ledger
from research_tools.short_wfa_plateau_engine import _metrics, _score


CORE = ("A_plus_fast", "not_asia_overlap", "recent5", "tp075_full", "m5_mfe025", "risk_3_8pct")


def _candidate_key(row: pd.Series) -> tuple[str, str, str, str, str, str]:
    return (
        str(row["family_group"]),
        str(row["session_filter"]),
        str(row["stop_model"]),
        str(row["base_policy"]),
        str(row["guard"]),
        str(row.get("feature_name", "none") if pd.notna(row.get("feature_name", "none")) else "none"),
    )


def _key_text(key: tuple[str, str, str, str, str, str]) -> str:
    return "|".join(key)


def _load_candidate_keys(failed_dir: Path) -> list[tuple[str, str, str, str, str, str]]:
    keys = {CORE}
    paths = [
        failed_dir / "short_other_nature_marginal_screen.csv",
        failed_dir / "short_diversified_marginal_candidates.csv",
        failed_dir / "short_is_oos_guard_expansion_sanity.csv",
        failed_dir / "short_is_oos_max_verification_candidates.csv",
        failed_dir / "short_anchored_is_oos_result.csv",
    ]
    for path in paths:
        if not path.exists():
            continue
        frame = pd.read_csv(path)
        if "oos_pass" in frame.columns:
            frame = frame[frame["oos_pass"].astype(bool)].copy()
        if "marg_avg_r_cost10bps" in frame.columns:
            frame = frame[
                (pd.to_numeric(frame.get("marg_trades", 0), errors="coerce") >= 15)
                & (pd.to_numeric(frame.get("marg_avg_r", -999), errors="coerce") > 0)
                & (pd.to_numeric(frame.get("marg_median_r", -999), errors="coerce") > 0)
                & (pd.to_numeric(frame.get("marg_avg_r_cost10bps", -999), errors="coerce") > 0)
            ].copy()
        for _, row in frame.iterrows():
            if {"family_group", "session_filter", "stop_model", "base_policy", "guard"}.issubset(frame.columns):
                keys.add(_candidate_key(row))
    return sorted(keys)


def _ledger_for(
    base: pd.DataFrame,
    key: tuple[str, str, str, str, str, str],
    *,
    masks: dict[str, pd.Series],
    availability: dict[str, str],
) -> pd.DataFrame:
    family_group, session_filter, stop_model, base_policy, guard, feature_name = key
    ledger = _candidate_ledger(base, family_group, session_filter, stop_model, base_policy, guard)
    parts = [] if feature_name == "none" else [part.strip() for part in feature_name.split("&")]
    ledger = _apply_entry_parts(ledger, parts=parts, masks=masks, availability=availability)
    if ledger.empty:
        return ledger
    values = _apply_management_parts(ledger, parts=parts, masks=masks, availability=availability, base_policy=base_policy)
    ledger = ledger.copy()
    ledger["_final_r"] = values.reindex(ledger.index)
    ledger["candidate_key"] = _key_text(key)
    return ledger


def _candidate_table(
    base: pd.DataFrame,
    keys: list[tuple[str, str, str, str, str, str]],
    *,
    masks: dict[str, pd.Series],
    availability: dict[str, str],
    start: pd.Timestamp,
    split: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    ledgers: dict[str, pd.DataFrame] = {}
    rows = []
    core = _ledger_for(base, CORE, masks=masks, availability=availability)
    core_oos_ids = set(core[(core["date_ts"] >= split) & (core["date_ts"] < end)]["signal_id"].astype(str))

    for key in keys:
        try:
            ledger = _ledger_for(base, key, masks=masks, availability=availability)
        except Exception:
            continue
        if ledger.empty:
            continue
        key_text = _key_text(key)
        ledgers[key_text] = ledger
        is_metrics = _period_metrics(ledger, start, split)
        oos_metrics = _period_metrics(ledger, split, end)
        oos = ledger[(ledger["date_ts"] >= split) & (ledger["date_ts"] < end)].copy()
        marginal = oos[~oos["signal_id"].astype(str).isin(core_oos_ids)].copy()
        marginal_metrics = _metrics(marginal, marginal["_final_r"]) if not marginal.empty else _metrics(marginal, pd.Series(dtype=float))
        cost10 = (
            _metrics(marginal, marginal["_final_r"] - _extra_cost_r(marginal, 10))
            if not marginal.empty
            else _metrics(marginal, pd.Series(dtype=float))
        )
        row = {
            "candidate_key": key_text,
            "family_group": key[0],
            "session_filter": key[1],
            "stop_model": key[2],
            "base_policy": key[3],
            "guard": key[4],
            "feature_name": key[5],
            "is_score": _score(is_metrics),
            "oos_score": _score(oos_metrics),
            "marg_score": _score(marginal_metrics),
            "oos_overlap_with_core": int(len(oos) - len(marginal)),
            "marg_cost10_avg_r": cost10["avg_r"],
            "marg_cost10_sum_r": cost10["sum_r"],
        }
        row.update({f"is_{k}": v for k, v in is_metrics.items()})
        row.update({f"oos_{k}": v for k, v in oos_metrics.items()})
        row.update({f"marg_{k}": v for k, v in marginal_metrics.items()})
        rows.append(row)
    return pd.DataFrame(rows), ledgers


def _period_metrics(ledger: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, float | int]:
    sub = ledger[(ledger["date_ts"] >= start) & (ledger["date_ts"] < end)]
    return _metrics(sub, sub["_final_r"]) if not sub.empty else _metrics(sub, pd.Series(dtype=float))


def _portfolio_from_keys(
    keys: list[str],
    ledgers: dict[str, pd.DataFrame],
    *,
    split: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, dict[str, float | int]]:
    used: set[str] = set()
    frames = []
    for key in keys:
        ledger = ledgers[key]
        oos = ledger[(ledger["date_ts"] >= split) & (ledger["date_ts"] < end)].copy()
        new = oos[~oos["signal_id"].astype(str).isin(used)].copy()
        used.update(new["signal_id"].astype(str))
        new["sleeve"] = key
        frames.append(new)
    if not frames:
        empty = pd.DataFrame()
        return empty, _metrics(empty, pd.Series(dtype=float))
    book = pd.concat(frames, ignore_index=True).sort_values(["entry_timestamp_ms", "sleeve"]).drop_duplicates("signal_id")
    metrics = _metrics(book, book["_final_r"])
    cost10 = _metrics(book, book["_final_r"] - _extra_cost_r(book, 10))
    metrics["cost10_avg_r"] = cost10["avg_r"]
    metrics["cost10_sum_r"] = cost10["sum_r"]
    metrics["trades_per_day"] = float(len(book) / max((end - split).days, 1))
    return book, metrics


def _greedy_portfolio(
    candidates: pd.DataFrame,
    ledgers: dict[str, pd.DataFrame],
    *,
    split: pd.Timestamp,
    end: pd.Timestamp,
    mode: str,
    max_sleeves: int,
) -> tuple[list[str], pd.DataFrame, dict[str, float | int]]:
    selected = [_key_text(CORE)]
    used = set(
        ledgers[_key_text(CORE)]
        .loc[
            (ledgers[_key_text(CORE)]["date_ts"] >= split)
            & (ledgers[_key_text(CORE)]["date_ts"] < end),
            "signal_id",
        ]
        .astype(str)
    )
    pool = candidates[candidates["candidate_key"].ne(_key_text(CORE))].copy()
    pool = pool[
        (pd.to_numeric(pool["marg_trades"], errors="coerce") >= 15)
        & (pd.to_numeric(pool["marg_avg_r"], errors="coerce") > 0)
        & (pd.to_numeric(pool["marg_median_r"], errors="coerce") > 0)
        & (pd.to_numeric(pool["marg_cost10_avg_r"], errors="coerce") > 0)
    ].copy()
    if mode == "quality":
        pool["rank_score"] = pool["marg_score"] + 0.25 * pool["marg_top_trade_independence_pct"] + 0.25 * pool["marg_top_symbol_independence_pct"]
        min_new = 20
    elif mode == "frequency":
        pool["rank_score"] = pool["marg_sum_r"] + 0.035 * pool["marg_trades"]
        min_new = 12
    else:
        pool["rank_score"] = pool["marg_score"] + 0.10 * np.log1p(pool["marg_trades"]) + 0.10 * pool["marg_cost10_avg_r"]
        min_new = 15

    for _, row in pool.sort_values(["rank_score", "marg_sum_r"], ascending=False).iterrows():
        key = str(row["candidate_key"])
        if key in selected or key not in ledgers:
            continue
        ledger = ledgers[key]
        oos = ledger[(ledger["date_ts"] >= split) & (ledger["date_ts"] < end)].copy()
        new = oos[~oos["signal_id"].astype(str).isin(used)].copy()
        if len(new) < min_new or len(new) / max(len(oos), 1) < 0.50:
            continue
        new_metrics = _metrics(new, new["_final_r"])
        cost10 = _metrics(new, new["_final_r"] - _extra_cost_r(new, 10))
        if new_metrics["avg_r"] <= 0 or new_metrics["median_r"] <= 0 or cost10["avg_r"] <= 0:
            continue
        selected.append(key)
        used.update(new["signal_id"].astype(str))
        if len(selected) >= max_sleeves:
            break
    book, metrics = _portfolio_from_keys(selected, ledgers, split=split, end=end)
    metrics["mode"] = mode
    metrics["sleeves"] = len(selected)
    return selected, book, metrics


def run(*, failed_dir: Path, split_days: int, max_sleeves: int) -> dict[str, object]:
    base = _load_full_enriched(failed_dir)
    start, split, end = _split_dates(base, split_days)
    masks, availability = _feature_maps(base)
    keys = _load_candidate_keys(failed_dir)
    candidates, ledgers = _candidate_table(base, keys, masks=masks, availability=availability, start=start, split=split, end=end)

    portfolios = []
    all_books = []
    sleeve_rows = []
    for mode in ("quality", "balanced", "frequency"):
        selected, book, metrics = _greedy_portfolio(candidates, ledgers, split=split, end=end, mode=mode, max_sleeves=max_sleeves)
        metrics["portfolio"] = f"{mode}_frontier"
        portfolios.append(metrics)
        if not book.empty:
            book = book.copy()
            book["portfolio"] = f"{mode}_frontier"
            all_books.append(book)
        used: set[str] = set()
        for rank, key in enumerate(selected, start=1):
            ledger = ledgers[key]
            oos = ledger[(ledger["date_ts"] >= split) & (ledger["date_ts"] < end)].copy()
            new = oos[~oos["signal_id"].astype(str).isin(used)].copy()
            used.update(new["signal_id"].astype(str))
            stats = _metrics(new, new["_final_r"]) if not new.empty else _metrics(new, pd.Series(dtype=float))
            cost10 = _metrics(new, new["_final_r"] - _extra_cost_r(new, 10)) if not new.empty else _metrics(new, pd.Series(dtype=float))
            sleeve_rows.append({"portfolio": f"{mode}_frontier", "rank": rank, "candidate_key": key, "cost10_avg_r": cost10["avg_r"], **stats})

    out_candidates = failed_dir / "short_frontier_candidates.csv"
    out_portfolios = failed_dir / "short_frontier_portfolios.csv"
    out_sleeves = failed_dir / "short_frontier_sleeves.csv"
    out_trades = failed_dir / "short_frontier_oos_trades.csv"
    out_report = failed_dir / "short_frontier_report.md"
    candidates.to_csv(out_candidates, index=False)
    pd.DataFrame(portfolios).to_csv(out_portfolios, index=False)
    pd.DataFrame(sleeve_rows).to_csv(out_sleeves, index=False)
    if all_books:
        pd.concat(all_books, ignore_index=True).to_csv(out_trades, index=False)
    else:
        pd.DataFrame().to_csv(out_trades, index=False)

    portfolio_df = pd.DataFrame(portfolios)
    sleeve_df = pd.DataFrame(sleeve_rows)
    top_cols = [
        "candidate_key",
        "marg_trades",
        "marg_avg_r",
        "marg_median_r",
        "marg_win_rate",
        "marg_positive_day_rate",
        "marg_top_trade_independence_pct",
        "marg_top_symbol_independence_pct",
        "marg_cost10_avg_r",
    ]
    lines = [
        "# Short Diversified Frontier",
        "",
        "Research-frontier portfolio construction from already consumed OOS",
        "screens.  This is not fresh OOS proof.",
        "",
        "Portfolios:",
        "",
        "```text",
        portfolio_df.to_string(index=False),
        "```",
        "",
        "Sleeves:",
        "",
        "```text",
        sleeve_df.to_string(index=False),
        "```",
        "",
        "Top marginal candidates:",
        "",
        "```text",
        candidates.sort_values(["marg_score", "marg_sum_r"], ascending=False)[[c for c in top_cols if c in candidates.columns]]
        .head(50)
        .to_string(index=False),
        "```",
    ]
    out_report.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {
        "candidate_rows": len(candidates),
        "portfolio_rows": len(portfolio_df),
        "best_trades": int(portfolio_df["trades"].max()) if not portfolio_df.empty else 0,
        "best_cost10_avg_r": float(portfolio_df["cost10_avg_r"].max()) if not portfolio_df.empty else np.nan,
        "report": str(out_report),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failed-dir", type=Path, default=Path(".output/results/failed_pump_short_research_365d"))
    parser.add_argument("--split-days", type=int, default=180)
    parser.add_argument("--max-sleeves", type=int, default=8)
    args = parser.parse_args()
    result = run(failed_dir=args.failed_dir, split_days=args.split_days, max_sleeves=args.max_sleeves)
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
