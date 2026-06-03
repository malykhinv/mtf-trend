"""Pure rolling seed-first decision contracts and evaluator.

Live and backtest adapters must build these snapshots from their own data
sources, then call the source-neutral evaluator in this module. The evaluator
must not know whether a snapshot came from websockets, REST, aggTrades, or a
parity replay.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Mapping, Sequence


ROLLING_DECISION_CONTRACT_ID = "rolling_htf_seed_first_ltf_confirm_v1"
ROLLING_DECISION_CORE_VERSION = "p498_closed_baseline_context"

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
SUPPORTED_ROLLING_TF_SETS: tuple[str, ...] = ("5m_30s", "3m_30s", "5m_15s", "3m_15s")

ROLLING_BASELINE_WINDOWS = 60
ROLLING_DORMANCY_WINDOWS = 30
ROLLING_PREGROWTH_WINDOWS = 5
ROLLING_PRIOR_SPIKE_LOOKBACK_MS = 24 * 60 * 60 * 1000
ROLLING_MAX_PRE_SEED_CONTEXT_GAP_MS = 60 * 1000
ROLLING_CONTEXT_MIN_WINDOWS = max(ROLLING_BASELINE_WINDOWS, ROLLING_DORMANCY_WINDOWS, ROLLING_PREGROWTH_WINDOWS)
ROLLING_SEED_MIN_HTF_QUOTE_RATIO = 5.0
ROLLING_SEED_MIN_HTF_TRADE_RATIO = 5.0
ROLLING_SEED_MIN_HTF_RETURN_PCT = 0.0100
ROLLING_PRE_SEED_MAX_DUMP_RETURN_PCT = -0.0120
ROLLING_PRE_SEED_MAX_DUMP_PATH_RETURN_PCT = -0.0150
ROLLING_PRE_SEED_MAX_SINGLE_DUMP_RETURN_PCT = -0.0080
ROLLING_PRE_SEED_MAX_DUMP_POSITIVE_STEP_SHARE = 0.35
ROLLING_PRE_SEED_MAX_NOISY_DUMP_RANGE_PCT = 0.0250
ROLLING_LTF_MIN_CONFIRM_RETURN_PCT = 0.004
ROLLING_LTF_MIN_QUOTE_PACE_RATIO = 3.0
ROLLING_LTF_MIN_TRADE_PACE_RATIO = 3.0
ROLLING_LTF_MIN_SECOND_HALF_RETURN_PCT = 0.0
ROLLING_LTF_MIN_QUOTE_ACCELERATION = 1.0
ROLLING_LTF_MIN_TRADE_ACCELERATION = 1.0
ROLLING_STRUCTURAL_STOP_BUFFER_PCT = 0.0005
ROLLING_TP1_R = 0.75
ROLLING_MAX_INITIAL_RISK_PCT = 0.05


@dataclass(frozen=True, slots=True)
class RollingProfileSpec:
    """Decision-time timeframe contract for one rolling HTF/LTF profile."""

    tf_set: str
    htf_seconds: int
    ltf_seconds: int
    min_confirm_candles: int
    max_confirm_candles: int
    contract_id: str = ROLLING_DECISION_CONTRACT_ID
    description: str = "rolling HTF seed, then first LTF confirm after seed"


@dataclass(frozen=True, slots=True)
class RollingCategoryRule:
    """Named category membership contract."""

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
    """Post-seed LTF confirmation candidate supplied to the core."""

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
    """Complete, source-neutral input to the shared decision evaluator."""

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


ROLLING_PROFILE_SPECS: dict[str, RollingProfileSpec] = {
    "5m_30s": RollingProfileSpec(
        tf_set="5m_30s",
        htf_seconds=300,
        ltf_seconds=30,
        min_confirm_candles=2,
        max_confirm_candles=8,
    ),
    "5m_15s": RollingProfileSpec(
        tf_set="5m_15s",
        htf_seconds=300,
        ltf_seconds=15,
        min_confirm_candles=4,
        max_confirm_candles=16,
    ),
    "3m_30s": RollingProfileSpec(
        tf_set="3m_30s",
        htf_seconds=180,
        ltf_seconds=30,
        min_confirm_candles=2,
        max_confirm_candles=6,
    ),
    "3m_15s": RollingProfileSpec(
        tf_set="3m_15s",
        htf_seconds=180,
        ltf_seconds=15,
        min_confirm_candles=4,
        max_confirm_candles=12,
    ),
}

ROLLING_CATEGORY_RULES: tuple[RollingCategoryRule, ...] = tuple(
    RollingCategoryRule(category_id=category_id, priority=priority)
    for priority, category_id in enumerate(ROLLING_CATEGORY_PRIORITY, start=1)
)


def rolling_context_windows_for_spec(spec: RollingProfileSpec) -> int:
    """Return the exact seed-aligned HTF context length required by the contract.

    The context is non-overlapping HTF windows that end exactly at seed_open_ms:
    context[-1] = [seed_open - htf, seed_open), context[-2] before it, etc.
    It covers the 24h prior-spike horizon when possible, and at least the
    baseline/dormancy/pregrowth windows used by the core. Extra adapter history
    outside this slice is intentionally ignored by hashing and feature derivation.
    """

    htf_ms = int(spec.htf_seconds) * 1000
    prior_windows = int(math.ceil(ROLLING_PRIOR_SPIKE_LOOKBACK_MS / htf_ms))
    return max(ROLLING_CONTEXT_MIN_WINDOWS, prior_windows)


def rolling_context_windows_for_tf_set(tf_set: str) -> int | None:
    spec = ROLLING_PROFILE_SPECS.get(str(tf_set))
    return None if spec is None else rolling_context_windows_for_spec(spec)


@dataclass(frozen=True, slots=True)
class _AggregateCandle:
    open_time_ms: int
    close_time_ms: int
    open: float
    high: float
    low: float
    close: float
    quote_volume: float
    number_of_trades: float
    taker_buy_quote_volume: float | None = None


# ---------------------------------------------------------------------------
# Numeric helpers


def _finite_float(value: object) -> float:
    """Return a finite float or NaN without depending on pandas/numpy."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return number if math.isfinite(number) else float("nan")


def _safe_divide(numerator: object, denominator: object) -> float:
    num = _finite_float(numerator)
    den = _finite_float(denominator)
    if not math.isfinite(num) or not math.isfinite(den) or den == 0.0:
        return float("nan")
    return num / den


def _finite_values(values: Sequence[object]) -> list[float]:
    return [item for item in (_finite_float(value) for value in values) if math.isfinite(item)]


def _positive_values(values: Sequence[object]) -> list[float]:
    return [item for item in _finite_values(values) if item > 0.0]


def _median(values: Sequence[object]) -> float:
    finite = sorted(_finite_values(values))
    if not finite:
        return float("nan")
    mid = len(finite) // 2
    if len(finite) % 2:
        return float(finite[mid])
    return float((finite[mid - 1] + finite[mid]) / 2.0)


def _positive_median(values: Sequence[object]) -> float:
    return _median(_positive_values(values))


def _finite_max(values: Sequence[object]) -> float:
    finite = _finite_values(values)
    return float(max(finite)) if finite else float("nan")


def _finite_min(values: Sequence[object]) -> float:
    finite = _finite_values(values)
    return float(min(finite)) if finite else float("nan")


def _finite_sum(values: Sequence[object]) -> float:
    finite = _finite_values(values)
    return float(sum(finite)) if finite else float("nan")


def _candle_return(candle: DecisionCandle | _AggregateCandle) -> float:
    return _safe_divide(float(candle.close) - float(candle.open), float(candle.open))


