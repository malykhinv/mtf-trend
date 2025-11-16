from typing import Optional

from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.swing import Swing
from crypto_screener.domain.models.timeframe import Timeframe


def plot(
        symbol: str,
        timeframe: Timeframe,
        bars: list[Bar],
        main_high_swing: Optional[Swing],
        cascade_swings: list[Swing],
        resistance_swings: list[Swing],
        support_swings: list[Swing]
):
    if not symbol or not timeframe or not bars:
        raise ValueError("Недостаточно данных для построения графика.")