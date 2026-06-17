"""Anchored IS/OOS split for structural short plateau candidates.

This script uses the existing 1m path replay artifact.  It searches the bounded
candidate surface only on the first part of the date range, then evaluates the
same frozen candidate definitions on the second part.  It is stricter than the
same-period WFA report because OOS rows are not used for candidate selection.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_tools.short_wfa_plateau_engine import (
    _build_full_screen,
    _metrics,
    _score,
)


def _split_dates(base: pd.DataFrame, split_days: int | None) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    start = pd.to_datetime(base["date_ts"].min())
    end = pd.to_datetime(base["date_ts"].max()) + pd.Timedelta(days=1)
    if split_days is None:
        split = start + (end - start) / 2
        split = pd.Timestamp(split).normalize()
    else:
        split = start + pd.Timedelta(days=int(split_days))
    return start, split, end


def _eval_candidate_on_period(ledger: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, float | int]:
    mask = (ledger["date_ts"] >= start) & (ledger["date_ts"] < end)
    sub = ledger.loc[mask].copy()
    return _metrics(sub, sub["_net_r"]) if not sub.empty else _metrics(sub, pd.Series(dtype=float))


def _selected_is_candidates(screen: pd.DataFrame, *, top_n: int) -> pd.DataFrame:
    if screen.empty:
        return screen
    selected = screen[screen["plateau_pass"].astype(bool)].copy()
    if selected.empty:
        selected = screen[screen["full_pass"].astype(bool)].copy()
    return selected.sort_values(["plateau_pass", "score", "sum_r"], ascending=[False, False, False]).head(top_n)


def run(*, failed_dir: Path, split_days: int | None, top_n: int) -> dict[str, object]:
    path = failed_dir / "short_structural_075_path_replay_trades.csv"
    base = pd.read_csv(path)
    base = base[base["status"].eq("closed") & base["policy"].isin(["tp075_full", "tp1_full", "tp1_be075"])].copy()
    base["date_ts"] = pd.to_datetime(base["date"], errors="coerce", utc=True).dt.tz_convert(None)
    base = base.dropna(subset=["date_ts", "net_r", "entry_price", "initial_risk_pct"])
    start, split, end = _split_dates(base, split_days)

    is_base = base[(base["date_ts"] >= start) & (base["date_ts"] < split)].copy()
    oos_base = base[(base["date_ts"] >= split) & (base["date_ts"] < end)].copy()
    is_screen, is_ledgers = _build_full_screen(is_base)
    selected = _selected_is_candidates(is_screen, top_n=top_n)

    # Build ledgers on full base for exactly the selected candidate ids.  Reuse
    # the same full-surface builder to avoid duplicating candidate logic.
    full_screen, full_ledgers = _build_full_screen(base)
    full_lookup = full_screen.set_index("candidate_id")
    rows = []
    for _, is_row in selected.iterrows():
        candidate_id = str(is_row["candidate_id"])
        ledger = full_ledgers.get(candidate_id)
        if ledger is None or ledger.empty:
            continue
        is_metrics = _eval_candidate_on_period(ledger, start, split)
        oos_metrics = _eval_candidate_on_period(ledger, split, end)
        full_row = full_lookup.loc[candidate_id].to_dict() if candidate_id in full_lookup.index else {}
        row = {
            "candidate_id": candidate_id,
            "family_group": is_row["family_group"],
            "session_filter": is_row["session_filter"],
            "stop_model": is_row["stop_model"],
            "base_policy": is_row["base_policy"],
            "guard": is_row["guard"],
            "is_score": is_row["score"],
            "is_plateau_neighbor_passes": is_row["plateau_neighbor_passes"],
            "full_plateau_pass": bool(full_row.get("plateau_pass", False)),
            "is_start": start.date().isoformat(),
            "split_date": split.date().isoformat(),
            "oos_end": end.date().isoformat(),
        }
        for prefix, metrics in (("is", is_metrics), ("oos", oos_metrics)):
            for key, value in metrics.items():
                row[f"{prefix}_{key}"] = value
        row["oos_pass"] = bool(
            row.get("oos_trades", 0) >= 20
            and row.get("oos_avg_r", -999.0) > 0
            and row.get("oos_median_r", -999.0) > 0
            and row.get("oos_positive_day_rate", 0.0) >= 0.50
            and row.get("oos_top_trade_independence_pct", 0.0) >= 0.05
            and row.get("oos_top_symbol_independence_pct", 0.0) >= 0.05
        )
        oos_score_input = {
            key.replace("oos_", ""): value
            for key, value in row.items()
            if key.startswith("oos_") and key != "oos_pass"
        }
        row["oos_score"] = _score(oos_score_input)
        rows.append(row)

    result = pd.DataFrame(rows)
    out_dir = failed_dir
    selected_path = out_dir / "short_anchored_is_candidates.csv"
    result_path = out_dir / "short_anchored_is_oos_result.csv"
    report_path = out_dir / "short_anchored_is_oos_report.md"
    selected.to_csv(selected_path, index=False)
    result.to_csv(result_path, index=False)

    cols = [
        "family_group",
        "session_filter",
        "stop_model",
        "base_policy",
        "guard",
        "is_trades",
        "is_avg_r",
        "is_median_r",
        "is_top_trade_independence_pct",
        "oos_trades",
        "oos_avg_r",
        "oos_median_r",
        "oos_win_rate",
        "oos_positive_day_rate",
        "oos_top_trade_independence_pct",
        "oos_top_symbol_independence_pct",
        "oos_pass",
    ]
    report = [
        "# Anchored IS/OOS Structural Short Split",
        "",
        "Candidate selection uses only the first anchored period.  OOS is then",
        "evaluated once with the frozen candidate definitions.",
        "",
        "Period:",
        "",
        "```text",
        f"IS:  {start.date().isoformat()} -> {split.date().isoformat()}",
        f"OOS: {split.date().isoformat()} -> {end.date().isoformat()}",
        f"IS rows: {len(is_base)}",
        f"OOS rows: {len(oos_base)}",
        f"IS candidates: {len(is_screen)}",
        f"selected candidates: {len(selected)}",
        f"OOS passes: {int(result['oos_pass'].sum()) if not result.empty else 0}",
        "```",
        "",
        "Top OOS results among IS-selected candidates:",
        "",
        "```text",
        result[[c for c in cols if c in result.columns] + [c for c in ("oos_score", "oos_sum_r") if c in result.columns]]
        .sort_values(["oos_pass", "oos_score", "oos_sum_r"], ascending=[False, False, False])
        .head(40)
        .to_string(index=False)
        if not result.empty
        else "EMPTY",
        "```",
    ]
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
    return {
        "is_start": start.date().isoformat(),
        "split_date": split.date().isoformat(),
        "oos_end": end.date().isoformat(),
        "is_rows": len(is_base),
        "oos_rows": len(oos_base),
        "is_candidate_rows": len(is_screen),
        "selected_rows": len(selected),
        "oos_passes": int(result["oos_pass"].sum()) if not result.empty else 0,
        "selected_path": str(selected_path),
        "result_path": str(result_path),
        "report_path": str(report_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failed-dir", type=Path, default=Path(".output/results/failed_pump_short_research_365d"))
    parser.add_argument("--split-days", type=int, default=180)
    parser.add_argument("--top-n", type=int, default=80)
    args = parser.parse_args()
    result = run(failed_dir=args.failed_dir, split_days=args.split_days, top_n=args.top_n)
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
