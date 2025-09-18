from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, List, Sequence

from ..enums import BreakDirection, Side
from ..models.entities import Candle, Signal, Thresholds
from .metrics_service import MetricsService
from ...utils import ids
from ...utils.clock import utcnow


@dataclass(slots=True)
class SelectionResult:
    signals: List[Signal]
    rejected: List[str]


class SignalSelectorService:
    def __init__(self, metrics_service: MetricsService) -> None:
        self._metrics_service = metrics_service

    def _determine_side(self, momentum: float) -> tuple[Side, BreakDirection]:
        if momentum > 0:
            return Side.LONG, BreakDirection.HIGH_FIRST
        if momentum < 0:
            return Side.SHORT, BreakDirection.LOW_FIRST
        return Side.LONG, BreakDirection.NONE

    def select(self, symbol: str, candles: Sequence[Candle], thresholds: Thresholds) -> SelectionResult:
        metrics = self._metrics_service.calculate(candles)
        evaluations = self._metrics_service.evaluate_thresholds(metrics, thresholds)
        failed = [evaluation.name for evaluation in evaluations if not evaluation.passed]
        if failed:
            return SelectionResult(signals=[], rejected=failed)
        side, direction = self._determine_side(metrics.momentum)
        if (side == Side.LONG and not thresholds.allow_long) or (
            side == Side.SHORT and not thresholds.allow_short
        ):
            return SelectionResult(signals=[], rejected=["side_not_allowed"])
        signal = Signal(
            id=ids.uuid_str(),
            candle=candles[-1],
            side=side,
            direction=direction,
            score=abs(metrics.momentum),
            triggered_at=utcnow(),
            thresholds=thresholds,
            metrics=evaluations,
            allow_long=thresholds.allow_long,
            allow_short=thresholds.allow_short,
            metadata={
                "metrics": metrics.__dict__,
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
