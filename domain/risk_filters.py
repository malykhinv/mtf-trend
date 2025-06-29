from domain.models.bar import Bar
from typing import List
import statistics

# TODO FIXME
def is_stablecoin(symbol: str) -> bool:
    return False
    return any(stable in symbol.upper() for stable in ["USDC", "BUSD", "DAI", "TUSD"])

# TODO FIXME
def has_messy_candles(bars: List[Bar], tail_ratio_threshold: float = 0.5, body_threshold: float = 0.1) -> bool:
    """
    tail_ratio_threshold: доля свечей с длинными хвостами (tail/total range > 0.5)
    body_threshold: минимальный средний body size / range для чистых свечей
    """
    return False
    messy_count = 0
    total = len(bars[-20:])
    for bar in bars[-20:]:
        range_ = bar.high - bar.low
        upper_tail = bar.high - max(bar.close, bar.open)
        lower_tail = min(bar.close, bar.open) - bar.low

        if range_ == 0:
            continue
        if (upper_tail + lower_tail) / range_ > tail_ratio_threshold:
            messy_count += 1

    avg_body_ratio = statistics.mean(
        [abs(b.close - b.open) / (b.high - b.low) if (b.high - b.low) > 0 else 0 for b in bars[-20:]]
    )
    return messy_count / total > 0.3 or avg_body_ratio < body_threshold

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
