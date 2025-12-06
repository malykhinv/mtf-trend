from crypto_screener.config.config import cfg
from crypto_screener.domain.models.bar import Bar


def trim_by_volume(bars: list[Bar]) -> list[Bar]:
    length = len(bars)
    min_side_bars = cfg.VOLUME_TRIM_SIDE_BARS_MIN
    if length < 2 * min_side_bars:
        return []
    volumes = [bar.volume for bar in bars]
    prefix = [0.0] * (length + 1)
    for i, v in enumerate(volumes):
        prefix[i + 1] = prefix[i] + v
    best_ratio = -1.0
    best_index = -1
    high_volume_factor = cfg.HIGH_VOLUME_THRESHOLD
    min_high_fraction = cfg.HIGH_VOLUME_FRACTION_MIN
    for split in range(min_side_bars, length - min_side_bars + 1):
        left_avg = (prefix[split] - prefix[0]) / split
        if left_avg <= 0:
            continue
        right_avg = (prefix[length] - prefix[split]) / (length - split)
        ratio = right_avg / left_avg
        if ratio > best_ratio:
            best_ratio = ratio
            best_index = split
    if best_ratio < high_volume_factor or best_index == -1:
        return []
    left_avg = (prefix[best_index] - prefix[0]) / best_index
    right_volumes = volumes[best_index:]
    high_threshold = left_avg * high_volume_factor
    high_count = sum(1 for v in right_volumes if v >= high_threshold)
    if (high_count / len(right_volumes)) < min_high_fraction:
        return []
    right_count = len(bars) - best_index
    start_index = max(0, best_index - right_count)
    return bars[start_index:]
