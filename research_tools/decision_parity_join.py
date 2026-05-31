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


def main() -> None:
    parser = argparse.ArgumentParser(description="Join live2/backtest decision ledgers by snapshot_hash")
    parser.add_argument("--live", required=True, type=Path, help="Path to live2_decision_ledger.csv")
    parser.add_argument("--backtest", required=True, type=Path, help="Path to htf_ltf_runner_decision_ledger.csv")
    parser.add_argument("--output", required=True, type=Path, help="Output comparison CSV path")
    args = parser.parse_args()
    rows = build_comparison(args.live, args.backtest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
