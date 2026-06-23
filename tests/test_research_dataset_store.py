from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from anomaly_science.research import (
    ResearchDatasetBuildConfig,
    build_research_dataset,
    research_dataset_input_dir,
    validate_research_dataset_store,
)


def _write_cache(path: Path, *, day_count: int = 2) -> None:
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


def test_build_research_dataset_writes_input_phase_store(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    dataset_dir = tmp_path / "dataset"
    _write_cache(cache_dir)

    out = build_research_dataset(
        ResearchDatasetBuildConfig(
            strategy_name="broad_anomaly_v1_h30",
            cache_dir=cache_dir,
            out_dir=dataset_dir,
            days=2,
            max_phase="input",
            progress_every=0,
        )
    )

    assert out == dataset_dir
    assert (dataset_dir / "input" / "candles_1m.csv").exists()
    assert (dataset_dir / "input" / "cache_export_manifest.json").exists()
    assert (dataset_dir / "research_dataset_phases.csv").exists()
    manifest = json.loads((dataset_dir / "research_dataset_manifest.json").read_text(encoding="utf-8"))
    assert manifest["dataset_format_version"] == "research_dataset_store_v1"
    assert manifest["phase_contract_version"] == "phase_cache_v1"
    assert manifest["strategy_name"] == "broad_anomaly_v1_h30"
    assert manifest["requested_days"] == 2
    assert manifest["input_dir"] == "input"
    assert manifest["max_phase"] == "input"
    assert [phase["phase_name"] for phase in manifest["phases"]] == ["input"]

    validated = validate_research_dataset_store(dataset_dir)
    assert validated.dataset_format_version == "research_dataset_store_v1"
    assert research_dataset_input_dir(dataset_dir) == dataset_dir / "input"


def test_build_research_dataset_can_materialize_data_audit_phase(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    dataset_dir = tmp_path / "dataset"
    _write_cache(cache_dir)

    build_research_dataset(
        ResearchDatasetBuildConfig(
            strategy_name="broad_anomaly_v1_h30",
            cache_dir=cache_dir,
            out_dir=dataset_dir,
            days=2,
            max_phase="data_audit",
            progress_every=0,
        )
    )

    manifest = json.loads((dataset_dir / "research_dataset_manifest.json").read_text(encoding="utf-8"))
    assert [phase["phase_name"] for phase in manifest["phases"]] == ["input", "data_audit"]
    assert (dataset_dir / "stages" / "data_audit" / "strategy_data_quality.csv").exists()