def _range_pct(candle: DecisionCandle | _AggregateCandle) -> float:
    return _safe_divide(float(candle.high) - float(candle.low), float(candle.open))


# ---------------------------------------------------------------------------
# Shared category matcher


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


def decision_snapshot_match_key(snapshot: DecisionSnapshot) -> str:
    """Return a stable human-readable join key for live/backtest parity.

    The key identifies the exact source-neutral decision window. It intentionally
    excludes transport/source labels so websocket-built and REST/aggTrade-built
    snapshots can be joined when they describe the same normalized candles.
    """

    seed = snapshot.rolling_seed
    confirm = snapshot.ltf_confirm
    return "|".join(
        str(item)
        for item in (
            snapshot.contract_id,
            snapshot.core_version,
            snapshot.symbol,
            snapshot.tf_set,
            "" if seed is None else int(seed.seed_open_ms),
            "" if seed is None else int(seed.seed_close_ms),
            "" if confirm is None else int(confirm.confirm_start_ms),
            "" if confirm is None else int(confirm.confirm_end_ms),
        )
    )


def decision_snapshot_hash(snapshot: DecisionSnapshot) -> str:
    """Hash the source-neutral decision inputs used by the shared core.

    The hash is deliberately based on normalized candle values and exact decision
    window bounds, not on adapter names, socket/REST source labels, portfolio
    state, execution state, or artifact-only feature labels. Therefore:

    same normalized seed/context/confirm candles -> same hash;
    same hash + different core verdict -> bug.
    """

    payload = _snapshot_hash_payload(snapshot)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _snapshot_hash_payload(snapshot: DecisionSnapshot) -> dict[str, object]:
    seed = snapshot.rolling_seed
    confirm = snapshot.ltf_confirm
    spec = ROLLING_PROFILE_SPECS.get(snapshot.tf_set)
    return {
        "contract_id": snapshot.contract_id,
        "core_version": snapshot.core_version,
        "symbol": snapshot.symbol,
        "tf_set": snapshot.tf_set,
        "rolling_seed": None
        if seed is None
        else {
            "tf_set": seed.tf_set,
            "seed_open_ms": int(seed.seed_open_ms),
            "seed_close_ms": int(seed.seed_close_ms),
            "seed_candles": [_candle_hash_payload(item) for item in seed.seed_candles],
            "pre_seed_context_candles": [
                _candle_hash_payload(item)
                for item in (_contract_context_candles(seed, spec) if spec is not None else tuple(seed.pre_seed_context_candles))
            ],
            "dependencies": [_dependency_hash_payload(item) for item in seed.dependencies],
        },
        "ltf_confirm": None
        if confirm is None
        else {
            "confirm_start_ms": int(confirm.confirm_start_ms),
            "confirm_end_ms": int(confirm.confirm_end_ms),
            "confirm_candles": [_candle_hash_payload(item) for item in confirm.confirm_candles],
            "dependencies": [_dependency_hash_payload(item) for item in confirm.dependencies],
        },
        "dependencies": [_dependency_hash_payload(item) for item in snapshot.dependencies],
    }


def _candle_hash_payload(candle: DecisionCandle) -> dict[str, object]:
    return {
        "open_time_ms": int(candle.open_time_ms),
        "close_time_ms": int(candle.close_time_ms),
        "open": _canonical_number(candle.open),
        "high": _canonical_number(candle.high),
        "low": _canonical_number(candle.low),
        "close": _canonical_number(candle.close),
        "quote_volume": _canonical_number(candle.quote_volume),
        "number_of_trades": int(candle.number_of_trades),
        "taker_buy_quote_volume": _canonical_number(candle.taker_buy_quote_volume),
        "source_status": str(candle.source_status or "ok"),
    }


def _dependency_hash_payload(dependency: DataDependency) -> dict[str, object]:
    return {
        "name": str(dependency.name),
        "status": str(dependency.status),
        "reason": str(dependency.reason or ""),
        "asof_ms": "" if dependency.asof_ms is None else int(dependency.asof_ms),
    }


def _canonical_number(value: object) -> float | int | None:
    if value is None:
        return None
    number = _finite_float(value)
    if not math.isfinite(number):
        return None
    if float(number).is_integer() and abs(number) < 9_000_000_000_000_000:
        return int(number)
    return round(float(number), 12)


# ---------------------------------------------------------------------------
# Seed-first core


def evaluate_ltf_confirm_sequence_after_seed(
    snapshot: DecisionSnapshot,
    *,
    post_seed_ltf_candles: Sequence[DecisionCandle],
) -> tuple[DecisionVerdict, ...]:
    """Evaluate each exact post-seed LTF confirm window in order.

    One returned verdict corresponds to one exact confirm candidate
    (min_confirm..max_confirm) or to an upfront data dependency before any exact
    confirm can be built. This is the source-neutral audit primitive used by
    backtest rejected-exact-window artifacts and live parity debugging.
    """

    spec = ROLLING_PROFILE_SPECS.get(snapshot.tf_set)
    if spec is None:
        return (_rejected(snapshot, "rolling_htf_seed", "unsupported_tf_set", {"tf_set": snapshot.tf_set}),)
    seed = snapshot.rolling_seed
    if seed is None:
        return (_dependency_not_ready(snapshot, "rolling_seed_missing", ()),)
    ltf_ms = spec.ltf_seconds * 1000
    ordered = tuple(sorted(post_seed_ltf_candles, key=lambda item: item.open_time_ms))
    if not ordered:
        return (_dependency_not_ready(snapshot, "post_seed_ltf_missing", ()),)
    if int(ordered[0].open_time_ms) != int(seed.seed_close_ms):
        return (
            _dependency_not_ready(
                snapshot,
                "post_seed_ltf_not_contiguous_from_seed_close",
                (DataDependency(name="post_seed_ltf", status="gap", reason="first_ltf_open_not_seed_close", asof_ms=seed.seed_close_ms),),
            ),
        )
    continuity = _continuity_dependency("post_seed_ltf", ordered, expected_step_ms=ltf_ms)
    if continuity is not None:
        return (_dependency_not_ready(snapshot, continuity.reason or "post_seed_ltf_gap", (continuity,)),)

    verdicts: list[DecisionVerdict] = []
    max_count = min(spec.max_confirm_candles, len(ordered))
    for confirm_count in range(spec.min_confirm_candles, max_count + 1):
        confirm = tuple(ordered[:confirm_count])
        candidate = DecisionSnapshot(
            symbol=snapshot.symbol,
            tf_set=snapshot.tf_set,
            decision_time_ms=int(confirm[-1].close_time_ms),
            source=snapshot.source,
            rolling_seed=seed,
            ltf_confirm=LtfConfirmSnapshot(
                confirm_start_ms=int(confirm[0].open_time_ms),
                confirm_end_ms=int(confirm[-1].close_time_ms),
                confirm_candles=confirm,
            ),
            source_labels=snapshot.source_labels,
            features=snapshot.features,
            dependencies=snapshot.dependencies,
            contract_id=snapshot.contract_id,
            core_version=snapshot.core_version,
        )
        verdict = evaluate_seed_first(candidate)
        verdicts.append(verdict)
        if verdict.verdict in ("selected", "data_dependency_not_ready"):
            break
        if _is_terminal_seed_reject(verdict):
            break
    return tuple(verdicts)


