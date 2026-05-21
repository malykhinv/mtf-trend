"""Pure stream-only signal adapter for anomaly live2.

The adapter is deliberately hot-path safe: it evaluates already-built in-memory
aggTrade candles and SymbolState fields only. It performs no REST/cache/file IO
and does not guess unavailable derivative context.
"""

from __future__ import annotations

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
LIVE2_BACKTEST_ENTRY_TIMEFRAME_MS = 5_000
LIVE2_BACKTEST_BASELINE_CANDLES = 60
LIVE2_BACKTEST_CONFIRMATION_CANDLES = 4
LIVE2_BACKTEST_MIN_QUOTE_RATIO_START = 4.0
LIVE2_BACKTEST_MIN_TRADE_RATIO_START = 4.0
LIVE2_BACKTEST_MIN_PRICE_RETENTION = 0.70
LIVE2_BACKTEST_MIN_VERTICALITY_SCORE = 0.25
LIVE2_BACKTEST_MIN_HOLD_COUNT = 2
LIVE2_BACKTEST_MAX_INITIAL_RISK_PCT = 0.16


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
        category_ids: tuple[str, ...] = DEFAULT_PUMP_CATEGORY_IDS,
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

        dependency_reasons: list[str] = []
        reject_reasons: list[str] = []
        for category_id in self.category_ids:
            category = SUPPORTED_PUMP_CATEGORIES.get(category_id)
            if category is None:
                reject_reasons.append(f"{category_id}:unsupported_category")
                continue
            evaluation = self._category_accepts(category=category, features=features)
            if not evaluation.accepted:
                item = f"{category.category_id}:{evaluation.reason}"
                if evaluation.kind == "data_dependency_not_ready":
                    dependency_reasons.append(item)
                else:
                    reject_reasons.append(item)
                continue
            stop = float(features["initial_stop_at_decision"])
            entry = float(features["signal_entry_price"])
            tp1 = float(features["tp1_at_decision"])
            initial_risk_pct = (entry / stop) - 1.0 if stop > 0 else 0.0
            self._total_selected += 1
            self._selected_by_category[category.category_id] += 1
            self._last_dependency_reasons = tuple(dependency_reasons)
            self._last_reject_reasons = tuple(reject_reasons)
            return Live2SignalDecision(
                verdict="selected",
                reason=f"{actionable_reason}; stream_signal_category_selected",
                category_id=category.category_id,
                category_rank=category.priority,
                signal_entry_price=entry,
                initial_stop_at_decision=stop,
                initial_risk_pct_at_decision=initial_risk_pct,
                tp1_at_decision=tp1,
                dependency_reasons=tuple(dependency_reasons),
                reject_reasons=tuple(reject_reasons),
                features={
                    **features,
                    "category_contract": CATEGORY_CONTRACT_ID,
                    "category_label": category.label,
                    "signal_dependency_reasons": tuple(dependency_reasons),
                    "signal_reject_reasons": tuple(reject_reasons),
                },
            )
        if dependency_reasons:
            return self._data_dependency_not_ready(
                dependency_reasons=tuple(dependency_reasons),
                reject_reasons=tuple(reject_reasons),
                features=features,
            )
        return self._reject(
            "; ".join(reject_reasons) or "no_category_accepted",
            features={
                **features,
                "signal_reject_reasons": tuple(reject_reasons),
                "signal_dependency_reasons": (),
            },
            reject_reasons=tuple(reject_reasons),
        )

    def status(self) -> dict[str, object]:
        return {
            "status": "stream_signal_adapter_active",
            "category_contract": CATEGORY_CONTRACT_ID,
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
                "prior_context_from_live2_24h_closed_5m_ohlcv_poller; "
                "legacy_category_72h_names_use_24h_live_context; "
                "mark_context_from_live_markPrice_ws; "
                "oi_context_from_active_symbol_open_interest_poller; "
                "flow_hold_from_trailing_closed_live_5s_pre_entry_no_lookahead; "
                "missing_required_category_dependencies_return_data_dependency_not_ready"
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
                "category_contract": CATEGORY_CONTRACT_ID,
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
                "category_contract": CATEGORY_CONTRACT_ID,
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
        abs_return_pct = abs(return_pct)
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
        if quote_ratio is not None and abs_return_pct > 0:
            quote_ratio_per_abs_return = quote_ratio / abs_return_pct
        trade_ratio_per_abs_return = None
        if trade_ratio is not None and abs_return_pct > 0:
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
        prior_context_status = _effective_context_status(
            status=state.prior_context_status,
            last_seen_ms=state.prior_context_last_seen_ms,
            decision_time_ms=candle.close_time_ms,
            stale_ms=self.prior_context_stale_ms,
        )
        prior_whipsaw = None
        if (
            prior_context_status == "ok"
            and state.prior_high_24h is not None
            and state.prior_low_before_high_24h is not None
            and state.prior_low_after_high_24h is not None
            and decision_box_range > 0
        ):
            prior_up_leg = state.prior_high_24h - state.prior_low_before_high_24h
            prior_down_leg = state.prior_high_24h - state.prior_low_after_high_24h
            if prior_up_leg >= 0 and prior_down_leg >= 0:
                prior_whipsaw = min(prior_up_leg, prior_down_leg) / decision_box_range
        stop = decision_box_low
        entry = candle.close
        risk_fraction = (entry / stop) - 1.0 if stop > 0 else 0.0
        tp1 = entry + (entry - stop)
        return {
            "actionable_reason": actionable_reason,
            "signal_entry_price": entry,
            "initial_stop_at_decision": stop,
            "tp1_at_decision": tp1,
            "initial_risk_pct_at_decision": risk_fraction,
            "return_pct": return_pct,
            "abs_return_pct": abs_return_pct,
            "range_pct": candle_range,
            "baseline_quote_5s": baseline_quote,
            "baseline_quote_daily_proxy": baseline_quote_daily_proxy,
            "live_setup_status": live_setup["status"],
            "live_setup_reason": live_setup["reason"],
            "live_setup_timeframe": "1m",
            "live_setup_entry_timeframe": "5s",
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
            "live_setup_baseline_1m_count": live_setup.get("baseline_1m_count"),
            "live_setup_baseline_quote_1m": live_setup.get("baseline_quote_1m"),
            "live_setup_baseline_trade_count_1m": live_setup.get("baseline_trade_count_1m"),
            "live_setup_baseline_range_pct_1m": live_setup.get("baseline_range_pct_1m"),
            "live_setup_baseline_source": live_setup.get("baseline_source"),
            "baseline_trade_count_5s": baseline_trades,
            "baseline_range_pct_5s": baseline_range,
            "baseline_taker_buy_quote_share_5s": baseline_taker_share,
            "start_quote_ratio": quote_ratio,
            "start_trade_ratio": trade_ratio,
            "start_range_pct_ratio_to_baseline": range_ratio,
            "start_quote_ratio_per_abs_return": quote_ratio_per_abs_return,
            "start_trade_ratio_per_abs_return": trade_ratio_per_abs_return,
            "start_taker_buy_quote_share": taker_share,
            "start_taker_buy_quote_share_delta": taker_share_delta,
            "flow_hold_count": flow_hold.count,
            "flow_hold_status": flow_hold.status,
            "flow_hold_reason": flow_hold.reason,
            "flow_hold_definition": "trailing_closed_live_5s_pre_entry_no_lookahead",
            "flow_hold_window_ms": flow_hold.window_ms,
            "flow_hold_quote_volume": flow_hold.quote_volume,
            "flow_hold_number_of_trades": flow_hold.number_of_trades,
            "flow_hold_taker_buy_quote_volume": flow_hold.taker_buy_quote_volume,
            "flow_hold_taker_buy_quote_share_mean": flow_hold.taker_buy_quote_share_mean,
            "flow_hold_taker_buy_quote_share_last": flow_hold.taker_buy_quote_share_last,
            "flow_hold_taker_buy_quote_share_delta": flow_hold.taker_buy_quote_share_delta,
            "live_confirmed_taker_buy_quote_share": flow_hold.taker_buy_quote_share_last,
            "live_confirmed_taker_buy_quote_share_delta": flow_hold.taker_buy_quote_share_delta,
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
        if features.get("live_setup_status") != "ok":
            reason = str(features.get("live_setup_reason") or "live_setup_not_backtest_candidate")
            if features.get("live_setup_status") == "not_ready":
                return _dependency(reason)
            return _reject(reason)
        if (
            category.max_prior_spike_count_72h is not None
            or category.max_prior_fast_fade_count_72h is not None
            or category.max_prior_up_down_whipsaw_to_impulse_range is not None
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
            if oi_change < category.min_oi_change_pct_3x5m:
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
                return _dependency("start_quote_ratio_per_abs_return_not_ready")
            if quote_ratio_per_abs_return > category.max_start_quote_ratio_per_abs_return:
                return _reject("start_quote_ratio_per_abs_return_above_category_max")
        trade_ratio_per_abs_return = _float_or_none(features.get("start_trade_ratio_per_abs_return"))
        if category.max_start_trade_ratio_per_abs_return is not None:
            if trade_ratio_per_abs_return is None:
                return _dependency("start_trade_ratio_per_abs_return_not_ready")
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
                return _dependency("start_taker_buy_quote_share_delta_not_ready")
            if taker_share_delta > category.max_start_taker_buy_quote_share_delta:
                return _reject("start_taker_buy_quote_share_delta_above_category_max")
        live_confirmed_taker_share = _float_or_none(features.get("live_confirmed_taker_buy_quote_share"))
        if category.min_next_taker_buy_quote_share is not None:
            if features.get("flow_hold_status") != "ok" or live_confirmed_taker_share is None:
                return _dependency("live_confirmed_taker_buy_quote_share_not_ready")
            if live_confirmed_taker_share < category.min_next_taker_buy_quote_share:
                return _reject("live_confirmed_taker_buy_quote_share_below_category_min")
        flow_hold_count = _int_or_none(features.get("flow_hold_count"))
        if category.min_flow_hold_count is not None:
            if features.get("flow_hold_status") != "ok" or flow_hold_count is None:
                return _dependency("flow_hold_count_not_ready")
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


def _live_backtest_like_setup(
    *,
    closed_5s: tuple[Live2Candle, ...],
    closed_1m: tuple[Live2Candle, ...],
    decision_candle: Live2Candle,
) -> dict[str, object]:
    setup_open_ms = (int(decision_candle.open_time_ms) // LIVE2_BACKTEST_SETUP_TIMEFRAME_MS) * LIVE2_BACKTEST_SETUP_TIMEFRAME_MS
    segment = tuple(
        item
        for item in closed_5s
        if setup_open_ms <= int(item.open_time_ms) <= int(decision_candle.open_time_ms)
    )
    if not segment or segment[-1].open_time_ms != decision_candle.open_time_ms:
        return {
            "status": "not_ready",
            "reason": "live_setup_entry_segment_not_ready",
            "closed_entry_candles": len(segment),
            "baseline_1m_count": 0,
        }
    baseline = tuple(
        item
        for item in closed_1m
        if int(item.open_time_ms) < setup_open_ms
    )[-LIVE2_BACKTEST_BASELINE_CANDLES:]
    baseline_source = "closed_live_1m"
    baseline_quote = _median([item.quote_volume for item in baseline])
    baseline_trades = _median([float(item.number_of_trades) for item in baseline])
    baseline_range_pct = _median([_range_pct(item) for item in baseline])
    if len(baseline) < LIVE2_BACKTEST_BASELINE_CANDLES:
        previous_5s = tuple(item for item in closed_5s if int(item.open_time_ms) < setup_open_ms)[-24:]
        fallback_quote = _avg([item.quote_volume for item in previous_5s])
        fallback_trades = _avg([float(item.number_of_trades) for item in previous_5s])
        fallback_range_pct = _avg([_range_pct(item) for item in previous_5s])
        if fallback_quote > 0 and fallback_trades > 0 and fallback_range_pct > 0:
            baseline_source = "rolling_5s_scaled_to_1m_until_60_closed_1m_ready"
            baseline_quote = fallback_quote * (LIVE2_BACKTEST_SETUP_TIMEFRAME_MS / LIVE2_BACKTEST_ENTRY_TIMEFRAME_MS)
            baseline_trades = fallback_trades * (LIVE2_BACKTEST_SETUP_TIMEFRAME_MS / LIVE2_BACKTEST_ENTRY_TIMEFRAME_MS)
            baseline_range_pct = fallback_range_pct
        else:
            return {
                "status": "not_ready",
                "reason": "live_setup_1m_baseline_not_ready",
                "closed_entry_candles": len(segment),
                "baseline_1m_count": len(baseline),
                "baseline_source": baseline_source,
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
        }
    closed_entry_candles = len(segment)
    setup_elapsed_fraction = min(
        1.0,
        (closed_entry_candles * LIVE2_BACKTEST_ENTRY_TIMEFRAME_MS) / LIVE2_BACKTEST_SETUP_TIMEFRAME_MS,
    )
    elapsed_for_ratio = max(1e-9, setup_elapsed_fraction)
    quote_volume = sum(item.quote_volume for item in segment)
    trade_count = sum(float(item.number_of_trades) for item in segment)
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
    activation_price = open_price + max(0.0, close_price - open_price) * 0.50
    hold_count = sum(1 for item in segment if item.close >= activation_price)
    price_retention = (close_price - open_price) / (high - open_price) if high > open_price else None
    verticality = _verticality_score(segment)
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
        "range_pct_ratio_to_baseline": range_pct_ratio,
        "activation_price": activation_price,
        "hold_count": hold_count,
        "price_retention": price_retention,
        "verticality_score": verticality,
        "low": low,
        "high": high,
        "open": open_price,
        "close": close_price,
        "quote_volume": quote_volume,
        "number_of_trades": trade_count,
        "baseline_1m_count": len(baseline),
        "baseline_quote_1m": baseline_quote,
        "baseline_trade_count_1m": baseline_trades,
        "baseline_range_pct_1m": baseline_range_pct,
        "baseline_source": baseline_source,
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
    return (candle.high / candle.low) - 1.0 if candle.low > 0 else 0.0


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
