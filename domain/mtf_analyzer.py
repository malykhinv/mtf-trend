from config.constants import FLOAT_UNDEFINED
from domain.models.bar import Bar
from domain.models.mtf_state import MTFState
from domain.models.phase import Phase
from domain.models.price_direction import PriceDirection
from domain.models.timeframe import Timeframe
from domain.structures import StructureDetector
from utils.logger import log
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

        phase = Phase.UNDEFINED
        correction_direction = PriceDirection.UNDEFINED
        is_in_correction = False
        is_range = False
        range_high = FLOAT_UNDEFINED
        range_low = FLOAT_UNDEFINED

        highs = [s for s in swings if s.type.is_high]
        lows = [s for s in swings if s.type.is_low]

        # Проверка up-тренда
        if len(highs) >= 3 and len(lows) >= 3:
            hh1, hh2 = highs[-2].price, highs[-1].price
            hl1, hl2 = lows[-2].price, lows[-1].price
            if hh2 > hh1 and hl2 > hl1 and min(abs(hh2 - hh1), abs(hl2 - hl1)) > 1.5 * self.atr:
                phase = Phase.UPTREND
                if hl2 < hl1:
                    is_in_correction = True
                    correction_direction = PriceDirection.DOWN

        # Проверка down-тренда
        if not phase and len(highs) >= 3 and len(lows) >= 3:
            lh1, lh2 = highs[-2].price, highs[-1].price
            ll1, ll2 = lows[-2].price, lows[-1].price
            if lh2 < lh1 and ll2 < ll1 and min(abs(lh1 - lh2), abs(ll1 - ll2)) > 1.5 * self.atr:
                phase = Phase.DOWNTREND
                if lh2 > lh1:
                    is_in_correction = True
                    correction_direction = PriceDirection.UP

        # Проверка диапазона (флэт)
        if not phase and len(highs) >= 3 and len(lows) >= 3:
            recent_highs = [h.price for h in highs[-3:]]
            recent_lows = [l.price for l in lows[-3:]]
            max_high = max(recent_highs)
            min_low = min(recent_lows)
            range_size = max_high - min_low
            total_move = abs(self.bars[-1].close - self.bars[-20].close)

            if range_size / min_low > 0.5:
                log("Диапазон слишком широкий (>50%) — не считаем флетом.")
            elif range_size / self.atr < 6 and total_move < 3 * self.atr:
                is_range = True
                range_high = max_high
                range_low = min_low
                phase = Phase.FLAT

        rr = FLOAT_UNDEFINED
        last = swings[-1]
        if phase and phase.is_uptrend:
            rr = (last.price - min([b.low for b in self.bars[-10:]])) / self.atr
        elif phase and phase.is_downtrend:
            rr = (max([b.high for b in self.bars[-10:]]) - last.price) / self.atr

        log(f"{self.timeframe.value}: "
            f"тренд — {phase.value}, "
            f"коррекция — {correction_direction.value if is_in_correction else 'нет'}, "
            f"RR — {round(rr, 2)}.")

        return MTFState(
            timeframe=self.timeframe,
            phase=phase,
            structure=swings,
            is_in_correction=is_in_correction,
            correction_direction=correction_direction,
            rr_potential=round(rr, 2),
            is_range=is_range,
            range_high=range_high,
            range_low=range_low
        )
