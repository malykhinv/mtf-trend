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
    assert (run_dir / "stages" / "events" / "anomaly_events.csv").is_file()
    assert (run_dir / "research_run_summary.csv").is_file()


def test_run_research_cli_accepts_strategy_and_optional_days() -> None:
    args = build_parser().parse_args(["run-research", "broad_anomaly_v1_h30", "--days", "30"])

    assert args.command == "run-research"
    assert args.strategy == "broad_anomaly_v1_h30"
    assert args.days == 30
