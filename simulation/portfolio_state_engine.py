"""Портфельный state-engine для синхронной обработки символов на таймлайне 15m."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import pandas as pd

from domain.enums.position_side import PositionSide
from domain.enums.trade_result_type import TradeResultType
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
    reclaim_candle_score: float


@dataclass(slots=True)
class PortfolioStateEngine:
    config: PortfolioEngineConfig
    commission_rate: float
    slippage: float
    _states: dict[str, SymbolState] = field(default_factory=dict)
    _cooldown_until_idx: dict[str, int] = field(default_factory=dict)
    _sims: dict[str, StatefulPositionSimulator] = field(default_factory=dict)
    _risk_manager: RiskManager | None = None
    _current_timeline_idx: int = -1

    PROFILE_SCORE_THRESHOLDS: dict[str, float] = field(
        default_factory=lambda: {
            "A": 4.0,  # Conservative
            "B": 3.0,  # Balanced
            "C": 2.0,  # Aggressive
        }
    )
    PROFILE_COOLDOWN_HOURS: dict[str, int] = field(
        default_factory=lambda: {
            "A": 8,
            "B": 6,
            "C": 4,
        }
    )

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
        for idx, ts in enumerate(timeline):
            self._current_timeline_idx = idx
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

                candidate = self._update_state(symbol=symbol, state=state, row=row, timeline_idx=idx)
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
            "taker_buy_ratio": _to_optional_float(item.get("taker_buy_ratio")),
            "avg_volume_range": _to_optional_float(item.get("avg_volume_range")),
            "avg_range_volume": _to_optional_float(item.get("avg_range_volume")),
            "range_volume_avg": _to_optional_float(item.get("range_volume_avg")),
            "oi_reclaim": _to_optional_float(item.get("oi_reclaim")),
            "oi_break_avg": _to_optional_float(item.get("oi_break_avg")),
            "range_volume_zscore": _to_optional_float(item.get("range_volume_zscore")),
            "volume_range_zscore": _to_optional_float(item.get("volume_range_zscore")),
            "zscore_range_volume": _to_optional_float(item.get("zscore_range_volume")),
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
            if result.result_type == TradeResultType.SL:
                self._set_cooldown(symbol=symbol)
            return result
        return None

    def _update_state(
        self,
        *,
        symbol: str,
        state: SymbolState,
        row: dict[str, float | None],
        timeline_idx: int,
    ) -> EntryCandidate | None:
        close = float(row["close"] or 0.0)
        state.age += 1

        if state.state == PortfolioState.IN_TRADE:
            return None

        if state.state == PortfolioState.IDLE:
            if self._is_on_cooldown(symbol=symbol, timeline_idx=timeline_idx):
                return None
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
                score, reclaim_candle_score = self._score_candidate(
                    row=row,
                    side=state.break_side,
                    range_high=state.range_high,
                    range_low=state.range_low,
                    reclaim_bars=reclaim_bars,
                )
                return EntryCandidate(
                    symbol=symbol,
                    score=score,
                    signal=signal,
                    side=state.break_side,
                    reclaim_bars=reclaim_bars,
                    liquidity=float(row["volume"] or 0.0),
                    reclaim_candle_score=reclaim_candle_score,
                )

            if state.break_fail_count >= self.config.break_fail_threshold or state.age > self.config.max_age_range:
                self._reset_state(state)
                self._set_cooldown(symbol=symbol, timeline_idx=timeline_idx)
            return None

        if state.state == PortfolioState.ENTRY_SIGNAL and state.signal is not None:
            reclaim_bars = max(state.age, 1)
            score, reclaim_candle_score = self._score_candidate(
                row=row,
                side=state.signal.position_side,
                range_high=state.range_high,
                range_low=state.range_low,
                reclaim_bars=reclaim_bars,
            )
            return EntryCandidate(
                symbol=symbol,
                score=score,
                signal=state.signal,
                side=state.signal.position_side,
                reclaim_bars=reclaim_bars,
                liquidity=float(row["volume"] or 0.0),
                reclaim_candle_score=reclaim_candle_score,
            )

        return None

    def _score_candidate(
        self,
        *,
        row: dict[str, float | None],
        side: PositionSide,
        range_high: float | None,
        range_low: float | None,
        reclaim_bars: int,
    ) -> tuple[float, float]:
        close = float(row["close"] or 0.0)
        open_ = float(row["open"] or 0.0)
        high = float(row["high"] or 0.0)
        low = float(row["low"] or 0.0)
        volume = float(row["volume"] or 0.0)

        spread = max(high - low, 1e-12)
        body_ratio = abs(close - open_) / spread
        close_position = (close - low) / spread if side == PositionSide.LONG else (high - close) / spread

        profile = (self.config.bee_bite_profile_id or "B").upper()
        # A=Conservative, B=Balanced, C=Aggressive.
        score_threshold = self.PROFILE_SCORE_THRESHOLDS.get(profile, self.config.score_threshold)

        score = 0.0
        reclaim_candle_score = 0.0

        # 1) Скорость reclaim.
        if reclaim_bars <= 1:
            score += 2.0
        elif reclaim_bars <= 2:
            score += 1.0
        elif reclaim_bars >= 5:
            score -= 1.0

        # 2) Качество reclaim-свечи.
        if body_ratio >= 0.6 and close_position >= 0.7:
            reclaim_candle_score = 2.0
        elif body_ratio >= 0.4 and close_position >= 0.55:
            reclaim_candle_score = 1.0
        elif body_ratio < 0.2 or close_position < 0.45:
            reclaim_candle_score = -1.0
        score += reclaim_candle_score

        # 3) Объём reclaim против avg_volume_range.
        avg_volume_range = _first_valid_positive(
            row,
            "avg_volume_range",
            "avg_range_volume",
            "range_volume_avg",
        )
        if avg_volume_range is not None:
            reclaim_to_avg = volume / max(avg_volume_range, 1e-12)
            if reclaim_to_avg >= 1.4:
                score += 1.5
            elif reclaim_to_avg >= 1.0:
                score += 0.5
            elif reclaim_to_avg < 0.7:
                score -= 1.0

        # 4) taker_buy_ratio (если валиден).
        taker_buy_ratio = _first_valid_ratio(
            row,
            "taker_buy_ratio",
            "taker_ratio",
        )
        if taker_buy_ratio is not None:
            if side == PositionSide.LONG:
                if taker_buy_ratio >= 0.55:
                    score += 1.0
                elif taker_buy_ratio <= 0.45:
                    score -= 1.0
            else:
                if taker_buy_ratio <= 0.45:
                    score += 1.0
                elif taker_buy_ratio >= 0.55:
                    score -= 1.0

        # 5) OI_reclaim vs OI_break_avg (если валиден).
        oi_reclaim = _first_valid_positive(row, "oi_reclaim", "OI_reclaim")
        oi_break_avg = _first_valid_positive(row, "oi_break_avg", "OI_break_avg")
        if oi_reclaim is not None and oi_break_avg is not None:
            oi_ratio = oi_reclaim / max(oi_break_avg, 1e-12)
            if oi_ratio >= 1.1:
                score += 1.0
            elif oi_ratio < 0.9:
                score -= 1.0

        # 6) Глубина прокола.
        depth = 0.0
        if range_high is not None and range_low is not None:
            range_width = max(range_high - range_low, 1e-12)
            if side == PositionSide.LONG:
                depth = max((range_low - low) / range_width, 0.0)
            else:
                depth = max((high - range_high) / range_width, 0.0)
        if 0.05 <= depth <= 0.5:
            score += 1.5
        elif depth > 0.8:
            score -= 1.5
        elif depth < 0.02:
            score -= 0.5

        # 7) z-score диапазонного объёма.
        zscore_range_volume = _first_valid_float(
            row,
            "range_volume_zscore",
            "volume_range_zscore",
            "zscore_range_volume",
        )
        if zscore_range_volume is not None:
            if zscore_range_volume >= 1.0:
                score += 1.0
            elif zscore_range_volume <= -1.0:
                score -= 1.0

        # Профильные штрафы.
        if profile == "A":  # Conservative
            if reclaim_bars >= 3:
                score -= 1.0
            if reclaim_candle_score <= 0.0:
                score -= 0.75
            if depth > 0.6:
                score -= 0.75
        elif profile == "B":  # Balanced
            if reclaim_bars >= 4:
                score -= 0.5
            if reclaim_candle_score < 0.0:
                score -= 0.25
        else:  # C / Aggressive
            if reclaim_bars >= 5:
                score -= 0.25

        if score_threshold >= 4.0 and score < 0.0:
            score -= 0.5

        return score, reclaim_candle_score

    def _resolve_score_threshold(self) -> float:
        profile = (self.config.bee_bite_profile_id or "").upper()
        if profile in self.PROFILE_SCORE_THRESHOLDS:
            return self.PROFILE_SCORE_THRESHOLDS[profile]
        return self.config.score_threshold

    def _pick_top_candidates(self, candidates: list[EntryCandidate]) -> list[EntryCandidate]:
        if not candidates:
            return []
        score_threshold = self._resolve_score_threshold()
        filtered = [candidate for candidate in candidates if candidate.score >= score_threshold]
        if not filtered:
            return []
        ordered = sorted(
            filtered,
            key=lambda c: (-c.score, c.reclaim_bars, -c.reclaim_candle_score, -c.liquidity, c.symbol),
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
        self._set_cooldown(symbol=symbol)

    def _is_on_cooldown(self, *, symbol: str, timeline_idx: int) -> bool:
        until_idx = self._cooldown_until_idx.get(symbol)
        return until_idx is not None and timeline_idx < until_idx

    def _set_cooldown(self, *, symbol: str, timeline_idx: int | None = None) -> None:
        bars = self._resolve_cooldown_bars()
        if bars <= 0:
            self._cooldown_until_idx.pop(symbol, None)
            return
        start_idx = timeline_idx if timeline_idx is not None else self._current_timeline_idx
        self._cooldown_until_idx[symbol] = start_idx + bars

    def _resolve_cooldown_bars(self) -> int:
        profile = (self.config.bee_bite_profile_id or "").upper()
        if profile in self.PROFILE_COOLDOWN_HOURS:
            return self._hours_to_15m_bars(self.PROFILE_COOLDOWN_HOURS[profile])
        return self.config.cooldown_bars

    @staticmethod
    def _hours_to_15m_bars(hours: int) -> int:
        return max(1, int((hours * 60) / 15))

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




def _first_valid_float(row: dict[str, float | None], *keys: str) -> float | None:
    for key in keys:
        value = _to_optional_float(row.get(key))
        if value is not None:
            return value
    return None


def _first_valid_positive(row: dict[str, float | None], *keys: str) -> float | None:
    value = _first_valid_float(row, *keys)
    if value is None or value <= 0.0:
        return None
    return value


def _first_valid_ratio(row: dict[str, float | None], *keys: str) -> float | None:
    value = _first_valid_float(row, *keys)
    if value is None:
        return None
    if 0.0 <= value <= 1.0:
        return value
    return None

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
