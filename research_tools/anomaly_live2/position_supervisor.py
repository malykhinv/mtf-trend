"""Verified position supervisor for anomaly live2.

The supervisor only manages positions that P321 already recorded as protected:
actual entry fill is known and the current stop is visible on the exchange. It
never substitutes candle/ticker prices for fills. TP1 can leave a protected
runner remainder only after a replacement stop is visible and the old stop is
verified gone. Current-OI exit checks compare fresh current-OI snapshots against
the protected entry snapshot, not against a signal-time proxy.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from math import isfinite
from statistics import median

from data.exchanges.ccxt_types import ExchangeOrderFill

from .clock import utc_now_ms
from .contracts import Live2Component, Live2Event, Live2Severity
from .execution import Live2ExecutionEngine, Live2ExecutionExchange, Live2ProtectedPosition
from .market_data.candles import Live2Candle
from .state import SymbolState, SymbolStateStore


@dataclass(frozen=True, slots=True)
class Live2PositionSupervisorConfig:
    """Strict defaults for generation-0 position supervision."""

    monitor_interval_ms: int = 1_000
    tp1_close_fraction: float = 0.5
    breakeven_stop_offset_pct: float = 0.0
    flat_position_abs_epsilon: float = 1e-12
    min_remaining_amount: float = 1e-12
    stop_trigger_settle_attempts: int = 3
    stop_trigger_settle_sleep_seconds: float = 0.5
    early_exit_enabled: bool = True
    early_exit_timeframe_ms: int = 5_000
    early_exit_min_hold_candles: int = 6
    early_exit_stall_candles: int = 12
    early_exit_min_mfe_r: float = 0.25
    early_exit_flow_collapse_quote_ratio: float = 0.45
    early_exit_seller_taker_buy_share_max: float = 0.45
    early_exit_oi_up_min_change_pct: float = 0.0
    early_exit_current_oi_down_min_change_pct: float = 0.0

    def __post_init__(self) -> None:
        if self.monitor_interval_ms <= 0:
            raise ValueError("monitor_interval_ms must be > 0")
        if not 0.0 < self.tp1_close_fraction <= 1.0:
            raise ValueError("tp1_close_fraction must be in (0, 1]")
        if self.breakeven_stop_offset_pct < 0:
            raise ValueError("breakeven_stop_offset_pct must be >= 0")
        if self.flat_position_abs_epsilon < 0:
            raise ValueError("flat_position_abs_epsilon must be >= 0")
        if self.min_remaining_amount < 0:
            raise ValueError("min_remaining_amount must be >= 0")
        if self.stop_trigger_settle_attempts < 0:
            raise ValueError("stop_trigger_settle_attempts must be >= 0")
        if self.stop_trigger_settle_sleep_seconds < 0:
            raise ValueError("stop_trigger_settle_sleep_seconds must be >= 0")
        if self.early_exit_timeframe_ms <= 0:
            raise ValueError("early_exit_timeframe_ms must be > 0")
        if self.early_exit_min_hold_candles <= 0:
            raise ValueError("early_exit_min_hold_candles must be > 0")
        if self.early_exit_stall_candles < self.early_exit_min_hold_candles:
            raise ValueError("early_exit_stall_candles must be >= early_exit_min_hold_candles")
        if self.early_exit_min_mfe_r < 0:
            raise ValueError("early_exit_min_mfe_r must be >= 0")
        if not 0.0 <= self.early_exit_flow_collapse_quote_ratio <= 1.0:
            raise ValueError("early_exit_flow_collapse_quote_ratio must be in [0, 1]")
        if not 0.0 <= self.early_exit_seller_taker_buy_share_max <= 1.0:
            raise ValueError("early_exit_seller_taker_buy_share_max must be in [0, 1]")
        if self.early_exit_oi_up_min_change_pct < 0.0:
            raise ValueError("early_exit_oi_up_min_change_pct must be >= 0")
        if self.early_exit_current_oi_down_min_change_pct < 0.0:
            raise ValueError("early_exit_current_oi_down_min_change_pct must be >= 0")


@dataclass(frozen=True, slots=True)
class Live2PositionSupervisorAction:
    """One supervisor verdict/action for artifacts."""

    event_type: str
    symbol: str
    position_id: str
    severity: Live2Severity
    message: str
    data: dict[str, object] = field(default_factory=dict)

    def as_event(self) -> Live2Event:
        return Live2Event(
            event_type=self.event_type,
            component=Live2Component.EXECUTION,
            severity=self.severity,
            symbol=self.symbol,
            message=self.message,
            data={"position_id": self.position_id, **self.data},
        )


@dataclass(slots=True)
class Live2PositionSupervisorCycleResult:
    checked_positions: int = 0
    actions: list[Live2PositionSupervisorAction] = field(default_factory=list)
    integrity_error_count: int = 0
    tp1_close_count: int = 0
    early_exit_close_count: int = 0
    final_close_count: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "checked_positions": self.checked_positions,
            "actions_total": len(self.actions),
            "integrity_error_count": self.integrity_error_count,
            "tp1_close_count": self.tp1_close_count,
            "early_exit_close_count": self.early_exit_close_count,
            "final_close_count": self.final_close_count,
        }


class Live2PositionSupervisor:
    """Supervise verified live2 positions without fake fill inference."""

    def __init__(
        self,
        *,
        exchange_client: Live2ExecutionExchange | None,
        execution_engine: Live2ExecutionEngine,
        config: Live2PositionSupervisorConfig | None = None,
    ) -> None:
        self.exchange_client = exchange_client
        self.execution_engine = execution_engine
        self.config = config or Live2PositionSupervisorConfig()
        self._total_cycles = 0
        self._total_checked_positions = 0
        self._total_tp1_closes = 0
        self._total_early_exit_closes = 0
        self._total_final_closes = 0
        self._total_integrity_errors = 0
        self._last_cycle_at_ms = 0
        self._last_error = ""

    @property
    def ready(self) -> bool:
        return self.exchange_client is not None and self.execution_engine.ready and not self._last_error

    def run_cycle(self, state_store: SymbolStateStore) -> Live2PositionSupervisorCycleResult:
        now_ms = utc_now_ms()
        if now_ms - self._last_cycle_at_ms < self.config.monitor_interval_ms:
            return Live2PositionSupervisorCycleResult()
        self._last_cycle_at_ms = now_ms
        self._total_cycles += 1
        result = Live2PositionSupervisorCycleResult()
        if self.exchange_client is None:
            self._last_error = "exchange_client_not_provided"
            return result

        for position in self.execution_engine.protected_positions_snapshot():
            result.checked_positions += 1
            self._total_checked_positions += 1
            state = state_store.get_or_create(position.symbol)
            action = self._supervise_position(position=position, state=state, now_ms=now_ms)
            if action is None:
                continue
            result.actions.append(action)
            if action.event_type in {
                "position_tp1_partial_close_verified",
                "position_tp1_full_close_verified",
                "position_tp1_filled_be_stop_verified",
            }:
                result.tp1_close_count += 1
                self._total_tp1_closes += 1
            if action.event_type == "position_early_exit_full_close_verified":
                result.early_exit_close_count += 1
                self._total_early_exit_closes += 1
            if action.event_type in {
                "position_tp1_full_close_verified",
                "position_early_exit_full_close_verified",
                "position_final_close_verified",
            }:
                result.final_close_count += 1
                self._total_final_closes += 1
            elif action.severity == Live2Severity.ERROR:
                result.integrity_error_count += 1
                self._total_integrity_errors += 1
        return result

    def status(self) -> dict[str, object]:
        return {
            "status": "ready" if self.ready else "not_ready",
            "ready": self.ready,
            "last_error": self._last_error,
            "config": {
                "monitor_interval_ms": self.config.monitor_interval_ms,
                "tp1_close_fraction": self.config.tp1_close_fraction,
                "breakeven_stop_offset_pct": self.config.breakeven_stop_offset_pct,
                "flat_position_abs_epsilon": self.config.flat_position_abs_epsilon,
                "min_remaining_amount": self.config.min_remaining_amount,
                "stop_trigger_settle_attempts": self.config.stop_trigger_settle_attempts,
                "stop_trigger_settle_sleep_seconds": self.config.stop_trigger_settle_sleep_seconds,
                "early_exit_enabled": self.config.early_exit_enabled,
                "early_exit_timeframe_ms": self.config.early_exit_timeframe_ms,
                "early_exit_min_hold_candles": self.config.early_exit_min_hold_candles,
                "early_exit_stall_candles": self.config.early_exit_stall_candles,
                "early_exit_min_mfe_r": self.config.early_exit_min_mfe_r,
                "early_exit_flow_collapse_quote_ratio": self.config.early_exit_flow_collapse_quote_ratio,
                "early_exit_seller_taker_buy_share_max": self.config.early_exit_seller_taker_buy_share_max,
                "early_exit_oi_up_min_change_pct": self.config.early_exit_oi_up_min_change_pct,
                "early_exit_current_oi_down_min_change_pct": self.config.early_exit_current_oi_down_min_change_pct,
            },
            "total_cycles": self._total_cycles,
            "total_checked_positions": self._total_checked_positions,
            "total_tp1_closes": self._total_tp1_closes,
            "total_early_exit_closes": self._total_early_exit_closes,
            "total_final_closes": self._total_final_closes,
            "total_integrity_errors": self._total_integrity_errors,
        }

    def _supervise_position(
        self,
        *,
        position: Live2ProtectedPosition,
        state: SymbolState,
        now_ms: int,
    ) -> Live2PositionSupervisorAction | None:
        latest_price = _latest_stream_price(state)
        exchange_amount = self._fetch_position_amount(position.symbol)
        if exchange_amount is None:
            return self._integrity_error(
                position=position,
                reason="position_supervisor_position_fetch_failed",
                emergency_amount=position.remaining_amount,
            )
        if abs(exchange_amount) <= self.config.flat_position_abs_epsilon:
            stop_gone_error = self._ensure_old_stop_gone_after_flat(position)
            if stop_gone_error is not None:
                return self._integrity_error(
                    position=position,
                    reason=stop_gone_error,
                    emergency_amount=0.0,
                    details={
                        "exchange_position_amount": exchange_amount,
                        "expected_contract": "flat_position_without_visible_orphan_stop",
                        "stop_client_order_id": position.stop_client_order_id,
                        "stop_order_id": position.stop_order_id,
                    },
                )
            closed = self.execution_engine.remove_protected_position(position.symbol) or position
            return Live2PositionSupervisorAction(
                event_type="position_final_close_verified",
                symbol=closed.symbol,
                position_id=closed.position_id,
                severity=Live2Severity.INFO,
                message="exchange position is flat and protected stop is gone; final close verified",
                data={
                    "reason": "exchange_position_flat_stop_gone",
                    "exchange_position_amount": exchange_amount,
                    "old_stop_order_id": position.stop_order_id,
                    "old_stop_client_order_id": position.stop_client_order_id,
                    "position": replace(
                        closed,
                        status="closed_verified_exchange_flat_stop_gone",
                        remaining_amount=0.0,
                        closed_at_ms=now_ms,
                        close_reason="exchange_position_flat_stop_gone",
                        last_supervised_ms=now_ms,
                    ).as_dict(),
                },
            )

        if self.execution_engine.verify_stop_visible(position.symbol, position.stop_client_order_id) is None:
            settled = self._wait_for_stop_trigger_settle(position=position, exchange_amount=exchange_amount, now_ms=now_ms)
            if settled is not None:
                return settled
            return self._integrity_error(
                position=position,
                reason="protected_position_stop_not_visible_during_supervision",
                emergency_amount=exchange_amount,
                details={"stop_client_order_id": position.stop_client_order_id, "stop_order_id": position.stop_order_id},
            )

        managed_statuses = {"protected_initial_stop_verified", "tp1_partial_protected_stop_verified"}
        if position.status in managed_statuses and _positive_finite(latest_price):
            if position.status == "protected_initial_stop_verified" and position.tp1_price > 0 and float(latest_price) >= position.tp1_price:
                return self._close_tp1_full_position(position=position, exchange_amount=exchange_amount, now_ms=now_ms)
            early_exit = self._early_exit_decision(position=position, state=state, latest_price=float(latest_price), now_ms=now_ms)
            if early_exit.get("should_exit") is True:
                return self._close_early_exit_full_position(
                    position=position,
                    exchange_amount=exchange_amount,
                    now_ms=now_ms,
                    early_exit=early_exit,
                )
            if position.status == "tp1_partial_protected_stop_verified":
                trail = self._structural_trail_decision(position=position, state=state, latest_price=float(latest_price), now_ms=now_ms)
                if trail.get("should_trail") is True:
                    return self._trail_structural_stop(
                        position=position,
                        exchange_amount=exchange_amount,
                        now_ms=now_ms,
                        trail=trail,
                    )

        self.execution_engine.replace_protected_position(replace(position, last_supervised_ms=now_ms))
        return None

    def _early_exit_decision(
        self,
        *,
        position: Live2ProtectedPosition,
        state: SymbolState,
        latest_price: float,
        now_ms: int,
    ) -> dict[str, object]:
        if not self.config.early_exit_enabled:
            return {"should_exit": False, "reason": "early_exit_disabled"}
        risk_abs = float(position.entry_fill_price) - float(position.stop_price)
        if not isfinite(risk_abs) or risk_abs <= 0.0:
            return {"should_exit": False, "reason": "early_exit_invalid_risk"}
        candles = _post_entry_closed_candles(
            state=state,
            opened_at_ms=int(position.opened_at_ms),
            timeframe_ms=int(self.config.early_exit_timeframe_ms),
        )
        closed_count = len(candles)
        if closed_count < int(self.config.early_exit_min_hold_candles):
            return {
                "should_exit": False,
                "reason": "early_exit_min_hold_not_reached",
                "post_entry_closed_5s_count": closed_count,
            }

        first_flow = candles[: min(3, closed_count)]
        last_two = candles[-2:] if closed_count >= 2 else candles
        last_three = candles[-3:] if closed_count >= 3 else candles
        previous_three = candles[-6:-3] if closed_count >= 6 else candles[: max(1, closed_count - 3)]
        first_quote_avg = _avg([item.quote_volume for item in first_flow])
        first_trade_avg = _avg([item.number_of_trades for item in first_flow])
        last_two_quote_avg = _avg([item.quote_volume for item in last_two])
        last_two_trade_avg = _avg([item.number_of_trades for item in last_two])
        last_two_taker_share = _taker_buy_share(last_two)
        last_candle = candles[-1]
        last_candle_taker_share = _taker_buy_share((last_candle,))
        previous_quote_median = _median([item.quote_volume for item in previous_three])
        seller_volume_ratio = (
            float(last_candle.quote_volume) / previous_quote_median
            if previous_quote_median is not None and previous_quote_median > 0.0
            else None
        )
        flow_collapse = (
            first_quote_avg is not None
            and first_quote_avg > 0.0
            and last_two_quote_avg is not None
            and last_two_quote_avg <= first_quote_avg * float(self.config.early_exit_flow_collapse_quote_ratio)
            and (
                first_trade_avg is None
                or first_trade_avg <= 0.0
                or last_two_trade_avg is None
                or last_two_trade_avg <= first_trade_avg * 0.70
            )
        )
        seller_arrived = (
            last_candle.close < last_candle.open
            and last_candle_taker_share is not None
            and last_candle_taker_share <= float(self.config.early_exit_seller_taker_buy_share_max)
            and (seller_volume_ratio is None or seller_volume_ratio >= 0.70)
        )
        max_high = max(float(item.high) for item in candles)
        prior_high = max((float(item.high) for item in candles[:-3]), default=max_high)
        recent_high = max(float(item.high) for item in last_three)
        high_stalled = closed_count >= 4 and recent_high <= prior_high
        current_r = (float(latest_price) - float(position.entry_fill_price)) / risk_abs
        mfe_r = (max_high - float(position.entry_fill_price)) / risk_abs
        oi_up = (
            state.oi_status == "ok"
            and state.oi_change_pct_3x5m is not None
            and isfinite(float(state.oi_change_pct_3x5m))
            and float(state.oi_change_pct_3x5m) > float(self.config.early_exit_oi_up_min_change_pct)
        )
        oi_5m_change_from_entry_pct = None
        if (
            state.oi_open_interest is not None
            and position.entry_5m_oi_open_interest is not None
            and isfinite(float(state.oi_open_interest))
            and isfinite(float(position.entry_5m_oi_open_interest))
            and float(position.entry_5m_oi_open_interest) > 0.0
        ):
            oi_5m_change_from_entry_pct = (float(state.oi_open_interest) / float(position.entry_5m_oi_open_interest)) - 1.0
        current_oi_change_from_entry_pct = None
        current_oi_entry_comparable = (
            position.entry_current_oi_status == "ok"
            and state.current_oi_status == "ok"
            and position.entry_current_oi_open_interest is not None
            and state.current_oi_open_interest is not None
            and isfinite(float(position.entry_current_oi_open_interest))
            and isfinite(float(state.current_oi_open_interest))
            and float(position.entry_current_oi_open_interest) > 0.0
        )
        if current_oi_entry_comparable:
            current_oi_change_from_entry_pct = (float(state.current_oi_open_interest) / float(position.entry_current_oi_open_interest)) - 1.0
        current_oi_down_since_entry = (
            current_oi_change_from_entry_pct is not None
            and current_oi_change_from_entry_pct < -float(self.config.early_exit_current_oi_down_min_change_pct)
        )
        oi_up_nonprogress = (
            oi_up
            and closed_count >= int(self.config.early_exit_stall_candles)
            and current_r <= 0.10
            and (last_two_taker_share is None or last_two_taker_share <= 0.55 or last_candle.close < last_candle.open)
        )
        flow_exhausted_after_mfe = (
            mfe_r >= float(self.config.early_exit_min_mfe_r)
            and current_r <= 0.25
            and high_stalled
            and (flow_collapse or (last_two_taker_share is not None and last_two_taker_share <= 0.45))
        )
        seller_pressure_after_mfe = (
            mfe_r >= float(self.config.early_exit_min_mfe_r)
            and current_r <= 0.20
            and seller_arrived
        )
        stall_without_progress = (
            closed_count >= int(self.config.early_exit_stall_candles)
            and mfe_r < float(self.config.early_exit_min_mfe_r)
            and current_r <= 0.05
            and (flow_collapse or seller_arrived or oi_up)
        )
        current_oi_down_flow_exhausted_after_mfe = (
            current_oi_down_since_entry
            and mfe_r >= float(self.config.early_exit_min_mfe_r)
            and current_r <= 0.35
            and high_stalled
            and (flow_collapse or seller_arrived or (last_two_taker_share is not None and last_two_taker_share <= 0.45))
        )
        reason = ""
        if oi_up_nonprogress:
            reason = "early_exit_oi_up_price_not_progressing"
        elif current_oi_down_flow_exhausted_after_mfe:
            reason = "early_exit_current_oi_down_flow_exhausted_after_mfe"
        elif seller_pressure_after_mfe:
            reason = "early_exit_seller_pressure_after_mfe"
        elif flow_exhausted_after_mfe:
            reason = "early_exit_flow_exhausted_after_mfe"
        elif stall_without_progress:
            reason = "early_exit_stall_without_progress"
        return {
            "should_exit": bool(reason),
            "reason": reason or "early_exit_hold",
            "post_entry_closed_5s_count": closed_count,
            "hold_ms": max(0, int(now_ms) - int(position.opened_at_ms)),
            "latest_price": float(latest_price),
            "entry_fill_price": float(position.entry_fill_price),
            "stop_price": float(position.stop_price),
            "tp1_price": float(position.tp1_price),
            "current_r": current_r,
            "mfe_r": mfe_r,
            "max_high": max_high,
            "first3_quote_avg": first_quote_avg,
            "last2_quote_avg": last_two_quote_avg,
            "first3_trade_avg": first_trade_avg,
            "last2_trade_avg": last_two_trade_avg,
            "last2_taker_buy_share": last_two_taker_share,
            "last_candle_taker_buy_share": last_candle_taker_share,
            "seller_volume_ratio": seller_volume_ratio,
            "flow_collapse": flow_collapse,
            "seller_arrived": seller_arrived,
            "high_stalled": high_stalled,
            "oi_status": state.oi_status,
            "oi_change_pct_3x5m": state.oi_change_pct_3x5m,
            "oi_up": oi_up,
            "entry_5m_oi_open_interest": position.entry_5m_oi_open_interest,
            "entry_5m_oi_latest_timestamp_ms": position.entry_5m_oi_latest_timestamp_ms,
            "current_5m_oi_open_interest": state.oi_open_interest,
            "current_5m_oi_latest_timestamp_ms": state.oi_latest_timestamp_ms,
            "post_entry_oi_5m_change_pct_from_entry_5m": oi_5m_change_from_entry_pct,
            "entry_current_oi_open_interest": position.entry_current_oi_open_interest,
            "entry_current_oi_timestamp_ms": position.entry_current_oi_timestamp_ms,
            "entry_current_oi_last_seen_ms": position.entry_current_oi_last_seen_ms,
            "entry_current_oi_source": position.entry_current_oi_source,
            "entry_current_oi_status": position.entry_current_oi_status,
            "entry_current_oi_reason": position.entry_current_oi_reason,
            "current_oi_open_interest": state.current_oi_open_interest,
            "current_oi_timestamp_ms": state.current_oi_timestamp_ms,
            "current_oi_last_seen_ms": state.current_oi_last_seen_ms,
            "current_oi_source": state.current_oi_source,
            "current_oi_status": state.current_oi_status,
            "current_oi_reason": state.current_oi_reason,
            "current_oi_entry_comparable": current_oi_entry_comparable,
            "post_entry_current_oi_change_pct_from_entry": current_oi_change_from_entry_pct,
            "current_oi_down_since_entry": current_oi_down_since_entry,
            "early_exit_current_oi_down_min_change_pct": self.config.early_exit_current_oi_down_min_change_pct,
            "source_flow_window_ms": position.source_flow_window_ms,
            "source_flow_quote_per_second": position.source_flow_quote_per_second,
            "source_flow_trades_per_second": position.source_flow_trades_per_second,
            "source_flow_quote_ratio": position.source_flow_quote_ratio,
            "source_flow_trade_ratio": position.source_flow_trade_ratio,
        }

    def _structural_trail_decision(
        self,
        *,
        position: Live2ProtectedPosition,
        state: SymbolState,
        latest_price: float,
        now_ms: int,
    ) -> dict[str, object]:
        risk_abs = float(position.entry_fill_price) - float(position.stop_price)
        if not isfinite(risk_abs) or risk_abs <= 0.0:
            return {"should_trail": False, "reason": "structural_trail_invalid_risk"}
        candles = _post_entry_closed_candles(
            state=state,
            opened_at_ms=int(position.opened_at_ms),
            timeframe_ms=int(self.config.early_exit_timeframe_ms),
        )
        if len(candles) < max(8, int(self.config.early_exit_min_hold_candles)):
            return {"should_trail": False, "reason": "structural_trail_not_enough_closed_5s", "post_entry_closed_5s_count": len(candles)}
        recent = candles[-3:]
        structural_low = min(float(item.low) for item in recent)
        candidate_stop = structural_low * (1.0 - 0.0005)
        min_step = max(float(latest_price) * 0.0005, 1e-12)
        should_trail = candidate_stop > float(position.stop_price) + min_step and candidate_stop < float(latest_price)
        return {
            "should_trail": should_trail,
            "reason": "structural_trail_recent_3x5s_swing_low" if should_trail else "structural_trail_hold",
            "post_entry_closed_5s_count": len(candles),
            "latest_price": float(latest_price),
            "current_stop_price": float(position.stop_price),
            "candidate_stop_price": candidate_stop,
            "structural_low": structural_low,
            "structure_window_candles": 3,
            "hold_ms": max(0, int(now_ms) - int(position.opened_at_ms)),
        }

    def _wait_for_stop_trigger_settle(
        self,
        *,
        position: Live2ProtectedPosition,
        exchange_amount: float,
        now_ms: int,
    ) -> Live2PositionSupervisorAction | None:
        """Verify a stop-trigger transition before declaring unprotected exposure.

        On Binance futures a stop-market algo can disappear from the open-algo
        endpoint while the exchange is settling the triggered reduce-only market
        order. That intermediate state is not safe to ignore, but treating the
        first invisible-stop read as a permanent integrity failure also halts
        live after a normal stop fill. We wait briefly and accept only two
        explicit outcomes: the stop becomes visible again, or the exchange
        position is flat and the old stop is gone.
        """

        attempts = int(self.config.stop_trigger_settle_attempts)
        if attempts <= 0:
            return None
        last_amount = exchange_amount
        for _ in range(attempts):
            if self.config.stop_trigger_settle_sleep_seconds > 0:
                time.sleep(float(self.config.stop_trigger_settle_sleep_seconds))
            visible_stop = self.execution_engine.verify_stop_visible(position.symbol, position.stop_client_order_id)
            if visible_stop is not None:
                self.execution_engine.replace_protected_position(replace(position, last_supervised_ms=now_ms))
                return Live2PositionSupervisorAction(
                    event_type="position_stop_visibility_restored",
                    symbol=position.symbol,
                    position_id=position.position_id,
                    severity=Live2Severity.INFO,
                    message="protected stop became visible again during trigger-settle verification",
                    data={
                        "reason": "stop_visibility_restored_after_transient_invisible_read",
                        "exchange_position_amount": last_amount,
                        "stop_client_order_id": position.stop_client_order_id,
                        "stop_order_id": position.stop_order_id,
                    },
                )
            refreshed_amount = self._fetch_position_amount(position.symbol)
            if refreshed_amount is None:
                return None
            last_amount = refreshed_amount
            if abs(refreshed_amount) > self.config.flat_position_abs_epsilon:
                continue
            stop_gone_error = self._ensure_old_stop_gone_after_flat(position)
            if stop_gone_error is not None:
                return self._integrity_error(
                    position=position,
                    reason=stop_gone_error,
                    emergency_amount=0.0,
                    details={
                        "exchange_position_amount": refreshed_amount,
                        "expected_contract": "flat_position_without_visible_orphan_stop",
                        "stop_client_order_id": position.stop_client_order_id,
                        "stop_order_id": position.stop_order_id,
                    },
                )
            closed = self.execution_engine.remove_protected_position(position.symbol) or position
            return Live2PositionSupervisorAction(
                event_type="position_final_close_verified",
                symbol=closed.symbol,
                position_id=closed.position_id,
                severity=Live2Severity.INFO,
                message="protected stop trigger settled: exchange position is flat and stop is gone",
                data={
                    "reason": "stop_trigger_settled_exchange_flat_stop_gone",
                    "exchange_position_amount": refreshed_amount,
                    "old_stop_order_id": position.stop_order_id,
                    "old_stop_client_order_id": position.stop_client_order_id,
                    "position": replace(
                        closed,
                        status="closed_verified_stop_trigger_exchange_flat_stop_gone",
                        remaining_amount=0.0,
                        closed_at_ms=now_ms,
                        close_reason="stop_trigger_settled_exchange_flat_stop_gone",
                        last_supervised_ms=now_ms,
                    ).as_dict(),
                },
            )
        return None

    def _close_tp1_full_position(
        self,
        *,
        position: Live2ProtectedPosition,
        exchange_amount: float,
        now_ms: int,
    ) -> Live2PositionSupervisorAction:
        close_fraction = float(self.config.tp1_close_fraction)
        close_amount = float(exchange_amount) if close_fraction >= 1.0 else float(exchange_amount) * close_fraction
        if close_amount <= self.config.min_remaining_amount:
            return self._integrity_error(
                position=position,
                reason="tp1_full_close_amount_too_small",
                emergency_amount=exchange_amount,
                details={"computed_close_amount": close_amount, "tp1_close_fraction": close_fraction},
            )
        assert self.exchange_client is not None
        tp1_client_order_id = self.execution_engine.make_client_order_id(
            prefix="l2tp1",
            symbol=position.symbol,
            timestamp_ms=now_ms,
        )
        try:
            fill = self.exchange_client.create_market_order_with_fill(
                position.symbol,
                "sell",
                close_amount,
                reduce_only=True,
                client_order_id=tp1_client_order_id,
            )
        except Exception as exc:
            self.execution_engine.mark_exchange_error()
            return self._integrity_error(
                position=position,
                reason=f"tp1_full_reduce_only_close_failed:{type(exc).__name__}:{exc}",
                emergency_amount=exchange_amount,
                details={"tp1_client_order_id": tp1_client_order_id, "close_amount": close_amount},
            )
        if not _valid_fill(fill):
            return self._integrity_error(
                position=position,
                reason="tp1_full_close_fill_missing_positive_price_or_amount",
                emergency_amount=exchange_amount,
                details={"tp1_client_order_id": tp1_client_order_id, "fill": _fill_dict(fill)},
            )

        remaining_amount = self._fetch_position_amount(position.symbol)
        if remaining_amount is None:
            return self._integrity_error(
                position=position,
                reason="post_tp1_full_close_position_fetch_failed",
                emergency_amount=max(exchange_amount - float(fill.filled_amount), 0.0),
                details={"tp1_fill": _fill_dict(fill)},
            )
        if close_fraction >= 1.0 and remaining_amount > self.config.flat_position_abs_epsilon:
            return self._integrity_error(
                position=position,
                reason="tp1_full_close_did_not_flatten_exchange_position",
                emergency_amount=remaining_amount,
                details={
                    "tp1_fill": _fill_dict(fill),
                    "exchange_position_amount_after_tp1": remaining_amount,
                    "expected_contract": "tp1_full_position_close",
                },
            )

        if close_fraction < 1.0 and remaining_amount > self.config.flat_position_abs_epsilon:
            replacement = self._replace_stop_for_remaining(
                position=position,
                remaining_amount=remaining_amount,
                stop_price=float(position.stop_price),
                now_ms=now_ms,
                reason="tp1_partial_resize_stop_to_remaining_amount",
            )
            if isinstance(replacement, Live2PositionSupervisorAction):
                return replacement
            new_stop_order_id, new_stop_client_order_id = replacement
            realized_pnl = (float(fill.average_price) - position.entry_fill_price) * float(fill.filled_amount)
            updated = replace(
                position,
                status="tp1_partial_protected_stop_verified",
                amount=remaining_amount,
                remaining_amount=remaining_amount,
                stop_order_id=new_stop_order_id,
                stop_client_order_id=new_stop_client_order_id,
                tp1_close_fraction=close_fraction,
                tp1_closed_amount=float(fill.filled_amount),
                tp1_fill_price=float(fill.average_price),
                tp1_order_id=fill.order_id,
                tp1_client_order_id=tp1_client_order_id,
                tp1_closed_at_ms=int(fill.timestamp_ms),
                realized_pnl_usdt=float(position.realized_pnl_usdt) + realized_pnl,
                last_supervised_ms=now_ms,
            )
            self.execution_engine.replace_protected_position(updated)
            return Live2PositionSupervisorAction(
                event_type="position_tp1_partial_close_verified",
                symbol=updated.symbol,
                position_id=updated.position_id,
                severity=Live2Severity.INFO,
                message="TP1 partial reduce-only fill verified and remaining stop resized",
                data={
                    "reason": "tp1_partial_close_verified_remaining_stop_resized",
                    "tp1_fill": _fill_dict(fill),
                    "old_stop_order_id": position.stop_order_id,
                    "old_stop_client_order_id": position.stop_client_order_id,
                    "new_stop_order_id": new_stop_order_id,
                    "new_stop_client_order_id": new_stop_client_order_id,
                    "exchange_position_amount": remaining_amount,
                    "realized_pnl_usdt": realized_pnl,
                    "position": updated.as_dict(),
                },
            )

        cancel_result = self._cancel_old_stop_and_verify_gone(position)
        if cancel_result is not None:
            return self._integrity_error(
                position=position,
                reason=cancel_result,
                emergency_amount=0.0,
                details={
                    "tp1_fill": _fill_dict(fill),
                    "exchange_position_amount_after_tp1": remaining_amount,
                    "expected_contract": "flat_position_without_orphan_stop",
                },
            )

        removed = self.execution_engine.remove_protected_position(position.symbol) or position
        realized_pnl = (float(fill.average_price) - removed.entry_fill_price) * float(fill.filled_amount)
        closed = replace(
            removed,
            status="closed_verified_tp1_full",
            amount=0.0,
            remaining_amount=0.0,
            tp1_close_fraction=self.config.tp1_close_fraction,
            tp1_closed_amount=float(fill.filled_amount),
            tp1_fill_price=float(fill.average_price),
            tp1_order_id=fill.order_id,
            tp1_client_order_id=tp1_client_order_id,
            tp1_closed_at_ms=int(fill.timestamp_ms),
            realized_pnl_usdt=float(removed.realized_pnl_usdt) + realized_pnl,
            closed_at_ms=now_ms,
            close_reason="tp1_full_close_verified" if close_fraction >= 1.0 else "tp1_partial_request_flattened_position",
            last_supervised_ms=now_ms,
        )
        return Live2PositionSupervisorAction(
            event_type="position_tp1_full_close_verified",
            symbol=closed.symbol,
            position_id=closed.position_id,
            severity=Live2Severity.INFO,
            message="TP1 full-position reduce-only fill verified and initial stop cancelled",
            data={
                "reason": "tp1_full_close_verified",
                "tp1_fill": _fill_dict(fill),
                "old_stop_order_id": position.stop_order_id,
                "old_stop_client_order_id": position.stop_client_order_id,
                "exchange_position_amount": remaining_amount,
                "realized_pnl_usdt": float(removed.realized_pnl_usdt) + realized_pnl,
                "position": closed.as_dict(),
            },
        )

    def _close_early_exit_full_position(
        self,
        *,
        position: Live2ProtectedPosition,
        exchange_amount: float,
        now_ms: int,
        early_exit: dict[str, object],
    ) -> Live2PositionSupervisorAction:
        close_amount = float(exchange_amount)
        if close_amount <= self.config.min_remaining_amount:
            return self._integrity_error(
                position=position,
                reason="early_exit_full_close_amount_too_small",
                emergency_amount=exchange_amount,
                details={"computed_close_amount": close_amount, "early_exit": early_exit},
            )
        assert self.exchange_client is not None
        client_order_id = self.execution_engine.make_client_order_id(
            prefix="l2ex",
            symbol=position.symbol,
            timestamp_ms=now_ms,
        )
        try:
            fill = self.exchange_client.create_market_order_with_fill(
                position.symbol,
                "sell",
                close_amount,
                reduce_only=True,
                client_order_id=client_order_id,
            )
        except Exception as exc:
            self.execution_engine.mark_exchange_error()
            return self._integrity_error(
                position=position,
                reason=f"early_exit_full_reduce_only_close_failed:{type(exc).__name__}:{exc}",
                emergency_amount=exchange_amount,
                details={"early_exit_client_order_id": client_order_id, "close_amount": close_amount, "early_exit": early_exit},
            )
        if not _valid_fill(fill):
            return self._integrity_error(
                position=position,
                reason="early_exit_full_close_fill_missing_positive_price_or_amount",
                emergency_amount=exchange_amount,
                details={"early_exit_client_order_id": client_order_id, "fill": _fill_dict(fill), "early_exit": early_exit},
            )

        remaining_amount = self._fetch_position_amount(position.symbol)
        if remaining_amount is None:
            return self._integrity_error(
                position=position,
                reason="post_early_exit_full_close_position_fetch_failed",
                emergency_amount=max(exchange_amount - float(fill.filled_amount), 0.0),
                details={"early_exit_fill": _fill_dict(fill), "early_exit": early_exit},
            )
        if remaining_amount > self.config.flat_position_abs_epsilon:
            return self._integrity_error(
                position=position,
                reason="early_exit_full_close_did_not_flatten_exchange_position",
                emergency_amount=remaining_amount,
                details={
                    "early_exit_fill": _fill_dict(fill),
                    "early_exit": early_exit,
                    "exchange_position_amount_after_early_exit": remaining_amount,
                    "expected_contract": "early_exit_full_position_close",
                },
            )

        cancel_result = self._cancel_old_stop_and_verify_gone(position)
        if cancel_result is not None:
            return self._integrity_error(
                position=position,
                reason=cancel_result,
                emergency_amount=0.0,
                details={
                    "early_exit_fill": _fill_dict(fill),
                    "early_exit": early_exit,
                    "exchange_position_amount_after_early_exit": remaining_amount,
                    "expected_contract": "flat_position_without_orphan_stop",
                },
            )

        removed = self.execution_engine.remove_protected_position(position.symbol) or position
        realized_pnl = (float(fill.average_price) - removed.entry_fill_price) * float(fill.filled_amount)
        closed = replace(
            removed,
            status="closed_verified_early_exit_full",
            amount=0.0,
            remaining_amount=0.0,
            tp1_close_fraction=removed.tp1_close_fraction,
            tp1_closed_amount=removed.tp1_closed_amount,
            tp1_fill_price=removed.tp1_fill_price,
            tp1_order_id=removed.tp1_order_id,
            tp1_client_order_id=removed.tp1_client_order_id,
            tp1_closed_at_ms=removed.tp1_closed_at_ms,
            realized_pnl_usdt=float(removed.realized_pnl_usdt) + realized_pnl,
            closed_at_ms=now_ms,
            close_reason=str(early_exit.get("reason") or "early_exit_full_close_verified"),
            last_supervised_ms=now_ms,
        )
        return Live2PositionSupervisorAction(
            event_type="position_early_exit_full_close_verified",
            symbol=closed.symbol,
            position_id=closed.position_id,
            severity=Live2Severity.INFO,
            message="early-exit full-position reduce-only fill verified and initial stop cancelled",
            data={
                "reason": str(early_exit.get("reason") or "early_exit_full_close_verified"),
                "early_exit": early_exit,
                "early_exit_fill": _fill_dict(fill),
                "old_stop_order_id": position.stop_order_id,
                "old_stop_client_order_id": position.stop_client_order_id,
                "exchange_position_amount": remaining_amount,
                "realized_pnl_usdt": float(removed.realized_pnl_usdt) + realized_pnl,
                "position": closed.as_dict(),
            },
        )

    def _replace_stop_for_remaining(
        self,
        *,
        position: Live2ProtectedPosition,
        remaining_amount: float,
        stop_price: float,
        now_ms: int,
        reason: str,
    ) -> tuple[str, str] | Live2PositionSupervisorAction:
        if remaining_amount <= self.config.min_remaining_amount:
            return self._integrity_error(
                position=position,
                reason=f"{reason}:remaining_amount_too_small",
                emergency_amount=remaining_amount,
                details={"remaining_amount": remaining_amount, "stop_price": stop_price},
            )
        assert self.exchange_client is not None
        new_stop_client_order_id = self.execution_engine.make_client_order_id(
            prefix="l2sr",
            symbol=position.symbol,
            timestamp_ms=now_ms,
        )
        try:
            stop_payload = self.exchange_client.create_stop_market_order(
                position.symbol,
                "sell",
                float(remaining_amount),
                float(stop_price),
                client_order_id=new_stop_client_order_id,
            )
        except Exception as exc:
            return self._integrity_error(
                position=position,
                reason=f"{reason}:replacement_stop_submit_failed:{type(exc).__name__}:{exc}",
                emergency_amount=remaining_amount,
                details={"remaining_amount": remaining_amount, "stop_price": stop_price},
            )
        visible = self.execution_engine.verify_stop_visible(position.symbol, new_stop_client_order_id)
        if visible is None:
            return self._integrity_error(
                position=position,
                reason=f"{reason}:replacement_stop_not_visible",
                emergency_amount=remaining_amount,
                details={"remaining_amount": remaining_amount, "stop_price": stop_price},
            )
        cancel_result = self._cancel_old_stop_and_verify_gone(position)
        if cancel_result is not None:
            return self._integrity_error(
                position=position,
                reason=f"{reason}:{cancel_result}",
                emergency_amount=remaining_amount,
                details={"remaining_amount": remaining_amount, "stop_price": stop_price},
            )
        return _extract_order_id(visible) or _extract_order_id(stop_payload) or new_stop_client_order_id, new_stop_client_order_id

    def _trail_structural_stop(
        self,
        *,
        position: Live2ProtectedPosition,
        exchange_amount: float,
        now_ms: int,
        trail: dict[str, object],
    ) -> Live2PositionSupervisorAction:
        candidate_stop = _float_or_none(trail.get("candidate_stop_price"))
        if candidate_stop is None or candidate_stop <= 0.0:
            return self._integrity_error(
                position=position,
                reason="structural_trail_candidate_stop_invalid",
                emergency_amount=exchange_amount,
                details={"trail": trail},
            )
        replacement = self._replace_stop_for_remaining(
            position=position,
            remaining_amount=float(exchange_amount),
            stop_price=float(candidate_stop),
            now_ms=now_ms,
            reason="structural_trail_replace_stop",
        )
        if isinstance(replacement, Live2PositionSupervisorAction):
            return replacement
        new_stop_order_id, new_stop_client_order_id = replacement
        updated = replace(
            position,
            amount=float(exchange_amount),
            remaining_amount=float(exchange_amount),
            stop_price=float(candidate_stop),
            stop_order_id=new_stop_order_id,
            stop_client_order_id=new_stop_client_order_id,
            last_supervised_ms=now_ms,
        )
        self.execution_engine.replace_protected_position(updated)
        return Live2PositionSupervisorAction(
            event_type="position_structural_stop_trail_verified",
            symbol=updated.symbol,
            position_id=updated.position_id,
            severity=Live2Severity.INFO,
            message="structural trailing stop replacement verified",
            data={
                "reason": "structural_trail_stop_verified",
                "trail": trail,
                "old_stop_order_id": position.stop_order_id,
                "old_stop_client_order_id": position.stop_client_order_id,
                "new_stop_order_id": new_stop_order_id,
                "new_stop_client_order_id": new_stop_client_order_id,
                "exchange_position_amount": float(exchange_amount),
                "position": updated.as_dict(),
            },
        )

    def _cancel_old_stop_and_verify_gone(self, position: Live2ProtectedPosition) -> str | None:
        assert self.exchange_client is not None
        if not position.stop_order_id:
            return "old_stop_order_id_missing_before_replacement"
        try:
            self.exchange_client.cancel_stop_order(position.symbol, position.stop_order_id)
        except Exception as exc:
            self.execution_engine.mark_exchange_error()
            return f"old_stop_cancel_failed:{type(exc).__name__}:{exc}"
        still_visible = self.execution_engine.verify_stop_visible(position.symbol, position.stop_client_order_id)
        if still_visible is not None:
            return "old_stop_still_visible_after_cancel"
        return None

    def _ensure_old_stop_gone_after_flat(self, position: Live2ProtectedPosition) -> str | None:
        assert self.exchange_client is not None
        visible_stop = self.execution_engine.verify_stop_visible(position.symbol, position.stop_client_order_id)
        if visible_stop is None:
            return None
        order_id = _extract_order_id(visible_stop) or position.stop_order_id
        if not order_id:
            return "flat_position_visible_stop_without_order_id"
        try:
            self.exchange_client.cancel_stop_order(position.symbol, order_id)
        except Exception as exc:
            self.execution_engine.mark_exchange_error()
            return f"flat_position_orphan_stop_cancel_failed:{type(exc).__name__}:{exc}"
        still_visible = self.execution_engine.verify_stop_visible(position.symbol, position.stop_client_order_id)
        if still_visible is not None:
            return "flat_position_orphan_stop_still_visible_after_cancel"
        return None

    def _fetch_position_amount(self, symbol: str) -> float | None:
        assert self.exchange_client is not None
        try:
            amount = float(self.exchange_client.fetch_symbol_position_amount(symbol))
        except Exception as exc:
            self.execution_engine.mark_exchange_error()
            self._last_error = f"fetch_symbol_position_amount_failed:{type(exc).__name__}:{exc}"
            return None
        if not isfinite(amount):
            self._last_error = "exchange_position_amount_is_not_finite"
            return None
        return amount

    def _integrity_error(
        self,
        *,
        position: Live2ProtectedPosition,
        reason: str,
        emergency_amount: float | None,
        details: dict[str, object] | None = None,
    ) -> Live2PositionSupervisorAction:
        self._last_error = reason
        self.execution_engine.halt_due_to_position_integrity(reason)
        emergency_close_status = self.execution_engine.attempt_emergency_close(position.symbol, emergency_amount)
        return Live2PositionSupervisorAction(
            event_type="position_integrity_error",
            symbol=position.symbol,
            position_id=position.position_id,
            severity=Live2Severity.ERROR,
            message=reason,
            data={
                "reason": reason,
                "emergency_close_status": emergency_close_status,
                "emergency_amount": emergency_amount,
                "position": position.as_dict(),
                "details": details or {},
            },
        )


def _latest_stream_price(state: SymbolState) -> float | None:
    if state.aggtrade_last_price is not None and state.aggtrade_last_price > 0:
        return state.aggtrade_last_price
    if state.ticker_last_price is not None and state.ticker_last_price > 0:
        return state.ticker_last_price
    return None


def _post_entry_closed_candles(
    *,
    state: SymbolState,
    opened_at_ms: int,
    timeframe_ms: int,
) -> tuple[Live2Candle, ...]:
    ring = state.candle_book.rings.get(int(timeframe_ms))
    if ring is None:
        return ()
    first_full_open_ms = ((int(opened_at_ms) + int(timeframe_ms) - 1) // int(timeframe_ms)) * int(timeframe_ms)
    return tuple(
        candle
        for candle in ring.closed_snapshot()
        if int(candle.open_time_ms) >= first_full_open_ms and int(candle.close_time_ms) > int(opened_at_ms)
    )


def _avg(values: list[float | int]) -> float | None:
    finite_values = [float(item) for item in values if isfinite(float(item))]
    if not finite_values:
        return None
    return sum(finite_values) / len(finite_values)


def _median(values: list[float | int]) -> float | None:
    finite_values = [float(item) for item in values if isfinite(float(item))]
    if not finite_values:
        return None
    return float(median(finite_values))


def _taker_buy_share(candles: tuple[Live2Candle, ...]) -> float | None:
    quote = sum(float(item.quote_volume) for item in candles)
    if quote <= 0.0:
        return None
    taker_quote = sum(float(item.taker_buy_quote_volume) for item in candles)
    return taker_quote / quote


def _positive_finite(value: object) -> bool:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return isfinite(parsed) and parsed > 0.0


def _float_or_none(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not isfinite(parsed):
        return None
    return parsed


def _valid_fill(fill: ExchangeOrderFill) -> bool:
    return _positive_finite(fill.average_price) and _positive_finite(fill.filled_amount)


def _fill_dict(fill: ExchangeOrderFill) -> dict[str, object]:
    return {
        "order_id": fill.order_id,
        "status": fill.status,
        "timestamp_ms": fill.timestamp_ms,
        "average_price": fill.average_price,
        "filled_amount": fill.filled_amount,
        "cost": fill.cost,
        "fee_cost": fill.fee_cost,
        "fee_currency": fill.fee_currency,
    }


def _extract_order_id(payload: dict[str, object]) -> str | None:
    value = payload.get("id") or payload.get("orderId")
    info = payload.get("info")
    if value is None and isinstance(info, dict):
        value = info.get("algoId") or info.get("orderId") or info.get("clientAlgoId")
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
