from domain.models.bar import Bar
from typing import List, Literal
from domain.models.mtf_state import MTFState
from domain.structures import StructureDetector
from utils.logger import log


class MTFAnalyzer:
    def __init__(self, bars: List[Bar], timeframe: str, atr: float):
        self.bars = bars
        self.timeframe = timeframe
        self.atr = atr

    def analyze(self) -> MTFState:
        log(f"Анализ таймфрейма {self.timeframe.upper()}.")

        detector = StructureDetector(self.bars, self.atr)
        swings = detector.detect_swing_points()

        if len(swings) < 3:
            log("Недостаточно swing-точек для анализа.")
            return MTFState(
                timeframe=self.timeframe,
                trend='flat',
                structure=swings,
                is_in_correction=False,
                correction_direction='none',
                rr_potential=0.0
            )

        last = swings[-1]
        prev = swings[-3]

        trend: Literal['up', 'down', 'flat'] = 'flat'
        is_in_correction = False
        correction_direction: Literal['up', 'down', 'none'] = 'none'

        if last.kind == 'high' and last.price > prev.price:
            trend = 'up'
        elif last.kind == 'low' and last.price < prev.price:
            trend = 'down'

        if trend == 'up':
            lows = [s for s in swings if s.kind == 'low']
            if len(lows) >= 2 and lows[-1].price < lows[-2].price:
                is_in_correction = True
                correction_direction = 'down'

        elif trend == 'down':
            highs = [s for s in swings if s.kind == 'high']
            if len(highs) >= 2 and highs[-1].price > highs[-2].price:
                is_in_correction = True
                correction_direction = 'up'

        rr = 0.0
        if trend == 'up':
            rr = (last.price - min([b.low for b in self.bars[-10:]])) / self.atr
        elif trend == 'down':
            rr = (max([b.high for b in self.bars[-10:]]) - last.price) / self.atr

        log(f"Результат: тренд — {trend}, коррекция — {correction_direction if is_in_correction else 'нет'}, RR — {round(rr, 2)}.")

        return MTFState(
            timeframe=self.timeframe,
            trend=trend,
            structure=swings,
            is_in_correction=is_in_correction,
            correction_direction=correction_direction,
            rr_potential=round(rr, 2)
        )
