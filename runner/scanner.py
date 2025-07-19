from typing import List

from config.constants import IS_TRADING_ENABLED
from config.credentials import TELEGRAM_ORDERS_BOT_TOKEN, TELEGRAM_EVENTS_BOT_TOKEN
from data.loader import Loader
from domain.detection.setup_detector import SetupDetector
from domain.models.bar import Bar
from domain.models.mtf_profile import MTFProfile
from domain.models.setup_signal import SetupSignal
from domain.models.timeframe import Timeframe
from domain.risk_filters import is_calm, has_repeating_ohlc, is_rising
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
        log(f"Отобрано {len(symbols)} символов для анализа.")
        print()

        for symbol in symbols:
            for tfs in tfss:
                try:
                    self._process_symbol(symbol, tfs)
                except Exception as error:
                    logw(f"Ошибка при обработке {symbol}: {error}")
                    raise

        log("Цикл сканирования завершён.")

    def _process_symbol(self, symbol: str, tfs: MTFProfile):
        log(f"{symbol} : {tfs}")

        bars_by_tf = {tfs.macro: self.loader.fetch_ohlcvi(symbol, tfs.macro)}
        if not self._check_if_passes_macro_filters(symbol, bars_by_tf[tfs.macro]):
            return

        bars_by_tf[tfs.context] = self.loader.fetch_ohlcvi(symbol, tfs.context, has_oi=True)
        if not self._check_if_passes_context_filters(symbol, bars_by_tf[tfs.context]):
            return

        bars_by_tf[tfs.setup] = self.loader.fetch_ohlcvi(symbol, tfs.setup, has_oi=True)
        bars_by_tf[tfs.entry] = self.loader.fetch_ohlcvi(symbol, tfs.entry)
        self._check_setups(symbol, tfs, bars_by_tf)

    @staticmethod
    def _check_if_passes_macro_filters(symbol: str, bars: List[Bar]):
        if has_repeating_ohlc(bars):
            logw(f"{symbol} фильтруется из-за грязных свечей.")
            return False

        if not is_calm(bars):
            logw(f"{symbol} фильтруется из-за большого диапазона.")
            return False

        return True

    @staticmethod
    def _check_if_passes_context_filters(symbol: str, bars: List[Bar]):
        if has_repeating_ohlc(bars):
            logw(f"{symbol} фильтруется из-за грязных свечей.")
            return False

        if not is_calm(bars):
            logw(f"{symbol} фильтруется из-за большого диапазона.")
            return False

        if not is_rising(bars):
            logw(f"{symbol} фильтруется из-за отсутствия локального максимума.")
            return False

        return True

    def _check_setups(self, symbol, tfs, bars_by_tf):
        signal = self.setup_detector.detect(symbol=symbol, tfs=tfs, bars_by_tf=bars_by_tf)
        if signal:
            tf = tfs.setup
            self._handle_signal(signal, bars_by_tf[tf], tf)
            print()

    def _handle_signal(self, signal: SetupSignal, setup_bars: List[Bar], tf: Timeframe):
        message = format_message(signal)

        # Генерация графика
        plot = Plot(symbol=signal.symbol, bars=setup_bars, tf=tf)
        plot.plot_main()
        plot.mark_pump_start(signal.timestamp)

        if signal.trendline:
            plot.draw_trendline(signal.trendline)

        filename = f"{signal.confidence.value.capitalize()}_{signal.symbol}.png"
        plot.save(filename)
        image_path = f".generated/plot/charts/{filename}"

        plot = Plot(symbol=signal.symbol, bars=setup_bars, tf=tf)
        plot.generate_and_save(
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
