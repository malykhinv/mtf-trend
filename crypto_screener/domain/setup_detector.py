from distutils.command.install import main_key
from email.contentmanager import maintype
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

    min_side_bars = cfg.VOLUME_TRIM_MIN_SIDE_BARS
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
    main_low_idx, _ = main_low
    main_high = _get_first_open_swing_indexed(bars[main_low_idx + 1:], SwingType.HIGH)
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


def _get_cascade_long(swings: list[Swing], length_min: int, range_max: float) -> list[Swing]:
    cascade = []
    # TODO
    return cascade


def _filter_by_price(swings: list[Swing], swing_type: SwingType, price: float) -> list[Swing]:
    filtered = []
    for swing in swings:
        if swing.type != swing_type:
            continue
        match swing.type:
            case SwingType.HIGH:
                if swing.price > price:
                    filtered.append(swing)

            case SwingType.LOW:
                if swing.price < price:
                    filtered.append(swing)

    return filtered


# endregion


def detect_setup(bars: list[Bar]) -> Setup | None:
    setup = None

    # Анализ повышения объемов.
    bars = _trim_by_volume(bars)
    if not bars:
        return setup

    # Анализ роста.
    main_low, main_high = _get_main_rising_swings_indexed(bars)
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
    _, retrace_low_swing = correction_low
    retrace = main_high_swing.price - retrace_low_swing.price
    retrace_ratio = retrace / rise
    is_retrace_valid = retrace >= 0 and retrace_ratio <= cfg.RETRACE_RATIO_MAX
    if not is_retrace_valid:
        return setup

    # Анализ лонгового каскада.
    open_high_swings = _get_open_swings(correction_bars, SwingType.HIGH)
    filter_price = correction_low + retrace * cfg.CASCADE_LONG_RETRACE_RATIO_MIN
    open_high_swings = _filter_by_price(open_high_swings, SwingType.HIGH, filter_price)
    cascade_range_max = retrace * cfg.CASCADE_RANGE_RATIO_MAX
    cascade_long = _get_cascade_long(open_high_swings, cfg.CASCADE_LENGTH_MIN, cascade_range_max)
    if not cascade_long:
        return setup

    # Анализ сопротивления над каскадом.
    resistance = _get_resistance(correction_bars, cascade_long)
    has_resistance = len(resistance) > cfg.RESISTANCE_COUNT_MAX
    if has_resistance:
        return setup

    setup = Setup.CAPTURE

    # Анализ поддержки под каскадом.
    support = _get_support(correction_bars)
    has_support = False
    if not has_support:
        return setup

    # Анализ пробоя лонгового каскада.
    has_breakout_long = _check_if_has_breakout_high(correction_bars, cascade_long)
    if not has_breakout_long:
        return setup

    # Пробой каскада.
    setup = Setup.ORDER
    return setup
