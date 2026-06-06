"""Pure large-runner nature rules for pump-awakening research.

The evaluator is deliberately source-neutral and side-effect free. It only
looks at decision-time features. Future labels, realized PnL, MFE/MAE, and exit
fields must not affect the output.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping


LARGE_RUNNER_NATURE_POLICY_ID = "large_runner_nature_rules"
LARGE_RUNNER_NATURE_VERSION = "v4_quality_cool_20260606"
LARGE_RUNNER_NATURE_FEATURE_BOUNDARY = "known_at_seed_5m_close"

RULE_V4_QUALITY_COOL = "v4_quality_cool"
RULE_V4_STANDARD = "v4_standard"

CORE_STRESS_SEED = "stress_seed"
CORE_LIQUID_OI_SEED = "liquid_oi_seed"

NATURE_A_COOL_STRESS_ABSORPTION = "A_cool_stress_absorption"
NATURE_B_LIQUID_DISTRIBUTED_MODERATE_TAKER = "B_liquid_distributed_moderate_taker"

BOOSTER_C_OI_SUPPORTED_LIQUID_DISTRIBUTION = "C_oi_supported_liquid_distribution"
BOOSTER_D_24H_FLOW_RECORD_ABSORPTION = "D_24h_flow_record_absorption"
BOOSTER_E_EXTREME_RANGE_DISTRIBUTED = "E_extreme_range_distributed"

TRADE_ELIGIBLE_E5_ARMS = frozenset({"E5_ignition_strict", "E5_mass_ignition"})


@dataclass(frozen=True, slots=True)
class LargeRunnerNatureDecision:
    rule_ids: tuple[str, ...]
    trade_rule_ids: tuple[str, ...]
    core_ids: tuple[str, ...]
    nature_ids: tuple[str, ...]
    booster_ids: tuple[str, ...]
    veto_reasons: tuple[str, ...]
    entry_scope_ok: bool
    entry_scope_reason: str
    confidence_score: int
    policy_id: str = LARGE_RUNNER_NATURE_POLICY_ID
    policy_version: str = LARGE_RUNNER_NATURE_VERSION
    feature_boundary: str = LARGE_RUNNER_NATURE_FEATURE_BOUNDARY

    @property
    def selected(self) -> bool:
        return bool(self.trade_rule_ids)

    def as_features(self) -> dict[str, object]:
        return {
            "large_runner_nature_policy_id": self.policy_id,
            "large_runner_nature_version": self.policy_version,
            "large_runner_nature_feature_boundary": self.feature_boundary,
            "large_runner_nature_rule_ids": "|".join(self.rule_ids),
            "large_runner_nature_trade_rule_ids": "|".join(self.trade_rule_ids),
            "large_runner_nature_core_ids": "|".join(self.core_ids),
            "large_runner_nature_ids": "|".join(self.nature_ids),
            "large_runner_nature_booster_ids": "|".join(self.booster_ids),
            "large_runner_nature_veto_reasons": "|".join(self.veto_reasons),
            "large_runner_nature_entry_scope_ok": bool(self.entry_scope_ok),
            "large_runner_nature_entry_scope_reason": self.entry_scope_reason,
            "large_runner_nature_confidence_score": int(self.confidence_score),
            "large_runner_nature_selected": bool(self.selected),
        }


def evaluate_large_runner_nature(features: Mapping[str, object]) -> LargeRunnerNatureDecision:
    """Evaluate v4 large-runner natures from decision-time features only."""

    pre60_range = _finite(features.get("pre60_range_pct"))
    pre60_trades = _finite(features.get("pre60_trades_sum"))
    pre60_quote = _finite(features.get("pre60_quote_sum"))
    pre60_return = _finite(features.get("pre60_return_pct"))
    early_return = _finite(features.get("early_return_pct"))
    m1_min_path = _finite(features.get("m1_min_path_return"))
    m1_quote_top1 = _finite(features.get("m1_quote_top1_share"))
    m1_trade_top1 = _finite(features.get("m1_trade_top1_share"))
    early_taker = _finite(features.get("early_taker_buy_quote_share"))
    current_vs_prior_trade_24h = _finite(features.get("current_vs_prior_max_trade_24h"))
    oi_change_early = _finite(features.get("oi_change_early_pct"))

    stress_seed = pre60_range >= 0.030 and pre60_trades >= 12_000 and m1_min_path <= -0.004
    liquid_oi_seed = (
        early_return <= 0.075
        and pre60_trades >= 40_000
        and m1_quote_top1 <= 0.41
        and _oi_nonnegative_or_missing(oi_change_early)
    )

    core_ids: list[str] = []
    if stress_seed:
        core_ids.append(CORE_STRESS_SEED)
    if liquid_oi_seed:
        core_ids.append(CORE_LIQUID_OI_SEED)

    quality_veto = _veto_reasons(
        pre60_return=pre60_return,
        pre60_return_cap=0.06,
        early_return=early_return,
        m1_quote_top1=m1_quote_top1,
        m1_trade_top1=m1_trade_top1,
        early_taker=early_taker,
    )
    standard_veto = _veto_reasons(
        pre60_return=pre60_return,
        pre60_return_cap=0.12,
        early_return=early_return,
        m1_quote_top1=m1_quote_top1,
        m1_trade_top1=m1_trade_top1,
        early_taker=early_taker,
    )

    rule_ids: list[str] = []
    if core_ids and not quality_veto:
        rule_ids.append(RULE_V4_QUALITY_COOL)
    if core_ids and not standard_veto:
        rule_ids.append(RULE_V4_STANDARD)

    nature_ids: list[str] = []
    if (
        early_return <= 0.075
        and pre60_return <= 0.06
        and pre60_range >= 0.058
        and m1_min_path <= -0.004
    ):
        nature_ids.append(NATURE_A_COOL_STRESS_ABSORPTION)
    if (
        early_return <= 0.075
        and pre60_quote >= 1_000_000
        and m1_trade_top1 <= 0.36
        and early_taker <= 0.55
    ):
        nature_ids.append(NATURE_B_LIQUID_DISTRIBUTED_MODERATE_TAKER)

    booster_ids: list[str] = []
    if (
        early_return <= 0.075
        and pre60_trades >= 40_000
        and m1_quote_top1 <= 0.35
        and _oi_nonnegative_or_missing(oi_change_early)
    ):
        booster_ids.append(BOOSTER_C_OI_SUPPORTED_LIQUID_DISTRIBUTION)
    if (
        pre60_quote >= 1_000_000
        and m1_min_path <= -0.004
        and m1_trade_top1 <= 0.36
        and current_vs_prior_trade_24h >= 0.70
    ):
        booster_ids.append(BOOSTER_D_24H_FLOW_RECORD_ABSORPTION)
    if pre60_return <= 0.06 and pre60_range >= 0.10 and m1_trade_top1 <= 0.36:
        booster_ids.append(BOOSTER_E_EXTREME_RANGE_DISTRIBUTED)

    entry_scope_ok, entry_scope_reason = _entry_scope(features)
    trade_rule_ids = tuple(rule_ids) if entry_scope_ok else ()

    # Base pass + boosters. This is an audit/confidence score, not position size.
    confidence_score = len(nature_ids) + len(booster_ids)

    return LargeRunnerNatureDecision(
        rule_ids=tuple(rule_ids),
        trade_rule_ids=trade_rule_ids,
        core_ids=tuple(core_ids),
        nature_ids=tuple(nature_ids),
        booster_ids=tuple(booster_ids),
        veto_reasons=tuple(quality_veto),
        entry_scope_ok=entry_scope_ok,
        entry_scope_reason=entry_scope_reason,
        confidence_score=confidence_score,
    )


def v4_quality_mask(features: Mapping[str, object], *, pre60_return_cap: float = 0.06) -> bool:
    """Return the v4-quality pass with a configurable pre60 cap.

    This helper exists for sensitivity reporting. It uses the same feature
    boundary as the main evaluator and intentionally ignores arm/portfolio
    execution scope.
    """

    pre60_range = _finite(features.get("pre60_range_pct"))
    pre60_trades = _finite(features.get("pre60_trades_sum"))
    pre60_return = _finite(features.get("pre60_return_pct"))
    early_return = _finite(features.get("early_return_pct"))
    m1_min_path = _finite(features.get("m1_min_path_return"))
    m1_quote_top1 = _finite(features.get("m1_quote_top1_share"))
    m1_trade_top1 = _finite(features.get("m1_trade_top1_share"))
    early_taker = _finite(features.get("early_taker_buy_quote_share"))
    oi_change_early = _finite(features.get("oi_change_early_pct"))

    stress_seed = pre60_range >= 0.030 and pre60_trades >= 12_000 and m1_min_path <= -0.004
    liquid_oi_seed = (
        early_return <= 0.075
        and pre60_trades >= 40_000
        and m1_quote_top1 <= 0.41
        and _oi_nonnegative_or_missing(oi_change_early)
    )
    return bool(
        (stress_seed or liquid_oi_seed)
        and not _veto_reasons(
            pre60_return=pre60_return,
            pre60_return_cap=pre60_return_cap,
            early_return=early_return,
            m1_quote_top1=m1_quote_top1,
            m1_trade_top1=m1_trade_top1,
            early_taker=early_taker,
        )
    )


def _veto_reasons(
    *,
    pre60_return: float,
    pre60_return_cap: float,
    early_return: float,
    m1_quote_top1: float,
    m1_trade_top1: float,
    early_taker: float,
) -> list[str]:
    reasons: list[str] = []
    if pre60_return > float(pre60_return_cap):
        reasons.append(f"pre60_return_gt_{float(pre60_return_cap):.2f}")
    if early_return > 0.09:
        reasons.append("early_return_gt_0.09")
    if m1_quote_top1 > 0.55:
        reasons.append("m1_quote_top1_gt_0.55")
    if m1_trade_top1 > 0.55:
        reasons.append("m1_trade_top1_gt_0.55")
    if early_taker > 0.62:
        reasons.append("early_taker_buy_quote_share_gt_0.62")
    return reasons


def _entry_scope(features: Mapping[str, object]) -> tuple[bool, str]:
    arm_id = str(features.get("arm_id", "") or "").strip()
    offset = _finite(features.get("decision_offset_minutes"))
    if not arm_id and not math.isfinite(offset):
        return False, "setup_level_no_entry_arm"
    if arm_id not in TRADE_ELIGIBLE_E5_ARMS:
        return False, "not_e5_strict_or_mass_arm"
    if offset != 5.0:
        return False, "not_e5_decision_offset"
    return True, "e5_strict_mass_entry_scope"


def _oi_nonnegative_or_missing(value: float) -> bool:
    return not math.isfinite(value) or value >= 0.0


def _finite(value: object) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")
    return parsed if math.isfinite(parsed) else float("nan")
