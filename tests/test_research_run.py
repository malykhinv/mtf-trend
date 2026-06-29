from __future__ import annotations

from pathlib import Path

import pandas as pd

from anomaly_science.cli import build_parser
from anomaly_science.research import ResearchRunConfig, run_research_pipeline
from anomaly_science.research.run import _effective_holdout_days, _resolve_research_input_view, _state_config_for_strategy


def _write_cache(path: Path, *, day_count: int = 1) -> None:
    path.mkdir()
    base_timestamp = 1704067200000
    timestamps = [
        base_timestamp + day_offset * 86_400_000 + minute_offset * 60_000
        for day_offset in range(day_count)
        for minute_offset in range(4)
    ]
    pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [100.0 + index * 0.5 for index in range(len(timestamps))],
            "high": [101.0 + index * 0.5 for index in range(len(timestamps))],
            "low": [99.5 + index * 0.5 for index in range(len(timestamps))],
            "close": [100.5 + index * 0.5 for index in range(len(timestamps))],
            "volume": [10.0 + index for index in range(len(timestamps))],
            "quote_volume": [1005.0 + index * 100.0 for index in range(len(timestamps))],
            "trade_count": [20 + index for index in range(len(timestamps))],
            "taker_buy_quote_volume": [550.0 + index * 50.0 for index in range(len(timestamps))],
            "open_interest": [1000.0 + index for index in range(len(timestamps))],
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
    assert (run_dir / "input" / "cache_export_coverage.csv").is_file()
    assert (run_dir / "input" / "cache_export_manifest.json").is_file()
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
    assert (run_dir / "strategy_stage_timings.csv").is_file()
    assert (run_dir / "anomaly_stage_timings.csv").is_file()
    assert (run_dir / "strategy_resource_usage.csv").is_file()
    assert (run_dir / "anomaly_resource_usage.csv").is_file()
    assert (run_dir / "research_run.log").is_file()
    assert (run_dir / "artifact_manifest.json").is_file()

    live_log = (run_dir / "research_run.log").read_text(encoding="utf-8")
    assert "run-research stage START cache_export" in live_log
    assert "run-research stage PASS summary" in live_log

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
    assert summary_by_key["forensic_evidence_mode"] == "development"
    assert summary_by_key["forensic_evidence_status"] in {"DEVELOPMENT_ONLY", "NON_EVIDENTIAL_WARN"}
    assert summary_by_key["forensic_evidence_claim_allowed"] == "false"

    run_config = pd.read_csv(run_dir / "strategy_run_config.csv")
    run_config_by_key = dict(zip(run_config["key"], run_config["value"].astype(str), strict=True))
    assert run_config_by_key["strategy_name"] == "broad_anomaly_v1_h30"
    assert run_config_by_key["target_horizon_minutes"] == "30"
    assert run_config_by_key["active_h_max_minutes"] == "30"
    assert run_config_by_key["forensic_audit_status"] in {"PASS", "WARN"}
    assert run_config_by_key["forensic_evidence_mode"] == "development"
    assert run_config_by_key["forensic_evidence_status"] in {"DEVELOPMENT_ONLY", "NON_EVIDENTIAL_WARN"}
    assert run_config_by_key["forensic_evidence_claim_allowed"] == "false"
    assert run_config_by_key["data_snapshot_hash"]
    assert run_config_by_key["config_hash"]

    timings = pd.read_csv(run_dir / "strategy_stage_timings.csv")
    assert set(timings["stage_name"]).issuperset(
        {
            "cache_export",
            "state",
            "future",
            "feature_matrix",
            "prediction",
            "expected_value",
            "simulation",
            "forensic_audit",
            "summary",
        }
    )
    assert set(timings["status"]) == {"PASS"}
    assert (timings["duration_seconds"] >= 0).all()

    resources = pd.read_csv(run_dir / "strategy_resource_usage.csv")
    assert set(resources["stage_name"]).issuperset({"cache_export", "state", "feature_matrix", "summary"})
    assert set(resources["status"]) == {"PASS"}
    assert (resources["duration_seconds"] >= 0).all()
    assert (resources["run_dir_size_mb"] >= 0).all()
    assert (resources["run_dir_file_count"] > 0).all()
    assert "strategy_resource_usage.csv" in resources.iloc[-1]["largest_files"] or resources.iloc[-1]["largest_files"]

    coverage = pd.read_csv(run_dir / "input" / "cache_export_coverage.csv")
    assert coverage.loc[0, "symbol"] == "AAAUSDT"
    assert coverage.loc[0, "rows_1m"] == 4
    assert coverage.loc[0, "first_date"] == "2024-01-01"


