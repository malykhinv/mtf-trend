from __future__ import annotations

import pytest

from anomaly_science.contracts.horizons import (
    FUTURE_PATH_RETURN_HORIZONS,
    SUPPORTED_RESEARCH_HORIZONS,
    is_supported_research_horizon,
    validate_supported_research_horizon,
    validate_supported_research_horizons,
)
from anomaly_science.controls.config import ControlsConfig
from anomaly_science.decision.config import ExpectedValueConfig
from anomaly_science.future.config import FuturePathBuilderConfig
from anomaly_science.labels.config import OutcomeLabelConfig
from anomaly_science.prediction.config import WalkForwardPredictionConfig
from anomaly_science.simulation.config import TradeSimulationConfig


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


def test_target_horizon_configs_reject_arbitrary_minutes() -> None:
    config_types = (
        WalkForwardPredictionConfig,
        ControlsConfig,
        ExpectedValueConfig,
        TradeSimulationConfig,
    )
    for config_type in config_types:
        config_type(target_horizon_minutes=180)
        with pytest.raises(ValueError, match="target_horizon_minutes must be one of 15, 30, 60, 120, 180"):
            config_type(target_horizon_minutes=32)
