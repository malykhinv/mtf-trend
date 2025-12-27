from abc import ABC
from dataclasses import dataclass
from typing import Optional

from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.cascade_type import CascadeType
from crypto_screener.domain.models.swing import Swing
from crypto_screener.domain.models.timeframe import Timeframe


@dataclass
class SetupData(ABC):
    symbol: str
    timeframe: Timeframe
    bars: list[Bar]


@dataclass
class Ppo(SetupData):
    main_low_swing: Optional[Swing]
    main_high_swing: Optional[Swing]
    cascade_swings: Optional[list[Swing]]
    resistance_swings: Optional[list[Swing]]
    support_swings: Optional[list[Swing]]


@dataclass
class Gu(SetupData):
    direction: CascadeType
    level_price: Optional[float]
    open_extremums: Optional[list[float]]
    atr: Optional[float] = None
    current_price: Optional[float] = None
    distance_to_level: Optional[float] = None
    distance_atr_ratio: Optional[float] = None
    cascade_swings: Optional[list[Swing]] = None
    support_swings: Optional[list[Swing]] = None
