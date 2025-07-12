# domain/detection/pump/pump_setup.py
from typing import Dict, List

from domain.detection.setup import Setup
from domain.detection.trendline_builder import TrendlineBuilder
from domain.models.bar import Bar
from domain.models.confidence import Confidence
from domain.models.ema import EMA
from domain.models.mtf_profile import MTFProfile
from config.constants import (
    MIN_RR,
    FLOAT_UNDEFINED,
    MIN_SL_PCT,
    MIN_TP_PCT,
    MAX_CORRECTION_PCT,
    CONSOLIDATION_HOURS,
    MAX_RANGE_PCT,
    PUMP_MIN_MINUTES,
    MIN_PUMP_PCT,
    VOLUME_RATIO_MIN,
    BIG_BODY_ATR_MULTIPLIER,
    MAX_BIG_BODY_SHARE
)
from domain.models.swing_point import SwingPoint
from domain.models.swing_type import SwingType
from domain.models.timeframe import Timeframe
from domain.models.trendline import Trendline
from domain.structures import StructureDetector
from utils.math_utils import calculate_atr


class PumpSetup(Setup):
    def __init__(
            self,
            symbol: str,
            bars_by_tf: Dict[Timeframe, List[Bar]],
            confidence: Confidence,
            tfs: MTFProfile
    ):
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
        self.pump_timestamp = 0

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

        self.correction_bars = self._get_correction_bars(self.pump_bars)
        self.swings = swings = self.structure_detector.detect_swing_points(self.correction_bars)

        if not self._check_correction_structure(self.correction_bars, swings):
            return False

        if not self._check_correction_depth():
            return False

        return True

    def has_weak_conditions(self) -> bool:
        if not self._check_pump_condition(self.bars_setup):
            return False

        return True

    def _check_pump_condition(self, bars: List[Bar]) -> bool:
        """
        Поиск period1 и period2 по EMA и ATR, затем проверка условий пампа.
        """
        if len(bars) < 100:
            self.logw("Недостаточно истории баров.")
            return False

        # EMA series
        ema_series = self._calculate_ema_series(bars)
        atr_series = self._calculate_atr_series(bars)

        period1_end_index = self._find_period1_end_index(bars, ema_series, atr_series)

        if not period1_end_index or period1_end_index not in [0, len(bars) - 1]:
            self.logw("Не удалось найти старт пампа.")
            return False

        period2_start_index = period1_end_index + 1
        self.pump_timestamp = bars[period2_start_index].timestamp

        self.consolidation_bars = bars[:period1_end_index]
        self.pump_bars = bars[period2_start_index:]

        if not self.consolidation_bars or not self.pump_bars:
            self.logw("Недостаточно данных после разделения на периоды.")
            return False

        high_bar = max(self.pump_bars, key=lambda b: b.high)
        high_index_global = bars.index(high_bar)
        self.main_high = SwingPoint(
            type=SwingType.HIGH,
            index=high_index_global,
            price=high_bar.high,
            confirmed=True
        )

        # ---------- Проверка диапазона цены в консолидации ----------
        high_p1 = max(b.high for b in self.consolidation_bars)
        low_p1 = min(b.low for b in self.consolidation_bars)
        self.mid_p1 = (high_p1 + low_p1) / 2
        range_p1_pct = abs(high_p1 - low_p1) / low_p1 * 100

        if range_p1_pct > MAX_RANGE_PCT:
            self.logw(f"Диапазон консолидации слишком большой: {range_p1_pct:.1f}% > {MAX_RANGE_PCT}%")
            return False

        # ---------- Проверка длительности периодов ----------
        consolidation_start_time = self.consolidation_bars[0].timestamp
        consolidation_end_time = self.consolidation_bars[-1].timestamp
        delta = consolidation_end_time - consolidation_start_time
        consolidation_duration_hours = int(delta.total_seconds() / 3600)

        if consolidation_duration_hours < CONSOLIDATION_HOURS:
            self.logw(f"Консолидация короче {CONSOLIDATION_HOURS}h: {consolidation_duration_hours:.1f}h")
            return False

        delta = bars[-1].timestamp - bars[period2_start_index].timestamp
        pump_duration_min = int(delta.total_seconds()) / 60

        if pump_duration_min < PUMP_MIN_MINUTES:
            self.logw(f"Период пампа слишком короткий: {pump_duration_min:.0f}m < {PUMP_MIN_MINUTES}m")
            return False

        # ---------- Проверка роста цены после старта пампа ----------
        pump_start_close = bars[period2_start_index].close
        pump_end_close = bars[-1].close
        pump_change_pct = (pump_end_close - pump_start_close) / pump_start_close * 100

        if pump_change_pct < MIN_PUMP_PCT:
            self.logw(f"Рост цены недостаточный: {pump_change_pct:.1f}% < {MIN_PUMP_PCT}%")
            return False

        if pump_end_close <= high_p1:
            self.logw("Цена после старта не закрепилась выше high периода 1.")
            return False

        # ---------- Объемы ----------
        avg_vol_p1 = sum(b.volume for b in self.consolidation_bars) / len(self.consolidation_bars)
        avg_vol_p2 = sum(b.volume for b in self.pump_bars) / len(self.pump_bars)

        if avg_vol_p2 < avg_vol_p1 * VOLUME_RATIO_MIN:
            self.logw(f"Объём пампа недостаточный: {avg_vol_p2:.0f} < {avg_vol_p1 * VOLUME_RATIO_MIN:.0f}")
            return False

        atr_values = [abs(b.high - b.low) for b in self.pump_bars]
        avg_atr = sum(atr_values) / len(atr_values)

        big_body_count = 0
        for b in self.pump_bars:
            body = abs(b.close - b.open)
            if body > avg_atr * BIG_BODY_ATR_MULTIPLIER:
                big_body_count += 1

        big_body_share = big_body_count / len(self.pump_bars)

        if big_body_share > MAX_BIG_BODY_SHARE:
            self.logw(f"Рост слишком резкий: доля больших тел {big_body_share:.2f} > {MAX_BIG_BODY_SHARE}")
            return False

        self.log(
            f"Памп подтверждён: рост {pump_change_pct:.2f}%, объём в {avg_vol_p2 / avg_vol_p1:.2f}x")
        return True

    @staticmethod
    def _calculate_ema_series(bars: List[Bar]):
        """
        bars: List[Bar]
        periods: list of ints
        Возвращает список EMA объектов, каждый соответствует бару.
        """
        periods = [20, 50, 100, 200]
        closes = [b.close for b in bars]
        ema_values = {p: [] for p in periods}
        k_values = {p: 2 / (p + 1) for p in periods}

        # Инициализируем EMA начальным SMA
        for p in periods:
            if len(closes) >= p:
                sma = sum(closes[:p]) / p
                ema_values[p].append(sma)
            else:
                ema_values[p].append(closes[0])

        # Рассчитываем EMA для каждого бара
        for i in range(1, len(closes)):
            for p in periods:
                prev_ema = ema_values[p][-1]
                k = k_values[p]
                ema = closes[i] * k + prev_ema * (1 - k)
                ema_values[p].append(ema)

        # Формируем список EMA объектов
        ema_list = []
        for i in range(len(bars)):
            ema_obj = EMA(
                ema20=ema_values[20][i] if 20 in periods and i < len(ema_values[20]) else 0.0,
                ema50=ema_values[50][i] if 50 in periods and i < len(ema_values[50]) else 0.0,
                ema100=ema_values[100][i] if 100 in periods and i < len(ema_values[100]) else 0.0,
                ema200=ema_values[200][i] if 200 in periods and i < len(ema_values[200]) else 0.0,
            )
            ema_list.append(ema_obj)

        return ema_list

    @staticmethod
    def _calculate_atr_series(bars, period=14):
        """
        bars: List[Bar]
        period: int
        Возвращает список ATR, каждый элемент соответствует бару.
        """
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

    @staticmethod
    def _find_period1_end_index(bars, ema_series, atr_series):
        """
        bars: List[Bar]
        ema_series: List[EMA]
        atr_series: List[float]
        Возвращает индекс конца period1 (старт period2), либо None.
        """
        for i in range(50, len(bars)):
            ema = ema_series[i]
            atr = atr_series[i]

            if ema.ema20 > ema.ema50 > ema.ema100 > ema.ema200:
                if (ema.ema20 - ema.ema50) > atr and \
                        (ema.ema50 - ema.ema100) > atr and \
                        (ema.ema100 - ema.ema200) > atr:
                    return i  # Конец period1, старт period2
        return None

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

    def _check_correction_structure(self, bars: List[Bar], swings: List[SwingPoint]) -> bool:
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

        ema_series = self._calculate_ema_series(bars)

        # Проверка закрытия баров
        for s in swings:
            bar = bars[s.index]
            if bar.close < ema_series[s.index].ema100:
                self.logw(f"Закрытие ниже EMA на баре {s.index}.")
                return False

        self.log("Структура коррекции подтверждена: есть LH и LL, нет закрытия ниже EMA.")
        return True

    def _get_correction_bars(self, bars: List[Bar]) -> List[Bar]:
        """
        Возвращает бары коррекции — все бары после главного high.
        """
        if self.main_high.is_undefined:
            self.logw("main_high не задан, не можем выделить correction bars.")
            return []

        correction_bars = bars[self.main_high.index + 1:]  # Все после main_high
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

    def _check_trendline_breakout_volume(self, bars_before_breakout: List[Bar], bars_after_breakout: List[Bar]) -> bool:
        """
        Проверка объёма пробоя.
        - Средний объём в коррекции должен быть меньше среднего объёма на пробое.
        """
        avg_vol_before = sum(b.volume for b in bars_before_breakout) / len(bars_before_breakout)
        avg_vol_after = sum(b.volume for b in bars_after_breakout) / len(bars_after_breakout)

        if avg_vol_after <= avg_vol_before:
            self.logw(f"Объём пробоя недостаточный: {avg_vol_after:.2f} ≤ {avg_vol_before:.2f}")
            return False

        self.log(f"Объём пробоя подтверждён: breakout {avg_vol_after:.2f} > correction {avg_vol_before:.2f}")
        return True

    def define_rr(self):
        entry = self.last.close
        sl_candidates = [s.price for s in reversed(self.swings) if s.type.is_low and s.price < entry]

        if not sl_candidates:
            self.logw("Нет swing low для SL.")
            return FLOAT_UNDEFINED, FLOAT_UNDEFINED, FLOAT_UNDEFINED, FLOAT_UNDEFINED

        sl = sl_candidates[0]
        tp = self.main_high.price
        if tp is None or tp <= entry:
            self.logw("Нет подходящего TP.")
            return FLOAT_UNDEFINED, FLOAT_UNDEFINED, FLOAT_UNDEFINED, FLOAT_UNDEFINED

        sl_distance_pct = abs(entry - sl) / entry * 100
        tp_distance_pct = abs(tp - entry) / entry * 100

        if sl_distance_pct < MIN_SL_PCT:
            self.logw(f"SL слишком близко: {sl_distance_pct:.2f}% < {MIN_SL_PCT:.2f}%")
            return FLOAT_UNDEFINED, FLOAT_UNDEFINED, FLOAT_UNDEFINED, FLOAT_UNDEFINED

        if tp_distance_pct < MIN_TP_PCT:
            self.logw(f"TP слишком близко: {tp_distance_pct:.2f}% < {MIN_TP_PCT:.2f}%")
            return FLOAT_UNDEFINED, FLOAT_UNDEFINED, FLOAT_UNDEFINED, FLOAT_UNDEFINED

        rr = abs(tp - entry) / abs(entry - sl)
        if rr < MIN_RR:
            self.logw(f"RR {rr:.2f} меньше минимального {MIN_RR}.")
            return FLOAT_UNDEFINED, FLOAT_UNDEFINED, FLOAT_UNDEFINED, FLOAT_UNDEFINED

        self.log(
            f"Entry: {entry:.5f}, "
            f"SL: {sl:.5f}, "
            f"TP: {tp:.5f}, "
            f"SL%: {sl_distance_pct:.2f}, "
            f"TP%: {tp_distance_pct:.2f}, "
            f"RR: {rr:.2f}"
        )
        return entry, sl, tp, rr
