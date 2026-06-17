"""Freeze best first-180d short candidates and verify on the next 180d.

This script is deliberately anchored:

- candidate ranking uses only the first 180 days;
- the second half is evaluated once with frozen definitions;
- entry-known features are applied before entry;
- post-entry features are treated as executable management checks, not as
  hindsight filters.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_tools.short_is_feature_combo_search import BASE_POLICIES, _feature_masks
from research_tools.short_wfa_plateau_engine import (
    FAMILY_GROUPS,
    GUARDS,
    SESSION_FILTERS,
    _apply_guard,
    _guard_exit_r,
    _metrics,
    _score,
)


POST_PART_RE = re.compile(r"^m(?P<minute>3|5|10|15)_")
STRESS_BPS = (5, 10, 20)
R_HAIRCUTS = (0.05, 0.10, 0.20)


def _load_full_enriched(failed_dir: Path) -> pd.DataFrame:
    trades = pd.read_csv(failed_dir / "short_structural_075_path_replay_trades.csv")
    trades = trades[trades["status"].eq("closed") & trades["policy"].isin(BASE_POLICIES)].copy()
    trades["date_ts"] = pd.to_datetime(trades["date"], errors="coerce", utc=True).dt.tz_convert(None)
    trades = trades.dropna(subset=["date_ts", "entry_price", "initial_risk_pct"])

    signal_cols = [
        "signal_id",
        "confirm_close_position",
        "confirm_return_pct",
        "confirm_quote_ratio_vs_norm",
        "confirm_trade_ratio_vs_norm",
        "confirm_taker_buy_share",
        "confirm_taker_buy_share_norm",
        "confirm_taker_buy_vs_norm_pct",
        "confirm_taker_buy_norm_ratio",
        "confirm_taker_buy_below_norm_flag",
        "pre_confirm_return_from_seed_close_pct",
        "post_pump_preconfirm_quote_top1_share",
        "post_pump_preconfirm_trade_top1_share",
        "post_pump_preconfirm_distribution_bucket",
        "seed_1m_quote_top1_share",
        "seed_1m_trade_top1_share",
        "seed_1m_distribution_bucket",
        "pump_high_timing_pct",
        "seed_first_half_return_pct",
        "seed_second_half_return_pct",
        "seed_last_2m_return_pct",
        "has_lower_high_before_break",
    ]
    signals = pd.read_csv(failed_dir / "failed_pump_short_signals.csv", usecols=lambda c: c in set(signal_cols))
    signals = signals.drop_duplicates("signal_id", keep="last")
    return trades.merge(signals, on="signal_id", how="left", suffixes=("", "_signal")).reset_index(drop=True)


def _split_dates(base: pd.DataFrame, split_days: int) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    start = pd.to_datetime(base["date_ts"].min())
    end = pd.to_datetime(base["date_ts"].max()) + pd.Timedelta(days=1)
    split = start + pd.Timedelta(days=int(split_days))
    return start, split, end


def _candidate_ledger(
    base: pd.DataFrame,
    *,
    family_group: str,
    session_filter: str,
    stop_model: str,
    base_policy: str,
    guard_name: str,
) -> pd.DataFrame:
    guard = {guard.name: guard for guard in GUARDS}[guard_name]
    families = FAMILY_GROUPS[family_group]
    frame = base[base["family"].isin(families) & base["stop_model"].eq(stop_model) & base["policy"].eq(base_policy)].copy()
    sessions = SESSION_FILTERS[session_filter]
    if sessions is not None:
        frame = frame[frame["session_bucket"].isin(sessions)].copy()
    if frame.empty:
        return frame
    frame["_net_r"] = _apply_guard(frame, base_policy, guard)
    family_rank = {family: idx for idx, family in enumerate(families)}
    frame["_family_rank"] = frame["family"].map(family_rank).fillna(999).astype(int)
    return (
        frame.sort_values(["entry_timestamp_ms", "signal_id", "_family_rank"])
        .drop_duplicates(["signal_id", "stop_model"], keep="first")
        .copy()
    )


def _feature_maps(base: pd.DataFrame) -> tuple[dict[str, pd.Series], dict[str, str]]:
    masks = _feature_masks(base)
    by_name = {mask.name: mask.mask for mask in masks}
    availability = {mask.name: mask.availability for mask in masks}
    return by_name, availability


def _parts(feature_name: str) -> list[str]:
    if not feature_name or feature_name == "none":
        return []
    return [part.strip() for part in str(feature_name).split("&")]


def _is_post_part(part: str, availability: dict[str, str]) -> bool:
    return availability.get(part) == "post_entry_management"


def _post_minute(part: str) -> int:
    match = POST_PART_RE.match(part)
    return int(match.group("minute")) if match else 0


def _apply_entry_parts(
    ledger: pd.DataFrame,
    *,
    parts: list[str],
    masks: dict[str, pd.Series],
    availability: dict[str, str],
) -> pd.DataFrame:
    out = ledger
    for part in parts:
        if _is_post_part(part, availability):
            continue
        mask = masks.get(part)
        if mask is None:
            continue
        out = out.loc[mask.reindex(out.index, fill_value=False)].copy()
        if out.empty:
            break
    return out


def _target_not_done(frame: pd.DataFrame, base_policy: str, minute: int) -> pd.Series:
    target_col = "time_to_1r_min" if base_policy in {"tp1_full", "tp1_be075"} else "time_to_075r_min"
    target_time = pd.to_numeric(frame[target_col], errors="coerce")
    return target_time.isna() | (target_time > float(minute))


def _apply_management_parts(
    ledger: pd.DataFrame,
    *,
    parts: list[str],
    masks: dict[str, pd.Series],
    availability: dict[str, str],
    base_policy: str,
) -> pd.Series:
    net = pd.to_numeric(ledger["_net_r"], errors="coerce").copy()
    post_parts = sorted([part for part in parts if _is_post_part(part, availability)], key=_post_minute)
    for part in post_parts:
        minute = _post_minute(part)
        if minute <= 0 or f"m{minute}_close_r" not in ledger.columns:
            continue
        mask = masks.get(part)
        if mask is None:
            continue
        condition_ok = mask.reindex(ledger.index, fill_value=False)
        hold_ok = pd.to_numeric(ledger["hold_minutes"], errors="coerce") >= float(minute)
        fail = (~condition_ok) & hold_ok & _target_not_done(ledger, base_policy, minute)
        if fail.any():
            guarded = _guard_exit_r(ledger, minute)
            net.loc[fail] = guarded.loc[fail]
    return net


def _extra_cost_r(frame: pd.DataFrame, bps: int) -> pd.Series:
    entry = pd.to_numeric(frame["entry_price"], errors="coerce")
    risk_abs = entry * pd.to_numeric(frame["initial_risk_pct"], errors="coerce")
    return (float(bps) / 10000.0) * (2.0 * entry) / risk_abs


def _period_metrics(frame: pd.DataFrame, values: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, float | int]:
    sub = frame[(frame["date_ts"] >= start) & (frame["date_ts"] < end)].copy()
    vals = values.reindex(sub.index)
    return _metrics(sub, vals) if not sub.empty else _metrics(sub, pd.Series(dtype=float))


def _pass_oos(metrics: dict[str, float | int], *, min_trades: int = 20) -> bool:
    return bool(
        int(metrics.get("trades", 0)) >= min_trades
        and float(metrics.get("avg_r", -999.0)) > 0.0
        and float(metrics.get("median_r", -999.0)) > 0.0
        and float(metrics.get("positive_day_rate", 0.0)) >= 0.50
        and float(metrics.get("top_trade_independence_pct", 0.0)) >= 0.05
        and float(metrics.get("top_symbol_independence_pct", 0.0)) >= 0.05
    )


def _selection_score(screen: pd.DataFrame) -> pd.Series:
    trades = pd.to_numeric(screen["trades"], errors="coerce").fillna(0)
    symbols = pd.to_numeric(screen["symbols"], errors="coerce").fillna(0)
    days = pd.to_numeric(screen["days"], errors="coerce").fillna(0)
    score = pd.to_numeric(screen["score"], errors="coerce").fillna(0)
    top_trade = pd.to_numeric(screen["top_trade_independence_pct"], errors="coerce").fillna(0)
    top_symbol = pd.to_numeric(screen["top_symbol_independence_pct"], errors="coerce").fillna(0)
    pos_day = pd.to_numeric(screen["positive_day_rate"], errors="coerce").fillna(0)
    support = screen.groupby(["family_group", "session_filter", "stop_model", "base_policy", "guard"])["candidate_pass"].transform("sum")
    feature_support = screen.groupby("feature_name")["candidate_pass"].transform("sum")
    return (
        score
        + 0.06 * np.log1p(trades)
        + 0.03 * np.log1p(symbols)
        + 0.03 * np.log1p(days)
        + 0.10 * top_trade
        + 0.10 * top_symbol
        + 0.05 * pos_day
        + 0.015 * np.log1p(support)
        + 0.010 * np.log1p(feature_support)
    )


def _select_shortlists(screen: pd.DataFrame, *, max_entry: int, max_management: int) -> pd.DataFrame:
    work = screen[screen["candidate_pass"].astype(bool)].copy()
    work = work[work["trades"].ge(20) & work["symbols"].ge(15) & work["days"].ge(15)].copy()
    work["selection_score"] = _selection_score(work)
    work["candidate_uid"] = (
        work["family_group"].astype(str)
        + "|"
        + work["session_filter"].astype(str)
        + "|"
        + work["stop_model"].astype(str)
        + "|"
        + work["base_policy"].astype(str)
        + "|"
        + work["guard"].astype(str)
        + "|"
        + work["feature_name"].astype(str)
    )
    entry = work[work["feature_availability"].isin(["base_surface", "entry_known"])].copy()
    mgmt = work[work["feature_availability"].eq("post_entry_management")].copy()
    entry = entry.sort_values(["selection_score", "score", "sum_r"], ascending=[False, False, False]).drop_duplicates("candidate_uid").head(max_entry)
    mgmt = mgmt.sort_values(["selection_score", "score", "sum_r"], ascending=[False, False, False]).drop_duplicates("candidate_uid").head(max_management)
    entry["shortlist"] = "entry"
    mgmt["shortlist"] = "management"
    selected = pd.concat([entry, mgmt], ignore_index=True)
    selected["rank_in_shortlist"] = selected.groupby("shortlist").cumcount() + 1
    return selected


def _evaluate_candidate(
    base: pd.DataFrame,
    selected_row: pd.Series,
    *,
    masks: dict[str, pd.Series],
    availability: dict[str, str],
    start: pd.Timestamp,
    split: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[dict[str, object], pd.DataFrame]:
    ledger = _candidate_ledger(
        base,
        family_group=str(selected_row["family_group"]),
        session_filter=str(selected_row["session_filter"]),
        stop_model=str(selected_row["stop_model"]),
        base_policy=str(selected_row["base_policy"]),
        guard_name=str(selected_row["guard"]),
    )
    parts = _parts(str(selected_row["feature_name"]))
    ledger = _apply_entry_parts(ledger, parts=parts, masks=masks, availability=availability)
    if ledger.empty:
        values = pd.Series(dtype=float)
    else:
        values = _apply_management_parts(
            ledger,
            parts=parts,
            masks=masks,
            availability=availability,
            base_policy=str(selected_row["base_policy"]),
        )
    ledger = ledger.copy()
    ledger["_final_r"] = values.reindex(ledger.index)
    ledger["candidate_uid"] = selected_row["candidate_uid"]
    ledger["shortlist"] = selected_row["shortlist"]
    ledger["rank_in_shortlist"] = int(selected_row["rank_in_shortlist"])
    ledger["feature_name"] = selected_row["feature_name"]

    is_metrics = _period_metrics(ledger, ledger["_final_r"], start, split)
    oos_metrics = _period_metrics(ledger, ledger["_final_r"], split, end)
    out: dict[str, object] = {
        "candidate_uid": selected_row["candidate_uid"],
        "shortlist": selected_row["shortlist"],
        "rank_in_shortlist": int(selected_row["rank_in_shortlist"]),
        "family_group": selected_row["family_group"],
        "session_filter": selected_row["session_filter"],
        "stop_model": selected_row["stop_model"],
        "base_policy": selected_row["base_policy"],
        "guard": selected_row["guard"],
        "feature_name": selected_row["feature_name"],
        "feature_availability": selected_row["feature_availability"],
        "feature_family": selected_row["feature_family"],
        "selection_score": float(selected_row["selection_score"]),
        "is_screen_trades": int(selected_row["trades"]),
        "is_screen_avg_r": float(selected_row["avg_r"]),
        "is_screen_median_r": float(selected_row["median_r"]),
    }
    for prefix, metrics in (("is", is_metrics), ("oos", oos_metrics)):
        for key, value in metrics.items():
            out[f"{prefix}_{key}"] = value
    out["oos_pass"] = _pass_oos(oos_metrics)
    out["oos_score"] = _score(oos_metrics)

    oos_mask = (ledger["date_ts"] >= split) & (ledger["date_ts"] < end)
    oos_ledger = ledger.loc[oos_mask]
    for bps in STRESS_BPS:
        stressed = ledger["_final_r"] - _extra_cost_r(ledger, bps)
        out[f"oos_avg_r_cost{bps}bps"] = _period_metrics(ledger, stressed, split, end)["avg_r"]
        out[f"oos_sum_r_cost{bps}bps"] = _period_metrics(ledger, stressed, split, end)["sum_r"]
    for haircut in R_HAIRCUTS:
        stressed = ledger["_final_r"] - float(haircut)
        out[f"oos_avg_r_haircut{int(haircut * 100):02d}"] = _period_metrics(ledger, stressed, split, end)["avg_r"]
        out[f"oos_sum_r_haircut{int(haircut * 100):02d}"] = _period_metrics(ledger, stressed, split, end)["sum_r"]
    out["oos_trades_per_day"] = float(len(oos_ledger) / max((end - split).days, 1))
    return out, ledger


def _portfolio_metrics(
    ledgers: list[pd.DataFrame],
    *,
    name: str,
    start: pd.Timestamp,
    split: pd.Timestamp,
    end: pd.Timestamp,
    max_rank: int,
    shortlist: str,
) -> dict[str, object]:
    frames = [ledger[(ledger["shortlist"].eq(shortlist)) & (ledger["rank_in_shortlist"].le(max_rank))] for ledger in ledgers]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        empty = _metrics(pd.DataFrame(), pd.Series(dtype=float))
        return {"portfolio": name, "shortlist": shortlist, "top_k": max_rank, **{f"is_{k}": v for k, v in empty.items()}, **{f"oos_{k}": v for k, v in empty.items()}}
    book = pd.concat(frames, ignore_index=True)
    book = book.sort_values(["entry_timestamp_ms", "rank_in_shortlist", "candidate_uid"])
    book = book.drop_duplicates(["signal_id"], keep="first").copy()
    book["_final_r"] = pd.to_numeric(book["_final_r"], errors="coerce")
    is_metrics = _period_metrics(book, book["_final_r"], start, split)
    oos_metrics = _period_metrics(book, book["_final_r"], split, end)
    out: dict[str, object] = {"portfolio": name, "shortlist": shortlist, "top_k": max_rank}
    for prefix, metrics in (("is", is_metrics), ("oos", oos_metrics)):
        for key, value in metrics.items():
            out[f"{prefix}_{key}"] = value
    out["oos_pass"] = _pass_oos(oos_metrics, min_trades=25)
    out["oos_score"] = _score(oos_metrics)
    out["oos_trades_per_day"] = float(oos_metrics["trades"] / max((end - split).days, 1))
    for bps in STRESS_BPS:
        stressed = book["_final_r"] - _extra_cost_r(book, bps)
        out[f"oos_avg_r_cost{bps}bps"] = _period_metrics(book, stressed, split, end)["avg_r"]
        out[f"oos_sum_r_cost{bps}bps"] = _period_metrics(book, stressed, split, end)["sum_r"]
    for haircut in R_HAIRCUTS:
        stressed = book["_final_r"] - float(haircut)
        out[f"oos_avg_r_haircut{int(haircut * 100):02d}"] = _period_metrics(book, stressed, split, end)["avg_r"]
        out[f"oos_sum_r_haircut{int(haircut * 100):02d}"] = _period_metrics(book, stressed, split, end)["sum_r"]
    return out


def _write_report(out_dir: Path, candidates: pd.DataFrame, portfolios: pd.DataFrame, *, start: pd.Timestamp, split: pd.Timestamp, end: pd.Timestamp) -> None:
    candidate_cols = [
        "shortlist",
        "rank_in_shortlist",
        "family_group",
        "session_filter",
        "stop_model",
        "base_policy",
        "guard",
        "feature_name",
        "is_trades",
        "is_avg_r",
        "is_median_r",
        "oos_trades",
        "oos_avg_r",
        "oos_median_r",
        "oos_win_rate",
        "oos_positive_day_rate",
        "oos_top_trade_independence_pct",
        "oos_top_symbol_independence_pct",
        "oos_avg_r_cost10bps",
        "oos_avg_r_haircut10",
        "oos_pass",
    ]
    portfolio_cols = [
        "portfolio",
        "shortlist",
        "top_k",
        "is_trades",
        "is_avg_r",
        "is_median_r",
        "oos_trades",
        "oos_avg_r",
        "oos_median_r",
        "oos_win_rate",
        "oos_positive_day_rate",
        "oos_top_trade_independence_pct",
        "oos_top_symbol_independence_pct",
        "oos_trades_per_day",
        "oos_avg_r_cost10bps",
        "oos_avg_r_haircut10",
        "oos_pass",
    ]
    if not candidates.empty and {"oos_pass", "oos_score", "oos_sum_r"}.issubset(candidates.columns):
        candidate_table = (
            candidates.sort_values(["oos_pass", "oos_score", "oos_sum_r"], ascending=[False, False, False])
            .head(50)
            [[c for c in candidate_cols if c in candidates.columns]]
            .to_string(index=False)
        )
    else:
        candidate_table = "EMPTY"
    if not portfolios.empty and {"oos_pass", "oos_score", "oos_sum_r"}.issubset(portfolios.columns):
        portfolio_table = (
            portfolios.sort_values(["oos_pass", "oos_score", "oos_sum_r"], ascending=[False, False, False])
            [[c for c in portfolio_cols if c in portfolios.columns]]
            .to_string(index=False)
        )
    else:
        portfolio_table = "EMPTY"

    lines = [
        "# IS Max / OOS Verification",
        "",
        "Candidate selection uses only the first 180 days. The second half is",
        "evaluated once with frozen definitions.",
        "",
        "```text",
        f"IS:  {start.date().isoformat()} -> {split.date().isoformat()}",
        f"OOS: {split.date().isoformat()} -> {end.date().isoformat()}",
        f"selected candidates: {len(candidates)}",
        f"OOS passing candidates: {int(candidates['oos_pass'].sum()) if not candidates.empty else 0}",
        f"OOS passing portfolios: {int(portfolios['oos_pass'].sum()) if not portfolios.empty else 0}",
        "```",
        "",
        "Boundary:",
        "",
        "Post-entry features are evaluated as management checks. If the condition",
        "is missing after the selected minute, the trade exits at that minute's",
        "close-r proxy instead of being removed from the sample.",
        "",
        "Top OOS candidates among the frozen IS shortlist:",
        "",
        "```text",
        candidate_table,
        "```",
        "",
        "Frozen-rank portfolios:",
        "",
        "```text",
        portfolio_table,
        "```",
    ]
    (out_dir / "short_is_oos_max_verification_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(*, failed_dir: Path, split_days: int, max_entry: int, max_management: int) -> dict[str, object]:
    base = _load_full_enriched(failed_dir)
    start, split, end = _split_dates(base, split_days)
    screen = pd.read_csv(failed_dir / "short_is_feature_combo_screen.csv")
    selected = _select_shortlists(screen, max_entry=max_entry, max_management=max_management)
    masks, availability = _feature_maps(base)

    rows: list[dict[str, object]] = []
    ledgers: list[pd.DataFrame] = []
    for _, selected_row in selected.iterrows():
        row, ledger = _evaluate_candidate(
            base,
            selected_row,
            masks=masks,
            availability=availability,
            start=start,
            split=split,
            end=end,
        )
        rows.append(row)
        if not ledger.empty:
            ledgers.append(ledger)

    candidates = pd.DataFrame(rows)
    portfolio_rows = []
    for shortlist in ("entry", "management"):
        for top_k in (3, 5, 10, 20, 40):
            if top_k <= int((selected["shortlist"] == shortlist).sum()):
                portfolio_rows.append(
                    _portfolio_metrics(
                        ledgers,
                        name=f"{shortlist}_top{top_k}",
                        start=start,
                        split=split,
                        end=end,
                        max_rank=top_k,
                        shortlist=shortlist,
                    )
                )
    portfolios = pd.DataFrame(portfolio_rows)

    out_dir = failed_dir
    candidates_path = out_dir / "short_is_oos_max_verification_candidates.csv"
    portfolios_path = out_dir / "short_is_oos_max_verification_portfolios.csv"
    selected_path = out_dir / "short_is_oos_max_verification_selected.csv"
    candidates.to_csv(candidates_path, index=False)
    portfolios.to_csv(portfolios_path, index=False)
    selected.to_csv(selected_path, index=False)
    _write_report(out_dir, candidates, portfolios, start=start, split=split, end=end)
    return {
        "is_start": start.date().isoformat(),
        "split_date": split.date().isoformat(),
        "oos_end": end.date().isoformat(),
        "selected": len(selected),
        "candidate_oos_passes": int(candidates["oos_pass"].sum()) if not candidates.empty else 0,
        "portfolio_oos_passes": int(portfolios["oos_pass"].sum()) if not portfolios.empty else 0,
        "candidates_path": str(candidates_path),
        "portfolios_path": str(portfolios_path),
        "report_path": str(out_dir / "short_is_oos_max_verification_report.md"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failed-dir", type=Path, default=Path(".output/results/failed_pump_short_research_365d"))
    parser.add_argument("--split-days", type=int, default=180)
    parser.add_argument("--max-entry", type=int, default=80)
    parser.add_argument("--max-management", type=int, default=80)
    args = parser.parse_args()
    result = run(
        failed_dir=args.failed_dir,
        split_days=args.split_days,
        max_entry=args.max_entry,
        max_management=args.max_management,
    )
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
