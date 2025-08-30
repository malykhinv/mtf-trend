from dataclasses import dataclass
from datetime import datetime

from config.constants import FLOAT_UNDEFINED


@dataclass
class Bar:
    """
    Описывает один бар/свечу на рынке (OHLCV + oi + ATR).
    timestamp: момент времени бара
    open, high, low, close: цены открытия, максимума, минимума, закрытия
    volume: объём
    tbq: taker buy quote
    oi: open interest (может отсутствовать)
    atr: индекс ATR данного бара
    """
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    tbq: float = FLOAT_UNDEFINED
    oi: float = FLOAT_UNDEFINED
    atr: float = FLOAT_UNDEFINED