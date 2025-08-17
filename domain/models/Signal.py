from dataclasses import dataclass

from domain.models.Bar import Bar
from domain.models.Exchange import Exchange
from domain.models.Timeframe import Timeframe


@dataclass(frozen=True, slots=True)
class Signal:
    symbol: str
    exchange: Exchange
    timeframe: Timeframe
    extremums: list[Bar]
    chart_path: str
