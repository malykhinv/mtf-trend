from abc import ABC
from typing import Dict, List, Optional, Tuple

from config.constants import MIN_RISK_REWARD, FLOAT_UNDEFINED, MIN_STOP_LOSS_PERCENT, MIN_TAKE_PROFIT_PERCENT
from domain.models.bar import Bar
from domain.models.mtf_profile import MTFProfile
from domain.models.setup_signal import SetupSignal
from domain.models.side import Side
from domain.models.timeframe import Timeframe
from utils.float_utils import is_defined
from utils.logger import logw


class Setup(ABC):
    """
    Базовый абстрактный класс для трейдинг сетапа (алгоритма выявления точки входа).
    Определяет поля-критерии для оценки возможности сделки и структуру интерфейса потомков.
    """
    def __init__(
            self,
            symbol: str,
            bars_by_tf: Dict[Timeframe, List[Bar]],
            tfs: MTFProfile
    ) -> None:
        """Сохраняет исходные данные и профиль таймфреймов."""
        self.symbol: str = symbol
        self.bars_setup: List[Bar] = bars_by_tf[tfs.setup]
        self.confidence: Optional = None
        self.tfs: MTFProfile = tfs
        self.trendline = None
        self.setup_timestamp = None
        self.entry: float = FLOAT_UNDEFINED
        self.sl: float = FLOAT_UNDEFINED
        self.tp: float = FLOAT_UNDEFINED
        self.rr: float = FLOAT_UNDEFINED
        self.price_growth_pct: float = FLOAT_UNDEFINED
        self.volume_growth_x: float = FLOAT_UNDEFINED
        self.atr_growth_pct: float = FLOAT_UNDEFINED

    def validated_or_none(self) -> Optional[SetupSignal]:
        """Возвращает сигнал, если все условия сетапа выполнены."""
        self.define_confidence()
        if self.confidence:
            signal = self.build_signal()
            print()
            return signal
        return None

    # region RR
    def define_rr(self) -> Tuple[float, float, float, float]:
        """Абстрактный метод расчёта Entry, SL, TP и соотношения RR."""
        pass

    def _check_rr(self) -> bool:
        """Проверяет, соответствует ли сделка минимальным требованиям RR."""
        self.entry, self.sl, self.tp, self.rr = self.define_rr()
        if not is_defined(self.entry, self.sl, self.tp, self.rr):
            logw("Некорректные Entry/SL/TP/RR")
            return False
        sl_distance_pct = abs(self.entry - self.sl) / self.entry * 100
        tp_distance_pct = abs(self.tp - self.entry) / self.entry * 100
        if not sl_distance_pct >= MIN_STOP_LOSS_PERCENT:
            logw(f"SL слишком близко: {round(sl_distance_pct, 2)}% < {round(MIN_STOP_LOSS_PERCENT, 2)}%")
            return False
        if not tp_distance_pct >= MIN_TAKE_PROFIT_PERCENT:
            logw(f"TP слишком близко: {round(tp_distance_pct, 2)}% < {round(MIN_TAKE_PROFIT_PERCENT, 2)}%")
            return False
        if self.rr < MIN_RISK_REWARD:
            logw(f"RR {round(self.rr, 1)} < {round(MIN_RISK_REWARD, 1)}.")
            return False
        return True
    # endregion

    # region Conditions by confidence
    def define_confidence(self, *args) -> None:
        """
        Абстрактный метод: логика определения self.confidence на основе условий сетапа.
        """
        pass
    # endregion

    # region Signal
    def build_signal(self) -> SetupSignal:
        """Создаёт объект сигнала на открытие позиции."""
        return SetupSignal(
            symbol=self.symbol,
            side=Side.LONG,
            confidence=self.confidence,
            timestamp=self.setup_timestamp,
            trendline=self.trendline,
            entry=self.entry,
            sl=self.sl,
            tp=self.tp,
            rr=self.rr,
            price_growth_pct=self.price_growth_pct,
            volume_growth_x=self.volume_growth_x,
            atr_growth_pct=self.atr_growth_pct
        )
    # endregion
