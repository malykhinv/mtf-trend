"""Простая реализация стратегии пробоя на базе симуляции с сохранением состояния позиции."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from constants import (
    STRATEGY_DEFAULT_OPEN_INTEREST,
    STRATEGY_MIN_LOOKBACK,
    STRATEGY_MIN_LOOKBACK_BUFFER,
    STRATEGY_NATR_EPSILON,
    STRATEGY_MIN_RR,
    STRATEGY_MIN_TP2_MULT,
    STRATEGY_MIN_VOLUME_MULT,
    STRATEGY_POSITION_SIZE,
    STRATEGY_REQUIRED_COLUMNS,
    STRATEGY_RISK_FLOOR, STRATEGY_PRICE_EPSILON,
)
from domain.enums.entry_trigger import EntryTrigger
from domain.enums.level_type import LevelType
from domain.enums.position_side import PositionSide
from domain.enums.timeframe import Timeframe
from domain.models.candle import Candle
from domain.models.level import Level
from domain.models.retest_plot_span import RetestPlotSpan
from domain.models.trade_result import TradeResult
from domain.models.trade_signal import TradeSignal
from domain.value_objects.price import Price
from domain.value_objects.volume import Volume
from simulation.order_processor import OrderProcessor
from simulation.position_simulator import StatefulPositionSimulator
from simulation.trade_classifier import TradeClassifier
from strategy.base_strategy import BaseStrategy
from strategy.breakout.config import BreakoutParams
from strategy.breakout.pending_breakout import PendingBreakout
from strategy.breakout.pending_retest import PendingRetest
from vectorbt_runner.mtf_frames import SymbolMtfFrames


class BreakoutStrategy(BaseStrategy[BreakoutParams]):
    """Стратегия пробоя/ретеста с подтверждением продолжения и проверкой режима объема."""

    REQUIRED_COLUMNS = STRATEGY_REQUIRED_COLUMNS
    ANNOTATED_COLUMNS = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "natr",
    ]
    def __init__(
        self,
        *,
        commission_rate: float,
        slippage: float,
        logger: logging.Logger | None = None,
    ) -> None:
        self._commission_rate = commission_rate
        self._slippage = slippage
        self._logger = logger or logging.getLogger(__name__)
        self._last_generation_diagnostics: dict[str, object] = {}

    def set_logger(self, logger: logging.Logger) -> None:
        self._logger = logger

    def consume_last_generation_diagnostics(self) -> dict[str, object]:
        """Возвращает диагностику последней генерации сигналов и очищает буфер."""
        diagnostics = self._last_generation_diagnostics.copy()
        self._last_generation_diagnostics = {}
        return diagnostics

    # region Приватные

    def _to_candle(self, row: pd.Series) -> Candle:
        return Candle(
            timestamp_ms=int(row["timestamp"]),
            open=Price(float(row["open"])),
            high=Price(float(row["high"])),
            low=Price(float(row["low"])),
            close=Price(float(row["close"])),
            volume=Volume(float(row["volume"])),
            open_interest=Volume(float(row.get("open_interest", STRATEGY_DEFAULT_OPEN_INTEREST))),
        )

    @staticmethod
    def _hours_to_candles(hours: int, timeframe: Timeframe) -> int:
        timeframe_minutes = {
            Timeframe.M1: 1,
            Timeframe.M5: 5,
            Timeframe.M15: 15,
            Timeframe.M30: 30,
            Timeframe.H1: 60,
            Timeframe.H4: 240,
            Timeframe.D1: 1440,
            Timeframe.W1: 10080,
        }
        candle_minutes = timeframe_minutes[timeframe]
        return max(1, int((hours * 60) / candle_minutes))

    @staticmethod
    def _body_ratio(row: pd.Series) -> float:
        high = float(row["high"])
        low = float(row["low"])
        spread = max(high - low, STRATEGY_PRICE_EPSILON)
        return abs(float(row["close"]) - float(row["open"])) / spread

    def _is_retest_candle(self, *, row: pd.Series, breakout: PendingBreakout, params: BreakoutParams) -> bool:
        level_price = breakout.level.price.value
        natr = max(float(row.get("natr", 0.0)), 0.0)
        zone_ratio = params.resolve_retest_zone_ratio(natr)
        zone_top = level_price * (1 + zone_ratio)
        zone_bottom = level_price * (1 - zone_ratio)
        touched = float(row["low"]) <= zone_top and float(row["high"]) >= zone_bottom
        if not touched or self._body_ratio(row) < params.min_body_ratio:
            return False
        if breakout.side == PositionSide.LONG:
            return float(row["close"]) > float(row["open"]) and float(row["close"]) > zone_bottom
        return float(row["close"]) < float(row["open"]) and float(row["close"]) < zone_top

    @staticmethod
    def _extra_retest_filters_ok(*, row: pd.Series, breakout: PendingBreakout, params: BreakoutParams) -> bool:
        metrics = BreakoutStrategy._extra_retest_filter_metrics(row=row, breakout=breakout, params=params)
        return bool(metrics["is_ok"])

    @staticmethod
    def _extra_retest_filter_metrics(
        *,
        row: pd.Series,
        breakout: PendingBreakout,
        params: BreakoutParams,
    ) -> dict[str, float | bool]:
        natr = max(float(row.get("natr", 0.0)), STRATEGY_NATR_EPSILON)
        if breakout.side == PositionSide.LONG:
            move = (float(row["close"]) - float(row["low"])) / max(float(row["close"]), STRATEGY_PRICE_EPSILON)
            level_price = breakout.level.price.value
            depth = max(0.0, (level_price - float(row["low"])) / max(level_price, STRATEGY_PRICE_EPSILON))
        else:
            move = (float(row["high"]) - float(row["close"])) / max(float(row["close"]), STRATEGY_PRICE_EPSILON)
            level_price = breakout.level.price.value
            depth = max(0.0, (float(row["high"]) - level_price) / max(level_price, STRATEGY_PRICE_EPSILON))
        min_move_threshold = params.min_move_atr * natr
        max_depth_threshold = params.max_retest_depth * natr
        return {
            "is_ok": move >= min_move_threshold and depth <= max_depth_threshold,
            "body_ratio": BreakoutStrategy._body_ratio(row),
            "body_ratio_min": params.min_body_ratio,
            "move": move,
            "move_threshold": min_move_threshold,
            "max_retest_depth": depth,
            "max_retest_depth_threshold": max_depth_threshold,
            "natr": natr,
        }


    def _build_level(
        self,
        *,
        price: float,
        side: PositionSide,
        level_start_time: int,
        lookback: int,
        volume_before: float | None,
        volume_after: float | None = None,
    ) -> Level:
        return Level(
            price=Price(price),
            level_type=LevelType.RESISTANCE if side == PositionSide.LONG else LevelType.SUPPORT,
            formation_timestamp_ms=level_start_time,
            lookback=lookback,
            shadow_ratio=0.0,
            volume_before=volume_before,
            volume_after=volume_after,
        )

    @staticmethod
    def _mean_volume_between_indices(
        *,
        prefix_volume_sum: np.ndarray,
        start_idx: int,
        end_idx: int,
    ) -> float | None:
        if end_idx <= start_idx:
            return None
        interval_sum = float(prefix_volume_sum[end_idx] - prefix_volume_sum[start_idx])
        return interval_sum / float(end_idx - start_idx)

    @staticmethod
    def _average_volume_before(
        *,
        timestamps: np.ndarray,
        prefix_volume_sum: np.ndarray,
        breakout_idx: int,
        level_start_time: int,
    ) -> float | None:
        breakout_timestamp = int(timestamps[breakout_idx])
        level_start_idx = int(np.searchsorted(timestamps, level_start_time, side="left"))
        breakout_timestamp_idx = int(np.searchsorted(timestamps, breakout_timestamp, side="left"))
        return BreakoutStrategy._mean_volume_between_indices(
            prefix_volume_sum=prefix_volume_sum,
            start_idx=level_start_idx,
            end_idx=breakout_timestamp_idx,
        )

    @staticmethod
    def _append_natr(*, annotated: pd.DataFrame, atr_window: int) -> pd.DataFrame:
        frame = annotated.copy()
        prev_close = frame["close"].shift(1)
        true_range = pd.concat(
            [
                frame["high"] - frame["low"],
                (frame["high"] - prev_close).abs(),
                (frame["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        frame["atr"] = true_range.rolling(window=max(2, int(atr_window)), min_periods=max(2, int(atr_window))).mean()
        frame["natr"] = frame["atr"] / frame["close"].replace(0, pd.NA)
        frame = frame.dropna(subset=["natr"]).reset_index(drop=True)
        return frame

    def _evaluate_volume_regime(
        self,
        *,
        timestamps: np.ndarray,
        prefix_volume_sum: np.ndarray,
        breakout: PendingBreakout,
        breakout_idx: int,
        retest_idx: int,
        volume_mult: float,
    ) -> dict[str, float | bool]:
        breakout_timestamp = int(timestamps[breakout_idx])
        retest_timestamp = int(timestamps[retest_idx])

        level_start_idx = int(np.searchsorted(timestamps, breakout.level_start_time, side="left"))
        breakout_timestamp_idx = int(np.searchsorted(timestamps, breakout_timestamp, side="left"))
        retest_timestamp_idx = int(np.searchsorted(timestamps, retest_timestamp, side="left"))

        v_before_computed = self._mean_volume_between_indices(
            prefix_volume_sum=prefix_volume_sum,
            start_idx=level_start_idx,
            end_idx=breakout_timestamp_idx,
        )
        v_after = self._mean_volume_between_indices(
            prefix_volume_sum=prefix_volume_sum,
            start_idx=breakout_timestamp_idx,
            end_idx=retest_timestamp_idx,
        )
        if v_before_computed is None or v_after is None:
            self._logger.debug(
                "режим_объема_отклонен_пустое_окно время_формирования=%s время_пробоя=%s время_ретеста=%s множитель_объема=%.4f",
                breakout.level_start_time,
                breakout_timestamp,
                retest_timestamp,
                volume_mult,
            )
            return {
                "v_before": 0.0,
                "v_after": 0.0,
                "threshold": 0.0,
                "is_ok": False,
            }

        v_before = breakout.level.volume_before
        if v_before is None:
            v_before = v_before_computed
        threshold = v_before * volume_mult
        is_ok = v_after >= threshold

        updated_level = self._build_level(
            price=breakout.level.price.value,
            side=breakout.side,
            level_start_time=breakout.level_start_time,
            lookback=breakout.level.lookback,
            volume_before=v_before,
            volume_after=v_after,
        )
        breakout.level = updated_level

        if not is_ok:
            self._logger.debug(
                "режим_объема_отклонен объем_до=%.6f объем_после=%.6f множитель_объема=%.4f порог=%.6f",
                v_before,
                v_after,
                volume_mult,
                threshold,
            )
        return {
            "v_before": float(v_before),
            "v_after": v_after,
            "threshold": threshold,
            "is_ok": is_ok,
        }

    @staticmethod
    def _is_confirmation(*, row: pd.Series, retest: PendingRetest) -> bool:
        if retest.breakout.side == PositionSide.LONG:
            return float(row["close"]) > retest.retest_high
        return float(row["close"]) < retest.retest_low

    def _build_signal_from_retest(
        self,
        *,
        annotated: pd.DataFrame,
        entry_idx: int,
        pending_retest: PendingRetest,
        params: BreakoutParams,
    ) -> TradeSignal | None:
        if entry_idx >= len(annotated):
            return None
        entry_row = annotated.iloc[entry_idx]
        entry_price = float(entry_row["open"])
        stop = self._resolve_stop_loss(
            params=params,
            side=pending_retest.breakout.side,
            level=pending_retest.breakout.level.price.value,
            breakout_extreme=pending_retest.breakout.breakout_extreme,
            retest_low=pending_retest.retest_low,
            retest_high=pending_retest.retest_high,
            zone_ratio=pending_retest.retest_zone_ratio,
        )
        risk = self._risk_from_entry(entry_price=entry_price, stop=stop, side=pending_retest.breakout.side)
        tp1, tp2 = self._targets_from_entry(
            entry_price=entry_price,
            risk=risk,
            min_rr=params.min_rr,
            tp2_mult=params.tp2_mult,
            side=pending_retest.breakout.side,
        )
        side = pending_retest.breakout.side
        breakout_idx = pending_retest.breakout.breakout_idx
        retest_idx = pending_retest.retest_idx

        def _log_invalid_signal(reason: str) -> None:
            self._logger.info(
                "signal skipped: %s symbol=%s side=%s sl_mode=%s entry=%.8f stop=%.8f tp1=%.8f tp2=%.8f breakout_idx=%s retest_idx=%s",
                reason,
                params.symbol,
                side.value,
                params.sl_mode.value,
                entry_price,
                stop,
                tp1,
                tp2,
                breakout_idx,
                retest_idx,
            )

        if side == PositionSide.SHORT:
            if tp1 < 0:
                _log_invalid_signal(reason="negative tp1")
                return None
            if tp2 < 0:
                tp2 = tp1
        if side == PositionSide.LONG and not (stop < entry_price < tp1):
            _log_invalid_signal(reason="invalid LONG levels invariant")
            return None
        if side == PositionSide.SHORT and not (stop > entry_price > tp1):
            _log_invalid_signal(reason="invalid SHORT levels invariant")
            return None
        return TradeSignal(
            entry_price=Price(entry_price),
            entry_timestamp_ms=int(entry_row["timestamp"]),
            stop_loss=Price(float(stop)),
            take_profit_1=Price(float(tp1)),
            take_profit_2=Price(float(tp2)),
            position_side=side,
            symbol=params.symbol,
        )

    @staticmethod
    def _resolve_stop_loss(
        *,
        params: BreakoutParams,
        side: PositionSide,
        level: float,
        breakout_extreme: float,
        retest_low: float,
        retest_high: float,
        natr: float | None = None,
        zone_ratio: float | None = None,
    ) -> float:
        """Рассчитывает SL; для режима LEVEL использует ту же зону, что и ретест.

        Приоритет зоны: явный ``zone_ratio`` -> ``resolve_retest_zone_ratio(natr)`` ->
        фиксированный ``retest_zone`` (когда ``retest_zone_atr is None``).
        """
        if params.sl_mode.value == "LEVEL":
            resolved_zone_ratio = zone_ratio
            if resolved_zone_ratio is None:
                if natr is not None:
                    resolved_zone_ratio = params.resolve_retest_zone_ratio(natr)
                elif params.retest_zone_atr is None:
                    resolved_zone_ratio = max(params.retest_zone, 0.0)
                else:
                    resolved_zone_ratio = params.resolve_retest_zone_ratio(0.0)
            return level * (1 - resolved_zone_ratio) if side == PositionSide.LONG else level * (1 + resolved_zone_ratio)
        if params.sl_mode.value == "BREAKOUT_EXTREME":
            return breakout_extreme
        return retest_low if side == PositionSide.LONG else retest_high

    @staticmethod
    def _risk_from_entry(*, entry_price: float, stop: float, side: PositionSide) -> float:
        if side == PositionSide.LONG:
            return max(entry_price - stop, entry_price * STRATEGY_RISK_FLOOR)
        return max(stop - entry_price, entry_price * STRATEGY_RISK_FLOOR)

    @staticmethod
    def _targets_from_entry(*, entry_price: float, risk: float, min_rr: float, tp2_mult: float, side: PositionSide) -> tuple[float, float]:
        if side == PositionSide.LONG:
            tp1 = entry_price + risk * min_rr
            tp2 = entry_price + risk * min_rr * tp2_mult
            return tp1, tp2
        tp1 = entry_price - risk * min_rr
        tp2 = entry_price - risk * min_rr * tp2_mult
        return tp1, tp2

    # endregion Приватные

    def validate_config(self, params: BreakoutParams) -> None:
        """Проверяет корректность параметров стратегии."""
        if params.lookback < STRATEGY_MIN_LOOKBACK:
            raise ValueError(f"параметр lookback должен быть >= {STRATEGY_MIN_LOOKBACK}")
        if params.volume_mult <= STRATEGY_MIN_VOLUME_MULT:
            raise ValueError("параметр volume_mult должен быть > 0")
        if params.min_rr <= STRATEGY_MIN_RR:
            raise ValueError("параметр min_rr должен быть > 0")
        if params.tp2_mult <= STRATEGY_MIN_TP2_MULT:
            raise ValueError(f"параметр tp2_mult должен быть > {STRATEGY_MIN_TP2_MULT}")
        if params.confirmation_bars < 1:
            raise ValueError("параметр confirmation_bars должен быть >= 1")

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """Подготавливает входные данные перед расчётом сигналов."""
        missing = [col for col in self.REQUIRED_COLUMNS if col not in data.columns]
        if missing:
            raise ValueError(f"Отсутствуют обязательные колонки: {missing}")

        prepared = data.copy()
        prepared["timestamp"] = pd.to_numeric(prepared["timestamp"], errors="coerce")
        prepared = prepared.dropna(subset=["timestamp"])
        prepared["timestamp"] = prepared["timestamp"].astype("int64")
        prepared = prepared.sort_values("timestamp").reset_index(drop=True)

        for col in ("open", "high", "low", "close", "volume"):
            prepared[col] = pd.to_numeric(prepared[col], errors="coerce")
        prepared = prepared.dropna(subset=["open", "high", "low", "close", "volume"])
        return prepared

    def prepare_multi_tf_data(
        self,
        *,
        mtf_frames: SymbolMtfFrames,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Готовит базовые фреймы старшего/младшего ТФ для дальнейшего аннотирования."""
        higher_prepared = self.prepare_data(mtf_frames.get_frame(levels_timeframe))
        lower_prepared = self.prepare_data(mtf_frames.get_frame(entry_timeframe))
        return (
            higher_prepared[["timestamp", "high", "low"]].copy(),
            lower_prepared[["timestamp", "open", "high", "low", "close", "volume"]].copy(),
        )

    def prepare_annotated_multi_tf_data(
        self,
        *,
        lower_base: pd.DataFrame,
        lookback: int,
    ) -> pd.DataFrame:
        """Строит аннотированный фрейм младшего ТФ с NATR, готовый к проходу сигналов."""
        if len(lower_base) < STRATEGY_MIN_LOOKBACK_BUFFER:
            return pd.DataFrame(columns=self.ANNOTATED_COLUMNS)

        annotated = self._append_natr(
            annotated=lower_base.sort_values("timestamp").reset_index(drop=True),
            atr_window=lookback,
        )
        if annotated.empty:
            return pd.DataFrame(columns=self.ANNOTATED_COLUMNS)

        return annotated[self.ANNOTATED_COLUMNS].copy()

    @staticmethod
    def prepare_higher_tf_levels(*, higher_base: pd.DataFrame, lookback: int) -> pd.DataFrame:
        """Готовит уровни старшего ТФ без маппинга на младший ТФ."""
        if len(higher_base) < lookback + STRATEGY_MIN_LOOKBACK_BUFFER:
            return pd.DataFrame(columns=["timestamp", "level_high", "level_low", "level_start_time"])

        higher_levels = higher_base.copy()
        higher_levels["level_high"] = higher_levels["high"].rolling(window=lookback).max().shift(1)
        higher_levels["level_low"] = higher_levels["low"].rolling(window=lookback).min().shift(1)
        higher_levels["level_start_time"] = higher_levels["timestamp"]
        higher_levels = higher_levels.dropna(subset=["level_high", "level_low"]).sort_values("timestamp")
        return higher_levels[["timestamp", "level_high", "level_low", "level_start_time"]].reset_index(drop=True)

    def generate_events(self, data: pd.DataFrame, params: BreakoutParams) -> list[TradeResult]:
        """Обратносовместимая обертка для вызовов с одним таймфреймом."""
        return self.generate_events_multi_tf(
            mtf_frames=SymbolMtfFrames(
                levels_timeframe=Timeframe.D1,
                entry_timeframe=Timeframe.M15,
                levels_frame=data,
                entry_frame=data,
            ),
            params=params,
        )

    def generate_events_multi_tf(
        self,
        *,
        mtf_frames: SymbolMtfFrames,
        params: BreakoutParams,
        annotated: pd.DataFrame | None = None,
        higher_levels: pd.DataFrame | None = None,
        skip_validation: bool = False,
    ) -> list[TradeResult]:
        """Генерирует сделки по уровням старшего ТФ и логике пробоя/ретеста младшего ТФ."""
        if not skip_validation:
            self.validate_config(params)
        if annotated is None:
            higher_base, lower_base = self.prepare_multi_tf_data(
                mtf_frames=mtf_frames,
                levels_timeframe=params.levels_timeframe,
                entry_timeframe=params.entry_timeframe,
            )
            annotated = self.prepare_annotated_multi_tf_data(
                lower_base=lower_base,
                lookback=params.lookback,
            )
        if higher_levels is None:
            higher_base, _ = self.prepare_multi_tf_data(
                mtf_frames=mtf_frames,
                levels_timeframe=params.levels_timeframe,
                entry_timeframe=params.entry_timeframe,
            )
            higher_levels = self.prepare_higher_tf_levels(higher_base=higher_base, lookback=params.lookback)
        diagnostics: dict[str, object] = {
            "annotated_rows": int(len(annotated)),
            "breakouts_found": 0,
            "retests_found": 0,
            "retest_rejected_by_volume": 0,
            "retest_rejected_by_extra_filters": 0,
            "breakout_retest_window_expired": 0,
            "retest_confirmation_expired": 0,
            "retest_confirmation_not_received": 0,
            "signal_not_filled_end_of_data": 0,
            "breakout_pending_end_of_data": 0,
            "retest_pending_end_of_data": 0,
            "trades_generated": 0,
            "retest_plot_spans": [],
        }
        diagnostics["context"] = {
            "symbol": params.symbol,
            "lookback": params.lookback,
            "volume_mult": params.volume_mult,
            "retest_window_hours": params.retest_window_hours,
            "entry_trigger": params.entry_trigger.value,
            "confirmation_bars": params.confirmation_bars,
        }
        if annotated.empty:
            self._last_generation_diagnostics = diagnostics
            return []
        if higher_levels.empty:
            self._last_generation_diagnostics = diagnostics
            return []

        annotated_rows = list(annotated[self.ANNOTATED_COLUMNS].itertuples(index=False, name=None))
        higher_level_columns = ["timestamp", "level_high", "level_low", "level_start_time"]
        higher_level_rows = list(higher_levels[higher_level_columns].itertuples(index=False, name=None))
        if (
            len(annotated_rows) != len(annotated)
            or len(higher_level_rows) != len(higher_levels)
            or (annotated_rows and len(annotated_rows[0]) != len(self.ANNOTATED_COLUMNS))
            or (higher_level_rows and len(higher_level_rows[0]) != len(higher_level_columns))
        ):
            raise ValueError("Неконсистентные входные данные для генерации событий")

        timestamps_by_idx = [int(row[0]) for row in annotated_rows]
        timestamps = annotated["timestamp"].to_numpy(dtype="int64", copy=False)
        volumes = annotated["volume"].to_numpy(dtype="float64", copy=False)
        prefix_volume_sum = np.empty(len(volumes) + 1, dtype="float64")
        prefix_volume_sum[0] = 0.0
        np.cumsum(volumes, out=prefix_volume_sum[1:])

        trades: list[TradeResult] = []
        retest_plot_spans: list[RetestPlotSpan] = []
        pending_signal: TradeSignal | None = None
        pending_breakout: PendingBreakout | None = None
        pending_retest: PendingRetest | None = None
        active_sim: StatefulPositionSimulator | None = None
        retest_window_candles = max(1, self._hours_to_candles(params.retest_window_hours, params.entry_timeframe))
        level_idx = 0
        active_level_high: float | None = None
        active_level_low: float | None = None
        active_level_start_time: int | None = None

        for idx, row_values in enumerate(annotated_rows):
            row_timestamp = int(row_values[0])
            row_open = float(row_values[1])
            row_high = float(row_values[2])
            row_low = float(row_values[3])
            row_close = float(row_values[4])
            row_volume = float(row_values[5])
            row_natr = float(row_values[6])
            row = {
                "timestamp": row_timestamp,
                "open": row_open,
                "high": row_high,
                "low": row_low,
                "close": row_close,
                "volume": row_volume,
                "natr": row_natr,
            }
            candle = Candle(
                timestamp_ms=row_timestamp,
                open=Price(row_open),
                high=Price(row_high),
                low=Price(row_low),
                close=Price(row_close),
                volume=Volume(row_volume),
                open_interest=Volume(float(STRATEGY_DEFAULT_OPEN_INTEREST)),
            )

            while level_idx < len(higher_level_rows) and int(higher_level_rows[level_idx][0]) <= row_timestamp:
                level_row = higher_level_rows[level_idx]
                active_level_high = float(level_row[1])
                active_level_low = float(level_row[2])
                active_level_start_time = int(level_row[3])
                level_idx += 1

            if pending_signal is not None and (active_sim is None or active_sim.position is None):
                active_sim = StatefulPositionSimulator(
                    side=pending_signal.position_side,
                    order_processor=OrderProcessor(commission_rate=self._commission_rate, slippage=self._slippage),
                    trade_classifier=TradeClassifier(),
                )
                active_sim.register_signal(pending_signal, size=STRATEGY_POSITION_SIZE)
                pending_signal = None

            if active_sim is not None:
                result = active_sim.process_candle(candle)
                if result is not None:
                    trades.append(result)

            active_position = active_sim is not None and active_sim.position is not None
            if active_position or pending_signal is not None:
                continue

            if pending_retest is not None:
                if params.entry_trigger == EntryTrigger.IMMEDIATE:
                    entry_idx = pending_retest.retest_idx + 1
                    pending_retest.retest_end_idx = pending_retest.retest_idx
                    retest_plot_spans.append(
                        RetestPlotSpan(
                            symbol=params.symbol,
                            side=pending_retest.breakout.side,
                            level_price=pending_retest.breakout.level.price.value,
                            retest_low=pending_retest.retest_low,
                            retest_high=pending_retest.retest_high,
                            retest_start_timestamp_ms=timestamps_by_idx[pending_retest.retest_start_idx],
                            retest_end_timestamp_ms=timestamps_by_idx[pending_retest.retest_end_idx],
                            status="confirmed",
                        )
                    )
                    self._logger.debug(
                        "signal built from retest symbol=%s datetime=%s side=%s entry_trigger=%s entry_idx=%s retest_idx=%s breakout_idx=%s",
                        params.symbol,
                        row["timestamp"],
                        pending_retest.breakout.side.value,
                        params.entry_trigger.value,
                        entry_idx,
                        pending_retest.retest_idx,
                        pending_retest.breakout.breakout_idx,
                    )
                    pending_signal = self._build_signal_from_retest(
                        annotated=annotated,
                        entry_idx=entry_idx,
                        pending_retest=pending_retest,
                        params=params,
                    )
                    pending_retest = None
                    continue

                if idx > pending_retest.confirmation_end_idx:
                    diagnostics["retest_confirmation_expired"] += 1
                    pending_retest.retest_end_idx = pending_retest.confirmation_end_idx
                    retest_plot_spans.append(
                        RetestPlotSpan(
                            symbol=params.symbol,
                            side=pending_retest.breakout.side,
                            level_price=pending_retest.breakout.level.price.value,
                            retest_low=pending_retest.retest_low,
                            retest_high=pending_retest.retest_high,
                            retest_start_timestamp_ms=timestamps_by_idx[pending_retest.retest_start_idx],
                            retest_end_timestamp_ms=timestamps_by_idx[pending_retest.retest_end_idx],
                            status="confirmation_expired",
                        )
                    )
                    self._logger.debug(
                        "retest_confirmation_expired symbol=%s confirmation_end_idx=%s idx=%s candle_time=%s",
                        params.symbol,
                        pending_retest.confirmation_end_idx,
                        idx,
                        row["timestamp"],
                    )
                    pending_retest = None
                    continue
                if self._is_confirmation(row=row, retest=pending_retest):
                    entry_idx = idx + 1
                    pending_retest.retest_end_idx = idx
                    retest_plot_spans.append(
                        RetestPlotSpan(
                            symbol=params.symbol,
                            side=pending_retest.breakout.side,
                            level_price=pending_retest.breakout.level.price.value,
                            retest_low=pending_retest.retest_low,
                            retest_high=pending_retest.retest_high,
                            retest_start_timestamp_ms=timestamps_by_idx[pending_retest.retest_start_idx],
                            retest_end_timestamp_ms=timestamps_by_idx[pending_retest.retest_end_idx],
                            status="confirmed",
                        )
                    )
                    self._logger.debug(
                        "signal built from retest symbol=%s datetime=%s side=%s entry_trigger=%s entry_idx=%s retest_idx=%s breakout_idx=%s",
                        params.symbol,
                        row["timestamp"],
                        pending_retest.breakout.side.value,
                        params.entry_trigger.value,
                        entry_idx,
                        pending_retest.retest_idx,
                        pending_retest.breakout.breakout_idx,
                    )
                    pending_signal = self._build_signal_from_retest(
                        annotated=annotated,
                        entry_idx=entry_idx,
                        pending_retest=pending_retest,
                        params=params,
                    )
                    pending_retest = None
                    continue
                if idx == pending_retest.confirmation_end_idx:
                    diagnostics["retest_confirmation_not_received"] += 1
                    pending_retest.retest_end_idx = idx
                    retest_plot_spans.append(
                        RetestPlotSpan(
                            symbol=params.symbol,
                            side=pending_retest.breakout.side,
                            level_price=pending_retest.breakout.level.price.value,
                            retest_low=pending_retest.retest_low,
                            retest_high=pending_retest.retest_high,
                            retest_start_timestamp_ms=timestamps_by_idx[pending_retest.retest_start_idx],
                            retest_end_timestamp_ms=timestamps_by_idx[pending_retest.retest_end_idx],
                            status="confirmation_not_received",
                        )
                    )
                    self._logger.debug(
                        "retest_confirmation_not_received symbol=%s confirmation_end_idx=%s idx=%s candle_time=%s",
                        params.symbol,
                        pending_retest.confirmation_end_idx,
                        idx,
                        row["timestamp"],
                    )
                    pending_retest = None
                continue

            if pending_breakout is not None:
                breakout_idx = pending_breakout.breakout_idx
                if idx - breakout_idx > retest_window_candles:
                    diagnostics["breakout_retest_window_expired"] += 1
                    pending_breakout = None
                elif self._is_retest_candle(row=row, breakout=pending_breakout, params=params):
                    self._logger.debug(
                        "retest_candle_detected symbol=%s datetime=%s side=%s level=%.8f breakout_idx=%s retest_idx=%s",
                        params.symbol,
                        row["timestamp"],
                        pending_breakout.side.value,
                        pending_breakout.level.price.value,
                        breakout_idx,
                        idx,
                    )
                    diagnostics["retests_found"] += 1
                    volume_check = self._evaluate_volume_regime(
                        timestamps=timestamps,
                        prefix_volume_sum=prefix_volume_sum,
                        breakout=pending_breakout,
                        breakout_idx=breakout_idx,
                        retest_idx=idx,
                        volume_mult=params.volume_mult,
                    )
                    extra_filters = self._extra_retest_filter_metrics(
                        row=row,
                        breakout=pending_breakout,
                        params=params,
                    )
                    if volume_check["is_ok"] and extra_filters["is_ok"]:
                        pending_retest = PendingRetest(
                            breakout=pending_breakout,
                            retest_idx=idx,
                            retest_start_idx=idx,
                            retest_end_idx=None,
                            retest_low=float(row["low"]),
                            retest_high=float(row["high"]),
                            retest_zone_ratio=params.resolve_retest_zone_ratio(max(float(row.get("natr", 0.0)), 0.0)),
                            confirmation_end_idx=idx + max(1, int(params.confirmation_bars)),
                            volume_before=volume_check["v_before"],
                            volume_after=volume_check["v_after"],
                            volume_threshold=volume_check["threshold"],
                            volume_filter_passed=volume_check["is_ok"],
                        )
                        self._logger.debug(
                            "retest accepted -> pending_retest created symbol=%s datetime=%s side=%s level=%.8f breakout_idx=%s retest_idx=%s entry_trigger=%s entry_idx=%s",
                            params.symbol,
                            row["timestamp"],
                            pending_breakout.side.value,
                            pending_breakout.level.price.value,
                            breakout_idx,
                            idx,
                            params.entry_trigger.value,
                            idx + 1 if params.entry_trigger == EntryTrigger.IMMEDIATE else idx + max(1, int(params.confirmation_bars)) + 1,
                        )
                        pending_breakout = None
                        continue
                    if not bool(volume_check["is_ok"]):
                        diagnostics["retest_rejected_by_volume"] += 1
                        retest_plot_spans.append(
                            RetestPlotSpan(
                                symbol=params.symbol,
                                side=pending_breakout.side,
                                level_price=pending_breakout.level.price.value,
                                retest_low=float(row["low"]),
                                retest_high=float(row["high"]),
                                retest_start_timestamp_ms=int(row["timestamp"]),
                                retest_end_timestamp_ms=int(row["timestamp"]),
                                status="rejected_by_volume",
                            )
                        )
                        self._logger.debug(
                            "retest_rejected_by_volume symbol=%s datetime=%s side=%s level=%.8f breakout_idx=%s retest_idx=%s v_before=%.6f v_after=%.6f threshold=%.6f volume_mult=%.4f volume_filter_passed=false",
                            params.symbol,
                            row["timestamp"],
                            pending_breakout.side.value,
                            pending_breakout.level.price.value,
                            breakout_idx,
                            idx,
                            float(volume_check["v_before"]),
                            float(volume_check["v_after"]),
                            float(volume_check["threshold"]),
                            params.volume_mult,
                        )
                    else:
                        diagnostics["retest_rejected_by_extra_filters"] += 1
                        retest_plot_spans.append(
                            RetestPlotSpan(
                                symbol=params.symbol,
                                side=pending_breakout.side,
                                level_price=pending_breakout.level.price.value,
                                retest_low=float(row["low"]),
                                retest_high=float(row["high"]),
                                retest_start_timestamp_ms=int(row["timestamp"]),
                                retest_end_timestamp_ms=int(row["timestamp"]),
                                status="rejected_by_extra_filters",
                            )
                        )
                        self._logger.debug(
                            "retest_rejected_by_extra_filters symbol=%s datetime=%s side=%s level=%.8f breakout_idx=%s retest_idx=%s body_ratio=%.6f body_ratio_min=%.6f move_atr=%.6f move_atr_threshold=%.6f max_retest_depth=%.6f max_retest_depth_threshold=%.6f natr=%.6f",
                            params.symbol,
                            row["timestamp"],
                            pending_breakout.side.value,
                            pending_breakout.level.price.value,
                            breakout_idx,
                            idx,
                            float(extra_filters["body_ratio"]),
                            float(extra_filters["body_ratio_min"]),
                            float(extra_filters["move"]),
                            float(extra_filters["move_threshold"]),
                            float(extra_filters["max_retest_depth"]),
                            float(extra_filters["max_retest_depth_threshold"]),
                            float(extra_filters["natr"]),
                        )

            if pending_breakout is None:
                if (
                    active_level_high is None
                    or active_level_low is None
                    or active_level_start_time is None
                ):
                    continue
                level_high = active_level_high
                level_low = active_level_low
                breakout_long = float(row["close"]) > level_high
                breakout_short = float(row["close"]) < level_low
                if breakout_long:
                    diagnostics["breakouts_found"] += 1
                    pending_breakout = PendingBreakout(
                        breakout_idx=idx,
                        level=self._build_level(
                            price=level_high,
                            side=PositionSide.LONG,
                            level_start_time=active_level_start_time,
                            lookback=params.lookback,
                            volume_before=self._average_volume_before(
                                timestamps=timestamps,
                                prefix_volume_sum=prefix_volume_sum,
                                breakout_idx=idx,
                                level_start_time=active_level_start_time,
                            ),
                        ),
                        breakout_extreme=float(row["low"]),
                        side=PositionSide.LONG,
                        level_start_time=active_level_start_time,
                    )
                elif breakout_short:
                    diagnostics["breakouts_found"] += 1
                    pending_breakout = PendingBreakout(
                        breakout_idx=idx,
                        level=self._build_level(
                            price=level_low,
                            side=PositionSide.SHORT,
                            level_start_time=active_level_start_time,
                            lookback=params.lookback,
                            volume_before=self._average_volume_before(
                                timestamps=timestamps,
                                prefix_volume_sum=prefix_volume_sum,
                                breakout_idx=idx,
                                level_start_time=active_level_start_time,
                            ),
                        ),
                        breakout_extreme=float(row["high"]),
                        side=PositionSide.SHORT,
                        level_start_time=active_level_start_time,
                    )

        if pending_signal is not None:
            diagnostics["signal_not_filled_end_of_data"] += 1
            self._logger.debug(
                "сигнал_не_исполнен_конец_данных символ=%s тф_уровней=%s тф_входа=%s время_входа=%s цена_входа=%.8f",
                params.symbol,
                params.levels_timeframe.value,
                params.entry_timeframe.value,
                str(pending_signal.entry_timestamp_ms),
                pending_signal.entry_price.value,
            )

        if pending_breakout is not None:
            diagnostics["breakout_pending_end_of_data"] += 1
        if pending_retest is not None:
            diagnostics["retest_pending_end_of_data"] += 1
            pending_retest.retest_end_idx = min(pending_retest.confirmation_end_idx, len(annotated) - 1)
            retest_plot_spans.append(
                RetestPlotSpan(
                    symbol=params.symbol,
                    side=pending_retest.breakout.side,
                    level_price=pending_retest.breakout.level.price.value,
                    retest_low=pending_retest.retest_low,
                    retest_high=pending_retest.retest_high,
                    retest_start_timestamp_ms=timestamps_by_idx[pending_retest.retest_start_idx],
                    retest_end_timestamp_ms=timestamps_by_idx[pending_retest.retest_end_idx],
                    status="pending_end_of_data",
                )
            )

        if active_sim is not None and active_sim.position is not None:
            final_row = annotated_rows[-1]
            final_timestamp_ms = int(final_row[0])
            trades.append(active_sim.close_position(price=float(final_row[4]), exit_timestamp_ms=final_timestamp_ms))

        diagnostics["trades_generated"] = len(trades)
        diagnostics["retest_plot_spans"] = retest_plot_spans
        self._last_generation_diagnostics = diagnostics
        return trades

    # region Приватные
