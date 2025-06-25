from domain.models.bar import Bar
from domain.models.mtf_state import MTFState
from domain.structures import StructureDetector
from utils.logger import log
from typing import List, Literal, cast


class MTFAnalyzer:
    def __init__(self, bars: List[Bar], timeframe: str, atr: float):
        self.bars = bars
        self.timeframe = timeframe
        self.atr = atr

    def analyze(self) -> MTFState:
        log(f"Анализ таймфрейма {self.timeframe.upper()}.")
        detector = StructureDetector(self.bars, self.atr)
        swings = detector.detect_swing_points()

        if len(swings) < 4:
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
        correction_direction: Literal['up', 'down', 'none'] = 'none'
        is_in_correction = False
        is_range = False
        range_high = None
        range_low = None

        highs = [s for s in swings if s.kind == 'high']
        lows = [s for s in swings if s.kind == 'low']

        # Проверка up-тренда
        if len(highs) >= 3 and len(lows) >= 3:
            hh1, hh2 = highs[-2].price, highs[-1].price
            hl1, hl2 = lows[-2].price, lows[-1].price
            if hh2 > hh1 and hl2 > hl1 and min(abs(hh2 - hh1), abs(hl2 - hl1)) > 1.5 * self.atr:
                trend = 'up'
                if hl2 < hl1:
                    is_in_correction = True
                    correction_direction = 'down'

        # Проверка down-тренда
        if trend == 'flat' and len(highs) >= 3 and len(lows) >= 3:
            lh1, lh2 = highs[-2].price, highs[-1].price
            ll1, ll2 = lows[-2].price, lows[-1].price
            if lh2 < lh1 and ll2 < ll1 and min(abs(lh1 - lh2), abs(ll1 - ll2)) > 1.5 * self.atr:
                trend = 'down'
                if lh2 > lh1:
                    is_in_correction = True
                    correction_direction = 'up'

        # Проверка диапазона (флэт)
        if trend == 'flat' and len(highs) >= 3 and len(lows) >= 3:
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

        rr = 0.0
        last = swings[-1]
        if trend == 'up':
            rr = (last.price - min([b.low for b in self.bars[-10:]])) / self.atr
        elif trend == 'down':
            rr = (max([b.high for b in self.bars[-10:]]) - last.price) / self.atr

        log(f"Результат: тренд — {trend}, коррекция — {correction_direction if is_in_correction else 'нет'}, RR — {round(rr, 2)}.")

        return MTFState(
            timeframe=self.timeframe,
            trend=cast(Literal['up', 'down', 'flat'], trend),
            structure=swings,
            is_in_correction=is_in_correction,
            correction_direction=cast(Literal['up', 'down', 'none'], correction_direction),
            rr_potential=round(rr, 2),
            is_range=is_range,
            range_high=range_high,
            range_low=range_low
        )
