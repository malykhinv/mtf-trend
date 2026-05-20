"""Pure stream-only signal adapter for anomaly live2.

The adapter is deliberately hot-path safe: it evaluates already-built in-memory
aggTrade candles and SymbolState fields only. It performs no REST/cache/file IO
and does not guess unavailable derivative context.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from research_tools.anomaly_category_contract import (
    CATEGORY_CONTRACT_ID,
    DEFAULT_PUMP_CATEGORY_IDS,
    SUPPORTED_PUMP_CATEGORIES,
    PumpCategoryContract,
)

from .market_data.candles import Live2Candle
from .state import SymbolState


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


class Live2SignalEngine:
    """Small stream-only adapter for the shared pump-category contract.

    Generation 0 has no prior-fast-fade context. Mark basis comes from the
    live2 markPrice stream and OI delta comes from the live2 active-symbol OI
    poller; unavailable required fields are rejected explicitly instead of
    silently substituting zeros.
    """

    def __init__(self, *, category_ids: tuple[str, ...] = DEFAULT_PUMP_CATEGORY_IDS) -> None:
        self.category_ids = tuple(category_ids)
        self._total_evaluations = 0
        self._total_selected = 0
        self._total_rejected = 0

    def evaluate(self, *, state: SymbolState, candle: Live2Candle, actionable_reason: str) -> Live2SignalDecision:
        self._total_evaluations += 1
        features = self._features(state=state, candle=candle, actionable_reason=actionable_reason)
        if candle.open <= 0 or candle.close <= 0:
            return self._reject("invalid_stream_candle_price", features=features)
        if candle.close <= candle.open:
            return self._reject("stream_candle_is_not_upward_price_confirmation", features=features)
        if candle.quote_volume <= 0 or candle.number_of_trades <= 0:
            return self._reject("stream_candle_has_no_real_flow", features=features)

        reject_reasons: list[str] = []
        for category_id in self.category_ids:
            category = SUPPORTED_PUMP_CATEGORIES.get(category_id)
            if category is None:
                reject_reasons.append(f"{category_id}:unsupported_category")
                continue
            accepted, reason = self._category_accepts(category=category, features=features)
            if not accepted:
                reject_reasons.append(f"{category.category_id}:{reason}")
                continue
            stop = float(features["initial_stop_at_decision"])
            entry = float(features["signal_entry_price"])
            tp1 = float(features["tp1_at_decision"])
            initial_risk_pct = (entry / stop) - 1.0 if stop > 0 else 0.0
            self._total_selected += 1
            return Live2SignalDecision(
                verdict="selected",
                reason=f"{actionable_reason}; stream_signal_category_selected",
                category_id=category.category_id,
                category_rank=category.priority,
                signal_entry_price=entry,
                initial_stop_at_decision=stop,
                initial_risk_pct_at_decision=initial_risk_pct,
                tp1_at_decision=tp1,
                features={
                    **features,
                    "category_contract": CATEGORY_CONTRACT_ID,
                    "category_label": category.label,
                },
            )
        return self._reject("; ".join(reject_reasons) or "no_category_accepted", features=features)

    def status(self) -> dict[str, object]:
        return {
            "status": "stream_signal_adapter_active",
            "category_contract": CATEGORY_CONTRACT_ID,
            "category_ids": self.category_ids,
            "total_evaluations": self._total_evaluations,
            "total_selected": self._total_selected,
            "total_rejected": self._total_rejected,
            "limitations": (
                "generation_0_has_no_prior_fast_fade_context; "
                "mark_context_from_live_markPrice_ws; "
                "oi_context_from_active_symbol_open_interest_poller; "
                "categories_requiring_unavailable_context_are_rejected"
            ),
        }

    def _reject(self, reason: str, *, features: dict[str, object]) -> Live2SignalDecision:
        self._total_rejected += 1
        return Live2SignalDecision(
            verdict="rejected_signal_contract",
            reason=reason,
            features={**features, "category_contract": CATEGORY_CONTRACT_ID},
        )

    def _features(self, *, state: SymbolState, candle: Live2Candle, actionable_reason: str) -> dict[str, object]:
        closed_5s = state.candle_book.rings.get(5_000).closed_snapshot() if 5_000 in state.candle_book.rings else ()
        previous = tuple(item for item in closed_5s if item.open_time_ms < candle.open_time_ms)[-24:]
        baseline_quote = _avg([item.quote_volume for item in previous])
        baseline_trades = _avg([float(item.number_of_trades) for item in previous])
        baseline_range = _avg([_range_pct(item) for item in previous])
        candle_range = _range_pct(candle)
        return_pct = (candle.close / candle.open) - 1.0 if candle.open > 0 else 0.0
        quote_ratio = candle.quote_volume / baseline_quote if baseline_quote > 0 else None
        trade_ratio = float(candle.number_of_trades) / baseline_trades if baseline_trades > 0 else None
        range_ratio = candle_range / baseline_range if baseline_range > 0 else None
        taker_share = candle.taker_buy_quote_volume / candle.quote_volume if candle.quote_volume > 0 else None
        mark_basis = None
        mark_basis_status = "not_available"
        if state.mark_status == "ok" and state.mark_price is not None and state.mark_price > 0 and candle.close > 0:
            mark_basis = (state.mark_price - candle.close) / candle.close
            mark_basis_status = "ok"
        elif state.mark_status:
            mark_basis_status = state.mark_status
        # Generation 0 uses the current 5s bucket low as a strict stream-local
        # initial stop candidate. Execution remains disabled until P318; this is
        # only the signal-side risk candidate consumed by P317 entry guards.
        stop = candle.low
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
            "range_pct": candle_range,
            "baseline_quote_5s": baseline_quote,
            "baseline_trade_count_5s": baseline_trades,
            "baseline_range_pct_5s": baseline_range,
            "start_quote_ratio": quote_ratio,
            "start_trade_ratio": trade_ratio,
            "start_range_pct_ratio_to_baseline": range_ratio,
            "start_taker_buy_quote_share": taker_share,
            "quote_volume": candle.quote_volume,
            "number_of_trades": candle.number_of_trades,
            "prior_closed_5s_count": len(previous),
            "ticker_last_price": state.ticker_last_price,
            "aggtrade_last_price": state.aggtrade_last_price,
            "mark_price": state.mark_price,
            "mark_index_price": state.mark_index_price,
            "mark_funding_rate": state.mark_funding_rate,
            "mark_last_seen_ms": state.mark_last_seen_ms,
            "mark_status": state.mark_status,
            "mark_basis_status": mark_basis_status,
            "mark_close_vs_decision_close_basis": mark_basis,
            "oi_open_interest": state.oi_open_interest,
            "oi_previous_open_interest": state.oi_previous_open_interest,
            "oi_change_pct_3x5m": state.oi_change_pct_3x5m,
            "oi_latest_timestamp_ms": state.oi_latest_timestamp_ms,
            "oi_previous_timestamp_ms": state.oi_previous_timestamp_ms,
            "oi_last_seen_ms": state.oi_last_seen_ms,
            "oi_source": state.oi_source,
            "oi_status": state.oi_status,
            "oi_reason": state.oi_reason,
        }

    def _category_accepts(self, *, category: PumpCategoryContract, features: dict[str, object]) -> tuple[bool, str]:
        if category.min_oi_change_pct_3x5m is not None:
            oi_change = _float_or_none(features.get("oi_change_pct_3x5m"))
            if features.get("oi_status") != "ok" or oi_change is None:
                return False, "oi_context_not_ready"
            if oi_change < category.min_oi_change_pct_3x5m:
                return False, "oi_change_3x5m_below_category_min"
        if category.min_mark_close_vs_decision_close_basis is not None:
            mark_basis = _float_or_none(features.get("mark_close_vs_decision_close_basis"))
            if mark_basis is None:
                return False, "mark_price_context_not_ready"
            if mark_basis < category.min_mark_close_vs_decision_close_basis:
                return False, "mark_basis_below_category_min"
        baseline_quote = _float_or_none(features.get("baseline_quote_5s"))
        if category.min_baseline_quote_daily_proxy is not None and (baseline_quote is None or baseline_quote <= 0):
            return False, "stream_baseline_quote_not_ready"
        quote_ratio = _float_or_none(features.get("start_quote_ratio"))
        if category.max_start_quote_ratio is not None and quote_ratio is not None and quote_ratio > category.max_start_quote_ratio:
            return False, "start_quote_ratio_above_category_max"
        trade_ratio = _float_or_none(features.get("start_trade_ratio"))
        if category.max_start_trade_ratio is not None and trade_ratio is not None and trade_ratio > category.max_start_trade_ratio:
            return False, "start_trade_ratio_above_category_max"
        range_ratio = _float_or_none(features.get("start_range_pct_ratio_to_baseline"))
        if category.min_start_range_pct_ratio_to_baseline is not None and (
            range_ratio is None or range_ratio < category.min_start_range_pct_ratio_to_baseline
        ):
            return False, "start_range_ratio_below_category_min"
        if category.max_start_range_pct_ratio_to_baseline is not None and range_ratio is not None and range_ratio > category.max_start_range_pct_ratio_to_baseline:
            return False, "start_range_ratio_above_category_max"
        initial_risk_pct = _float_or_none(features.get("initial_risk_pct_at_decision"))
        if category.min_initial_risk_pct is not None and (initial_risk_pct is None or initial_risk_pct < category.min_initial_risk_pct):
            return False, "initial_risk_pct_below_category_min"
        if category.max_initial_risk_pct is not None and initial_risk_pct is not None and initial_risk_pct > category.max_initial_risk_pct:
            return False, "initial_risk_pct_above_category_max"
        return True, "accepted"


def _avg(values: list[float]) -> float:
    valid = [float(value) for value in values if value is not None and value >= 0]
    return sum(valid) / len(valid) if valid else 0.0


def _range_pct(candle: Live2Candle) -> float:
    return (candle.high / candle.low) - 1.0 if candle.low > 0 else 0.0


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
