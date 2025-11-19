from dataclasses import dataclass, replace

from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.swing import Swing, SwingType
from crypto_screener.domain.models.timeframe import Timeframe


# region Private.
@dataclass
class _SwingDetectionConfig:
    window: int
    atr_multiplier: float
    atr_window: int
    open_lag: int


_SWING_PARAMS = {
    Timeframe.M1: _SwingDetectionConfig(window=4, atr_multiplier=2.4, atr_window=50, open_lag=5),
    Timeframe.M5: _SwingDetectionConfig(window=3, atr_multiplier=2.0, atr_window=40, open_lag=4),
    Timeframe.M15: _SwingDetectionConfig(window=3, atr_multiplier=1.6, atr_window=30, open_lag=3),
    Timeframe.M30: _SwingDetectionConfig(window=2, atr_multiplier=1.4, atr_window=30, open_lag=2),
    Timeframe.H1: _SwingDetectionConfig(window=2, atr_multiplier=1.2, atr_window=20, open_lag=1),
    Timeframe.H4: _SwingDetectionConfig(window=2, atr_multiplier=1.0, atr_window=14, open_lag=1),
}


def _add_swings(
        bars: list[Bar],
        window: int,
        atr_mult: float,
        atr_window: int
) -> list[Bar]:
    length = len(bars)
    if length == 0:
        return []

    if length < 2 * window + 1:
        return [replace(b, swing=None) for b in bars]

    min_move_by_idx: list[float | None] = _compute_min_move_series(
        bars=bars,
        atr_mult=atr_mult,
        atr_window=atr_window,
    )

    candidates: list[tuple[int, SwingType, float]] = []

    for idx in range(window, length - window):
        bar = bars[idx]
        segment = bars[idx - window: idx + window + 1]

        low = bar.low
        high = bar.high

        is_local_low = (
                all(low <= b.low for b in segment)
                and low < bars[idx - 1].low
                and low < bars[idx + 1].low
        )

        is_local_high = (
                all(high >= b.high for b in segment)
                and high > bars[idx - 1].high
                and high > bars[idx + 1].high
        )

        if is_local_low:
            candidates.append((idx, SwingType.LOW, low))
        if is_local_high:
            candidates.append((idx, SwingType.HIGH, high))

    if not candidates:
        return [replace(b, swing=None) for b in bars]

    candidates.sort(key=lambda x: x[0])

    filtered: list[tuple[int, SwingType, float]] = []

    for idx, swing_type, price in candidates:
        if not filtered:
            filtered.append((idx, swing_type, price))
            continue

        last_idx, last_type, last_price = filtered[-1]

        # тот же тип → оставляем только более экстремальный свинг
        if swing_type == last_type:
            if _is_more_extreme(swing_type, price, last_price):
                filtered[-1] = (idx, swing_type, price)
            continue

        # другой тип → проверяем, достаточно ли движение в ценах
        move_abs = abs(price - last_price)
        threshold = _get_move_threshold(min_move_by_idx, last_idx, idx)

        if threshold is not None and move_abs < threshold:
            # движение слишком маленькое — игнорируем
            continue

        filtered.append((idx, swing_type, price))

    swing_by_idx: dict[int, Swing] = {}

    for idx, swing_type, price in filtered:
        swing_by_idx[idx] = Swing(
            time=bars[idx].time,
            price=price,
            type=swing_type
        )

    enriched: list[Bar] = []
    for i, bar in enumerate(bars):
        swing = swing_by_idx.get(i)
        enriched.append(replace(bar, swing=swing))

    return enriched


def _is_more_extreme(
        swing_type: SwingType,
        new_price: float,
        old_price: float
) -> bool:
    if swing_type == SwingType.LOW:
        return new_price < old_price
    else:
        return new_price > old_price


def _get_move_threshold(
        min_move_series: list[float | None],
        idx1: int,
        idx2: int,
) -> float | None:
    candidates_thresholds: list[float] = []
    for idx in (idx1, idx2):
        v = min_move_series[idx]
        if v is not None and v > 0:
            candidates_thresholds.append(v)
    if not candidates_thresholds:
        return None
    return max(candidates_thresholds)


