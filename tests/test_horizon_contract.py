from __future__ import annotations

import pytest

from anomaly_science.contracts.horizons import (
    FUTURE_PATH_RETURN_HORIZONS,
    SUPPORTED_RESEARCH_HORIZONS,
    is_supported_research_horizon,
    research_horizon_label_available_column,
    research_horizon_label_column,
    validate_supported_research_horizon,
    validate_supported_research_horizons,
)
from anomaly_science.controls.config import ControlsConfig
from anomaly_science.decision.config import ExpectedValueConfig
from anomaly_science.future.config import FuturePathBuilderConfig
from anomaly_science.labels.config import OutcomeLabelConfig
from anomaly_science.prediction.config import WalkForwardPredictionConfig
from anomaly_science.simulation.config import TradeSimulationConfig
from anomaly_science.strategy.registry import StrategyRegistryError, validate_strategy_horizon


def test_core_supported_research_horizons_are_single_whitelist() -> None:
    assert SUPPORTED_RESEARCH_HORIZONS == (15, 30, 60, 120, 180)
    assert FUTURE_PATH_RETURN_HORIZONS == (5, *SUPPORTED_RESEARCH_HORIZONS)


def test_supported_research_horizon_validator_rejects_arbitrary_minutes() -> None:
    validate_supported_research_horizon(15)
    validate_supported_research_horizon(180)

    for invalid_horizon in (0, 11, 32, 181, True):
        with pytest.raises(ValueError, match="must be one of 15, 30, 60, 120, 180"):
            validate_supported_research_horizon(invalid_horizon)  # type: ignore[arg-type]
        assert not is_supported_research_horizon(invalid_horizon)  # type: ignore[arg-type]


def test_core_horizon_label_column_helpers_are_whitelist_backed() -> None:
    assert research_horizon_label_column(30) == "scenario_30m"
    assert research_horizon_label_column(180) == "scenario_180m"
    assert research_horizon_label_available_column(30) == "label_available_30m"

    with pytest.raises(ValueError, match="must be one of 15, 30, 60, 120, 180"):
        research_horizon_label_column(32)


def test_supported_research_horizon_tuple_validator_rejects_duplicates_and_unknowns() -> None:
    validate_supported_research_horizons(SUPPORTED_RESEARCH_HORIZONS)

    with pytest.raises(ValueError, match="duplicate"):
        validate_supported_research_horizons((15, 15))

    with pytest.raises(ValueError, match="must be one of 15, 30, 60, 120, 180"):
        validate_supported_research_horizons((15, 32))


def test_future_and_label_configs_read_core_horizon_constants() -> None:
    assert FuturePathBuilderConfig().future_return_horizons_minutes == FUTURE_PATH_RETURN_HORIZONS
    assert OutcomeLabelConfig().horizons_minutes == SUPPORTED_RESEARCH_HORIZONS

    with pytest.raises(ValueError, match="Core research horizons"):
        OutcomeLabelConfig(horizons_minutes=(15, 30, 60))


def test_registry_validates_executable_strategy_horizon_pairs() -> None:
    validate_strategy_horizon("broad_anomaly_v1_h30", 30)
    validate_strategy_horizon("broad_anomaly_v1_h60", 60)

    with pytest.raises(StrategyRegistryError, match="strategy/horizon mismatch"):
        validate_strategy_horizon("broad_anomaly_v1_h30", 60)

    with pytest.raises(StrategyRegistryError, match="must be one of 15, 30, 60, 120, 180"):
        validate_strategy_horizon("broad_anomaly_v1_h30", 32)

    with pytest.raises(StrategyRegistryError, match="unknown strategy_name"):
        validate_strategy_horizon("broad_anomaly_v1_h180", 180)

    with pytest.raises(StrategyRegistryError, match="specified but not implemented yet"):
        validate_strategy_horizon("post_pump_distribution_v1_h120", 120)


def test_target_horizon_configs_validate_strategy_horizon_pair() -> None:
    config_types = (
        WalkForwardPredictionConfig,
        ControlsConfig,
        ExpectedValueConfig,
        TradeSimulationConfig,
    )
    for config_type in config_types:
        config_type(target_horizon_minutes=30)
        config_type(strategy_name="broad_anomaly_v1_h60", target_horizon_minutes=60)

        with pytest.raises(StrategyRegistryError, match="must be one of 15, 30, 60, 120, 180"):
            config_type(target_horizon_minutes=32)

        with pytest.raises(StrategyRegistryError, match="strategy/horizon mismatch"):
            config_type(strategy_name="broad_anomaly_v1_h30", target_horizon_minutes=60)

        with pytest.raises(StrategyRegistryError, match="unknown strategy_name"):
            config_type(strategy_name="broad_anomaly_v1_h180", target_horizon_minutes=180)

        with pytest.raises(StrategyRegistryError, match="specified but not implemented yet"):
            config_type(strategy_name="post_pump_distribution_v1_h120", target_horizon_minutes=120)



def test_cli_target_horizon_arguments_use_core_supported_horizons() -> None:
    from anomaly_science.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(
        [
            "run-mvp1-prediction",
            "--state",
            "state.csv",
            "--labels",
            "labels.csv",
            "--features",
            "features.csv",
            "--out",
            "out",
            "--horizon-minutes",
            "180",
        ]
    )

    assert args.horizon_minutes == 180
    assert args.strategy_name == ""

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "run-mvp1-prediction",
                "--state",
                "state.csv",
                "--labels",
                "labels.csv",
                "--features",
                "features.csv",
                "--out",
                "out",
                "--horizon-minutes",
                "32",
            ]
        )


def test_low_level_cli_validates_strategy_horizon_pair_before_file_io(tmp_path, capsys) -> None:
    from anomaly_science.cli import main

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "run-mvp1-prediction",
                "--state",
                "missing_state.csv",
                "--labels",
                "missing_labels.csv",
                "--features",
                "missing_features.csv",
                "--out",
                str(tmp_path / "prediction"),
                "--horizon-minutes",
                "180",
            ]
        )

    assert exc_info.value.code == 2
    assert "unknown strategy_name" in capsys.readouterr().err


def test_low_level_cli_accepts_explicit_matching_strategy_name() -> None:
    from anomaly_science.cli import build_parser

    args = build_parser().parse_args(
        [
            "run-mvp1-controls",
            "--state",
            "state.csv",
            "--labels",
            "labels.csv",
            "--features",
            "features.csv",
            "--out",
            "out",
            "--strategy-name",
            "broad_anomaly_v1_h60",
            "--horizon-minutes",
            "60",
        ]
    )

    assert args.strategy_name == "broad_anomaly_v1_h60"
    assert args.horizon_minutes == 60
