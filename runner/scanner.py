from typing import List

from config.constants import IS_TRADING_ENABLED
from config.credentials import TELEGRAM_ORDERS_BOT_TOKEN, TELEGRAM_EVENTS_BOT_TOKEN
from data.loader import Loader
from domain.detection.setup_detector import SetupDetector
from domain.models.bar import Bar
from domain.models.mtf_profile import MTFProfile
from domain.models.setup_signal import SetupSignal
from domain.models.timeframe import Timeframe
from domain.risk_filters import is_calm, has_repeating_ohlc, is_rising, has_gaps
from notifier.formatter import format_message
from notifier.telegram import TelegramNotifier
from services.position_tracker_service import PositionTrackerService
from services.trade_executor import TradeExecutor
from utils.logger import log, logw
from utils.plot import Plot


class Scanner:
    def __init__(self):
        self.loader = Loader()
        self.setup_detector = SetupDetector()
        self.orders_notifier = TelegramNotifier(TELEGRAM_ORDERS_BOT_TOKEN)
        self.events_notifier = TelegramNotifier(TELEGRAM_EVENTS_BOT_TOKEN)
        self.tracker = PositionTrackerService()
        self.trade_executor = TradeExecutor(self.loader.binance, self.tracker)
        self._sent_signals = {}  # {symbol: set(confidences)}

    def run(self, tfss: List[MTFProfile]):
        log("Запущен цикл сканирования.")
        symbols = self.loader.get_filtered_symbols()

        if symbols:
            log(f"Отобрано {len(symbols)} символов.")

        for symbol in symbols:
            for tfs in tfss:
                try:
                    self._process_symbol(symbol, tfs)
                except Exception as error:
                    logw(f"Ошибка при обработке {symbol}: {error}")

        log("Цикл сканирования завершён.")
        print()

    def _process_symbol(self, symbol: str, tfs: MTFProfile):
        bars_by_tf = {tfs.macro: self.loader.fetch_ohlcvi(symbol, tfs.macro)}
        if not self._check_if_passes_macro_filters(bars_by_tf[tfs.macro]):
            return

        bars_by_tf[tfs.context] = self.loader.fetch_ohlcvi(symbol, tfs.context)
        if not self._check_if_passes_context_filters(bars_by_tf[tfs.context]):
            return

        bars_by_tf[tfs.setup] = self.loader.fetch_ohlcvi(symbol, tfs.setup, limit=250, has_oi=True)
        bars_by_tf[tfs.entry] = self.loader.fetch_ohlcvi(symbol, tfs.entry)
        self._check_setups(symbol, tfs, bars_by_tf)

    @staticmethod
    def _check_if_passes_macro_filters(bars: List[Bar]):
        if has_repeating_ohlc(bars):
            return False

        if not is_calm(bars):
            return False

        return True

    @staticmethod
    def _check_if_passes_context_filters(bars: List[Bar]):
        if not has_gaps(bars):
            return False

        if has_repeating_ohlc(bars):
            return False

        if not is_calm(bars):
            return False

        if not is_rising(bars):
            return False

        return True

    def _check_setups(self, symbol, tfs, bars_by_tf):
        signal = self.setup_detector.detect(symbol=symbol, tfs=tfs, bars_by_tf=bars_by_tf)
        if signal:
            tf = tfs.setup
            self._handle_signal(signal, bars_by_tf[tf], tf)
            print()

    def _handle_signal(
            self,
            signal: SetupSignal,
            setup_bars: List[Bar],
            tf: Timeframe
    ):
        message = format_message(signal)

        # Генерация графика
        filename = f"{signal.confidence.value.capitalize()}_{signal.symbol}.png"

        plot = Plot(
            symbol=signal.symbol,
            bars=setup_bars,
            correction_swings=signal.correction_swings,
            tf=tf,
            save_dir='confirmed'
        )
        image_path = plot.generate_and_save(
            filename=filename,
            pump_start_time=signal.timestamp,
            trendline=signal.trendline,
        )
        # Проверка, отправлялся ли уже сигнал с таким confidence для этого символа
        already_sent = (
                signal.symbol in self._sent_signals and
                signal.confidence in self._sent_signals[signal.symbol]
        )
        if already_sent:
            logw(f"Сигнал уже отправлялся: {signal.symbol} [{signal.confidence.name}] — пропуск.")
        else:
            self._send_signal(signal, message, image_path)
            if signal.symbol not in self._sent_signals:
                self._sent_signals[signal.symbol] = set()
            self._sent_signals[signal.symbol].add(signal.confidence)

    def _send_signal(self, signal: SetupSignal, message: str, image_path: str):
        if signal.is_order_signal:
            self.orders_notifier.send_message(message, image_path)

            if IS_TRADING_ENABLED:
                self.trade_executor.execute(
                    symbol=signal.symbol,
                    side=signal.side,
                    sl=signal.sl,
                    tp=signal.tp
                )

        elif signal.is_event_signal:
            self.events_notifier.send_message(message, image_path)
