from config.constants import FLOAT_UNDEFINED
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
        detector = StructureDetector(self.bars, self.atr, threshold_multiplier=1.0)
        swings = detector.detect_swing_points()

        if len(swings) < 4:
            logw("Недостаточно swing-точек для тренда. Помечаем как FLAT.")
            return MTFState(
                timeframe=self.timeframe,
                phase=Phase.FLAT,
                structure=swings,
                is_in_correction=False,
                correction_direction=PriceDirection.UNDEFINED,
                rr_potential=FLOAT_UNDEFINED,
                is_range=True,
                range_high=max([s.price for s in swings]) if swings else FLOAT_UNDEFINED,
                range_low=min([s.price for s in swings]) if swings else FLOAT_UNDEFINED
            )

        highs = [s for s in swings if s.type.is_high]
        lows = [s for s in swings if s.type.is_low]

        lower_highs = sum(1 for i in range(1, len(highs)) if highs[i].price < highs[i - 1].price)
        higher_lows = sum(1 for i in range(1, len(lows)) if lows[i].price > lows[i - 1].price)

        downtrend_confidence = lower_highs / (len(highs) - 1) if len(highs) > 1 else 0
        uptrend_confidence = higher_lows / (len(lows) - 1) if len(lows) > 1 else 0

        if downtrend_confidence >= 0.7:
            phase = Phase.DOWNTREND
        elif uptrend_confidence >= 0.7:
            phase = Phase.UPTREND
        else:
            phase = Phase.FLAT

        # Добавляем определение коррекции
        is_in_correction = False
        correction_direction = PriceDirection.UNDEFINED
        if phase.is_uptrend and swings[-1].type.is_low:
            is_in_correction = True
            correction_direction = PriceDirection.DOWN
        elif phase.is_downtrend and swings[-1].type.is_high:
            is_in_correction = True
            correction_direction = PriceDirection.UP

        rr = FLOAT_UNDEFINED
        last = swings[-1]
        if phase.is_uptrend:
            rr = (last.price - min([b.low for b in self.bars[-10:]])) / self.atr
        elif phase.is_downtrend:
            rr = (max([b.high for b in self.bars[-10:]]) - last.price) / self.atr

        log(f"{self.timeframe.value}: "
            f"тренд — {phase.value}, "
            f"коррекция — {'да' if is_in_correction else 'нет'}, "
            f"RR — {round(rr, 2)}.")

        return MTFState(
            timeframe=self.timeframe,
            phase=phase,
            structure=swings,
            is_in_correction=is_in_correction,
            correction_direction=correction_direction,
            rr_potential=round(rr, 2) if rr > 0.0 else FLOAT_UNDEFINED,
            is_range=phase.is_flat,
            range_high=max([s.price for s in swings]) if phase == Phase.FLAT else FLOAT_UNDEFINED,
            range_low=min([s.price for s in swings]) if phase == Phase.FLAT else FLOAT_UNDEFINED
        )



