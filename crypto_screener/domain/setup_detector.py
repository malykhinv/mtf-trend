import numpy as np

from crypto_screener.config.config import cfg
from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.setup import Setup, Capture, Buy, Unfilled
from crypto_screener.domain.models.swing import SwingType, Swing
from crypto_screener.domain.models.symbol import Context
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.models.trade_levels import TradeLevels
from crypto_screener.domain.swing_detector import add_swings


# region Private.
def _trim_by_volume(bars: list[Bar]) -> list[Bar]:
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


def _get_shadow_range_pct(bars: list[Bar]) -> float:
    if not bars:
        return 0.0
    highs = np.array([bar.high for bar in bars])
    lows = np.array([bar.low for bar in bars])
    opens = np.array([bar.open for bar in bars])
    closes = np.array([bar.close for bar in bars])
    bar_ranges = highs - lows
    valid_ranges = bar_ranges > 0
    if not np.any(valid_ranges):
        return 0.0
    bodies = np.abs(opens - closes)
    shadows = np.maximum(bar_ranges - bodies, 0.0)
    total_range = np.sum(bar_ranges[valid_ranges])
    total_shadow = np.sum(shadows[valid_ranges])
    return 100 * total_shadow / total_range if total_range > 0 else 0.0


def _get_main_rising_swings_indexed(bars: list[Bar]) -> list[tuple[int, Swing]]:
    main_high = _get_first_open_swing_indexed(bars, SwingType.HIGH)
    if not main_high:
        return []
    main_high_index, main_high_swing = main_high
    
    min_low_price = float('inf')
    min_low_swing = None
    min_low_index = -1
    
    for i in range(main_high_index + 1):
        bar = bars[i]
        if bar.swing and bar.swing.type == SwingType.LOW and bar.swing.is_open:
            if bar.low < min_low_price:
                min_low_price = bar.low
                min_low_swing = bar.swing
                min_low_index = i
    
    if not min_low_swing or min_low_index == -1:
        return []
        
    main_low_swing = Swing(
        time=min_low_swing.time,
        price=min_low_price,
        type=SwingType.LOW,
        is_open=True
    )
    
    return [(min_low_index, main_low_swing), main_high]


def _get_open_swings(
        bars: list[Bar],
        swing_type: SwingType
) -> list[Swing]:
    return [bar.swing for bar in bars
            if bar.swing is not None
            and bar.swing.is_open
            and bar.swing.type == swing_type]


def _get_first_open_swing_indexed(
        bars: list[Bar],
        swing_type: SwingType,
) -> tuple[int, Swing] | None:
    for index in range(0, len(bars)):
        swing = bars[index].swing
        if swing is None:
            continue
        if not swing.is_open:
            continue
        if swing.type != swing_type:
            continue
        return index, swing
    return None


def _filter_by_price(
        swings: list[Swing],
        price_min: float,
        price_max: float
) -> list[Swing]:
    if price_min <= 0 or price_min >= price_max:
        raise ValueError(f"Некорректные границы цены: {price_min}..{price_max}.")
    return [swing for swing in swings if price_min <= swing.price <= price_max]


