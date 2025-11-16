from typing import Optional

from crypto_screener.config.config import cfg
from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.setup import Setup
from crypto_screener.domain.models.swing import SwingType, Swing


# region Private
def _trim_by_volume(bars: list[Bar]) -> list[Bar]:
    length = len(bars)
    if length == 0:
        return []

    min_side_bars = cfg.VOLUME_TRIM_SIDE_BARS_MIN
    if length < 2 * min_side_bars:
        return []

    volumes = [b.volume for b in bars]

    prefix = [0.0] * (length + 1)
    for i, v in enumerate(volumes):
        prefix[i + 1] = prefix[i] + v

    best_idx: Optional[int] = None
    best_ratio: float = float("-inf")

    for split in range(min_side_bars, length - min_side_bars + 1):
        left_len = split
        right_len = length - split

        if left_len <= 0 or right_len <= 0:
            continue

        left_sum = prefix[split] - prefix[0]
        right_sum = prefix[length] - prefix[split]

        left_avg = left_sum / left_len if left_len > 0 else 0.0
        if left_avg <= 0:
            continue

        right_avg = right_sum / right_len if right_len > 0 else 0.0
        ratio = right_avg / left_avg

        if ratio > best_ratio:
            best_ratio = ratio
            best_idx = split

    if best_idx is None:
        return []

    if best_ratio < cfg.HIGH_VOLUME_THRESHOLD:
        return []

    left_avg = (prefix[best_idx] - prefix[0]) / best_idx
    right_volumes = volumes[best_idx:]
    right_len = len(right_volumes)
    if right_len == 0:
        return []

    high_volume_factor = cfg.HIGH_VOLUME_THRESHOLD
    min_high_fraction = cfg.HIGH_VOLUME_FRACTION_MIN

    high_threshold = left_avg * high_volume_factor
    high_count = sum(1 for v in right_volumes if v >= high_threshold)

    if high_count / right_len < min_high_fraction:
        return []

    return bars[best_idx:]


def _get_main_rising_swings_indexed(bars: list[Bar]) -> list[tuple[int, Swing]]:
    main_low = _get_first_open_swing_indexed(bars, SwingType.LOW)
    if not main_low:
        return []
    main_high = _get_first_open_swing_indexed(bars, SwingType.HIGH)
    if not main_high:
        return []
    main_low_idx, _ = main_low
    main_high_idx, _ = main_high
    if main_high_idx <= main_low_idx:
        return []
    return [main_low, main_high]


def _get_open_swings(
        bars: list[Bar],
        swing_type: SwingType,
) -> list[Swing] | None:
    swings = []
    for idx in range(0, len(bars)):
        swing = bars[idx].swing
        if swing is None:
            continue
        if not swing.is_open:
            continue
        if swing.type != swing_type:
            continue
        swings.append(swing)
    return swings


def _get_first_open_swing_indexed(
        bars: list[Bar],
        swing_type: SwingType,
) -> tuple[int, Swing] | None:
    for idx in range(0, len(bars)):
        swing = bars[idx].swing
        if swing is None:
            continue
        if not swing.is_open:
            continue
        if swing.type != swing_type:
            continue
        return idx, swing
    return None


def _filter_by_price(
        swings: list[Swing],
        price_min: float,
        price_max: float
) -> list[Swing]:
    filtered = []
    if price_min <= 0 or price_min >= price_max:
        raise ValueError(f"Некорректные границы цены: {price_min}..{price_max}.")
    for swing in swings:
        if price_min <= swing.price <= price_max:
            filtered.append(swing)
    return filtered