def test_run_research_is_mode_auto_scales_holdout_for_short_windows(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    _write_cache(cache_dir, day_count=2)

    run_dir = run_research_pipeline(
        ResearchRunConfig(
            strategy_name="broad_anomaly_v1_h30",
            cache_dir=cache_dir,
            days=2,
            output_root=tmp_path / "runs",
        )
    )

    summary = pd.read_csv(run_dir / "research_run_summary.csv")
    summary_by_key = dict(zip(summary["key"], summary["value"], strict=True))
    assert summary_by_key["research_mode"] == "is"
    assert summary_by_key["requested_holdout_days"] == "60"
    assert summary_by_key["effective_holdout_days"] == "1"
    assert summary_by_key["research_start_date"] == "2024-01-01"
    assert summary_by_key["research_end_date"] == "2024-01-01"

    ledger = pd.read_csv(run_dir / "stages" / "holdout_governance" / "research_ledger.csv")
    assert ledger.loc[0, "final_holdout_start_date"] == "2024-01-02"
    assert pd.read_csv(run_dir / "stages" / "holdout_governance" / "holdout_access_log.csv").empty

    run_config = pd.read_csv(run_dir / "strategy_run_config.csv")
    run_config_by_key = dict(zip(run_config["key"], run_config["value"].astype(str), strict=True))
    assert run_config_by_key["holdout_days"] == "1"
    assert run_config_by_key["requested_holdout_days"] == "60"
    assert run_config_by_key["effective_holdout_days"] == "1"


def test_run_research_cli_accepts_strategy_and_optional_days() -> None:
    args = build_parser().parse_args(
        [
            "run-research",
            "broad_anomaly_v1_h30",
            "--days",
            "30",
            "--research-mode",
            "frozen_holdout",
            "--protocol-freeze-id",
            "freeze-id",
        ]
    )

    assert args.command == "run-research"
    assert args.strategy == "broad_anomaly_v1_h30"
    assert args.days == 30
    assert not hasattr(args, "prepared_input_dir")
    assert args.research_mode == "frozen_holdout"
    assert args.holdout_days == 60


def test_effective_holdout_days_keeps_non_holdout_rows_for_short_is_windows() -> None:
    assert _effective_holdout_days(
        full_start_date=pd.Timestamp("2024-01-01").date(),
        full_end_date=pd.Timestamp("2024-01-02").date(),
        requested_holdout_days=60,
        research_mode="is",
    ) == 1
    assert _effective_holdout_days(
        full_start_date=pd.Timestamp("2024-01-01").date(),
        full_end_date=pd.Timestamp("2024-01-09").date(),
        requested_holdout_days=60,
        research_mode="is",
    ) == 1
    assert _effective_holdout_days(
        full_start_date=pd.Timestamp("2024-01-01").date(),
        full_end_date=pd.Timestamp("2024-03-01").date(),
        requested_holdout_days=60,
        research_mode="is",
    ) == 53
    assert _effective_holdout_days(
        full_start_date=pd.Timestamp("2024-01-01").date(),
        full_end_date=pd.Timestamp("2024-03-31").date(),
        requested_holdout_days=60,
        research_mode="is",
    ) == 60


def test_run_research_state_window_uses_active_strategy_hmax() -> None:
    assert _state_config_for_strategy(strategy_name="broad_anomaly_v1_h30").max_state_minutes_after_detection == 30
    assert _state_config_for_strategy(strategy_name="post_anomaly_extension_v1_h180").max_state_minutes_after_detection == 180


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


def test_is_mode_research_input_view_does_not_mutate_prepared_input(tmp_path: Path) -> None:
    from anomaly_science.data.source import CsvDirectoryDataSource

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

    input_view = _resolve_research_input_view(
        full_start_date=pd.to_datetime(first_day, unit="ms", utc=True).date(),
        full_end_date=pd.to_datetime(second_day, unit="ms", utc=True).date(),
        holdout_days=1,
        research_mode="is",
    )

    assert input_view.start_date.isoformat() == "2024-01-01"
    assert input_view.end_date.isoformat() == "2024-01-01"
    assert input_view.max_input_time_ms == second_day
    source = CsvDirectoryDataSource(input_dir, max_time_ms=input_view.max_input_time_ms)
    filtered = source.read_frame("candles_1m", required=True)
    assert filtered is not None
    assert filtered["open_time_ms"].tolist() == [first_day]

    unmutated = pd.read_csv(input_dir / "candles_1m.csv")
    assert unmutated["open_time_ms"].tolist() == [first_day, second_day]


def test_run_research_reuses_prepared_input_without_cache_export(monkeypatch, tmp_path: Path) -> None:
    from anomaly_science.cache_export import CacheMvp1CsvExportConfig, export_cache_to_mvp1_csv
    import anomaly_science.research.run as research_run

    cache_dir = tmp_path / "cache"
    prepared_input_dir = tmp_path / "prepared_input"
    _write_cache(cache_dir)
    export_cache_to_mvp1_csv(CacheMvp1CsvExportConfig(cache_dir=cache_dir, out_dir=prepared_input_dir, days=1))

    def fail_export(*args, **kwargs):
        raise AssertionError("run-research must not export cache when prepared_input_dir is provided")

    monkeypatch.setattr(research_run, "export_cache_to_mvp1_csv", fail_export)

    run_dir = run_research_pipeline(
        ResearchRunConfig(
            strategy_name="broad_anomaly_v1_h30",
            cache_dir=cache_dir,
            days=1,
            output_root=tmp_path / "runs",
            prepared_input_dir=prepared_input_dir,
            research_mode="frozen_holdout",
            protocol_freeze_id="reuse-smoke-freeze",
        )
    )

    assert (run_dir / "prepared_input_reuse.json").is_file()
    summary = pd.read_csv(run_dir / "research_run_summary.csv")
    summary_by_key = dict(zip(summary["key"], summary["value"].astype(str), strict=True))
    assert summary_by_key["input_boundary_mode"] == "reused_prepared_input"
    assert summary_by_key["prepared_input_dir"] == str(prepared_input_dir)
    assert (prepared_input_dir / "candles_1m.csv").is_file()

    run_config = pd.read_csv(run_dir / "strategy_run_config.csv")
    run_config_by_key = dict(zip(run_config["key"], run_config["value"].astype(str), strict=True))
    assert run_config_by_key["input_boundary_mode"] == "reused_prepared_input"
    assert run_config_by_key["input_dir"] == str(prepared_input_dir)

    timings = pd.read_csv(run_dir / "strategy_stage_timings.csv")
    assert "input_reuse_validation" in set(timings["stage_name"])
    assert "cache_export" not in set(timings["stage_name"])


def test_run_research_prepared_input_reuse_is_blocked_for_mutating_is_mode(tmp_path: Path) -> None:
    try:
        ResearchRunConfig(
            strategy_name="broad_anomaly_v1_h30",
            cache_dir=tmp_path / "cache",
            prepared_input_dir=tmp_path / "prepared_input",
            research_mode="is",
        )
    except ValueError as exc:
        assert "only supported in frozen_holdout" in str(exc)
    else:
        raise AssertionError("prepared_input_dir must not be accepted for mutating IS mode")
