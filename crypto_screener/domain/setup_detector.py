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
    main_low = _get_first_open_swing(bars, swing_type=SwingType.LOW)
    main_low_idx, _ = main_low
    main_high = _get_first_open_swing(bars[main_low_idx + 1:], swing_type=SwingType.HIGH)
    return [main_low, main_high]


def _get_first_open_swing(
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


def _get_cascade_long(bars: list[Bar]) -> list[Swing]:
    cascade = []
    main_high = _get_first_open_swing(bars, swing_type=SwingType.HIGH)
    if main_high is None:
        return cascade
    main_high_idx, main_high_swing = main_high
    # TODO
    return cascade


# endregion


def detect_setup(bars: list[Bar]) -> Setup | None:
    setup = None
    bars = _trim_by_volume(bars)
    if not bars:
        return None

    # Анализ роста.
    main_low, main_high = _get_main_rising_swings_indexed(bars)
    if not main_low or not main_high:
        return setup
    _, main_low_swing = main_low
    main_high_index, main_high_swing = main_high
    rise = main_high_swing.price - main_low_swing.price
    if rise <= 0:
        return setup

    # Анализ коррекции.
    correction_bars = bars[main_high_index + 1:]
    retrace_low = _get_first_open_swing(correction_bars, swing_type=SwingType.LOW)
    if not retrace_low:
        return setup
    _, retrace_low_swing = retrace_low
    retrace = main_high_swing.price - retrace_low_swing.price
    if retrace <= 0:
        return setup
    has_dump = retrace >= rise * cfg.RETRACE_RATIO_MAX
    if has_dump:
        return setup

    # Анализ лонгового каскада.
    cascade_long = _get_cascade_long(correction_bars)
    has_cascade_long = len(cascade_long) >= cfg.CASCADE_LENGTH_MIN
    if not has_cascade_long:
        return setup

    # Анализ сопротивления над каскадом.
    resistance = _get_resistance(bars, cascade_long)
    has_resistance = len(resistance) > cfg.RESISTANCE_COUNT_MAX
    if has_resistance:
        return setup

    setup = Setup.CAPTURE

    # Анализ поддержки под каскадом.
    support = _get_support(bars)
    has_support = False
    if not has_support:
        return setup

    # Анализ пробоя лонгового каскада.
    has_breakout_long = _check_if_has_breakout_high(bars, cascade_long)
    if not has_breakout_long:
        return setup

    # Пробой каскада.
    setup = Setup.ORDER
    return setup