def _get_cascade(swings: list[Swing], length_min: int, range_max: float) -> list[Swing]:
    cascade = []
    if not swings or length_min <= 0 or range_max < 0:
        return cascade

    best_start = -1
    best_end = -1

    for start_idx in range(len(swings)):
        min_price = float("inf")
        max_price = float("-inf")
        for end_idx in range(start_idx, len(swings)):
            price = swings[end_idx].price
            if price < min_price:
                min_price = price
            if price > max_price:
                max_price = price

            if max_price - min_price > range_max:
                break

            current_length = end_idx - start_idx + 1
            if current_length < length_min:
                continue

            best_length = best_end - best_start + 1 if best_start != -1 else 0
            if current_length > best_length or (
                    current_length == best_length and (best_start == -1 or start_idx < best_start)):
                best_start = start_idx
                best_end = end_idx

    if best_start == -1:
        return cascade

    cascade = swings[best_start:best_end + 1]
    return cascade


# endregion


def detect_setup(bars: list[Bar]) -> Setup | None:
    setup = None

    # Анализ повышения объемов.
    bars = _trim_by_volume(bars)
    if not bars:
        return setup

    # Анализ роста.
    main_rising_swings = _get_main_rising_swings_indexed(bars)
    if not main_rising_swings:
        return setup
    main_low, main_high = main_rising_swings
    if not main_low or not main_high:
        return setup
    _, main_low_swing = main_low
    main_high_index, main_high_swing = main_high
    rise = main_high_swing.price - main_low_swing.price
    rise_pct = 100 * rise / main_low_swing.price
    is_rise_valid = rise > 0 and rise_pct >= cfg.PRICE_RISE_PCT_MIN
    if not is_rise_valid:
        return setup

    # Анализ коррекции.
    correction_bars = bars[main_high_index + 1:]
    correction_low = _get_first_open_swing_indexed(correction_bars, SwingType.LOW)
    if not correction_low:
        return setup
    _, correction_low_swing = correction_low
    retrace_range = main_high_swing.price - correction_low_swing.price
    retrace_ratio = retrace_range / rise
    is_retrace_valid = retrace_range >= 0 and retrace_ratio <= cfg.RETRACE_RATIO_MAX
    if not is_retrace_valid:
        return setup

    # Анализ лонгового каскада.
    open_high_swings = _get_open_swings(correction_bars, SwingType.HIGH)
    cascade_price_min = correction_low_swing.price + retrace_range * cfg.CASCADE_RETRACE_RATIO_MIN
    cascade_price_max = main_high_swing.price
    open_high_swings = _filter_by_price(open_high_swings, cascade_price_min, cascade_price_max)
    cascade_range_max = retrace_range * cfg.CASCADE_RANGE_RATIO_MAX
    cascade_long = _get_cascade(open_high_swings, cfg.CASCADE_LENGTH_MIN, cascade_range_max)
    if not cascade_long:
        return setup

    # Анализ сопротивления над каскадом.
    resistance_price_min = max(cascade_long, key=lambda swing: swing.price).price
    resistance_price_max = main_high_swing.price
    resistance_swings = _filter_by_price(open_high_swings, resistance_price_min, resistance_price_max)
    has_resistance = len(resistance_swings) > cfg.RESISTANCE_COUNT_MAX
    if has_resistance:
        return setup

    setup = Setup.CAPTURE

    # Анализ поддержки под каскадом.
    initial_swing = cascade_long[0]
    consolidation_range = initial_swing.price - correction_low_swing.price
    open_low_swings = _get_open_swings(correction_bars, SwingType.LOW)
    support_price_min = correction_low_swing.price + consolidation_range * cfg.SUPPORT_CONSOLIDATION_RATIO_MIN
    support_price_max = initial_swing.price
    open_low_swings = _filter_by_price(open_low_swings, support_price_min, support_price_max)
    support = open_low_swings[-1] if open_low_swings else None
    has_support = support is not None
    if not has_support:
        return setup

    # Анализ пробоя лонгового каскада.
    target_swing = cascade_long[-1]
    current_bar = correction_bars[-1]
    has_breakout_long = current_bar.close > target_swing.price
    if not has_breakout_long:
        return setup

    # Пробой каскада.
    setup = Setup.ORDER
    return setup
