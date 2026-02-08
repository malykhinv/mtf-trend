"""Simple breakout strategy implementation backed by stateful position simulation."""

from __future__ import annotations

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


class BreakoutStrategy(BaseStrategy[BreakoutParams]):
    """Breakout/retest-lite LONG strategy with TP1/TP2 and BE support."""

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
        """Generate closed trades from historical candles.

        Signals are always created on a retest candle and queued for execution on the
        next candle open via ``pending_signal``. If data ends before that next candle
        appears, the strategy works in **strict mode**: it does not force an entry on
        the last candle and records the skipped signal with
        ``signal_not_executed_end_of_data`` in logs.
        """
        self.validate_config(params)
        prepared = self.prepare_data(data)
        if len(prepared) < params.lookback + STRATEGY_MIN_LOOKBACK_BUFFER:
            return []

        sim = StatefulPositionSimulator(
            side=PositionSide.LONG,
            order_processor=OrderProcessor(commission_rate=self._commission_rate, slippage=self._slippage),
            trade_classifier=TradeClassifier(),
            simulation_timezone=self._simulation_timezone,
        )

        trades: list[TradeResult] = []
        pending_signal: TradeSignal | None = None
        pending_breakout: dict[str, float | int] | None = None

        for idx in range(params.lookback, len(prepared)):
            row = prepared.iloc[idx]
            candle = self._to_candle(row)

            if sim.position is None and pending_signal is not None:
                sim.register_signal(pending_signal, size=STRATEGY_POSITION_SIZE)
                pending_signal = None

            result = sim.process_candle(candle)
            if result is not None:
                trades.append(result)

            if sim.position is None and pending_signal is None:
                if pending_breakout is not None:
                    breakout_idx = int(pending_breakout["breakout_idx"])
                    if idx - breakout_idx > params.retest_window:
                        pending_breakout = None
                    else:
                        level_high = float(pending_breakout["level_high"])
                        breakout_low = float(pending_breakout["breakout_low"])
                        breakout_close = float(pending_breakout["breakout_close"])

                        upper_retest_bound = level_high * (1 + params.retest_zone)
                        lower_retest_bound = level_high * (1 - params.retest_zone)
                        retest_hit = row["low"] <= upper_retest_bound and row["close"] >= lower_retest_bound

                        if retest_hit:
                            stop = self._resolve_stop_loss(
                                params=params,
                                level_high=level_high,
                                breakout_low=breakout_low,
                                retest_low=float(row["low"]),
                            )
                            risk = max(breakout_close - stop, breakout_close * STRATEGY_RISK_FLOOR)
                            tp1 = breakout_close + risk * params.min_rr
                            tp2 = breakout_close + risk * params.min_rr * params.tp2_mult
                            entry_idx = min(idx + 1, len(prepared) - 1)
                            entry_row = prepared.iloc[entry_idx]
                            pending_signal = TradeSignal(
                                entry_price=Price(breakout_close),
                                entry_time=datetime_to_timezone(
                                    entry_row["datetime"].to_pydatetime(),
                                    self._simulation_timezone,
                                ),
                                stop_loss=Price(float(stop)),
                                take_profit_1=Price(float(tp1)),
                                take_profit_2=Price(float(tp2)),
                                position_side=PositionSide.LONG,
                                symbol=params.symbol,
                            )
                            pending_breakout = None
                            continue

                rolling = prepared.iloc[idx - params.lookback : idx]
                level_high = float(rolling["high"].max())
                avg_volume = float(rolling["volume"].mean())

                breakout = row["close"] > level_high
                volume_ok = row["volume"] >= avg_volume * params.volume_mult
                if breakout and volume_ok:
                    pending_breakout = {
                        "breakout_idx": idx,
                        "level_high": level_high,
                        "breakout_low": float(row["low"]),
                        "breakout_close": float(row["close"]),
                    }

        if pending_signal is not None:
            self._logger.info(
                "signal_not_executed_end_of_data symbol=%s entry_time=%s entry_price=%.8f",
                params.symbol,
                pending_signal.entry_time.isoformat(),
                float(pending_signal.entry_price),
            )

        if sim.position is not None:
            final_row = prepared.iloc[-1]
            final_time = datetime_to_timezone(final_row["datetime"].to_pydatetime(), self._simulation_timezone)
            trades.append(sim.close_position(price=float(final_row["close"]), exit_time=final_time))

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
    def _resolve_stop_loss(*, params: BreakoutParams, level_high: float, breakout_low: float, retest_low: float) -> float:
        if params.sl_mode.value == "LEVEL":
            return level_high * (1 - params.retest_zone)
        if params.sl_mode.value == "BREAKOUT_EXTREME":
            return breakout_low
        return retest_low

    # endregion Private
