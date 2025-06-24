# domain/risk_filters.py
from domain.models.bar import Bar
from typing import List
import statistics


def is_low_liquidity(bars: List[Bar], threshold_usd: float = 100000) -> bool:
    avg_volume = statistics.mean([b.volume for b in bars[-20:]])
    return avg_volume < threshold_usd


def is_news_spike(bars: List[Bar], spike_multiplier: float = 3.0, body_ratio: float = 2.0) -> bool:
    recent = bars[-1]
    volumes = [b.volume for b in bars[-21:-1]]
    avg_volume = statistics.mean(volumes)

    range_ = recent.high - recent.low
    avg_range = statistics.mean([b.high - b.low for b in bars[-21:-1]])

    volume_spike = recent.volume > spike_multiplier * avg_volume
    body_spike = range_ > body_ratio * avg_range

    return volume_spike and body_spike


def is_in_dead_hours(hour_utc: int) -> bool:
    return 3 <= hour_utc <= 6
