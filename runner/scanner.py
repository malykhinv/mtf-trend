from typing import List

from config.constants import IS_TRADING_ENABLED
from config.credentials import TELEGRAM_ORDERS_BOT_TOKEN, TELEGRAM_EVENTS_BOT_TOKEN
from data.loader import Loader
from domain.detection.setup_detector import SetupDetector
from domain.models.bar import Bar
from domain.models.confidence import Confidence
from domain.models.mtf_profile import MTFProfile
from domain.risk_filters import has_messy_candles, is_stablecoin, is_calm
from notifier.formatter import format_message
from notifier.telegram import TelegramNotifier
from services.position_tracker_service import PositionTrackerService
from services.trade_executor import TradeExecutor
from utils.logger import log, logw


class Scanner:
    def __init__(self):
        self.loader = Loader()
        self.setup_detector = SetupDetector()
        self.orders_notifier = TelegramNotifier(TELEGRAM_ORDERS_BOT_TOKEN)
        self.events_notifier = TelegramNotifier(TELEGRAM_EVENTS_BOT_TOKEN)
        self.tracker = PositionTrackerService()
        self.trade_executor = TradeExecutor(self.loader.binance, self.tracker)

    def run(self, tfss: List[MTFProfile]):
        log("Запущен цикл сканирования.")
        symbols = self.loader.get_filtered_symbols()
        log(f"Отобрано {len(symbols)} символов для анализа.")

        for symbol in symbols:
            for tfs in tfss:
                try:
                    self._process_symbol(symbol, tfs)
                except Exception as error:
                    logw(f"Ошибка при обработке {symbol}: {error}")
                    raise

        log("Цикл сканирования завершён.")

    def _process_symbol(self, symbol: str, tfs: MTFProfile):
        print()
        log(f"{symbol} : {tfs}")

        bars_by_tf = {tfs.macro: self.loader.fetch_ohlcv(symbol, tfs.macro)}

        if not self._check_if_passes_macro_filters(symbol, bars_by_tf[tfs.macro]):
            return

        bars_by_tf[tfs.setup] = self.loader.fetch_ohlcv(symbol, tfs.setup)
        bars_by_tf[tfs.entry] = self.loader.fetch_ohlcv(symbol, tfs.entry)

        self._check_setups(symbol, tfs, bars_by_tf)

    @staticmethod
    def _check_if_passes_macro_filters(symbol: str, bars: List[Bar]):
        if is_stablecoin(symbol):
            logw(f"{symbol} фильтруется как стейблкоин.")
            return False

        if has_messy_candles(bars):
            logw(f"{symbol} фильтруется из-за грязных свечей.")
            return False

        if not is_calm(bars):
            logw(f"{symbol} фильтруется из-за большого диапазона.")
            return False

        return True

    def _check_setups(self, symbol, tfs, bars_by_tf):
        for confidence in [Confidence.STRONG, Confidence.MODERATE, Confidence.WEAK]:
            signal = self.setup_detector.detect(
                symbol=symbol,
                tfs=tfs,
                bars_by_tf=bars_by_tf,
                confidence=confidence
            )
            if signal:
                self._handle_signal(signal)
                break
        else:
            logw(f"Сетап по {symbol} не подтверждён.")

    def _handle_signal(self, signal):
        message = format_message(signal)

        if signal.is_order_signal:
            self.orders_notifier.send_message(message)
            if IS_TRADING_ENABLED:
                self.trade_executor.execute(
                    symbol=signal.symbol,
                    side=signal.side,
                    sl=signal.sl,
                    tp=signal.tp
                )

        elif signal.is_event_signal:
            self.events_notifier.send_message(message)
