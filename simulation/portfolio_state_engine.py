"""Портфельный state-engine для синхронной обработки символов на таймлайне 15m."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import pandas as pd

from domain.enums.position_side import PositionSide
from domain.models.candle import Candle
from domain.models.trade_result import TradeResult
from domain.models.trade_signal import TradeSignal
from domain.value_objects.price import Price
from simulation.exit_manager import ExitManager, ExitManagerConfig
from simulation.order_processor import OrderProcessor
from simulation.position_simulator import StatefulPositionSimulator
from simulation.trade_classifier import TradeClassifier
from simulation.risk_manager import RiskConfig, RiskManager


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
    score_threshold: float = 0.0
    r_trade: float = 1.0
    portfolio_risk_limit: float = 3.0
    min_stop_atr_ratio: float = 0.3
    t_max_in_trade: int | None = None
    cooldown_bars: int = 8
    max_age_range: int = 24
    reclaim_limit: int = 3
    break_fail_threshold: int = 2
    min_pump_pct: float = 0.02
    max_range_width_pct: float = 0.03
    min_break_pct: float = 0.003
    min_reclaim_pct: float = 0.001
    rr: float = 2.0
    bee_bite_profile_id: str | None = None


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
    high_pump: float | None = None
    support: float | None = None
    resistance: float | None = None
    lowest_break: float | None = None
    atr_bg: float | None = None
    signal: TradeSignal | None = None


@dataclass(slots=True)
class EntryCandidate:
    symbol: str
    score: float
    signal: TradeSignal
    side: PositionSide
    reclaim_bars: int
    liquidity: float


@dataclass(slots=True)
class PortfolioStateEngine:
    config: PortfolioEngineConfig
    commission_rate: float
    slippage: float
    _states: dict[str, SymbolState] = field(default_factory=dict)
    _sims: dict[str, StatefulPositionSimulator] = field(default_factory=dict)
    _risk_manager: RiskManager | None = None

    def run(self, symbol_frames: dict[str, pd.DataFrame]) -> list[TradeResult]:
        timeline = self._build_timeline(symbol_frames)
        if not timeline:
            return []

        if self._risk_manager is None:
            self._risk_manager = RiskManager(
                RiskConfig(
                    r_trade=self.config.r_trade,
                    portfolio_risk_limit=self.config.portfolio_risk_limit,
                    min_stop_atr_ratio=self.config.min_stop_atr_ratio,
                )
            )

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
    def _row_by_timestamp(frame: pd.DataFrame, timestamp_ms: int) -> dict[str, float | None] | None:
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
            "open_interest": _to_optional_float(item.get("open_interest")),
            "taker_buy_volume": _to_optional_float(item.get("taker_buy_volume")),
            "atr_bg": _to_optional_float(item.get("atr_bg")),
            "high_pump": _to_optional_float(item.get("high_pump")),
            "lowest_break": _to_optional_float(item.get("lowest_break")),
            "support": _to_optional_float(item.get("support")),
            "resistance": _to_optional_float(item.get("resistance")),
        }

    def _new_simulator(self) -> StatefulPositionSimulator:
        return StatefulPositionSimulator(
            side=PositionSide.LONG,
            order_processor=OrderProcessor(commission_rate=self.commission_rate, slippage=self.slippage),
            trade_classifier=TradeClassifier(),
            exit_manager=ExitManager(config=ExitManagerConfig(bee_bite_mode=True)),
            max_bars_in_trade=self.config.t_max_in_trade,
        )

    def _process_active_trade(
        self,
        *,
        symbol: str,
        state: SymbolState,
        sim: StatefulPositionSimulator,
        row: dict[str, float | None],
    ) -> TradeResult | None:
        if state.state != PortfolioState.IN_TRADE:
            return None

        candle = self._to_candle(row)
        result = sim.process_candle(candle)
        if result is not None:
            state.state = PortfolioState.IDLE
            state.signal = None
            state.pump_anchor_close = None
            state.high_pump = None
            state.support = None
            state.resistance = None
            state.lowest_break = None
            state.atr_bg = None
            state.age = 0
            return result
        return None

    def _update_state(self, *, symbol: str, state: SymbolState, row: dict[str, float | None]) -> EntryCandidate | None:
        close = float(row["close"] or 0.0)
        state.age += 1

        if state.cooldown_bars_left > 0:
            state.cooldown_bars_left -= 1
            return None
        if state.state == PortfolioState.IN_TRADE:
            return None

        if state.state == PortfolioState.IDLE:
            state.pump_anchor_close = close
            state.high_pump = float(row["high"] or close)
            state.state = PortfolioState.SEEK_PUMP
            state.age = 0
            return None

        if state.state == PortfolioState.SEEK_PUMP:
            assert state.pump_anchor_close is not None
            state.high_pump = max(state.high_pump or close, float(row["high"] or close))
            if (close - state.pump_anchor_close) / max(state.pump_anchor_close, 1e-12) >= self.config.min_pump_pct:
                state.state = PortfolioState.SEEK_RANGE
                state.range_high = float(row["high"] or close)
                state.range_low = float(row["low"] or close)
                state.support = state.range_low
                state.resistance = state.range_high
                row_atr = row.get("atr_bg")
                state.atr_bg = float(row_atr) if row_atr is not None and row_atr > 0 else state.range_high - state.range_low
                state.age = 0
            elif state.age > self.config.max_age_range:
                self._reset_state(state)
            return None

        if state.state == PortfolioState.SEEK_RANGE:
            high = float(row["high"] or close)
            low = float(row["low"] or close)
            state.range_high = max(state.range_high or high, high)
            state.range_low = min(state.range_low or low, low)
            assert state.range_high is not None and state.range_low is not None
            state.support = state.range_low
            state.resistance = state.range_high
            candle_range = max(high - low, 1e-12)
            state.atr_bg = candle_range if state.atr_bg is None else (state.atr_bg * 0.7 + candle_range * 0.3)
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
                state.lowest_break = float(row["low"] or close)
                state.reclaim_count = 0
                state.break_fail_count = 0
                state.age = 0
            elif close < state.range_low * (1 - self.config.min_break_pct):
                state.state = PortfolioState.BREAK_ACTIVE
                state.break_side = PositionSide.SHORT
                state.break_price = close
                state.lowest_break = float(row["high"] or close)
                state.reclaim_count = 0
                state.break_fail_count = 0
                state.age = 0
            elif state.age > self.config.max_age_range:
                self._reset_state(state)
            return None

        if state.state == PortfolioState.BREAK_ACTIVE:
            assert state.break_side is not None and state.break_price is not None
            if state.break_side == PositionSide.LONG:
                state.lowest_break = min(state.lowest_break or float(row["low"] or close), float(row["low"] or close))
            else:
                state.lowest_break = max(state.lowest_break or float(row["high"] or close), float(row["high"] or close))
            if state.break_side == PositionSide.LONG and close >= state.break_price * (1 + self.config.min_reclaim_pct):
                state.reclaim_count += 1
            elif state.break_side == PositionSide.SHORT and close <= state.break_price * (1 - self.config.min_reclaim_pct):
                state.reclaim_count += 1
            else:
                state.break_fail_count += 1

            if state.reclaim_count >= self.config.reclaim_limit:
                signal = self._build_signal(symbol=symbol, row=row, side=state.break_side, state=state)
                if signal is None:
                    self._reset_state(state)
                    return None
                reclaim_bars = max(state.age, 1)
                state.signal = signal
                state.state = PortfolioState.ENTRY_SIGNAL
                state.age = 0
                score = self._score_candidate(row=row, side=state.break_side, range_high=state.range_high, range_low=state.range_low)
                return EntryCandidate(
                    symbol=symbol,
                    score=score,
                    signal=signal,
                    side=state.break_side,
                    reclaim_bars=reclaim_bars,
                    liquidity=float(row["volume"] or 0.0),
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
                reclaim_bars=max(state.age, 1),
                liquidity=float(row["volume"] or 0.0),
            )

        return None

    def _score_candidate(self, *, row: dict[str, float | None], side: PositionSide, range_high: float | None, range_low: float | None) -> float:
        close = float(row["close"] or 0.0)
        open_ = float(row["open"] or 0.0)
        high = float(row["high"] or 0.0)
        low = float(row["low"] or 0.0)
        volume = float(row["volume"] or 0.0)

        spread = max(high - low, 1e-12)
        body_ratio = abs(close - open_) / spread
        spread_to_close = spread / max(close, 1e-12)

        if range_high is None or range_low is None:
            breakout_strength = 0.0
        elif side == PositionSide.LONG:
            breakout_strength = (close - range_high) / max(range_high, 1e-12)
        else:
            breakout_strength = (range_low - close) / max(range_low, 1e-12)

        score = 0.0

        # Импульс свечи (плюсы/штрафы).
        if breakout_strength >= 0.004:
            score += 3.0
        elif breakout_strength >= 0.002:
            score += 2.0
        elif breakout_strength > 0.0:
            score += 1.0
        else:
            score -= 2.0

        if body_ratio >= 0.7:
            score += 2.0
        elif body_ratio >= 0.45:
            score += 1.0
        elif body_ratio < 0.2:
            score -= 1.0

        if spread_to_close <= 0.008:
            score += 1.0
        elif spread_to_close > 0.03:
            score -= 2.0

        # Ликвидность (обязательный компонент).
        if volume >= 1_000_000:
            score += 2.0
        elif volume >= 250_000:
            score += 1.0
        elif volume < 25_000:
            score -= 1.0

        # OI/taker применяем условно только при наличии валидных данных.
        open_interest = row.get("open_interest")
        if open_interest is not None and open_interest > 0.0:
            oi_to_volume = float(open_interest) / max(volume, 1e-12)
            if oi_to_volume >= 5.0:
                score += 1.0
            elif oi_to_volume < 1.0:
                score -= 1.0

        taker_buy_volume = row.get("taker_buy_volume")
        if taker_buy_volume is not None and taker_buy_volume > 0.0:
            taker_share = float(taker_buy_volume) / max(volume, 1e-12)
            if side == PositionSide.LONG:
                if taker_share >= 0.55:
                    score += 1.0
                elif taker_share <= 0.45:
                    score -= 1.0
            else:
                if taker_share <= 0.45:
                    score += 1.0
                elif taker_share >= 0.55:
                    score -= 1.0

        return score

    def _pick_top_candidates(self, candidates: list[EntryCandidate]) -> list[EntryCandidate]:
        if not candidates:
            return []
        filtered = [candidate for candidate in candidates if candidate.score >= self.config.score_threshold]
        if not filtered:
            return []
        ordered = sorted(
            filtered,
            key=lambda c: (c.score, -c.reclaim_bars, c.liquidity, c.symbol),
            reverse=True,
        )
        return ordered[: self.config.top_n]

    def _activate_candidate(self, candidate: EntryCandidate) -> None:
        state = self._states[candidate.symbol]
        sim = self._sims[candidate.symbol]
        sim.side = candidate.side
        assert self._risk_manager is not None
        active_positions = [
            (other_sim.position, other_sim.side)
            for other_sim in self._sims.values()
            if other_sim.position is not None
        ]
        if not self._risk_manager.can_open_with_portfolio_limit(active_positions=active_positions, signal=candidate.signal):
            self._reject_candidate(candidate.symbol)
            return
        if not self._risk_manager.check_stop_distance_by_atr(signal=candidate.signal, atr_bg=float(candidate.signal.atr_bg or 0.0)):
            self._reject_candidate(candidate.symbol)
            return
        size = self._risk_manager.calc_position_size(signal=candidate.signal)
        sim.register_signal(candidate.signal, size=size)
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
            state.pump_anchor_close = None
            state.high_pump = None
            state.support = None
            state.resistance = None
            state.lowest_break = None
            state.atr_bg = None
        return trades

    def _build_signal(self, *, symbol: str, row: dict[str, float], side: PositionSide, state: SymbolState) -> TradeSignal | None:
        entry = float(row["close"])
        support = float(state.support if state.support is not None else entry)
        resistance = float(state.resistance if state.resistance is not None else entry)
        atr_bg = float(state.atr_bg if state.atr_bg is not None else max(abs(resistance - support) * 0.25, entry * 0.001))
        high_pump = float(state.high_pump if state.high_pump is not None else max(entry, resistance))

        if side == PositionSide.LONG:
            lowest_break = float(state.lowest_break if state.lowest_break is not None else support)
            stop = min(lowest_break, support - 0.1 * atr_bg)
            if (entry - stop) < (0.3 * atr_bg):
                return None
            tp1 = max(resistance, entry + 0.5 * atr_bg)
            tp2 = max(high_pump, tp1 + atr_bg)
        else:
            lowest_break = float(state.lowest_break if state.lowest_break is not None else resistance)
            stop = max(lowest_break, resistance + 0.1 * atr_bg)
            if (stop - entry) < (0.3 * atr_bg):
                return None
            tp1 = min(support, entry - 0.5 * atr_bg)
            tp2 = min(high_pump if high_pump < entry else entry - atr_bg, tp1 - atr_bg)

        signal = TradeSignal(
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
            atr_bg=atr_bg,
            high_pump=high_pump,
            tp1_close_ratio=self._resolve_tp1_close_ratio(),
        )
        return signal

    def _resolve_tp1_close_ratio(self) -> float:
        profile = (self.config.bee_bite_profile_id or "").upper()
        if profile == "B":
            return 0.6
        if profile == "C":
            return 0.7
        return 0.5

    @staticmethod
    def _to_candle(row: dict[str, float | None]) -> Candle:
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
        state.pump_anchor_close = None
        state.high_pump = None
        state.support = None
        state.resistance = None
        state.lowest_break = None
        state.atr_bg = None



def _to_optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result
