"""Registered pump-long (continuation riding) execution protocol.

Every economic constant here is frozen BEFORE outcomes are inspected
(phase-0 registration). Thresholds that depend on data (d_max, the weekly
p-quantile entry rule) are intentionally absent: they are frozen later from
the development period only and recorded in the research config, never here.
"""

from __future__ import annotations

from dataclasses import dataclass

PUMP_LONG_OUTCOME_SCHEMA_VERSION = "pump_long_outcomes_v1"

# Initial-stop variants registered a priori (exactly two; no mining).
STOP_VARIANT_EVENT_BASE = "event_base"
STOP_VARIANT_CONFIRMED_SWING_LOW = "confirmed_swing_low"
PUMP_LONG_STOP_VARIANTS: tuple[str, ...] = (
    STOP_VARIANT_EVENT_BASE,
    STOP_VARIANT_CONFIRMED_SWING_LOW,
)


@dataclass(frozen=True, slots=True)
class PumpLongExecutionSpec:
    """Frozen mechanical execution contract for the long side."""

    # Entry: next 1m open after the closed decision bar, pessimistic slippage.
    # Mirrors next_1m_open_structural_policy_with_pessimistic_slippage_v2.
    entry_slippage_bps: float = 5.0
    exit_slippage_bps: float = 5.0
    # 20 bps round trip primary (consistent with the prior short-side study);
    # cost stress (10/30) is applied in the EV layer, not by re-simulating.
    fee_per_side_bps: float = 10.0
    # Trailing: confirmed swing low with the registry-standard confirmation.
    swing_confirmation_bars: int = 2
    # Primary stop trigger matches the close-race label philosophy.
    stop_trigger_close_beyond: bool = True
    # Eligibility ("train has left"): entries stop once price has CONFIRMED
    # closes below retrace_50 = base + retrace_kill_fraction*(running_high-base)
    # for a consecutive run of at least
    # max(retrace_kill_min_minutes, retrace_kill_elapsed_fraction * pump_elapsed_min).
    retrace_kill_fraction: float = 0.5
    retrace_kill_min_minutes: int = 3
    retrace_kill_elapsed_fraction: float = 0.10

    def __post_init__(self) -> None:
        if self.fee_per_side_bps < 0 or self.entry_slippage_bps < 0 or self.exit_slippage_bps < 0:
            raise ValueError("costs must be non-negative")
        if self.swing_confirmation_bars < 1:
            raise ValueError("swing_confirmation_bars must be positive")
        if not 0.0 < self.retrace_kill_fraction < 1.0:
            raise ValueError("retrace_kill_fraction must lie in (0, 1)")
        if self.retrace_kill_min_minutes < 1:
            raise ValueError("retrace_kill_min_minutes must be positive")
        if not 0.0 < self.retrace_kill_elapsed_fraction < 1.0:
            raise ValueError("retrace_kill_elapsed_fraction must lie in (0, 1)")


PUMP_LONG_EXECUTION_SPEC = PumpLongExecutionSpec()

__all__ = [
    "PUMP_LONG_EXECUTION_SPEC",
    "PUMP_LONG_OUTCOME_SCHEMA_VERSION",
    "PUMP_LONG_STOP_VARIANTS",
    "PumpLongExecutionSpec",
    "STOP_VARIANT_CONFIRMED_SWING_LOW",
    "STOP_VARIANT_EVENT_BASE",
]
