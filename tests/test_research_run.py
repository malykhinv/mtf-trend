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
            research_mode="frozen_holdout",
            protocol_freeze_id="smoke-freeze",
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
    assert (run_dir / "strategy_run_config.csv").is_file()
    assert (run_dir / "anomaly_run_config.csv").is_file()
    assert (run_dir / "artifact_manifest.json").is_file()

    ledger = pd.read_csv(run_dir / "stages" / "holdout_governance" / "research_ledger.csv")
    assert ledger.loc[0, "protocol_freeze_id"] == "smoke-freeze"
    assert ledger.loc[0, "final_holdout_start_date"] == "2024-01-01"

    access_log = pd.read_csv(run_dir / "stages" / "holdout_governance" / "holdout_access_log.csv")
    assert len(access_log) == 1
    assert bool(access_log.loc[0, "access_approved"])

    summary = pd.read_csv(run_dir / "research_run_summary.csv")
    summary_by_key = dict(zip(summary["key"], summary["value"], strict=True))
    assert summary_by_key["research_start_date"] == "2024-01-01"
    assert summary_by_key["research_end_date"] == "2024-01-01"
    assert summary_by_key["research_mode"] == "frozen_holdout"
    assert summary_by_key["protocol_freeze_id"] == "smoke-freeze"
    assert summary_by_key["forensic_audit_dir"].endswith("stages/forensic_audit") or summary_by_key["forensic_audit_dir"].endswith("stages\\forensic_audit")
    assert summary_by_key["forensic_audit_status"] in {"PASS", "WARN"}
    assert summary_by_key["forensic_audit_fail_count"] == "0"

    run_config = pd.read_csv(run_dir / "strategy_run_config.csv")
    run_config_by_key = dict(zip(run_config["key"], run_config["value"].astype(str), strict=True))
    assert run_config_by_key["strategy_name"] == "broad_anomaly_v1_h30"
    assert run_config_by_key["target_horizon_minutes"] == "30"
    assert run_config_by_key["active_h_max_minutes"] == "30"
    assert run_config_by_key["forensic_audit_status"] in {"PASS", "WARN"}
    assert run_config_by_key["data_snapshot_hash"]
    assert run_config_by_key["config_hash"]


def test_run_research_cli_accepts_strategy_and_optional_days() -> None:
    args = build_parser().parse_args(["run-research", "broad_anomaly_v1_h30", "--days", "30"])

    assert args.command == "run-research"
    assert args.strategy == "broad_anomaly_v1_h30"
    assert args.days == 30
    assert args.research_mode == "is"
    assert args.holdout_days == 60


def test_run_research_config_requires_freeze_id_for_frozen_holdout(tmp_path: Path) -> None:
    try:
        ResearchRunConfig(
            strategy_name="broad_anomaly_v1_h30",
            cache_dir=tmp_path,
            research_mode="frozen_holdout",
        )
    except ValueError as exc:
        assert "protocol_freeze_id" in str(exc)
    else:
        raise AssertionError("frozen_holdout mode must require protocol_freeze_id")


def test_is_mode_holdout_lock_filters_downstream_input(tmp_path: Path) -> None:
    from anomaly_science.research.run import _apply_holdout_lock_to_input

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    first_day = 1704067200000
    second_day = first_day + 86_400_000
    candles = pd.DataFrame(
        {
            "symbol": ["AAAUSDT", "AAAUSDT"],
            "open_time_ms": [first_day, second_day],
            "available_time_ms": [first_day + 60_000, second_day + 60_000],
            "open": [1.0, 2.0],
            "high": [1.1, 2.1],
            "low": [0.9, 1.9],
            "close": [1.0, 2.0],
            "volume": [1.0, 1.0],
            "quote_volume": [1.0, 1.0],
            "number_of_trades": [1, 1],
            "taker_buy_quote_volume": [0.5, 0.5],
        }
    )
    candles.to_csv(input_dir / "candles_1m.csv", index=False)
    candles.to_csv(input_dir / "candles_5m.csv", index=False)
    pd.DataFrame(
        {
            "symbol": ["AAAUSDT", "AAAUSDT"],
            "timestamp_ms": [first_day, second_day],
            "available_time_ms": [first_day + 300_000, second_day + 300_000],
            "open_interest": [1.0, 2.0],
            "source": ["fixture", "fixture"],
        }
    ).to_csv(input_dir / "open_interest_5m.csv", index=False)

    start_date, end_date = _apply_holdout_lock_to_input(
        input_dir=input_dir,
        full_start_date=pd.to_datetime(first_day, unit="ms", utc=True).date(),
        full_end_date=pd.to_datetime(second_day, unit="ms", utc=True).date(),
        holdout_days=1,
        research_mode="is",
    )

    assert start_date.isoformat() == "2024-01-01"
    assert end_date.isoformat() == "2024-01-01"
    filtered = pd.read_csv(input_dir / "candles_1m.csv")
    assert filtered["open_time_ms"].tolist() == [first_day]
