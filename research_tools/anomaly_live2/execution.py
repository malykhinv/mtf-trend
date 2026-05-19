"""Strict execution boundary for anomaly live2.

Generation 0 does not place orders yet. This boundary still performs the first
real exchange safety checks that must exist before order placement can be
enabled: account preflight and pre-entry position inspection. If the symbol is
already open on the exchange, execution is rejected. If the symbol is flat, the
result is an explicit `rejected_execution_order_placement_not_implemented` gate
instead of a fake success.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Protocol, runtime_checkable

from data.exchanges.ccxt_types import ExchangeLiveAccountPreflight

from .clock import utc_now_ms
from .state import SymbolState


@runtime_checkable
class Live2ExecutionExchange(Protocol):
    """Typed exchange boundary required by live2 execution generation 0."""

    def fetch_live_account_preflight(self) -> ExchangeLiveAccountPreflight:
        """Return explicit account-mode snapshot for real-order live trading."""
        ...

    def fetch_symbol_position_amount(self, symbol: str) -> float:
        """Return signed exchange position amount for one symbol."""
        ...


@dataclass(frozen=True, slots=True)
class Live2ExecutionConfig:
    """Execution readiness contract for generation 0."""

    max_existing_position_abs_amount: float = 0.0

    def __post_init__(self) -> None:
        if self.max_existing_position_abs_amount < 0:
            raise ValueError("max_existing_position_abs_amount must be >= 0")


@dataclass(frozen=True, slots=True)
class Live2ExecutionPreflightResult:
    status: str
    reason: str
    exchange: str = ""
    position_mode: str = ""
    hedge_mode_enabled: bool | None = None
    checked_at_ms: int = 0

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "ready": self.ready,
            "reason": self.reason,
            "exchange": self.exchange,
            "position_mode": self.position_mode,
            "hedge_mode_enabled": self.hedge_mode_enabled,
            "checked_at_ms": self.checked_at_ms,
        }


@dataclass(frozen=True, slots=True)
class Live2ExecutionResult:
    verdict: str
    reason: str
    checked_at_ms: int
    pre_position_amount: float | None = None
    exchange_boundary_status: str = ""
    order_placement_status: str = "not_attempted"

    def as_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "checked_at_ms": self.checked_at_ms,
            "pre_position_amount": self.pre_position_amount,
            "exchange_boundary_status": self.exchange_boundary_status,
            "order_placement_status": self.order_placement_status,
        }


class Live2ExecutionEngine:
    """Strict generation-0 execution boundary.

    This class intentionally has no candle/ticker fallback and no local-memory
    position assumption. Every selected signal must pass an exchange position
    read before future order placement can be enabled.
    """

    def __init__(
        self,
        *,
        exchange_client: Live2ExecutionExchange | None,
        config: Live2ExecutionConfig | None = None,
    ) -> None:
        self.exchange_client = exchange_client
        self.config = config or Live2ExecutionConfig()
        self.preflight_result: Live2ExecutionPreflightResult = Live2ExecutionPreflightResult(
            status="not_checked",
            reason="execution_preflight_not_checked",
            checked_at_ms=0,
        )
        self._total_execute_calls = 0
        self._total_rejected_existing_position = 0
        self._total_rejected_not_implemented = 0
        self._total_exchange_errors = 0

    def preflight(self) -> Live2ExecutionPreflightResult:
        checked_at_ms = utc_now_ms()
        if self.exchange_client is None:
            self.preflight_result = Live2ExecutionPreflightResult(
                status="not_ready",
                reason="exchange_client_not_provided",
                checked_at_ms=checked_at_ms,
            )
            return self.preflight_result
        try:
            snapshot = self.exchange_client.fetch_live_account_preflight()
        except Exception as exc:
            self._total_exchange_errors += 1
            self.preflight_result = Live2ExecutionPreflightResult(
                status="not_ready",
                reason=f"exchange_preflight_failed:{type(exc).__name__}:{exc}",
                checked_at_ms=checked_at_ms,
            )
            return self.preflight_result
        self.preflight_result = Live2ExecutionPreflightResult(
            status="ready",
            reason="exchange_account_preflight_ok",
            exchange=snapshot.exchange,
            position_mode=snapshot.position_mode,
            hedge_mode_enabled=snapshot.hedge_mode_enabled,
            checked_at_ms=checked_at_ms,
        )
        return self.preflight_result

    def execute_selected(self, *, state: SymbolState) -> Live2ExecutionResult:
        self._total_execute_calls += 1
        checked_at_ms = utc_now_ms()
        if not self.preflight_result.ready:
            self._total_exchange_errors += 1
            return Live2ExecutionResult(
                verdict="rejected_execution_boundary_not_ready",
                reason=self.preflight_result.reason or "execution_preflight_not_ready",
                checked_at_ms=checked_at_ms,
                exchange_boundary_status=self.preflight_result.status,
            )
        if self.exchange_client is None:
            self._total_exchange_errors += 1
            return Live2ExecutionResult(
                verdict="rejected_execution_boundary_not_ready",
                reason="exchange_client_not_provided",
                checked_at_ms=checked_at_ms,
                exchange_boundary_status="not_ready",
            )
        try:
            pre_position_amount = float(self.exchange_client.fetch_symbol_position_amount(state.symbol))
        except Exception as exc:
            self._total_exchange_errors += 1
            return Live2ExecutionResult(
                verdict="rejected_execution_position_precheck_failed",
                reason=f"fetch_symbol_position_amount_failed:{type(exc).__name__}:{exc}",
                checked_at_ms=checked_at_ms,
                exchange_boundary_status="error",
            )
        if not isfinite(pre_position_amount):
            self._total_exchange_errors += 1
            return Live2ExecutionResult(
                verdict="rejected_execution_invalid_position_amount",
                reason="exchange_position_amount_is_not_finite",
                checked_at_ms=checked_at_ms,
                pre_position_amount=pre_position_amount,
                exchange_boundary_status="error",
            )
        if abs(pre_position_amount) > self.config.max_existing_position_abs_amount:
            self._total_rejected_existing_position += 1
            return Live2ExecutionResult(
                verdict="rejected_existing_exchange_position",
                reason="pre_entry_exchange_position_amount_is_not_flat",
                checked_at_ms=checked_at_ms,
                pre_position_amount=pre_position_amount,
                exchange_boundary_status="ready",
            )
        self._total_rejected_not_implemented += 1
        return Live2ExecutionResult(
            verdict="rejected_execution_order_placement_not_implemented",
            reason="generation_0_verified_fill_and_stop_order_path_not_implemented",
            checked_at_ms=checked_at_ms,
            pre_position_amount=pre_position_amount,
            exchange_boundary_status="ready",
            order_placement_status="not_implemented",
        )

    def status(self) -> dict[str, object]:
        return {
            "status": "generation_0_execution_boundary_active",
            "ready": self.preflight_result.ready,
            "preflight": self.preflight_result.as_dict(),
            "order_placement_status": "not_implemented_until_verified_fill_and_stop_path_exists",
            "total_execute_calls": self._total_execute_calls,
            "total_rejected_existing_position": self._total_rejected_existing_position,
            "total_rejected_not_implemented": self._total_rejected_not_implemented,
            "total_exchange_errors": self._total_exchange_errors,
        }
