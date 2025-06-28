from typing import List

from config.credentials import TELEGRAM_ORDERS_BOT_TOKEN, TELEGRAM_EVENTS_BOT_TOKEN
from data.loader import Loader
from domain.detection.setup_detector import SetupDetector
from domain.detection.phase_resolver import PhaseResolver
from domain.models.confidence import Confidence
from domain.models.mtf_profile import MTFProfile
from domain.risk_filters import is_low_liquidity, is_abnormal_spike, is_anomalous_trend
from domain.structures import StructureDetector
from notifier.formatter import format_message
from notifier.telegram import TelegramNotifier
from services.position_tracker_service import PositionTrackerService
from services.trade_executor import TradeExecutor
from utils.logger import log, logw


class Scanner:
    def __init__(self):
        self.loader = Loader()
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

        bars_by_tf = self.loader.fetch_multiple_timeframes(symbol, tfs)

        if not self._passes_filters(symbol, bars_by_tf, tfs):
            return

        atr_by_tf = self._calculate_atr(bars_by_tf)
        mtf_states = self._resolve_phases(tfs, bars_by_tf, atr_by_tf)
        swings = self._detect_swings(bars_by_tf[tfs.trend], atr_by_tf[tfs.trend])

        self._check_setups(symbol, tfs, bars_by_tf, atr_by_tf, mtf_states, swings)

    @staticmethod
    def _passes_filters(symbol, bars_by_tf, tfs: MTFProfile):
        if is_low_liquidity(bars_by_tf[tfs.macro]):
            logw(f"Низкая ликвидность по {symbol} ({tfs.macro.value}).")
            return False

        if is_abnormal_spike(bars_by_tf[tfs.setup]):
            logw(f"Аномальный всплеск по {symbol} ({tfs.setup.value}).")
            return False

        atr_tf_setup = sum(abs(b.high - b.low) for b in bars_by_tf[tfs.setup]) / len(bars_by_tf[tfs.setup])
        if is_anomalous_trend(bars_by_tf[tfs.setup], atr_tf_setup):
            logw(f"Аномально сильный тренд по {symbol}.")
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
            logw(f"Сетап по {symbol} не подтверждён.")

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
