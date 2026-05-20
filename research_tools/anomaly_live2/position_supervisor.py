"""Verified position supervisor for anomaly live2.

The supervisor only manages positions that P321 already recorded as protected:
actual entry fill is known and the current stop is visible on the exchange. It
never substitutes candle/ticker prices for fills. TP1 is a full-position
reduce-only market close: no runner remainder and no BE-stop replacement. Final
TP1 close is accepted only after the exchange position is flat and the old
initial stop is cancelled/verified gone.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from math import isfinite

from data.exchanges.ccxt_types import ExchangeOrderFill

from .clock import utc_now_ms
from .contracts import Live2Component, Live2Event, Live2Severity
from .execution import Live2ExecutionEngine, Live2ExecutionExchange, Live2ProtectedPosition
from .state import SymbolState, SymbolStateStore


@dataclass(frozen=True, slots=True)
class Live2PositionSupervisorConfig:
    """Strict defaults for generation-0 position supervision."""

    monitor_interval_ms: int = 1_000
    tp1_close_fraction: float = 1.0
    breakeven_stop_offset_pct: float = 0.0
    flat_position_abs_epsilon: float = 1e-12
    min_remaining_amount: float = 1e-12

    def __post_init__(self) -> None:
        if self.monitor_interval_ms <= 0:
            raise ValueError("monitor_interval_ms must be > 0")
        if self.tp1_close_fraction != 1.0:
            raise ValueError("tp1_close_fraction must be exactly 1.0 for live2 full-TP1 contract")
        if self.breakeven_stop_offset_pct < 0:
            raise ValueError("breakeven_stop_offset_pct must be >= 0")
        if self.flat_position_abs_epsilon < 0:
            raise ValueError("flat_position_abs_epsilon must be >= 0")
        if self.min_remaining_amount < 0:
            raise ValueError("min_remaining_amount must be >= 0")


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
    final_close_count: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "checked_positions": self.checked_positions,
            "actions_total": len(self.actions),
            "integrity_error_count": self.integrity_error_count,
            "tp1_close_count": self.tp1_close_count,
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
            if action.event_type in {"position_tp1_full_close_verified", "position_tp1_filled_be_stop_verified"}:
                result.tp1_close_count += 1
                self._total_tp1_closes += 1
            if action.event_type in {"position_tp1_full_close_verified", "position_final_close_verified"}:
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
            },
            "total_cycles": self._total_cycles,
            "total_checked_positions": self._total_checked_positions,
            "total_tp1_closes": self._total_tp1_closes,
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
            closed = self.execution_engine.remove_protected_position(position.symbol) or position
            return Live2PositionSupervisorAction(
                event_type="position_final_close_verified",
                symbol=closed.symbol,
                position_id=closed.position_id,
                severity=Live2Severity.INFO,
                message="exchange position is flat; final close verified",
                data={
                    "reason": "exchange_position_flat",
                    "exchange_position_amount": exchange_amount,
                    "position": replace(
                        closed,
                        status="closed_verified_exchange_flat",
                        remaining_amount=0.0,
                        closed_at_ms=now_ms,
                        close_reason="exchange_position_flat",
                        last_supervised_ms=now_ms,
                    ).as_dict(),
                },
            )

        if self.execution_engine.verify_stop_visible(position.symbol, position.stop_client_order_id) is None:
            return self._integrity_error(
                position=position,
                reason="protected_position_stop_not_visible_during_supervision",
                emergency_amount=exchange_amount,
                details={"stop_client_order_id": position.stop_client_order_id, "stop_order_id": position.stop_order_id},
            )

        if position.status == "protected_initial_stop_verified" and _positive_finite(latest_price):
            if position.tp1_price > 0 and float(latest_price) >= position.tp1_price:
                return self._close_tp1_full_position(position=position, exchange_amount=exchange_amount, now_ms=now_ms)

        self.execution_engine.replace_protected_position(replace(position, last_supervised_ms=now_ms))
        return None

    def _close_tp1_full_position(
        self,
        *,
        position: Live2ProtectedPosition,
        exchange_amount: float,
        now_ms: int,
    ) -> Live2PositionSupervisorAction:
        close_amount = float(exchange_amount)
        if close_amount <= self.config.min_remaining_amount:
            return self._integrity_error(
                position=position,
                reason="tp1_full_close_amount_too_small",
                emergency_amount=exchange_amount,
                details={"computed_close_amount": close_amount},
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
        if remaining_amount > self.config.flat_position_abs_epsilon:
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
            realized_pnl_usdt=realized_pnl,
            closed_at_ms=now_ms,
            close_reason="tp1_full_close_verified",
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
                "realized_pnl_usdt": realized_pnl,
                "position": closed.as_dict(),
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


def _positive_finite(value: object) -> bool:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return isfinite(parsed) and parsed > 0.0


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
