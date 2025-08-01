from __future__ import annotations

import logging
from typing import Any

from utils.market_analysis import (
    load_btc_eth_candles,
    has_consecutive_move,
    price_above_ema,
)


class TrendFilter:
    """Фильтрует торговые сигналы с учётом общего тренда по BTC.

    Смотрим последние пятиминутные свечи и решаем, можно ли брать сделки
    вверх или вниз. Длинные сигналы отклоняются, если BTC пять минут подряд
    падает или закрывается ниже своей EMA 20. Короткие сигналы не проходят,
    если BTC пять минут подряд растёт. Решение сохраняется в полях
    ``allow_long`` и ``allow_short``.
    """

    def filter(self, data: Any) -> Any:
        logging.info("Applying trend filters")

        # Загружаем свежие свечи BTC, чтобы понять общий тренд
        candles = load_btc_eth_candles()
        btc = candles.get("BTC/USDT")

        # Определяем простые характеристики тренда
        btc_up = has_consecutive_move(btc, "up")
        btc_down = has_consecutive_move(btc, "down")
        btc_above = price_above_ema(btc)

        allow_long = not (btc_down or not btc_above)
        allow_short = not btc_up
        self.allow_long = allow_long
        self.allow_short = allow_short

        filtered: dict[str, Any] = {}
        for symbol, signals in (data or {}).items():
            # ``signals`` может быть списком объектов Signal или одним объектом.
            # Для удобства приводим всё к списку.
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
                # Сохраняем изначальную структуру: список или один объект
                filtered[symbol] = passed if isinstance(signals, list) else passed[0]

        return filtered
