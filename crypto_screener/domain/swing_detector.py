from dataclasses import replace

from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.swing import Swing, SwingType


def add_swings(bars: list[Bar]) -> list[Bar]:
    if not bars:
        return []

    enriched: list[Bar] = []
    total = len(bars)

    for idx, bar in enumerate(bars):
        swing = bar.swing

        if 0 < idx < total - 1:
            prev_bar = bars[idx - 1]
            next_bar = bars[idx + 1]

            if bar.low < prev_bar.low and bar.low < next_bar.low:
                swing_price = bar.low
                swing = Swing(
                    ts=bar.ts,
                    price=swing_price,
                    type=SwingType.LOW,
                    is_open=_is_swing_open(swing_price, bars[idx + 1 :]),
                )
            elif bar.high > prev_bar.high and bar.high > next_bar.high:
                swing_price = bar.high
                swing = Swing(
                    ts=bar.ts,
                    price=swing_price,
                    type=SwingType.HIGH,
                    is_open=_is_swing_open(swing_price, bars[idx + 1 :]),
                )

        enriched.append(replace(bar, swing=swing))

    return enriched


def _is_swing_open(price: float, future_bars: list[Bar]) -> bool:
    for bar in future_bars:
        body_low = min(bar.open, bar.close)
        body_high = max(bar.open, bar.close)
        if body_low <= price <= body_high:
            return False
    return True
