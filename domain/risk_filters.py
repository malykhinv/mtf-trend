from domain.models.bar import Bar
from typing import List
import statistics
from utils.logger import log

def is_low_liquidity(bars: List[Bar], threshold_usd: float = 100000) -> bool:
    avg_volume = statistics.mean([b.volume for b in bars[-20:]])
    low = avg_volume < threshold_usd
    if low:
        log(f"Низкая ликвидность: средний объём за 20 свечей — {int(avg_volume)}.")
    return low

def is_abnormal_spike(bars: List[Bar], spike_multiplier: float = 3.0, body_ratio: float = 2.0) -> bool:
    recent = bars[-1]
    volumes = [b.volume for b in bars[-21:-1]]
    avg_volume = statistics.mean(volumes)

    range_ = recent.high - recent.low
    avg_range = statistics.mean([b.high - b.low for b in bars[-21:-1]])

    volume_spike = recent.volume > spike_multiplier * avg_volume
    body_spike = range_ > body_ratio * avg_range

    if volume_spike and body_spike:
        log(f"Резкий всплеск: объём = {int(recent.volume)}, диапазон = {round(range_, 4)}.")

    return volume_spike and body_spike