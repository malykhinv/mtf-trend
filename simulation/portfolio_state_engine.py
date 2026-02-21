"""Портфельный state-engine для синхронной обработки символов на таймлайне 15m."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import pandas as pd

from constants import STRATEGY_POSITION_SIZE
from domain.enums.position_side import PositionSide
from domain.models.candle import Candle
from domain.models.trade_result import TradeResult
from domain.models.trade_signal import TradeSignal
from domain.value_objects.price import Price
from simulation.order_processor import OrderProcessor
from simulation.position_simulator import StatefulPositionSimulator
from simulation.trade_classifier import TradeClassifier


class PortfolioState(str, Enum):
    IDLE = "IDLE"
    SEEK_PUMP = "SEEK_PUMP"
    SEEK_RANGE = "SEEK_RANGE"
    RANGE_LOCKED = "RANGE_LOCKED"
    BREAK_ACTIVE = "BREAK_ACTIVE"
    ENTRY_SIGNAL = "ENTRY_SIGNAL"
    IN_TRADE = "IN_TRADE"


@dataclass(slots=True)
class PortfolioEngineConfig:
    top_n: int = 1
    cooldown_bars: int = 8
    max_age_range: int = 24
    reclaim_limit: int = 3
    break_fail_threshold: int = 2
    min_pump_pct: float = 0.02
    max_range_width_pct: float = 0.03
    min_break_pct: float = 0.003
    min_reclaim_pct: float = 0.001
    rr: float = 2.0


@dataclass(slots=True)
class SymbolState:
    state: PortfolioState = PortfolioState.IDLE
    age: int = 0
    cooldown_bars_left: int = 0
    range_high: float | None = None
    range_low: float | None = None
    break_side: PositionSide | None = None
    reclaim_count: int = 0
    break_fail_count: int = 0
    break_price: float | None = None
    pump_anchor_close: float | None = None
    signal: TradeSignal | None = None


@dataclass(slots=True)
class EntryCandidate:
    symbol: str
    score: float
    signal: TradeSignal
    side: PositionSide
    tie_breaker_age: int


@dataclass(slots=True)
class PortfolioStateEngine:
    config: PortfolioEngineConfig
    commission_rate: float
    slippage: float
    _states: dict[str, SymbolState] = field(default_factory=dict)
    _sims: dict[str, StatefulPositionSimulator] = field(default_factory=dict)

    def run(self, symbol_frames: dict[str, pd.DataFrame]) -> list[TradeResult]:
        timeline = self._build_timeline(symbol_frames)
        if not timeline:
            return []

        trades: list[TradeResult] = []
        for ts in timeline:
            candidates: list[EntryCandidate] = []
            for symbol, frame in symbol_frames.items():
                row = self._row_by_timestamp(frame, ts)
                if row is None:
                    continue
                state = self._states.setdefault(symbol, SymbolState())
                sim = self._sims.setdefault(symbol, self._new_simulator())

                closed_trade = self._process_active_trade(symbol=symbol, state=state, sim=sim, row=row)
                if closed_trade is not None:
                    trades.append(closed_trade)

                candidate = self._update_state(symbol=symbol, state=state, row=row)
                if candidate is not None:
                    candidates.append(candidate)

            winners = self._pick_top_candidates(candidates)
            winners_symbols = {candidate.symbol for candidate in winners}
            for candidate in winners:
                self._activate_candidate(candidate)
            for candidate in candidates:
                if candidate.symbol not in winners_symbols:
                    self._reject_candidate(candidate.symbol)

        trades.extend(self._close_open_positions(symbol_frames=symbol_frames, timeline=timeline))
        return sorted(trades, key=lambda trade: (trade.exit_timestamp_ms, trade.entry_timestamp_ms))

    def _build_timeline(self, symbol_frames: dict[str, pd.DataFrame]) -> list[int]:
        timeline: set[int] = set()
        for frame in symbol_frames.values():
            if "timestamp" not in frame.columns:
                continue
            timeline.update(int(ts) for ts in frame["timestamp"].tolist())
        return sorted(timeline)

    @staticmethod
    def _row_by_timestamp(frame: pd.DataFrame, timestamp_ms: int) -> dict[str, float] | None:
        row = frame.loc[frame["timestamp"] == timestamp_ms]
        if row.empty:
            return None
        item = row.iloc[0]
        return {
            "timestamp": int(item["timestamp"]),
            "open": float(item["open"]),
            "high": float(item["high"]),
            "low": float(item["low"]),
            "close": float(item["close"]),
            "volume": float(item["volume"]),
        }

    def _new_simulator(self) -> StatefulPositionSimulator:
        return StatefulPositionSimulator(
            side=PositionSide.LONG,
            order_processor=OrderProcessor(commission_rate=self.commission_rate, slippage=self.slippage),
            trade_classifier=TradeClassifier(),
        )

    def _process_active_trade(
        self,
        *,
        symbol: str,
        state: SymbolState,
        sim: StatefulPositionSimulator,
        row: dict[str, float],
    ) -> TradeResult | None:
        if state.state != PortfolioState.IN_TRADE:
            return None

        candle = self._to_candle(row)
        result = sim.process_candle(candle)
        if result is not None:
            state.state = PortfolioState.IDLE
            state.signal = None
            state.age = 0
            return result
        return None

    def _update_state(self, *, symbol: str, state: SymbolState, row: dict[str, float]) -> EntryCandidate | None:
        close = row["close"]
        state.age += 1

        if state.cooldown_bars_left > 0:
            state.cooldown_bars_left -= 1
            return None
        if state.state == PortfolioState.IN_TRADE:
            return None

        if state.state == PortfolioState.IDLE:
            state.pump_anchor_close = close
            state.state = PortfolioState.SEEK_PUMP
            state.age = 0
            return None

        if state.state == PortfolioState.SEEK_PUMP:
            assert state.pump_anchor_close is not None
            if (close - state.pump_anchor_close) / max(state.pump_anchor_close, 1e-12) >= self.config.min_pump_pct:
                state.state = PortfolioState.SEEK_RANGE
                state.range_high = row["high"]
                state.range_low = row["low"]
                state.age = 0
            elif state.age > self.config.max_age_range:
                self._reset_state(state)
            return None

        if state.state == PortfolioState.SEEK_RANGE:
            state.range_high = max(state.range_high or row["high"], row["high"])
            state.range_low = min(state.range_low or row["low"], row["low"])
            assert state.range_high is not None and state.range_low is not None
            width_pct = (state.range_high - state.range_low) / max(close, 1e-12)
            if width_pct <= self.config.max_range_width_pct and state.age >= 2:
                state.state = PortfolioState.RANGE_LOCKED
                state.age = 0
            elif state.age > self.config.max_age_range:
                self._reset_state(state)
            return None

        if state.state == PortfolioState.RANGE_LOCKED:
            assert state.range_high is not None and state.range_low is not None
            if close > state.range_high * (1 + self.config.min_break_pct):
                state.state = PortfolioState.BREAK_ACTIVE
                state.break_side = PositionSide.LONG
                state.break_price = close
                state.reclaim_count = 0
                state.break_fail_count = 0
                state.age = 0
            elif close < state.range_low * (1 - self.config.min_break_pct):
                state.state = PortfolioState.BREAK_ACTIVE
                state.break_side = PositionSide.SHORT
                state.break_price = close
                state.reclaim_count = 0
                state.break_fail_count = 0
                state.age = 0
            elif state.age > self.config.max_age_range:
                self._reset_state(state)
            return None

        if state.state == PortfolioState.BREAK_ACTIVE:
            assert state.break_side is not None and state.break_price is not None
            if state.break_side == PositionSide.LONG and close >= state.break_price * (1 + self.config.min_reclaim_pct):
                state.reclaim_count += 1
            elif state.break_side == PositionSide.SHORT and close <= state.break_price * (1 - self.config.min_reclaim_pct):
                state.reclaim_count += 1
            else:
                state.break_fail_count += 1

            if state.reclaim_count >= self.config.reclaim_limit:
                signal = self._build_signal(symbol=symbol, row=row, side=state.break_side)
                state.signal = signal
                state.state = PortfolioState.ENTRY_SIGNAL
                state.age = 0
                score = self._score_candidate(row=row, side=state.break_side, range_high=state.range_high, range_low=state.range_low)
                return EntryCandidate(
                    symbol=symbol,
                    score=score,
                    signal=signal,
                    side=state.break_side,
                    tie_breaker_age=state.age,
                )

            if state.break_fail_count >= self.config.break_fail_threshold or state.age > self.config.max_age_range:
                self._reset_state(state)
            return None

        if state.state == PortfolioState.ENTRY_SIGNAL and state.signal is not None:
            score = self._score_candidate(row=row, side=state.signal.position_side, range_high=state.range_high, range_low=state.range_low)
            return EntryCandidate(
                symbol=symbol,
                score=score,
                signal=state.signal,
                side=state.signal.position_side,
                tie_breaker_age=state.age,
            )

        return None

    def _score_candidate(self, *, row: dict[str, float], side: PositionSide, range_high: float | None, range_low: float | None) -> float:
        body = abs(row["close"] - row["open"]) / max(row["close"], 1e-12)
        if range_high is None or range_low is None:
            breakout_strength = 0.0
        elif side == PositionSide.LONG:
            breakout_strength = (row["close"] - range_high) / max(range_high, 1e-12)
        else:
            breakout_strength = (range_low - row["close"]) / max(range_low, 1e-12)
        return float(row["volume"]) * 0.5 + body * 100.0 + breakout_strength * 200.0

    def _pick_top_candidates(self, candidates: list[EntryCandidate]) -> list[EntryCandidate]:
        if not candidates:
            return []
        ordered = sorted(candidates, key=lambda c: (c.score, -c.tie_breaker_age, c.symbol), reverse=True)
        return ordered[: self.config.top_n]

    def _activate_candidate(self, candidate: EntryCandidate) -> None:
        state = self._states[candidate.symbol]
        sim = self._sims[candidate.symbol]
        sim.side = candidate.side
        sim.register_signal(candidate.signal, size=STRATEGY_POSITION_SIZE)
        state.state = PortfolioState.IN_TRADE
        state.age = 0

    def _reject_candidate(self, symbol: str) -> None:
        state = self._states[symbol]
        self._reset_state(state)
        state.cooldown_bars_left = self.config.cooldown_bars

    def _close_open_positions(self, *, symbol_frames: dict[str, pd.DataFrame], timeline: list[int]) -> list[TradeResult]:
        if not timeline:
            return []
        last_ts = timeline[-1]
        trades: list[TradeResult] = []
        for symbol, sim in self._sims.items():
            if sim.position is None:
                continue
            frame = symbol_frames[symbol]
            row = self._row_by_timestamp(frame, last_ts)
            if row is None:
                continue
            trades.append(sim.close_position(price=row["close"], exit_timestamp_ms=last_ts))
            state = self._states[symbol]
            state.state = PortfolioState.IDLE
            state.signal = None
        return trades

    def _build_signal(self, *, symbol: str, row: dict[str, float], side: PositionSide) -> TradeSignal:
        entry = row["close"]
        risk = max(entry * 0.005, 1e-8)
        if side == PositionSide.LONG:
            stop = entry - risk
            tp1 = entry + risk
            tp2 = entry + risk * self.config.rr
        else:
            stop = entry + risk
            tp1 = entry - risk
            tp2 = entry - risk * self.config.rr
        return TradeSignal(
            symbol=symbol,
            position_side=side,
            entry_price=Price(entry),
            entry_timestamp_ms=int(row["timestamp"]),
            formation_timestamp_ms=int(row["timestamp"]),
            stop_loss=Price(stop),
            take_profit_1=Price(tp1),
            take_profit_2=Price(tp2),
            breakout_timestamp_ms=int(row["timestamp"]),
            retest_timestamp_ms=int(row["timestamp"]),
        )

    @staticmethod
    def _to_candle(row: dict[str, float]) -> Candle:
        from domain.value_objects.volume import Volume

        return Candle(
            timestamp_ms=int(row["timestamp"]),
            open=Price(row["open"]),
            high=Price(row["high"]),
            low=Price(row["low"]),
            close=Price(row["close"]),
            volume=Volume(row["volume"]),
            open_interest=Volume(0.0),
            taker_buy_volume=None,
        )

    @staticmethod
    def _reset_state(state: SymbolState) -> None:
        state.state = PortfolioState.IDLE
        state.age = 0
        state.range_high = None
        state.range_low = None
        state.break_side = None
        state.break_price = None
        state.reclaim_count = 0
        state.break_fail_count = 0
        state.signal = None

