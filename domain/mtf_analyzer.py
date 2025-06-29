from config.constants import FLOAT_UNDEFINED, TREND_SIZE_ATR_FACTOR, MAX_RANGE_SIZE_PCT
from domain.models.bar import Bar
from domain.models.mtf_state import MTFState
from domain.models.phase import Phase
from domain.models.price_direction import PriceDirection
from domain.models.timeframe import Timeframe
from domain.structures import StructureDetector
from utils.logger import log, logw
from typing import List


class MTFAnalyzer:
    def __init__(self, bars: List[Bar], timeframe: Timeframe, atr: float):
        self.bars = bars
        self.timeframe = timeframe
        self.atr = atr

    def analyze(self) -> MTFState:
        detector = StructureDetector(self.bars, self.atr)
        swings = detector.detect_swing_points()

        if len(swings) < 4:
            log("Недостаточно swing-точек для анализа.")
            return MTFState(
                timeframe=self.timeframe,
                phase=Phase.FLAT,
                structure=swings,
                is_in_correction=False,
                correction_direction=PriceDirection.UNDEFINED,
                rr_potential=FLOAT_UNDEFINED,
                is_range=False,
                range_high=FLOAT_UNDEFINED,
                range_low=FLOAT_UNDEFINED
            )

        highs = [s for s in swings if s.type.is_high]
        lows = [s for s in swings if s.type.is_low]

        check_n_highs = min(len(highs), 5)
        check_n_lows = min(len(lows), 5)

        lower_highs = sum(1 for i in range(1, check_n_highs) if highs[i].price < highs[i - 1].price)
        higher_lows = sum(1 for i in range(1, check_n_lows) if lows[i].price > lows[i - 1].price)

        downtrend_confidence = lower_highs / (check_n_highs - 1) if check_n_highs > 1 else 0
        uptrend_confidence = higher_lows / (check_n_lows - 1) if check_n_lows > 1 else 0

        phase = Phase.UNDEFINED
        if downtrend_confidence >= 0.7:
            phase = Phase.DOWNTREND
        elif uptrend_confidence >= 0.7:
            phase = Phase.UPTREND

        # Коррекция
        is_in_correction = False
        correction_direction = PriceDirection.UNDEFINED

        rr = FLOAT_UNDEFINED
        last = swings[-1]
        if phase and phase.is_uptrend:
            rr = (last.price - min([b.low for b in self.bars[-10:]])) / self.atr
        elif phase and phase.is_downtrend:
            rr = (max([b.high for b in self.bars[-10:]]) - last.price) / self.atr

        log(f"{self.timeframe.value}: "
            f"тренд — {phase.value if phase else 'undefined'}, "
            f"коррекция — {'нет' if not is_in_correction else correction_direction.value}, "
            f"RR — {round(rr, 2)}.")

        return MTFState(
            timeframe=self.timeframe,
            phase=phase,
            structure=swings,
            is_in_correction=is_in_correction,
            correction_direction=correction_direction,
            rr_potential=round(rr, 2) if rr != FLOAT_UNDEFINED else FLOAT_UNDEFINED,
            is_range=False,
            range_high=FLOAT_UNDEFINED,
            range_low=FLOAT_UNDEFINED
        )


