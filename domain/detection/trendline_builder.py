from typing import List, Optional
import numpy as np
from domain.models.bar import Bar
from domain.models.swing_point import SwingPoint
from domain.models.trendline import Trendline
from utils.logger import log, logw


class TrendlineBuilder:
    @staticmethod
    def build(swings: List[SwingPoint], bars: List[Bar]) -> Optional[Trendline]:
        """Строит наклонку по свингам, если это возможно."""
        if not swings or len(swings) < 2:
            logw("Недостаточно swing-точек для построения наклонки.")
            return None

        highs = [s for s in swings if s.type.is_high]
        if len(highs) < 2:
            logw("Недостаточно high-точек.")
            return None

        main_high = max(highs, key=lambda s: s.price)

        # Кандидаты справа от main_high, ниже по цене
        candidates = [
            s for s in highs
            if s.timestamp > main_high.timestamp and s.price < main_high.price
        ]

        best_line = None
        min_deviation = float("inf")

        for candidate in candidates:
            t1 = main_high.timestamp.timestamp()
            t2 = candidate.timestamp.timestamp()
            if t2 == t1:
                continue

            k = (candidate.price - main_high.price) / (t2 - t1)
            b = main_high.price - k * t1

            # Проверка всех свечей между двумя точками
            crossed = False
            deviations = []

            for bar in bars:
                if not (main_high.timestamp < bar.timestamp < candidate.timestamp):
                    continue
                t = bar.timestamp.timestamp()
                y_line = k * t + b
                deviation = abs(y_line - bar.high)
                deviations.append(deviation)
                if deviation > bar.atr:
                    crossed = True
                    break

            if crossed:
                continue

            avg_dev = np.mean(deviations) if deviations else 0
            if avg_dev < min_deviation:
                min_deviation = avg_dev
                best_line = Trendline(
                    k=k,
                    b=b,
                    point1_time=main_high.timestamp,
                    point2_time=candidate.timestamp,
                    valid=True
                )

        if best_line:
            log(f"Найдено подходящее построение наклонки: от {best_line.point1_time} до {best_line.point2_time}")
            return best_line

        logw("Не удалось построить допустимую наклонку.")
        return None

    @staticmethod
    def has_breakout(trendline: Trendline, bars: List[Bar], idx: int, tolerance_pct: float = 0.5) -> bool:
        """Проверяет факт пробоя наклонки на указанной свече."""
        if not trendline or not trendline.valid:
            logw("Наклонка невалидна для проверки пробоя.")
            return False
        if idx >= len(bars):
            return False

        bar = bars[idx]
        line_price = trendline.get_value_at_time(bar.timestamp)
        diff = bar.close - line_price

        if diff > bar.atr * tolerance_pct:
            log(f"Пробой подтверждён: close={bar.close:.5f} > линия={line_price:.5f} (diff={diff:.5f})")
            return True

        logw(f"Нет пробоя: close={bar.close:.5f}, линия={line_price:.5f}, diff={diff:.5f}")
        return False

    @staticmethod
    def count_touches(trendline: Trendline, bars: List[Bar], tolerance_pct: float = 0.5) -> int:
        """Считает количество касаний наклонки."""
        if not trendline or not trendline.valid:
            logw("Наклонка невалидна для подсчёта касаний.")
            return 0

        touch_count = 0

        for bar in bars:
            line_price = trendline.get_value_at_time(bar.timestamp)
            diff = abs(bar.close - line_price)
            if diff < bar.atr * tolerance_pct:
                touch_count += 1

        log(f"Касаний наклонки найдено: {touch_count}")
        return touch_count
