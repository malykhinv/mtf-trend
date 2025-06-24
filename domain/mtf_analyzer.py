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
                rr_potential=0.0,
                is_range=False,
                range_high=None,
                range_low=None
            )

        trend: Literal['up', 'down', 'flat'] = 'flat'
        is_in_correction = False
        correction_direction: Literal['up', 'down', 'none'] = 'none'
        is_range = False
        range_high = None
        range_low = None

        highs = [s for s in swings if s.kind == 'high']
        lows = [s for s in swings if s.kind == 'low']

        if len(highs) >= 2 and len(lows) >= 1:
            if highs[-1].price > highs[-2].price and lows[-1].price > lows[-2].price:
                trend = 'up'
        elif len(lows) >= 2 and len(highs) >= 1:
            if lows[-1].price < lows[-2].price and highs[-1].price < highs[-2].price:
                trend = 'down'

        if trend == 'up':
            if len(lows) >= 2 and lows[-1].price < lows[-2].price:
                is_in_correction = True
                correction_direction = 'down'

        elif trend == 'down':
            if len(highs) >= 2 and highs[-1].price > highs[-2].price:
                is_in_correction = True
                correction_direction = 'up'

        # Флет, если отсутствует ясный тренд + хаи/лои в диапазоне
        if trend == 'flat' and len(highs) >= 2 and len(lows) >= 2:
            recent_highs = [h.price for h in highs[-3:]]
            recent_lows = [l.price for l in lows[-3:]]
            max_high = max(recent_highs)
            min_low = min(recent_lows)
            range_size = max_high - min_low
            is_range = range_size / self.atr < 6
            if is_range:
                range_high = max_high
                range_low = min_low

        rr = 0.0
        last = swings[-1]
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
            rr_potential=round(rr, 2),
            is_range=is_range,
            range_high=range_high,
            range_low=range_low
        )
