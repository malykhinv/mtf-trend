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
    MAX_RANGE_PCT,
    PUMP_MIN_MINUTES,
    MIN_PUMP_PCT,
    VOLUME_RATIO_MIN,
)
from utils.float_utils import is_defined
from utils.logger import log
from utils.math_utils import calculate_atr
from utils.plot import Plot


class PumpSetup(Setup):
    def __init__(self, symbol: str, bars_by_tf: Dict[Timeframe, List[Bar]], tfs: MTFProfile):
        super().__init__(symbol, bars_by_tf, tfs)
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

        self.confidence = Confidence.STRONG
        return True

    def has_moderate_conditions(self) -> bool:
        if not self.has_weak_conditions():
            return False
        self.correction_bars = self._get_correction_bars()
        self.swings = swings = self.structure_detector.detect_swing_points(self.correction_bars)

        if not self._check_correction_structure(swings):
            return False

        if not self._check_correction_depth():
            return False

        self.confidence = Confidence.MODERATE
        return True

    def has_weak_conditions(self) -> bool:
        if not self._check_consolidation():
            return False

        # Найти главный хай после пампа
        main_high_index, main_high_bar = max(enumerate(self.bars_setup), key=lambda item: item[1].high)

        self.main_high = SwingPoint(
            price=main_high_bar.high,
            index=main_high_index,
            type=SwingType.HIGH,
        )

        if not self._check_pump_growth():
            return False

        if not self._check_volume():
            return False

        if not self._check_pump_duration():
            return False

        self.confidence = Confidence.WEAK
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
            return False

        period2_start_index = period1_end_index + 1

        self.consolidation_bars = bars[:period1_end_index]
        self.pump_bars = bars[period2_start_index:]
        self.setup_timestamp = self.pump_bars[0].timestamp

        if not self.consolidation_bars or not self.pump_bars:
            self._capture_pump("Недостаточно данных после разделения на периоды.")
            return False

        high_p1 = max(b.high for b in self.consolidation_bars)
        low_p1 = min(b.low for b in self.consolidation_bars)

        if low_p1 <= 0:
            self._capture_pump("Неверный low в консолидации (<= 0).")
            return False

        self.high_p1 = high_p1
        self.mid_p1 = (high_p1 + low_p1) / 2

        range_p1_pct = abs(high_p1 - low_p1) / low_p1 * 100
        if range_p1_pct > MAX_RANGE_PCT:
            self._capture_pump(f"Диапазон консолидации слишком большой: {range_p1_pct:.1f}% > {MAX_RANGE_PCT}%")
            return False

        return True

    def _check_pump_duration(self) -> bool:
        bars = self.bars_setup
        pump_start_index = len(self.consolidation_bars)
        pump_duration_min = int((bars[-1].timestamp - bars[pump_start_index].timestamp).total_seconds() / 60)
        if pump_duration_min < PUMP_MIN_MINUTES:
            self._capture_pump(f"Период пампа слишком короткий: {pump_duration_min:.0f}m < {PUMP_MIN_MINUTES}m")
            return False
        return True

    def _check_pump_growth(self) -> bool:
        if not self.main_high or self.main_high.is_undefined:
            self._capture_pump("main_high не определён для оценки роста.")
            return False

        ema_series_price = self._calculate_ema_series_from_values([b.close for b in self.bars_setup])
        main_high_index = self.main_high.index

        if main_high_index >= len(ema_series_price):
            self._capture_pump("main_high index вне диапазона EMA.")
            return False

        ema_obj = ema_series_price[main_high_index]

        if is_defined(ema_obj.ema200) and ema_obj.ema200 > 0:
            ema_base = ema_obj.ema200
        elif is_defined(ema_obj.ema100) and ema_obj.ema100 > 0:
            ema_base = ema_obj.ema100
        else:
            self._capture_pump("EMA100 и EMA200 невалидны.")
            return False

        self.pump_growth = pump_growth = self.main_high.price - ema_base
        pump_growth_pct = pump_growth / ema_base * 100

        if pump_growth_pct < MIN_PUMP_PCT:
            self._capture_pump(f"Рост цены от EMA недостаточный: {pump_growth_pct:.1f}% < {MIN_PUMP_PCT}%")
            return False

        log(f"Памп подтверждён: рост {pump_growth_pct:.1f}% от EMA")
        return True

    def _check_volume(self) -> bool:
        avg_vol_p1 = sum(b.volume for b in self.consolidation_bars) / len(self.consolidation_bars)
        avg_vol_p2 = sum(b.volume for b in self.pump_bars) / len(self.pump_bars)

        if avg_vol_p2 < avg_vol_p1 * VOLUME_RATIO_MIN:
            self._capture_pump(f"Объём пампа недостаточный: {avg_vol_p2:.0f} < {avg_vol_p1 * VOLUME_RATIO_MIN:.0f}")
            return False

        log(f"Объём пампа подтверждён: в {avg_vol_p2 / avg_vol_p1:.1f}x")
        return True

    def _check_correction_depth(self) -> bool:
        correction_low = min(bar.low for bar in self.correction_bars)
        correction_depth = abs(self.main_high.price - correction_low) / self.pump_growth * 100

        if correction_depth > MAX_CORRECTION_PCT:
            self._capture_pump(f"Глубина коррекции слишком большая: {correction_depth:.2f}% > {MAX_CORRECTION_PCT}%")
            return False

        log(f"Глубина коррекции подтверждена: {correction_depth:.2f}% ≤ {MAX_CORRECTION_PCT}%")
        return True

    def _check_correction_structure(self, swings: List[SwingPoint]) -> bool:
        """
        Проверка структуры коррекции.
        - Есть нисходящий тренд с LH и LL.
        - Нет закрытия ниже EMA.
        """
        if not swings or len(swings) < 5:
            self._capture_pump("Недостаточно swing-поинтов для анализа коррекции.")
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
            self._capture_pump(f"Недостаточно LH/LL: LH={lh_count}, LL={ll_count}")
            return False

        log("Структура коррекции подтверждена: есть LH и LL.")
        return True

    def _get_correction_bars(self) -> List[Bar]:
        """
        Возвращает бары коррекции — все бары после главного high.
        """
        if self.main_high.is_undefined:
            self._capture_pump("main_high не задан, не можем выделить correction bars.")
            return []

        correction_bars = self.bars_setup[self.main_high.index + 1:]
        if not correction_bars:
            self._capture_pump("После main_high нет баров для коррекции.")
        return correction_bars

    def _check_trendline_validity(self, trendline: Trendline) -> bool:
        if not trendline or not trendline.valid:
            self._capture_pump("Наклонка невалидна.")
            return False
        return True

    def _check_trendline_touches(self, trendline: Trendline, bars: List[Bar]) -> bool:
        correction_atr = sum(abs(b.high - b.low) for b in bars) / len(bars)
        touches = self.trendline_builder.count_touches(trendline, bars, correction_atr)
        if touches < 2:
            self._capture_pump(f"Недостаточно касаний наклонки: {touches} < 2.")
            return False
        log(f"Подтверждено касаний наклонки: {touches}.")
        return True

    def _check_trendline_breakout(self, trendline: Trendline, bars: List[Bar]) -> bool:
        correction_atr = sum(abs(b.high - b.low) for b in bars) / len(bars)

        last_two_indices = [len(bars) - 2, len(bars) - 1]
        for idx in last_two_indices:
            if not self.trendline_builder.has_breakout(trendline, bars, idx, correction_atr):
                self._capture_pump(f"Бар {idx} не закрепился выше наклонки.")
                return False

            # Заполняем вспомогательные списки
            self.bars_before_breakout = bars[:idx]
            self.bars_after_breakout = bars[idx + 1:]

        log("Пробой и закрепление выше наклонки подтверждены последними двумя свечами.")
        return True

    def _check_rr(self) -> bool:
        entry = self.last.close
        sl_candidates = [s.price for s in reversed(self.swings) if s.type.is_low and s.price < entry]

        if not sl_candidates:
            self._capture_pump("Нет swing low для SL.")
            return False

        sl = sl_candidates[0]
        tp = self.main_high.price
        if tp is None or tp <= entry:
            self._capture_pump("Нет подходящего TP.")
            return False

        sl_distance_pct = abs(entry - sl) / entry * 100
        tp_distance_pct = abs(tp - entry) / entry * 100

        if sl_distance_pct < MIN_SL_PCT:
            self._capture_pump(f"SL слишком близко: {sl_distance_pct:.2f}% < {MIN_SL_PCT}%")
            return False

        if tp_distance_pct < MIN_TP_PCT:
            self._capture_pump(f"TP слишком близко: {tp_distance_pct:.2f}% < {MIN_TP_PCT}%")
            return False

        rr = abs(tp - entry) / abs(entry - sl)
        if rr < MIN_RR:
            self._capture_pump(f"RR {rr:.2f} меньше минимального {MIN_RR}.")
            return False

        log(f"RR подтверждён: "
                 f"Entry={entry:.5f}, "
                 f"SL={sl:.5f}, "
                 f"TP={tp:.5f}, "
                 f"SL%={sl_distance_pct:.2f}, "
                 f"TP%={tp_distance_pct:.2f}, "
                 f"RR={rr:.2f}")
        return True

    def _find_pump_start_index(self,
                               bars: List[Bar],
                               ema_series_price: List[EMA],
                               ema_series_vol: List[EMA],
                               ema_series_oi: List[Optional[EMA]],
                               atr_series: List[float]) -> Optional[int]:
        atr_mean = FLOAT_UNDEFINED
        index_candidate = FLOAT_UNDEFINED
        for i in range(50, len(bars)):
            ema_p = ema_series_price[i]
            ema_v = ema_series_vol[i]
            ema_o = ema_series_oi[i] if ema_series_oi[i] else None
            atr_mean = mean([atr_series[i], atr_mean]) if is_defined(atr_mean) else atr_series[i]
            price = bars[i].close

            price_ok = self._check_ema_structure(ema_p, atr_mean) or self._check_ema_structure(ema_p)
            oi_ok = True if ema_o is None else self._check_ema_structure(ema_o)
            vol_ok = self._check_ema_structure(ema_v)

            factors_count = sum([price_ok, vol_ok, oi_ok])
            if factors_count < 2 and is_defined(index_candidate):
                index_candidate = FLOAT_UNDEFINED
            if factors_count >= 2 and not is_defined(index_candidate):
                index_candidate = i
            price_above = price > ema_p.ema20 and price > ema_p.ema50 and price > ema_p.ema100 and price > ema_p.ema200

            if price_ok and vol_ok and oi_ok and price_above:
                for j in range(index_candidate, 0, -1):
                    bar_j = bars[j]
                    ema_j = ema_series_price[j]

                    price_below_ema = (
                            bar_j.low < ema_j.ema20 or
                            bar_j.low < ema_j.ema50 or
                            bar_j.low < ema_j.ema100 or
                            bar_j.low < ema_j.ema200
                    )
                    ema_crossed = not (ema_j.ema20 > ema_j.ema100)

                    if price_below_ema or ema_crossed:
                        log(f"✨ {bars[j + 1].timestamp.strftime('%d.%m %H:%M')} ⬅ Сдвиг")
                        return j + 1

                index_candidate = index_candidate if is_defined(index_candidate) else i
                log(f"✨ {bars[index_candidate].timestamp.strftime('%d.%m %H:%M')}")
                return index_candidate

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
            spacing_20_50 = (ema_obj.ema20 - ema_obj.ema50) / ema_obj.ema50 if is_defined(ema_obj.ema50) else 0
            spacing_50_100 = (ema_obj.ema50 - ema_obj.ema100) / ema_obj.ema100 if is_defined(ema_obj.ema100) else 0
            spacing_100_200 = (ema_obj.ema100 - ema_obj.ema200) / ema_obj.ema200 if is_defined(ema_obj.ema200) else 0
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

    def _capture_pump(self, message: Optional[str]):
        logw(message)
        self._plot(message)

    def _plot(self, message: Optional[str]):
        plot = Plot(symbol=self.symbol,
                    bars=self.bars_setup,
                    tf=self.tfs.setup,
                    message=message,
                    save_dir=".generated/plot/charts_skipped")
        filename = f"{self.symbol}_{self.tfs.setup.value}.png"
        plot.generate_and_save(
            filename=filename,
            pump_start_time=self.pump_bars[0].timestamp,
            trendline=self.trendline,
        )
