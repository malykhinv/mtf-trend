"""Shared trade-policy layer for pump-awakening signals.

``PumpDecisionCore`` answers one question: did this source-neutral snapshot
produce a valid pump-awakening signal?  This module answers the next question:
is that selected signal one of the clean-buyer continuation types that we are
currently willing to trade, and how should that position be managed?

The policy is intentionally pure and source-neutral.  Live and backtest must
call it with the same decision-time feature dictionary and must not duplicate
these rules in adapter code.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

from research_tools.pump_decision_core import rolling_tf_set_from_features


PUMP_TRADE_POLICY_ID = "clean_buyer_continuation_v1"
PUMP_TRADE_POLICY_VERSION = "p512_clean_buyer_trade_policy"

EXIT_POLICY_TP075_CLOSE75_TRAIL = "tp075_close75_structural_trail_v1"

BROAD_WATCHLIST_BUYER55_NOTDUMP = "broad_buyer55_notdump_watchlist"
RULE_STRONG_HIGH_WIN_CLEAN_NOTDUMP = "strong_high_win_clean_notdump"
RULE_5M_STRONG_BUYER_CONFIRM_HISTORY = "five_minute_strong_buyer_confirm_history"
RULE_15S_FAST_TAPE_HISTORY = "fifteen_second_fast_tape_history"
RULE_5M15_ACTIVE_TAPE = "five_minute_15s_active_tape"
RULE_DISTRIBUTED_TRADE_TOP1_CONFIRM08 = "distributed_trade_top1_confirm08"
RULE_DISTRIBUTED_QUOTE_TOP1_CONFIRM08 = "distributed_quote_top1_confirm08"
RULE_NOT_LATE_TAILQ55_PACE5 = "not_late_tailq55_pace5"

ACCEPTED_RULE_PRIORITY: tuple[str, ...] = (
    RULE_STRONG_HIGH_WIN_CLEAN_NOTDUMP,
    RULE_5M_STRONG_BUYER_CONFIRM_HISTORY,
    RULE_15S_FAST_TAPE_HISTORY,
    RULE_5M15_ACTIVE_TAPE,
    RULE_DISTRIBUTED_TRADE_TOP1_CONFIRM08,
    RULE_DISTRIBUTED_QUOTE_TOP1_CONFIRM08,
    RULE_NOT_LATE_TAILQ55_PACE5,
)


@dataclass(frozen=True, slots=True)
class PumpExitPolicy:
    policy_id: str = EXIT_POLICY_TP075_CLOSE75_TRAIL
    tp1_r: float = 0.75
    tp1_close_fraction: float = 0.75
    trail_model: str = "structural_after_tp1"
    rest_target_r: float | None = None

    @property
    def runner_fraction(self) -> float:
        return max(0.0, 1.0 - float(self.tp1_close_fraction))

    def as_features(self) -> dict[str, object]:
        return {
            "exit_policy_id": self.policy_id,
            "exit_tp1_r": float(self.tp1_r),
            "exit_tp1_close_fraction": float(self.tp1_close_fraction),
            "exit_runner_fraction": self.runner_fraction,
            "exit_trail_model": self.trail_model,
            "exit_rest_target_r": "" if self.rest_target_r is None else float(self.rest_target_r),
        }


DEFAULT_PUMP_EXIT_POLICY = PumpExitPolicy()


@dataclass(frozen=True, slots=True)
class PumpTradePolicyDecision:
    verdict: str
    reason: str
    policy_id: str = PUMP_TRADE_POLICY_ID
    policy_version: str = PUMP_TRADE_POLICY_VERSION
    matched_rule_id: str = ""
    matched_rule_ids: tuple[str, ...] = ()
    watchlist_rule_ids: tuple[str, ...] = ()
    exit_policy: PumpExitPolicy = DEFAULT_PUMP_EXIT_POLICY

    @property
    def accepted(self) -> bool:
        return self.verdict == "accepted"

    def as_features(self) -> dict[str, object]:
        return {
            "trade_policy_id": self.policy_id,
            "trade_policy_version": self.policy_version,
            "trade_policy_verdict": self.verdict,
            "trade_policy_reason": self.reason,
            "trade_policy_rule_id": self.matched_rule_id,
            "trade_policy_matched_rule_ids": "|".join(self.matched_rule_ids),
            "trade_policy_watchlist_rule_ids": "|".join(self.watchlist_rule_ids),
            **self.exit_policy.as_features(),
        }


def evaluate_pump_trade_policy(features: Mapping[str, object]) -> PumpTradePolicyDecision:
    """Evaluate the frozen clean-buyer continuation trade policy.

    All inputs are decision-time fields already produced by the shared core.
    Future labels, MFE/MAE, realized PnL, and exit fields are deliberately not
    accepted as inputs.
    """

    tf_set = rolling_tf_set_from_features(features)
    buyer55 = _ge(features.get("ltf_taker_buy_quote_share"), 0.55)
    buyer60 = _ge(features.get("ltf_taker_buy_quote_share"), 0.60)
    no_dump = _ge(features.get("pregrowth_min_path_return_pct"), -0.01)
    confirm06 = _ge(features.get("ltf_confirm_return_pct"), 0.006)
    confirm08 = _ge(features.get("ltf_confirm_return_pct"), 0.008)
    prior10 = _le(features.get("prior_spike_count_24h"), 10.0)
    seed_quote_top1_50 = _le(features.get("htf_ltf_quote_top1_share"), 0.50)
    seed_trade_top1_40 = _le(features.get("htf_ltf_trade_top1_share"), 0.40)
    seed_tail_quote_55 = _le(features.get("htf_ltf_tail_quote_share"), 0.55)
    confirm_trade_pace5 = _ge(features.get("ltf_trade_pace_ratio"), 5.0)
    confirm_quote_pace = _between(features.get("ltf_quote_pace_ratio"), 3.0, 40.0)
    tf_5m = tf_set.startswith("5m_")
    tf_15s = tf_set.endswith("_15s")
    tf_5m15 = tf_set == "5m_15s"

    matched: list[str] = []
    watchlist: list[str] = []
    if buyer55 and no_dump:
        watchlist.append(BROAD_WATCHLIST_BUYER55_NOTDUMP)
    if buyer60 and confirm08 and prior10 and no_dump:
        matched.append(RULE_STRONG_HIGH_WIN_CLEAN_NOTDUMP)
    if buyer60 and confirm06 and prior10 and tf_5m:
        matched.append(RULE_5M_STRONG_BUYER_CONFIRM_HISTORY)
    if buyer60 and confirm_trade_pace5 and prior10 and tf_15s:
        matched.append(RULE_15S_FAST_TAPE_HISTORY)
    if buyer60 and confirm_trade_pace5 and confirm_quote_pace and tf_5m15:
        matched.append(RULE_5M15_ACTIVE_TAPE)
    if buyer60 and confirm08 and prior10 and seed_trade_top1_40:
        matched.append(RULE_DISTRIBUTED_TRADE_TOP1_CONFIRM08)
    if buyer60 and confirm08 and prior10 and seed_quote_top1_50:
        matched.append(RULE_DISTRIBUTED_QUOTE_TOP1_CONFIRM08)
    if buyer60 and seed_tail_quote_55 and confirm_trade_pace5 and prior10:
        matched.append(RULE_NOT_LATE_TAILQ55_PACE5)

    ordered = tuple(rule for rule in ACCEPTED_RULE_PRIORITY if rule in matched)
    if ordered:
        return PumpTradePolicyDecision(
            verdict="accepted",
            reason="clean_buyer_continuation_policy_accepted",
            matched_rule_id=ordered[0],
            matched_rule_ids=ordered,
            watchlist_rule_ids=tuple(watchlist),
        )
    if watchlist:
        return PumpTradePolicyDecision(
            verdict="rejected",
            reason="clean_buyer_continuation_watchlist_only",
            watchlist_rule_ids=tuple(watchlist),
        )
    return PumpTradePolicyDecision(
        verdict="rejected",
        reason="clean_buyer_continuation_rule_not_matched",
    )


def _finite_float(value: object) -> float:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")
    return result if math.isfinite(result) else float("nan")


def _ge(value: object, threshold: float) -> bool:
    parsed = _finite_float(value)
    return math.isfinite(parsed) and parsed >= float(threshold)


def _le(value: object, threshold: float) -> bool:
    parsed = _finite_float(value)
    return math.isfinite(parsed) and parsed <= float(threshold)


def _between(value: object, low: float, high: float) -> bool:
    parsed = _finite_float(value)
    return math.isfinite(parsed) and float(low) <= parsed <= float(high)
