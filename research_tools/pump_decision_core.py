"""Pure rolling seed-first decision contracts.

This module intentionally contains only typed contracts and immutable constants in
P465. Live and backtest adapters must build these snapshots from their own data
sources and, in later patches, pass them into one shared evaluator.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping


ROLLING_DECISION_CONTRACT_ID = "rolling_htf_seed_first_ltf_confirm_v1"
ROLLING_DECISION_CORE_VERSION = "p466_shared_cas_matcher"

DecisionSource = Literal["live", "backtest", "parity_replay", "test"]
DecisionVerdictType = Literal["selected", "rejected", "data_dependency_not_ready"]
DecisionStage = Literal[
    "data_dependency",
    "rolling_htf_seed",
    "ltf_confirm",
    "category",
    "risk",
    "unknown",
]

CATEGORY_C_BALANCED_FLOW_ACCEPTANCE = "C_balanced_flow_acceptance"
CATEGORY_A_RESONANCE_PRIOR_SPIKE = "A_resonance_prior_spike"
CATEGORY_S_7D_5M30_STRICT = "S_7d_5m30_strict"
ROLLING_CATEGORY_PRIORITY: tuple[str, ...] = (
    CATEGORY_C_BALANCED_FLOW_ACCEPTANCE,
    CATEGORY_A_RESONANCE_PRIOR_SPIKE,
    CATEGORY_S_7D_5M30_STRICT,
)
SUPPORTED_ROLLING_TF_SETS: tuple[str, ...] = ("5m_30s", "3m_30s")


@dataclass(frozen=True, slots=True)
class RollingProfileSpec:
    """Decision-time timeframe contract for one rolling HTF/LTF profile."""

    tf_set: str
    htf_seconds: int
    ltf_seconds: int
    contract_id: str = ROLLING_DECISION_CONTRACT_ID
    description: str = "rolling HTF seed, then first LTF confirm after seed"


@dataclass(frozen=True, slots=True)
class RollingCategoryRule:
    """Named category membership contract without matcher thresholds yet."""

    category_id: str
    priority: int
    enabled_tf_sets: tuple[str, ...] = SUPPORTED_ROLLING_TF_SETS
    contract_id: str = ROLLING_DECISION_CONTRACT_ID


@dataclass(frozen=True, slots=True)
class DecisionCandle:
    """Normalized closed candle known at decision time.

    `close_time_ms` is exclusive-end style: the candle is available only after
    this timestamp plus the orchestrator's configured close/deadline latency.
    """

    open_time_ms: int
    close_time_ms: int
    open: float
    high: float
    low: float
    close: float
    quote_volume: float
    number_of_trades: int
    taker_buy_quote_volume: float | None = None
    source: str = ""
    source_status: str = "ok"


@dataclass(frozen=True, slots=True)
class DataDependency:
    """Typed missing/degraded data dependency for parity-visible rejects."""

    name: str
    status: Literal["ok", "missing", "stale", "gap", "degraded", "error"]
    reason: str = ""
    asof_ms: int | None = None
    source: str = ""


@dataclass(frozen=True, slots=True)
class RollingSeedSnapshot:
    """Rolling HTF seed and pre-seed context supplied to the decision core."""

    tf_set: str
    seed_open_ms: int
    seed_close_ms: int
    seed_candles: tuple[DecisionCandle, ...]
    pre_seed_context_candles: tuple[DecisionCandle, ...] = ()
    dependencies: tuple[DataDependency, ...] = ()


@dataclass(frozen=True, slots=True)
class LtfConfirmSnapshot:
    """First post-seed LTF confirmation candidate supplied to the core."""

    confirm_start_ms: int
    confirm_end_ms: int
    confirm_candles: tuple[DecisionCandle, ...]
    dependencies: tuple[DataDependency, ...] = ()


@dataclass(frozen=True, slots=True)
class DecisionReject:
    """One exact reason why the core did not select a signal."""

    stage: DecisionStage
    reason: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DecisionSnapshot:
    """Complete, source-neutral input to the future shared decision evaluator."""

    symbol: str
    tf_set: str
    decision_time_ms: int
    source: DecisionSource
    rolling_seed: RollingSeedSnapshot | None = None
    ltf_confirm: LtfConfirmSnapshot | None = None
    source_labels: Mapping[str, str] = field(default_factory=dict)
    features: Mapping[str, float | int | str | bool | None] = field(default_factory=dict)
    dependencies: tuple[DataDependency, ...] = ()
    contract_id: str = ROLLING_DECISION_CONTRACT_ID
    core_version: str = ROLLING_DECISION_CORE_VERSION


@dataclass(frozen=True, slots=True)
class DecisionVerdict:
    """Source-neutral core output before portfolio/execution allocation."""

    verdict: DecisionVerdictType
    snapshot: DecisionSnapshot
    category_id: str = ""
    rejects: tuple[DecisionReject, ...] = ()
    dependencies: tuple[DataDependency, ...] = ()
    signal_entry_price: float | None = None
    initial_stop_price: float | None = None
    tp1_price: float | None = None
    features: Mapping[str, float | int | str | bool | None] = field(default_factory=dict)

    @property
    def is_selected(self) -> bool:
        return self.verdict == "selected"




def _finite_float(value: object) -> float:
    """Return a finite float or NaN without depending on pandas/numpy."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return number if math.isfinite(number) else float("nan")


