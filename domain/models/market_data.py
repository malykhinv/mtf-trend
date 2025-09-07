from dataclasses import dataclass
from typing import Tuple

from .enums import Side


@dataclass(frozen=True)
class AggTrade:
    symbol: str
    price: float
    quantity: float
    timestamp: int


@dataclass(frozen=True)
class DepthSnapshot:
    symbol: str
    bids: Tuple[Tuple[float, float], ...]
    asks: Tuple[Tuple[float, float], ...]
    timestamp: int


@dataclass(frozen=True)
class LiquidationEvent:
    symbol: str
    side: Side
    price: float
    quantity: float
    timestamp: int
