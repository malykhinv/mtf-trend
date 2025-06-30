from abc import ABC
from typing import Dict, List, Optional, Tuple

from config.constants import MIN_RR, FLOAT_UNDEFINED, MIN_SL_PCT
from domain.models.bar import Bar
from domain.models.confidence import Confidence
from domain.models.mtf_profile import MTFProfile
from domain.models.scenario import Scenario
from domain.models.setup_signal import SetupSignal
from domain.models.side import Side
from domain.models.swing_point import SwingPoint
from domain.models.timeframe import Timeframe
from utils.float_utils import is_defined
from utils.logger import log, logw


class BaseSetup(ABC):
    def __init__(
            self,
            symbol: str,
            bars_by_tf: Dict[Timeframe, List[Bar]],
            atr_by_tf: Dict[Timeframe, float],
            swings: List[SwingPoint],
            confidence: Confidence,
            mtf_states: Dict[Timeframe, any],
            tfs: MTFProfile
    ):
        self.symbol = symbol
        self.bars_by_tf = bars_by_tf
        self.atr_by_tf = atr_by_tf
        self.swings = swings
        self.confidence = confidence
        self.mtf_states = mtf_states
        self.tfs = tfs
        self.bars_tf_setup = self.bars_by_tf[self.tfs.setup]
        self.atr_tf_setup = self.atr_by_tf[self.tfs.setup]
        self.last = self.bars_tf_setup[-1]
        self.prev = self.bars_tf_setup[-2]
        self.side = Side.UNDEFINED
        self.entry = FLOAT_UNDEFINED
        self.sl = FLOAT_UNDEFINED
        self.tp = FLOAT_UNDEFINED
        self.rr = FLOAT_UNDEFINED
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
                if self.confidence.is_strong:
                    self.entry, self.sl, self.tp, self.rr = self.define_rr()
                    if not is_defined(self.entry, self.sl, self.tp, self.rr):
                        self.logw("Некорректные Entry/SL/TP/RR")
                        return None
                    sl_distance_pct = abs(self.entry - self.sl) / self.entry * 100
                    tp_distance_pct = abs(self.tp - self.entry) / self.entry * 100
                    if not sl_distance_pct >= MIN_SL_PCT:
                        self.logw(f"SL слишком близко: {round(sl_distance_pct, 2)}% < {round(MIN_SL_PCT, 2)}%")
                        return None

                    if not tp_distance_pct >= MIN_SL_PCT:
                        self.logw(f"TP слишком близко: {round(tp_distance_pct, 2)}% < {round(MIN_SL_PCT, 2)}%")
                        return None

                if condition_func():
                    signal = self.build_signal()
                    self.log(self.message)
                    return signal

        self.logw("Факторы не подтверждены.")
        return None

    # region RR
    def define_rr(self) -> Tuple[float, float, float, float]:
        pass

    def rr_condition(self) -> bool:
        if self.rr < MIN_RR:
            self.logw(f"RR {round(self.rr, 1)} < {round(MIN_RR, 1)}.")
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
    def build_signal(self) -> SetupSignal:
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
        log(self._get_log_message(message))

    def logw(self, message: str):
        logw(self._get_log_message(message))

    def _get_log_message(self, message: str) -> str:
        return f"{self.confidence.capitalize()} {self.scenario.value} : {message}"