def rolling_tf_set_from_features(features: Mapping[str, object]) -> str:
    """Resolve the rolling profile id from live or backtest feature rows."""

    explicit = features.get("rolling_runner_tf_set") or features.get("tf_set")
    if explicit:
        return str(explicit)
    htf = str(features.get("htf_timeframe", "") or "")
    ltf = str(features.get("ltf_timeframe", "") or "")
    return f"{htf}_{ltf}" if htf or ltf else ""


def match_rolling_categories(features: Mapping[str, object]) -> tuple[str, ...]:
    """Match the frozen rolling C/A/S category rules.

    P466 intentionally moves the existing live/backtest thresholds into one
    shared function without changing any threshold, order, or supported TF set.
    The function is pure and source-neutral; callers are responsible for
    building the features from known-at-decision-time data.
    """

    tf_set = rolling_tf_set_from_features(features)
    if tf_set not in SUPPORTED_ROLLING_TF_SETS:
        return ()

    htf_trade_ratio = _finite_float(features.get("htf_trade_ratio"))
    htf_quote_ratio = _finite_float(features.get("htf_quote_ratio"))
    ltf_trade_pace_ratio = _finite_float(features.get("ltf_trade_pace_ratio"))
    ltf_quote_pace_ratio = _finite_float(features.get("ltf_quote_pace_ratio"))
    dormancy_trade_ratio = _finite_float(features.get("dormancy_to_anomaly_trade_ratio"))
    current_vs_prior_max = _finite_float(features.get("current_vs_prior_spike_max_quote"))
    second_half_return = _finite_float(features.get("ltf_second_half_return_pct"))
    pregrowth_max_single = _finite_float(features.get("pregrowth_max_single_return_pct"))

    matches: list[str] = []
    if (
        math.isfinite(dormancy_trade_ratio)
        and dormancy_trade_ratio <= 32.0
        and math.isfinite(current_vs_prior_max)
        and current_vs_prior_max > 0.45
        and math.isfinite(ltf_quote_pace_ratio)
        and ltf_quote_pace_ratio <= 52.0
        and math.isfinite(second_half_return)
        and second_half_return <= 0.0125
        and math.isfinite(pregrowth_max_single)
        and pregrowth_max_single > 0.005
    ):
        matches.append(CATEGORY_C_BALANCED_FLOW_ACCEPTANCE)
    if (
        math.isfinite(htf_trade_ratio)
        and htf_trade_ratio <= 18.0
        and math.isfinite(current_vs_prior_max)
        and current_vs_prior_max > 0.6
        and current_vs_prior_max <= 1.5
    ):
        matches.append(CATEGORY_A_RESONANCE_PRIOR_SPIKE)
    if (
        tf_set == "5m_30s"
        and math.isfinite(htf_trade_ratio)
        and htf_trade_ratio >= 11.7
        and math.isfinite(ltf_trade_pace_ratio)
        and ltf_trade_pace_ratio <= 5.7
        and math.isfinite(htf_quote_ratio)
        and htf_quote_ratio <= 47.9
    ):
        matches.append(CATEGORY_S_7D_5M30_STRICT)
    return tuple(matches)


def rolling_category_priority_rank(category_id: str) -> int | None:
    """Return the 1-based shared C/A/S priority rank, if known."""

    try:
        return ROLLING_CATEGORY_PRIORITY.index(category_id) + 1
    except ValueError:
        return None


ROLLING_PROFILE_SPECS: dict[str, RollingProfileSpec] = {
    "5m_30s": RollingProfileSpec(tf_set="5m_30s", htf_seconds=300, ltf_seconds=30),
    "3m_30s": RollingProfileSpec(tf_set="3m_30s", htf_seconds=180, ltf_seconds=30),
}

ROLLING_CATEGORY_RULES: tuple[RollingCategoryRule, ...] = tuple(
    RollingCategoryRule(category_id=category_id, priority=priority)
    for priority, category_id in enumerate(ROLLING_CATEGORY_PRIORITY, start=1)
)
