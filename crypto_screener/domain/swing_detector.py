from dataclasses import dataclass, replace

from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.swing import Swing, SwingType
from crypto_screener.domain.models.timeframe import Timeframe


@dataclass
class SwingDetectionConfig:
    window: int
    atr_multiplier: float
    atr_window: int


_SWING_PARAMS = {
    Timeframe.M1: SwingDetectionConfig(window=4, atr_multiplier=2.4, atr_window=50),
    Timeframe.M5: SwingDetectionConfig(window=3, atr_multiplier=2.0, atr_window=40),
    Timeframe.M15: SwingDetectionConfig(window=3, atr_multiplier=1.6, atr_window=30),
    Timeframe.M30: SwingDetectionConfig(window=2, atr_multiplier=1.4, atr_window=30),
    Timeframe.H1: SwingDetectionConfig(window=2, atr_multiplier=1.2, atr_window=20),
    Timeframe.H4: SwingDetectionConfig(window=2, atr_multiplier=1.0, atr_window=14),
}


def add_swings(
        bars: list[Bar],
        timeframe: Timeframe,
) -> list[Bar]:
    config = _SWING_PARAMS.get(timeframe)
    if config is None:
        raise ValueError(f"Таймфрейм не поддерживается: {timeframe}.")

    return _add_swings(
        bars=bars,
        window=config.window,
        atr_mult=config.atr_multiplier,
        atr_window=config.atr_window
    )


def _add_swings(
        bars: list[Bar],
        window: int,
        atr_mult: float,
        atr_window: int,
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

    def _is_more_extreme(type: SwingType, new_price: float, old_price: float) -> bool:
        if type == SwingType.LOW:
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
        is_open = _is_swing_open(price, bars[idx + 1:])
        swing_by_idx[idx] = Swing(
            ts=bars[idx].ts,
            price=price,
            type=swing_type,
            is_open=is_open,
        )

    enriched: list[Bar] = []
    for i, bar in enumerate(bars):
        swing = swing_by_idx.get(i)
        enriched.append(replace(bar, swing=swing))

    return enriched


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



def _is_swing_open(price: float, future_bars: list[Bar]) -> bool:
    for bar in future_bars:
        body_low = min(bar.open, bar.close)
        body_high = max(bar.open, bar.close)
        if body_low <= price <= body_high:
            return False
    return True
