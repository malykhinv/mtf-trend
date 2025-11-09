from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

from config.config import AppConfig
from data_providers.ccxt_client import CcxtClient, OHLCV
from domain.models import LevelPattern, Pump, SwingsOutput, SwingHigh
from domain.models.candle import Candle
from infrastructure import get_logger
from integrations import SwingsAdapter, SwingsExtractor
from state import SymbolState
from strategies.breakout import RiskRewardResult
from strategies.htf_pipeline import HtfPipeline
from strategies.ltf_pipeline import LtfPipeline
from utils.precision import round_price_to_tick


@dataclass(frozen=True)
class BacktestParameters:
    """User-facing configuration for the backtest runner."""

    symbol: str
    start_datetime: datetime
    htf: str
    ltf: str
    end_datetime: datetime | None = None
    timeframe_batch: int = 150
    tick_size: float | None = None


@dataclass
class TradeSimulation:
    """State machine emulating order execution for a single trade."""

    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    rr: float
    stop_trigger: float
    tick_size: float | None
    entry_timestamp: datetime | None = None
    tp1_timestamp: datetime | None = None
    tp2_timestamp: datetime | None = None
    stop_timestamp: datetime | None = None
    breakeven_stop: float | None = None
    stop_order_price: float = field(init=False)
    breakeven_stop_order: float | None = None
    position_open: bool = False
    tp1_hit: bool = False
    outcome: str | None = None

    def __post_init__(self) -> None:
        self.stop_order_price = self.stop_loss + self.stop_trigger

    def process_candle(self, candle: OHLCV) -> None:
        """Process a single low timeframe candle."""

        if self.outcome is not None:
            return

        timestamp = _normalise_timestamp(candle["timestamp"])
        high = candle["high"]
        low = candle["low"]

        if not self.position_open:
            if high >= self.entry_price:
                self.entry_timestamp = timestamp
                self.position_open = True
            else:
                return

        # Apply stop-loss priority before take-profits.
        active_stop = self.breakeven_stop or self.stop_loss
        if low <= active_stop:
            self.outcome = "SL" if not self.tp1_hit else "TP1+BU"
            self.stop_timestamp = timestamp
            return

        tp1_triggered = high >= self.take_profit_1
        tp2_triggered = high >= self.take_profit_2

        if tp1_triggered and not self.tp1_hit:
            self.tp1_hit = True
            self.tp1_timestamp = timestamp
            new_stop = (self.entry_price + self.take_profit_1) / 2.0
            if self.tick_size:
                new_stop = round_price_to_tick(new_stop, self.tick_size)
            self.breakeven_stop = new_stop
            self.breakeven_stop_order = new_stop + self.stop_trigger
            if low <= new_stop:
                self.outcome = "TP1+BU"
                self.stop_timestamp = timestamp
                return

        if self.tp1_hit and tp2_triggered:
            self.tp2_timestamp = timestamp
            self.outcome = "TP1+TP2"


@dataclass
class BacktestLogEntry:
    symbol: str
    entry_time: datetime | None
    exit_time: datetime | None
    h_main: float
    l_pullback: float
    level_low: float
    level_top: float
    level_width: float
    pattern: str
    swings: str
    entry_price: float
    stop_loss: float
    stop_order_price: float
    take_profit_1: float
    take_profit_2: float
    rr: float
    meets_min_tp: bool
    outcome: str
    tp1_time: datetime | None
    tp2_time: datetime | None
    stop_time: datetime | None
    breakeven_stop: float | None
    breakeven_stop_order: float | None

    def as_csv_row(self) -> list[str]:
        def fmt(dt: datetime | None) -> str:
            return dt.astimezone(timezone.utc).isoformat() if dt else ""

        return [
            self.symbol,
            fmt(self.entry_time),
            fmt(self.exit_time),
            f"{self.h_main:.8f}",
            f"{self.l_pullback:.8f}",
            f"{self.level_low:.8f}",
            f"{self.level_top:.8f}",
            f"{self.level_width:.8f}",
            self.pattern,
            self.swings,
            f"{self.entry_price:.8f}",
            f"{self.stop_loss:.8f}",
            f"{self.stop_order_price:.8f}",
            f"{self.take_profit_1:.8f}",
            f"{self.take_profit_2:.8f}",
            f"{self.rr:.4f}",
            "1" if self.meets_min_tp else "0",
            self.outcome,
            fmt(self.tp1_time),
            fmt(self.tp2_time),
            fmt(self.stop_time),
            f"{self.breakeven_stop:.8f}" if self.breakeven_stop is not None else "",
            f"{self.breakeven_stop_order:.8f}" if self.breakeven_stop_order is not None else "",
        ]