def evaluate_first_ltf_confirm_after_seed(
    snapshot: DecisionSnapshot,
    *,
    post_seed_ltf_candles: Sequence[DecisionCandle],
) -> DecisionVerdict:
    """Find the first selected LTF confirmation after the supplied seed."""

    verdicts = evaluate_ltf_confirm_sequence_after_seed(snapshot, post_seed_ltf_candles=post_seed_ltf_candles)
    if not verdicts:
        return _dependency_not_ready(snapshot, "post_seed_ltf_missing", ())
    for verdict in verdicts:
        if verdict.verdict in ("selected", "data_dependency_not_ready"):
            return verdict
    last = verdicts[-1]
    return replace(
        last,
        features={
            **dict(last.features),
            "checked_confirm_candles": len(verdicts),
            "last_confirm_end_ms": last.snapshot.ltf_confirm.confirm_end_ms if last.snapshot.ltf_confirm is not None else None,
        },
    )


def evaluate_rolling_seed_stage(snapshot: DecisionSnapshot) -> DecisionVerdict:
    """Evaluate only the rolling seed/context part of the shared contract.

    A ``selected`` verdict from this function means only "seed stage can still
    become a signal"; it is not a trade signal and has no category/entry price.
    """

    if snapshot.contract_id != ROLLING_DECISION_CONTRACT_ID:
        return _rejected(snapshot, "unknown", "contract_id_mismatch", {"contract_id": snapshot.contract_id})
    spec = ROLLING_PROFILE_SPECS.get(snapshot.tf_set)
    if spec is None:
        return _rejected(snapshot, "rolling_htf_seed", "unsupported_tf_set", {"tf_set": snapshot.tf_set})

    dependency = _first_bad_dependency(snapshot)
    if dependency is not None:
        return _dependency_not_ready(snapshot, dependency.reason or dependency.name, (dependency,))

    seed = snapshot.rolling_seed
    if seed is None:
        return _dependency_not_ready(snapshot, "rolling_seed_missing", ())
    seed_dependency = _validate_seed(seed, spec)
    if seed_dependency is not None:
        return _dependency_not_ready(snapshot, seed_dependency.reason or "rolling_seed_invalid", (seed_dependency,))
    context_dependency = _validate_pre_seed_context(seed, spec)
    if context_dependency is not None:
        return _dependency_not_ready(snapshot, context_dependency.reason or "pre_seed_context_invalid", (context_dependency,))

    features = _derive_rolling_seed_stage_features(snapshot, spec, include_prior_spike=False, include_snapshot_hash=False)
    seed_reject = _seed_reject_reason(features)
    if seed_reject is not None:
        return _rejected(snapshot, "rolling_htf_seed", seed_reject, features)
    return DecisionVerdict(
        verdict="selected",
        snapshot=snapshot,
        features={
            **features,
            "seed_stage_verdict": "passed",
            "seed_stage_trading_signal": False,
        },
    )


def evaluate_seed_first(snapshot: DecisionSnapshot) -> DecisionVerdict:
    """Evaluate one exact rolling-seed + post-seed-confirm snapshot.

    This function is the shared strategy decision boundary. It returns only a
    signal verdict. Portfolio capacity, exchange position state, market order
    fills, slippage, fees, and live entry guards belong outside this core.
    """

    if snapshot.contract_id != ROLLING_DECISION_CONTRACT_ID:
        return _rejected(snapshot, "unknown", "contract_id_mismatch", {"contract_id": snapshot.contract_id})
    spec = ROLLING_PROFILE_SPECS.get(snapshot.tf_set)
    if spec is None:
        return _rejected(snapshot, "rolling_htf_seed", "unsupported_tf_set", {"tf_set": snapshot.tf_set})

    dependency = _first_bad_dependency(snapshot)
    if dependency is not None:
        return _dependency_not_ready(snapshot, dependency.reason or dependency.name, (dependency,))

    seed = snapshot.rolling_seed
    if seed is None:
        return _dependency_not_ready(snapshot, "rolling_seed_missing", ())
    confirm = snapshot.ltf_confirm
    if confirm is None:
        return _dependency_not_ready(snapshot, "ltf_confirm_missing", ())

    seed_stage = evaluate_rolling_seed_stage(snapshot)
    if seed_stage.verdict != "selected":
        return seed_stage

    confirm_dependency = _validate_confirm(seed, confirm, spec)
    if confirm_dependency is not None:
        return _dependency_not_ready(snapshot, confirm_dependency.reason or "ltf_confirm_invalid", (confirm_dependency,))

    features = _derive_seed_first_features(snapshot, spec)

    seed_reject = _seed_reject_reason(features)
    if seed_reject is not None:
        return _rejected(snapshot, "rolling_htf_seed", seed_reject, features)

    confirm_reject = _confirm_reject_reason(features)
    if confirm_reject is not None:
        return _rejected(snapshot, "ltf_confirm", confirm_reject, features)

    risk_reject = _risk_reject_reason(features)
    if risk_reject is not None:
        return _rejected(snapshot, "risk", risk_reject, features)

    matched = match_rolling_categories(features)
    if not matched:
        return _rejected(snapshot, "category", "rolling_category_not_matched", features)

    category_id = matched[0]
    entry = _finite_float(features.get("signal_entry_price"))
    stop = _finite_float(features.get("initial_stop_at_decision"))
    tp1 = _finite_float(features.get("tp1_at_decision"))
    return DecisionVerdict(
        verdict="selected",
        snapshot=snapshot,
        category_id=category_id,
        signal_entry_price=entry,
        initial_stop_price=stop,
        tp1_price=tp1,
        features={
            **features,
            "rolling_runner_category_id": category_id,
            "rolling_runner_matched_categories": "|".join(matched),
            "rolling_runner_category_priority_rank": rolling_category_priority_rank(category_id),
        },
    )


def _is_terminal_seed_reject(verdict: DecisionVerdict) -> bool:
    """Return true when later confirm candles cannot repair the rejection."""

    if verdict.verdict != "rejected":
        return False
    return any(str(reject.stage) == "rolling_htf_seed" for reject in verdict.rejects)


