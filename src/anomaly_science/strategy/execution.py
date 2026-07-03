from __future__ import annotations

from anomaly_science.contracts.execution_policy import (
    BarrierTrigger,
    PositionSide,
    StrategyExecutionPolicies,
    StructuralAnchor,
    StructuralStopPolicy,
    StructuralTakeProfitPolicy,
)


PUMP_FADE_EXECUTION_POLICIES = StrategyExecutionPolicies(
    policy_version="pump_fade_structural_execution_v1",
    stop_policies=(
        StructuralStopPolicy(
            policy_id="short_main_high_close_then_swing_high_trail",
            side=PositionSide.SHORT,
            initial_anchor=StructuralAnchor.EVENT_MAIN_HIGH,
            trigger=BarrierTrigger.CLOSE_BEYOND,
            trailing_anchor=StructuralAnchor.CONFIRMED_SWING_HIGH,
            swing_confirmation_bars=2,
        ),
    ),
    take_profit_policies=(
        StructuralTakeProfitPolicy(
            policy_id="short_partial_at_event_base",
            side=PositionSide.SHORT,
            anchor=StructuralAnchor.EVENT_BASE,
            trigger=BarrierTrigger.TOUCH,
            close_fraction_grid=(0.25, 0.5, 0.75, 1.0),
        ),
    ),
)


GENERIC_ANOMALY_EXECUTION_POLICIES = StrategyExecutionPolicies(
    policy_version="generic_anomaly_structural_execution_v1",
    stop_policies=(
        StructuralStopPolicy(
            policy_id="long_running_low_close_then_swing_low_trail",
            side=PositionSide.LONG,
            initial_anchor=StructuralAnchor.RUNNING_LOW,
            trigger=BarrierTrigger.CLOSE_BEYOND,
            trailing_anchor=StructuralAnchor.CONFIRMED_SWING_LOW,
        ),
        StructuralStopPolicy(
            policy_id="short_running_high_close_then_swing_high_trail",
            side=PositionSide.SHORT,
            initial_anchor=StructuralAnchor.RUNNING_HIGH,
            trigger=BarrierTrigger.CLOSE_BEYOND,
            trailing_anchor=StructuralAnchor.CONFIRMED_SWING_HIGH,
        ),
    ),
    take_profit_policies=(
        StructuralTakeProfitPolicy(
            policy_id="long_partial_at_running_high",
            side=PositionSide.LONG,
            anchor=StructuralAnchor.RUNNING_HIGH,
            close_fraction_grid=(0.25, 0.5, 0.75, 1.0),
        ),
        StructuralTakeProfitPolicy(
            policy_id="short_partial_at_running_low",
            side=PositionSide.SHORT,
            anchor=StructuralAnchor.RUNNING_LOW,
            close_fraction_grid=(0.25, 0.5, 0.75, 1.0),
        ),
    ),
)
