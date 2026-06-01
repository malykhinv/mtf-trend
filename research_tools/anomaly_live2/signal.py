"""Live2 signal adapter for the shared rolling seed-first decision core.

The live adapter owns only orchestration state: it discovers closed rolling HTF
seeds from in-memory LTF candles, keeps them pending, builds a source-neutral
``DecisionSnapshot`` from closed live candles, and calls ``PumpDecisionCore``.
It must not maintain a second live-only C/A/S matcher or confirm-backward path.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from research_tools.pump_decision_core import (
    ROLLING_BASELINE_WINDOWS,
    ROLLING_CATEGORY_PRIORITY,
    ROLLING_DECISION_CONTRACT_ID,
    ROLLING_DECISION_CORE_VERSION,
    ROLLING_SEED_MIN_HTF_QUOTE_RATIO,
    ROLLING_SEED_MIN_HTF_RETURN_PCT,
    ROLLING_SEED_MIN_HTF_TRADE_RATIO,
    DataDependency,
    DecisionCandle,
    DecisionSnapshot,
    DecisionVerdict,
    LtfConfirmSnapshot,
    RollingSeedSnapshot,
    decision_snapshot_hash,
    decision_snapshot_match_key,
    evaluate_first_ltf_confirm_after_seed,
    rolling_category_priority_rank,
    rolling_context_windows_for_tf_set,
)

from .market_data.candles import Live2Candle
from .state import Live2RollingSeedState, SymbolState


LIVE2_ROLLING_RUNNER_CATEGORY_PRIORITY = ROLLING_CATEGORY_PRIORITY
LIVE2_ROLLING_RUNNER_PROFILES = (
    {
        "tf_set": "5m_30s",
        "htf_timeframe_ms": 300_000,
        "ltf_timeframe_ms": 30_000,
        "htf_candles": 10,
        "min_confirm_candles": 2,
        "max_confirm_candles": 8,
        "profile_rank": 1,
    },
    {
        "tf_set": "3m_30s",
        "htf_timeframe_ms": 180_000,
        "ltf_timeframe_ms": 30_000,
        "htf_candles": 6,
        "min_confirm_candles": 2,
        "max_confirm_candles": 6,
        "profile_rank": 2,
    },
    {
        "tf_set": "5m_15s",
        "htf_timeframe_ms": 300_000,
        "ltf_timeframe_ms": 15_000,
        "htf_candles": 20,
        "min_confirm_candles": 4,
        "max_confirm_candles": 16,
        "profile_rank": 3,
    },
    {
        "tf_set": "3m_15s",
        "htf_timeframe_ms": 180_000,
        "ltf_timeframe_ms": 15_000,
        "htf_candles": 12,
        "min_confirm_candles": 4,
        "max_confirm_candles": 12,
        "profile_rank": 4,
    },
)


@dataclass(frozen=True, slots=True)
class Live2SignalDecision:
    """Result of pure live2 signal evaluation before execution guards."""

    verdict: str
    reason: str
    category_id: str = ""
    category_rank: int | None = None
    signal_entry_price: float | None = None
    initial_stop_at_decision: float | None = None
    initial_risk_pct_at_decision: float | None = None
    tp1_at_decision: float | None = None
    features: dict[str, object] = field(default_factory=dict)
    dependency_reasons: tuple[str, ...] = ()
    reject_reasons: tuple[str, ...] = ()


class Live2SignalEngine:
    """Thin stream adapter around ``PumpDecisionCore``.

    Live and backtest may build candles differently, but live signal selection is
    only the shared seed-first decision contract. Execution guards, portfolio
    allocation, actual fills, and stop verification remain outside this class.
    """

    def __init__(
        self,
        *,
        category_ids: tuple[str, ...] = LIVE2_ROLLING_RUNNER_CATEGORY_PRIORITY,
        mark_stale_ms: int | None = None,
        oi_stale_ms: int | None = None,
        prior_context_stale_ms: int | None = None,
        rolling_context_repair: Callable[[str, int], dict[str, object]] | None = None,
    ) -> None:
        self.category_ids = tuple(category_ids)
        # Kept for constructor compatibility with the runner. Signal selection
        # no longer uses live-only context gates outside the shared core.
        self.mark_stale_ms = None if mark_stale_ms is None else int(mark_stale_ms)
        self.oi_stale_ms = None if oi_stale_ms is None else int(oi_stale_ms)
        self.prior_context_stale_ms = None if prior_context_stale_ms is None else int(prior_context_stale_ms)
        self.rolling_context_repair = rolling_context_repair
        self._total_evaluations = 0
        self._total_selected = 0
        self._total_rejected = 0
        self._total_data_dependency_not_ready = 0
        self._total_seed_context_not_ready_skipped = 0
        self._total_seed_basic_gate_rejected = 0
        self._total_seed_return_gate_rejected = 0
        self._selected_by_category: Counter[str] = Counter()
        self._dependency_reason_counts: Counter[str] = Counter()
        self._reject_reason_counts: Counter[str] = Counter()
        self._last_dependency_reasons: tuple[str, ...] = ()
        self._last_reject_reasons: tuple[str, ...] = ()

    def evaluate_seed_first_tick(self, *, state: SymbolState, candle: Live2Candle) -> Live2SignalDecision | None:
        """Evaluate live2 through the shared seed-first decision contract."""

        self._total_evaluations += 1
        self._discover_rolling_seeds(state=state, decision_candle=candle)
        ltf_timeframe_ms = int(candle.timeframe_ms)
        closed_ltf = _closed_candles_for_timeframe(state=state, timeframe_ms=ltf_timeframe_ms)
        if not state.rolling_pending_seeds:
            self._last_dependency_reasons = ()
            self._last_reject_reasons = ()
            return None

        candidate_verdicts: list[Live2SignalDecision] = []
        for seed in sorted(state.rolling_pending_seeds.values(), key=lambda item: (item.profile_rank, item.seed_open_ms, item.tf_set)):
            profile = _rolling_profile_by_tf_set(seed.tf_set)
            if profile is None:
                self._consume_rolling_seed(state=state, seed=seed, outcome="rejected")
                candidate_verdicts.append(
                    self._reject(
                        f"{seed.tf_set}:unsupported_rolling_profile",
                        features=_seed_state_features(seed=seed, decision_candle=candle),
                        reject_reasons=(f"{seed.tf_set}:unsupported_rolling_profile",),
                    )
                )
                continue
            max_confirm = int(profile["max_confirm_candles"])
            min_confirm = int(profile["min_confirm_candles"])
            profile_ltf_ms = int(profile["ltf_timeframe_ms"])
            if profile_ltf_ms != ltf_timeframe_ms:
                continue
            post_seed = _post_seed_ltf_candles(closed_ltf=closed_ltf, seed=seed, max_confirm=max_confirm, ltf_timeframe_ms=ltf_timeframe_ms)
            expiry_ms = int(seed.seed_close_ms) + max_confirm * ltf_timeframe_ms
            if len(post_seed) < min_confirm:
                if int(candle.close_time_ms) >= expiry_ms:
                    self._consume_rolling_seed(state=state, seed=seed, outcome="expired")
                    candidate_verdicts.append(
                        self._data_dependency_not_ready(
                            dependency_reasons=(f"{seed.tf_set}:post_seed_ltf_expired_before_min_confirm",),
                            reject_reasons=(),
                            features={
                                **_seed_state_features(seed=seed, decision_candle=candle),
                                "post_seed_ltf_candles_seen": len(post_seed),
                                "post_seed_ltf_expiry_ms": expiry_ms,
                            },
                        )
                    )
                continue
            snapshot = _build_live_seed_first_snapshot(
                state=state,
                seed=seed,
                closed_ltf=closed_ltf,
                decision_time_ms=int(candle.close_time_ms),
                ltf_timeframe_ms=ltf_timeframe_ms,
            )
            verdict = evaluate_first_ltf_confirm_after_seed(
                snapshot,
                post_seed_ltf_candles=tuple(_to_decision_candle(item) for item in post_seed),
            )
            live_decision = self._live_decision_from_core_verdict(
                verdict=verdict,
                seed=seed,
                post_seed=post_seed,
                decision_candle=candle,
            )
            if verdict.verdict == "selected":
                self._consume_rolling_seed(state=state, seed=seed, outcome="selected")
                return live_decision
            if verdict.verdict == "rejected" and len(post_seed) >= max_confirm:
                self._consume_rolling_seed(state=state, seed=seed, outcome="rejected")
                candidate_verdicts.append(live_decision)
                continue
            if verdict.verdict == "data_dependency_not_ready":
                state.rolling_dependency_seed_count += 1
                if int(candle.close_time_ms) >= expiry_ms:
                    self._consume_rolling_seed(state=state, seed=seed, outcome="expired")
                candidate_verdicts.append(live_decision)
                continue
            candidate_verdicts.append(live_decision)

        if not candidate_verdicts:
            return None
        for item in candidate_verdicts:
            if item.verdict == "data_dependency_not_ready":
                return item
        return candidate_verdicts[-1]

    def prepare_seed_first_tick(self, *, state: SymbolState, candle: Live2Candle) -> bool:
        """Discover current-tick seeds and report whether this TF can be evaluated."""

        self._discover_rolling_seeds(state=state, decision_candle=candle)
        return self.has_pending_seed_for_timeframe(state=state, timeframe_ms=int(candle.timeframe_ms))

    def has_pending_seed_for_timeframe(self, *, state: SymbolState, timeframe_ms: int) -> bool:
        for seed in state.rolling_pending_seeds.values():
            profile = _rolling_profile_by_tf_set(seed.tf_set)
            if profile is not None and int(profile["ltf_timeframe_ms"]) == int(timeframe_ms):
                return True
        return False

    def _discover_rolling_seeds(self, *, state: SymbolState, decision_candle: Live2Candle) -> None:
        ltf_timeframe_ms = int(decision_candle.timeframe_ms)
        if int(state.rolling_last_seed_discovery_close_ms_by_timeframe.get(ltf_timeframe_ms, 0) or 0) == int(decision_candle.close_time_ms):
            return
        closed_ltf = _closed_candles_for_timeframe(state=state, timeframe_ms=ltf_timeframe_ms)
        for profile in LIVE2_ROLLING_RUNNER_PROFILES:
            if int(profile["ltf_timeframe_ms"]) != ltf_timeframe_ms:
                continue
            tf_set = str(profile["tf_set"])
            htf_candles = int(profile["htf_candles"])
            htf_timeframe_ms = int(profile["htf_timeframe_ms"])
            profile_rank = int(profile["profile_rank"])
            seed_candles = _closed_segment_before(
                candles=closed_ltf,
                end_open_ms=int(decision_candle.close_time_ms),
                count=htf_candles,
                timeframe_ms=ltf_timeframe_ms,
            )
            if seed_candles is None:
                continue
            seed_key = f"{tf_set}:{int(seed_candles[0].open_time_ms)}:{int(seed_candles[-1].close_time_ms)}"
            if seed_key in state.rolling_consumed_seed_keys or seed_key in state.rolling_pending_seeds:
                continue
            if not _seed_passes_return_gate(seed_candles=tuple(seed_candles)):
                self._total_seed_return_gate_rejected += 1
                continue
            context = _pre_seed_context_for_live(
                state=state,
                closed_ltf=closed_ltf,
                tf_set=tf_set,
                htf_timeframe_ms=htf_timeframe_ms,
                ltf_timeframe_ms=ltf_timeframe_ms,
                before_ms=int(seed_candles[0].open_time_ms),
            )
            if not context:
                self._total_seed_context_not_ready_skipped += 1
                continue
            if not _seed_passes_basic_core_gate(seed_candles=tuple(seed_candles), context=context):
                self._total_seed_basic_gate_rejected += 1
                continue
            state.rolling_pending_seeds[seed_key] = Live2RollingSeedState(
                seed_key=seed_key,
                tf_set=tf_set,
                profile_rank=profile_rank,
                seed_open_ms=int(seed_candles[0].open_time_ms),
                seed_close_ms=int(seed_candles[-1].close_time_ms),
                seed_candles=tuple(seed_candles),
                created_ms=int(decision_candle.close_time_ms),
            )
            state.rolling_discovered_seed_count += 1
            state.rolling_last_seed_key = seed_key
            state.rolling_last_seed_open_ms = int(seed_candles[0].open_time_ms)
            state.rolling_last_seed_close_ms = int(seed_candles[-1].close_time_ms)
        state.rolling_last_seed_discovery_close_ms = int(decision_candle.close_time_ms)
        state.rolling_last_seed_discovery_close_ms_by_timeframe[ltf_timeframe_ms] = int(decision_candle.close_time_ms)
        state.rolling_pending_seed_count = len(state.rolling_pending_seeds)

    def _consume_rolling_seed(self, *, state: SymbolState, seed: Live2RollingSeedState, outcome: str) -> None:
        state.rolling_pending_seeds.pop(seed.seed_key, None)
        state.rolling_consumed_seed_keys.add(seed.seed_key)
        if len(state.rolling_consumed_seed_keys) > 2048:
            state.rolling_consumed_seed_keys = set(sorted(state.rolling_consumed_seed_keys)[-1024:])
        state.rolling_consumed_seed_count += 1
        state.rolling_pending_seed_count = len(state.rolling_pending_seeds)
        if outcome == "selected":
            state.rolling_selected_seed_count += 1
        elif outcome == "rejected":
            state.rolling_rejected_seed_count += 1
        elif outcome == "expired":
            state.rolling_expired_seed_count += 1

    def _live_decision_from_core_verdict(
        self,
        *,
        verdict: DecisionVerdict,
        seed: Live2RollingSeedState,
        post_seed: tuple[Live2Candle, ...],
        decision_candle: Live2Candle,
    ) -> Live2SignalDecision:
        features: dict[str, object] = {
            "feature_mode": "rolling_seed_first_core",
            "rolling_runner_contract": ROLLING_DECISION_CONTRACT_ID,
            "rolling_runner_model": ROLLING_DECISION_CONTRACT_ID,
            "rolling_runner_core_version": ROLLING_DECISION_CORE_VERSION,
            "rolling_runner_tf_set": seed.tf_set,
            "rolling_runner_profile_rank": seed.profile_rank,
            "rolling_seed_key": seed.seed_key,
            "rolling_seed_open_ms": seed.seed_open_ms,
            "rolling_seed_close_ms": seed.seed_close_ms,
            "post_seed_ltf_candles_seen": len(post_seed),
            "confirm_start_ms": int(post_seed[0].open_time_ms) if post_seed else None,
            "confirm_end_ms": int(post_seed[-1].close_time_ms) if post_seed else None,
            "confirmation_candles": len(post_seed),
            "decision_time_ms": int(decision_candle.close_time_ms),
            **dict(verdict.features),
        }
        state_snapshot = verdict.snapshot
        features["snapshot_hash"] = decision_snapshot_hash(state_snapshot)
        features["snapshot_match_key"] = decision_snapshot_match_key(state_snapshot)
        if state_snapshot.ltf_confirm is not None:
            features["confirm_start_ms"] = int(state_snapshot.ltf_confirm.confirm_start_ms)
            features["confirm_end_ms"] = int(state_snapshot.ltf_confirm.confirm_end_ms)
            features["confirmation_candles"] = len(state_snapshot.ltf_confirm.confirm_candles)
        if verdict.verdict == "selected":
            category_id = str(verdict.category_id or features.get("rolling_runner_category_id", "") or "")
            self._total_selected += 1
            self._selected_by_category[category_id] += 1
            dependency_reasons = tuple(_dependency_reason(item) for item in verdict.dependencies)
            reject_reasons = tuple(item.reason for item in verdict.rejects)
            self._last_dependency_reasons = dependency_reasons
            self._last_reject_reasons = reject_reasons
            return Live2SignalDecision(
                verdict="selected",
                reason="rolling_seed_first_core_selected",
                category_id=category_id,
                category_rank=rolling_category_priority_rank(category_id),
                signal_entry_price=verdict.signal_entry_price,
                initial_stop_at_decision=verdict.initial_stop_price,
                initial_risk_pct_at_decision=_float_or_none(features.get("initial_risk_pct_at_decision")),
                tp1_at_decision=verdict.tp1_price,
                features={
                    **features,
                    "category_contract": ROLLING_DECISION_CONTRACT_ID,
                    "category_label": category_id,
                    "signal_dependency_reasons": dependency_reasons,
                    "signal_reject_reasons": reject_reasons,
                },
                dependency_reasons=dependency_reasons,
                reject_reasons=reject_reasons,
            )
        if verdict.verdict == "data_dependency_not_ready":
            dependency_reasons = (
                tuple(_dependency_reason(item) for item in verdict.dependencies)
                or tuple(item.reason for item in verdict.rejects)
                or ("core_data_dependency_not_ready",)
            )
            return self._data_dependency_not_ready(
                dependency_reasons=dependency_reasons,
                reject_reasons=tuple(item.reason for item in verdict.rejects),
                features=features,
            )
        reject_reasons = tuple(item.reason for item in verdict.rejects) or ("core_rejected",)
        return self._reject("; ".join(reject_reasons), features=features, reject_reasons=reject_reasons)

    def status(self) -> dict[str, object]:
        return {
            "status": "rolling_seed_first_core_signal_adapter_active",
            "category_contract": ROLLING_DECISION_CONTRACT_ID,
            "core_version": ROLLING_DECISION_CORE_VERSION,
            "category_ids": self.category_ids,
            "total_evaluations": self._total_evaluations,
            "total_selected": self._total_selected,
            "total_rejected": self._total_rejected,
            "total_data_dependency_not_ready": self._total_data_dependency_not_ready,
            "total_seed_context_not_ready_skipped": self._total_seed_context_not_ready_skipped,
            "total_seed_basic_gate_rejected": self._total_seed_basic_gate_rejected,
            "total_seed_return_gate_rejected": self._total_seed_return_gate_rejected,
            "selected_by_category": dict(self._selected_by_category),
            "dependency_reason_counts": dict(self._dependency_reason_counts),
            "reject_reason_counts": dict(self._reject_reason_counts),
            "last_dependency_reasons": self._last_dependency_reasons,
            "last_reject_reasons": self._last_reject_reasons,
            "legacy_live_paths_removed": True,
            "limitations": (
                "live_signal_engine_is_adapter_only; "
                "selected/rejected/data_dependency_not_ready_verdicts_come_from_shared_core; "
                "execution_guard_portfolio_actual_fill_and_stop_verification_are_outside_signal_core"
            ),
        }

    def _reject(
        self,
        reason: str,
        *,
        features: dict[str, object],
        reject_reasons: tuple[str, ...] | None = None,
    ) -> Live2SignalDecision:
        self._total_rejected += 1
        effective_rejects = reject_reasons if reject_reasons is not None else (reason,)
        for item in effective_rejects:
            self._reject_reason_counts[item] += 1
        self._last_reject_reasons = tuple(effective_rejects)
        self._last_dependency_reasons = ()
        return Live2SignalDecision(
            verdict="rejected_signal_contract",
            reason=reason,
            reject_reasons=tuple(effective_rejects),
            features={
                **features,
                "category_contract": ROLLING_DECISION_CONTRACT_ID,
                "signal_reject_reasons": tuple(effective_rejects),
                "signal_dependency_reasons": (),
            },
        )

    def _data_dependency_not_ready(
        self,
        *,
        dependency_reasons: tuple[str, ...],
        reject_reasons: tuple[str, ...],
        features: dict[str, object],
    ) -> Live2SignalDecision:
        self._total_data_dependency_not_ready += 1
        for item in dependency_reasons:
            self._dependency_reason_counts[item] += 1
        for item in reject_reasons:
            self._reject_reason_counts[item] += 1
        self._last_dependency_reasons = tuple(dependency_reasons)
        self._last_reject_reasons = tuple(reject_reasons)
        return Live2SignalDecision(
            verdict="data_dependency_not_ready",
            reason="; ".join(dependency_reasons),
            dependency_reasons=tuple(dependency_reasons),
            reject_reasons=tuple(reject_reasons),
            features={
                **features,
                "category_contract": ROLLING_DECISION_CONTRACT_ID,
                "signal_dependency_reasons": tuple(dependency_reasons),
                "signal_reject_reasons": tuple(reject_reasons),
            },
        )


def _closed_candles_for_timeframe(*, state: SymbolState, timeframe_ms: int) -> tuple[Live2Candle, ...]:
    ring = state.candle_book.rings.get(int(timeframe_ms))
    return () if ring is None else ring.closed_snapshot()


def _rolling_profile_by_tf_set(tf_set: str) -> dict[str, object] | None:
    for profile in LIVE2_ROLLING_RUNNER_PROFILES:
        if str(profile.get("tf_set", "")) == str(tf_set):
            return profile
    return None


def _post_seed_ltf_candles(
    *,
    closed_ltf: tuple[Live2Candle, ...],
    seed: Live2RollingSeedState,
    max_confirm: int,
    ltf_timeframe_ms: int,
) -> tuple[Live2Candle, ...]:
    candles = tuple(item for item in closed_ltf if int(item.open_time_ms) >= int(seed.seed_close_ms))
    if not candles:
        return ()
    if int(candles[0].open_time_ms) != int(seed.seed_close_ms):
        return ()
    limited = candles[: max(0, int(max_confirm))]
    expected = tuple(int(seed.seed_close_ms) + idx * int(ltf_timeframe_ms) for idx in range(len(limited)))
    if tuple(int(item.open_time_ms) for item in limited) != expected:
        return ()
    return tuple(limited)


def _to_decision_candle(candle: Live2Candle) -> DecisionCandle:
    return DecisionCandle(
        open_time_ms=int(candle.open_time_ms),
        close_time_ms=int(candle.close_time_ms),
        open=float(candle.open),
        high=float(candle.high),
        low=float(candle.low),
        close=float(candle.close),
        quote_volume=float(candle.quote_volume),
        number_of_trades=int(candle.number_of_trades),
        taker_buy_quote_volume=float(candle.taker_buy_quote_volume),
        source=f"{candle.first_source}->{candle.last_source}" if candle.first_source != candle.last_source else candle.last_source,
        source_status="ok",
    )


def _build_live_seed_first_snapshot(
    *,
    state: SymbolState,
    seed: Live2RollingSeedState,
    closed_ltf: tuple[Live2Candle, ...],
    decision_time_ms: int,
    ltf_timeframe_ms: int,
) -> DecisionSnapshot:
    profile = _rolling_profile_by_tf_set(seed.tf_set)
    htf_timeframe_ms = int(profile["htf_timeframe_ms"]) if profile is not None else 0
    context = _pre_seed_context_for_live(
        state=state,
        closed_ltf=closed_ltf,
        tf_set=seed.tf_set,
        htf_timeframe_ms=htf_timeframe_ms,
        ltf_timeframe_ms=int(ltf_timeframe_ms),
        before_ms=int(seed.seed_open_ms),
    )
    dependencies: list[DataDependency] = []
    if not context:
        dependencies.append(
            DataDependency(
                name="pre_seed_context",
                status="missing",
                reason="live_seed_aligned_context_not_ready",
                asof_ms=int(seed.seed_open_ms),
                source=f"live2_{int(ltf_timeframe_ms // 1000)}s_or_1m_context_rings",
            )
        )
    return DecisionSnapshot(
        symbol=state.symbol,
        tf_set=seed.tf_set,
        decision_time_ms=int(decision_time_ms),
        source="live",
        rolling_seed=RollingSeedSnapshot(
            tf_set=seed.tf_set,
            seed_open_ms=int(seed.seed_open_ms),
            seed_close_ms=int(seed.seed_close_ms),
            seed_candles=tuple(_to_decision_candle(item) for item in seed.seed_candles),
            pre_seed_context_candles=tuple(_to_decision_candle(item) for item in context),
        ),
        source_labels={
            "adapter": "live2_seed_first_core_adapter",
            "candle_source": "binance_futures_aggtrade_ws_or_startup_rest_ring",
            "context_source": f"live2_closed_{int(ltf_timeframe_ms // 1000)}s_or_1m_seed_aligned_htf_context",
        },
        features={
            "live_mark_status": state.mark_status,
            "live_oi_status": state.oi_status,
            "live_current_oi_status": state.current_oi_status,
            "live_prior_context_status": state.prior_context_status,
        },
        dependencies=tuple(dependencies),
    )


def _dependency_reason(dependency: DataDependency) -> str:
    if dependency.reason:
        return f"{dependency.name}:{dependency.reason}"
    return f"{dependency.name}:{dependency.status}"


def _seed_state_features(*, seed: Live2RollingSeedState, decision_candle: Live2Candle) -> dict[str, object]:
    return {
        "feature_mode": "rolling_seed_first_state_machine",
        "rolling_runner_contract": ROLLING_DECISION_CONTRACT_ID,
        "rolling_runner_core_version": ROLLING_DECISION_CORE_VERSION,
        "rolling_runner_tf_set": seed.tf_set,
        "rolling_runner_profile_rank": seed.profile_rank,
        "rolling_seed_key": seed.seed_key,
        "rolling_seed_open_ms": seed.seed_open_ms,
        "rolling_seed_close_ms": seed.seed_close_ms,
        "decision_time_ms": int(decision_candle.close_time_ms),
    }


def _closed_segment_before(
    *,
    candles: tuple[Live2Candle, ...],
    end_open_ms: int,
    count: int,
    timeframe_ms: int,
) -> tuple[Live2Candle, ...] | None:
    end_idx = 0
    for idx in range(len(candles) - 1, -1, -1):
        if int(candles[idx].open_time_ms) < int(end_open_ms):
            end_idx = idx + 1
            break
    if end_idx < count:
        return None
    segment = candles[end_idx - count:end_idx]
    expected = tuple(int(segment[0].open_time_ms) + index * timeframe_ms for index in range(count))
    if tuple(int(item.open_time_ms) for item in segment) != expected:
        return None
    return segment


def _aggregate_candles_to_live2_candle(*, candles: tuple[Live2Candle, ...], timeframe_ms: int) -> Live2Candle:
    ordered = tuple(sorted(candles, key=lambda item: item.open_time_ms))
    first = ordered[0]
    last = ordered[-1]
    return Live2Candle(
        timeframe_ms=int(timeframe_ms),
        open_time_ms=int(first.open_time_ms),
        close_time_ms=int(last.close_time_ms),
        open=float(first.open),
        high=max(float(item.high) for item in ordered),
        low=min(float(item.low) for item in ordered),
        close=float(last.close),
        base_volume=sum(float(item.base_volume) for item in ordered),
        quote_volume=sum(float(item.quote_volume) for item in ordered),
        number_of_trades=sum(int(item.number_of_trades) for item in ordered),
        taker_buy_quote_volume=sum(float(item.taker_buy_quote_volume) for item in ordered),
        first_trade_time_ms=int(first.first_trade_time_ms),
        last_trade_time_ms=int(last.last_trade_time_ms),
        first_source=first.first_source,
        last_source=last.last_source,
        startup_rest_trade_count=sum(int(item.startup_rest_trade_count) for item in ordered),
        live_ws_trade_count=sum(int(item.live_ws_trade_count) for item in ordered),
        agg_trade_id_gap_count=sum(int(item.agg_trade_id_gap_count) for item in ordered),
        missing_agg_trade_id_count=sum(int(item.missing_agg_trade_id_count) for item in ordered),
        max_agg_trade_id_gap=max((int(item.max_agg_trade_id_gap) for item in ordered), default=0),
    )


def _aggregate_ltf_history_to_rolling_htf(
    *,
    closed_ltf: tuple[Live2Candle, ...],
    tf_set: str,
    htf_timeframe_ms: int,
    ltf_timeframe_ms: int,
    before_ms: int,
) -> tuple[Live2Candle, ...]:
    """Build the exact seed-aligned non-overlapping HTF context contract."""

    required = rolling_context_windows_for_tf_set(tf_set)
    group_size = htf_timeframe_ms // ltf_timeframe_ms
    if required is None or required <= 0 or group_size <= 0:
        return ()
    by_open = {int(item.open_time_ms): item for item in closed_ltf}
    windows: list[Live2Candle] = []
    start_ms = int(before_ms) - int(required) * int(htf_timeframe_ms)
    for window_start_ms in range(start_ms, int(before_ms), int(htf_timeframe_ms)):
        expected_opens = tuple(int(window_start_ms) + idx * int(ltf_timeframe_ms) for idx in range(group_size))
        chunk = tuple(by_open.get(open_ms) for open_ms in expected_opens)
        if any(item is None for item in chunk):
            return ()
        candles = tuple(item for item in chunk if item is not None)
        windows.append(_aggregate_candles_to_live2_candle(candles=candles, timeframe_ms=htf_timeframe_ms))
    return tuple(windows)


def _pre_seed_context_for_live(
    *,
    state: SymbolState,
    closed_ltf: tuple[Live2Candle, ...],
    tf_set: str,
    htf_timeframe_ms: int,
    ltf_timeframe_ms: int,
    before_ms: int,
) -> tuple[Live2Candle, ...]:
    """Build live pre-seed context from exact LTF history or startup 1m context.

    Fresh live 15s/30s rings cannot contain the 24h context immediately after
    startup. The startup/maintenance 1m ring can honestly supply the same
    non-overlapping 3m/5m context when the rolling seed boundary is minute
    aligned. Non-minute-aligned seeds still require true LTF history.
    """

    if int(htf_timeframe_ms) <= 0:
        return ()
    ltf_context = _aggregate_ltf_history_to_rolling_htf(
        closed_ltf=closed_ltf,
        tf_set=tf_set,
        htf_timeframe_ms=int(htf_timeframe_ms),
        ltf_timeframe_ms=int(ltf_timeframe_ms),
        before_ms=int(before_ms),
    )
    if ltf_context:
        return ltf_context
    one_minute = _closed_candles_for_timeframe(state=state, timeframe_ms=60_000)
    if int(before_ms) % 60_000 != 0:
        return ()
    return _aggregate_ltf_history_to_rolling_htf(
        closed_ltf=one_minute,
        tf_set=tf_set,
        htf_timeframe_ms=int(htf_timeframe_ms),
        ltf_timeframe_ms=60_000,
        before_ms=int(before_ms),
    )


def _seed_passes_basic_core_gate(
    *,
    seed_candles: tuple[Live2Candle, ...],
    context: tuple[Live2Candle, ...],
) -> bool:
    """Cheap exact subset of the shared core seed gate for live seed storage."""

    if not seed_candles or len(context) < ROLLING_BASELINE_WINDOWS:
        return False
    seed_open = float(seed_candles[0].open)
    seed_close = float(seed_candles[-1].close)
    if seed_open <= 0.0:
        return False
    seed_quote = sum(float(item.quote_volume) for item in seed_candles)
    seed_trades = sum(int(item.number_of_trades) for item in seed_candles)
    baseline = context[-ROLLING_BASELINE_WINDOWS:]
    baseline_quote = _positive_median(float(item.quote_volume) for item in baseline)
    baseline_trades = _positive_median(float(item.number_of_trades) for item in baseline)
    if baseline_quote <= 0.0 or baseline_trades <= 0.0:
        return False
    seed_return = (seed_close / seed_open) - 1.0
    return (
        seed_return >= ROLLING_SEED_MIN_HTF_RETURN_PCT
        and seed_quote / baseline_quote >= ROLLING_SEED_MIN_HTF_QUOTE_RATIO
        and seed_trades / baseline_trades >= ROLLING_SEED_MIN_HTF_TRADE_RATIO
    )


def _seed_passes_return_gate(*, seed_candles: tuple[Live2Candle, ...]) -> bool:
    """Context-free prerequisite for the shared core seed return gate."""

    if not seed_candles:
        return False
    seed_open = float(seed_candles[0].open)
    seed_close = float(seed_candles[-1].close)
    if seed_open <= 0.0:
        return False
    return (seed_close / seed_open) - 1.0 >= ROLLING_SEED_MIN_HTF_RETURN_PCT


def _positive_median(values: object) -> float:
    finite = sorted(float(value) for value in values if _float_or_none(value) is not None and float(value) > 0.0)
    if not finite:
        return float("nan")
    mid = len(finite) // 2
    if len(finite) % 2:
        return finite[mid]
    return (finite[mid - 1] + finite[mid]) / 2.0


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
