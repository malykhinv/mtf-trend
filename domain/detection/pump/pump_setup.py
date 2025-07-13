# domain/detection/pump/pump_setup.py
from statistics import mean
from typing import List, Dict, Optional

from domain.detection.setup import Setup
from domain.detection.trendline_builder import TrendlineBuilder
from domain.models.bar import Bar
from domain.models.confidence import Confidence
from domain.models.ema import EMA
from domain.models.mtf_profile import MTFProfile
from domain.models.swing_point import SwingPoint
from domain.models.swing_type import SwingType
from domain.models.timeframe import Timeframe
from domain.models.trendline import Trendline
from domain.structures import StructureDetector
from config.constants import (
    FLOAT_UNDEFINED,
    MIN_RR,
    MIN_SL_PCT,
    MIN_TP_PCT,
    MAX_CORRECTION_PCT,
    CONSOLIDATION_HOURS,
    MAX_RANGE_PCT,
    PUMP_MIN_MINUTES,
    MIN_PUMP_PCT,
    VOLUME_RATIO_MIN,
)
from utils.float_utils import is_defined
from utils.math_utils import calculate_atr


class PumpSetup(Setup):
    def __init__(self, symbol: str, bars_by_tf: Dict[Timeframe, List[Bar]], confidence: Confidence, tfs: MTFProfile):
        super().__init__(symbol, bars_by_tf, confidence, tfs)
        self.structure_detector = StructureDetector()
        self.trendline_builder = TrendlineBuilder()
        self.swings = []
        self.mid_p1 = FLOAT_UNDEFINED
        self.last = self.bars_setup[-1]
        self.consolidation_bars = []
        self.pump_bars = []
        self.correction_bars = []
        self.main_high = SwingPoint.undefined()

    def has_strong_conditions(self) -> bool:
        if not self.has_moderate_conditions():
            return False

        atr = calculate_atr(self.correction_bars)
        self.trendline = trendline = self.trendline_builder.build(self.swings, atr)

        if not self._check_trendline_validity(trendline):
            return False

        if not self._check_trendline_touches(trendline, self.correction_bars):
            return False

        if not self._check_trendline_breakout(trendline, self.correction_bars):
            return False

        if not self._check_rr():
            return False

        return True

    def has_moderate_conditions(self) -> bool:
        if not self.has_weak_conditions():
            return False

        # Найти главный хай после пампа
        main_high_bar = max(self.pump_bars, key=lambda b: b.high)
        self.main_high = SwingPoint(
            price=main_high_bar.high,
            index=len(self.consolidation_bars) + self.pump_bars.index(main_high_bar),
            type=SwingType.HIGH,
            confirmed=True
        )

        self.correction_bars = self._get_correction_bars(self.pump_bars)
        self.swings = swings = self.structure_detector.detect_swing_points(self.correction_bars)

        if not self._check_correction_structure(swings):
            return False

        if not self._check_correction_depth():
            return False

        return True

    def has_weak_conditions(self) -> bool:
        if not self._check_consolidation():
            return False

        if not self._check_pump_duration():
            return False

        if not self._check_pump_growth():
            return False

        if not self._check_volume():
            return False

        return True

    def _check_consolidation(self) -> bool:
        bars = self.bars_setup

        closes = [b.close for b in bars]
        volumes = [b.volume for b in bars]
        oi_values = [b.oi for b in bars]

        ema_series_price = self._calculate_ema_series_from_values(closes)
        ema_series_vol = self._calculate_ema_series_from_values(volumes)
        has_oi_data = any(oi != FLOAT_UNDEFINED for oi in oi_values)
        ema_series_oi = self._calculate_ema_series_from_values(oi_values) if has_oi_data else [None] * len(bars)
        atr_series = self._calculate_atr_series(bars)

        period1_end_index = self._find_pump_start_index(
            bars,
            ema_series_price,
            ema_series_vol,
            ema_series_oi,
            atr_series
        )

        if period1_end_index is None or period1_end_index not in range(0, len(bars) - 1):
            self.logw("Не удалось найти старт пампа.")
            return False

        period2_start_index = period1_end_index + 1
        self.setup_timestamp = bars[period2_start_index].timestamp

        self.consolidation_bars = bars[:period1_end_index]
        self.pump_bars = bars[period2_start_index:]

        if not self.consolidation_bars or not self.pump_bars:
            self.logw("Недостаточно данных после разделения на периоды.")
            return False

        high_p1 = max(b.high for b in self.consolidation_bars)
        low_p1 = min(b.low for b in self.consolidation_bars)

        if low_p1 <= 0:
            self.logw("Неверный low в консолидации (<= 0).")
            return False

        self.high_p1 = high_p1
        self.mid_p1 = (high_p1 + low_p1) / 2

        range_p1_pct = abs(high_p1 - low_p1) / low_p1 * 100
        if range_p1_pct > MAX_RANGE_PCT:
            self.logw(f"Диапазон консолидации слишком большой: {range_p1_pct:.1f}% > {MAX_RANGE_PCT}%")
            return False

        consolidation_duration_hours = int(
            (self.consolidation_bars[-1].timestamp - self.consolidation_bars[0].timestamp).total_seconds() / 3600)
        if consolidation_duration_hours < CONSOLIDATION_HOURS:
            self.logw(f"Консолидация короче {CONSOLIDATION_HOURS}h: {consolidation_duration_hours:.0f}h")
            return False

        return True

    def _check_pump_duration(self) -> bool:
        bars = self.bars_setup
        pump_start_index = len(self.consolidation_bars)
        pump_duration_min = int((bars[-1].timestamp - bars[pump_start_index].timestamp).total_seconds() / 60)
        if pump_duration_min < PUMP_MIN_MINUTES:
            self.logw(f"Период пампа слишком короткий: {pump_duration_min:.0f}m < {PUMP_MIN_MINUTES}m")
            return False
        return True

    def _check_pump_growth(self) -> bool:
        bars = self.bars_setup
        pump_start_index = len(self.consolidation_bars)

        pump_start_close = bars[pump_start_index].close
        pump_end_close = bars[-1].close
        pump_change_pct = (pump_end_close - pump_start_close) / pump_start_close * 100

        if pump_change_pct < MIN_PUMP_PCT:
            self.logw(f"Рост цены недостаточный: {pump_change_pct:.1f}% < {MIN_PUMP_PCT}%")
            return False

        if pump_end_close <= self.high_p1:
            self.logw("Цена после старта не закрепилась выше high периода 1.")
            return False

        self.log(f"Памп подтверждён: рост {pump_change_pct:.0f}%")
        return True

    def _check_volume(self) -> bool:
        avg_vol_p1 = sum(b.volume for b in self.consolidation_bars) / len(self.consolidation_bars)
        avg_vol_p2 = sum(b.volume for b in self.pump_bars) / len(self.pump_bars)

        if avg_vol_p2 < avg_vol_p1 * VOLUME_RATIO_MIN:
            self.logw(f"Объём пампа недостаточный: {avg_vol_p2:.0f} < {avg_vol_p1 * VOLUME_RATIO_MIN:.0f}")
            return False

        self.log(f"Объём пампа подтверждён: в {avg_vol_p2 / avg_vol_p1:.1f}x")
        return True

    def _check_correction_depth(self) -> bool:
        rise = self.main_high.price - self.mid_p1

        if rise <= 0:
            self.logw("Некорректный рост перед коррекцией (<= 0).")
            return False

        correction_low = min(bar.low for bar in self.correction_bars)
        correction_depth = abs(self.main_high.price - correction_low) / rise * 100

        if correction_depth > MAX_CORRECTION_PCT:
            self.logw(f"Глубина коррекции слишком большая: {correction_depth:.2f}% > {MAX_CORRECTION_PCT}%")
            return False

        self.log(f"Глубина коррекции подтверждена: {correction_depth:.2f}% ≤ {MAX_CORRECTION_PCT}%")
        return True

    def _check_correction_structure(self, swings: List[SwingPoint]) -> bool:
        """
        Проверка структуры коррекции.
        - Есть нисходящий тренд с LH и LL.
        - Нет закрытия ниже EMA.
        """
        if not swings or len(swings) < 5:
            self.logw("Недостаточно swing-поинтов для анализа коррекции.")
            return False

        # Проверяем LH
        lh_count = 0
        highs = [s for s in swings if s.type.is_high]
        for i in range(1, len(highs)):
            if highs[i].price < highs[i - 1].price:
                lh_count += 1

        # Проверяем LL
        ll_count = 0
        lows = [s for s in swings if s.type.is_low]
        for i in range(1, len(lows)):
            if lows[i].price < lows[i - 1].price:
                ll_count += 1

        if lh_count < 2 or ll_count < 2:
            self.logw(f"Недостаточно LH/LL: LH={lh_count}, LL={ll_count}")
            return False

        self.log("Структура коррекции подтверждена: есть LH и LL.")
        return True

    def _get_correction_bars(self, bars: List[Bar]) -> List[Bar]:
        """
        Возвращает бары коррекции — все бары после главного high.
        """
        if self.main_high.is_undefined:
            self.logw("main_high не задан, не можем выделить correction bars.")
            return []

        correction_bars = bars[self.main_high.index - len(self.consolidation_bars) + 1:]
        if not correction_bars:
            self.logw("После main_high нет баров для коррекции.")
        return correction_bars

    def _check_trendline_validity(self, trendline: Trendline) -> bool:
        if not trendline or not trendline.valid:
            self.logw("Наклонка невалидна.")
            return False
        return True

    def _check_trendline_touches(self, trendline: Trendline, bars: List[Bar]) -> bool:
        correction_atr = sum(abs(b.high - b.low) for b in bars) / len(bars)
        touches = self.trendline_builder.count_touches(trendline, bars, correction_atr)
        if touches < 2:
            self.logw(f"Недостаточно касаний наклонки: {touches} < 2.")
            return False
        self.log(f"Подтверждено касаний наклонки: {touches}.")
        return True

    def _check_trendline_breakout(self, trendline: Trendline, bars: List[Bar]) -> bool:
        correction_atr = sum(abs(b.high - b.low) for b in bars) / len(bars)

        last_two_indices = [len(bars) - 2, len(bars) - 1]
        for idx in last_two_indices:
            if not self.trendline_builder.has_breakout(trendline, bars, idx, correction_atr):
                self.logw(f"Бар {idx} не закрепился выше наклонки.")
                return False

            # Заполняем вспомогательные списки
            self.bars_before_breakout = bars[:idx]
            self.bars_after_breakout = bars[idx + 1:]

        self.log("Пробой и закрепление выше наклонки подтверждены последними двумя свечами.")
        return True

    def _check_rr(self) -> bool:
        entry = self.last.close
        sl_candidates = [s.price for s in reversed(self.swings) if s.type.is_low and s.price < entry]

        if not sl_candidates:
            self.logw("Нет swing low для SL.")
            return False

        sl = sl_candidates[0]
        tp = self.main_high.price
        if tp is None or tp <= entry:
            self.logw("Нет подходящего TP.")
            return False

        sl_distance_pct = abs(entry - sl) / entry * 100
        tp_distance_pct = abs(tp - entry) / entry * 100

        if sl_distance_pct < MIN_SL_PCT:
            self.logw(f"SL слишком близко: {sl_distance_pct:.2f}% < {MIN_SL_PCT}%")
            return False

        if tp_distance_pct < MIN_TP_PCT:
            self.logw(f"TP слишком близко: {tp_distance_pct:.2f}% < {MIN_TP_PCT}%")
            return False

        rr = abs(tp - entry) / abs(entry - sl)
        if rr < MIN_RR:
            self.logw(f"RR {rr:.2f} меньше минимального {MIN_RR}.")
            return False

        self.log(
            f"RR подтверждён: Entry={entry:.5f}, SL={sl:.5f}, TP={tp:.5f}, SL%={sl_distance_pct:.2f}, TP%={tp_distance_pct:.2f}, RR={rr:.2f}")
        return True

    def _find_pump_start_index(self, bars: List[Bar], ema_series_price: List[EMA], ema_series_vol: List[EMA],
                               ema_series_oi: List[Optional[EMA]], atr_series: List[float]) -> Optional[int]:
        atr_mean = FLOAT_UNDEFINED
        for i in range(50, len(bars)):
            ema_p = ema_series_price[i]
            ema_v = ema_series_vol[i]
            ema_o = ema_series_oi[i] if ema_series_oi[i] else None
            atr_mean = mean([atr_series[i], atr_mean]) if is_defined(atr_mean) else atr_series[i]
            price = bars[i].close

            price_ok = self._check_ema_structure(ema_p, atr_mean) or self._check_ema_structure(ema_p)
            vol_ok = self._check_ema_structure(ema_v)
            oi_ok = True if ema_o is None else self._check_ema_structure(ema_o)

            if sum([price_ok, vol_ok, oi_ok]) >= 1:
                self.log(f"{i:>4} "
                         f"{bars[i].timestamp.strftime('%d.%m %H:%M')} "
                         f"{'+' if price_ok else ''} "
                         f"{'+' if vol_ok else ''} "
                         f"{'+' if oi_ok else ''}")

            price_above = price > ema_p.ema20 and price > ema_p.ema50 and price > ema_p.ema100 and price > ema_p.ema200

            if price_ok and vol_ok and oi_ok and price_above:
                self.log(f"✨ {bars[i].timestamp.strftime('%d.%m %H:%M')}")
                return i

        self.logw("Старт пампа не найден.")
        return None

    @staticmethod
    def _check_ema_structure(ema_obj: EMA, atr_value: float = FLOAT_UNDEFINED) -> bool:
        order_ok = ema_obj.ema20 > ema_obj.ema50 > ema_obj.ema100 > ema_obj.ema200

        if is_defined(atr_value):
            spacing_20_50 = (ema_obj.ema20 - ema_obj.ema50)
            spacing_50_100 = (ema_obj.ema50 - ema_obj.ema100)
            spacing_100_200 = (ema_obj.ema100 - ema_obj.ema200)
            spacing_ok = spacing_20_50 > atr_value and spacing_50_100 > atr_value and spacing_100_200 > atr_value
        else:
            spacing_pct = 0.0025
            spacing_20_50 = (ema_obj.ema20 - ema_obj.ema50) / ema_obj.ema50
            spacing_50_100 = (ema_obj.ema50 - ema_obj.ema100) / ema_obj.ema100
            spacing_100_200 = (ema_obj.ema100 - ema_obj.ema200) / ema_obj.ema200
            spacing_ok = spacing_20_50 > spacing_pct and spacing_50_100 > spacing_pct and spacing_100_200 > spacing_pct

        return order_ok and spacing_ok

    @staticmethod
    def _calculate_ema_series_from_values(values: List[float]) -> List[EMA]:
        periods = [20, 50, 100, 200]
        ema_values = {p: [] for p in periods}
        k_values = {p: 2 / (p + 1) for p in periods}

        for p in periods:
            if len(values) >= p:
                sma = sum(values[:p]) / p
                ema_values[p].append(sma)
            else:
                ema_values[p].append(values[0])

        for i in range(1, len(values)):
            for p in periods:
                prev_ema = ema_values[p][-1]
                k = k_values[p]
                ema = values[i] * k + prev_ema * (1 - k)
                ema_values[p].append(ema)

        ema_list = []
        for i in range(len(values)):
            ema_obj = EMA(
                ema20=ema_values[20][i],
                ema50=ema_values[50][i],
                ema100=ema_values[100][i],
                ema200=ema_values[200][i],
            )
            ema_list.append(ema_obj)

        return ema_list

    @staticmethod
    def _calculate_atr_series(bars: List[Bar], period: int = 14) -> List[float]:
        trs = []
        for i in range(1, len(bars)):
            high = bars[i].high
            low = bars[i].low
            prev_close = bars[i - 1].close
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)

        atr_list = []
        for i in range(len(trs)):
            if i < period:
                atr_list.append(sum(trs[:i + 1]) / (i + 1))
            else:
                prev_atr = atr_list[-1]
                atr = (prev_atr * (period - 1) + trs[i]) / period
                atr_list.append(atr)
        atr_list.insert(0, 0.0)
        return atr_list
