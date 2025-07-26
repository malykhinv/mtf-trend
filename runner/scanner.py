import traceback
from typing import List, Dict, Set

from config.constants import IS_TRADING_ENABLED, FLOAT_UNDEFINED
from config.credentials import TELEGRAM_ORDERS_BOT_TOKEN, TELEGRAM_EVENTS_BOT_TOKEN
from data.loader import Loader
from domain.detection.setup_detector import SetupDetector
from domain.models.bar import Bar
from domain.models.mtf_profile import MTFProfile
from domain.models.setup_signal import SetupSignal
from domain.models.timeframe import Timeframe
from domain.risk_filters import is_calm, has_repeating_ohlc, has_gaps
from notifier.formatter import format_message
from notifier.telegram import TelegramNotifier
from services.position_tracker_service import PositionTrackerService
from services.trade_executor import TradeExecutor
from domain.models.update_details import UpdateDetails
from utils.logger import log, logw
from utils.plot import Plot


class Scanner:
    """
    Класс-сканер: организует основной цикл торгового сканирования символов, обрабатывает сетапы, отправляет уведомления
    и управляет автоторговлей и историей сигналов.
    """
    def __init__(self) -> None:
        """
        Инициализация основных сервисов и вспомогательных структур.
        """
        self.loader: Loader = Loader()
        self.setup_detector: SetupDetector = SetupDetector()
        self.orders_notifier: TelegramNotifier = TelegramNotifier(TELEGRAM_ORDERS_BOT_TOKEN)
        self.events_notifier: TelegramNotifier = TelegramNotifier(TELEGRAM_EVENTS_BOT_TOKEN)
        self.tracker: PositionTrackerService = PositionTrackerService()
        self.trade_executor: TradeExecutor = TradeExecutor(self.loader.binance, self.tracker)
        self._sent_signals: Dict[str, Set] = {}  # {symbol: set(confidences)}
        self._pending_signals: Dict[str, UpdateDetails] = {}

    def run(self, tfss: List[MTFProfile]) -> None:
        """
        Запускает основной цикл: перебирает символы и профили таймфреймов, запускает обработку по каждому символу.
        Args:
            tfss (List[MTFProfile]): Список профилей таймфреймов для мульти-анализ.
        """
        log("Запущен цикл сканирования.")
        symbols: List[str] = self.loader.get_filtered_symbols()
        if symbols:
            log(f"Отобрано {len(symbols)} символов.")
        for symbol in symbols:
            for tfs in tfss:
                try:
                    self._process_symbol(symbol, tfs)
                except Exception as error:
                    logw(f"Ошибка при обработке {symbol}: {error}\n{traceback.format_exc()}")
        log("Цикл сканирования завершён.")
        print()

    def _process_symbol(self, symbol: str, tfs: MTFProfile) -> None:
        """
        Основной процесс анализа по одному символу и профилю таймфреймов.
        Args:
            symbol (str): тикер
            tfs (MTFProfile): профиль таймфреймов
        """
        bars_by_tf: Dict[Timeframe, List[Bar]] = {tfs.macro: self.loader.fetch_ohlcvi(symbol, tfs.macro, limit=50)}
        if not self._check_if_passes_macro_filters(bars_by_tf[tfs.macro]):
            return
        bars_by_tf[tfs.context] = self.loader.fetch_ohlcvi(symbol, tfs.context, limit=100)
        if not self._check_if_passes_context_filters(bars_by_tf[tfs.context]):
            return
        bars_by_tf[tfs.setup] = self.loader.fetch_ohlcvi(symbol, tfs.setup, has_oi=True)
        if tfs.entry == tfs.setup:
            bars_by_tf[tfs.entry] = bars_by_tf[tfs.setup]
        else:
            bars_by_tf[tfs.entry] = self.loader.fetch_ohlcvi(symbol, tfs.entry, limit=100)
        last_price = bars_by_tf[tfs.entry][-1].close if bars_by_tf[tfs.entry] else None
        if last_price:
            self._update_pending_signal(symbol, last_price)
        self._check_setups(symbol, tfs, bars_by_tf)

    @staticmethod
    def _check_if_passes_macro_filters(bars: List[Bar]) -> bool:
        """
        Проверка на базовые фильтры на дневном/широком таймфрейме.
        Args:
            bars (List[Bar]): Массив баров для анализа.
        Returns:
            bool: True, если все фильтры пройдены.
        """
        if has_repeating_ohlc(bars):
            return False
        if not is_calm(bars):
            return False
        return True

    @staticmethod
    def _check_if_passes_context_filters(bars: List[Bar]) -> bool:
        """
        Проверка на фильтры контекстного таймфрейма.
        Args:
            bars (List[Bar]): Массив баров для анализа.
        Returns:
            bool: True, если все фильтры пройдены.
        """
        if has_gaps(bars):
            return False
        if has_repeating_ohlc(bars):
            return False
        if not is_calm(bars):
            return False
        return True

    def _check_setups(self, symbol: str, tfs: MTFProfile, bars_by_tf: Dict[Timeframe, List[Bar]]) -> None:
        """
        Ищет сетапы среди отобранных баров, при наличии сигнала вызывает обработчик.
        Args:
            symbol (str): тикер
            tfs (MTFProfile): профиль таймфреймов
            bars_by_tf (dict): бары по всему набору таймфреймов
        """
        signal: SetupSignal | None = self.setup_detector.detect(symbol=symbol, tfs=tfs, bars_by_tf=bars_by_tf)
        if signal:
            tf = tfs.setup
            current_price = bars_by_tf[tfs.entry][-1].close if bars_by_tf[tfs.entry] else None
            self._handle_signal(signal, bars_by_tf[tf], tf, current_price)
            print()

    def _handle_signal(
            self,
            signal: SetupSignal,
            setup_bars: List[Bar],
            tf: Timeframe,
            current_price: float | None
    ) -> None:
        """
        Обрабатывает и отправляет торговый сигнал, генерирует график, ведёт учёт отправленных сигналов, вызывает исполнителя.
        Args:
            signal (SetupSignal): найденный сигнал
            setup_bars (List[Bar]): бары на рабочем таймфрейме
            tf (Timeframe): рабочий таймфрейм
        """
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
            msg_id, with_photo = self._send_signal(signal, message, image_path)
            if msg_id:
                highs = [s.price for s in signal.correction_swings if s.type.is_high]
                lows = [s.price for s in signal.correction_swings if s.type.is_low]
                if highs and lows and current_price:
                    high = max(highs)
                    low = min(lows)
                    rr = (
                        (high - current_price) / (current_price - low)
                        if current_price != low
                        else FLOAT_UNDEFINED
                    )
                    self._pending_signals[signal.symbol] = UpdateDetails(
                        message_id=msg_id,
                        notifier=self.orders_notifier if signal.is_order_signal else self.events_notifier,
                        with_photo=bool(image_path),
                        text=message,
                        high=high,
                        low=low,
                        entry_price=current_price,
                        rr=rr,
                        max_price=current_price,
                        min_price=current_price,
                    )
            if signal.symbol not in self._sent_signals:
                self._sent_signals[signal.symbol] = set()
            self._sent_signals[signal.symbol].add(signal.confidence)

    def _send_signal(self, signal: SetupSignal, message: str, image_path: str) -> tuple[int | None, bool]:
        """
        Отправляет сигнал в Telegram-каналы, вызывает исполнение сделки для order-сигналов.
        Args:
            signal (SetupSignal): торговый сигнал
            message (str): текст для Telegram
            image_path (str): путь к картинке графика (скриншота)
        """
        msg_id = None
        if signal.is_order_signal:
            msg_id = self.orders_notifier.send_message(message, image_path)
            if IS_TRADING_ENABLED:
                self.trade_executor.execute(
                    symbol=signal.symbol,
                    side=signal.side,
                    sl=signal.sl,
                    tp=signal.tp
                )
        elif signal.is_event_signal:
            msg_id = self.events_notifier.send_message(message, image_path)
        return msg_id, bool(image_path)

    def _update_pending_signal(self, symbol: str, current_price: float) -> None:
        """Обновляет текст сообщения, когда цена достигает экстремумов."""
        info = self._pending_signals.get(symbol)
        if not info:
            return
        info.max_price = max(info.max_price, current_price)
        info.min_price = min(info.min_price, current_price)
        crossed_high = current_price >= info.high
        crossed_low = current_price <= info.low
        if crossed_high or crossed_low:
            pct = (current_price - info.entry_price) / info.entry_price * 100
            if crossed_high:
                peak_pct = (info.max_price - info.entry_price) / info.entry_price * 100
                extra = f"Максимальный рост: {peak_pct:+.2f}%"
            else:
                drop_pct = (info.min_price - info.entry_price) / info.entry_price * 100
                extra = f"Максимальное падение: {drop_pct:+.2f}%"
            new_text = (
                f"{info.text}\n\nРезультат: {pct:+.2f}% RR {info.rr:.1f}\n{extra}"
            )
            notifier: TelegramNotifier = info.notifier
            notifier.edit_message(info.message_id, new_text, info.with_photo)
            del self._pending_signals[symbol]
