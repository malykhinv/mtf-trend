from domain.models.bar import Bar
from typing import List
import statistics

def is_low_liquidity(bars: List[Bar], threshold_usd: float = 100_000) -> bool:
    avg_volume = statistics.mean([b.volume for b in bars[-20:]])
    low = avg_volume < threshold_usd
    return low

def is_abnormal_spike(bars: List[Bar], spike_multiplier: float = 3.0, body_ratio: float = 2.0) -> bool:
    recent = bars[-1]
    volumes = [b.volume for b in bars[-21:-1]]
    avg_volume = statistics.mean(volumes)

    range_ = recent.high - recent.low
    avg_range = statistics.mean([b.high - b.low for b in bars[-21:-1]])

    volume_spike = recent.volume > spike_multiplier * avg_volume
    body_spike = range_ > body_ratio * avg_range

    return volume_spike and body_spike

def is_anomalous_trend(bars: List[Bar], atr: float, threshold: float = 5.0) -> bool:
    """
    Проверяет, было ли аномальное движение за последние 2 свечи (без коррекции).
    """
    if len(bars) < 3:
        return False

    recent_move = abs(bars[-1].close - bars[-3].open)
    return recent_move > threshold * atr
