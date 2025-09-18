from __future__ import annotations

from dataclasses import asdict, dataclass
from math import fabs, isnan
from typing import Iterable, List, Sequence

from ..enums import BreakDirection, Side
from ..models.entities import Candle, Signal, Thresholds
from .metrics_service import Metrics, MetricsService
from ...utils import ids
from ...utils.clock import utcnow


@dataclass(slots=True)
class SelectionResult:
    signals: List[Signal]
    rejected: List[str]


class SignalSelectorService:
    def __init__(self, metrics_service: MetricsService) -> None:
        self._metrics_service = metrics_service

    def _evaluate_long_rules(
        self, metrics: Metrics, thresholds: Thresholds, reasons: List[str]
    ) -> bool:
        if not thresholds.allow_long:
            reasons.append("long_disabled")
            return False
        if metrics.pct_move <= 0:
            reasons.append("long_negative_body")
            return False
        if thresholds.s > 0 and metrics.pct_move < thresholds.s:
            reasons.append("long_pct_move")
            return False
        if thresholds.t > 0 and metrics.relative_volume < thresholds.t:
            reasons.append("long_relative_volume")
            return False
        if thresholds.u > 0 and metrics.atr_multiple < thresholds.u:
            reasons.append("long_atr_mult")
            return False
        if thresholds.v > 0 and metrics.upper_wick_pct > thresholds.v:
            reasons.append("long_upper_wick")
            return False
        if thresholds.w > 0 and metrics.lower_wick_pct > thresholds.w:
            reasons.append("long_lower_wick")
            return False
        if thresholds.x > 0 and not isnan(metrics.pct_to_high) and metrics.pct_to_high < thresholds.x:
            reasons.append("long_pct_to_high")
            return False
        if thresholds.y > 0 and not isnan(metrics.pct_to_low) and fabs(metrics.pct_to_low) > thresholds.y:
            reasons.append("long_pct_to_low")
            return False
        return True

    def _evaluate_short_rules(
        self, metrics: Metrics, thresholds: Thresholds, reasons: List[str]
    ) -> bool:
        if not thresholds.allow_short:
            reasons.append("short_disabled")
            return False
        if metrics.pct_move >= 0:
            reasons.append("short_positive_body")
            return False
        if thresholds.s > 0 and fabs(metrics.pct_move) < thresholds.s:
            reasons.append("short_pct_move")
            return False
        if thresholds.t > 0 and metrics.relative_volume < thresholds.t:
            reasons.append("short_relative_volume")
            return False
        if thresholds.u > 0 and metrics.atr_multiple < thresholds.u:
            reasons.append("short_atr_mult")
            return False
        if thresholds.v > 0 and metrics.lower_wick_pct > thresholds.v:
            reasons.append("short_lower_wick")
            return False
        if thresholds.w > 0 and metrics.upper_wick_pct > thresholds.w:
            reasons.append("short_upper_wick")
            return False
        if thresholds.x > 0 and not isnan(metrics.pct_to_low) and fabs(metrics.pct_to_low) < thresholds.x:
            reasons.append("short_pct_to_low")
            return False
        if thresholds.y > 0 and not isnan(metrics.pct_to_high) and metrics.pct_to_high > thresholds.y:
            reasons.append("short_pct_to_high")
            return False
        return True

    def select(
        self,
        symbol: str,
        candles: Sequence[Candle],
        thresholds: Thresholds,
        future_candles: Sequence[Candle] | None = None,
    ) -> SelectionResult:
        metrics = self._metrics_service.calculate(candles, future_candles)
        evaluations = self._metrics_service.evaluate_thresholds(metrics, thresholds)
        failed = [evaluation.name for evaluation in evaluations if not evaluation.passed]
        if failed:
            return SelectionResult(signals=[], rejected=failed)
        entry_failures: List[str] = []
        allow_long = self._evaluate_long_rules(metrics, thresholds, entry_failures)
        allow_short = self._evaluate_short_rules(metrics, thresholds, entry_failures)
        if not allow_long and not allow_short:
            return SelectionResult(signals=[], rejected=failed + entry_failures)
        side: Side | None = None
        if metrics.pct_move > 0 and allow_long:
            side = Side.LONG
        elif metrics.pct_move < 0 and allow_short:
            side = Side.SHORT
        elif allow_long:
            side = Side.LONG
        elif allow_short:
            side = Side.SHORT
        if side is None:
            return SelectionResult(signals=[], rejected=failed + entry_failures + ["no_side"])
        direction = metrics.break_direction
        if direction is BreakDirection.NONE:
            direction = BreakDirection.HIGH_FIRST if side == Side.LONG else BreakDirection.LOW_FIRST
        candle = candles[-1]
        timestamp = utcnow()
        signal = Signal(
            id=ids.uuid_str(),
            candle_id=candle.id,
            candle=candle,
            timeframe=candle.timeframe,
            side=side,
            direction=direction,
            score=fabs(metrics.pct_move),
            triggered_at=candle.closed_at,
            created_at=timestamp,
            updated_at=timestamp,
            thresholds=thresholds,
            metrics=evaluations,
            allow_long=allow_long,
            allow_short=allow_short,
            metadata={
                "metrics": metrics.as_dict(),
                "evaluations": [asdict(evaluation) for evaluation in evaluations],
                "symbol": symbol,
            },
        )
        return SelectionResult(signals=[signal], rejected=[])

    def batch_select(
        self, batch: Iterable[tuple[str, Sequence[Candle], Thresholds]]
    ) -> List[Signal]:
        signals: List[Signal] = []
        for symbol, candles, thresholds in batch:
            result = self.select(symbol, candles, thresholds)
            signals.extend(result.signals)
        return signals
