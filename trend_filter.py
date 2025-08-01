from __future__ import annotations

import logging
from typing import Any

from utils.market_analysis import (
    load_btc_eth_candles,
    has_consecutive_move,
    price_above_ema,
)


class TrendFilter:
    """Filter trading signals based on the broader BTC market trend.

    The filter inspects recent five minute candles for BTC to decide whether
    long or short signals should be allowed. Long signals are rejected when
    BTC shows a consecutive down move for at least five minutes or when the
    latest close is below its 20 period EMA. Short signals are rejected when
    BTC has moved up for at least five consecutive minutes. Only signals that
    pass these checks are returned. The decisions are stored on the instance as
    ``allow_long`` and ``allow_short`` for reuse elsewhere.
    """

    def filter(self, data: Any) -> Any:
        logging.info("Applying trend filters")

        # Load recent BTC candles to determine the broader trend
        candles = load_btc_eth_candles()
        btc = candles.get("BTC/USDT")

        # Determine simple trend characteristics
        btc_up = has_consecutive_move(btc, "up")
        btc_down = has_consecutive_move(btc, "down")
        btc_above = price_above_ema(btc)

        allow_long = not (btc_down or not btc_above)
        allow_short = not btc_up
        self.allow_long = allow_long
        self.allow_short = allow_short

        filtered: dict[str, Any] = {}
        for symbol, signals in (data or {}).items():
            # ``signals`` may be a list of Signal objects or a single Signal.
            # We normalise to a list to simplify processing.
            sig_list = signals if isinstance(signals, list) else [signals]
            passed = []
            for sig in sig_list:
                direction = getattr(sig, "direction", None)
                if direction == "long" and not allow_long:
                    continue
                if direction == "short" and not allow_short:
                    continue
                passed.append(sig)
            if passed:
                # Preserve original structure (list vs single object)
                filtered[symbol] = passed if isinstance(signals, list) else passed[0]

        return filtered
