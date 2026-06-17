"""Join live2/backtest decision ledgers by source-neutral snapshot hash.

Usage:
    python -m research_tools.decision_parity_join \
        --live live_run/live2_decision_ledger.csv \
        --backtest backtest/htf_ltf_runner_decision_ledger.csv \
        --output decision_parity_comparison.csv

This is an audit helper only. It does not rewrite either source ledger and does
not apply tolerance, fuzzy matching, or post-hoc fixes. Exact same snapshot hash
must imply the same shared-core signal verdict.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Iterable


_FIELDS = (
    "snapshot_hash",
    "snapshot_match_key",
    "symbol",
    "tf_set",
    "live_rows",
    "backtest_rows",
    "live_signal_verdicts",
    "backtest_signal_verdicts",
    "live_signal_reasons",
    "backtest_signal_reasons",
    "live_portfolio_verdicts",
    "backtest_portfolio_verdicts",
    "mismatch_type",
)


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        return [dict(row) for row in csv.DictReader(file_obj)]


def _group_by_hash(rows: Iterable[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        key = str(row.get("snapshot_hash") or "")
        if not key:
            continue
        grouped.setdefault(key, []).append(row)
    return grouped


def _values(rows: list[dict[str, str]], field: str) -> str:
    return "|".join(sorted({str(row.get(field) or "") for row in rows if str(row.get(field) or "")}))


def _first(rows: list[dict[str, str]], field: str) -> str:
    for row in rows:
        value = str(row.get(field) or "")
        if value:
            return value
    return ""


def _mismatch_type(live_rows: list[dict[str, str]], backtest_rows: list[dict[str, str]]) -> str:
    if not live_rows:
        return "backtest_only_snapshot"
    if not backtest_rows:
        return "live_only_snapshot"
    live_signal = _values(live_rows, "signal_verdict")
    backtest_signal = _values(backtest_rows, "signal_verdict")
    if live_signal != backtest_signal:
        return "same_snapshot_different_signal_verdict"
    live_reason = _values(live_rows, "signal_reason")
    backtest_reason = _values(backtest_rows, "signal_reason")
    if live_reason != backtest_reason and live_signal != "selected":
        return "same_snapshot_same_signal_verdict_different_reason"
    live_portfolio = _values(live_rows, "portfolio_verdict")
    backtest_portfolio = _values(backtest_rows, "portfolio_verdict")
    if live_portfolio != backtest_portfolio:
        return "same_signal_different_portfolio_or_execution"
    return "match"


def build_comparison(live_path: Path, backtest_path: Path) -> list[dict[str, str]]:
    live = _group_by_hash(_read_rows(live_path))
    backtest = _group_by_hash(_read_rows(backtest_path))
    output: list[dict[str, str]] = []
    for key in sorted(set(live) | set(backtest)):
        live_rows = live.get(key, [])
        backtest_rows = backtest.get(key, [])
        either = live_rows or backtest_rows
        output.append(
            {
                "snapshot_hash": key,
                "snapshot_match_key": _first(either, "snapshot_match_key"),
                "symbol": _first(either, "symbol"),
                "tf_set": _first(either, "tf_set"),
                "live_rows": str(len(live_rows)),
                "backtest_rows": str(len(backtest_rows)),
                "live_signal_verdicts": _values(live_rows, "signal_verdict"),
                "backtest_signal_verdicts": _values(backtest_rows, "signal_verdict"),
                "live_signal_reasons": _values(live_rows, "signal_reason"),
                "backtest_signal_reasons": _values(backtest_rows, "signal_reason"),
                "live_portfolio_verdicts": _values(live_rows, "portfolio_verdict"),
                "backtest_portfolio_verdicts": _values(backtest_rows, "portfolio_verdict"),
                "mismatch_type": _mismatch_type(live_rows, backtest_rows),
            }
        )
    return output


def _hashes_with_value(rows: list[dict[str, str]], field: str, value: str) -> set[str]:
    result: set[str] = set()
    for row in rows:
        snapshot_hash = str(row.get("snapshot_hash") or "")
        if snapshot_hash and str(row.get(field) or "") == value:
            result.add(snapshot_hash)
    return result


def _ratio(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return ""
    return f"{float(numerator) / float(denominator):.6f}"


def build_summary(live_path: Path, backtest_path: Path, comparison: list[dict[str, str]]) -> list[dict[str, str]]:
    live_rows = _read_rows(live_path)
    backtest_rows = _read_rows(backtest_path)
    live_hashes = {str(row.get("snapshot_hash") or "") for row in live_rows if str(row.get("snapshot_hash") or "")}
    backtest_hashes = {str(row.get("snapshot_hash") or "") for row in backtest_rows if str(row.get("snapshot_hash") or "")}
    shared_hashes = live_hashes & backtest_hashes
    live_selected = _hashes_with_value(live_rows, "signal_verdict", "selected")
    backtest_selected = _hashes_with_value(backtest_rows, "signal_verdict", "selected")
    shared_selected = live_selected & backtest_selected
    mismatch_counts: dict[str, int] = {}
    for row in comparison:
        mismatch = str(row.get("mismatch_type") or "")
        mismatch_counts[mismatch] = mismatch_counts.get(mismatch, 0) + 1
    signal_mismatches = int(mismatch_counts.get("same_snapshot_different_signal_verdict", 0))
    shared_signal_matches = max(0, int(len(shared_hashes)) - signal_mismatches)
    selected_union = live_selected | backtest_selected
    metrics = {
        "live_snapshot_hashes": len(live_hashes),
        "backtest_snapshot_hashes": len(backtest_hashes),
        "shared_snapshot_hashes": len(shared_hashes),
        "live_snapshot_coverage_by_backtest": _ratio(len(shared_hashes), len(live_hashes)),
        "backtest_snapshot_coverage_by_live": _ratio(len(shared_hashes), len(backtest_hashes)),
        "shared_signal_matches": shared_signal_matches,
        "same_snapshot_different_signal_verdict": signal_mismatches,
        "shared_signal_parity_rate": _ratio(shared_signal_matches, len(shared_hashes)),
        "live_selected_snapshot_hashes": len(live_selected),
        "backtest_selected_snapshot_hashes": len(backtest_selected),
        "shared_selected_snapshot_hashes": len(shared_selected),
        "selected_overlap_vs_live": _ratio(len(shared_selected), len(live_selected)),
        "selected_overlap_vs_backtest": _ratio(len(shared_selected), len(backtest_selected)),
        "selected_jaccard_overlap": _ratio(len(shared_selected), len(selected_union)),
    }
    for mismatch, count in sorted(mismatch_counts.items()):
        metrics[f"mismatch_type:{mismatch}"] = int(count)
    return [{"metric": key, "value": str(value)} for key, value in metrics.items()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Join live2/backtest decision ledgers by snapshot_hash")
    parser.add_argument("--live", required=True, type=Path, help="Path to live2_decision_ledger.csv")
    parser.add_argument("--backtest", required=True, type=Path, help="Path to htf_ltf_runner_decision_ledger.csv")
    parser.add_argument("--output", required=True, type=Path, help="Output comparison CSV path")
    parser.add_argument("--summary-output", type=Path, default=None, help="Optional parity summary CSV path")
    args = parser.parse_args()
    rows = build_comparison(args.live, args.backtest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    if args.summary_output is not None:
        summary = build_summary(args.live, args.backtest, rows)
        args.summary_output.parent.mkdir(parents=True, exist_ok=True)
        with args.summary_output.open("w", encoding="utf-8-sig", newline="") as file_obj:
            writer = csv.DictWriter(file_obj, fieldnames=("metric", "value"))
            writer.writeheader()
            writer.writerows(summary)


if __name__ == "__main__":
    main()
