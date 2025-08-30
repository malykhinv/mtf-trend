# domain/detection/pump/pump_setup.py
from statistics import mean
from typing import List, Dict, Optional
from datetime import datetime

from millify import millify as mf

from domain.detection.setup import Setup
from domain.detection.trendline_builder import TrendlineBuilder
from domain.models.bar import Bar
from domain.models.confidence import Confidence
from domain.models.ema import EMA
from domain.models.mtf_profile import MTFProfile
from domain.models.swing_point import SwingPoint
from domain.models.swing_type import SwingType
from domain.models.timeframe import Timeframe
from domain.structures import StructureDetector
from config.constants import (
    FLOAT_UNDEFINED,
    MIN_RISK_REWARD,
    MIN_STOP_LOSS_PERCENT,
    MIN_TAKE_PROFIT_PERCENT,
    MAX_CORRECTION_PERCENT,
    MAX_RANGE_PERCENT_FOR_PUMP,
    PUMP_MIN_DURATION_MINUTES,
    MIN_PRICE_GROWTH_PERCENT,
    MIN_ATR_GROWTH_PERCENT,
    MIN_VOLUME_RATIO, MIN_VOLUME_GROWTH, ATR_PERIOD, PUMP_MAX_DURATION_MINUTES, BIG_BODY_ATR_MULTIPLIER,
    IS_CAPTURING_ENABLED,
    MAX_BIG_BODY_SHARE, TBQ_HOLD_BARS, TBQ_EMA_PERIOD, TBQ_THRUST, ANTI_SPIKE_ATR_MULT, IS_BACKTEST_MODE_ENABLED,
)
from utils.decorator import inject_method_name, log_duration_ms
from concurrent.futures import ThreadPoolExecutor
from utils.float_utils import is_defined
from utils.logger import log, logw
from utils.math_utils import calculate_atr
from utils.plot import Plot
import numpy as np

_capture_executor = ThreadPoolExecutor(max_workers=2)


