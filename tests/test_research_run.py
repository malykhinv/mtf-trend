from __future__ import annotations

from pathlib import Path

import pandas as pd

from anomaly_science.cli import build_parser
from anomaly_science.research import ResearchRunConfig, run_research_pipeline


def _write_cache(path: Path) -> None:
    path.mkdir()
    pd.DataFrame(
        {
            "timestamp": [1704067200000, 1704067260000, 1704067320000, 1704067380000],
            "open": [100.0, 100.5, 101.0, 101.5],
            "high": [101.0, 101.5, 102.0, 102.5],
            "low": [99.5, 100.0, 100.5, 101.0],
            "close": [100.5, 101.0, 101.5, 102.0],
            "volume": [10.0, 11.0, 12.0, 13.0],
            "quote_volume": [1005.0, 1111.0, 1218.0, 1326.0],
            "trade_count": [20, 22, 24, 26],
            "taker_buy_quote_volume": [550.0, 600.0, 650.0, 700.0],
            "open_interest": [1000.0, 1001.0, 1002.0, 1003.0],
        }
    ).to_parquet(path / "AAAUSDT.parquet")


def test_run_research_pipeline_uses_auto_output_and_cache_period(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    _write_cache(cache_dir)

    run_dir = run_research_pipeline(
        ResearchRunConfig(
            strategy_name="broad_anomaly_v1_h30",
            cache_dir=cache_dir,
            output_root=tmp_path / "runs",
        )
    )

    assert run_dir.parent == tmp_path / "runs"
    assert (run_dir / "input" / "candles_1m.csv").is_file()
    assert (run_dir / "stages" / "events" / "strategy_events.csv").is_file()
    assert (run_dir / "stages" / "feature_matrix" / "strategy_feature_matrix.csv").is_file()
    assert (run_dir / "stages" / "prediction" / "strategy_oos_predictions.csv").is_file()
    assert (run_dir / "stages" / "simulation" / "strategy_trade_simulation.csv").is_file()
    assert (run_dir / "stages" / "holdout_governance" / "research_ledger.csv").is_file()
    assert (run_dir / "stages" / "holdout_governance" / "holdout_access_log.csv").is_file()
    assert (run_dir / "stages" / "holdout_governance" / "strategy_protocol_audit.csv").is_file()
    assert (run_dir / "stages" / "forensic_audit" / "strategy_protocol_audit.csv").is_file()
    assert (run_dir / "stages" / "events" / "anomaly_events.csv").is_file()
    assert (run_dir / "research_run_summary.csv").is_file()

    ledger = pd.read_csv(run_dir / "stages" / "holdout_governance" / "research_ledger.csv")
    assert ledger.loc[0, "protocol_freeze_id"] == "run_research_broad_anomaly_v1_h30_2024-01-01_2024-01-01_protocol_freeze_v1"
    assert ledger.loc[0, "final_holdout_start_date"] == "2024-01-01"

    access_log = pd.read_csv(run_dir / "stages" / "holdout_governance" / "holdout_access_log.csv")
    assert access_log.empty

    summary = pd.read_csv(run_dir / "research_run_summary.csv")
    summary_by_key = dict(zip(summary["key"], summary["value"], strict=True))
    assert summary_by_key["research_start_date"] == "2024-01-01"
    assert summary_by_key["research_end_date"] == "2024-01-01"
    assert summary_by_key["forensic_audit_dir"].endswith("stages/forensic_audit") or summary_by_key["forensic_audit_dir"].endswith("stages\\forensic_audit")
    assert summary_by_key["forensic_audit_status"] in {"PASS", "WARN"}
    assert summary_by_key["forensic_audit_fail_count"] == "0"


def test_run_research_cli_accepts_strategy_and_optional_days() -> None:
    args = build_parser().parse_args(["run-research", "broad_anomaly_v1_h30", "--days", "30"])

    assert args.command == "run-research"
    assert args.strategy == "broad_anomaly_v1_h30"
    assert args.days == 30