def _derive_rolling_seed_stage_features(
    snapshot: DecisionSnapshot,
    spec: RollingProfileSpec,
    *,
    include_prior_spike: bool = True,
    include_snapshot_hash: bool = True,
) -> dict[str, float | int | str | bool | None]:
    seed = snapshot.rolling_seed
    if seed is None:
        return dict(snapshot.features)

    htf_ms = spec.htf_seconds * 1000
    seed_candle = _aggregate_candles(seed.seed_candles)
    context = _contract_context_candles(seed, spec)
    baseline = context[-ROLLING_BASELINE_WINDOWS:]
    dormancy = context[-ROLLING_DORMANCY_WINDOWS:]
    pregrowth = context[-ROLLING_PREGROWTH_WINDOWS:]

    baseline_quote = _positive_median([item.quote_volume for item in baseline])
    baseline_trades = _positive_median([item.number_of_trades for item in baseline])
    dormancy_quote = _positive_median([item.quote_volume for item in dormancy])
    dormancy_trades = _positive_median([item.number_of_trades for item in dormancy])
    dormancy_range_pct_median = _positive_median([_range_pct(item) for item in dormancy])

    htf_return = _candle_return(seed_candle)
    htf_quote_ratio = _safe_divide(seed_candle.quote_volume, baseline_quote)
    htf_trade_ratio = _safe_divide(seed_candle.number_of_trades, baseline_trades)
    htf_range_pct = _range_pct(seed_candle)
    dormancy_quote_ratio = _safe_divide(seed_candle.quote_volume, dormancy_quote)
    dormancy_trade_ratio = _safe_divide(seed_candle.number_of_trades, dormancy_trades)

    pregrowth_return_pct = _safe_divide(float(pregrowth[-1].close) - float(pregrowth[0].open), float(pregrowth[0].open)) if pregrowth else float("nan")
    pregrowth_single_returns = [_candle_return(item) for item in pregrowth]
    pregrowth_max_single = _finite_max(pregrowth_single_returns)
    pregrowth_min_single = _finite_min(pregrowth_single_returns)
    pregrowth_start = float(pregrowth[0].open) if pregrowth else float("nan")
    pregrowth_min_path_return = _safe_divide(_finite_min([item.low for item in pregrowth]) - pregrowth_start, pregrowth_start) if pregrowth else float("nan")
    pregrowth_high = _finite_max([item.high for item in pregrowth])
    pregrowth_low = _finite_min([item.low for item in pregrowth])
    pregrowth_range_pct = _safe_divide(pregrowth_high - pregrowth_low, pregrowth_start) if pregrowth else float("nan")
    positive_steps = [1.0 if float(item.close) > float(item.open) else 0.0 for item in pregrowth]
    pregrowth_positive_share = _safe_divide(sum(positive_steps), len(positive_steps)) if positive_steps else float("nan")
    pre_seed_dump_rebound_ok = _pre_seed_dump_rebound_ok(
        pregrowth_return_pct=pregrowth_return_pct,
        pregrowth_min_path_return_pct=pregrowth_min_path_return,
        pregrowth_min_single_return_pct=pregrowth_min_single,
        pregrowth_positive_step_share=pregrowth_positive_share,
        pregrowth_range_pct=pregrowth_range_pct,
    )

    prior_spike = _prior_spike_features(context=context, current=seed_candle) if include_prior_spike else {}
    htf_ltf_features = _htf_internal_ltf_features(seed.seed_candles, htf_open=seed_candle.open, htf_close=seed_candle.close)
    hash_features = (
        {
            "snapshot_hash": decision_snapshot_hash(snapshot),
            "snapshot_match_key": decision_snapshot_match_key(snapshot),
        }
        if include_snapshot_hash
        else {
            "snapshot_hash": "",
            "snapshot_match_key": "",
        }
    )

    return {
        **dict(snapshot.features),
        "contract_id": snapshot.contract_id,
        "core_version": snapshot.core_version,
        "tf_set": snapshot.tf_set,
        "rolling_runner_tf_set": snapshot.tf_set,
        "rolling_runner_htf_timeframe_ms": htf_ms,
        "rolling_runner_ltf_timeframe_ms": spec.ltf_seconds * 1000,
        "symbol": snapshot.symbol,
        "decision_time_ms": snapshot.decision_time_ms,
        **hash_features,
        "rolling_htf_open_ms": int(seed.seed_open_ms),
        "rolling_htf_close_ms": int(seed.seed_close_ms),
        "anomaly_open": seed_candle.open,
        "anomaly_high": seed_candle.high,
        "anomaly_low": seed_candle.low,
        "anomaly_close": seed_candle.close,
        "anomaly_quote_volume": seed_candle.quote_volume,
        "anomaly_number_of_trades": seed_candle.number_of_trades,
        "baseline_quote_volume_median": baseline_quote,
        "baseline_number_of_trades_median": baseline_trades,
        "dormancy_quote_volume_median": dormancy_quote,
        "dormancy_number_of_trades_median": dormancy_trades,
        "dormancy_range_pct_median": dormancy_range_pct_median,
        "htf_return_pct": htf_return,
        "htf_range_pct": htf_range_pct,
        "htf_quote_ratio": htf_quote_ratio,
        "htf_trade_ratio": htf_trade_ratio,
        "dormancy_to_anomaly_quote_ratio": dormancy_quote_ratio,
        "dormancy_to_anomaly_trade_ratio": dormancy_trade_ratio,
        "pregrowth_return_pct": pregrowth_return_pct,
        "pregrowth_max_single_return_pct": pregrowth_max_single,
        "pregrowth_min_single_return_pct": pregrowth_min_single,
        "pregrowth_min_path_return_pct": pregrowth_min_path_return,
        "pregrowth_range_pct": pregrowth_range_pct,
        "pregrowth_positive_step_share": pregrowth_positive_share,
        "pre_seed_dump_rebound_ok": pre_seed_dump_rebound_ok,
        **prior_spike,
        **htf_ltf_features,
    }


def _derive_seed_first_features(snapshot: DecisionSnapshot, spec: RollingProfileSpec) -> dict[str, float | int | str | bool | None]:
    seed = snapshot.rolling_seed
    confirm = snapshot.ltf_confirm
    if seed is None or confirm is None:
        return dict(snapshot.features)

    htf_ms = spec.htf_seconds * 1000
    seed_candle = _aggregate_candles(seed.seed_candles)
    seed_features = _derive_rolling_seed_stage_features(snapshot, spec)
    baseline_quote = _finite_float(seed_features.get("baseline_quote_volume_median"))
    baseline_trades = _finite_float(seed_features.get("baseline_number_of_trades_median"))
    ltf_features = _ltf_confirmation_features(confirm.confirm_candles, baseline_quote=baseline_quote, baseline_trades=baseline_trades, htf_ms=htf_ms)

    structural_low = min(seed_candle.low, _finite_min([item.low for item in confirm.confirm_candles]))
    entry = float(confirm.confirm_candles[-1].close)
    stop = structural_low * (1.0 - ROLLING_STRUCTURAL_STOP_BUFFER_PCT)
    initial_risk = entry - stop
    initial_risk_pct = _safe_divide(initial_risk, entry)
    tp1 = entry + ROLLING_TP1_R * initial_risk if math.isfinite(initial_risk) else float("nan")

    return {
        **seed_features,
        "snapshot_hash": decision_snapshot_hash(snapshot),
        "snapshot_match_key": decision_snapshot_match_key(snapshot),
        "decision_time_ms": snapshot.decision_time_ms,
        "confirm_start_ms": int(confirm.confirm_start_ms),
        "confirm_end_ms": int(confirm.confirm_end_ms),
        "confirmation_candles": int(len(confirm.confirm_candles)),
        "signal_entry_price": entry,
        "initial_stop_at_decision": stop,
        "initial_risk_pct_at_decision": initial_risk_pct,
        "tp1_at_decision": tp1,
        **ltf_features,
    }


def _seed_reject_reason(features: Mapping[str, object]) -> str | None:
    if _finite_float(features.get("htf_return_pct")) < ROLLING_SEED_MIN_HTF_RETURN_PCT:
        return "seed_htf_return_below_min"
    if _finite_float(features.get("htf_quote_ratio")) < ROLLING_SEED_MIN_HTF_QUOTE_RATIO:
        return "seed_htf_quote_ratio_below_min"
    if _finite_float(features.get("htf_trade_ratio")) < ROLLING_SEED_MIN_HTF_TRADE_RATIO:
        return "seed_htf_trade_ratio_below_min"
    if features.get("pre_seed_dump_rebound_ok") is False:
        return "pre_seed_dump_rebound_pattern"
    if features.get("htf_ltf_sustained_flow_ok") is False:
        return "seed_ltf_flow_not_sustained"
    return None


