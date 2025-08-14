from dataclasses import dataclass

from domain.models.bar import Bar
from domain.models.exchange import Exchange
from domain.models.timeframe import Timeframe


@dataclass(frozen=True, slots=True)
class Signal:
    symbol: str
    exchange: Exchange
    timeframe: Timeframe
    extremums: list[Bar]
    chart_path: str
