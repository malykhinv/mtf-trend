from abc import ABC
from typing import Dict, List, Optional, Tuple

from config.constants import MIN_RR, FLOAT_UNDEFINED, MIN_SL_PCT
from domain.models.bar import Bar
from domain.models.confidence import Confidence
from domain.models.mtf_profile import MTFProfile
from domain.models.setup_signal import SetupSignal
from domain.models.side import Side
from domain.models.timeframe import Timeframe
from utils.float_utils import is_defined
from utils.logger import log, logw


class Setup(ABC):
    def __init__(
            self,
            symbol: str,
            bars_by_tf: Dict[Timeframe, List[Bar]],
            confidence: Confidence,
            tfs: MTFProfile
    ):
        self.symbol = symbol
        self.bars_setup = bars_by_tf[tfs.setup]
        self.bars_entry = bars_by_tf[tfs.entry]
        self.confidence = confidence
        self.tfs = tfs
        self.trendline = None
        self.entry = FLOAT_UNDEFINED
        self.sl = FLOAT_UNDEFINED
        self.tp = FLOAT_UNDEFINED
        self.rr = FLOAT_UNDEFINED
        self.message = f"{self.confidence.value.capitalize()}"

    def validated_or_none(self) -> Optional[SetupSignal]:
        levels = [
            (self.confidence.is_strong, self.has_strong_conditions),
            (self.confidence.is_moderate, self.has_moderate_conditions),
            (self.confidence.is_weak, self.has_weak_conditions)
        ]

        for confidence_condition, condition_func in levels:
            if confidence_condition:
                if condition_func():
                    signal = self.build_signal()
                    self.log(self.message)
                    return signal

        return None

    # region RR
    def define_rr(self) -> Tuple[float, float, float, float]:
        pass

    def _check_rr(self) -> bool:
        self.entry, self.sl, self.tp, self.rr = self.define_rr()

        if not is_defined(self.entry, self.sl, self.tp, self.rr):
            self.logw("Некорректные Entry/SL/TP/RR")
            return False

        sl_distance_pct = abs(self.entry - self.sl) / self.entry * 100
        tp_distance_pct = abs(self.tp - self.entry) / self.entry * 100
        if not sl_distance_pct >= MIN_SL_PCT:
            self.logw(f"SL слишком близко: {round(sl_distance_pct, 2)}% < {round(MIN_SL_PCT, 2)}%")
            return False

        if not tp_distance_pct >= MIN_SL_PCT:
            self.logw(f"TP слишком близко: {round(tp_distance_pct, 2)}% < {round(MIN_SL_PCT, 2)}%")
            return False

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
            side=Side.LONG,
            confidence=self.confidence,
            text=self.message,
            timestamp=self.bars_entry[-1].timestamp,
            trendline=self.trendline,
            entry=self.entry,
            sl=self.sl,
            tp=self.tp,
            rr=self.rr
        )

    # endregion

    def log(self, message: str):
        log(self._get_log_message(message))

    def logw(self, message: str):
        logw(self._get_log_message(message))

    def _get_log_message(self, message: str) -> str:
        return f"{self.confidence.capitalize()} : {message}"
