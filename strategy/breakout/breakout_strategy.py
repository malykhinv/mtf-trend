"""Simple breakout strategy implementation backed by stateful position simulation."""

from __future__ import annotations

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

        for idx in range(params.lookback, len(prepared)):
            row = prepared.iloc[idx]
            candle = self._to_candle(row)

            if sim.position is None and pending_signal is None:
                rolling = prepared.iloc[idx - params.lookback : idx]
                level_high = float(rolling["high"].max())
                level_low = float(rolling["low"].min())
                avg_volume = float(rolling["volume"].mean())

                breakout = row["close"] > level_high
                volume_ok = row["volume"] >= avg_volume * params.volume_mult
                if breakout and volume_ok:
                    risk = max(row["close"] - level_low, row["close"] * STRATEGY_RISK_FLOOR)
                    stop = row["close"] - risk
                    tp1 = row["close"] + risk * params.min_rr
                    tp2 = row["close"] + risk * params.min_rr * params.tp2_mult
                    pending_signal = TradeSignal(
                        entry_price=Price(float(row["close"])),
                        entry_time=datetime_to_timezone(row["datetime"].to_pydatetime(), self._simulation_timezone),
                        stop_loss=Price(float(stop)),
                        take_profit_1=Price(float(tp1)),
                        take_profit_2=Price(float(tp2)),
                        position_side=PositionSide.LONG,
                        symbol=params.symbol,
                    )

            if sim.position is None and pending_signal is not None:
                sim.register_signal(pending_signal, size=STRATEGY_POSITION_SIZE)
                pending_signal = None

            result = sim.process_candle(candle)
            if result is not None:
                trades.append(result)

        if sim.position is not None:
            final_row = prepared.iloc[-1]
            trades.append(sim._finalize(self._to_candle(final_row), float(final_row["close"])))  # noqa: SLF001

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

    # endregion Private