def _pre_seed_dump_rebound_ok(
    *,
    pregrowth_return_pct: object,
    pregrowth_min_path_return_pct: object,
    pregrowth_min_single_return_pct: object,
    pregrowth_positive_step_share: object,
    pregrowth_range_pct: object,
) -> bool:
    """Reject rebound-after-dump seeds; pump awakening should emerge from quiet dormancy."""

    positive_share = _finite_float(pregrowth_positive_step_share)
    if not math.isfinite(positive_share):
        return True
    mostly_red = positive_share <= ROLLING_PRE_SEED_MAX_DUMP_POSITIVE_STEP_SHARE
    cumulative_drop = _finite_float(pregrowth_return_pct) <= ROLLING_PRE_SEED_MAX_DUMP_RETURN_PCT
    path_drop = _finite_float(pregrowth_min_path_return_pct) <= ROLLING_PRE_SEED_MAX_DUMP_PATH_RETURN_PCT
    single_drop = _finite_float(pregrowth_min_single_return_pct) <= ROLLING_PRE_SEED_MAX_SINGLE_DUMP_RETURN_PCT
    noisy_dump = (
        _finite_float(pregrowth_range_pct) >= ROLLING_PRE_SEED_MAX_NOISY_DUMP_RANGE_PCT
        and _finite_float(pregrowth_min_path_return_pct) <= ROLLING_PRE_SEED_MAX_DUMP_RETURN_PCT
        and positive_share <= 0.50
    )
    return not (mostly_red and (cumulative_drop or path_drop or single_drop or noisy_dump))


def _confirm_reject_reason(features: Mapping[str, object]) -> str | None:
    if _finite_float(features.get("ltf_confirm_return_pct")) < ROLLING_LTF_MIN_CONFIRM_RETURN_PCT:
        return "ltf_confirm_return_below_min"
    if _finite_float(features.get("ltf_quote_pace_ratio")) < ROLLING_LTF_MIN_QUOTE_PACE_RATIO:
        return "ltf_quote_pace_ratio_below_min"
    if _finite_float(features.get("ltf_trade_pace_ratio")) < ROLLING_LTF_MIN_TRADE_PACE_RATIO:
        return "ltf_trade_pace_ratio_below_min"
    if _finite_float(features.get("ltf_second_half_return_pct")) < ROLLING_LTF_MIN_SECOND_HALF_RETURN_PCT:
        return "ltf_second_half_return_below_min"
    if _finite_float(features.get("ltf_quote_acceleration")) < ROLLING_LTF_MIN_QUOTE_ACCELERATION:
        return "ltf_quote_acceleration_below_min"
    if _finite_float(features.get("ltf_trade_acceleration")) < ROLLING_LTF_MIN_TRADE_ACCELERATION:
        return "ltf_trade_acceleration_below_min"
    anomaly_low = _finite_float(features.get("anomaly_low"))
    confirm_low = _finite_float(features.get("ltf_confirm_low"))
    if math.isfinite(anomaly_low) and math.isfinite(confirm_low) and confirm_low < anomaly_low:
        return "ltf_confirm_undercut_seed_low"
    return None


def _risk_reject_reason(features: Mapping[str, object]) -> str | None:
    risk = _finite_float(features.get("initial_risk_pct_at_decision"))
    initial_stop = _finite_float(features.get("initial_stop_at_decision"))
    entry = _finite_float(features.get("signal_entry_price"))
    if not math.isfinite(initial_stop) or not math.isfinite(entry) or initial_stop >= entry:
        return "initial_stop_not_below_signal_entry"
    if not math.isfinite(risk) or risk <= 0.0:
        return "initial_risk_invalid"
    if risk > ROLLING_MAX_INITIAL_RISK_PCT:
        return "initial_risk_above_max"
    return None


