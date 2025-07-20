from config.constants import FLOAT_UNDEFINED
from domain.models.bar import Bar
from domain.models.swing_point import SwingPoint
from typing import List, Optional
from utils.logger import log, logw
from domain.models.trendline import Trendline
import numpy as np

class TrendlineBuilder:

    @staticmethod
    def build(swings: List[SwingPoint], atr: float) -> Optional[Trendline]:
        if not swings or len(swings) < 2:
            logw("Недостаточно swings для построения наклонки.")
            return None

        point1 = max(swings, key=lambda s: s.price if s.type.is_high else FLOAT_UNDEFINED)
        correction_highs = [s for s in swings if s.type.is_high and s.index > point1.index]

        if not correction_highs:
            logw("Нет correction high для построения наклонки.")
            return None

        # Используем все correction_highs для регрессии
        x = np.array([s.index for s in correction_highs])
        y = np.array([s.price for s in correction_highs])

        if len(x) < 2:
            logw("Недостаточно high для регрессии.")
            return None

        # Линейная регрессия: y = kx + b
        A = np.vstack([x, np.ones(len(x))]).T
        k, b = np.linalg.lstsq(A, y, rcond=None)[0]

        # Проверяем отклонения
        deviations = np.abs(y - (k * x + b))
        avg_dev = np.mean(deviations)
        atr_threshold = 0.5

        if avg_dev > atr * atr_threshold:
            logw(f"Среднее отклонение {avg_dev:.5f} превышает допустимое ({atr * atr_threshold:.5f}).")
            return None

        return Trendline(k=k, b=b, point1_index=int(x[0]), point2_index=int(x[-1]), valid=True)

    @staticmethod
    def has_breakout(
            trendline: Trendline,
            bars: List[Bar],
            idx: int,
            tolerance_pct: float = 0.5
    ) -> bool:
        if not trendline or not trendline.valid:
            logw("Наклонка невалидна для проверки пробоя.")
            return False

        close_price = bars[idx].close
        line_price = trendline.get_value_at(idx)
        diff = close_price - line_price

        if diff > bars[idx].atr * tolerance_pct:
            log(f"Пробой подтверждён: close={close_price:.5f} > линия={line_price:.5f} (diff={diff:.5f})")
            return True

        logw(f"Нет пробоя: close={close_price:.5f}, линия={line_price:.5f}, diff={diff:.5f}")
        return False

    @staticmethod
    def count_touches(trendline: Trendline, bars: List[Bar], tolerance_pct: float = 0.5) -> int:
        if not trendline or not trendline.valid:
            logw("Наклонка невалидна для подсчёта касаний.")
            return 0

        touch_count = 0

        for i, bar in enumerate(bars):
            line_price = trendline.get_value_at(i)
            diff = abs(bar.close - line_price)
            if diff < bar.atr * tolerance_pct:
                touch_count += 1

        log(f"Касаний наклонки найдено: {touch_count}")
        return touch_count
