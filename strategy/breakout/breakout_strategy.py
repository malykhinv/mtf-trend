"""Simple breakout strategy implementation backed by stateful position simulation."""

from __future__ import annotations

from dataclasses import dataclass
import logging

import pandas as pd

from constants import (
    STRATEGY_DEFAULT_OPEN_INTEREST,
    STRATEGY_MIN_LOOKBACK,
    STRATEGY_MIN_LOOKBACK_BUFFER,
    STRATEGY_MIN_RR,
    STRATEGY_MIN_TP2_MULT,
    STRATEGY_MIN_VOLUME_MULT,
    STRATEGY_POSITION_SIZE,
    STRATEGY_REQUIRED_COLUMNS,
    STRATEGY_RISK_FLOOR,
)
from domain.enums.position_side import PositionSide
from domain.enums.timeframe import Timeframe
from domain.models.candle import Candle
from domain.models.trade_result import TradeResult
from domain.models.trade_signal import TradeSignal
from domain.value_objects.price import Price
from domain.value_objects.volume import Volume
from simulation.order_processor import OrderProcessor
from simulation.position_simulator import StatefulPositionSimulator
from simulation.trade_classifier import TradeClassifier
from strategy.base_strategy import BaseStrategy
from strategy.breakout.config import BreakoutParams
from utils.formatters import datetime_to_timezone, utc_ms_to_local_datetime
from vectorbt_runner.mtf_frames import SymbolMtfFrames


@dataclass(slots=True)
class PendingBreakout:
    breakout_idx: int
    level: float
    breakout_extreme: float
    side: PositionSide
    level_start_time: pd.Timestamp


@dataclass(slots=True)
class PendingRetest:
    breakout: PendingBreakout
    retest_idx: int
    retest_low: float
    retest_high: float
    confirmation_end_idx: int


