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
        self.message = f"{self.scenario.value} : {self.confidence.value.capitalize()}"

    scenario: Scenario

    def detect(self) -> Optional[SetupSignal]:
        levels = [
            (self.confidence.is_strong, self.has_strong_conditions),
            (self.confidence.is_moderate, self.has_moderate_conditions),
            (self.confidence.is_weak, self.has_weak_conditions)
        ]

        for confidence_condition, condition_func in levels:
            if confidence_condition:
                rr_result = self.define_rr()
                if rr_result:
                    self.entry, self.sl, self.tp, self.rr = rr_result
                if condition_func():
                    signal = self.build_signal()
                    self.log(self.message)
                    return signal

        self.log("✖ Факторы не подтверждены.")
        return None

    @staticmethod
    def is_confirmed(*conditions: bool) -> bool:
        return all(conditions)

    # region RR
    def define_rr(self) -> Optional[Tuple[float, float, float, float]]:
        pass

    def rr_condition(self) -> bool:
        if self.rr < MIN_RR:
            self.log(f"✖ RR {round(self.rr, 1)} < {round(MIN_RR, 1)}.")
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
    def build_signal(self) -> Optional[SetupSignal]:
        return SetupSignal(
            symbol=self.symbol,
            side=self.side,
            confidence=self.confidence,
            rr=self.rr,
            text=self.message,
            timestamp=self.last.timestamp,
            entry=self.entry,
            sl=self.sl,
            tp=self.tp,
            scenario=self.scenario
        )

    # endregion

    def log(self, message: str):
        log(f"{self.symbol} {self.scenario.capitalize()} {self.confidence.capitalize()}: {message}")
