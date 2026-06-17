from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FeatureFamily(str, Enum):
    PRICE_PATH = "price_path"
    SPEED_TIME = "speed_time"
    VOLUME = "volume"
    FLOW = "flow"
    OPEN_INTEREST = "open_interest"
    LIQUIDATION = "liquidation"
    MARKET_CONTEXT = "market_context"
    STRUCTURE = "structure"
    DATA_QUALITY = "data_quality"


@dataclass(frozen=True, slots=True)
class FeatureCatalogRow:
    feature_name: str
    family: FeatureFamily
    dtype: str
    description: str
    availability_rule: str
    uses_future_data: bool = False
    nullable: bool = True

    def __post_init__(self) -> None:
        if not self.feature_name:
            raise ValueError("feature_name is required")
        if self.uses_future_data:
            raise ValueError("feature catalog rows for model features must not use future data")
        if not self.dtype:
            raise ValueError("dtype is required")
        if not self.availability_rule:
            raise ValueError("availability_rule is required")
