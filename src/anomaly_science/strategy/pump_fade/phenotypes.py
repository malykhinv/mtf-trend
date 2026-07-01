from __future__ import annotations

import json
from pathlib import Path

from anomaly_science.market_context import (
    event_positioning_feature_names,
    perp_crowding_feature_names,
    reference_market_feature_names,
    reference_positioning_feature_names,
)
from anomaly_science.phenotypes import (
    CrossFittedPhenotypeConfig,
    cross_fitted_phenotype_config_from_mapping,
)
from anomaly_science.strategy.pump_fade.market_context import (
    PUMP_FADE_PERP_CROWDING_CONTEXT,
    PUMP_FADE_REFERENCE_MARKET_CONTEXT,
    PUMP_FADE_REFERENCE_POSITIONING_CONTEXT,
    PUMP_FADE_SYMBOL_POSITIONING_CONTEXT,
)
from anomaly_science.strategy.pump_fade.spec import PUMP_FADE_DATASET_FEATURES


PUMP_FADE_BROAD_PHENOTYPE_SURFACE_VERSION = "pump_fade_broad_phenotype_surface_v2"


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


PUMP_FADE_BROAD_PHENOTYPE_NUMERIC_FEATURES = _unique(
    tuple(
        spec.name
        for spec in PUMP_FADE_DATASET_FEATURES
        if spec.is_model_feature and spec.dtype != "str"
    )
    + reference_market_feature_names(PUMP_FADE_REFERENCE_MARKET_CONTEXT)
    + reference_positioning_feature_names(PUMP_FADE_REFERENCE_POSITIONING_CONTEXT)
    + event_positioning_feature_names(PUMP_FADE_SYMBOL_POSITIONING_CONTEXT)
    + perp_crowding_feature_names(PUMP_FADE_PERP_CROWDING_CONTEXT)
    + (
        "has_btc_context", "has_eth_context",
        "has_btc_positioning_context", "has_eth_positioning_context",
        "has_symbol_positioning_context", "has_symbol_positioning_at_ignition",
        "symbol_positioning_complete", "has_symbol_premium_context",
        "has_symbol_funding_context", "perp_crowding_complete",
    )
)

PUMP_FADE_BROAD_PHENOTYPE_CATEGORICAL_FEATURES = ("session",)


def load_pump_fade_phenotype_config(path: Path) -> CrossFittedPhenotypeConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    surface = raw.pop("feature_surface", None)
    if surface != PUMP_FADE_BROAD_PHENOTYPE_SURFACE_VERSION:
        raise ValueError(
            "pump-fade phenotype feature_surface must equal the registered strategy surface"
        )
    if "numeric_features" in raw or "categorical_features" in raw:
        raise ValueError("pump-fade phenotype features are owned by the registered surface")
    raw["numeric_features"] = list(PUMP_FADE_BROAD_PHENOTYPE_NUMERIC_FEATURES)
    raw["categorical_features"] = list(PUMP_FADE_BROAD_PHENOTYPE_CATEGORICAL_FEATURES)
    return cross_fitted_phenotype_config_from_mapping(raw)


__all__ = [
    "PUMP_FADE_BROAD_PHENOTYPE_CATEGORICAL_FEATURES",
    "PUMP_FADE_BROAD_PHENOTYPE_NUMERIC_FEATURES",
    "PUMP_FADE_BROAD_PHENOTYPE_SURFACE_VERSION",
    "load_pump_fade_phenotype_config",
]