@dataclass
class ScenarioContext:
    pump: Pump
    atr_value: float
    l_pullback: float | None
    start_timestamp: datetime
    ltf_buffer: list[OHLCV] = field(default_factory=list)
    swings_output: SwingsOutput | None = None
    level_pattern: LevelPattern | None = None
    level_low: float | None = None
    level_top: float | None = None
    level_width: float | None = None
    swings_summary: str = ""
    risk_reward: RiskRewardResult | None = None
    meets_min_tp: bool = False
    trade: TradeSimulation | None = None
    outcome: str | None = None


class BacktestRunner:
    """High level orchestrator performing a historical simulation."""

    def __init__(
        self,
        *,
        config: AppConfig,
        client: CcxtClient,
        swings_extractor: SwingsExtractor,
        parameters: BacktestParameters,
        logger: logging.Logger | None = None,
        log_directory: Path | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._parameters = parameters
        self._logger = logger or get_logger(__name__)
        self._swings_adapter = SwingsAdapter(swings_extractor)
        self._log_directory = log_directory or Path("backtest/logs")
        self._log_directory.mkdir(parents=True, exist_ok=True)

        self._htf_pipeline = HtfPipeline.from_config(config, logger=self._logger)
        self._ltf_pipeline = LtfPipeline.from_config(config, swings_adapter=self._swings_adapter, logger=self._logger)

    def run(self) -> list[BacktestLogEntry]:
        symbol = self._parameters.symbol
        self._logger.info("Запуск бэктеста для %s", symbol)

        htf_candles = self._load_candles(self._parameters.htf)
        ltf_candles = self._load_candles(self._parameters.ltf)

        candles = [_convert_ohlcv_to_candle(item) for item in htf_candles]
        state = SymbolState(symbol=symbol)

        scenario: ScenarioContext | None = None
        l_index = 0
        log_entries: list[BacktestLogEntry] = []

        for index in range(len(candles)):
            subset = candles[: index + 1]
            existing_pump = scenario.pump if scenario else None
            htf_result = self._htf_pipeline.run(subset, existing_pump, symbol=symbol)

            if htf_result.pump is None:
                scenario = None
                continue

            pump = htf_result.pump
            pump_index = _find_candle_index(subset, pump.candle)
            atr_value = htf_result.atr_values[pump_index] if pump_index is not None else htf_result.atr_values[-1]
            l_pullback = htf_result.pullback.l_pullback

            if scenario is None or pump.candle.timestamp != scenario.pump.candle.timestamp:
                scenario = ScenarioContext(
                    pump=pump,
                    atr_value=atr_value,
                    l_pullback=l_pullback,
                    start_timestamp=pump.candle.timestamp,
                )
                l_index = _align_low_index(ltf_candles, l_index, scenario.start_timestamp)

            if l_pullback is not None:
                scenario.l_pullback = l_pullback

            if not htf_result.ready_for_l_analysis:
                if htf_result.pullback.cancel_reason is not None and scenario.trade is None:
                    self._logger.info(
                        "Сценарий отменён до входа: %s", htf_result.pullback.cancel_reason
                    )
                continue

            if scenario.l_pullback is None:
                continue

            next_boundary = _next_candle_timestamp(candles, index)

            while l_index < len(ltf_candles):
                l_candle = ltf_candles[l_index]
                l_timestamp = _normalise_timestamp(l_candle["timestamp"])
                if l_timestamp < scenario.start_timestamp:
                    l_index += 1
                    continue
                if next_boundary is not None and l_timestamp >= next_boundary:
                    break

                scenario.ltf_buffer.append(l_candle)
                l_index += 1

                ltf_result = self._ltf_pipeline.run(
                    state,
                    tuple(scenario.ltf_buffer),
                    atr_h=scenario.atr_value,
                    h_main=scenario.pump.h_main,
                    l_pullback=scenario.l_pullback,
                    last_price=l_candle["high"],
                )

                if ltf_result.swings_output is not None:
                    scenario.swings_output = ltf_result.swings_output
                    scenario.swings_summary = _format_swings(ltf_result.swings_output.swings)
                if ltf_result.level_result is not None:
                    level = ltf_result.level_result.level
                    scenario.level_pattern = level.pattern
                    scenario.level_low = level.level_low
                    scenario.level_top = level.level_top
                    scenario.level_width = level.width
                if ltf_result.risk_reward is not None:
                    scenario.risk_reward = ltf_result.risk_reward
                    scenario.meets_min_tp = ltf_result.risk_reward.meets_min_tp

                if ltf_result.signal is not None and scenario.trade is None:
                    signal = ltf_result.signal
                    scenario.trade = TradeSimulation(
                        entry_price=signal.entry_price,
                        stop_loss=signal.stop_loss,
                        take_profit_1=signal.take_profit_1,
                        take_profit_2=signal.take_profit_2,
                        rr=signal.rr,
                        stop_trigger=self._config.order.stop_trigger,
                        tick_size=self._parameters.tick_size,
                    )

                if scenario.trade is not None:
                    scenario.trade.process_candle(l_candle)
                    if scenario.trade.outcome is not None:
                        state.reset_all()
                        entry = self._build_log_entry(symbol, scenario)
                        log_entries.append(entry)
                        scenario = None
                        break

            if scenario is not None and scenario.trade is not None and scenario.trade.outcome is None:
                self._logger.debug(
                    "Открытая позиция, ожидание следующих свечей L (%s)", symbol
                )

        if scenario is not None and scenario.trade is not None and scenario.trade.outcome is None:
            scenario.trade.outcome = "OPEN"
            log_entries.append(self._build_log_entry(symbol, scenario))

        self._write_log(log_entries)
        self._logger.info("Бэктест завершён, записано %d сделок", len(log_entries))
        return log_entries

    def _load_candles(self, timeframe: str) -> list[OHLCV]:
        params = self._parameters
        start_dt = _ensure_timezone(params.start_datetime)
        since = int(start_dt.timestamp() * 1000)
        end_dt = _ensure_timezone(params.end_datetime) if params.end_datetime else None
        until = int(end_dt.timestamp() * 1000) if end_dt else None
        step_ms = int(_timeframe_to_timedelta(timeframe).total_seconds() * 1000)

        all_candles: list[OHLCV] = []
        cursor = since
        while True:
            batch = self._client.fetch_ohlcv(
                params.symbol,
                timeframe,
                since=cursor,
                limit=params.timeframe_batch,
            )
            if not batch:
                break
            for candle in batch:
                if until is not None and candle["timestamp"] >= until:
                    all_candles.append(candle)
                    return all_candles
                all_candles.append(candle)
            last_ts = batch[-1]["timestamp"]
            if until is not None and last_ts >= until:
                break
            if len(batch) < params.timeframe_batch:
                break
            next_cursor = last_ts + step_ms
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        return all_candles

    def _build_log_entry(self, symbol: str, scenario: ScenarioContext) -> BacktestLogEntry:
        assert scenario.trade is not None
        trade = scenario.trade
        level_low = scenario.level_low or trade.entry_price
        level_top = scenario.level_top or trade.entry_price
        level_width = scenario.level_width or 0.0
        pattern = _pattern_label(scenario.level_pattern)
        outcome = trade.outcome or "OPEN"
        exit_time = trade.stop_timestamp or trade.tp2_timestamp or trade.tp1_timestamp
        breakeven_stop = trade.breakeven_stop

        return BacktestLogEntry(
            symbol=symbol,
            entry_time=trade.entry_timestamp,
            exit_time=exit_time,
            h_main=scenario.pump.h_main,
            l_pullback=scenario.l_pullback or trade.stop_loss,
            level_low=level_low,
            level_top=level_top,
            level_width=level_width,
            pattern=pattern,
            swings=scenario.swings_summary,
            entry_price=trade.entry_price,
            stop_loss=trade.stop_loss,
            stop_order_price=trade.stop_order_price,
            take_profit_1=trade.take_profit_1,
            take_profit_2=trade.take_profit_2,
            rr=trade.rr,
            meets_min_tp=scenario.meets_min_tp,
            outcome=outcome,
            tp1_time=trade.tp1_timestamp,
            tp2_time=trade.tp2_timestamp,
            stop_time=trade.stop_timestamp,
            breakeven_stop=breakeven_stop,
            breakeven_stop_order=trade.breakeven_stop_order,
        )

    def _write_log(self, entries: Sequence[BacktestLogEntry]) -> None:
        if not entries:
            return
        path = self._log_directory / f"{self._parameters.symbol}_backtest.csv"
        header = [
            "symbol",
            "entry_time",
            "exit_time",
            "h_main",
            "l_pullback",
            "level_low",
            "level_top",
            "level_width",
            "pattern",
            "swings",
            "entry",
            "stop",
            "stop_order",
            "tp1",
            "tp2",
            "rr",
            "min_tp",
            "outcome",
            "tp1_time",
            "tp2_time",
            "stop_time",
            "breakeven_stop",
            "breakeven_stop_order",
        ]
        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(header)
            for entry in entries:
                writer.writerow(entry.as_csv_row())


def _timeframe_to_timedelta(value: str) -> timedelta:
    units = {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
    }
    if not value:
        raise ValueError("timeframe не задан")
    suffix = value[-1]
    if suffix not in units:
        raise ValueError(f"Неизвестный суффикс таймфрейма: {value}")
    amount = int(value[:-1])
    seconds = amount * units[suffix]
    return timedelta(seconds=seconds)


def _convert_ohlcv_to_candle(ohlcv: OHLCV) -> Candle:
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


def _ensure_timezone(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _find_candle_index(candles: Sequence[Candle], target: Candle) -> int | None:
    for index, candle in enumerate(candles):
        if candle.timestamp == target.timestamp:
            return index
    return None


def _next_candle_timestamp(candles: Sequence[Candle], index: int) -> datetime | None:
    if index + 1 >= len(candles):
        return None
    return candles[index + 1].timestamp


def _align_low_index(candles: Sequence[OHLCV], start_index: int, start_time: datetime) -> int:
    index = start_index
    while index < len(candles):
        ts = _normalise_timestamp(candles[index]["timestamp"])
        if ts >= start_time:
            break
        index += 1
    return index


def _pattern_label(pattern: LevelPattern | None) -> str:
    if pattern == LevelPattern.MULTIPLE_SWINGS:
        return "A"
    if pattern == LevelPattern.SINGLE_WITH_CONSOLIDATION:
        return "B"
    return "?"


def _format_swings(swings: Iterable[SwingHigh]) -> str:
    formatted: list[str] = []
    for swing in swings:
        formatted.append(f"{swing.price:.8f}@{swing.timestamp.astimezone(timezone.utc).isoformat()}")
    return " | ".join(formatted)


__all__ = ["BacktestRunner", "BacktestParameters"]
