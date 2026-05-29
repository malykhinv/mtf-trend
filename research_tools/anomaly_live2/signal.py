"""Pure stream-only signal adapter for anomaly live2.

The adapter is deliberately hot-path safe: it evaluates already-built in-memory
aggTrade candles and SymbolState fields only. It performs no REST/cache/file IO
and does not guess unavailable derivative context.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from research_tools.anomaly_category_contract import (
    CATEGORY_CONTRACT_ID,
    DEFAULT_PUMP_CATEGORY_IDS,
    SUPPORTED_PUMP_CATEGORIES,
    PumpCategoryContract,
)

from .market_data.candles import Live2Candle
from .state import SymbolState

Live2CategoryDecisionKind = Literal["accepted", "rejected", "data_dependency_not_ready"]

LIVE2_BACKTEST_SETUP_TIMEFRAME_MS = 60_000
LIVE2_BACKTEST_ENTRY_TIMEFRAME_MS = 30_000
LIVE2_BACKTEST_SETUP_CANDLES = LIVE2_BACKTEST_SETUP_TIMEFRAME_MS // LIVE2_BACKTEST_ENTRY_TIMEFRAME_MS
LIVE2_BACKTEST_BASELINE_CANDLES = 60
LIVE2_BACKTEST_CONFIRMATION_CANDLES = 4
LIVE2_BACKTEST_MIN_QUOTE_RATIO_START = 5.0
LIVE2_BACKTEST_MIN_TRADE_RATIO_START = 5.0
LIVE2_BACKTEST_MIN_PRICE_RETENTION = 0.70
LIVE2_BACKTEST_MIN_VERTICALITY_SCORE = 0.25
LIVE2_BACKTEST_MIN_HOLD_COUNT = 2
LIVE2_BACKTEST_MAX_INITIAL_RISK_PCT = 0.16
LIVE2_BACKTEST_STOP_BUFFER_RANGE_FRACTION = 0.05
LIVE2_BACKTEST_TP1_R = 0.75
LIVE2_RUNNER_SHAPE_HALF_CANDLES = LIVE2_BACKTEST_SETUP_CANDLES // 2
POST_HTF_ACCEPTANCE_CATEGORY_ID = "post_htf_acceptance_long"
POST_HTF_ACCEPTANCE_CONTRACT = "post_htf_acceptance_long_v1"
POST_HTF_ACCEPTANCE_HTF_CANDLES = 12
POST_HTF_ACCEPTANCE_CONFIRMATION_CANDLES = 6
POST_HTF_ACCEPTANCE_BASELINE_WINDOWS = 60
POST_HTF_ACCEPTANCE_BASELINE_WINDOW_CANDLES = 12
POST_HTF_ACCEPTANCE_STOP_BUFFER_PCT = 0.0005
POST_HTF_ACCEPTANCE_MIN_HTF_RETURN_PCT = 0.015
POST_HTF_ACCEPTANCE_MIN_HTF_QUOTE_RATIO = 10.0
POST_HTF_ACCEPTANCE_MIN_HTF_TRADE_RATIO = 8.0
POST_HTF_ACCEPTANCE_TARGET_R = 1.5
POST_HTF_ACCEPTANCE_OI_DIVERGENCE_WINDOW_CANDLES = 180


LIVE2_ROLLING_RUNNER_CONTRACT_ID = "rolling_runner_cas_v1"
LIVE2_ROLLING_RUNNER_CATEGORY_PRIORITY = (
    "C_balanced_flow_acceptance",
    "A_resonance_prior_spike",
    "S_7d_5m30_strict",
)
LIVE2_ROLLING_RUNNER_PROFILES = (
    {
        "tf_set": "5m_30s",
        "htf_timeframe_ms": 300_000,
        "htf_candles": 10,
        "min_confirm_candles": 2,
        "max_confirm_candles": 8,
        "profile_rank": 1,
    },
    {
        "tf_set": "3m_30s",
        "htf_timeframe_ms": 180_000,
        "htf_candles": 6,
        "min_confirm_candles": 2,
        "max_confirm_candles": 6,
        "profile_rank": 2,
    },
)
LIVE2_ROLLING_BASELINE_WINDOWS = 60
LIVE2_ROLLING_DORMANCY_WINDOWS = 30
LIVE2_ROLLING_PREGROWTH_WINDOWS = 5
LIVE2_ROLLING_PRIOR_SPIKE_LOOKBACK_MS = 24 * 60 * 60 * 1000
LIVE2_ROLLING_CONTEXT_SAFETY_LOOKBACK_MS = LIVE2_ROLLING_PRIOR_SPIKE_LOOKBACK_MS + LIVE2_ROLLING_BASELINE_WINDOWS * 300_000
LIVE2_ROLLING_SEED_MIN_HTF_QUOTE_RATIO = 5.0
LIVE2_ROLLING_SEED_MIN_HTF_TRADE_RATIO = 5.0
LIVE2_ROLLING_SEED_MIN_HTF_RETURN_PCT = 0.0100
LIVE2_ROLLING_STRUCTURAL_STOP_BUFFER_PCT = 0.0005
LIVE2_ROLLING_TP1_R = 0.75
LIVE2_ROLLING_MAX_ENTRY_DRIFT_PCT = 0.004
LIVE2_ROLLING_MAX_INITIAL_RISK_PCT = 0.05


@dataclass(frozen=True, slots=True)
class Live2CategoryEvaluation:
    """One category verdict before entry/execution guards."""

    accepted: bool
    reason: str
    kind: Live2CategoryDecisionKind


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
    """Small stream-only adapter for the shared pump-category contract.

    Mark basis comes from the live2 markPrice stream, OI delta comes from the
    live2 active-symbol OI poller, and prior fake-pump context comes from the
    live2 24h prior-context poller. Missing required fields return
    ``data_dependency_not_ready`` instead of being mixed into trading rejects.
    """

    def __init__(
        self,
        *,
        category_ids: tuple[str, ...] = LIVE2_ROLLING_RUNNER_CATEGORY_PRIORITY,
        mark_stale_ms: int | None = None,
        oi_stale_ms: int | None = None,
        prior_context_stale_ms: int | None = None,
    ) -> None:
        self.category_ids = tuple(category_ids)
        self.mark_stale_ms = None if mark_stale_ms is None else int(mark_stale_ms)
        self.oi_stale_ms = None if oi_stale_ms is None else int(oi_stale_ms)
        self.prior_context_stale_ms = None if prior_context_stale_ms is None else int(prior_context_stale_ms)
        self._total_evaluations = 0
        self._total_selected = 0
        self._total_rejected = 0
        self._total_data_dependency_not_ready = 0
        self._selected_by_category: Counter[str] = Counter()
        self._dependency_reason_counts: Counter[str] = Counter()
        self._reject_reason_counts: Counter[str] = Counter()
        self._last_dependency_reasons: tuple[str, ...] = ()
        self._last_reject_reasons: tuple[str, ...] = ()

    def evaluate(self, *, state: SymbolState, candle: Live2Candle, actionable_reason: str) -> Live2SignalDecision:
        self._total_evaluations += 1
        features = self._features(state=state, candle=candle, actionable_reason=actionable_reason)
        if candle.open <= 0 or candle.close <= 0:
            return self._reject("invalid_stream_candle_price", features=features)
        if candle.quote_volume <= 0 or candle.number_of_trades <= 0:
            return self._reject("stream_candle_has_no_real_flow", features=features)

        category_id = str(features.get("rolling_runner_category_id", "") or "")
        if category_id:
            stop = _float_or_none(features.get("rolling_initial_stop_at_decision"))
            entry = _float_or_none(features.get("rolling_signal_entry_price"))
            tp1 = _float_or_none(features.get("rolling_tp1_at_decision"))
            initial_risk_pct = _float_or_none(features.get("rolling_initial_risk_pct_at_decision"))
            if stop is None or entry is None or tp1 is None or initial_risk_pct is None:
                return self._reject("rolling_runner_selected_but_prices_missing", features=features)
            self._total_selected += 1
            self._selected_by_category[category_id] += 1
            dependency_reasons = tuple(str(item) for item in features.get("rolling_runner_dependency_reasons", ()) or ())
            reject_reasons = tuple(str(item) for item in features.get("rolling_runner_reject_reasons", ()) or ())
            self._last_dependency_reasons = dependency_reasons
            self._last_reject_reasons = reject_reasons
            return Live2SignalDecision(
                verdict="selected",
                reason=f"{actionable_reason}; rolling_runner_category_selected",
                category_id=category_id,
                category_rank=_rolling_category_rank(category_id),
                signal_entry_price=entry,
                initial_stop_at_decision=stop,
                initial_risk_pct_at_decision=initial_risk_pct,
                tp1_at_decision=tp1,
                dependency_reasons=dependency_reasons,
                reject_reasons=reject_reasons,
                features={
                    **features,
                    "category_contract": LIVE2_ROLLING_RUNNER_CONTRACT_ID,
                    "category_label": category_id,
                    "signal_dependency_reasons": dependency_reasons,
                    "signal_reject_reasons": reject_reasons,
                },
            )
        dependency_reasons = tuple(str(item) for item in features.get("rolling_runner_dependency_reasons", ()) or ())
        reject_reasons = tuple(str(item) for item in features.get("rolling_runner_reject_reasons", ()) or ())
        if dependency_reasons:
            return self._data_dependency_not_ready(
                dependency_reasons=dependency_reasons,
                reject_reasons=reject_reasons,
                features=features,
            )
        return self._reject(
            "; ".join(reject_reasons) or "no_rolling_runner_category_accepted",
            features={
                **features,
                "signal_reject_reasons": reject_reasons,
                "signal_dependency_reasons": (),
            },
            reject_reasons=reject_reasons or ("no_rolling_runner_category_accepted",),
        )

    def status(self) -> dict[str, object]:
        return {
            "status": "rolling_runner_cas_signal_adapter_active",
            "category_contract": LIVE2_ROLLING_RUNNER_CONTRACT_ID,
            "category_ids": self.category_ids,
            "total_evaluations": self._total_evaluations,
            "total_selected": self._total_selected,
            "total_rejected": self._total_rejected,
            "total_data_dependency_not_ready": self._total_data_dependency_not_ready,
            "selected_by_category": dict(self._selected_by_category),
            "dependency_reason_counts": dict(self._dependency_reason_counts),
            "reject_reason_counts": dict(self._reject_reason_counts),
            "last_dependency_reasons": self._last_dependency_reasons,
            "last_reject_reasons": self._last_reject_reasons,
            "limitations": (
                "rolling_3m_30s_and_5m_30s_only; "
                "baseline_from_closed_1m_klines_fully_before_rolling_window; "
                "category_priority_C_then_A_then_S; "
                "missing_required_rolling_context_returns_data_dependency_not_ready; "
                "no_future_labels_or_post_entry_outcomes_used"
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
                "category_contract": LIVE2_ROLLING_RUNNER_CONTRACT_ID,
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
                "category_contract": LIVE2_ROLLING_RUNNER_CONTRACT_ID,
                "signal_dependency_reasons": tuple(dependency_reasons),
                "signal_reject_reasons": tuple(reject_reasons),
            },
        )

    def _features(self, *, state: SymbolState, candle: Live2Candle, actionable_reason: str) -> dict[str, object]:
        closed_5s = state.candle_book.rings.get(5_000).closed_snapshot() if 5_000 in state.candle_book.rings else ()
        closed_1m = (
            state.candle_book.rings.get(LIVE2_BACKTEST_SETUP_TIMEFRAME_MS).closed_snapshot()
            if LIVE2_BACKTEST_SETUP_TIMEFRAME_MS in state.candle_book.rings
            else ()
        )
        live_setup = _live_backtest_like_setup(
            closed_5s=closed_5s,
            closed_1m=closed_1m,
            decision_candle=candle,
        )
        post_htf_acceptance = _post_htf_acceptance_setup(
            closed_5s=closed_5s,
            closed_1m=closed_1m,
            decision_candle=candle,
        )
        previous = tuple(item for item in closed_5s if item.open_time_ms < candle.open_time_ms)[-24:]
        baseline_quote = _avg([item.quote_volume for item in previous])
        baseline_trades = _avg([float(item.number_of_trades) for item in previous])
        baseline_range = _avg([_range_pct(item) for item in previous])
        baseline_taker_share = _avg([
            item.taker_buy_quote_volume / item.quote_volume
            for item in previous
            if item.quote_volume > 0
        ])
        candle_range = _range_pct(candle)
        return_pct = (candle.close / candle.open) - 1.0 if candle.open > 0 else 0.0
        setup_return_pct = _float_or_none(live_setup.get("decision_return_from_start_open"))
        abs_return_pct = abs(setup_return_pct) if setup_return_pct is not None else abs(return_pct)
        quote_ratio = candle.quote_volume / baseline_quote if baseline_quote > 0 else None
        trade_ratio = float(candle.number_of_trades) / baseline_trades if baseline_trades > 0 else None
        range_ratio = candle_range / baseline_range if baseline_range > 0 else None
        taker_share = candle.taker_buy_quote_volume / candle.quote_volume if candle.quote_volume > 0 else None
        taker_share_delta = None
        if taker_share is not None and baseline_taker_share > 0:
            taker_share_delta = taker_share - baseline_taker_share
        setup_quote_ratio = live_setup.get("quote_ratio")
        if setup_quote_ratio is not None:
            quote_ratio = float(setup_quote_ratio)
        setup_trade_ratio = live_setup.get("trade_ratio")
        if setup_trade_ratio is not None:
            trade_ratio = float(setup_trade_ratio)
        setup_range_ratio = live_setup.get("range_pct_ratio_to_baseline")
        if setup_range_ratio is not None:
            range_ratio = float(setup_range_ratio)
        quote_ratio_per_abs_return = None
        setup_quote_ratio_per_abs_return = live_setup.get("start_quote_ratio_per_abs_return")
        if setup_quote_ratio_per_abs_return is not None:
            quote_ratio_per_abs_return = float(setup_quote_ratio_per_abs_return)
        elif quote_ratio is not None and abs_return_pct > 0:
            quote_ratio_per_abs_return = quote_ratio / abs_return_pct
        trade_ratio_per_abs_return = None
        setup_trade_ratio_per_abs_return = live_setup.get("start_trade_ratio_per_abs_return")
        if setup_trade_ratio_per_abs_return is not None:
            trade_ratio_per_abs_return = float(setup_trade_ratio_per_abs_return)
        elif trade_ratio is not None and abs_return_pct > 0:
            trade_ratio_per_abs_return = trade_ratio / abs_return_pct
        flow_hold = _trailing_live_flow_hold(
            closed_5s=closed_5s,
            decision_candle=candle,
            baseline_quote=baseline_quote,
            baseline_trades=baseline_trades,
            baseline_taker_share=baseline_taker_share,
        )
        decision_box = (*previous, candle)
        decision_box_low = float(live_setup.get("low") or min((item.low for item in decision_box), default=candle.low))
        decision_box_high = float(live_setup.get("high") or max((item.high for item in decision_box), default=candle.high))
        decision_box_range = decision_box_high - decision_box_low
        setup_baseline_quote = live_setup.get("baseline_quote_1m")
        baseline_quote_daily_proxy = (
            float(setup_baseline_quote) * 1440.0
            if setup_baseline_quote is not None and float(setup_baseline_quote) > 0
            else (baseline_quote * (1440.0 / (5.0 / 60.0)) if baseline_quote > 0 else None)
        )
        setup_taker_share = live_setup.get("start_taker_buy_quote_share")
        if setup_taker_share is not None:
            taker_share = float(setup_taker_share)
        setup_taker_share_delta = live_setup.get("start_taker_buy_quote_share_delta")
        if setup_taker_share_delta is not None:
            taker_share_delta = float(setup_taker_share_delta)
        setup_flow_hold_count = live_setup.get("flow_hold_count")
        flow_hold_count = int(setup_flow_hold_count) if setup_flow_hold_count is not None else flow_hold.count
        setup_next_taker_share = live_setup.get("next_n_taker_buy_quote_share_mean")
        setup_next_taker_delta = live_setup.get("next_n_taker_buy_quote_share_delta")
        mark_basis = None
        mark_basis_status = "not_available"
        effective_mark_status = _effective_context_status(
            status=state.mark_status,
            last_seen_ms=state.mark_last_seen_ms,
            decision_time_ms=candle.close_time_ms,
            stale_ms=self.mark_stale_ms,
        )
        if effective_mark_status == "ok" and state.mark_price is not None and state.mark_price > 0 and candle.close > 0:
            mark_basis = (state.mark_price - candle.close) / candle.close
            mark_basis_status = "ok"
        elif effective_mark_status:
            mark_basis_status = effective_mark_status
        oi_status = _effective_context_status(
            status=state.oi_status,
            last_seen_ms=state.oi_last_seen_ms,
            decision_time_ms=candle.close_time_ms,
            stale_ms=self.oi_stale_ms,
        )
        post_htf_oi_divergence = _post_htf_acceptance_oi_divergence_check(
            closed_5s=closed_5s,
            closed_1m=closed_1m,
            decision_candle=candle,
            oi_status=oi_status,
            oi_change_pct_3x5m=state.oi_change_pct_3x5m,
        )
        prior_context_status = _effective_context_status(
            status=state.prior_context_status,
            last_seen_ms=state.prior_context_last_seen_ms,
            decision_time_ms=candle.close_time_ms,
            stale_ms=self.prior_context_stale_ms,
        )
        prior_whipsaw = live_setup.get("prior_up_down_whipsaw_to_impulse_range")
        entry = candle.close
        setup_stop = live_setup.get("initial_stop_at_decision")
        stop = float(setup_stop) if setup_stop is not None else decision_box_low
        risk_fraction = (entry / stop) - 1.0 if stop > 0 else 0.0
        setup_tp1 = live_setup.get("tp1_at_decision")
        tp1 = float(setup_tp1) if setup_tp1 is not None else entry + LIVE2_BACKTEST_TP1_R * (entry - decision_box_low)
        rolling_runner = _rolling_runner_category_setup(state=state, decision_candle=candle)
        return {
            "actionable_reason": actionable_reason,
            **rolling_runner,
            "signal_entry_price": entry,
            "initial_stop_at_decision": stop,
            "tp1_at_decision": tp1,
            "initial_risk_pct_at_decision": risk_fraction,
            "return_pct": return_pct,
            "abs_return_pct": abs_return_pct,
            "decision_return_from_start_open": setup_return_pct,
            "range_pct": candle_range,
            "baseline_quote_5s": baseline_quote,
            "baseline_quote_daily_proxy": baseline_quote_daily_proxy,
            "live_setup_status": live_setup["status"],
            "live_setup_reason": live_setup["reason"],
            "live_setup_timeframe": "1m",
            "live_setup_entry_timeframe": "5s",
            "live_setup_alignment": live_setup.get("alignment"),
            "live_setup_calendar_aligned": live_setup.get("calendar_aligned"),
            "live_setup_setup_open_ms": live_setup.get("setup_open_ms"),
            "live_setup_setup_close_ms": live_setup.get("setup_close_ms"),
            "live_setup_closed_entry_candles": live_setup.get("closed_entry_candles"),
            "live_setup_elapsed_fraction": live_setup.get("elapsed_fraction"),
            "live_setup_raw_quote_ratio": live_setup.get("raw_quote_ratio"),
            "live_setup_raw_trade_ratio": live_setup.get("raw_trade_ratio"),
            "live_setup_min_raw_quote_ratio": live_setup.get("min_raw_quote_ratio"),
            "live_setup_min_raw_trade_ratio": live_setup.get("min_raw_trade_ratio"),
            "live_setup_quote_ratio": live_setup.get("quote_ratio"),
            "live_setup_trade_ratio": live_setup.get("trade_ratio"),
            "live_setup_min_quote_ratio": LIVE2_BACKTEST_MIN_QUOTE_RATIO_START,
            "live_setup_min_trade_ratio": LIVE2_BACKTEST_MIN_TRADE_RATIO_START,
            "live_setup_price_retention": live_setup.get("price_retention"),
            "live_setup_min_price_retention": LIVE2_BACKTEST_MIN_PRICE_RETENTION,
            "live_setup_hold_count": live_setup.get("hold_count"),
            "live_setup_min_hold_count": LIVE2_BACKTEST_MIN_HOLD_COUNT,
            "live_setup_verticality_score": live_setup.get("verticality_score"),
            "live_setup_min_verticality_score": LIVE2_BACKTEST_MIN_VERTICALITY_SCORE,
            "live_setup_range": live_setup.get("range"),
            "live_setup_range_pct": live_setup.get("range_pct"),
            "live_setup_range_pct_ratio_to_baseline": live_setup.get("range_pct_ratio_to_baseline"),
            "live_setup_runner_shape_first_half_quote_volume": live_setup.get("runner_shape_first_half_quote_volume"),
            "live_setup_runner_shape_second_half_quote_volume": live_setup.get("runner_shape_second_half_quote_volume"),
            "live_setup_runner_shape_first_half_number_of_trades": live_setup.get("runner_shape_first_half_number_of_trades"),
            "live_setup_runner_shape_second_half_number_of_trades": live_setup.get("runner_shape_second_half_number_of_trades"),
            "live_setup_runner_shape_first_half_range_pct": live_setup.get("runner_shape_first_half_range_pct"),
            "live_setup_runner_shape_second_half_range_pct": live_setup.get("runner_shape_second_half_range_pct"),
            "live_setup_runner_shape_quote_acceleration": live_setup.get("runner_shape_quote_acceleration"),
            "live_setup_runner_shape_trade_acceleration": live_setup.get("runner_shape_trade_acceleration"),
            "live_setup_runner_shape_range_acceleration": live_setup.get("runner_shape_range_acceleration"),
            "live_setup_runner_shape_second_half_return_pct": live_setup.get("runner_shape_second_half_return_pct"),
            "live_setup_runner_shape_top1_quote_share": live_setup.get("runner_shape_top1_quote_share"),
            "live_setup_flow_window_ms": live_setup.get("flow_window_ms"),
            "live_setup_flow_quote_per_second": live_setup.get("flow_quote_per_second"),
            "live_setup_flow_trades_per_second": live_setup.get("flow_trades_per_second"),
            "live_setup_prior_up_down_whipsaw_to_impulse_range": live_setup.get("prior_up_down_whipsaw_to_impulse_range"),
            "live_setup_flow_hold_count": live_setup.get("flow_hold_count"),
            "live_setup_start_quote_ratio_per_abs_return": live_setup.get("start_quote_ratio_per_abs_return"),
            "live_setup_start_trade_ratio_per_abs_return": live_setup.get("start_trade_ratio_per_abs_return"),
            "live_setup_start_taker_buy_quote_share": live_setup.get("start_taker_buy_quote_share"),
            "live_setup_start_taker_buy_quote_share_delta": live_setup.get("start_taker_buy_quote_share_delta"),
            "live_setup_next_taker_buy_quote_share_mean": live_setup.get("next_n_taker_buy_quote_share_mean"),
            "live_setup_next_taker_buy_quote_share_delta": live_setup.get("next_n_taker_buy_quote_share_delta"),
            "live_setup_decision_ema20": live_setup.get("decision_ema20"),
            "live_setup_stop_buffer_range_fraction": LIVE2_BACKTEST_STOP_BUFFER_RANGE_FRACTION,
            "live_setup_tp1_r": LIVE2_BACKTEST_TP1_R,
            "live_setup_baseline_1m_count": live_setup.get("baseline_1m_count"),
            "live_setup_baseline_quote_1m": live_setup.get("baseline_quote_1m"),
            "live_setup_baseline_trade_count_1m": live_setup.get("baseline_trade_count_1m"),
            "live_setup_baseline_range_pct_1m": live_setup.get("baseline_range_pct_1m"),
            "live_setup_baseline_source": live_setup.get("baseline_source"),
            "post_htf_acceptance_contract": POST_HTF_ACCEPTANCE_CONTRACT,
            "post_htf_acceptance_mode": bool(post_htf_acceptance.get("mode")),
            "post_htf_acceptance_status": post_htf_acceptance.get("status"),
            "post_htf_acceptance_reason": post_htf_acceptance.get("reason"),
            "post_htf_acceptance_htf_alignment": post_htf_acceptance.get("htf_alignment"),
            "post_htf_acceptance_htf_calendar_aligned": post_htf_acceptance.get("htf_calendar_aligned"),
            "post_htf_acceptance_setup_timeframe": "1m",
            "post_htf_acceptance_entry_timeframe": "5s",
            "post_htf_acceptance_confirmation_candles": post_htf_acceptance.get("confirmation_candles"),
            "post_htf_acceptance_required_confirmation_candles": POST_HTF_ACCEPTANCE_CONFIRMATION_CANDLES,
            "post_htf_acceptance_required_htf_candles": POST_HTF_ACCEPTANCE_HTF_CANDLES,
            "post_htf_acceptance_required_baseline_windows": POST_HTF_ACCEPTANCE_BASELINE_WINDOWS,
            "post_htf_acceptance_htf_open_ms": post_htf_acceptance.get("htf_open_ms"),
            "post_htf_acceptance_htf_close_ms": post_htf_acceptance.get("htf_close_ms"),
            "post_htf_acceptance_htf_open": post_htf_acceptance.get("htf_open"),
            "post_htf_acceptance_htf_high": post_htf_acceptance.get("htf_high"),
            "post_htf_acceptance_htf_low": post_htf_acceptance.get("htf_low"),
            "post_htf_acceptance_htf_close": post_htf_acceptance.get("htf_close"),
            "post_htf_acceptance_htf_return_pct": post_htf_acceptance.get("htf_return_pct"),
            "post_htf_acceptance_htf_quote_ratio": post_htf_acceptance.get("htf_quote_ratio"),
            "post_htf_acceptance_htf_trade_ratio": post_htf_acceptance.get("htf_trade_ratio"),
            "post_htf_acceptance_htf_flow_window_ms": post_htf_acceptance.get("htf_flow_window_ms"),
            "post_htf_acceptance_htf_quote_per_second": post_htf_acceptance.get("htf_quote_per_second"),
            "post_htf_acceptance_htf_trades_per_second": post_htf_acceptance.get("htf_trades_per_second"),
            "post_htf_acceptance_ltf6_return_pct": post_htf_acceptance.get("ltf6_return_pct"),
            "post_htf_acceptance_ltf6_quote_volume": post_htf_acceptance.get("ltf6_quote_volume"),
            "post_htf_acceptance_ltf6_number_of_trades": post_htf_acceptance.get("ltf6_number_of_trades"),
            "post_htf_acceptance_ltf6_flow_window_ms": post_htf_acceptance.get("ltf6_flow_window_ms"),
            "post_htf_acceptance_ltf6_quote_per_second": post_htf_acceptance.get("ltf6_quote_per_second"),
            "post_htf_acceptance_ltf6_trades_per_second": post_htf_acceptance.get("ltf6_trades_per_second"),
            "post_htf_acceptance_ltf6_taker_buy_quote_share": post_htf_acceptance.get("ltf6_taker_buy_quote_share"),
            "post_htf_acceptance_ltf6_top1_quote_share": post_htf_acceptance.get("ltf6_top1_quote_share"),
            "post_htf_acceptance_ltf6_last3_quote_share": post_htf_acceptance.get("ltf6_last3_quote_share"),
            "post_htf_acceptance_ltf6_low_vs_htf_close": post_htf_acceptance.get("ltf6_low_vs_htf_close"),
            "post_htf_acceptance_signal_entry_price": post_htf_acceptance.get("signal_entry_price"),
            "post_htf_acceptance_initial_stop_at_decision": post_htf_acceptance.get("initial_stop_at_decision"),
            "post_htf_acceptance_initial_risk_pct_at_decision": post_htf_acceptance.get("initial_risk_pct_at_decision"),
            "post_htf_acceptance_tp1_at_decision": post_htf_acceptance.get("tp1_at_decision"),
            "post_htf_acceptance_target_r": post_htf_acceptance.get("target_r"),
            "post_htf_acceptance_structural_stop_source": post_htf_acceptance.get("structural_stop_source"),
            "post_htf_acceptance_oi_divergence_status": post_htf_oi_divergence.get("status"),
            "post_htf_acceptance_oi_divergence_reason": post_htf_oi_divergence.get("reason"),
            "post_htf_acceptance_oi_divergence_rejected": post_htf_oi_divergence.get("rejected"),
            "post_htf_acceptance_oi_divergence_window_candles": post_htf_oi_divergence.get("window_candles"),
            "post_htf_acceptance_oi_divergence_price_return_pct": post_htf_oi_divergence.get("price_return_pct"),
            "post_htf_acceptance_oi_divergence_oi_change_pct_3x5m": post_htf_oi_divergence.get("oi_change_pct_3x5m"),
            "post_htf_acceptance_oi_divergence_policy": "block_long_when_oi_up_and_15m_price_down",
            "post_htf_acceptance_prior_context_lookback_hours": 24,
            "post_htf_acceptance_research_prior_lookback_hours": 72,
            "post_htf_acceptance_prior_context_parity_note": "live_uses_24h_prior_context_for_legacy_72h_field_name",
            "baseline_trade_count_5s": baseline_trades,
            "baseline_range_pct_5s": baseline_range,
            "baseline_taker_buy_quote_share_5s": baseline_taker_share,
            "baseline_taker_buy_quote_share_1m": live_setup.get("baseline_taker_buy_quote_share_median"),
            "start_quote_ratio": quote_ratio,
            "start_trade_ratio": trade_ratio,
            "selected_source_flow_window_ms": live_setup.get("flow_window_ms"),
            "selected_source_flow_quote_per_second": live_setup.get("flow_quote_per_second"),
            "selected_source_flow_trades_per_second": live_setup.get("flow_trades_per_second"),
            "selected_source_flow_quote_ratio": quote_ratio,
            "selected_source_flow_trade_ratio": trade_ratio,
            "start_range_pct_ratio_to_baseline": range_ratio,
            "runner_shape_quote_ratio": quote_ratio,
            "runner_shape_trade_ratio": trade_ratio,
            "runner_shape_range_ratio": range_ratio,
            "runner_shape_quote_acceleration": live_setup.get("runner_shape_quote_acceleration"),
            "runner_shape_trade_acceleration": live_setup.get("runner_shape_trade_acceleration"),
            "runner_shape_range_acceleration": live_setup.get("runner_shape_range_acceleration"),
            "runner_shape_second_half_return_pct": live_setup.get("runner_shape_second_half_return_pct"),
            "runner_shape_top1_quote_share": live_setup.get("runner_shape_top1_quote_share"),
            "start_quote_ratio_per_abs_return": quote_ratio_per_abs_return,
            "start_trade_ratio_per_abs_return": trade_ratio_per_abs_return,
            "start_taker_buy_quote_share": taker_share,
            "start_taker_buy_quote_share_delta": taker_share_delta,
            "flow_hold_count": flow_hold_count,
            "flow_hold_status": "ok" if setup_flow_hold_count is not None else flow_hold.status,
            "flow_hold_reason": "backtest_confirmation_segment_flow_hold" if setup_flow_hold_count is not None else flow_hold.reason,
            "flow_hold_definition": "backtest_confirmation_segment_per_5s_vs_cumulative_start_and_1m_baseline",
            "flow_hold_window_ms": live_setup.get("flow_hold_window_ms"),
            "flow_hold_quote_volume": live_setup.get("flow_hold_quote_volume"),
            "flow_hold_number_of_trades": live_setup.get("flow_hold_number_of_trades"),
            "flow_hold_taker_buy_quote_volume": live_setup.get("flow_hold_taker_buy_quote_volume"),
            "flow_hold_taker_buy_quote_share_mean": setup_next_taker_share,
            "flow_hold_taker_buy_quote_share_last": live_setup.get("start_taker_buy_quote_share"),
            "flow_hold_taker_buy_quote_share_delta": setup_next_taker_delta,
            "trailing_live_flow_hold_count": flow_hold.count,
            "trailing_live_flow_hold_status": flow_hold.status,
            "trailing_live_flow_hold_reason": flow_hold.reason,
            "live_confirmed_taker_buy_quote_share": setup_next_taker_share,
            "live_confirmed_taker_buy_quote_share_delta": setup_next_taker_delta,
            "quote_volume": candle.quote_volume,
            "number_of_trades": candle.number_of_trades,
            "prior_closed_5s_count": len(previous),
            "decision_box_window_ms": 5_000 * len(decision_box),
            "decision_box_low": decision_box_low,
            "decision_box_high": decision_box_high,
            "decision_box_range": decision_box_range,
            "decision_box_range_pct": decision_box_range / entry if entry > 0 else None,
            "ticker_last_price": state.ticker_last_price,
            "aggtrade_last_price": state.aggtrade_last_price,
            "mark_price": state.mark_price,
            "mark_index_price": state.mark_index_price,
            "mark_funding_rate": state.mark_funding_rate,
            "mark_last_seen_ms": state.mark_last_seen_ms,
            "mark_status": effective_mark_status,
            "mark_raw_status": state.mark_status,
            "mark_basis_status": mark_basis_status,
            "mark_close_vs_decision_close_basis": mark_basis,
            "oi_open_interest": state.oi_open_interest,
            "oi_previous_open_interest": state.oi_previous_open_interest,
            "oi_change_pct_3x5m": state.oi_change_pct_3x5m,
            "oi_latest_timestamp_ms": state.oi_latest_timestamp_ms,
            "oi_previous_timestamp_ms": state.oi_previous_timestamp_ms,
            "oi_last_seen_ms": state.oi_last_seen_ms,
            "oi_source": state.oi_source,
            "oi_status": oi_status,
            "oi_raw_status": state.oi_status,
            "oi_reason": state.oi_reason,
            "current_oi_open_interest": state.current_oi_open_interest,
            "current_oi_timestamp_ms": state.current_oi_timestamp_ms,
            "current_oi_last_seen_ms": state.current_oi_last_seen_ms,
            "current_oi_source": state.current_oi_source,
            "current_oi_status": state.current_oi_status,
            "current_oi_raw_status": state.current_oi_status,
            "current_oi_reason": state.current_oi_reason,
            "current_oi_first_ok_seen_ms": state.current_oi_first_ok_seen_ms,
            "current_oi_first_ok_timestamp_ms": state.current_oi_first_ok_timestamp_ms,
            "current_oi_first_ok_open_interest": state.current_oi_first_ok_open_interest,
            "current_oi_first_ok_source": state.current_oi_first_ok_source,
            "current_oi_first_ok_status": state.current_oi_first_ok_status,
            "current_oi_first_ok_reason": state.current_oi_first_ok_reason,
            "pump_start_current_oi_open_interest": state.current_oi_first_ok_open_interest,
            "pump_start_current_oi_timestamp_ms": state.current_oi_first_ok_timestamp_ms,
            "pump_start_current_oi_last_seen_ms": state.current_oi_first_ok_seen_ms,
            "pump_start_current_oi_source": state.current_oi_first_ok_source,
            "pump_start_current_oi_status": state.current_oi_first_ok_status,
            "pump_start_current_oi_reason": state.current_oi_first_ok_reason,
            "signal_current_oi_open_interest": state.current_oi_open_interest,
            "signal_current_oi_timestamp_ms": state.current_oi_timestamp_ms,
            "signal_current_oi_last_seen_ms": state.current_oi_last_seen_ms,
            "signal_current_oi_source": state.current_oi_source,
            "signal_current_oi_status": state.current_oi_status,
            "signal_current_oi_reason": state.current_oi_reason,
            "prior_context_status": prior_context_status,
            "prior_context_raw_status": state.prior_context_status,
            "prior_context_reason": state.prior_context_reason,
            "prior_context_source": state.prior_context_source,
            "prior_context_last_seen_ms": state.prior_context_last_seen_ms,
            "prior_context_start_ms": state.prior_context_start_ms,
            "prior_context_end_ms": state.prior_context_end_ms,
            "prior_context_rows_used": state.prior_context_rows_used,
            "prior_context_lookback_hours": 24,
            "prior_spike_count_24h": state.prior_spike_count_24h,
            "prior_fast_fade_count_24h": state.prior_fast_fade_count_24h,
            # Legacy category contract field names are intentionally populated
            # from the current live 24h context; source labels above make this
            # explicit in artifacts.
            "prior_spike_count_72h": state.prior_spike_count_24h,
            "prior_fast_fade_count_72h": state.prior_fast_fade_count_24h,
            "prior_up_down_whipsaw_to_impulse_range": prior_whipsaw,
            "prior_context_spike_return_pct": state.prior_context_spike_return_pct,
            "prior_context_fast_fade_retrace_fraction": state.prior_context_fast_fade_retrace_fraction,
        }

    def _category_accepts(self, *, category: PumpCategoryContract, features: dict[str, object]) -> Live2CategoryEvaluation:
        if category.post_htf_acceptance_long:
            return _post_htf_acceptance_category_accepts(category=category, features=features)
        if features.get("live_setup_status") != "ok":
            reason = str(features.get("live_setup_reason") or "live_setup_not_backtest_candidate")
            if features.get("live_setup_status") == "not_ready":
                return _dependency(reason)
            return _reject(reason)
        runner_shape_evaluation = _runner_shape_accepts(category=category, features=features)
        if runner_shape_evaluation is not None:
            return runner_shape_evaluation
        if (
            category.max_prior_spike_count_72h is not None
            or category.max_prior_fast_fade_count_72h is not None
        ):
            if features.get("prior_context_status") != "ok":
                return _dependency("prior_24h_context_not_ready")
        if category.max_prior_spike_count_72h is not None:
            prior_spikes = _int_or_none(features.get("prior_spike_count_24h"))
            if prior_spikes is None:
                return _dependency("prior_spike_count_24h_not_ready")
            if prior_spikes > category.max_prior_spike_count_72h:
                return _reject("prior_spike_count_24h_above_category_max")
        if category.max_prior_fast_fade_count_72h is not None:
            prior_fast_fades = _int_or_none(features.get("prior_fast_fade_count_24h"))
            if prior_fast_fades is None:
                return _dependency("prior_fast_fade_count_24h_not_ready")
            if prior_fast_fades > category.max_prior_fast_fade_count_72h:
                return _reject("prior_fast_fade_count_24h_above_category_max")
        if category.max_prior_up_down_whipsaw_to_impulse_range is not None:
            prior_whipsaw = _float_or_none(features.get("prior_up_down_whipsaw_to_impulse_range"))
            if prior_whipsaw is None:
                return _dependency("prior_whipsaw_24h_not_ready")
            if prior_whipsaw > category.max_prior_up_down_whipsaw_to_impulse_range:
                return _reject("prior_whipsaw_24h_above_category_max")
        if category.min_oi_change_pct_3x5m is not None:
            oi_change = _float_or_none(features.get("oi_change_pct_3x5m"))
            if features.get("oi_status") != "ok" or oi_change is None:
                return _dependency("oi_context_not_ready")
            if oi_change <= category.min_oi_change_pct_3x5m:
                return _reject("oi_change_3x5m_below_category_min")
        if category.min_mark_close_vs_decision_close_basis is not None:
            mark_basis = _float_or_none(features.get("mark_close_vs_decision_close_basis"))
            if features.get("mark_basis_status") != "ok" or mark_basis is None:
                return _dependency("mark_price_context_not_ready")
            if mark_basis < category.min_mark_close_vs_decision_close_basis:
                return _reject("mark_basis_below_category_min")
        baseline_quote = _float_or_none(features.get("baseline_quote_5s"))
        if category.min_baseline_quote_daily_proxy is not None:
            baseline_quote_daily_proxy = _float_or_none(features.get("baseline_quote_daily_proxy"))
            if baseline_quote_daily_proxy is None or baseline_quote_daily_proxy <= 0:
                return _dependency("stream_baseline_quote_daily_proxy_not_ready")
            if baseline_quote_daily_proxy < category.min_baseline_quote_daily_proxy:
                return _reject("stream_baseline_quote_below_category_min")
        quote_ratio = _float_or_none(features.get("start_quote_ratio"))
        if category.max_start_quote_ratio is not None:
            if quote_ratio is None:
                return _dependency("start_quote_ratio_not_ready")
            if quote_ratio > category.max_start_quote_ratio:
                return _reject("start_quote_ratio_above_category_max")
        trade_ratio = _float_or_none(features.get("start_trade_ratio"))
        if category.max_start_trade_ratio is not None:
            if trade_ratio is None:
                return _dependency("start_trade_ratio_not_ready")
            if trade_ratio > category.max_start_trade_ratio:
                return _reject("start_trade_ratio_above_category_max")
        quote_ratio_per_abs_return = _float_or_none(features.get("start_quote_ratio_per_abs_return"))
        if category.max_start_quote_ratio_per_abs_return is not None:
            if quote_ratio_per_abs_return is None:
                return _reject("start_quote_ratio_per_abs_return_not_ready")
            if quote_ratio_per_abs_return > category.max_start_quote_ratio_per_abs_return:
                return _reject("start_quote_ratio_per_abs_return_above_category_max")
        trade_ratio_per_abs_return = _float_or_none(features.get("start_trade_ratio_per_abs_return"))
        if category.max_start_trade_ratio_per_abs_return is not None:
            if trade_ratio_per_abs_return is None:
                return _reject("start_trade_ratio_per_abs_return_not_ready")
            if trade_ratio_per_abs_return > category.max_start_trade_ratio_per_abs_return:
                return _reject("start_trade_ratio_per_abs_return_above_category_max")
        range_ratio = _float_or_none(features.get("start_range_pct_ratio_to_baseline"))
        if category.min_start_range_pct_ratio_to_baseline is not None:
            if range_ratio is None:
                return _dependency("start_range_ratio_not_ready")
            if range_ratio < category.min_start_range_pct_ratio_to_baseline:
                return _reject("start_range_ratio_below_category_min")
        if category.max_start_range_pct_ratio_to_baseline is not None:
            if range_ratio is None:
                return _dependency("start_range_ratio_not_ready")
            if range_ratio > category.max_start_range_pct_ratio_to_baseline:
                return _reject("start_range_ratio_above_category_max")
        taker_share_delta = _float_or_none(features.get("start_taker_buy_quote_share_delta"))
        if category.max_start_taker_buy_quote_share_delta is not None:
            if taker_share_delta is None:
                return _reject("start_taker_buy_quote_share_delta_not_ready")
            if taker_share_delta > category.max_start_taker_buy_quote_share_delta:
                return _reject("start_taker_buy_quote_share_delta_above_category_max")
        live_confirmed_taker_share = _float_or_none(features.get("live_confirmed_taker_buy_quote_share"))
        if category.min_next_taker_buy_quote_share is not None:
            if features.get("flow_hold_status") != "ok" or live_confirmed_taker_share is None:
                return _reject("live_confirmed_taker_buy_quote_share_not_ready")
            if live_confirmed_taker_share < category.min_next_taker_buy_quote_share:
                return _reject("live_confirmed_taker_buy_quote_share_below_category_min")
        flow_hold_count = _int_or_none(features.get("flow_hold_count"))
        if category.min_flow_hold_count is not None:
            if features.get("flow_hold_status") != "ok" or flow_hold_count is None:
                return _reject("flow_hold_count_not_ready")
            if flow_hold_count < category.min_flow_hold_count:
                return _reject("flow_hold_count_below_category_min")
        initial_risk_pct = _float_or_none(features.get("initial_risk_pct_at_decision"))
        if category.min_initial_risk_pct is not None and (initial_risk_pct is None or initial_risk_pct < category.min_initial_risk_pct):
            return _reject("initial_risk_pct_below_category_min")
        if initial_risk_pct is not None and initial_risk_pct > LIVE2_BACKTEST_MAX_INITIAL_RISK_PCT:
            return _reject("initial_risk_pct_above_backtest_max")
        if category.max_initial_risk_pct is not None and initial_risk_pct is not None and initial_risk_pct > category.max_initial_risk_pct:
            return _reject("initial_risk_pct_above_category_max")
        return Live2CategoryEvaluation(True, "accepted", "accepted")


def _runner_shape_accepts(*, category: PumpCategoryContract, features: dict[str, object]) -> Live2CategoryEvaluation | None:
    checks: tuple[tuple[str, str, float | None, str], ...] = (
        ("runner_shape_quote_ratio", "min_runner_shape_quote_ratio", category.min_runner_shape_quote_ratio, "runner_shape_quote_ratio_below_category_min"),
        ("runner_shape_trade_ratio", "min_runner_shape_trade_ratio", category.min_runner_shape_trade_ratio, "runner_shape_trade_ratio_below_category_min"),
        ("runner_shape_range_ratio", "min_runner_shape_range_ratio", category.min_runner_shape_range_ratio, "runner_shape_range_ratio_below_category_min"),
        ("runner_shape_quote_acceleration", "min_runner_shape_quote_acceleration", category.min_runner_shape_quote_acceleration, "runner_shape_quote_acceleration_below_category_min"),
        ("runner_shape_trade_acceleration", "min_runner_shape_trade_acceleration", category.min_runner_shape_trade_acceleration, "runner_shape_trade_acceleration_below_category_min"),
        ("runner_shape_range_acceleration", "min_runner_shape_range_acceleration", category.min_runner_shape_range_acceleration, "runner_shape_range_acceleration_below_category_min"),
        ("runner_shape_second_half_return_pct", "min_runner_shape_second_half_return_pct", category.min_runner_shape_second_half_return_pct, "runner_shape_second_half_return_below_category_min"),
    )
    any_enabled = False
    for feature_key, _threshold_key, threshold, reason in checks:
        if threshold is None:
            continue
        any_enabled = True
        value = _float_or_none(features.get(feature_key))
        if value is None:
            return _reject(f"{feature_key}_not_ready")
        if value < threshold:
            return _reject(reason)
    if category.max_runner_shape_top1_quote_share is not None:
        any_enabled = True
        top1_quote_share = _float_or_none(features.get("runner_shape_top1_quote_share"))
        if top1_quote_share is None:
            return _reject("runner_shape_top1_quote_share_not_ready")
        if top1_quote_share > category.max_runner_shape_top1_quote_share:
            return _reject("runner_shape_top1_quote_share_above_category_max")
    if not any_enabled:
        return None
    return None


def _category_effective_features(*, category: PumpCategoryContract, features: dict[str, object]) -> dict[str, object]:
    if not category.post_htf_acceptance_long:
        return features
    entry = _float_or_none(features.get("post_htf_acceptance_signal_entry_price"))
    stop = _float_or_none(features.get("post_htf_acceptance_initial_stop_at_decision"))
    risk = _float_or_none(features.get("post_htf_acceptance_initial_risk_pct_at_decision"))
    tp1 = _float_or_none(features.get("post_htf_acceptance_tp1_at_decision"))
    return {
        **features,
        "signal_entry_price": entry,
        "initial_stop_at_decision": stop,
        "initial_risk_pct_at_decision": risk,
        "tp1_at_decision": tp1,
        "pump_category_family": "post_htf_acceptance",
        "selected_source_flow_window_ms": features.get("post_htf_acceptance_ltf6_flow_window_ms"),
        "selected_source_flow_quote_per_second": features.get("post_htf_acceptance_ltf6_quote_per_second"),
        "selected_source_flow_trades_per_second": features.get("post_htf_acceptance_ltf6_trades_per_second"),
        "selected_source_flow_quote_ratio": features.get("post_htf_acceptance_htf_quote_ratio"),
        "selected_source_flow_trade_ratio": features.get("post_htf_acceptance_htf_trade_ratio"),
        "post_htf_acceptance_selected": True,
        "post_htf_acceptance_artifact_mode": "post_htf_acceptance_long",
    }


def _post_htf_acceptance_category_accepts(
    *,
    category: PumpCategoryContract,
    features: dict[str, object],
) -> Live2CategoryEvaluation:
    status = str(features.get("post_htf_acceptance_status") or "")
    reason = str(features.get("post_htf_acceptance_reason") or "post_htf_acceptance_not_ready")
    if status != "ok":
        if status == "not_ready":
            return _dependency(reason)
        return _reject(reason)
    if bool(features.get("post_htf_acceptance_oi_divergence_rejected")):
        return _reject(str(features.get("post_htf_acceptance_oi_divergence_reason") or "post_htf_acceptance_oi_up_price_down"))
    htf_return = _float_or_none(features.get("post_htf_acceptance_htf_return_pct"))
    if htf_return is None or htf_return < POST_HTF_ACCEPTANCE_MIN_HTF_RETURN_PCT:
        return _reject("post_htf_acceptance_htf_return_below_min")
    htf_quote_ratio = _float_or_none(features.get("post_htf_acceptance_htf_quote_ratio"))
    if htf_quote_ratio is None or htf_quote_ratio < POST_HTF_ACCEPTANCE_MIN_HTF_QUOTE_RATIO:
        return _reject("post_htf_acceptance_htf_quote_ratio_below_min")
    htf_trade_ratio = _float_or_none(features.get("post_htf_acceptance_htf_trade_ratio"))
    if htf_trade_ratio is None or htf_trade_ratio < POST_HTF_ACCEPTANCE_MIN_HTF_TRADE_RATIO:
        return _reject("post_htf_acceptance_htf_trade_ratio_below_min")
    ltf_return = _float_or_none(features.get("post_htf_acceptance_ltf6_return_pct"))
    if category.min_post_htf_ltf6_return_pct is not None and (
        ltf_return is None or ltf_return < category.min_post_htf_ltf6_return_pct
    ):
        return _reject("post_htf_acceptance_ltf6_return_below_min")
    risk = _float_or_none(features.get("post_htf_acceptance_initial_risk_pct_at_decision"))
    if category.min_post_htf_structural_risk_pct is not None and (
        risk is None or risk < category.min_post_htf_structural_risk_pct
    ):
        return _reject("post_htf_acceptance_structural_risk_below_min")
    if category.max_post_htf_structural_risk_pct is not None and (
        risk is None or risk > category.max_post_htf_structural_risk_pct
    ):
        return _reject("post_htf_acceptance_structural_risk_above_max")
    if features.get("prior_context_status") != "ok":
        return _dependency("prior_24h_context_not_ready")
    prior_spikes = _int_or_none(features.get("prior_spike_count_24h"))
    if prior_spikes is None:
        return _dependency("prior_spike_count_24h_not_ready")
    if category.max_prior_spike_count_72h is not None and prior_spikes > category.max_prior_spike_count_72h:
        return _reject("post_htf_acceptance_prior_spike_count_24h_above_max")
    last3_quote_share = _float_or_none(features.get("post_htf_acceptance_ltf6_last3_quote_share"))
    if category.max_post_htf_last3_quote_share is not None and (
        last3_quote_share is None or last3_quote_share > category.max_post_htf_last3_quote_share
    ):
        return _reject("post_htf_acceptance_last3_quote_share_above_max")
    top1_quote_share = _float_or_none(features.get("post_htf_acceptance_ltf6_top1_quote_share"))
    if category.max_post_htf_top1_quote_share is not None and (
        top1_quote_share is None or top1_quote_share > category.max_post_htf_top1_quote_share
    ):
        return _reject("post_htf_acceptance_top1_quote_share_above_max")
    return Live2CategoryEvaluation(True, "accepted", "accepted")


@dataclass(frozen=True, slots=True)
class Live2FlowHoldSnapshot:
    """Closed live-flow confirmation available before entry.

    This deliberately uses only closed live WS candles at or before the decision
    candle. It does not read future buckets and it does not perform hot-path IO.
    """

    status: str
    reason: str
    count: int | None
    window_ms: int | None
    quote_volume: float | None
    number_of_trades: int | None
    taker_buy_quote_volume: float | None
    taker_buy_quote_share_mean: float | None
    taker_buy_quote_share_last: float | None
    taker_buy_quote_share_delta: float | None


def _trailing_live_flow_hold(
    *,
    closed_5s: tuple[Live2Candle, ...],
    decision_candle: Live2Candle,
    baseline_quote: float,
    baseline_trades: float,
    baseline_taker_share: float,
) -> Live2FlowHoldSnapshot:
    if baseline_quote <= 0 or baseline_trades <= 0:
        return _flow_hold_not_ready("baseline_flow_not_ready")
    if decision_candle.live_ws_trade_count <= 0:
        return _flow_hold_not_ready("decision_candle_not_from_live_ws")
    ordered = tuple(item for item in closed_5s if item.open_time_ms <= decision_candle.open_time_ms)
    if not ordered or ordered[-1].open_time_ms != decision_candle.open_time_ms:
        return _flow_hold_not_ready("decision_candle_missing_from_closed_ring")

    held: list[Live2Candle] = []
    expected_open_ms: int | None = decision_candle.open_time_ms
    for item in reversed(ordered):
        if expected_open_ms is not None and item.open_time_ms != expected_open_ms:
            break
        if not _is_live_flow_hold_candle(
            item,
            baseline_quote=baseline_quote,
            baseline_trades=baseline_trades,
            baseline_taker_share=baseline_taker_share,
        ):
            break
        held.append(item)
        expected_open_ms = item.open_time_ms - item.timeframe_ms

    if not held:
        return Live2FlowHoldSnapshot(
            status="ok",
            reason="no_trailing_live_flow_hold_candles",
            count=0,
            window_ms=0,
            quote_volume=0.0,
            number_of_trades=0,
            taker_buy_quote_volume=0.0,
            taker_buy_quote_share_mean=None,
            taker_buy_quote_share_last=None,
            taker_buy_quote_share_delta=None,
        )

    chronological = tuple(reversed(held))
    quote_volume = sum(item.quote_volume for item in chronological)
    number_of_trades = sum(item.number_of_trades for item in chronological)
    taker_buy_quote_volume = sum(item.taker_buy_quote_volume for item in chronological)
    shares = [
        item.taker_buy_quote_volume / item.quote_volume
        for item in chronological
        if item.quote_volume > 0
    ]
    share_mean = sum(shares) / len(shares) if shares else None
    share_last = shares[-1] if shares else None
    share_delta = None
    if share_last is not None and baseline_taker_share > 0:
        share_delta = share_last - baseline_taker_share
    return Live2FlowHoldSnapshot(
        status="ok",
        reason="trailing_live_flow_hold_ready",
        count=len(chronological),
        window_ms=sum(item.timeframe_ms for item in chronological),
        quote_volume=quote_volume,
        number_of_trades=number_of_trades,
        taker_buy_quote_volume=taker_buy_quote_volume,
        taker_buy_quote_share_mean=share_mean,
        taker_buy_quote_share_last=share_last,
        taker_buy_quote_share_delta=share_delta,
    )


def _flow_hold_not_ready(reason: str) -> Live2FlowHoldSnapshot:
    return Live2FlowHoldSnapshot(
        status="not_ready",
        reason=reason,
        count=None,
        window_ms=None,
        quote_volume=None,
        number_of_trades=None,
        taker_buy_quote_volume=None,
        taker_buy_quote_share_mean=None,
        taker_buy_quote_share_last=None,
        taker_buy_quote_share_delta=None,
    )


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


def _post_htf_acceptance_oi_divergence_check(
    *,
    closed_5s: tuple[Live2Candle, ...],
    closed_1m: tuple[Live2Candle, ...],
    decision_candle: Live2Candle,
    oi_status: str,
    oi_change_pct_3x5m: float | None,
) -> dict[str, object]:
    oi_change = _float_or_none(oi_change_pct_3x5m)
    common = {
        "window_candles": POST_HTF_ACCEPTANCE_OI_DIVERGENCE_WINDOW_CANDLES,
        "oi_change_pct_3x5m": oi_change,
    }
    if oi_status != "ok" or oi_change is None:
        return {**common, "status": "not_ready", "reason": "oi_context_not_ready_for_divergence_guard", "rejected": False}
    price_return = None
    ordered_5s = tuple(sorted((item for item in closed_5s if int(item.open_time_ms) <= decision_candle.open_time_ms), key=lambda item: item.open_time_ms))
    if len(ordered_5s) >= POST_HTF_ACCEPTANCE_OI_DIVERGENCE_WINDOW_CANDLES:
        window = ordered_5s[-POST_HTF_ACCEPTANCE_OI_DIVERGENCE_WINDOW_CANDLES:]
        if window[0].open > 0:
            price_return = (decision_candle.close / window[0].open) - 1.0
    if price_return is None:
        cutoff_ms = int(decision_candle.close_time_ms) - 15 * 60_000
        baseline_1m = tuple(item for item in closed_1m if int(item.close_time_ms) <= cutoff_ms)
        if baseline_1m and baseline_1m[-1].close > 0:
            price_return = (decision_candle.close / baseline_1m[-1].close) - 1.0
    if price_return is None:
        return {**common, "status": "not_ready", "reason": "price_context_not_ready_for_oi_divergence_guard", "rejected": False}
    rejected = oi_change > 0.0 and price_return < 0.0
    return {
        **common,
        "status": "ok",
        "reason": "oi_up_price_down_blocked" if rejected else "oi_price_divergence_guard_passed",
        "rejected": rejected,
        "price_return_pct": price_return,
    }


def _post_htf_acceptance_setup(
    *,
    closed_5s: tuple[Live2Candle, ...],
    closed_1m: tuple[Live2Candle, ...],
    decision_candle: Live2Candle,
) -> dict[str, object]:
    decision_open_ms = int(decision_candle.open_time_ms)
    ordered = tuple(sorted((item for item in closed_5s if int(item.open_time_ms) <= decision_open_ms), key=lambda item: item.open_time_ms))
    segment = ordered[-POST_HTF_ACCEPTANCE_CONFIRMATION_CANDLES:]
    htf_segment = ordered[-(POST_HTF_ACCEPTANCE_HTF_CANDLES + POST_HTF_ACCEPTANCE_CONFIRMATION_CANDLES):-POST_HTF_ACCEPTANCE_CONFIRMATION_CANDLES]
    htf_open_ms = int(htf_segment[0].open_time_ms) if htf_segment else decision_open_ms - (
        POST_HTF_ACCEPTANCE_CONFIRMATION_CANDLES + POST_HTF_ACCEPTANCE_HTF_CANDLES - 1
    ) * LIVE2_BACKTEST_ENTRY_TIMEFRAME_MS
    htf_close_ms = htf_open_ms + POST_HTF_ACCEPTANCE_HTF_CANDLES * LIVE2_BACKTEST_ENTRY_TIMEFRAME_MS
    common_not_ready = {
        "mode": True,
        "confirmation_candles": len(segment),
        "htf_open_ms": htf_open_ms,
        "htf_close_ms": htf_close_ms,
        "htf_alignment": "rolling_60s_5s_step",
        "htf_calendar_aligned": False,
        "target_r": POST_HTF_ACCEPTANCE_TARGET_R,
        "structural_stop_source": "rolling_closed_htf_anomaly_low_buffered_5bps",
    }
    if not segment or segment[-1].open_time_ms != decision_candle.open_time_ms:
        return {**common_not_ready, "status": "not_ready", "reason": "post_htf_acceptance_ltf_segment_not_ready"}
    if len(segment) < POST_HTF_ACCEPTANCE_CONFIRMATION_CANDLES:
        return {**common_not_ready, "status": "not_ready", "reason": "post_htf_acceptance_waiting_for_6_closed_5s"}
    expected_opens = tuple(
        decision_open_ms - (POST_HTF_ACCEPTANCE_CONFIRMATION_CANDLES - 1 - index) * LIVE2_BACKTEST_ENTRY_TIMEFRAME_MS
        for index in range(POST_HTF_ACCEPTANCE_CONFIRMATION_CANDLES)
    )
    actual_opens = tuple(int(item.open_time_ms) for item in segment)
    if actual_opens != expected_opens:
        return {**common_not_ready, "status": "rejected", "reason": "post_htf_acceptance_ltf6_window_not_contiguous"}
    if len(htf_segment) < POST_HTF_ACCEPTANCE_HTF_CANDLES:
        return {**common_not_ready, "status": "not_ready", "reason": "post_htf_acceptance_rolling_htf_window_not_ready"}
    expected_htf_opens = tuple(
        htf_open_ms + index * LIVE2_BACKTEST_ENTRY_TIMEFRAME_MS
        for index in range(POST_HTF_ACCEPTANCE_HTF_CANDLES)
    )
    actual_htf_opens = tuple(int(item.open_time_ms) for item in htf_segment)
    if actual_htf_opens != expected_htf_opens:
        return {**common_not_ready, "status": "rejected", "reason": "post_htf_acceptance_rolling_htf_window_not_contiguous"}
    htf = _aggregate_candles_to_live2_candle(candles=htf_segment, timeframe_ms=LIVE2_BACKTEST_SETUP_TIMEFRAME_MS)
    baseline = tuple(item for item in closed_1m if int(item.close_time_ms) <= htf_open_ms)[-POST_HTF_ACCEPTANCE_BASELINE_WINDOWS:]
    if len(baseline) < POST_HTF_ACCEPTANCE_BASELINE_WINDOWS:
        return {**common_not_ready, "status": "not_ready", "reason": "post_htf_acceptance_1m_baseline_not_ready"}
    baseline_quote = _median([item.quote_volume for item in baseline])
    baseline_trades = _median([float(item.number_of_trades) for item in baseline])
    if baseline_quote <= 0 or baseline_trades <= 0:
        return {
            **common_not_ready,
            "status": "not_ready",
            "reason": "post_htf_acceptance_1m_baseline_invalid",
            "baseline_quote_1m": baseline_quote,
            "baseline_trade_count_1m": baseline_trades,
        }
    htf_return = (htf.close / htf.open) - 1.0 if htf.open > 0 else None
    htf_quote_ratio = htf.quote_volume / baseline_quote if baseline_quote > 0 else None
    htf_trade_ratio = float(htf.number_of_trades) / baseline_trades if baseline_trades > 0 else None
    htf_window_ms = POST_HTF_ACCEPTANCE_HTF_CANDLES * LIVE2_BACKTEST_ENTRY_TIMEFRAME_MS
    segment_quote = sum(item.quote_volume for item in segment)
    segment_trades = sum(float(item.number_of_trades) for item in segment)
    segment_taker_quote = sum(item.taker_buy_quote_volume for item in segment)
    segment_open = segment[0].open
    segment_close = segment[-1].close
    segment_low = min(item.low for item in segment)
    ltf_return = (segment_close / segment_open) - 1.0 if segment_open > 0 else None
    top1_quote_share = max((item.quote_volume for item in segment), default=0.0) / segment_quote if segment_quote > 0 else None
    last3 = segment[-3:]
    last3_quote_share = sum(item.quote_volume for item in last3) / segment_quote if segment_quote > 0 else None
    taker_share = segment_taker_quote / segment_quote if segment_quote > 0 else None
    entry = decision_candle.close
    stop = htf.low * (1.0 - POST_HTF_ACCEPTANCE_STOP_BUFFER_PCT)
    risk_pct = (entry - stop) / entry if entry > 0 and stop > 0 else None
    target_r = POST_HTF_ACCEPTANCE_TARGET_R
    tp1 = entry + target_r * (entry - stop) if entry > stop else None
    return {
        "mode": True,
        "status": "ok",
        "reason": "post_htf_acceptance_setup_ready",
        "confirmation_candles": len(segment),
        "htf_alignment": "rolling_60s_5s_step",
        "htf_calendar_aligned": False,
        "htf_open_ms": htf_open_ms,
        "htf_close_ms": htf_close_ms,
        "htf_open": htf.open,
        "htf_high": htf.high,
        "htf_low": htf.low,
        "htf_close": htf.close,
        "htf_return_pct": htf_return,
        "htf_quote_ratio": htf_quote_ratio,
        "htf_trade_ratio": htf_trade_ratio,
        "htf_flow_window_ms": htf_window_ms,
        "htf_quote_per_second": htf.quote_volume / (htf_window_ms / 1000.0),
        "htf_trades_per_second": float(htf.number_of_trades) / (htf_window_ms / 1000.0),
        "baseline_quote_1m": baseline_quote,
        "baseline_trade_count_1m": baseline_trades,
        "baseline_source": "startup_or_live_closed_1m_htf_baseline_for_rolling_htf",
        "ltf6_return_pct": ltf_return,
        "ltf6_quote_volume": segment_quote,
        "ltf6_number_of_trades": segment_trades,
        "ltf6_flow_window_ms": POST_HTF_ACCEPTANCE_CONFIRMATION_CANDLES * LIVE2_BACKTEST_ENTRY_TIMEFRAME_MS,
        "ltf6_quote_per_second": segment_quote / ((POST_HTF_ACCEPTANCE_CONFIRMATION_CANDLES * LIVE2_BACKTEST_ENTRY_TIMEFRAME_MS) / 1000.0),
        "ltf6_trades_per_second": segment_trades / ((POST_HTF_ACCEPTANCE_CONFIRMATION_CANDLES * LIVE2_BACKTEST_ENTRY_TIMEFRAME_MS) / 1000.0),
        "ltf6_taker_buy_quote_share": taker_share,
        "ltf6_top1_quote_share": top1_quote_share,
        "ltf6_last3_quote_share": last3_quote_share,
        "ltf6_low_vs_htf_close": (segment_low / htf.close) - 1.0 if htf.close > 0 else None,
        "signal_entry_price": entry,
        "initial_stop_at_decision": stop,
        "initial_risk_pct_at_decision": risk_pct,
        "tp1_at_decision": tp1,
        "target_r": target_r,
        "structural_stop_source": "rolling_closed_htf_anomaly_low_buffered_5bps",
    }


def _live_backtest_like_setup(
    *,
    closed_5s: tuple[Live2Candle, ...],
    closed_1m: tuple[Live2Candle, ...],
    decision_candle: Live2Candle,
) -> dict[str, object]:
    decision_open_ms = int(decision_candle.open_time_ms)
    ordered = tuple(sorted((item for item in closed_5s if int(item.open_time_ms) <= decision_open_ms), key=lambda item: item.open_time_ms))
    segment = ordered[-LIVE2_BACKTEST_SETUP_CANDLES:]
    setup_open_ms = int(segment[0].open_time_ms) if segment else decision_open_ms - (
        LIVE2_BACKTEST_SETUP_CANDLES - 1
    ) * LIVE2_BACKTEST_ENTRY_TIMEFRAME_MS
    if not segment or segment[-1].open_time_ms != decision_candle.open_time_ms:
        return {
            "status": "not_ready",
            "reason": "live_setup_entry_segment_not_ready",
            "closed_entry_candles": len(segment),
            "baseline_1m_count": 0,
            "alignment": "rolling_60s_5s_step",
            "calendar_aligned": False,
        }
    expected_opens = tuple(
        decision_open_ms - (len(segment) - 1 - index) * LIVE2_BACKTEST_ENTRY_TIMEFRAME_MS
        for index in range(len(segment))
    )
    actual_opens = tuple(int(item.open_time_ms) for item in segment)
    if actual_opens != expected_opens:
        return {
            "status": "rejected",
            "reason": "live_setup_rolling_window_not_contiguous",
            "closed_entry_candles": len(segment),
            "baseline_1m_count": 0,
            "alignment": "rolling_60s_5s_step",
            "calendar_aligned": False,
        }
    baseline = tuple(
        item
        for item in closed_1m
        if int(item.close_time_ms) <= setup_open_ms
    )[-LIVE2_BACKTEST_BASELINE_CANDLES:]
    baseline_source = "startup_or_live_closed_1m_baseline_for_rolling_live_setup"
    baseline_quote = _median([item.quote_volume for item in baseline])
    baseline_trades = _median([float(item.number_of_trades) for item in baseline])
    baseline_range_pct = _median([_range_pct(item) for item in baseline])
    if len(baseline) < LIVE2_BACKTEST_BASELINE_CANDLES:
        return {
            "status": "not_ready",
            "reason": "live_setup_1m_baseline_not_ready",
            "closed_entry_candles": len(segment),
            "baseline_1m_count": len(baseline),
            "baseline_source": baseline_source,
            "alignment": "rolling_60s_5s_step",
            "calendar_aligned": False,
        }
    if baseline_quote <= 0 or baseline_trades <= 0 or baseline_range_pct <= 0:
        return {
            "status": "not_ready",
            "reason": "live_setup_1m_baseline_invalid",
            "closed_entry_candles": len(segment),
            "baseline_1m_count": len(baseline),
            "baseline_quote_1m": baseline_quote,
            "baseline_trade_count_1m": baseline_trades,
            "baseline_range_pct_1m": baseline_range_pct,
            "baseline_source": baseline_source,
            "alignment": "rolling_60s_5s_step",
            "calendar_aligned": False,
        }
    closed_entry_candles = len(segment)
    setup_elapsed_fraction = min(
        1.0,
        (closed_entry_candles * LIVE2_BACKTEST_ENTRY_TIMEFRAME_MS) / LIVE2_BACKTEST_SETUP_TIMEFRAME_MS,
    )
    elapsed_for_ratio = max(1e-9, setup_elapsed_fraction)
    quote_volume = sum(item.quote_volume for item in segment)
    trade_count = sum(float(item.number_of_trades) for item in segment)
    flow_window_ms = closed_entry_candles * LIVE2_BACKTEST_ENTRY_TIMEFRAME_MS
    flow_window_seconds = max(1e-9, flow_window_ms / 1000.0)
    raw_quote_ratio = quote_volume / baseline_quote
    raw_trade_ratio = trade_count / baseline_trades
    quote_ratio = raw_quote_ratio / elapsed_for_ratio
    trade_ratio = raw_trade_ratio / elapsed_for_ratio
    min_raw_quote_ratio = LIVE2_BACKTEST_MIN_QUOTE_RATIO_START * min(1.0, max(0.35, elapsed_for_ratio))
    min_raw_trade_ratio = LIVE2_BACKTEST_MIN_TRADE_RATIO_START * min(1.0, max(0.35, elapsed_for_ratio))
    low = min(item.low for item in segment)
    high = max(item.high for item in segment)
    open_price = segment[0].open
    close_price = decision_candle.close
    setup_range = high - low
    range_pct = setup_range / open_price if open_price > 0 else None
    range_pct_ratio = (range_pct / baseline_range_pct) if range_pct is not None and baseline_range_pct > 0 else None
    decision_return_from_start_open = (close_price / open_price) - 1.0 if open_price > 0 else None
    abs_start_return = abs(decision_return_from_start_open) if decision_return_from_start_open is not None else None
    quote_ratio_per_abs_return = quote_ratio / abs_start_return if abs_start_return is not None and abs_start_return > 0 else None
    trade_ratio_per_abs_return = trade_ratio / abs_start_return if abs_start_return is not None and abs_start_return > 0 else None
    activation_price = open_price + max(0.0, close_price - open_price) * 0.50
    hold_count = sum(1 for item in segment if item.close >= activation_price)
    price_retention = (close_price - open_price) / (high - open_price) if high > open_price else None
    verticality = _verticality_score(segment)
    baseline_taker_share = _median([
        item.taker_buy_quote_volume / item.quote_volume
        for item in baseline
        if item.quote_volume > 0
    ])
    start_taker_buy_quote_share = (
        segment[0].taker_buy_quote_volume / segment[0].quote_volume
        if segment[0].quote_volume > 0
        else None
    )
    segment_taker_shares = [
        item.taker_buy_quote_volume / item.quote_volume
        for item in segment
        if item.quote_volume > 0
    ]
    next_taker_buy_quote_share_mean = (
        sum(segment_taker_shares) / len(segment_taker_shares)
        if segment_taker_shares
        else None
    )
    start_taker_buy_quote_share_delta = (
        start_taker_buy_quote_share - baseline_taker_share
        if start_taker_buy_quote_share is not None and baseline_taker_share > 0
        else None
    )
    next_taker_buy_quote_share_delta = (
        next_taker_buy_quote_share_mean - baseline_taker_share
        if next_taker_buy_quote_share_mean is not None and baseline_taker_share > 0
        else None
    )
    flow_hold_threshold_quote = max(0.35 * quote_volume, 3.0 * baseline_quote)
    flow_hold_threshold_trades = max(0.35 * trade_count, 3.0 * baseline_trades)
    flow_hold_candles = tuple(
        item
        for item in segment
        if item.quote_volume >= flow_hold_threshold_quote
        and float(item.number_of_trades) >= flow_hold_threshold_trades
    )
    flow_hold_count = len(flow_hold_candles)
    flow_hold_quote_volume = sum(item.quote_volume for item in flow_hold_candles)
    flow_hold_trades = sum(float(item.number_of_trades) for item in flow_hold_candles)
    flow_hold_taker_quote = sum(item.taker_buy_quote_volume for item in flow_hold_candles)
    prior_whipsaw = _prior_up_down_whipsaw_to_impulse_range(baseline, impulse_range=setup_range)
    decision_ema20 = _ema20([item.close for item in (*baseline, decision_candle)])
    previous_stop = low - LIVE2_BACKTEST_STOP_BUFFER_RANGE_FRACTION * setup_range
    initial_stop = max(previous_stop, decision_ema20) if decision_ema20 is not None else previous_stop
    pump_leg_risk = close_price - low
    base_tp1 = close_price + LIVE2_BACKTEST_TP1_R * pump_leg_risk
    tp1, tp1_round_step = _round_up_tp1_to_market_number(
        base_tp1,
        reference_price=close_price,
        movement=max(pump_leg_risk, setup_range),
    )
    first_half: tuple[Live2Candle, ...] = ()
    second_half: tuple[Live2Candle, ...] = ()
    if len(segment) >= LIVE2_RUNNER_SHAPE_HALF_CANDLES * 2:
        first_half = tuple(segment[:LIVE2_RUNNER_SHAPE_HALF_CANDLES])
        second_half = tuple(segment[LIVE2_RUNNER_SHAPE_HALF_CANDLES:LIVE2_RUNNER_SHAPE_HALF_CANDLES * 2])
    first_half_quote = sum(item.quote_volume for item in first_half) if first_half else None
    second_half_quote = sum(item.quote_volume for item in second_half) if second_half else None
    first_half_trades = sum(float(item.number_of_trades) for item in first_half) if first_half else None
    second_half_trades = sum(float(item.number_of_trades) for item in second_half) if second_half else None
    first_half_range_pct = _window_range_pct(first_half)
    second_half_range_pct = _window_range_pct(second_half)
    runner_shape_quote_acceleration = (
        second_half_quote / first_half_quote
        if first_half_quote is not None and first_half_quote > 0 and second_half_quote is not None
        else None
    )
    runner_shape_trade_acceleration = (
        second_half_trades / first_half_trades
        if first_half_trades is not None and first_half_trades > 0 and second_half_trades is not None
        else None
    )
    runner_shape_range_acceleration = (
        second_half_range_pct / first_half_range_pct
        if first_half_range_pct is not None and first_half_range_pct > 0 and second_half_range_pct is not None
        else None
    )
    runner_shape_second_half_return_pct = (
        (second_half[-1].close / second_half[0].open) - 1.0
        if second_half and second_half[0].open > 0
        else None
    )
    runner_shape_top1_quote_share = (
        max(item.quote_volume for item in segment) / quote_volume
        if quote_volume > 0
        else None
    )
    common = {
        "closed_entry_candles": closed_entry_candles,
        "elapsed_fraction": setup_elapsed_fraction,
        "raw_quote_ratio": raw_quote_ratio,
        "raw_trade_ratio": raw_trade_ratio,
        "min_raw_quote_ratio": min_raw_quote_ratio,
        "min_raw_trade_ratio": min_raw_trade_ratio,
        "quote_ratio": quote_ratio,
        "trade_ratio": trade_ratio,
        "range": setup_range,
        "range_pct": range_pct,
        "runner_shape_first_half_quote_volume": first_half_quote,
        "runner_shape_second_half_quote_volume": second_half_quote,
        "runner_shape_first_half_number_of_trades": first_half_trades,
        "runner_shape_second_half_number_of_trades": second_half_trades,
        "runner_shape_first_half_range_pct": first_half_range_pct,
        "runner_shape_second_half_range_pct": second_half_range_pct,
        "runner_shape_quote_acceleration": runner_shape_quote_acceleration,
        "runner_shape_trade_acceleration": runner_shape_trade_acceleration,
        "runner_shape_range_acceleration": runner_shape_range_acceleration,
        "runner_shape_second_half_return_pct": runner_shape_second_half_return_pct,
        "runner_shape_top1_quote_share": runner_shape_top1_quote_share,
        "flow_window_ms": flow_window_ms,
        "flow_quote_per_second": quote_volume / flow_window_seconds,
        "flow_trades_per_second": trade_count / flow_window_seconds,
        "range_pct_ratio_to_baseline": range_pct_ratio,
        "decision_return_from_start_open": decision_return_from_start_open,
        "start_quote_ratio_per_abs_return": quote_ratio_per_abs_return,
        "start_trade_ratio_per_abs_return": trade_ratio_per_abs_return,
        "activation_price": activation_price,
        "hold_count": hold_count,
        "price_retention": price_retention,
        "verticality_score": verticality,
        "baseline_taker_buy_quote_share_median": baseline_taker_share,
        "start_taker_buy_quote_share": start_taker_buy_quote_share,
        "start_taker_buy_quote_share_delta": start_taker_buy_quote_share_delta,
        "next_n_taker_buy_quote_share_mean": next_taker_buy_quote_share_mean,
        "next_n_taker_buy_quote_share_delta": next_taker_buy_quote_share_delta,
        "flow_hold_count": flow_hold_count,
        "flow_hold_window_ms": closed_entry_candles * LIVE2_BACKTEST_ENTRY_TIMEFRAME_MS,
        "flow_hold_quote_volume": flow_hold_quote_volume,
        "flow_hold_number_of_trades": flow_hold_trades,
        "flow_hold_taker_buy_quote_volume": flow_hold_taker_quote,
        "flow_hold_threshold_quote_volume": flow_hold_threshold_quote,
        "flow_hold_threshold_number_of_trades": flow_hold_threshold_trades,
        "prior_up_down_whipsaw_to_impulse_range": prior_whipsaw,
        "low": low,
        "high": high,
        "open": open_price,
        "close": close_price,
        "decision_ema20": decision_ema20,
        "initial_stop_at_decision": initial_stop,
        "tp1_at_decision": tp1,
        "tp1_round_step": tp1_round_step,
        "stop_buffer_range_fraction": LIVE2_BACKTEST_STOP_BUFFER_RANGE_FRACTION,
        "tp1_r": LIVE2_BACKTEST_TP1_R,
        "quote_volume": quote_volume,
        "number_of_trades": trade_count,
        "baseline_1m_count": len(baseline),
        "baseline_quote_1m": baseline_quote,
        "baseline_trade_count_1m": baseline_trades,
        "baseline_range_pct_1m": baseline_range_pct,
        "baseline_source": baseline_source,
        "alignment": "rolling_60s_5s_step",
        "calendar_aligned": False,
        "setup_open_ms": setup_open_ms,
        "setup_close_ms": int(segment[-1].close_time_ms),
    }
    if closed_entry_candles < LIVE2_BACKTEST_CONFIRMATION_CANDLES:
        return {**common, "status": "not_ready", "reason": "live_setup_confirmation_candles_below_backtest_min"}
    if quote_ratio < LIVE2_BACKTEST_MIN_QUOTE_RATIO_START or raw_quote_ratio < min_raw_quote_ratio:
        return {**common, "status": "rejected", "reason": "live_setup_quote_ratio_below_backtest_min"}
    if trade_ratio < LIVE2_BACKTEST_MIN_TRADE_RATIO_START or raw_trade_ratio < min_raw_trade_ratio:
        return {**common, "status": "rejected", "reason": "live_setup_trade_ratio_below_backtest_min"}
    if price_retention is None or price_retention < LIVE2_BACKTEST_MIN_PRICE_RETENTION:
        return {**common, "status": "rejected", "reason": "live_setup_price_retention_below_backtest_min"}
    if verticality < LIVE2_BACKTEST_MIN_VERTICALITY_SCORE:
        return {**common, "status": "rejected", "reason": "live_setup_verticality_below_backtest_min"}
    if hold_count < LIVE2_BACKTEST_MIN_HOLD_COUNT:
        return {**common, "status": "rejected", "reason": "live_setup_hold_count_below_backtest_min"}
    return {**common, "status": "ok", "reason": "live_setup_backtest_candidate"}


def _is_live_flow_hold_candle(
    candle: Live2Candle,
    *,
    baseline_quote: float,
    baseline_trades: float,
    baseline_taker_share: float,
) -> bool:
    if candle.live_ws_trade_count <= 0:
        return False
    if candle.quote_volume <= baseline_quote:
        return False
    if float(candle.number_of_trades) <= baseline_trades:
        return False
    if candle.close < candle.open:
        return False
    if baseline_taker_share > 0 and candle.quote_volume > 0:
        return (candle.taker_buy_quote_volume / candle.quote_volume) > baseline_taker_share
    return True




def _rolling_category_rank(category_id: str) -> int | None:
    try:
        return LIVE2_ROLLING_RUNNER_CATEGORY_PRIORITY.index(category_id) + 1
    except ValueError:
        return None


def _rolling_runner_category_setup(*, state: SymbolState, decision_candle: Live2Candle) -> dict[str, object]:
    """Live mirror of rolling HTF -> first C/A/S category trigger.

    All inputs are closed candles already present in SymbolState. The current
    decision candle is the last closed 30s confirmation candle. HTF baseline,
    dormancy, pregrowth, and prior-spike context are built from 1m candles whose
    close_time_ms is <= rolling_htf_open_ms, so no part of the current rolling
    HTF window leaks into context.
    """

    ring_30s = state.candle_book.rings.get(30_000)
    ring_1m = state.candle_book.rings.get(60_000)
    closed_30s = () if ring_30s is None else ring_30s.closed_snapshot()
    closed_1m = () if ring_1m is None else ring_1m.closed_snapshot()
    common: dict[str, object] = {
        "rolling_runner_contract": LIVE2_ROLLING_RUNNER_CONTRACT_ID,
        "rolling_runner_model": "rolling_htf_then_first_category_qualified_30s_confirm",
        "rolling_runner_category_id": "",
        "rolling_runner_matched_categories": (),
        "rolling_runner_dependency_reasons": (),
        "rolling_runner_reject_reasons": (),
        "rolling_runner_decision_timeframe_ms": 30_000,
    }
    if decision_candle.timeframe_ms != 30_000:
        return {**common, "rolling_runner_reject_reasons": ("decision_candle_is_not_30s",)}
    if not closed_30s or closed_30s[-1].open_time_ms != decision_candle.open_time_ms:
        return {**common, "rolling_runner_dependency_reasons": ("latest_closed_30s_decision_candle_not_ready",)}
    if not closed_1m:
        return {**common, "rolling_runner_dependency_reasons": ("closed_1m_baseline_not_ready",)}

    evaluations: list[dict[str, object]] = []
    dependency_reasons: list[str] = []
    reject_reasons: list[str] = []
    for profile in LIVE2_ROLLING_RUNNER_PROFILES:
        result = _evaluate_rolling_profile(
            closed_30s=closed_30s,
            closed_1m=closed_1m,
            decision_candle=decision_candle,
            profile=profile,
        )
        status = str(result.get("status", ""))
        reason = str(result.get("reason", ""))
        if status == "selected":
            evaluations.append(result)
        elif status == "not_ready":
            dependency_reasons.append(f"{profile['tf_set']}:{reason}")
        else:
            reject_reasons.append(f"{profile['tf_set']}:{reason}")

    if not evaluations:
        if dependency_reasons and not reject_reasons:
            return {**common, "rolling_runner_dependency_reasons": tuple(dependency_reasons)}
        return {**common, "rolling_runner_reject_reasons": tuple(reject_reasons or ("rolling_runner_no_profile_selected",))}

    def sort_key(item: dict[str, object]) -> tuple[int, int, int]:
        category_id = str(item.get("rolling_runner_category_id", ""))
        category_rank = _rolling_category_rank(category_id) or 999
        profile_rank = int(item.get("rolling_runner_profile_rank", 999))
        confirm_count = int(item.get("confirmation_candles", 999))
        return category_rank, profile_rank, confirm_count

    selected = sorted(evaluations, key=sort_key)[0]
    return {
        **common,
        **selected,
        "rolling_runner_dependency_reasons": tuple(dependency_reasons),
        "rolling_runner_reject_reasons": tuple(reject_reasons),
    }


def _evaluate_rolling_profile(
    *,
    closed_30s: tuple[Live2Candle, ...],
    closed_1m: tuple[Live2Candle, ...],
    decision_candle: Live2Candle,
    profile: dict[str, object],
) -> dict[str, object]:
    tf_set = str(profile["tf_set"])
    htf_timeframe_ms = int(profile["htf_timeframe_ms"])
    htf_candles = int(profile["htf_candles"])
    min_confirm = int(profile["min_confirm_candles"])
    max_confirm = int(profile["max_confirm_candles"])
    profile_rank = int(profile["profile_rank"])
    profile_common = {
        "rolling_runner_tf_set": tf_set,
        "rolling_runner_htf_timeframe_ms": htf_timeframe_ms,
        "rolling_runner_profile_rank": profile_rank,
    }
    for confirm_count in range(min_confirm, max_confirm + 1):
        confirm = _closed_30s_confirm_segment(closed_30s, decision_candle=decision_candle, confirm_count=confirm_count)
        if confirm is None:
            continue
        if _candles_have_aggtrade_gaps(confirm):
            return {**profile_common, "status": "not_ready", "reason": "confirm_30s_aggtrade_gap"}
        htf = _closed_segment_before(candles=closed_30s, end_open_ms=confirm[0].open_time_ms, count=htf_candles, timeframe_ms=30_000)
        if htf is None:
            continue
        if _candles_have_aggtrade_gaps(htf):
            return {**profile_common, "status": "not_ready", "reason": "rolling_htf_30s_aggtrade_gap"}
        htf_open_ms = int(htf[0].open_time_ms)
        history = _aggregate_1m_history_to_htf(closed_1m=closed_1m, htf_timeframe_ms=htf_timeframe_ms, before_ms=htf_open_ms)
        required_history = max(
            LIVE2_ROLLING_BASELINE_WINDOWS + LIVE2_ROLLING_DORMANCY_WINDOWS + LIVE2_ROLLING_PREGROWTH_WINDOWS,
            math.ceil((LIVE2_ROLLING_PRIOR_SPIKE_LOOKBACK_MS + LIVE2_ROLLING_BASELINE_WINDOWS * htf_timeframe_ms) / htf_timeframe_ms),
        )
        if len(history) < required_history:
            return {**profile_common, "status": "not_ready", "reason": f"rolling_1m_history_not_ready:{len(history)}/{required_history}"}
        baseline = history[-LIVE2_ROLLING_BASELINE_WINDOWS:]
        dormancy = history[-(LIVE2_ROLLING_BASELINE_WINDOWS + LIVE2_ROLLING_DORMANCY_WINDOWS):-LIVE2_ROLLING_BASELINE_WINDOWS]
        pregrowth = history[-(LIVE2_ROLLING_BASELINE_WINDOWS + LIVE2_ROLLING_PREGROWTH_WINDOWS):-LIVE2_ROLLING_BASELINE_WINDOWS]
        htf_candle = _aggregate_candles_to_live2_candle(candles=htf, timeframe_ms=htf_timeframe_ms)
        baseline_quote = _median([item.quote_volume for item in baseline])
        baseline_trades = _median([float(item.number_of_trades) for item in baseline])
        dormancy_trades = _median([float(item.number_of_trades) for item in dormancy])
        if baseline_quote <= 0.0 or baseline_trades <= 0.0 or dormancy_trades <= 0.0:
            return {**profile_common, "status": "not_ready", "reason": "rolling_baseline_or_dormancy_invalid"}
        htf_return = (htf_candle.close / htf_candle.open) - 1.0 if htf_candle.open > 0 else float("nan")
        htf_quote_ratio = htf_candle.quote_volume / baseline_quote
        htf_trade_ratio = float(htf_candle.number_of_trades) / baseline_trades
        if htf_return < LIVE2_ROLLING_SEED_MIN_HTF_RETURN_PCT or htf_quote_ratio < LIVE2_ROLLING_SEED_MIN_HTF_QUOTE_RATIO or htf_trade_ratio < LIVE2_ROLLING_SEED_MIN_HTF_TRADE_RATIO:
            continue
        ltf_features = _rolling_ltf_confirmation_features(confirm, baseline_quote=baseline_quote, baseline_trades=baseline_trades, htf_ms=htf_timeframe_ms)
        if (
            ltf_features["ltf_confirm_return_pct"] < 0.004
            or ltf_features["ltf_quote_pace_ratio"] < 3.0
            or ltf_features["ltf_trade_pace_ratio"] < 3.0
            or ltf_features["ltf_second_half_return_pct"] < 0.0
            or ltf_features["ltf_quote_acceleration"] < 1.0
            or ltf_features["ltf_trade_acceleration"] < 1.0
        ):
            continue
        anomaly_low = min(item.low for item in htf)
        if min(item.low for item in confirm) < anomaly_low:
            continue
        structural_low = min(anomaly_low, min(item.low for item in confirm))
        entry = float(decision_candle.close)
        stop = structural_low * (1.0 - LIVE2_ROLLING_STRUCTURAL_STOP_BUFFER_PCT)
        initial_risk = entry - stop
        initial_risk_pct = initial_risk / entry if entry > 0 else float("nan")
        if not math.isfinite(initial_risk) or initial_risk <= 0.0 or not math.isfinite(initial_risk_pct):
            continue
        if initial_risk_pct > LIVE2_ROLLING_MAX_INITIAL_RISK_PCT:
            continue
        entry_drift = abs((entry - float(decision_candle.close)) / float(decision_candle.close)) if decision_candle.close > 0 else float("nan")
        if math.isfinite(entry_drift) and entry_drift > LIVE2_ROLLING_MAX_ENTRY_DRIFT_PCT:
            continue
        prior_spike = _rolling_prior_spike_features(history=history, current=htf_candle, current_open_ms=htf_open_ms)
        pregrowth_max_single = max(((item.close / item.open) - 1.0 for item in pregrowth if item.open > 0), default=float("nan"))
        features = {
            **profile_common,
            "status": "selected",
            "reason": "rolling_profile_category_selected",
            "signal_entry_price": entry,
            "initial_stop_at_decision": stop,
            "initial_risk_pct_at_decision": initial_risk_pct,
            "tp1_at_decision": entry + LIVE2_ROLLING_TP1_R * initial_risk,
            "rolling_signal_entry_price": entry,
            "rolling_initial_stop_at_decision": stop,
            "rolling_initial_risk_pct_at_decision": initial_risk_pct,
            "rolling_tp1_at_decision": entry + LIVE2_ROLLING_TP1_R * initial_risk,
            "confirmation_candles": confirm_count,
            "decision_available_timestamp_ms": int(decision_candle.close_time_ms),
            "rolling_htf_open_ms": htf_open_ms,
            "rolling_htf_close_ms": int(htf[-1].close_time_ms),
            "htf_quote_ratio": htf_quote_ratio,
            "htf_trade_ratio": htf_trade_ratio,
            "htf_return_pct": htf_return,
            "dormancy_to_anomaly_trade_ratio": float(htf_candle.number_of_trades) / dormancy_trades,
            "pregrowth_max_single_return_pct": pregrowth_max_single,
            **prior_spike,
            **ltf_features,
        }
        matched = _rolling_runner_matches(features)
        if not matched:
            continue
        return {
            **features,
            "rolling_runner_category_id": matched[0],
            "rolling_runner_matched_categories": tuple(matched),
            "rolling_runner_category_priority_rank": _rolling_category_rank(matched[0]),
        }
    return {**profile_common, "status": "rejected", "reason": "rolling_profile_no_category_qualified_confirm"}


def _closed_30s_confirm_segment(
    closed_30s: tuple[Live2Candle, ...],
    *,
    decision_candle: Live2Candle,
    confirm_count: int,
) -> tuple[Live2Candle, ...] | None:
    ordered = tuple(sorted((item for item in closed_30s if item.close_time_ms <= decision_candle.close_time_ms), key=lambda item: item.open_time_ms))
    if len(ordered) < confirm_count or ordered[-1].open_time_ms != decision_candle.open_time_ms:
        return None
    segment = ordered[-confirm_count:]
    expected = tuple(segment[0].open_time_ms + index * 30_000 for index in range(confirm_count))
    if tuple(item.open_time_ms for item in segment) != expected:
        return None
    return segment


def _closed_segment_before(
    *,
    candles: tuple[Live2Candle, ...],
    end_open_ms: int,
    count: int,
    timeframe_ms: int,
) -> tuple[Live2Candle, ...] | None:
    segment = tuple(sorted((item for item in candles if item.open_time_ms < end_open_ms), key=lambda item: item.open_time_ms))[-count:]
    if len(segment) < count:
        return None
    expected = tuple(segment[0].open_time_ms + index * timeframe_ms for index in range(count))
    if tuple(item.open_time_ms for item in segment) != expected:
        return None
    return segment


def _aggregate_1m_history_to_htf(*, closed_1m: tuple[Live2Candle, ...], htf_timeframe_ms: int, before_ms: int) -> tuple[Live2Candle, ...]:
    """Build event-rolling HTF context fully closed before rolling HTF.

    Live must not anchor baseline/dormancy/pregrowth/prior-spike context to
    wall-clock HTF buckets.  It uses the latest closed 1m candle before the
    rolling HTF seed starts, then walks backward in non-overlapping HTF-width
    chunks.  This keeps the context rolling and still guarantees that no 1m
    candle overlapping the current rolling HTF window enters the baseline.
    """

    group_size = htf_timeframe_ms // 60_000
    if group_size <= 0:
        return ()
    ordered = tuple(sorted((item for item in closed_1m if item.close_time_ms <= before_ms), key=lambda item: item.open_time_ms))
    if len(ordered) < group_size:
        return ()
    groups_reversed: list[Live2Candle] = []
    end = len(ordered)
    while end >= group_size:
        chunk = tuple(ordered[end - group_size:end])
        expected = tuple(int(chunk[0].open_time_ms) + offset * 60_000 for offset in range(group_size))
        if tuple(int(item.open_time_ms) for item in chunk) != expected:
            break
        groups_reversed.append(_aggregate_candles_to_live2_candle(candles=chunk, timeframe_ms=htf_timeframe_ms))
        end -= group_size
    return tuple(reversed(groups_reversed))


def _candles_have_aggtrade_gaps(candles: tuple[Live2Candle, ...]) -> bool:
    return any(
        int(getattr(item, "missing_agg_trade_id_count", 0) or 0) > 0
        or int(getattr(item, "agg_trade_id_gap_count", 0) or 0) > 0
        or int(getattr(item, "max_agg_trade_id_gap", 0) or 0) > 0
        for item in candles
    )


def _rolling_ltf_confirmation_features(
    confirm: tuple[Live2Candle, ...],
    *,
    baseline_quote: float,
    baseline_trades: float,
    htf_ms: int,
) -> dict[str, float]:
    duration_ms = max(1, len(confirm) * 30_000)
    quote = sum(item.quote_volume for item in confirm)
    trades = sum(float(item.number_of_trades) for item in confirm)
    expected_quote = baseline_quote * duration_ms / htf_ms
    expected_trades = baseline_trades * duration_ms / htf_ms
    first_open = float(confirm[0].open)
    last_close = float(confirm[-1].close)
    first_half = confirm[: max(1, len(confirm) // 2)]
    second_half = confirm[len(first_half):] or confirm[-1:]
    first_quote = sum(item.quote_volume for item in first_half)
    second_quote = sum(item.quote_volume for item in second_half)
    first_trades = sum(float(item.number_of_trades) for item in first_half)
    second_trades = sum(float(item.number_of_trades) for item in second_half)
    taker_quote = sum(item.taker_buy_quote_volume for item in confirm)
    second_open = float(second_half[0].open)
    return {
        "ltf_confirm_return_pct": (last_close - first_open) / first_open if first_open > 0 else float("nan"),
        "ltf_quote_volume": quote,
        "ltf_number_of_trades": trades,
        "ltf_quote_pace_ratio": quote / expected_quote if expected_quote > 0 else float("nan"),
        "ltf_trade_pace_ratio": trades / expected_trades if expected_trades > 0 else float("nan"),
        "ltf_second_half_return_pct": (float(second_half[-1].close) - second_open) / second_open if second_open > 0 else float("nan"),
        "ltf_quote_acceleration": second_quote / first_quote if first_quote > 0 else float("nan"),
        "ltf_trade_acceleration": second_trades / first_trades if first_trades > 0 else float("nan"),
        "ltf_taker_buy_quote_share": taker_quote / quote if quote > 0 else float("nan"),
    }


def _rolling_prior_spike_features(*, history: tuple[Live2Candle, ...], current: Live2Candle, current_open_ms: int) -> dict[str, float | int]:
    """Prior-spike context with the same availability boundary as discovery.

    Spike detection itself needs a pre-spike baseline.  Therefore the baseline
    for a prior candle is taken from the full pre-current history, not only from
    the last 24h slice.  Only after a candle is classified as a prior spike do
    we apply the 24h lookback filter.
    """

    ordered = tuple(sorted((item for item in history if item.open_time_ms < current_open_ms), key=lambda item: item.open_time_ms))
    lookback_start = int(current_open_ms) - LIVE2_ROLLING_PRIOR_SPIKE_LOOKBACK_MS
    spike_quotes: list[float] = []
    for idx, candle in enumerate(ordered):
        if candle.open_time_ms < lookback_start:
            continue
        if candle.quote_volume <= 0 or candle.open <= 0:
            continue
        prev = ordered[max(0, idx - LIVE2_ROLLING_BASELINE_WINDOWS):idx]
        if len(prev) < min(10, LIVE2_ROLLING_BASELINE_WINDOWS):
            continue
        baseline_quote = _median([item.quote_volume for item in prev])
        baseline_trades = _median([float(item.number_of_trades) for item in prev])
        candle_return = (candle.close / candle.open) - 1.0
        if (
            baseline_quote > 0
            and baseline_trades > 0
            and candle.quote_volume / baseline_quote >= 3.0
            and float(candle.number_of_trades) / baseline_trades >= 3.0
            and candle_return >= 0.0
        ):
            spike_quotes.append(float(candle.quote_volume))
    max_quote = max(spike_quotes) if spike_quotes else float("nan")
    median_quote = _median(spike_quotes) if spike_quotes else float("nan")
    return {
        "prior_spike_count_24h": len(spike_quotes),
        "prior_spike_max_quote": max_quote,
        "prior_spike_median_quote": median_quote,
        "current_vs_prior_spike_max_quote": float(current.quote_volume) / max_quote if max_quote and math.isfinite(max_quote) else float("nan"),
        "current_vs_prior_spike_median_quote": float(current.quote_volume) / median_quote if median_quote and math.isfinite(median_quote) else float("nan"),
    }


def _rolling_runner_matches(row: dict[str, object]) -> list[str]:
    tf_set = str(row.get("rolling_runner_tf_set", ""))
    def f(name: str) -> float:
        value = _float_or_none(row.get(name))
        return float("nan") if value is None or not math.isfinite(value) else float(value)
    htf_trade_ratio = f("htf_trade_ratio")
    htf_quote_ratio = f("htf_quote_ratio")
    ltf_trade_pace_ratio = f("ltf_trade_pace_ratio")
    ltf_quote_pace_ratio = f("ltf_quote_pace_ratio")
    dormancy_trade_ratio = f("dormancy_to_anomaly_trade_ratio")
    current_vs_prior_max = f("current_vs_prior_spike_max_quote")
    second_half_return = f("ltf_second_half_return_pct")
    pregrowth_max_single = f("pregrowth_max_single_return_pct")
    matches: list[str] = []
    if (
        math.isfinite(dormancy_trade_ratio) and dormancy_trade_ratio <= 32.0
        and math.isfinite(current_vs_prior_max) and current_vs_prior_max > 0.45
        and math.isfinite(ltf_quote_pace_ratio) and ltf_quote_pace_ratio <= 52.0
        and math.isfinite(second_half_return) and second_half_return <= 0.0125
        and math.isfinite(pregrowth_max_single) and pregrowth_max_single > 0.005
    ):
        matches.append("C_balanced_flow_acceptance")
    if (
        math.isfinite(htf_trade_ratio) and htf_trade_ratio <= 18.0
        and math.isfinite(current_vs_prior_max) and current_vs_prior_max > 0.6
        and current_vs_prior_max <= 1.5
    ):
        matches.append("A_resonance_prior_spike")
    if (
        tf_set == "5m_30s"
        and math.isfinite(htf_trade_ratio) and htf_trade_ratio >= 11.7
        and math.isfinite(ltf_trade_pace_ratio) and ltf_trade_pace_ratio <= 5.7
        and math.isfinite(htf_quote_ratio) and htf_quote_ratio <= 47.9
    ):
        matches.append("S_7d_5m30_strict")
    return matches

def _dependency(reason: str) -> Live2CategoryEvaluation:
    return Live2CategoryEvaluation(False, reason, "data_dependency_not_ready")


def _reject(reason: str) -> Live2CategoryEvaluation:
    return Live2CategoryEvaluation(False, reason, "rejected")


def _avg(values: list[float]) -> float:
    valid = [float(value) for value in values if value is not None and value >= 0]
    return sum(valid) / len(valid) if valid else 0.0


def _median(values: list[float]) -> float:
    valid = sorted(float(value) for value in values if value is not None and value >= 0)
    if not valid:
        return 0.0
    mid = len(valid) // 2
    if len(valid) % 2:
        return valid[mid]
    return (valid[mid - 1] + valid[mid]) / 2.0


def _prior_up_down_whipsaw_to_impulse_range(
    baseline: tuple[Live2Candle, ...],
    *,
    impulse_range: float,
) -> float | None:
    if not baseline or not math.isfinite(float(impulse_range)) or impulse_range <= 0.0:
        return None
    highs = [float(item.high) for item in baseline]
    lows = [float(item.low) for item in baseline]
    if not highs or not lows:
        return None
    high_pos = max(range(len(highs)), key=lambda idx: highs[idx])
    high_value = highs[high_pos]
    low_before_high = min(lows[: high_pos + 1])
    low_after_high = min(lows[high_pos:])
    prior_up_leg = high_value - low_before_high
    prior_down_leg = high_value - low_after_high
    if prior_up_leg < 0 or prior_down_leg < 0:
        return None
    return min(prior_up_leg, prior_down_leg) / impulse_range


def _ema20(values: list[float]) -> float | None:
    valid = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not valid:
        return None
    alpha = 2.0 / (20.0 + 1.0)
    ema = valid[0]
    for value in valid[1:]:
        ema = alpha * value + (1.0 - alpha) * ema
    return ema


def _nice_market_round_step(*, reference_price: float, movement: float) -> float:
    if not math.isfinite(reference_price) or reference_price <= 0.0:
        return float("nan")
    raw_step = max(abs(float(movement)) * 0.25, abs(float(reference_price)) * 0.0002, 1e-12)
    exponent = math.floor(math.log10(raw_step))
    base = 10.0 ** exponent
    normalized = raw_step / base
    for multiplier in (1.0, 2.0, 5.0, 10.0):
        if normalized <= multiplier:
            return multiplier * base
    return 10.0 * base


def _round_up_tp1_to_market_number(base_tp1_price: float, *, reference_price: float, movement: float) -> tuple[float, float]:
    if not math.isfinite(base_tp1_price) or base_tp1_price <= 0.0:
        return base_tp1_price, float("nan")
    step = _nice_market_round_step(reference_price=reference_price, movement=movement)
    if not math.isfinite(step) or step <= 0.0:
        return base_tp1_price, float("nan")
    rounded = math.ceil((base_tp1_price - step * 1e-9) / step) * step
    tolerance = max(abs(float(base_tp1_price)) * 1e-12, step * 1e-9)
    if rounded <= base_tp1_price + tolerance:
        rounded += step
    decimals = max(0, int(math.ceil(-math.log10(step))) + 2) if step < 1.0 else 8
    return round(float(rounded), min(decimals, 12)), float(step)


def _verticality_score(segment: tuple[Live2Candle, ...]) -> float:
    if not segment:
        return 0.0
    opens = [item.open for item in segment]
    highs = [item.high for item in segment]
    lows = [item.low for item in segment]
    closes = [item.close for item in segment]
    start_price = float(opens[0])
    end_price = float(closes[-1])
    net_move = end_price - start_price
    positive_net_move = max(net_move, 0.0)
    close_path = abs(float(closes[0]) - start_price)
    for previous, current in zip(closes, closes[1:]):
        close_path += abs(float(current) - float(previous))
    path_efficiency = positive_net_move / close_path if close_path > 0 else 0.0
    segment_range = max(highs) - min(lows)
    range_efficiency = positive_net_move / segment_range if segment_range > 0 else 0.0
    running_high = highs[0]
    max_retrace = 0.0
    for high, low in zip(highs, lows):
        running_high = max(running_high, high)
        max_retrace = max(max_retrace, running_high - low)
    max_retrace_fraction = max_retrace / positive_net_move if positive_net_move > 0.0 else 1.0
    retrace_component = 1.0 - min(max(max_retrace_fraction, 0.0), 1.0)
    green_share = sum(1 for open_price, close_price in zip(opens, closes) if close_price >= open_price) / len(segment)
    verticality_score = (
        0.45 * min(max(path_efficiency, 0.0), 1.0)
        + 0.25 * min(max(range_efficiency, 0.0), 1.0)
        + 0.20 * retrace_component
        + 0.10 * green_share
    )
    return 0.0 if net_move <= 0.0 else float(verticality_score)


def _range_pct(candle: Live2Candle) -> float:
    return (candle.high - candle.low) / candle.close if candle.close > 0 else 0.0


def _window_range_pct(candles: tuple[Live2Candle, ...]) -> float | None:
    if not candles:
        return None
    open_price = candles[0].open
    if open_price <= 0:
        return None
    high = max(item.high for item in candles)
    low = min(item.low for item in candles)
    return (high - low) / open_price


def _effective_context_status(
    *,
    status: str,
    last_seen_ms: int | None,
    decision_time_ms: int,
    stale_ms: int | None,
) -> str:
    if status != "ok" or stale_ms is None or last_seen_ms is None:
        return status
    if int(decision_time_ms) - int(last_seen_ms) > int(stale_ms):
        return "stale"
    return status


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
