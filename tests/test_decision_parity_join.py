import csv
from pathlib import Path

from research_tools.decision_parity_join import build_comparison, build_summary


def _write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["snapshot_hash", "snapshot_match_key", "symbol", "tf_set", "signal_verdict", "signal_reason", "portfolio_verdict"]
    with path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_decision_parity_summary_reports_selected_overlap(tmp_path) -> None:
    live = tmp_path / "live.csv"
    backtest = tmp_path / "backtest.csv"
    _write_rows(
        live,
        [
            {
                "snapshot_hash": "a",
                "snapshot_match_key": "AAA|5m_30s",
                "symbol": "AAA/USDT:USDT",
                "tf_set": "5m_30s",
                "signal_verdict": "selected",
                "signal_reason": "ok",
                "portfolio_verdict": "opened",
            },
            {
                "snapshot_hash": "b",
                "snapshot_match_key": "BBB|5m_30s",
                "symbol": "BBB/USDT:USDT",
                "tf_set": "5m_30s",
                "signal_verdict": "rejected",
                "signal_reason": "weak_flow",
                "portfolio_verdict": "",
            },
        ],
    )
    _write_rows(
        backtest,
        [
            {
                "snapshot_hash": "a",
                "snapshot_match_key": "AAA|5m_30s",
                "symbol": "AAA/USDT:USDT",
                "tf_set": "5m_30s",
                "signal_verdict": "selected",
                "signal_reason": "ok",
                "portfolio_verdict": "opened",
            },
            {
                "snapshot_hash": "c",
                "snapshot_match_key": "CCC|5m_30s",
                "symbol": "CCC/USDT:USDT",
                "tf_set": "5m_30s",
                "signal_verdict": "selected",
                "signal_reason": "ok",
                "portfolio_verdict": "opened",
            },
        ],
    )

    comparison = build_comparison(live, backtest)
    summary = {row["metric"]: row["value"] for row in build_summary(live, backtest, comparison)}

    assert summary["shared_snapshot_hashes"] == "1"
    assert summary["shared_signal_parity_rate"] == "1.000000"
    assert summary["live_selected_snapshot_hashes"] == "1"
    assert summary["backtest_selected_snapshot_hashes"] == "2"
    assert summary["shared_selected_snapshot_hashes"] == "1"
    assert summary["selected_overlap_vs_live"] == "1.000000"
    assert summary["selected_overlap_vs_backtest"] == "0.500000"
