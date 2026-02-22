"""Портфельный state-engine для синхронной обработки символов на таймлайне 15m."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from domain.enums.position_side import PositionSide
from domain.enums.trade_result_type import TradeResultType
from domain.models.candle import Candle
from domain.models.trade_result import TradeResult
from domain.models.trade_signal import TradeSignal
from domain.value_objects.price import Price
from strategy.bee_bite.trade_plan import build_bee_bite_trade_plan, resolve_profile_tp1_share
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
    max_age_range_bars: int = 24
    reclaim_limit_bars: int = 3
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
    break_start_timeline_idx: int | None = None
    break_start_timestamp_ms: int | None = None
    reclaim_bar_timeline_idx: int | None = None
    retest_deadline_timeline_idx: int | None = None
    touched_retest_zone: bool = False
    break_price: float | None = None
    pump_anchor_close: float | None = None
    high_pump: float | None = None
    support: float | None = None
    resistance: float | None = None
    lowest_break: float | None = None
    atr_bg: float | None = None
    signal: TradeSignal | None = None
    atr_pre: float | None = None
    pump_height: float | None = None
    low_before_pump: float | None = None
    t_pump_end_idx: int | None = None
    range_window_end_idx: int | None = None
    core_width: float | None = None
    recent_rows: list[dict[str, float | None]] = field(default_factory=list)
    score_reclaim_row: dict[str, float | None] | None = None
    score_reclaim_timeline_idx: int | None = None
    score_entry_timeline_idx: int | None = None
    score_reclaim_bars: int | None = None


@dataclass(slots=True)
class EntryCandidate:
    symbol: str
    score: float
    signal: TradeSignal
    side: PositionSide
    reclaim_bars: int
    liquidity: float
    reclaim_candle_score: float
    score_trace: dict[str, object]


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
    _last_run_diagnostics: dict[str, object] = field(default_factory=dict)
    _build_score_trace: dict[str, object] = field(default_factory=dict)

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
    PROFILE_TIME_EXIT_HOURS_NO_TP1: dict[str, int] = field(
        default_factory=lambda: {
            "A": 12,
            "B": 8,
            "C": 6,
        }
    )
    IMPULSE_THRESHOLDS: dict[str, float] = field(default_factory=lambda: {"A": 2.5, "B": 2.2, "C": 2.0})
    PROFILE_RANGE_WINDOW: dict[str, int] = field(default_factory=lambda: {"A": 48, "B": 40, "C": 32})
    FIXED_RANGE_WINDOW: int | None = None
    PROFILE_STABILITY_THRESHOLD: dict[str, float] = field(default_factory=lambda: {"A": 0.20, "B": 0.25, "C": 0.30})
    PROFILE_RECLAIM_TIMEOUT: dict[str, int] = field(default_factory=lambda: {"A": 5, "B": 6, "C": 8})
    RESET_REASONS: tuple[str, ...] = (
        "max_age_range",
        "reclaim_timeout",
        "micro_filter_fail",
        "stop_distance_fail",
        "score_fail",
        "topn_fail",
        "break_emergency",
    )

    def run(self, symbol_frames: dict[str, pd.DataFrame]) -> list[TradeResult]:
        self._last_run_diagnostics = {
            "score_threshold": self._resolve_score_threshold(),
            "profile": (self.config.bee_bite_profile_id or "").upper() or None,
            "candidates": [],
            "selected_symbols": [],
            "reset_counters": {reason: 0 for reason in self.RESET_REASONS},
        }
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

            winners, score_failed_symbols = self._pick_top_candidates(candidates)
            winners_symbols = {candidate.symbol for candidate in winners}
            self._record_candidates_diagnostics(candidates=candidates, winners_symbols=winners_symbols)
            for candidate in winners:
                self._activate_candidate(candidate)
            for candidate in candidates:
                if candidate.symbol not in winners_symbols:
                    reject_reason = "score_fail" if candidate.symbol in score_failed_symbols else "topn_fail"
                    self._reject_candidate(candidate.symbol, reason=reject_reason)

        trades.extend(self._close_open_positions(symbol_frames=symbol_frames, timeline=timeline))
        return sorted(trades, key=lambda trade: (trade.exit_timestamp_ms, trade.entry_timestamp_ms))

    def consume_last_run_diagnostics(self) -> dict[str, object]:
        diagnostics = self._last_run_diagnostics.copy()
        self._last_run_diagnostics = {}
        return diagnostics

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
            "spread": _to_optional_float(item.get("spread")),
            "bid_ask_spread": _to_optional_float(item.get("bid_ask_spread")),
            "effective_spread": _to_optional_float(item.get("effective_spread")),
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
        state.recent_rows.append(row.copy())
        if len(state.recent_rows) > 256:
            state.recent_rows = state.recent_rows[-256:]

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
            pump_signal = self._resolve_pump_signal(recent_rows=state.recent_rows, timeline_idx=timeline_idx)
            if pump_signal is not None:
                high_pump, pump_height, atr_pre, atr_bg, low_before_pump, t_pump_end_idx = pump_signal
                state.state = PortfolioState.SEEK_RANGE
                state.high_pump = high_pump
                state.pump_height = pump_height
                state.atr_pre = atr_pre
                state.atr_bg = atr_bg
                state.low_before_pump = low_before_pump
                state.t_pump_end_idx = t_pump_end_idx
                state.range_window_end_idx = None
                state.range_high = None
                state.range_low = None
                state.support = None
                state.resistance = None
                state.age = 0
            elif state.age > self.config.max_age_range_bars:
                self._reset_symbol(symbol=symbol, state=state, reason="max_age_range", timeline_idx=timeline_idx)
            return None

        if state.state == PortfolioState.SEEK_RANGE:
            frozen_range = self._try_freeze_range(state=state, timeline_idx=timeline_idx)
            if frozen_range is not None:
                support, resistance, core_width, window_end_idx = frozen_range
                state.support = support
                state.resistance = resistance
                state.range_low = support
                state.range_high = resistance
                state.core_width = core_width
                state.range_window_end_idx = window_end_idx
                state.state = PortfolioState.RANGE_LOCKED
                state.age = 0
            elif state.age > self.config.max_age_range_bars:
                self._reset_symbol(symbol=symbol, state=state, reason="max_age_range", timeline_idx=timeline_idx)
            return None

        if state.state == PortfolioState.RANGE_LOCKED:
            assert state.support is not None and state.resistance is not None
            if float(row["low"] or close) < state.support:
                state.state = PortfolioState.BREAK_ACTIVE
                state.break_side = PositionSide.LONG
                state.break_price = close
                state.lowest_break = float(row["low"] or close)
                state.break_start_timeline_idx = timeline_idx
                state.break_start_timestamp_ms = int(row["timestamp"])
                state.reclaim_bar_timeline_idx = None
                state.retest_deadline_timeline_idx = None
                state.touched_retest_zone = False
                state.age = 0
            elif state.age > self.config.max_age_range_bars:
                self._reset_symbol(symbol=symbol, state=state, reason="max_age_range", timeline_idx=timeline_idx)
            return None

        if state.state == PortfolioState.BREAK_ACTIVE:
            assert state.break_side is not None and state.break_price is not None
            break_start_idx = state.break_start_timeline_idx if state.break_start_timeline_idx is not None else timeline_idx
            reclaim_anchor_idx = state.reclaim_bar_timeline_idx if state.reclaim_bar_timeline_idx is not None else break_start_idx
            reclaim_bars = max(timeline_idx - reclaim_anchor_idx + 1, 1)
            if (timeline_idx - break_start_idx) > self._resolve_reclaim_limit_bars():
                self._reset_symbol(symbol=symbol, state=state, reason="reclaim_timeout", timeline_idx=timeline_idx)
                return None

            state.lowest_break = min(state.lowest_break or float(row["low"] or close), float(row["low"] or close))
            core_width = float(state.core_width if state.core_width is not None else abs(float((state.resistance or close) - (state.support or close))))
            depth = max((state.support or close) - (state.lowest_break or close), 0.0)
            atr_bg = float(state.atr_bg or 0.0)
            min_depth = self._resolve_min_depth_threshold() * atr_bg
            max_depth = 0.5 * core_width
            if depth > max_depth:
                self._reset_symbol(symbol=symbol, state=state, reason="break_emergency", timeline_idx=timeline_idx)
                return None

            reclaim_confirmed = state.support is not None and close > state.support and depth >= min_depth

            profile = (self.config.bee_bite_profile_id or "").upper()
            if profile == "A" and reclaim_confirmed and state.reclaim_bar_timeline_idx is None:
                state.reclaim_bar_timeline_idx = timeline_idx
                state.retest_deadline_timeline_idx = timeline_idx + 6
                state.touched_retest_zone = False

            if profile == "A" and state.reclaim_bar_timeline_idx is not None:
                if state.retest_deadline_timeline_idx is not None and timeline_idx > state.retest_deadline_timeline_idx:
                    self._reset_symbol(symbol=symbol, state=state, reason="reclaim_timeout", timeline_idx=timeline_idx)
                    return None

                support = float(state.support if state.support is not None else close)
                atr_ref = float(state.atr_bg if state.atr_bg is not None else 0.0)
                zone_upper = support + (0.05 * atr_ref)
                trigger_close = support + (0.10 * atr_ref)
                low = float(row["low"] or close)
                high = float(row["high"] or close)
                touched_zone_now = high >= support and low <= zone_upper
                if touched_zone_now:
                    state.touched_retest_zone = True
                reclaim_confirmed = state.touched_retest_zone and close > trigger_close

            if reclaim_confirmed:
                signal = self._build_signal(symbol=symbol, row=row, side=state.break_side, state=state)
                if signal is None:
                    self._reset_symbol(symbol=symbol, state=state, reason="micro_filter_fail", timeline_idx=timeline_idx)
                    return None
                state.signal = signal
                state.state = PortfolioState.ENTRY_SIGNAL
                state.age = 0
                state.score_reclaim_row = row.copy()
                state.score_reclaim_timeline_idx = timeline_idx
                state.score_entry_timeline_idx = timeline_idx
                state.score_reclaim_bars = reclaim_bars
                score, reclaim_candle_score = self._score_candidate(
                    score_row=state.score_reclaim_row,
                    side=PositionSide.LONG,
                    range_high=state.range_high,
                    range_low=state.range_low,
                    reclaim_bars=int(state.score_reclaim_bars or reclaim_bars),
                    atr_bg=state.atr_bg,
                    score_reclaim_timeline_idx=state.score_reclaim_timeline_idx,
                    score_entry_timeline_idx=state.score_entry_timeline_idx,
                )
                score_trace = self._build_score_trace.copy()
                return EntryCandidate(
                    symbol=symbol,
                    score=score,
                    signal=signal,
                    side=PositionSide.LONG,
                    reclaim_bars=reclaim_bars,
                    liquidity=float(row["volume"] or 0.0),
                    reclaim_candle_score=reclaim_candle_score,
                    score_trace=score_trace,
                )
            return None

        if state.state == PortfolioState.ENTRY_SIGNAL and state.signal is not None:
            score_row = state.score_reclaim_row or row
            reclaim_bars = int(state.score_reclaim_bars or max(state.age, 1))
            score, reclaim_candle_score = self._score_candidate(
                score_row=score_row,
                side=state.signal.position_side,
                range_high=state.range_high,
                range_low=state.range_low,
                reclaim_bars=reclaim_bars,
                atr_bg=state.atr_bg,
                score_reclaim_timeline_idx=state.score_reclaim_timeline_idx,
                score_entry_timeline_idx=state.score_entry_timeline_idx,
            )
            score_trace = self._build_score_trace.copy()
            return EntryCandidate(
                symbol=symbol,
                score=score,
                signal=state.signal,
                side=state.signal.position_side,
                reclaim_bars=reclaim_bars,
                liquidity=float(row["volume"] or 0.0),
                reclaim_candle_score=reclaim_candle_score,
                score_trace=score_trace,
            )

        return None

    def _score_candidate(
        self,
        *,
        score_row: dict[str, float | None],
        side: PositionSide,
        range_high: float | None,
        range_low: float | None,
        reclaim_bars: int,
        atr_bg: float | None,
        score_reclaim_timeline_idx: int | None,
        score_entry_timeline_idx: int | None,
    ) -> tuple[float, float]:
        close = float(score_row["close"] or 0.0)
        high = float(score_row["high"] or 0.0)
        low = float(score_row["low"] or 0.0)
        volume = float(score_row["volume"] or 0.0)

        candle_range = max(high - low, 1e-12)
        close_position = (close - low) / candle_range if side == PositionSide.LONG else (high - close) / candle_range

        score = 0.0
        reclaim_candle_score = 0.0
        score_trace: dict[str, object] = {
            "score_eval_reclaim_timeline_idx": score_reclaim_timeline_idx,
            "score_eval_entry_timeline_idx": score_entry_timeline_idx,
            "score_eval_timestamp_ms": score_row.get("timestamp"),
            "reclaim_speed": 0.0,
            "reclaim_candle_quality": 0.0,
            "reclaim_volume_ratio": 0.0,
            "taker_ratio": 0.0,
            "oi_relation": 0.0,
            "depth_vs_atr": 0.0,
            "range_volume_zscore": 0.0,
            "data_quality_notes": [],
        }
        quality_notes: list[str] = []
        score_trace["data_quality_notes"] = quality_notes

        if reclaim_bars <= 1:
            score_trace["reclaim_speed"] = 2.0
        elif reclaim_bars <= 2:
            score_trace["reclaim_speed"] = 1.0
        score += float(score_trace["reclaim_speed"])

        if close_position >= 0.75:
            reclaim_candle_score = 1.0
        elif close_position < 0.45:
            reclaim_candle_score = -1.0
        score_trace["reclaim_candle_quality"] = reclaim_candle_score
        score += reclaim_candle_score

        avg_volume_range_raw = _first_valid_float(score_row, "avg_volume_range", "avg_range_volume", "range_volume_avg")
        if avg_volume_range_raw is None:
            quality_notes.append("avg_range_volume: данные отсутствуют -> 0 баллов")
        elif avg_volume_range_raw <= 0.0:
            quality_notes.append("avg_range_volume: низкое качество (<=0) -> 0 баллов")
        else:
            reclaim_to_avg = volume / max(avg_volume_range_raw, 1e-12)
            if reclaim_to_avg > 1.5:
                score_trace["reclaim_volume_ratio"] = 1.0
        score += float(score_trace["reclaim_volume_ratio"])

        taker_buy_ratio = _first_valid_float(score_row, "taker_buy_ratio", "taker_ratio")
        if taker_buy_ratio is None:
            quality_notes.append("taker_ratio: данные отсутствуют -> 0 баллов")
        elif not 0.0 <= taker_buy_ratio <= 1.0:
            quality_notes.append("taker_ratio: низкое качество (вне [0,1]) -> 0 баллов")
        elif side == PositionSide.LONG and taker_buy_ratio > 0.55:
            score_trace["taker_ratio"] = 1.0
        elif side == PositionSide.SHORT and taker_buy_ratio < 0.45:
            score_trace["taker_ratio"] = 1.0
        score += float(score_trace["taker_ratio"])

        oi_reclaim = _first_valid_float(score_row, "oi_reclaim", "OI_reclaim")
        oi_break_avg = _first_valid_float(score_row, "oi_break_avg", "OI_break_avg")
        if oi_reclaim is None or oi_break_avg is None:
            quality_notes.append("oi_relation: данные отсутствуют -> 0 баллов")
        elif oi_reclaim <= 0.0 or oi_break_avg <= 0.0:
            quality_notes.append("oi_relation: низкое качество (<=0) -> 0 баллов")
        elif oi_reclaim < oi_break_avg:
            score_trace["oi_relation"] = 1.0
        score += float(score_trace["oi_relation"])

        depth = 0.0
        if range_high is not None and range_low is not None:
            if side == PositionSide.LONG:
                depth = max(range_low - low, 0.0)
            else:
                depth = max(high - range_high, 0.0)
        atr_ref = float(atr_bg) if atr_bg is not None and atr_bg > 0 else None
        if atr_ref is not None and depth > 0.2 * atr_ref:
            score_trace["depth_vs_atr"] = 1.0
        score += float(score_trace["depth_vs_atr"])

        zscore_range_volume = _first_valid_float(score_row, "range_volume_zscore", "volume_range_zscore", "zscore_range_volume")
        if zscore_range_volume is None:
            quality_notes.append("range_volume_zscore: данные отсутствуют -> 0 баллов")
        elif not np.isfinite(zscore_range_volume):
            quality_notes.append("range_volume_zscore: низкое качество (нечисловое) -> 0 баллов")
        elif zscore_range_volume > 0.5:
            score_trace["range_volume_zscore"] = 1.0
        score += float(score_trace["range_volume_zscore"])

        score_trace["total"] = score
        self._build_score_trace = score_trace
        return score, reclaim_candle_score

    def _resolve_score_threshold(self) -> float:
        profile = (self.config.bee_bite_profile_id or "").upper()
        if profile in self.PROFILE_SCORE_THRESHOLDS:
            return self.PROFILE_SCORE_THRESHOLDS[profile]
        return self.PROFILE_SCORE_THRESHOLDS["B"]

    def _record_candidates_diagnostics(self, *, candidates: list[EntryCandidate], winners_symbols: set[str]) -> None:
        diagnostics_candidates = self._last_run_diagnostics.get("candidates")
        if isinstance(diagnostics_candidates, list):
            for candidate in candidates:
                diagnostics_candidates.append(
                    {
                        "symbol": candidate.symbol,
                        "timestamp_ms": candidate.signal.entry_timestamp_ms,
                        "score": candidate.score,
                        "reclaim_bars": candidate.reclaim_bars,
                        "selected": candidate.symbol in winners_symbols,
                        "score_trace": candidate.score_trace,
                    }
                )
        selected_symbols = self._last_run_diagnostics.get("selected_symbols")
        if isinstance(selected_symbols, list):
            selected_symbols.extend(sorted(winners_symbols))

    def _pick_top_candidates(self, candidates: list[EntryCandidate]) -> tuple[list[EntryCandidate], set[str]]:
        if not candidates:
            return [], set()
        score_threshold = self._resolve_score_threshold()
        filtered = [candidate for candidate in candidates if candidate.score >= score_threshold]
        score_failed_symbols = {candidate.symbol for candidate in candidates if candidate.score < score_threshold}
        if not filtered:
            return [], score_failed_symbols
        ordered = sorted(
            filtered,
            key=lambda c: (-c.score, c.reclaim_bars, -c.reclaim_candle_score, -c.liquidity, c.symbol),
        )
        return ordered[: self.config.top_n], score_failed_symbols

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
            self._reject_candidate(candidate.symbol, reason="topn_fail")
            return
        if not self._risk_manager.check_stop_distance_by_atr(signal=candidate.signal, atr_bg=float(candidate.signal.atr_bg or 0.0)):
            self._reject_candidate(candidate.symbol, reason="stop_distance_fail")
            return
        size = self._risk_manager.calc_position_size(signal=candidate.signal)
        sim.max_bars_in_trade = self._resolve_time_exit_bars()
        sim.register_signal(candidate.signal, size=size)
        state.state = PortfolioState.IN_TRADE
        state.age = 0

    def _reject_candidate(self, symbol: str, *, reason: str) -> None:
        state = self._states[symbol]
        self._reset_symbol(symbol=symbol, state=state, reason=reason)

    def _reset_symbol(self, *, symbol: str, state: SymbolState, reason: str, timeline_idx: int | None = None) -> None:
        self._record_reset_reason(reason)
        self._reset_state(state)
        self._set_cooldown(symbol=symbol, timeline_idx=timeline_idx)

    def _record_reset_reason(self, reason: str) -> None:
        reset_counters = self._last_run_diagnostics.get("reset_counters")
        if not isinstance(reset_counters, dict):
            return
        reset_counters[reason] = int(reset_counters.get(reason, 0)) + 1

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

    def _resolve_time_exit_bars(self) -> int | None:
        profile = (self.config.bee_bite_profile_id or "").upper()
        if profile in self.PROFILE_TIME_EXIT_HOURS_NO_TP1:
            return self._hours_to_15m_bars(self.PROFILE_TIME_EXIT_HOURS_NO_TP1[profile])
        return self.config.t_max_in_trade

    def _resolve_reclaim_limit_bars(self) -> int:
        profile = (self.config.bee_bite_profile_id or "").upper()
        if profile in self.PROFILE_RECLAIM_TIMEOUT:
            return int(self.PROFILE_RECLAIM_TIMEOUT[profile])
        return self.config.reclaim_limit_bars

    def _resolve_range_window_len(self) -> int:
        if self.FIXED_RANGE_WINDOW is not None:
            return max(6, int(self.FIXED_RANGE_WINDOW))
        profile = (self.config.bee_bite_profile_id or "").upper()
        return max(6, int(self.PROFILE_RANGE_WINDOW.get(profile, 40)))

    def _resolve_stability_threshold(self) -> float:
        profile = (self.config.bee_bite_profile_id or "").upper()
        return float(self.PROFILE_STABILITY_THRESHOLD.get(profile, 0.25))

    def _resolve_min_depth_threshold(self) -> float:
        profile = (self.config.bee_bite_profile_id or "").upper()
        mapping = {"A": 0.15, "B": 0.12, "C": 0.10}
        return float(mapping.get(profile, 0.12))

    def _resolve_pump_signal(
        self,
        *,
        recent_rows: list[dict[str, float | None]],
        timeline_idx: int,
    ) -> tuple[float, float, float, float, float, int] | None:
        pump_window = 6
        pre_pump_len = 14
        atr_bg_window_len = 96
        if len(recent_rows) < (pump_window + pre_pump_len + atr_bg_window_len + 1):
            return None
        recent = recent_rows[-pump_window:]
        before_pump = recent_rows[-pump_window - 1]
        atr_window = recent_rows[-pump_window - 1 - pre_pump_len : -pump_window - 1]
        atr_bg_window = recent_rows[-pump_window - atr_bg_window_len : -pump_window]

        tr_values: list[float] = []
        for idx, item in enumerate(atr_window):
            prev_close = float(atr_window[idx - 1]["close"] if idx > 0 else recent_rows[-pump_window - 2]["close"] or 0.0)
            high = float(item["high"] or 0.0)
            low = float(item["low"] or 0.0)
            tr_values.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        atr_pre = float(np.mean(tr_values)) if tr_values else 0.0
        if atr_pre <= 0:
            return None

        atr_bg_values = [float(item["atr_bg"]) for item in atr_bg_window if item.get("atr_bg") is not None and float(item["atr_bg"] or 0.0) > 0]
        atr_bg = float(np.median(atr_bg_values)) if atr_bg_values else atr_pre
        if atr_bg <= 0:
            return None

        high_pump = max(float(item["high"] or 0.0) for item in recent)
        low_before_pump = float(before_pump["low"] or 0.0)
        pump_height = high_pump - low_before_pump
        impulse_threshold = self.IMPULSE_THRESHOLDS.get((self.config.bee_bite_profile_id or "").upper(), 2.2) * atr_pre
        if pump_height < impulse_threshold:
            return None
        return high_pump, pump_height, atr_pre, atr_bg, low_before_pump, timeline_idx

    def _try_freeze_range(self, *, state: SymbolState, timeline_idx: int) -> tuple[float, float, float, int] | None:
        if state.t_pump_end_idx is None or state.pump_height is None or state.atr_bg is None:
            return None
        window_len = self._resolve_range_window_len()
        start_idx = state.t_pump_end_idx + 1
        if timeline_idx - start_idx + 1 < window_len:
            return None
        local_end = len(state.recent_rows) - 1
        local_start = local_end - window_len + 1
        if local_start < 0:
            return None
        window = state.recent_rows[local_start : local_end + 1]
        lows = np.array([float(item["low"] or 0.0) for item in window])
        highs = np.array([float(item["high"] or 0.0) for item in window])
        p10 = float(np.quantile(lows, 0.10))
        p85 = float(np.quantile(highs, 0.85))
        p90 = float(np.quantile(highs, 0.90))
        core_width = max(p90 - p10, 0.0)
        width_limit = min(0.45 * state.pump_height, 7.0 * state.atr_bg)
        if core_width > width_limit:
            return None
        p10_last6: list[float] = []
        for shift in range(6):
            rolling_end = local_end - (5 - shift)
            rolling_start = rolling_end - window_len + 1
            if rolling_start < 0:
                return None
            rolling = state.recent_rows[rolling_start : rolling_end + 1]
            rolling_lows = np.array([float(item["low"] or 0.0) for item in rolling])
            p10_last6.append(float(np.quantile(rolling_lows, 0.10)))
        if (max(p10_last6) - min(p10_last6)) > (self._resolve_stability_threshold() * state.atr_bg):
            return None
        support_band_high = p10 + (0.2 * state.atr_bg)
        support_cluster = lows[(lows >= p10) & (lows <= support_band_high)]
        support = float(np.median(support_cluster)) if support_cluster.size >= 2 else p10
        resistance = p85
        return support, resistance, core_width, timeline_idx

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

        buffer = max(0.1 * atr_bg, 0.001 * entry)
        if side == PositionSide.LONG:
            lowest_break = float(state.lowest_break if state.lowest_break is not None else support)
            stop = lowest_break - buffer
            low_before_pump = None
        else:
            lowest_break = float(state.lowest_break if state.lowest_break is not None else resistance)
            stop = lowest_break + buffer
            low_before_pump = high_pump if high_pump < entry else entry - atr_bg

        plan = build_bee_bite_trade_plan(
            side=side,
            entry_price=entry,
            atr_bg=atr_bg,
            stop_loss=stop,
            support=support,
            resistance=resistance,
            high_pump=high_pump,
            low_before_pump=low_before_pump,
        )
        if plan is None or plan.stop_distance < (0.3 * atr_bg):
            return None

        tp1 = plan.tp1
        tp2 = plan.tp2

        signal = TradeSignal(
            symbol=symbol,
            position_side=side,
            entry_price=Price(entry),
            entry_timestamp_ms=int(row["timestamp"]),
            formation_timestamp_ms=int(row["timestamp"]),
            stop_loss=Price(plan.stop_loss),
            take_profit_1=Price(tp1),
            take_profit_2=Price(tp2),
            breakout_timestamp_ms=int(row["timestamp"]),
            retest_timestamp_ms=int(row["timestamp"]),
            atr_bg=atr_bg,
            high_pump=high_pump,
            tp1_close_ratio=resolve_profile_tp1_share(self.config.bee_bite_profile_id),
        )
        return signal

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
        state.break_start_timeline_idx = None
        state.break_start_timestamp_ms = None
        state.reclaim_bar_timeline_idx = None
        state.retest_deadline_timeline_idx = None
        state.touched_retest_zone = False
        state.signal = None
        state.pump_anchor_close = None
        state.high_pump = None
        state.support = None
        state.resistance = None
        state.lowest_break = None
        state.atr_bg = None
        state.atr_pre = None
        state.pump_height = None
        state.low_before_pump = None
        state.t_pump_end_idx = None
        state.range_window_end_idx = None
        state.core_width = None
        state.score_reclaim_row = None
        state.score_reclaim_timeline_idx = None
        state.score_entry_timeline_idx = None
        state.score_reclaim_bars = None




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
