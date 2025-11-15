from datetime import datetime

from crypto_screener.domain.exchange import Exchange
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.setup_detector import detect_setup
from crypto_screener.utils.logger import log


def run_test_symbol(
        exchange: Exchange,
        symbol: str,
        timeframe: Timeframe,
        limit: int,
        end: datetime,
) -> None:
    log.d(f"Запуск тестирования {symbol} на {timeframe.tf}.")
    bars = exchange.get_ohlcv(symbol, timeframe, limit, end)
    setup = detect_setup(bars)
    message = f"Обнаружен {setup.type.name.capitalize()}-сетап." if setup else "Сетап не обнаружен."
    log.d(message)
