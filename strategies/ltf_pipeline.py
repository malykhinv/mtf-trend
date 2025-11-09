from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Sequence

from config.config import AppConfig, MinTakeProfitConfig, RiskRewardConfig
from data_providers.ccxt_client import OHLCV
from domain.models import Band, Candle, Level, SwingsOutput
from integrations import SwingsAdapter
from infrastructure import (
    BreakoutContext,
    Notifier,
    get_logger,
    log_breakout,
    log_level_identified,
)
from state import SymbolState
from strategies.breakout import RiskRewardResult, check_breakout, compute_rr
from strategies.level_builder import LevelBuildResult, build_level


@dataclass(frozen=True)
class BreakoutSignal:
    """Signal instructing the system to place breakout orders."""

    level: Level
    band: Band | None
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    rr: float


@dataclass(frozen=True)
class LtfPipelineResult:
    """Detailed outcome of the low timeframe analysis."""

    swings_output: SwingsOutput | None
    level_result: LevelBuildResult | None
    breakout_triggered: bool
    risk_reward: RiskRewardResult | None
    signal: BreakoutSignal | None
    state: SymbolState


@dataclass
class LtfPipeline:
    """Pipeline responsible for processing low timeframe data."""

    swings_adapter: SwingsAdapter
    risk_reward_config: RiskRewardConfig
    min_tp_config: MinTakeProfitConfig
    now_factory: Callable[[], datetime] = lambda: datetime.now(tz=timezone.utc)
    notifier: Notifier | None = None
    logger: logging.Logger = field(default_factory=lambda: get_logger(__name__))

    @classmethod
    def from_config(
        cls,
        config: AppConfig,
        swings_adapter: SwingsAdapter,
        *,
        now_factory: Callable[[], datetime] | None = None,
        notifier: Notifier | None = None,
        logger: logging.Logger | None = None,
    ) -> LtfPipeline:
        return cls(
            swings_adapter=swings_adapter,
            risk_reward_config=config.risk_reward,
            min_tp_config=config.min_take_profit,
            now_factory=now_factory or (lambda: datetime.now(tz=timezone.utc)),
            notifier=notifier,
            logger=logger or get_logger(__name__),
        )

    def run(
        self,
        state: SymbolState,
        ltf_candles: Sequence[OHLCV],
        *,
        atr_h: float,
        h_main: float,
        l_pullback: float,
        last_price: float,
    ) -> LtfPipelineResult:
        """Execute the pipeline for the provided symbol state and data."""

        state.update_pump(h_main=h_main, l_pullback=l_pullback)

        if not ltf_candles:
            return LtfPipelineResult(
                swings_output=None,
                level_result=None,
                breakout_triggered=False,
                risk_reward=None,
                signal=None,
                state=state,
            )

        candles = [_convert_ohlcv(candle) for candle in ltf_candles]
        swings_output = self.swings_adapter.extract_swings(candles)

        level_result = build_level(swings_output, atr_h)
        if level_result is not None:
            state.update_levels(
                level_top=level_result.level.level_top,
                level_low=level_result.level.level_low,
            )
            if not state.has_open_position:
                state.mark_monitoring()
            log_level_identified(
                logger=self.logger,
                notifier=self.notifier,
                symbol=state.symbol,
                level=level_result.level,
                pattern=level_result.level.pattern,
                swings_count=len(level_result.used_swings),
            )
        else:
            state.update_levels(level_top=None, level_low=None)

        breakout_detected = False
        risk_reward_result: RiskRewardResult | None = None
        signal: BreakoutSignal | None = None

        if level_result is not None and not state.has_open_position:
            breakout_detected = check_breakout(last_price, level_result.level.level_top)
            if breakout_detected:
                risk_reward_result = compute_rr(
                    entry_price=level_result.level.level_top,
                    l_pullback=l_pullback,
                    h_main=h_main,
                    min_rr=self.risk_reward_config.rr_min,
                    min_tp_pct=self.min_tp_config.min_tp_pct,
                )
                if risk_reward_result.meets_min_rr and risk_reward_result.meets_min_tp:
                    signal = BreakoutSignal(
                        level=level_result.level,
                        band=level_result.consolidation_band,
                        entry_price=level_result.level.level_top,
                        stop_loss=l_pullback,
                        take_profit_1=risk_reward_result.tp1,
                        take_profit_2=risk_reward_result.tp2,
                        rr=risk_reward_result.rr,
                    )
                    state.mark_active()
                    log_breakout(
                        logger=self.logger,
                        notifier=self.notifier,
                        context=BreakoutContext(
                            symbol=state.symbol,
                            entry_price=signal.entry_price,
                            rr=signal.rr,
                        ),
                    )

        return LtfPipelineResult(
            swings_output=swings_output,
            level_result=level_result,
            breakout_triggered=breakout_detected,
            risk_reward=risk_reward_result,
            signal=signal,
            state=state,
        )


def _convert_ohlcv(ohlcv: OHLCV) -> Candle:
    timestamp = _normalise_timestamp(ohlcv["timestamp"])
    return Candle(
        open=ohlcv["open"],
        high=ohlcv["high"],
        low=ohlcv["low"],
        close=ohlcv["close"],
        volume=ohlcv["volume"],
        timestamp=timestamp,
    )


def _normalise_timestamp(value: int | float) -> datetime:
    timestamp = float(value)
    if timestamp > 1_000_000_000_000:
        timestamp /= 1_000.0
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


__all__ = ["BreakoutSignal", "LtfPipeline", "LtfPipelineResult"]