def _compute_min_move_series(
        bars: list[Bar],
        atr_mult: float,
        atr_window: int,
) -> list[float | None]:
    n = len(bars)
    if n < 2 or atr_window <= 0:
        return [None] * n

    trs: list[float] = [0.0] * n
    prev_close = bars[0].close
    for i in range(1, n):
        bar = bars[i]
        tr = max(
            bar.high - bar.low,
            abs(bar.high - prev_close),
            abs(bar.low - prev_close),
        )
        trs[i] = tr
        prev_close = bar.close

    min_move: list[float | None] = [None] * n
    window_sum = 0.0
    count = 0

    for i in range(1, n):
        window_sum += trs[i]
        count += 1

        # как только превысили atr_window — начинаем выкидывать старые TR
        if count > atr_window:
            window_sum -= trs[i - atr_window]
            count -= 1

        # теперь count всегда > 0, начиная с i == 1
        avg_tr = window_sum / count
        min_move[i] = avg_tr * atr_mult

    return min_move


def _refine_swings(bars: list[Bar]) -> list[Bar]:
    length = len(bars)
    if length == 0:
        return []

    swing_indices: list[int] = []
    swing_types: list[SwingType] = []
    swing_prices: list[float] = []

    for i, bar in enumerate(bars):
        swing = bar.swing
        if swing is not None:
            swing_indices.append(i)
            swing_types.append(swing.type)
            swing_prices.append(swing.price)

    swings_count = len(swing_indices)
    if swings_count < 3:
        return bars

    for i in range(swings_count - 2):
        t0 = swing_types[i]
        t1 = swing_types[i + 1]
        t2 = swing_types[i + 2]

        if t0 != t2 or t0 == t1:
            continue

        mid_type = t1
        left_idx = swing_indices[i]
        mid_idx = swing_indices[i + 1]
        right_idx = swing_indices[i + 2]

        if right_idx - left_idx <= 1:
            continue

        best_idx = mid_idx
        match mid_type:
            case SwingType.LOW:
                best_value = bars[best_idx].low
                for j in range(left_idx + 1, right_idx):
                    value = bars[j].low
                    if value < best_value:
                        best_value = value
                        best_idx = j
                new_price = bars[best_idx].low
            case SwingType.HIGH:
                best_value = bars[best_idx].high
                for j in range(left_idx + 1, right_idx):
                    value = bars[j].high
                    if value > best_value:
                        best_value = value
                        best_idx = j
                new_price = bars[best_idx].high

        swing_indices[i + 1] = best_idx
        swing_prices[i + 1] = new_price

    swing_by_idx: dict[int, Swing] = {}
    for idx, swing_type, price in zip(swing_indices, swing_types, swing_prices):
        swing_by_idx[idx] = Swing(
            time=bars[idx].time,
            price=price,
            type=swing_type
        )

    refined: list[Bar] = []
    for i, bar in enumerate(bars):
        refined_swing = swing_by_idx.get(i)
        refined.append(replace(bar, swing=refined_swing))

    return refined


def _mark_swings_open(
        bars: list[Bar],
        open_lag: int,
) -> list[Bar]:
    length = len(bars)
    if length == 0:
        return []

    result: list[Bar] = []

    for idx, bar in enumerate(bars):
        swing = bar.swing
        if swing is None:
            result.append(bar)
            continue

        if 0 < open_lag < length:
            future_end = length - open_lag
            if idx + 1 < future_end:
                future_bars = bars[idx + 1:future_end]
            else:
                future_bars = []
        else:
            future_bars = bars[idx + 1:]

        is_open = _is_swing_open(swing.price, future_bars)

        result.append(
            replace(
                bar,
                swing=Swing(
                    time=swing.time,
                    price=swing.price,
                    type=swing.type,
                    is_open=is_open
                )
            )
        )

    return result


def _is_swing_open(
        price: float,
        future_bars: list[Bar]
) -> bool:
    for bar in future_bars:
        body_low = min(bar.open, bar.close)
        body_high = max(bar.open, bar.close)
        if body_low <= price <= body_high:
            return False
    return True


# endregion

def add_swings(
        bars: list[Bar],
        timeframe: Timeframe,
) -> list[Bar]:
    config = _SWING_PARAMS.get(timeframe)
    if config is None:
        raise ValueError(f"Таймфрейм не поддерживается: {timeframe}.")
    enriched = _add_swings(
        bars=bars,
        window=config.window,
        atr_mult=config.atr_multiplier,
        atr_window=config.atr_window,
    )
    refined = _refine_swings(enriched)
    marked_open = _mark_swings_open(refined, config.open_lag)
    return marked_open