class PumpSetup(Setup):
    """
    Детектор ситуаций типа Pump (скачка цены) по заданному символу, биржевым барам и профилю таймфреймов.
    Применяет разноплановые фильтры и проверки по структуре, объемам, ATR, OI, чтобы определить силу сигнала.
    В том числе отбрасывает пампы, начинающиеся с аномально больших свечей.
    """
    def __init__(self, symbol: str, bars_by_tf: Dict[Timeframe, List[Bar]], tfs: MTFProfile,
                 pump_start_time: Optional[datetime] = None) -> None:
        """Создаёт детектор пампов для указанного символа."""
        super().__init__(symbol, bars_by_tf, tfs)
        self.structure_detector: StructureDetector = StructureDetector()
        self.trendline_builder: TrendlineBuilder = TrendlineBuilder()
        self.last: Bar = self.bars_setup[-1]
        self.consolidation_bars: List[Bar] = []
        self.pump_bars: List[Bar] = []
        self.correction_bars: List[Bar] = []
        self.main_high: SwingPoint = SwingPoint.undefined()
        self.pump_start_time = pump_start_time

    @log_duration_ms
    def define_confidence(self) -> None:
        """Определяет силу сигнала (confidence) по степени выполнения условий фильтров."""
        if self.confidence:
            return
        if not self.has_weak_conditions():
            return
        if not self.has_moderate_conditions():
            return
        if not self.has_strong_conditions():
            return

    @log_duration_ms
    def has_strong_conditions(self) -> bool:
        """Проверяет выполнение фильтров сильного уровня (без свингов и наклонки)."""
        # 1) завершение коррекции через ре-акселерацию цены vs EMA (без трендлайна)
        if not self._check_takers_end_of_correction():
            return False
        if not self._check_rr():
            return False
        self.confidence = Confidence.STRONG
        self.log_setup()

        return True

    @log_duration_ms
    def has_moderate_conditions(self) -> bool:
        """Проверяет выполнение фильтров умеренной силы."""
        self._define_correction_bars()
        self._define_correction_atr()
        if not self._check_red_bars_size():
            return False
        if not self._check_correction_depth():
            return False
        self.confidence = Confidence.MODERATE
        self.log_setup()
        return True

    @log_duration_ms
    def has_weak_conditions(self) -> bool:
        """Проверяет выполнение слабых фильтров."""
        if not IS_BACKTEST_MODE_ENABLED and not self._check_oi():
            return False
        if not self._check_consolidation():
            return False
        self._define_main_high()
        if not self._check_price_growth():
            return False
        if not self._check_atr_growth():
            return False
        if not self._check_volume_growth():
            return False
        if not IS_BACKTEST_MODE_ENABLED and not self._check_pump_duration():
            return False
        if not self._check_pump_bars_size():
            return False
        self.confidence = Confidence.WEAK
        self.log_setup()
        return True

    # region Check
    @inject_method_name
    @log_duration_ms
    def _check_oi(self) -> bool:
        """Проверяет, что все значения OI присутствуют и больше нуля."""
        if all(not is_defined(bar.oi) for bar in self.bars_setup):
            logw("Неверный OI (<= 0).")
            return False
        return True

    @inject_method_name
    @log_duration_ms
    def _check_consolidation(self) -> bool:
        """Проверяет консолидацию и разделяет периоды движения."""
        bars = self.bars_setup
        closes = [b.close for b in bars]
        volumes = [b.volume for b in bars]
        oi_values = [b.oi for b in bars]
        ema_series_price = self._calculate_ema_series_from_values(closes)
        ema_series_vol = self._calculate_ema_series_from_values(volumes)
        has_oi_data = any(oi != FLOAT_UNDEFINED for oi in oi_values)
        ema_series_oi = self._calculate_ema_series_from_values(oi_values) if has_oi_data else [None] * len(bars)
        atr_series = self._calculate_atr_series(bars)
        if self.pump_start_time:
            pump_index = next((i for i, b in enumerate(bars) if b.timestamp == self.pump_start_time), None)
            if pump_index is None or pump_index < 1:
                return False
            period1_end_index = pump_index - 1
        else:
            period1_end_index = self._find_pump_start_index(
                bars,
                ema_series_price,
                ema_series_vol,
                ema_series_oi,
                atr_series
            )
            if period1_end_index is None or period1_end_index not in range(0, len(bars) - 1):
                return False
        period2_start_index = period1_end_index + 1
        self.consolidation_bars = bars[:period1_end_index]
        self.pump_bars = bars[period2_start_index:]
        self.setup_timestamp = self.pump_bars[0].timestamp
        if not self.consolidation_bars or not self.pump_bars:
            self._capture_pump("Недостаточно данных после разделения на периоды.", Confidence.WEAK, self._name)
            return False
        high_p1 = max(b.high for b in self.consolidation_bars)
        low_p1 = min(b.low for b in self.consolidation_bars)
        if low_p1 <= 0:
            self._capture_pump("Неверный low в консолидации (<= 0).", Confidence.WEAK, self._name)
            return False
        self.high_p1 = high_p1
        range_p1_pct = abs(high_p1 - low_p1) / low_p1 * 100
        if range_p1_pct > MAX_RANGE_PERCENT_FOR_PUMP:
            self._capture_pump(
                f"Диапазон консолидации слишком большой: {range_p1_pct:.1f}% > {MAX_RANGE_PERCENT_FOR_PUMP}%",
                Confidence.WEAK,
                self._name
            )
            return False
        return True

    @inject_method_name
    @log_duration_ms
    def _check_price_growth(self) -> bool:
        """Проверяет рост цены относительно EMA."""
        if not self.main_high or self.main_high.is_undefined:
            self._capture_pump("main_high не определён для оценки роста.", Confidence.WEAK, self._name)
            return False
        ema_series_price = self._calculate_ema_series_from_values([b.close for b in self.bars_setup])
        main_high_index = self.main_high.index
        if main_high_index >= len(ema_series_price):
            self._capture_pump("main_high index вне диапазона EMA.", Confidence.WEAK, self._name)
            return False
        ema_obj = ema_series_price[main_high_index]
        if is_defined(ema_obj.ema200) and ema_obj.ema200 > 0:
            ema_base = ema_obj.ema200
        elif is_defined(ema_obj.ema100) and ema_obj.ema100 > 0:
            ema_base = ema_obj.ema100
        else:
            self._capture_pump("EMA100 и EMA200 невалидны.", Confidence.WEAK, self._name)
            return False
        self.price_growth = pump_growth = self.main_high.price - ema_base
        self.price_growth_pct = price_growth_pct = pump_growth / ema_base * 100
        if price_growth_pct < MIN_PRICE_GROWTH_PERCENT:
            self._capture_pump(f"Рост цены от EMA недостаточный: {price_growth_pct:.1f}% < {MIN_PRICE_GROWTH_PERCENT}%",
                               Confidence.WEAK, self._name)
            return False
        log(f"{self.symbol} Рост цены подтверждён: {price_growth_pct:.1f}% от EMA")
        return True

    @inject_method_name
    @log_duration_ms
    def _check_atr_growth(self) -> bool:
        """Оценивает рост ATR между консолидацией и скачком."""
        if not self.consolidation_bars or not self.pump_bars:
            self._capture_pump("Недостаточно данных для оценки ATR.", Confidence.WEAK, self._name)
            return False

        atr_p1_series = calculate_atr(self.consolidation_bars, period=ATR_PERIOD)
        atr_p2_series = calculate_atr(self.pump_bars, period=ATR_PERIOD)

        if not atr_p1_series or not atr_p2_series:
            self._capture_pump("Ошибка при расчёте ATR.", Confidence.WEAK, self._name)
            return False

        atr_p1 = mean(atr_p1_series)
        atr_p2 = mean(atr_p2_series)

        if atr_p1 <= 0:
            self._capture_pump("ATR периода консолидации некорректен.", Confidence.WEAK, self._name)
            return False

        self.atr_growth_pct = atr_growth_pct = (atr_p2 - atr_p1) / atr_p1 * 100

        if atr_growth_pct < MIN_ATR_GROWTH_PERCENT:
            self._capture_pump(
                f"Рост ATR недостаточный: {atr_growth_pct:.2f}% < {MIN_ATR_GROWTH_PERCENT}%",
                Confidence.WEAK,
                self._name
            )
            return False

        log(f"{self.symbol} Рост ATR подтверждён: {atr_growth_pct:.2f}% ≥ {MIN_ATR_GROWTH_PERCENT}%")
        return True

    @inject_method_name
    @log_duration_ms
    def _check_volume_growth(self) -> bool:
        """Проверяет, что объём во время пампа значительно выше среднего."""
        avg_vol_p1 = sum(b.volume for b in self.consolidation_bars) / len(self.consolidation_bars)
        avg_vol_p2 = sum(b.volume for b in self.pump_bars) / len(self.pump_bars)
        timeframe_factor = self.tfs.setup.minutes
        min_volume_growth = MIN_VOLUME_GROWTH * timeframe_factor / mean(bar.close for bar in self.pump_bars)
        volume_threshold = max(avg_vol_p1 * MIN_VOLUME_RATIO, min_volume_growth)
        if avg_vol_p2 < volume_threshold:
            self._capture_pump(
                f"Объём пампа недостаточный: {mf(avg_vol_p2)} < {mf(volume_threshold)}",
                Confidence.WEAK,
                self._name
            )
            return False
        self.volume_growth_x = abs(avg_vol_p2 - avg_vol_p1) / avg_vol_p1
        log(f"{self.symbol} Объём пампа подтверждён: в {avg_vol_p2 / avg_vol_p1:.1f}x")
        return True

    @inject_method_name
    @log_duration_ms
    def _check_pump_duration(self) -> bool:
        """Проверяет, что длительность пампа находится в допустимых пределах."""
        bars = self.bars_setup
        pump_start_index = len(self.consolidation_bars)
        pump_duration_min = int((bars[-1].timestamp - bars[pump_start_index].timestamp).total_seconds() / 60)
        if pump_duration_min < PUMP_MIN_DURATION_MINUTES:
            self._capture_pump(
                f"Период пампа слишком короткий: {pump_duration_min}m < {PUMP_MIN_DURATION_MINUTES}m",
                Confidence.WEAK,
                self._name
            )
            return False
        if pump_duration_min > PUMP_MAX_DURATION_MINUTES:
            self._capture_pump(
                f"Период пампа слишком длинный: {pump_duration_min}m > {PUMP_MAX_DURATION_MINUTES}m",
                Confidence.WEAK,
                self._name
            )
            return False
        return True

    @inject_method_name
    @log_duration_ms
    def _check_pump_bars_size(self) -> bool:
        """Фильтрует пампы с чрезмерно большими свечами в начале движения."""
        pump_start_idx = len(self.consolidation_bars)
        if pump_start_idx >= len(self.bars_setup) or not self.pump_bars:
            return False

        amp = self.main_high.price - self.bars_setup[pump_start_idx].open
        if amp <= 0:
            return False

        relative_high_idx = self.main_high.index - pump_start_idx
        relative_high_idx = max(0, min(relative_high_idx, len(self.pump_bars) - 1))

        start_idx = max(0, pump_start_idx - 2)
        bars_to_check = self.bars_setup[start_idx:pump_start_idx] + self.pump_bars[0:relative_high_idx + 1]

        for bar in bars_to_check:
            if bar.high - bar.low > amp * MAX_BIG_BODY_SHARE:
                self._capture_pump("Большая свеча в начале пампа", Confidence.WEAK, self._name)
                return False

        return True

    @inject_method_name
    @log_duration_ms
    def _check_red_bars_size(self) -> bool:
        """Контролирует размер красных баров во время коррекции."""
        red_bars = [bar for bar in self.correction_bars if bar.close < bar.open]
        for bar in red_bars:
            if bar.high - bar.low > bar.atr * BIG_BODY_ATR_MULTIPLIER:
                self._capture_pump(
                    "Есть агрессивное движение в шорт.",
                    Confidence.MODERATE,
                    self._name
                )
                return False
        return True

    @inject_method_name
    @log_duration_ms
    def _check_correction_depth(self) -> bool:
        """Проверяет, что глубина коррекции не слишком велика."""
        correction_low = min(bar.low for bar in self.correction_bars)
        correction_depth = abs(self.main_high.price - correction_low) / self.main_high.price * 100
        if correction_depth > MAX_CORRECTION_PERCENT:
            self._capture_pump(
                f"Глубина коррекции слишком большая: {correction_depth:.2f}% > {MAX_CORRECTION_PERCENT}%",
                Confidence.MODERATE,
                self._name
            )
            return False
        log(f"{self.symbol} Глубина коррекции подтверждена: {correction_depth:.2f}% ≤ {MAX_CORRECTION_PERCENT}%")
        return True

    @inject_method_name
    @log_duration_ms
    def _check_takers_end_of_correction(self) -> bool:
        """
        TBQ-логика конца коррекции:
        1) последние TBQ_HOLD_BARS баров: TBQ ≥ TBQ_THRUST и EMA(I)>0, где I = 2*TBQ-1;
        2) до этого доминирования не было (средний TBQ в предыдущем окне ≤ 0.5);
        3) анти-шпилька: (High-Low) ≤ ANTI_SPIKE_ATR_MULT * ATR последнего бара.
        """

        bars = self.correction_bars
        if not bars or len(bars) < TBQ_HOLD_BARS * 2 + 1:
            self._capture_pump("Недостаточно баров коррекции для TBQ.", Confidence.STRONG, self._name)
            return False
        tbq = np.array([b.tbq for b in bars], dtype=float)
        if not np.isfinite(tbq).any():
            self._capture_pump("Нет TBQ данных на сетап ТФ.", Confidence.STRONG, self._name)
            return False
        # Индекс тейкеров I в [-1..+1] и его EMA
        I = 2.0 * tbq - 1.0
        k = 2.0 / (TBQ_EMA_PERIOD + 1.0)
        ema = np.empty_like(I)
        ema[0] = np.nanmean(I[:TBQ_EMA_PERIOD]) if len(I) >= TBQ_EMA_PERIOD else I[0]

        for i in range(1, len(I)):
            ema[i] = I[i] * k + ema[i - 1] * (1.0 - k)

        w = TBQ_HOLD_BARS
        last_ok = np.all(tbq[-w:] >= TBQ_THRUST) and np.all(ema[-w:] > 0.0)
        prev = tbq[-2 * w:-w] if len(tbq) >= 2 * w else tbq[:-w]
        prev_ok = prev.size > 0 and float(np.nanmean(prev)) <= 0.5

        last_bar = bars[-1]
        atr_last = last_bar.atr if is_defined(last_bar.atr) else 0.0
        anti_spike_ok = (last_bar.high - last_bar.low) <= ANTI_SPIKE_ATR_MULT * atr_last if atr_last > 0 else True

        if not last_ok:
            self._capture_pump("TBQ: нет удержания доминирования покупателей.", Confidence.STRONG, self._name)
            return False

        if not prev_ok:
            self._capture_pump("TBQ: не видно перехода продавцы→покупатели.", Confidence.STRONG, self._name)
            return False

        if not anti_spike_ok:
            self._capture_pump("TBQ: анти-шпилька — одиночный всплеск.", Confidence.STRONG, self._name)
            return False
        return True

    @inject_method_name
    @log_duration_ms
    def _check_rr(self) -> bool:
        """Оценивает RR на соответствие минимальным требованиям."""
        if not self.correction_bars:
            self._capture_pump("Нет correction bars для RR.", Confidence.STRONG, self._name)
            return False

        entry = self.last.close
        # SL — минимум последних N баров коррекции (локально-консервативно без свингов)
        N = 8
        window = self.correction_bars[-min(N, len(self.correction_bars)):]
        sl = min(b.low for b in window)

        tp = self.main_high.price
        if not is_defined(entry, sl, tp) or tp <= entry or sl >= entry:
            self._capture_pump("Некорректные уровни Entry/SL/TP.", Confidence.STRONG, self._name)
            return False

        sl_distance_pct = abs(entry - sl) / entry * 100
        tp_distance_pct = abs(tp - entry) / entry * 100
        if sl_distance_pct < MIN_STOP_LOSS_PERCENT:
            self._capture_pump(f"SL слишком близко: {sl_distance_pct:.2f}% < {MIN_STOP_LOSS_PERCENT}%", Confidence.STRONG, self._name)
            return False
        if tp_distance_pct < MIN_TAKE_PROFIT_PERCENT:
            self._capture_pump(f"TP слишком близко: {tp_distance_pct:.2f}% < {MIN_TAKE_PROFIT_PERCENT}%", Confidence.STRONG, self._name)
            return False
        rr = abs(tp - entry) / abs(entry - sl)
        if rr < MIN_RISK_REWARD:
            self._capture_pump(f"RR {rr:.2f} меньше минимального {MIN_RISK_REWARD}.", Confidence.STRONG, self._name)
            return False
        log(f"{self.symbol} RR подтверждён:\n"
            f"Entry={entry:.5f}\n"
            f"SL={sl:.5f}\n"
            f"TP={tp:.5f}\n"
            f"SL%={sl_distance_pct:.2f}\n"
            f"TP%={tp_distance_pct:.2f}\n"
            f"RR={rr:.2f}")
        return True

    # endregion

    # region Define
    @log_duration_ms
    def _define_main_high(self) -> None:
        """
        Определяет главный хай для пампа.
        """
        main_high_index, main_high_bar = max(enumerate(self.bars_setup), key=lambda item: item[1].high)
        self.main_high = SwingPoint(
            timestamp=main_high_bar.timestamp,
            price=main_high_bar.high,
            index=main_high_index,
            type=SwingType.HIGH,
        )

    @inject_method_name
    @log_duration_ms
    def _define_correction_bars(self) -> None:
        """
        Возвращает бары коррекции — все бары после главного high.
        """
        if self.main_high.is_undefined:
            self._capture_pump("main_high не задан, не можем выделить correction bars.", Confidence.MODERATE,
                               self._name)
            self.correction_bars = []
        correction_bars = self.bars_setup[self.main_high.index + 1:]
        if not correction_bars:
            self._capture_pump("После main_high нет баров для коррекции.", Confidence.MODERATE, self._name)
        self.correction_bars = correction_bars

    @log_duration_ms
    def _define_correction_atr(self) -> None:
        """
        Определяет ATR для коррекции.
        """
        self.correction_atrs = calculate_atr(self.correction_bars) if self.correction_bars else []

    # endregion

    # region Calculation
    @log_duration_ms
    def _find_pump_start_index(self,
                               bars: List[Bar],
                               ema_series_price: List[EMA],
                               ema_series_vol: List[EMA],
                               ema_series_oi: List[Optional[EMA]],
                               atr_series: List[float]) -> Optional[int]:
        """Возвращает индекс начала пампа, если удалось определить."""
        atr_mean = FLOAT_UNDEFINED
        index_candidate = FLOAT_UNDEFINED
        for i in range(50, len(bars)):
            ema_p = ema_series_price[i]
            ema_v = ema_series_vol[i]
            ema_oi = ema_series_oi[i] if ema_series_oi[i] else None
            atr_i = atr_series[i]
            price = bars[i].close
            atr_mean = mean([atr_i, atr_mean]) if is_defined(atr_mean) else atr_i
            price_ok = self._check_ema_structure(ema_p, atr_mean) or self._check_ema_structure(ema_p)
            vol_ok = self._check_ema_structure(ema_v)
            oi_ok = self._check_ema_structure(ema_oi) if ema_oi else False
            factors_count = sum([price_ok, vol_ok, oi_ok])
            if factors_count < 2 and is_defined(index_candidate):
                index_candidate = FLOAT_UNDEFINED
            if factors_count >= 2 and not is_defined(index_candidate):
                index_candidate = i
            price_above = price > ema_p.ema20 and price > ema_p.ema50 and price > ema_p.ema100 and price > ema_p.ema200
            if price_ok and vol_ok and oi_ok and price_above:
                for j in range(index_candidate - 1, 0, -1):
                    ema_j = ema_series_price[j]
                    vol_j = ema_series_vol[j]
                    oi_j = ema_series_oi[j] if ema_series_oi[j] else None
                    atr_j = atr_series[j]
                    price_ok_j = self._check_ema_structure(ema_j, atr_j) or self._check_ema_structure(ema_j)
                    vol_ok_j = self._check_ema_structure(vol_j)
                    oi_ok_j = self._check_ema_structure(oi_j) if oi_j else False
                    ema_spread = max(ema_j.ema20, ema_j.ema50, ema_j.ema100, ema_j.ema200) - \
                                 min(ema_j.ema20, ema_j.ema50, ema_j.ema100, ema_j.ema200)
                    compact_ema = ema_spread < atr_j
                    factors_j = sum([price_ok_j, vol_ok_j, oi_ok_j])
                    is_red_bar = bars[j].close < bars[j].open
                    price_below_ema = bars[j].close < min(ema_j.ema20, ema_j.ema50, ema_j.ema100, ema_j.ema200)
                    if (factors_j == 0 and compact_ema) or (factors_j <= 1 and price_below_ema and is_red_bar):
                        return j + 1
                return index_candidate
        return None

    @staticmethod
    @log_duration_ms
    def _check_ema_structure(ema_obj: EMA, atr_value: float = FLOAT_UNDEFINED) -> bool:
        """Проверяет выполнение структуры EMA на заданном баре."""
        order_ok = ema_obj.ema20 > ema_obj.ema50 > ema_obj.ema100 > ema_obj.ema200
        if is_defined(atr_value):
            spacing_20_50 = (ema_obj.ema20 - ema_obj.ema50)
            spacing_50_100 = (ema_obj.ema50 - ema_obj.ema100)
            spacing_100_200 = (ema_obj.ema100 - ema_obj.ema200)
            spacing_ok = spacing_20_50 > atr_value and spacing_50_100 > atr_value and spacing_100_200 > atr_value
        else:
            spacing_pct = 0.0025
            spacing_20_50 = (ema_obj.ema20 - ema_obj.ema50) / ema_obj.ema50 if is_defined(ema_obj.ema50) else 0
            spacing_50_100 = (ema_obj.ema50 - ema_obj.ema100) / ema_obj.ema100 if is_defined(ema_obj.ema100) else 0
            spacing_100_200 = (ema_obj.ema100 - ema_obj.ema200) / ema_obj.ema200 if is_defined(ema_obj.ema200) else 0
            spacing_ok = spacing_20_50 > spacing_pct and spacing_50_100 > spacing_pct and spacing_100_200 > spacing_pct
        return order_ok and spacing_ok

    @staticmethod
    @log_duration_ms
    def _calculate_ema_series_from_values(values: List[float]) -> List[EMA]:
        """Вычисляет EMA 20/50/100/200 по переданным значениям."""
        periods = np.array([20, 50, 100, 200])
        values_arr = np.asarray(values, dtype=float)
        if values_arr.size == 0:
            return []

        k = 2 / (periods + 1)
        ema_matrix = np.zeros((len(periods), len(values_arr)))

        start = [values_arr[:p].mean() if len(values_arr) >= p else values_arr[0] for p in periods]
        ema_matrix[:, 0] = np.array(start, dtype=float)

        for i in range(1, len(values_arr)):
            ema_matrix[:, i] = values_arr[i] * k + ema_matrix[:, i - 1] * (1 - k)

        ema_list = [
            EMA(
                ema20=float(ema_matrix[0, i]),
                ema50=float(ema_matrix[1, i]),
                ema100=float(ema_matrix[2, i]),
                ema200=float(ema_matrix[3, i]),
            )
            for i in range(len(values_arr))
        ]
        return ema_list

    @staticmethod
    @log_duration_ms
    def _calculate_atr_series(bars: List[Bar], period: int = ATR_PERIOD) -> List[float]:
        """Вычисляет значения ATR по всей выборке баров."""

        if not bars:
            return []

        highs = np.array([b.high for b in bars])
        lows = np.array([b.low for b in bars])
        closes = np.array([b.close for b in bars])

        prev_closes = np.concatenate(([closes[0]], closes[:-1]))
        tr = np.maximum.reduce([
            highs - lows,
            np.abs(highs - prev_closes),
            np.abs(lows - prev_closes)
        ])

        atr = np.zeros_like(tr)
        atr[0] = tr[:period].mean() if len(tr) >= period else tr[0]
        for i in range(1, len(tr)):
            if i < period:
                atr[i] = tr[:i + 1].mean()
            else:
                atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

        atr = np.insert(atr, 0, 0.0)
        return atr.tolist()

    # endregion

    @log_duration_ms
    def log_setup(self):
        print()
        log(f"{self.symbol} : {self.tfs} "
            f"✨ {self.confidence} {self.pump_bars[0].timestamp.strftime('%d.%m %H:%M')}")

    # region Plot
    @log_duration_ms
    def _capture_pump(self, message: Optional[str], confidence: Confidence, reason: str) -> None:
        """Создаёт скриншот и сохраняет информацию о пропущенном пампе."""
        logw(f"{self.symbol} {message}")
        if IS_CAPTURING_ENABLED and not confidence.is_weak:
            _capture_executor.submit(self._plot, message, confidence, reason)

    @log_duration_ms
    def _plot(self, message: Optional[str], confidence: Confidence, reason: str) -> None:
        """Строит и сохраняет изображение пампа."""
        plot = Plot(symbol=self.symbol,
                    bars=self.bars_setup,
                    tf=self.tfs.setup,
                    message=message,
                    save_dir="skipped")
        filename = f"{confidence.value.capitalize()}_{reason}_{self.tfs.setup.value}_{self.symbol}.png"
        plot.generate_and_save(
            filename=filename,
            pump_start_time=self.pump_bars[0].timestamp if self.pump_bars else None,
            trendline=self.trendline,
        )
    # endregion