def _ltf_confirmation_features(
    candles: tuple[DecisionCandle, ...],
    *,
    baseline_quote: float,
    baseline_trades: float,
    htf_ms: int,
) -> dict[str, float | int | str | bool | None]:
    duration_ms = max(1, int(candles[-1].close_time_ms) - int(candles[0].open_time_ms)) if candles else 1
    quote = _finite_sum([item.quote_volume for item in candles])
    trades = _finite_sum([item.number_of_trades for item in candles])
    expected_quote = baseline_quote * duration_ms / htf_ms
    expected_trades = baseline_trades * duration_ms / htf_ms
    first_open = float(candles[0].open) if candles else float("nan")
    last_close = float(candles[-1].close) if candles else float("nan")
    split = max(1, len(candles) // 2)
    first_half = candles[:split]
    second_half = candles[split:]
    if not second_half:
        second_half = candles[-1:]
    first_quote = _finite_sum([item.quote_volume for item in first_half])
    second_quote = _finite_sum([item.quote_volume for item in second_half])
    first_trades = _finite_sum([item.number_of_trades for item in first_half])
    second_trades = _finite_sum([item.number_of_trades for item in second_half])
    taker_values = [item.taker_buy_quote_volume for item in candles if item.taker_buy_quote_volume is not None]
    taker_quote = _finite_sum(taker_values) if taker_values else float("nan")
    second_open = float(second_half[0].open) if second_half else float("nan")
    second_close = float(second_half[-1].close) if second_half else float("nan")
    return {
        "ltf_confirm_return_pct": _safe_divide(last_close - first_open, first_open),
        "ltf_confirm_low": _finite_min([item.low for item in candles]),
        "ltf_confirm_high": _finite_max([item.high for item in candles]),
        "ltf_quote_volume": quote,
        "ltf_number_of_trades": trades,
        "ltf_quote_pace_ratio": _safe_divide(quote, expected_quote),
        "ltf_trade_pace_ratio": _safe_divide(trades, expected_trades),
        "ltf_second_half_return_pct": _safe_divide(second_close - second_open, second_open),
        "ltf_quote_acceleration": _safe_divide(second_quote, first_quote),
        "ltf_trade_acceleration": _safe_divide(second_trades, first_trades),
        "ltf_taker_buy_quote_share": _safe_divide(taker_quote, quote),
    }


def _htf_internal_ltf_features(
    seed_ltf_candles: tuple[DecisionCandle, ...],
    *,
    htf_open: float,
    htf_close: float,
) -> dict[str, float | int | str | bool | None]:
    if not seed_ltf_candles:
        return {
            "htf_ltf_status": "missing_ltf_inside_htf",
            "htf_ltf_candles": 0,
            "htf_ltf_sustained_flow_ok": False,
            "htf_ltf_trade_count_status": "missing",
        }
    quote_total = _finite_sum([item.quote_volume for item in seed_ltf_candles])
    trade_total = _finite_sum([item.number_of_trades for item in seed_ltf_candles])
    quote_top = _finite_max([item.quote_volume for item in seed_ltf_candles])
    trade_top = _finite_max([item.number_of_trades for item in seed_ltf_candles])
    split = max(1, len(seed_ltf_candles) // 2)
    first_half = seed_ltf_candles[:split]
    second_half = seed_ltf_candles[split:] or seed_ltf_candles[-1:]
    first_quote = _finite_sum([item.quote_volume for item in first_half])
    second_quote = _finite_sum([item.quote_volume for item in second_half])
    first_trades = _finite_sum([item.number_of_trades for item in first_half])
    second_trades = _finite_sum([item.number_of_trades for item in second_half])
    midpoint = (float(htf_open) + float(htf_close)) / 2.0
    green_share = _safe_divide(sum(1 for item in seed_ltf_candles if float(item.close) > float(item.open)), len(seed_ltf_candles))
    close_above_mid_share = _safe_divide(sum(1 for item in seed_ltf_candles if float(item.close) >= midpoint), len(seed_ltf_candles))
    second_half_return = _safe_divide(float(second_half[-1].close) - float(second_half[0].open), float(second_half[0].open))
    quote_acceleration = _safe_divide(second_quote, first_quote)
    trade_acceleration = _safe_divide(second_trades, first_trades)
    quote_top1_share = _safe_divide(quote_top, quote_total)
    trade_top1_share = _safe_divide(trade_top, trade_total)
    tail_count = min(4, max(2, len(seed_ltf_candles) // 3))
    tail = seed_ltf_candles[-tail_count:]
    tail_quote = _finite_sum([item.quote_volume for item in tail])
    tail_trades = _finite_sum([item.number_of_trades for item in tail])
    tail_green_share = _safe_divide(sum(1 for item in tail if float(item.close) > float(item.open)), len(tail))
    tail_quote_share = _safe_divide(tail_quote, quote_total)
    tail_trade_share = _safe_divide(tail_trades, trade_total)
    trade_count_status = "ok" if math.isfinite(trade_total) and trade_total > 0.0 else "missing"
    sustained_flow_ok = (
        math.isfinite(quote_total)
        and quote_total > 0.0
        and trade_count_status == "ok"
        and math.isfinite(quote_top1_share)
        and quote_top1_share <= 0.55
        and math.isfinite(trade_top1_share)
        and trade_top1_share <= 0.55
        and math.isfinite(green_share)
        and green_share >= 0.50
        and math.isfinite(second_half_return)
        and second_half_return >= 0.0
        and math.isfinite(quote_acceleration)
        and quote_acceleration >= 0.75
        and math.isfinite(trade_acceleration)
        and trade_acceleration >= 0.75
        and math.isfinite(tail_quote_share)
        and tail_quote_share >= 0.20
        and math.isfinite(tail_trade_share)
        and tail_trade_share >= 0.20
        and math.isfinite(tail_green_share)
        and tail_green_share >= 0.50
    )
    return {
        "htf_ltf_status": "ok",
        "htf_ltf_candles": int(len(seed_ltf_candles)),
        "htf_ltf_quote_volume": quote_total,
        "htf_ltf_number_of_trades": trade_total,
        "htf_ltf_quote_top1_share": quote_top1_share,
        "htf_ltf_trade_top1_share": trade_top1_share,
        "htf_ltf_green_share": green_share,
        "htf_ltf_close_above_mid_share": close_above_mid_share,
        "htf_ltf_second_half_return_pct": second_half_return,
        "htf_ltf_quote_acceleration": quote_acceleration,
        "htf_ltf_trade_acceleration": trade_acceleration,
        "htf_ltf_tail_candles": int(len(tail)),
        "htf_ltf_tail_quote_share": tail_quote_share,
        "htf_ltf_tail_trade_share": tail_trade_share,
        "htf_ltf_tail_green_share": tail_green_share,
        "htf_ltf_sustained_flow_ok": bool(sustained_flow_ok),
        "htf_ltf_trade_count_status": trade_count_status,
    }


def _prior_spike_features(*, context: tuple[DecisionCandle, ...], current: _AggregateCandle) -> dict[str, float | int | str]:
    ordered = tuple(sorted(context, key=lambda item: item.open_time_ms))
    lookback_start = int(current.open_time_ms) - ROLLING_PRIOR_SPIKE_LOOKBACK_MS
    spike_indices: list[int] = []
    for idx, candle in enumerate(ordered):
        if int(candle.open_time_ms) < lookback_start:
            continue
        prev = ordered[max(0, idx - ROLLING_BASELINE_WINDOWS):idx]
        baseline_quote = _positive_median([item.quote_volume for item in prev])
        baseline_trades = _positive_median([item.number_of_trades for item in prev])
        if baseline_quote <= 0.0 or baseline_trades <= 0.0:
            continue
        if (
            _safe_divide(candle.quote_volume, baseline_quote) >= 3.0
            and _safe_divide(candle.number_of_trades, baseline_trades) >= 3.0
            and _candle_return(candle) >= 0.0
        ):
            spike_indices.append(idx)
    lookback_ms = max(0, int(ordered[-1].close_time_ms) - int(ordered[0].open_time_ms)) if ordered else 0
    lookback_status = "ok" if lookback_ms >= ROLLING_PRIOR_SPIKE_LOOKBACK_MS else "short_context"
    if not spike_indices:
        return {
            "prior_spike_lookback_ms": int(lookback_ms),
            "prior_spike_context_windows": int(len(ordered)),
            "prior_spike_lookback_status": lookback_status,
            "prior_spike_count_24h": 0,
            "prior_spike_median_quote": float("nan"),
            "prior_spike_max_quote": float("nan"),
            "current_vs_prior_spike_median_quote": float("nan"),
            "current_vs_prior_spike_max_quote": float("nan"),
            "prior_spike_next_decay50_share": float("nan"),
            "prior_spike_median_next_quote_ratio": float("nan"),
            "prior_spike_median_bars_to_decay50": float("nan"),
        }
    prior_quote = [ordered[idx].quote_volume for idx in spike_indices]
    median_quote = _median(prior_quote)
    max_quote = _finite_max(prior_quote)
    next_quote_ratios: list[float] = []
    bars_to_decay: list[float] = []
    quote_path = [item.quote_volume for item in ordered] + [current.quote_volume]
    current_idx = len(quote_path) - 1
    for spike_idx in spike_indices:
        prior_quote_value = _finite_float(quote_path[spike_idx])
        if not math.isfinite(prior_quote_value) or prior_quote_value <= 0.0:
            continue
        if spike_idx + 1 < len(quote_path):
            next_quote_ratios.append(_safe_divide(quote_path[spike_idx + 1], prior_quote_value))
        found = False
        for idx in range(spike_idx + 1, min(current_idx, spike_idx + 12) + 1):
            if _finite_float(quote_path[idx]) < 0.5 * prior_quote_value:
                bars_to_decay.append(float(idx - spike_idx))
                found = True
                break
        if not found:
            bars_to_decay.append(float("nan"))
    finite_next = _finite_values(next_quote_ratios)
    return {
        "prior_spike_lookback_ms": int(lookback_ms),
        "prior_spike_context_windows": int(len(ordered)),
        "prior_spike_lookback_status": lookback_status,
        "prior_spike_count_24h": int(len(spike_indices)),
        "prior_spike_median_quote": median_quote,
        "prior_spike_max_quote": max_quote,
        "current_vs_prior_spike_median_quote": _safe_divide(current.quote_volume, median_quote),
        "current_vs_prior_spike_max_quote": _safe_divide(current.quote_volume, max_quote),
        "prior_spike_next_decay50_share": _safe_divide(sum(1 for item in finite_next if item < 0.5), len(finite_next)) if finite_next else float("nan"),
        "prior_spike_median_next_quote_ratio": _median(finite_next),
        "prior_spike_median_bars_to_decay50": _median(bars_to_decay),
    }


def _aggregate_candles(candles: tuple[DecisionCandle, ...]) -> _AggregateCandle:
    ordered = tuple(sorted(candles, key=lambda item: item.open_time_ms))
    taker_values = [item.taker_buy_quote_volume for item in ordered if item.taker_buy_quote_volume is not None]
    return _AggregateCandle(
        open_time_ms=int(ordered[0].open_time_ms),
        close_time_ms=int(ordered[-1].close_time_ms),
        open=float(ordered[0].open),
        high=_finite_max([item.high for item in ordered]),
        low=_finite_min([item.low for item in ordered]),
        close=float(ordered[-1].close),
        quote_volume=_finite_sum([item.quote_volume for item in ordered]),
        number_of_trades=_finite_sum([item.number_of_trades for item in ordered]),
        taker_buy_quote_volume=_finite_sum(taker_values) if taker_values else None,
    )


def _first_bad_dependency(snapshot: DecisionSnapshot) -> DataDependency | None:
    deps: list[DataDependency] = list(snapshot.dependencies)
    if snapshot.rolling_seed is not None:
        deps.extend(snapshot.rolling_seed.dependencies)
    if snapshot.ltf_confirm is not None:
        deps.extend(snapshot.ltf_confirm.dependencies)
    for dependency in deps:
        if dependency.status != "ok":
            return dependency
    return None


def _validate_seed(seed: RollingSeedSnapshot, spec: RollingProfileSpec) -> DataDependency | None:
    if seed.tf_set != spec.tf_set:
        return DataDependency(name="rolling_seed", status="error", reason="seed_tf_set_mismatch")
    expected_step_ms = spec.ltf_seconds * 1000
    expected_candles = spec.htf_seconds // spec.ltf_seconds
    if len(seed.seed_candles) != expected_candles:
        return DataDependency(name="rolling_seed", status="missing", reason="seed_candle_count_mismatch")
    if int(seed.seed_open_ms) != int(seed.seed_candles[0].open_time_ms) or int(seed.seed_close_ms) != int(seed.seed_candles[-1].close_time_ms):
        return DataDependency(name="rolling_seed", status="error", reason="seed_bounds_do_not_match_candles")
    return _continuity_dependency("rolling_seed", seed.seed_candles, expected_step_ms=expected_step_ms)


def _contract_context_candles(seed: RollingSeedSnapshot, spec: RollingProfileSpec) -> tuple[DecisionCandle, ...]:
    """Return only the contract-defined closed pre-seed context slice.

    Adapters may keep more history for efficiency. The core deliberately ignores
    extra history outside the deterministic context contract so live/backtest
    parity hashes do not depend on ring/cache retention length. The long context
    is allowed to end shortly before a rolling seed when the cheap baseline
    source is minute/HTF candles rather than the exact LTF grid.
    """

    required = rolling_context_windows_for_spec(spec)
    ordered = tuple(sorted(seed.pre_seed_context_candles, key=lambda item: item.open_time_ms))
    eligible = tuple(
        item
        for item in ordered
        if int(item.close_time_ms) <= int(seed.seed_open_ms)
    )
    return eligible[-required:]


def _validate_pre_seed_context(seed: RollingSeedSnapshot, spec: RollingProfileSpec) -> DataDependency | None:
    context = _contract_context_candles(seed, spec)
    required = rolling_context_windows_for_spec(spec)
    if len(context) < required:
        return DataDependency(
            name="pre_seed_context",
            status="missing",
            reason="contract_seed_aligned_context_not_ready",
            asof_ms=seed.seed_open_ms,
        )
    if context[-1].close_time_ms > seed.seed_open_ms:
        return DataDependency(name="pre_seed_context", status="error", reason="context_overlaps_seed", asof_ms=seed.seed_open_ms)
    context_gap_ms = int(seed.seed_open_ms) - int(context[-1].close_time_ms)
    if context_gap_ms > ROLLING_MAX_PRE_SEED_CONTEXT_GAP_MS:
        return DataDependency(name="pre_seed_context", status="stale", reason="closed_context_too_stale_for_seed_open", asof_ms=seed.seed_open_ms)
    expected_first_open = int(context[-1].close_time_ms) - required * int(spec.htf_seconds) * 1000
    if int(context[0].open_time_ms) != expected_first_open:
        return DataDependency(name="pre_seed_context", status="gap", reason="closed_context_wrong_start", asof_ms=seed.seed_open_ms)
    return _rolling_context_dependency(
        "pre_seed_context",
        context,
        window_width_ms=spec.htf_seconds * 1000,
        expected_step_ms=spec.htf_seconds * 1000,
    )


def _validate_confirm(seed: RollingSeedSnapshot, confirm: LtfConfirmSnapshot, spec: RollingProfileSpec) -> DataDependency | None:
    if not confirm.confirm_candles:
        return DataDependency(name="ltf_confirm", status="missing", reason="confirm_candles_missing", asof_ms=seed.seed_close_ms)
    if int(confirm.confirm_start_ms) != int(seed.seed_close_ms):
        return DataDependency(name="ltf_confirm", status="gap", reason="confirm_does_not_start_at_seed_close", asof_ms=seed.seed_close_ms)
    if int(confirm.confirm_start_ms) != int(confirm.confirm_candles[0].open_time_ms) or int(confirm.confirm_end_ms) != int(confirm.confirm_candles[-1].close_time_ms):
        return DataDependency(name="ltf_confirm", status="error", reason="confirm_bounds_do_not_match_candles")
    count = len(confirm.confirm_candles)
    if count < spec.min_confirm_candles:
        return DataDependency(name="ltf_confirm", status="missing", reason="confirm_candle_count_below_min", asof_ms=seed.seed_close_ms)
    if count > spec.max_confirm_candles:
        return DataDependency(name="ltf_confirm", status="error", reason="confirm_candle_count_above_max")
    return _continuity_dependency("ltf_confirm", confirm.confirm_candles, expected_step_ms=spec.ltf_seconds * 1000)


def _rolling_context_dependency(
    name: str,
    candles: tuple[DecisionCandle, ...],
    *,
    window_width_ms: int,
    expected_step_ms: int,
) -> DataDependency | None:
    if not candles:
        return DataDependency(name=name, status="missing", reason="empty_candles")
    ordered = tuple(sorted(candles, key=lambda item: item.open_time_ms))
    first_open = int(ordered[0].open_time_ms)
    for idx, candle in enumerate(ordered):
        if candle.source_status not in ("", "ok"):
            return DataDependency(name=name, status="degraded", reason=f"source_status:{candle.source_status}", asof_ms=candle.close_time_ms, source=candle.source)
        if not all(math.isfinite(_finite_float(value)) for value in (candle.open, candle.high, candle.low, candle.close, candle.quote_volume, candle.number_of_trades)):
            return DataDependency(name=name, status="error", reason="non_finite_candle_value", asof_ms=candle.close_time_ms, source=candle.source)
        if candle.open <= 0.0 or candle.high <= 0.0 or candle.low <= 0.0 or candle.close <= 0.0:
            return DataDependency(name=name, status="error", reason="non_positive_ohlc", asof_ms=candle.close_time_ms, source=candle.source)
        expected_open = first_open + idx * int(expected_step_ms)
        expected_close = expected_open + int(window_width_ms)
        if int(candle.open_time_ms) != expected_open or int(candle.close_time_ms) != expected_close:
            return DataDependency(name=name, status="gap", reason="non_contiguous_rolling_windows", asof_ms=candle.close_time_ms, source=candle.source)
    return None


def _continuity_dependency(name: str, candles: tuple[DecisionCandle, ...], *, expected_step_ms: int) -> DataDependency | None:
    if not candles:
        return DataDependency(name=name, status="missing", reason="empty_candles")
    ordered = tuple(sorted(candles, key=lambda item: item.open_time_ms))
    for idx, candle in enumerate(ordered):
        if candle.source_status not in ("", "ok"):
            return DataDependency(name=name, status="degraded", reason=f"source_status:{candle.source_status}", asof_ms=candle.close_time_ms, source=candle.source)
        if not all(math.isfinite(_finite_float(value)) for value in (candle.open, candle.high, candle.low, candle.close, candle.quote_volume, candle.number_of_trades)):
            return DataDependency(name=name, status="error", reason="non_finite_candle_value", asof_ms=candle.close_time_ms, source=candle.source)
        if candle.open <= 0.0 or candle.high <= 0.0 or candle.low <= 0.0 or candle.close <= 0.0:
            return DataDependency(name=name, status="error", reason="non_positive_ohlc", asof_ms=candle.close_time_ms, source=candle.source)
        expected_open = int(ordered[0].open_time_ms) + idx * int(expected_step_ms)
        expected_close = expected_open + int(expected_step_ms)
        if int(candle.open_time_ms) != expected_open or int(candle.close_time_ms) != expected_close:
            return DataDependency(name=name, status="gap", reason="non_contiguous_candles", asof_ms=candle.close_time_ms, source=candle.source)
    return None


def _dependency_not_ready(snapshot: DecisionSnapshot, reason: str, dependencies: tuple[DataDependency, ...]) -> DecisionVerdict:
    return DecisionVerdict(
        verdict="data_dependency_not_ready",
        snapshot=snapshot,
        dependencies=dependencies,
        rejects=(DecisionReject(stage="data_dependency", reason=reason),),
        features={
            "snapshot_hash": decision_snapshot_hash(snapshot),
            "snapshot_match_key": decision_snapshot_match_key(snapshot),
        },
    )


def _rejected(
    snapshot: DecisionSnapshot,
    stage: DecisionStage,
    reason: str,
    features: Mapping[str, float | int | str | bool | None],
) -> DecisionVerdict:
    return DecisionVerdict(
        verdict="rejected",
        snapshot=snapshot,
        rejects=(DecisionReject(stage=stage, reason=reason, details=dict(features)),),
        features={
            **dict(features),
            "snapshot_hash": decision_snapshot_hash(snapshot),
            "snapshot_match_key": decision_snapshot_match_key(snapshot),
        },
    )


def _build_seed_first_core_smoke_snapshot() -> tuple[DecisionSnapshot, tuple[DecisionCandle, ...]]:
    """Build a deterministic selected-signal smoke fixture for local validation."""

    spec = ROLLING_PROFILE_SPECS["5m_30s"]
    htf_ms = spec.htf_seconds * 1000
    ltf_ms = spec.ltf_seconds * 1000
    required_context = rolling_context_windows_for_spec(spec)
    context: list[DecisionCandle] = []
    start = 1_700_000_000_000
    price = 100.0
    for idx in range(required_context):
        ts = start + idx * htf_ms
        quote = 100.0
        trades = 100
        open_price = price
        close_price = price * 1.0001
        if idx == required_context - 80:
            quote = 1000.0
            trades = 1000
            close_price = price * 1.01
        if idx == required_context - 3:
            close_price = price * 1.006
        context.append(
            DecisionCandle(
                open_time_ms=ts,
                close_time_ms=ts + htf_ms,
                open=open_price,
                high=max(open_price, close_price) * 1.001,
                low=min(open_price, close_price) * 0.999,
                close=close_price,
                quote_volume=quote,
                number_of_trades=trades,
                source="smoke",
            )
        )
        price = close_price
    seed_open = context[-1].close_time_ms
    seed: list[DecisionCandle] = []
    seed_open_price = price
    for idx in range(spec.htf_seconds // spec.ltf_seconds):
        ts = seed_open + idx * ltf_ms
        o = seed_open_price * (1.0 + 0.0015 * idx)
        c = o * (1.004 if idx == 9 else 1.0015)
        seed.append(
            DecisionCandle(
                open_time_ms=ts,
                close_time_ms=ts + ltf_ms,
                open=o,
                high=max(o, c) * 1.001,
                low=min(o, c) * 0.999,
                close=c,
                quote_volume=120.0,
                number_of_trades=120,
                source="smoke",
            )
        )
    post_seed: list[DecisionCandle] = []
    confirm_open = seed[-1].close_time_ms
    for idx in range(3):
        ts = confirm_open + idx * ltf_ms
        o = seed[-1].close * (1.0 + 0.001 * idx)
        c = o * 1.003
        post_seed.append(
            DecisionCandle(
                open_time_ms=ts,
                close_time_ms=ts + ltf_ms,
                open=o,
                high=max(o, c) * 1.001,
                low=min(o, c) * 0.9995,
                close=c,
                quote_volume=100.0,
                number_of_trades=100,
                source="smoke",
            )
        )
    snapshot = DecisionSnapshot(
        symbol="SMOKEUSDT",
        tf_set="5m_30s",
        decision_time_ms=post_seed[1].close_time_ms,
        source="test",
        rolling_seed=RollingSeedSnapshot(
            tf_set="5m_30s",
            seed_open_ms=seed[0].open_time_ms,
            seed_close_ms=seed[-1].close_time_ms,
            seed_candles=tuple(seed),
            pre_seed_context_candles=tuple(context),
        ),
    )
    return snapshot, tuple(post_seed)

def run_seed_first_core_self_smoke() -> None:
    """Minimal deterministic smoke; useful before adapters are migrated."""

    snapshot, post_seed = _build_seed_first_core_smoke_snapshot()
    first = evaluate_first_ltf_confirm_after_seed(snapshot, post_seed_ltf_candles=post_seed)
    second = evaluate_first_ltf_confirm_after_seed(snapshot, post_seed_ltf_candles=post_seed)
    if first.verdict != "selected":
        raise AssertionError(f"expected selected verdict, got {first.verdict}: {first.rejects}")
    if second.verdict != first.verdict or second.category_id != first.category_id:
        raise AssertionError("seed-first core smoke is not deterministic")
    if decision_snapshot_hash(second.snapshot) != decision_snapshot_hash(first.snapshot):
        raise AssertionError("seed-first core snapshot hash is not deterministic")
    if snapshot.rolling_seed is None:
        raise AssertionError("smoke snapshot missing seed")
    extra_context: list[DecisionCandle] = []
    first_context = snapshot.rolling_seed.pre_seed_context_candles[0]
    for idx in range(12, 0, -1):
        open_ms = int(first_context.open_time_ms) - idx * 300_000
        extra_context.append(
            DecisionCandle(
                open_time_ms=open_ms,
                close_time_ms=open_ms + 300_000,
                open=95.0,
                high=95.2,
                low=94.8,
                close=95.1,
                quote_volume=42.0,
                number_of_trades=42,
                source="smoke_extra",
            )
        )
    extra_snapshot = DecisionSnapshot(
        symbol=snapshot.symbol,
        tf_set=snapshot.tf_set,
        decision_time_ms=snapshot.decision_time_ms,
        source=snapshot.source,
        rolling_seed=RollingSeedSnapshot(
            tf_set=snapshot.rolling_seed.tf_set,
            seed_open_ms=snapshot.rolling_seed.seed_open_ms,
            seed_close_ms=snapshot.rolling_seed.seed_close_ms,
            seed_candles=snapshot.rolling_seed.seed_candles,
            pre_seed_context_candles=tuple(extra_context) + snapshot.rolling_seed.pre_seed_context_candles,
        ),
    )
    extra_first = evaluate_first_ltf_confirm_after_seed(extra_snapshot, post_seed_ltf_candles=post_seed)
    if decision_snapshot_hash(extra_first.snapshot) != decision_snapshot_hash(first.snapshot):
        raise AssertionError("extra adapter history changed the contract snapshot hash")
    if extra_first.verdict != first.verdict or extra_first.category_id != first.category_id:
        raise AssertionError("extra adapter history changed the core verdict")
    for key in ("signal_entry_price", "initial_stop_at_decision", "tp1_at_decision", "htf_quote_ratio", "ltf_quote_pace_ratio"):
        if second.features.get(key) != first.features.get(key):
            raise AssertionError(f"seed-first core smoke changed feature {key}")


if __name__ == "__main__":
    run_seed_first_core_self_smoke()