def get_cascade_long(
        bars: list[Bar],
        price_min: float,
        price_max: float
) -> list[Swing]:
    if not bars or price_min >= price_max:
        return []

    def _calculate_atr(window: int) -> float:
        if not bars or window <= 0:
            return 0.0
        window = min(window, len(bars))
        true_ranges = []
        prev_close = bars[0].close
        for bar in bars:
            high_low = bar.high - bar.low
            high_close = abs(bar.high - prev_close)
            low_close = abs(bar.low - prev_close)
            true_ranges.append(max(high_low, high_close, low_close))
            prev_close = bar.close
        atr_window = true_ranges[-window:]
        return float(np.mean(atr_window)) if atr_window else 0.0

    atr_window = cfg.CASCADE_ATR_WINDOW
    atr = _calculate_atr(atr_window)
    touch_tolerance = max(cfg.CASCADE_TOUCH_EPS_NATR * atr, 0.0)
    min_pullback = max(cfg.CASCADE_MIN_PULLBACK_NATR * atr, 0.0)
    min_pullback_bars = cfg.CASCADE_MIN_PULLBACK_BARS
    min_gap_bars = cfg.CASCADE_MIN_GAP_BARS

    levels = []

    for index, bar in enumerate(bars):
        if not price_min <= bar.high <= price_max:
            continue

        matched_level = None
        min_distance = float('inf')
        for level in levels:
            distance = abs(bar.high - level['price'])
            if distance <= touch_tolerance and distance < min_distance:
                matched_level = level
                min_distance = distance

        if matched_level is None:
            matched_level = {
                'price': bar.high,
                'is_crossed': False,
                'distance': 0.0,
                'touches_raw': []
            }
            levels.append(matched_level)
            min_distance = 0.0

        matched_level['distance'] = min(matched_level['distance'] or min_distance, min_distance)

        if matched_level['is_crossed']:
            continue

        if abs(bar.high - matched_level['price']) <= touch_tolerance:
            matched_level['touches_raw'].append(index)

        if bar.close > matched_level['price'] + touch_tolerance:
            matched_level['is_crossed'] = True

    def refine_touches(level_price: float, touches: list[int]) -> list[int]:
        if not touches:
            return []
        refined = touches[:]
        while True:
            changed = False
            new_touches = []
            last_touch = None
            for touch_index in refined:
                if last_touch is None:
                    new_touches.append(touch_index)
                    last_touch = touch_index
                    continue
                if touch_index - last_touch < min_gap_bars:
                    changed = True
                    continue
                pullback_bars = bars[last_touch + 1:touch_index]
                if not pullback_bars:
                    changed = True
                    continue
                pullback_depth = max((level_price - bar.low) for bar in pullback_bars)
                consecutive = 0
                max_consecutive = 0
                for bar in pullback_bars:
                    if bar.high <= level_price - min_pullback:
                        consecutive += 1
                        max_consecutive = max(max_consecutive, consecutive)
                    else:
                        consecutive = 0
                has_pullback = pullback_depth >= min_pullback and max_consecutive >= min_pullback_bars
                if not has_pullback:
                    changed = True
                    continue
                new_touches.append(touch_index)
                last_touch = touch_index
            if not changed:
                return new_touches
            refined = new_touches

    best_level_touches = []
    best_third_touch_index = -1

    for level in levels:
        touches = refine_touches(level['price'], level['touches_raw'])
        if len(touches) < cfg.CASCADE_LENGTH_MIN:
            continue
        third_touch_index = touches[cfg.CASCADE_LENGTH_MIN - 1]
        if third_touch_index > best_third_touch_index:
            best_level_touches = touches
            best_third_touch_index = third_touch_index

    if not best_level_touches:
        return []

    cascade_swings = []
    for touch_index in best_level_touches:
        bar = bars[touch_index]
        swing = bar.swing if bar.swing else Swing(
            time=bar.time,
            price=bar.high,
            type=SwingType.HIGH,
            is_open=True
        )
        cascade_swings.append(swing)

    return cascade_swings


# endregion


