from typing import List

from config.credentials import TELEGRAM_ORDERS_BOT_TOKEN, TELEGRAM_EVENTS_BOT_TOKEN
from data.loader import Loader
from domain.detection.setup_detector import SetupDetector
from domain.detection.phase_resolver import PhaseResolver
from domain.models.confidence import Confidence
from domain.models.timeframe import Timeframe
from domain.risk_filters import is_low_liquidity, is_abnormal_spike, is_anomalous_trend
from domain.structures import StructureDetector
from notifier.formatter import format_message
from notifier.telegram import TelegramNotifier
from services.position_tracker_service import PositionTrackerService
from services.trade_executor import TradeExecutor
from utils.logger import log


class Scanner:
    def __init__(self):
        self.loader = Loader()
        self.orders_notifier = TelegramNotifier(TELEGRAM_ORDERS_BOT_TOKEN)
        self.events_notifier = TelegramNotifier(TELEGRAM_EVENTS_BOT_TOKEN)
        self.tracker = PositionTrackerService()
        self.trade_executor = TradeExecutor(self.loader.binance, self.tracker)

    def run(self, tf_list: List[List[Timeframe]]):
        log("Запущен цикл сканирования.")
        symbols = self.loader.get_filtered_symbols()
        log(f"Отобрано {len(symbols)} символов для анализа.")

        for symbol, tf_set in symbols, tf_list:
            try:
                self._process_symbol(symbol, tf_set)
            except Exception as error:
                log(f"✖ Ошибка при обработке {symbol}: {error}")
                raise

        log("Цикл сканирования завершён.")

    def _process_symbol(self, symbol: str, tfs: List[Timeframe]):
        print()
        log(symbol)

        bars_by_tf = self.loader.fetch_multiple_timeframes(symbol, tfs)

        if not self._passes_filters(symbol, bars_by_tf, tfs):
            return

        atr_by_tf = self._calculate_atr(bars_by_tf)
        mtf_states = self._resolve_phases(tfs, bars_by_tf, atr_by_tf)
        swings = self._detect_swings(bars_by_tf[tfs[1]], atr_by_tf[tfs[1]])

        self._check_setups(symbol, tfs, bars_by_tf, atr_by_tf, mtf_states, swings)

    @staticmethod
    def _passes_filters(symbol, bars_by_tf, tf_list):
        if is_low_liquidity(bars_by_tf[tf_list[0]]):
            log(f"✖ Низкая ликвидность по {symbol} ({tf_list[0]}).")
            return False

        if is_abnormal_spike(bars_by_tf[tf_list[2]]):
            log(f"✖ Аномальный всплеск по {symbol} ({tf_list[2]}).")
            return False

        atr_tf2 = sum(abs(b.high - b.low) for b in bars_by_tf[tf_list[2]]) / len(bars_by_tf[tf_list[2]])
        if is_anomalous_trend(bars_by_tf[tf_list[2]], atr_tf2):
            log(f"✖ Аномально сильный тренд по {symbol}.")
            return False

        return True

    @staticmethod
    def _calculate_atr(bars_by_tf):
        return {tf: sum(abs(b.high - b.low) for b in bars) / len(bars) for tf, bars in bars_by_tf.items()}

    @staticmethod
    def _resolve_phases(tfs, bars_by_tf, atr_by_tf):
        resolver = PhaseResolver(tfs, bars_by_tf, atr_by_tf)
        return resolver.resolve()

    @staticmethod
    def _detect_swings(bars_tf1, atr_tf1):
        detector = StructureDetector(bars_tf1, atr_tf1)
        return detector.detect_swing_points()

    def _check_setups(self, symbol, tfs, bars_by_tf, atr_by_tf, mtf_states, swings):
        for confidence in [Confidence.STRONG, Confidence.MODERATE, Confidence.WEAK]:
            setup_detector = SetupDetector(
                symbol=symbol,
                tfs=tfs,
                bars_by_tf=bars_by_tf,
                atr_by_tf=atr_by_tf,
                swings=swings,
                mtf_states=mtf_states,
                confidence=confidence
            )
            signal = setup_detector.detect()
            if signal:
                self._handle_signal(signal)
                break
        else:
            log(f"✖ Сетап по {symbol} не подтверждён.")

    def _handle_signal(self, signal):
        message = format_message(signal)

        if signal.is_order_signal:
            self.trade_executor.execute(
                symbol=signal.symbol,
                scenario=signal.scenario,
                side=signal.side,
                sl=signal.sl,
                tp=signal.tp
            )
            self.orders_notifier.send_message(message)

        elif signal.is_event_signal:
            self.events_notifier.send_message(message)
