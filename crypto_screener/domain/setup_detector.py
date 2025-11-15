from distutils.command.install import main_key
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


def _get_first_open_swing(
        bars: list[Bar],
        swing_type: SwingType,
        start_idx: int = 0,
) -> tuple[int, Swing] | None:
    for idx in range(start_idx, len(bars)):
        swing = bars[idx].swing
        if swing is None:
            continue
        if not swing.is_open:
            continue
        if swing.type != swing_type:
            continue
        return idx, swing
    return None


def _check_if_has_dump(bars: list[Bar]) -> bool:
    if not bars:
        return False

    first_low = _get_first_open_swing(bars, SwingType.LOW)
    if first_low is None:
        return False
    first_low_idx, first_low_swing = first_low

    main_high = _get_first_open_swing(bars, SwingType.HIGH, start_idx=first_low_idx + 1)
    if main_high is None:
        return False
    main_high_idx, main_high_swing = main_high

    retrace_low = _get_first_open_swing(bars, SwingType.LOW, start_idx=main_high_idx + 1)
    if retrace_low is None:
        return False
    _, retrace_low_swing = retrace_low

    rise = main_high_swing.price - first_low_swing.price
    if rise <= 0:
        return False

    retrace = main_high_swing.price - retrace_low_swing.price
    if retrace < 0:
        retrace = 0.0

    return retrace <= rise * cfg.RETRACE_RATIO_MAX


# endregion


def detect_setup(bars: list[Bar]) -> Setup | None:
    setup = None
    bars = _trim_by_volume(bars)

    # Анализ коррекции.
    has_dump = _check_if_has_dump(bars)
    if not has_dump:
        return setup

    # Анализ лонгового каскада.
    cascade_long = _get_cascade_long(bars)
    has_cascade_long = len(cascade_long) >= cfg.CASCADE_LENGTH_MIN
    if not has_cascade_long:
        return setup

    # Анализ сопротивления над каскадом.
    resistance = _get_resistance(bars, cascade_long)
    has_resistance = len(resistance) > cfg.RESISTANCE_COUNT_MAX
    if has_resistance:
        return setup

    setup = Setup.CAPTURE

    # Анализ пробоя лонгового каскада.
    has_breakout_long = _check_if_has_breakout_high(bars, cascade_long)
    if not has_breakout_long:
        return setup

    # Пробой каскада.
    setup = Setup.ORDER
    return setup
