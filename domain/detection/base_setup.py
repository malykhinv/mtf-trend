from abc import ABC
from typing import Dict, List, Optional, Tuple

from config.constants import MIN_RR
from domain.models.bar import Bar
from domain.models.confidence import Confidence
from domain.models.scenario import Scenario
from domain.models.setup_signal import SetupSignal
from domain.models.swing_point import SwingPoint
from domain.models.timeframe import Timeframe
from utils.logger import log


class BaseSetup(ABC):
    def __init__(
            self,
            symbol: str,
            bars_by_tf: Dict[Timeframe, List[Bar]],
            atr_by_tf: Dict[Timeframe, float],
            swings: List[SwingPoint],
            confidence: Confidence,
            mtf_states: Dict[Timeframe, any],
    ):
        self.symbol = symbol
        self.bars_by_tf = bars_by_tf
        self.atr_by_tf = atr_by_tf
        self.swings = swings
        self.confidence = confidence
        self.mtf_states = mtf_states
        self.bars_15m = self.bars_by_tf[Timeframe.M15]
        self.atr_15m = self.atr_by_tf[Timeframe.M15]
        self.last = self.bars_15m[-1]
        self.prev = self.bars_15m[-2]
        self.side = None
        self.entry = None
        self.sl = None
        self.tp = None
        self.rr = None

    scenario: Scenario

    def detect(self) -> Optional[SetupSignal]:
        self.log(f"Проверка {self.confidence.value.capitalize()} реакции...")
        if self.confidence.is_strong:
            self.entry, self.sl, self.tp, self.rr = self.define_rr()
            if self.has_strong_conditions():
                return self.build_signal("Флет: сильная реакция от границы")

        if self.confidence.is_moderate and self.has_moderate_conditions():
            return self.build_signal("Флет: умеренная реакция от границы")

        if self.confidence.is_weak and self.has_weak_conditions():
            return self.build_signal("Флет: слабый контакт с границей")

        self.log(f"{self.confidence.value.capitalize()}-факторы не подтверждены.")
        return None

    @staticmethod
    def is_confirmed(*conditions: bool) -> bool:
        return all(conditions)

    # region RR
    def define_rr(self) -> Optional[Tuple[float, float, float, float]]:
        pass

    def rr_condition(self) -> bool:
        if self.rr < MIN_RR:
            self.log(f"RR {self.rr:.2f} < MIN_RR — отклоняем.")
            return False

        return True

    # endregion

    # region Conditions by confidence
    def has_strong_conditions(self, *args) -> bool:
        pass

    def has_moderate_conditions(self, *args) -> bool:
        pass

    def has_weak_conditions(self, *args) -> bool:
        pass

    # endregion

    # region Signal
    def build_signal(self, text: str) -> Optional[SetupSignal]:
        return SetupSignal(
            symbol=self.symbol,
            side=self.side,
            confidence=self.confidence,
            rr=self.rr,
            text=text,
            timestamp=self.last.timestamp,
            entry=self.entry,
            sl=self.sl,
            tp=self.tp,
            scenario=self.scenario
        ) if self.symbol \
             and self.side \
             and self.confidence \
             and self.rr \
             and self.last \
             and self.entry \
             and self.sl \
             and self.tp \
             and self.scenario \
             else None

    # endregion

    def log(self, message: str):
        log(f"{self.symbol} {self.scenario}: {message}")