def detect_setup(
        symbol: str,
        bars: list[Bar],
        timeframe: Timeframe,
        context: Context,
) -> Setup:
    setup = Unfilled(
        symbol=symbol,
        timeframe=timeframe,
        bars=bars,
        main_low_swing=None,
        main_high_swing=None,
        cascade_swings=None,
        resistance_swings=None,
        support_swings=None
    )

    # Анализ повышения объемов.
    trimmed_bars = _trim_by_volume(bars)
    if trimmed_bars:
        bars = trimmed_bars
    elif not context.is_top:
        return setup

    # Анализ теней.
    shadows_pct = _get_shadow_range_pct(bars)
    if shadows_pct > cfg.SHADOW_RANGE_PCT_MAX:
        return setup

    bars = add_swings(bars, timeframe)
    setup.bars = bars

    # Анализ роста.
    main_rising_swings = _get_main_rising_swings_indexed(bars)
    if not main_rising_swings:
        return setup
    main_low, main_high = main_rising_swings
    if not main_low or not main_high:
        return setup
    main_low_index, main_low_swing = main_low
    main_high_index, main_high_swing = main_high
    
    # Центрирование относительно main_low_index.
    start_index = max(0, main_low_index - (len(bars) - main_low_index - 1))
    bars = bars[start_index:]
    setup.bars = bars
    main_low_index -= start_index
    main_high_index -= start_index
    
    setup.main_low_swing = main_low_swing
    setup.main_high_swing = main_high_swing
    rise = main_high_swing.price - main_low_swing.price
    rise_pct = 100 * rise / main_low_swing.price
    
    # Проверка максимального отката на участке роста.
    max_retrace = 0.0
    max_high = main_low_swing.price
    for i in range(main_low_index + 1, main_high_index + 1):
        bar = bars[i]
        max_high = max(max_high, bar.high)
        retrace = max_high - bar.low
        if rise > 0:  # Избегаем деления на ноль
            retrace_ratio = retrace / rise
            max_retrace = max(max_retrace, retrace_ratio)
    
    is_rise_valid = (
        rise > 0 and 
        rise_pct >= cfg.PRICE_RISE_PCT_MIN and
        max_retrace <= cfg.MAX_RETRACE_RATIO
    )
    
    if not is_rise_valid:
        return setup

    # Анализ коррекции.
    correction_bars = bars[main_high_index + 1:]
    if not correction_bars:
        return setup
    correction_low = _get_first_open_swing_indexed(correction_bars, SwingType.LOW)
    if not correction_low:
        return setup
    correction_low_index, correction_low_swing = correction_low
    retrace_range = main_high_swing.price - correction_low_swing.price
    retrace_ratio = retrace_range / rise
    is_retrace_valid = retrace_range >= 0 and retrace_ratio <= cfg.RETRACE_RATIO_MAX
    if not is_retrace_valid:
        return setup

    # Анализ цены до роста.
    pre_low_window_end = main_low_index
    pre_low_window_start = max(0, pre_low_window_end - len(correction_bars) + 1)
    pre_low_window = bars[pre_low_window_start:pre_low_window_end + 1]
    if pre_low_window:
        pre_low_above_correction_low_fraction = sum(1 for bar in pre_low_window
                                                    if bar.low > correction_low_swing.price) / len(pre_low_window)
        is_pre_low_above_correction_low_valid = (pre_low_above_correction_low_fraction <=
                                                 cfg.PRE_LOW_ABOVE_CORRECTION_LOW_FRACTION_MAX)
        if not is_pre_low_above_correction_low_valid:
            return setup

    # Анализ лонгового каскада.
    cascade_price_min = correction_low_swing.price + retrace_range * cfg.CASCADE_RETRACE_RATIO_MIN
    cascade_price_max = main_high_swing.price
    cascade_long = get_cascade_long(correction_bars, cascade_price_min, cascade_price_max)
    if not cascade_long:
        return setup
    cascade_top = max(cascade_long, key=lambda swing: swing.price).price
    resistance_gap = retrace_range * cfg.RESISTANCE_GAP_RATIO_MIN
    open_high_swings = _get_open_swings(correction_bars, SwingType.HIGH)
    open_high_swings = _filter_by_price(open_high_swings, cascade_price_min, cascade_price_max)
    extra_cascade_swings = [
        swing for swing in open_high_swings
        if cascade_top < swing.price <= cascade_top + resistance_gap
    ]
    cascade_long = cascade_long + [swing for swing in extra_cascade_swings if swing not in cascade_long]
    setup.cascade_swings = cascade_long
    if not cascade_long:
        return setup

    # Анализ сопротивления над каскадом.
    cascade_top = max(cascade_long, key=lambda swing: swing.price).price
    resistance_gap = retrace_range * cfg.RESISTANCE_GAP_RATIO_MIN
    resistance_price_min = cascade_top + resistance_gap
    resistance_price_max = main_high_swing.price - resistance_gap
    resistance_swings = []
    if resistance_price_min < resistance_price_max:
        resistance_swings = _filter_by_price(open_high_swings, resistance_price_min, resistance_price_max)
    setup.resistance_swings = resistance_swings
    has_resistance = len(resistance_swings) > cfg.RESISTANCE_COUNT_MAX
    if has_resistance:
        return setup

    # Анализ поддержки под каскадом.
    initial_swing = cascade_long[0]
    consolidation_range = initial_swing.price - correction_low_swing.price
    open_low_swings = _get_open_swings(correction_bars, SwingType.LOW)
    setup.support_swings = open_low_swings

    support_price_min = correction_low_swing.price + consolidation_range * cfg.SUPPORT_CONSOLIDATION_RATIO_MIN
    support_price_max = initial_swing.price
    open_low_swings = _filter_by_price(open_low_swings, support_price_min, support_price_max)
    support_swing = open_low_swings[-1] if open_low_swings else None
    has_support = support_swing is not None
    if not has_support:
        return setup

    # Анализ пробоя поддержки под каскадом.
    current_bar = correction_bars[-1]
    current_price = current_bar.close
    has_breakout_short = current_price < support_swing.price
    if has_breakout_short:
        return setup

    # Анализ риска и вознаграждения.
    profit_price = main_high_swing.price
    if context == Context.A:
        profit_price = main_high_swing.price + 2 * (main_high_swing.price - correction_low_swing.price)
    elif context == Context.B:
        profit_price = main_high_swing.price + (main_high_swing.price - correction_low_swing.price)
    target_swing = cascade_long[-1]
    loss_price = support_swing.price
    loss_pct = 100 * (loss_price - current_price) / loss_price
    is_loss_valid = abs(loss_pct) > cfg.LOSS_PCT_MIN
    if not is_loss_valid:
        return setup
    profit_pct = 100 * (profit_price - current_price) / current_price
    is_profit_valid = profit_pct > cfg.PROFIT_PCT_MIN
    if not is_profit_valid:
        return setup
    reward_risk = abs(profit_pct / loss_pct)
    is_reward_risk_valid = reward_risk >= cfg.REWARD_RISK_RATIO_MIN
    if not is_reward_risk_valid:
        return setup

    # Проторговка после отката с лонговым каскадом.
    setup = Capture(
        symbol=symbol,
        timeframe=timeframe,
        bars=bars,
        main_low_swing=main_low_swing,
        main_high_swing=main_high_swing,
        cascade_swings=cascade_long,
        resistance_swings=resistance_swings,
        support_swings=open_low_swings
    )

    # Анализ пробоя лонгового каскада.
    has_breakout_long = current_price > target_swing.price
    if not has_breakout_long:
        return setup

    # Пробой лонгового каскада.
    partial_close_price = None
    breakeven_price = None
    partial_close_side_pct = cfg.PARTIAL_CLOSE_SIDE_PCT_MIN
    if resistance_swings:
        nearest_resistance_price = resistance_swings[-1].price
        nearest_resistance_distance_pct = 100 * (nearest_resistance_price - current_price) / current_price
        has_partial_close = partial_close_side_pct <= nearest_resistance_distance_pct < profit_pct - partial_close_side_pct
        if has_partial_close:
            partial_close_price = nearest_resistance_price if has_partial_close else None
            breakeven_price = current_price + cfg.BREAKEVEN_PARTIAL_CLOSE_RATIO * (partial_close_price - current_price)
    elif context in {Context.A, Context.B}:
        main_high_distance_pct = 100 * (main_high_swing.price - current_price) / current_price
        has_partial_close = partial_close_side_pct <= main_high_distance_pct < profit_pct - partial_close_side_pct
        if has_partial_close:
            partial_close_price = main_high_swing.price
            breakeven_price = current_price + cfg.BREAKEVEN_PARTIAL_CLOSE_RATIO * (partial_close_price - current_price)
    entry_slippage_ratio = 1 + cfg.TEST_SLIPPAGE_PCT / 100 if context == Context.TEST else 1
    trade_levels = TradeLevels(
        entry_price=target_swing.price * entry_slippage_ratio,
        take_profit_price=profit_price,
        stop_loss_price=loss_price,
        partial_close_price=partial_close_price,
        breakeven_price=breakeven_price,
    )
    setup = Buy(
        symbol=symbol,
        timeframe=timeframe,
        bars=bars,
        main_low_swing=main_low_swing,
        main_high_swing=main_high_swing,
        cascade_swings=cascade_long,
        resistance_swings=resistance_swings,
        support_swings=open_low_swings,
        trade_levels=trade_levels,
    )

    return setup
