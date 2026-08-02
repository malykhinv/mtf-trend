"""Residual-response and positioning-absorption research strategy."""

from anomaly_science.strategy.residual_absorption.spec import (
    RESIDUAL_ABSORPTION_RESEARCH_SPLIT,
    MarketImpulseSpec,
    ResidualResponseSpec,
    ResponseOutcomeSpec,
    ResidualAbsorptionResearchSpec,
    StructuralDistanceFloorSpec,
)
from anomaly_science.strategy.residual_absorption.data import (
    read_is_parquet_schema,
    read_is_symbol_minutes,
)

__all__ = [
    "RESIDUAL_ABSORPTION_RESEARCH_SPLIT",
    "MarketImpulseSpec",
    "ResidualResponseSpec",
    "ResponseOutcomeSpec",
    "ResidualAbsorptionResearchSpec",
    "StructuralDistanceFloorSpec",
    "read_is_parquet_schema",
    "read_is_symbol_minutes",
]
