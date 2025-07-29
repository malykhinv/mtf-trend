import time
import traceback
from typing import List, Dict, Set
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import threading

from config.constants import IS_TRADING_ENABLED
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
from domain.models.active_setup import ActiveSetup
from config.constants import ACTIVE_SETUP_TIMEOUT_MINUTES
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
        self._active_setups: Dict[str, ActiveSetup] = {}
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=2)
        self._lock = threading.Lock()
        self._monitor_thread = threading.Thread(target=self._monitor_active_setups, daemon=True)
        self._monitor_thread.start()

    def run(self, tfss: List[MTFProfile]) -> None:
        """Перебирает символы и профили таймфреймов и запускает обработку."""
        start_time = time.perf_counter()
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

        duration = int(time.perf_counter() - start_time)
        hours, remainder = divmod(duration, 3600)
        minutes, seconds = divmod(remainder, 60)

        log(f"Цикл сканирования занял {hours:02d}:{minutes:02d}:{seconds:02d}.")
        print()


    def _process_symbol(self, symbol: str, tfs: MTFProfile) -> None:
        """Обрабатывает один символ по заданному профилю таймфреймов."""
        bars_by_tf: Dict[Timeframe, List[Bar]] = {
            tfs.macro: self.loader.fetch_ohlcvi(symbol, tfs.macro, limit=30, use_cache=True, ttl_minutes=tfs.macro.minutes)
        }
        if not self._check_if_passes_macro_filters(bars_by_tf[tfs.macro]):
            return
        bars_by_tf[tfs.context] = self.loader.fetch_ohlcvi(
            symbol,
            tfs.context,
            limit=50,
            use_cache=True,
            ttl_minutes=tfs.context.minutes,
        )
        if not self._check_if_passes_context_filters(bars_by_tf[tfs.context]):
            self.loader.fetch_ohlcvi(symbol, tfs.setup, has_oi=True)
            return
        bars_by_tf[tfs.setup] = self.loader.fetch_ohlcvi(
            symbol,
            tfs.setup,
            has_oi=True,
        )
        last_price = bars_by_tf[tfs.setup][-1].close if bars_by_tf[tfs.setup] else None
        if last_price:
            self._update_pending_signal(symbol, last_price)
        self._check_setups(symbol, tfs, bars_by_tf)

    @staticmethod
    def _check_if_passes_macro_filters(bars: List[Bar]) -> bool:
        """Проверяет фильтры на дневном таймфрейме."""
        if has_repeating_ohlc(bars):
            return False
        if not is_calm(bars):
            return False
        return True

    @staticmethod
    def _check_if_passes_context_filters(bars: List[Bar]) -> bool:
        """Проверяет фильтры контекстного таймфрейма."""
        if has_gaps(bars):
            return False
        if has_repeating_ohlc(bars):
            return False
        if not is_calm(bars):
            return False
        return True

    def _check_setups(self, symbol: str, tfs: MTFProfile, bars_by_tf: Dict[Timeframe, List[Bar]]) -> None:
        """Ищет сетапы и при наличии сигнала вызывает обработчик."""
        signal: SetupSignal | None = self.setup_detector.detect(symbol=symbol, tfs=tfs, bars_by_tf=bars_by_tf)
        if signal:
            current_price = bars_by_tf[tfs.setup][-1].close if bars_by_tf[tfs.setup] else None
            self._executor.submit(self._handle_signal, signal, bars_by_tf[tfs.setup], tfs, current_price)
            print()

    def _handle_signal(
            self,
            signal: SetupSignal,
            setup_bars: List[Bar],
            tfs: MTFProfile,
            current_price: float | None
    ) -> None:
        """Отправляет сигнал и при необходимости выполняет сделку."""
        message = format_message(signal)
        # Генерация графика
        filename = f"{signal.confidence.value.capitalize()}_{signal.symbol}.png"
        plot = Plot(
            symbol=signal.symbol,
            bars=setup_bars,
            correction_swings=signal.correction_swings,
            tf=tfs.setup,
            save_dir='confirmed'
        )
        image_path = plot.generate_and_save(
            filename=filename,
            pump_start_time=signal.timestamp,
            trendline=signal.trendline,
        )
        # Проверка, отправлялся ли уже сигнал с таким confidence для этого символа
        with self._lock:
            already_sent = (
                signal.symbol in self._sent_signals and
                signal.confidence in self._sent_signals[signal.symbol]
            )
        if already_sent:
            logw(f"Сигнал уже отправлялся: {signal.symbol} [{signal.confidence.name}] — пропуск.")
        else:
            msg_id, with_photo = self._send_signal(signal, message, image_path)
            if msg_id:
                log(f"Сообщение отправлено, message_id={msg_id}")
                highs = [s.price for s in signal.correction_swings if s.type.is_high]
                lows = [s.price for s in signal.correction_swings if s.type.is_low]
                if highs and lows and current_price:
                    high = max(highs)
                    low = min(lows)
                    with self._lock:
                        self._pending_signals[signal.symbol] = UpdateDetails(
                            message_id=msg_id,
                            notifier=self.orders_notifier if signal.is_order_signal else self.events_notifier,
                            with_photo=bool(image_path),
                            text=message,
                            high=high,
                            low=low,
                            entry_price=current_price,
                            max_price=current_price,
                            min_price=current_price,
                        )
        with self._lock:
            if signal.symbol not in self._sent_signals:
                self._sent_signals[signal.symbol] = set()
            self._sent_signals[signal.symbol].add(signal.confidence)
            self._active_setups[signal.symbol] = ActiveSetup(
                symbol=signal.symbol,
                tfs=tfs,
                pump_start_time=signal.timestamp,
                last_checked=datetime.now()
            )

    def _send_signal(self, signal: SetupSignal, message: str, image_path: str) -> tuple[int | None, bool]:
        """Отправляет сигнал в Telegram и, при необходимости, исполняет его."""
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
        with self._lock:
            info = self._pending_signals.get(symbol)
            if not info:
                return
            prev_max = info.max_price
            info.max_price = max(info.max_price, current_price)
            info.min_price = min(info.min_price, current_price)
        log(
            f"Анализ {symbol}: цена={current_price} high={info.high} "
            f"low={info.low} entry={info.entry_price} "
            f"max={info.max_price} min={info.min_price}"
        )
        crossed_high = current_price >= info.high
        crossed_low = current_price <= info.low
        if crossed_high or crossed_low:
            log(
                f"Цена {'выше' if crossed_high else 'ниже'} целевого уровня "
                f"для {symbol}"
            )
            if crossed_high and info.max_price > prev_max:
                new_text = f"{info.text}\n\nМаксимум обновлен"
                notifier: TelegramNotifier = info.notifier
                notifier.edit_message(info.message_id, new_text, info.with_photo)
            with self._lock:
                del self._pending_signals[symbol]

    def _monitor_active_setups(self) -> None:
        """Фоновый цикл повторной проверки активных сетапов."""
        while True:
            time.sleep(60)
            with self._lock:
                items = list(self._active_setups.items())
            for symbol, active in items:
                self._recheck_setup(symbol, active)

    def _recheck_setup(self, symbol: str, active_setup: ActiveSetup) -> None:
        if datetime.now() - active_setup.pump_start_time > timedelta(minutes=ACTIVE_SETUP_TIMEOUT_MINUTES):
            with self._lock:
                self._active_setups.pop(symbol, None)
            return

        tfs = active_setup.tfs
        bars_by_tf: Dict[Timeframe, List[Bar]] = {
            tfs.macro: self.loader.fetch_ohlcvi(symbol, tfs.macro, limit=30, use_cache=True, ttl_minutes=tfs.macro.minutes)
        }
        if not self._check_if_passes_macro_filters(bars_by_tf[tfs.macro]):
            with self._lock:
                self._active_setups.pop(symbol, None)
            return
        bars_by_tf[tfs.context] = self.loader.fetch_ohlcvi(
            symbol,
            tfs.context,
            limit=50,
            use_cache=True,
            ttl_minutes=tfs.context.minutes,
        )
        if not self._check_if_passes_context_filters(bars_by_tf[tfs.context]):
            with self._lock:
                self._active_setups.pop(symbol, None)
            return

        setup_bars = self.loader.fetch_ohlcvi(symbol, tfs.setup, has_oi=True)
        bars_by_tf[tfs.setup] = [b for b in setup_bars if b.timestamp >= active_setup.pump_start_time]
        if not bars_by_tf[tfs.setup]:
            with self._lock:
                self._active_setups.pop(symbol, None)
            return

        signal = self.setup_detector.detect(
            symbol=symbol,
            tfs=tfs,
            bars_by_tf=bars_by_tf,
            pump_start_time=active_setup.pump_start_time,
        )
        if not signal:
            with self._lock:
                self._active_setups.pop(symbol, None)
        else:
            active_setup.last_checked = datetime.now()

