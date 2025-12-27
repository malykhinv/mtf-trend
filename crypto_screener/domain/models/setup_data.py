from abc import ABC
from dataclasses import dataclass, field
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
    main_low_swing: Optional[Swing] = None
    main_high_swing: Optional[Swing] = None
    cascade_swings: list[Swing] = field(default_factory=list)
    resistance_swings: list[Swing] = field(default_factory=list)
    support_swings: list[Swing] = field(default_factory=list)
    level_price: Optional[float] = None
    current_price: Optional[float] = None


@dataclass
class Gu(SetupData):
    direction: CascadeType
    level_price: Optional[float] = None
    open_extremums: Optional[list[float]] = None
    cascade_swings: list[Swing] = field(default_factory=list)
    support_swings: list[Swing] = field(default_factory=list)
    atr: Optional[float] = None
    current_price: Optional[float] = None
    distance_to_level: Optional[float] = None
    distance_atr_ratio: Optional[float] = None