class BreakoutStrategy(BaseStrategy[BreakoutParams]):
    """Breakout/retest strategy with continuation confirmation and volume regime checks."""

    REQUIRED_COLUMNS = STRATEGY_REQUIRED_COLUMNS
    _logger = logging.getLogger(__name__)

    def __init__(self, *, commission_rate: float, slippage: float, strategy_timezone: str, simulation_timezone: str) -> None:
        self._commission_rate = commission_rate
        self._slippage = slippage
        self._strategy_timezone = strategy_timezone
        self._simulation_timezone = simulation_timezone

    def validate_config(self, params: BreakoutParams) -> None:
        if params.lookback < STRATEGY_MIN_LOOKBACK:
            raise ValueError(f"lookback must be >= {STRATEGY_MIN_LOOKBACK}")
        if params.volume_mult <= STRATEGY_MIN_VOLUME_MULT:
            raise ValueError("volume_mult must be > 0")
        if params.min_rr <= STRATEGY_MIN_RR:
            raise ValueError("min_rr must be > 0")
        if params.tp2_mult <= STRATEGY_MIN_TP2_MULT:
            raise ValueError(f"tp2_mult must be > {STRATEGY_MIN_TP2_MULT}")
        if params.confirmation_bars < 1:
            raise ValueError("confirmation_bars must be >= 1")

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        missing = [col for col in self.REQUIRED_COLUMNS if col not in data.columns]
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        prepared = data.copy()
        prepared["datetime"] = pd.to_numeric(prepared["timestamp"], errors="coerce").map(
            lambda value: utc_ms_to_local_datetime(value, self._strategy_timezone) if pd.notna(value) else pd.NaT
        )
        prepared = prepared.dropna(subset=["datetime"])
        prepared = prepared.sort_values("datetime").reset_index(drop=True)

        for col in ("open", "high", "low", "close", "volume"):
            prepared[col] = pd.to_numeric(prepared[col], errors="coerce")
        prepared = prepared.dropna(subset=["open", "high", "low", "close", "volume"])
        return prepared

    def generate_events(self, data: pd.DataFrame, params: BreakoutParams) -> list[TradeResult]:
        """Backward-compatible wrapper for single-timeframe callers."""
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
    ) -> list[TradeResult]:
        """Generate trades from higher-TF levels and lower-TF breakout/retest logic."""
        self.validate_config(params)
        higher_prepared = self.prepare_data(mtf_frames.get_frame(params.levels_timeframe))
        lower_prepared = self.prepare_data(mtf_frames.get_frame(params.entry_timeframe))
        self._logger.info(
            "breakout_generate_events symbol=%s levels_tf=%s entry_tf=%s",
            params.symbol,
            params.levels_timeframe.value,
            params.entry_timeframe.value,
        )
        if len(higher_prepared) < params.lookback + STRATEGY_MIN_LOOKBACK_BUFFER:
            return []
        if len(lower_prepared) < STRATEGY_MIN_LOOKBACK_BUFFER:
            return []

        higher_levels = higher_prepared[["datetime", "high", "low"]].copy()
        higher_levels["level_high"] = higher_levels["high"].rolling(window=params.lookback).max().shift(1)
        higher_levels["level_low"] = higher_levels["low"].rolling(window=params.lookback).min().shift(1)
        higher_levels["level_start_time"] = higher_levels["datetime"]
        higher_levels = higher_levels.dropna(subset=["level_high", "level_low"]).sort_values("datetime")
        if higher_levels.empty:
            return []

        lower_prepared = lower_prepared.sort_values("datetime").reset_index(drop=True)
        annotated = pd.merge_asof(
            lower_prepared,
            higher_levels[["datetime", "level_high", "level_low", "level_start_time"]],
            on="datetime",
            direction="backward",
        )
        annotated = annotated.dropna(subset=["level_high", "level_low", "level_start_time"]).reset_index(drop=True)
        if annotated.empty:
            return []

        annotated = self._append_natr(annotated=annotated, atr_window=params.lookback)
        if annotated.empty:
            return []

        trades: list[TradeResult] = []
        pending_signal: TradeSignal | None = None
        pending_breakout: PendingBreakout | None = None
        pending_retest: PendingRetest | None = None
        active_sim: StatefulPositionSimulator | None = None
        retest_window_candles = max(1, self._hours_to_candles(params.retest_window_hours, params.entry_timeframe))

        for idx in range(len(annotated)):
            row = annotated.iloc[idx]
            candle = self._to_candle(row)

            if pending_signal is not None and (active_sim is None or active_sim.position is None):
                active_sim = StatefulPositionSimulator(
                    side=pending_signal.position_side,
                    order_processor=OrderProcessor(commission_rate=self._commission_rate, slippage=self._slippage),
                    trade_classifier=TradeClassifier(),
                    simulation_timezone=self._simulation_timezone,
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
                if idx > pending_retest.confirmation_end_idx:
                    pending_retest = None
                    continue
                if self._is_confirmation(row=row, retest=pending_retest):
                    entry_idx = idx + 1
                    if entry_idx >= len(annotated):
                        pending_retest = None
                        continue
                    entry_row = annotated.iloc[entry_idx]
                    entry_price = float(entry_row["open"])
                    stop = self._resolve_stop_loss(
                        params=params,
                        side=pending_retest.breakout.side,
                        level=pending_retest.breakout.level,
                        breakout_extreme=pending_retest.breakout.breakout_extreme,
                        retest_low=pending_retest.retest_low,
                        retest_high=pending_retest.retest_high,
                    )
                    risk = self._risk_from_entry(entry_price=entry_price, stop=stop, side=pending_retest.breakout.side)
                    tp1, tp2 = self._targets_from_entry(
                        entry_price=entry_price,
                        risk=risk,
                        min_rr=params.min_rr,
                        tp2_mult=params.tp2_mult,
                        side=pending_retest.breakout.side,
                    )
                    pending_signal = TradeSignal(
                        entry_price=Price(entry_price),
                        entry_time=datetime_to_timezone(
                            entry_row["datetime"].to_pydatetime(),
                            self._simulation_timezone,
                        ),
                        stop_loss=Price(float(stop)),
                        take_profit_1=Price(float(tp1)),
                        take_profit_2=Price(float(tp2)),
                        position_side=pending_retest.breakout.side,
                        symbol=params.symbol,
                    )
                    pending_retest = None
                    continue
                if idx == pending_retest.confirmation_end_idx:
                    pending_retest = None
                continue

            if pending_breakout is not None:
                breakout_idx = pending_breakout.breakout_idx
                if idx - breakout_idx > retest_window_candles:
                    pending_breakout = None
                elif self._is_retest_candle(row=row, breakout=pending_breakout, params=params):
                    if self._volume_regime_ok(
                        annotated=annotated,
                        breakout=pending_breakout,
                        breakout_idx=breakout_idx,
                        retest_idx=idx,
                        volume_mult=params.volume_mult,
                    ) and self._extra_retest_filters_ok(
                        row=row,
                        breakout=pending_breakout,
                        params=params,
                    ):
                        pending_retest = PendingRetest(
                            breakout=pending_breakout,
                            retest_idx=idx,
                            retest_low=float(row["low"]),
                            retest_high=float(row["high"]),
                            confirmation_end_idx=idx + max(1, int(params.confirmation_bars)),
                        )
                        pending_breakout = None
                        continue

            if pending_breakout is None:
                level_high = float(row["level_high"])
                level_low = float(row["level_low"])
                breakout_long = float(row["close"]) > level_high
                breakout_short = float(row["close"]) < level_low
                if breakout_long:
                    pending_breakout = PendingBreakout(
                        breakout_idx=idx,
                        level=level_high,
                        breakout_extreme=float(row["low"]),
                        side=PositionSide.LONG,
                        level_start_time=row["level_start_time"],
                    )
                elif breakout_short:
                    pending_breakout = PendingBreakout(
                        breakout_idx=idx,
                        level=level_low,
                        breakout_extreme=float(row["high"]),
                        side=PositionSide.SHORT,
                        level_start_time=row["level_start_time"],
                    )

        if pending_signal is not None:
            self._logger.info(
                "signal_not_executed_end_of_data symbol=%s levels_tf=%s entry_tf=%s entry_time=%s entry_price=%.8f",
                params.symbol,
                params.levels_timeframe.value,
                params.entry_timeframe.value,
                pending_signal.entry_time.isoformat(),
                pending_signal.entry_price.value,
            )

        if active_sim is not None and active_sim.position is not None:
            final_row = annotated.iloc[-1]
            final_time = datetime_to_timezone(final_row["datetime"].to_pydatetime(), self._simulation_timezone)
            trades.append(active_sim.close_position(price=float(final_row["close"]), exit_time=final_time))

        return trades

    # region Private

    def _to_candle(self, row: pd.Series) -> Candle:
        return Candle(
            timestamp=datetime_to_timezone(row["datetime"].to_pydatetime(), self._simulation_timezone),
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
        spread = max(high - low, 1e-12)
        return abs(float(row["close"]) - float(row["open"])) / spread

    def _is_retest_candle(self, *, row: pd.Series, breakout: PendingBreakout, params: BreakoutParams) -> bool:
        level = breakout.level
        upper = level * (1 + params.retest_zone)
        lower = level * (1 - params.retest_zone)
        touched = float(row["low"]) <= upper and float(row["high"]) >= lower
        if not touched or self._body_ratio(row) < params.min_body_ratio:
            return False
        if breakout.side == PositionSide.LONG:
            return float(row["close"]) > level
        return float(row["close"]) < level

    def _extra_retest_filters_ok(self, *, row: pd.Series, breakout: PendingBreakout, params: BreakoutParams) -> bool:
        natr = max(float(row.get("natr", 0.0)), 1e-12)
        if breakout.side == PositionSide.LONG:
            move = (float(row["close"]) - float(row["low"])) / max(float(row["close"]), 1e-12)
            depth = max(0.0, (breakout.level - float(row["low"])) / max(breakout.level, 1e-12))
        else:
            move = (float(row["high"]) - float(row["close"])) / max(float(row["close"]), 1e-12)
            depth = max(0.0, (float(row["high"]) - breakout.level) / max(breakout.level, 1e-12))
        min_move_threshold = params.min_move_atr * natr
        max_depth_threshold = params.max_retest_depth * natr
        return move >= min_move_threshold and depth <= max_depth_threshold

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

    @staticmethod
    def _volume_regime_ok(
        *,
        annotated: pd.DataFrame,
        breakout: PendingBreakout,
        breakout_idx: int,
        retest_idx: int,
        volume_mult: float,
    ) -> bool:
        before_slice = annotated.iloc[:breakout_idx]
        before_slice = before_slice[before_slice["datetime"] >= breakout.level_start_time]
        after_slice = annotated.iloc[breakout_idx + 1 : retest_idx]
        if before_slice.empty or after_slice.empty:
            return False
        v_before = float(before_slice["volume"].mean())
        v_after = float(after_slice["volume"].mean())
        return v_after >= v_before * volume_mult

    @staticmethod
    def _is_confirmation(*, row: pd.Series, retest: PendingRetest) -> bool:
        if retest.breakout.side == PositionSide.LONG:
            return float(row["close"]) > retest.retest_high
        return float(row["close"]) < retest.retest_low

    @staticmethod
    def _resolve_stop_loss(
        *,
        params: BreakoutParams,
        side: PositionSide,
        level: float,
        breakout_extreme: float,
        retest_low: float,
        retest_high: float,
    ) -> float:
        if params.sl_mode.value == "LEVEL":
            return level * (1 - params.retest_zone) if side == PositionSide.LONG else level * (1 + params.retest_zone)
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

    # endregion Private
