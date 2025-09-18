from __future__ import annotations

from dataclasses import asdict, dataclass
from math import fabs
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
        if (
            thresholds.min_pct_move != 0
            and metrics.pct_move <= thresholds.min_pct_move
        ):
            reasons.append("long_pct_move")
            return False
        if (
            thresholds.max_pct_move != 0
            and metrics.pct_move >= thresholds.max_pct_move
        ):
            reasons.append("long_pct_move")
            return False
        if (
            thresholds.min_relative_volume > 0
            and metrics.relative_volume <= thresholds.min_relative_volume
        ):
            reasons.append("long_relative_volume")
            return False
        if (
            thresholds.max_relative_volume > 0
            and metrics.relative_volume >= thresholds.max_relative_volume
        ):
            reasons.append("long_relative_volume")
            return False
        if (
            thresholds.min_atr_mult > 0
            and metrics.atr_multiple <= thresholds.min_atr_mult
        ):
            reasons.append("long_atr_mult")
            return False
        if (
            thresholds.max_upper_wick_pct > 0
            and metrics.upper_wick_pct >= thresholds.max_upper_wick_pct
        ):
            reasons.append("long_upper_wick")
            return False
        if (
            thresholds.max_lower_wick_pct > 0
            and metrics.lower_wick_pct >= thresholds.max_lower_wick_pct
        ):
            reasons.append("long_lower_wick")
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
        min_rel_vol = thresholds.min_relative_volume
        max_rel_vol = thresholds.max_relative_volume
        rel_volume_valid = True
        if min_rel_vol > 0 and max_rel_vol > 0:
            rel_volume_valid = (
                metrics.relative_volume < min_rel_vol
                or metrics.relative_volume > max_rel_vol
            )
        elif min_rel_vol > 0:
            rel_volume_valid = metrics.relative_volume < min_rel_vol
        elif max_rel_vol > 0:
            rel_volume_valid = metrics.relative_volume > max_rel_vol
        if not rel_volume_valid:
            reasons.append("short_relative_volume")
            return False
        if (
            thresholds.min_atr_mult > 0
            and metrics.atr_multiple >= thresholds.min_atr_mult
        ):
            reasons.append("short_atr_mult")
            return False
        pct_move = metrics.pct_move
        min_pct_move = thresholds.min_pct_move
        max_pct_move = thresholds.max_pct_move
        pct_move_valid = True
        conditions = []
        if max_pct_move != 0:
            conditions.append(pct_move > max_pct_move)
        if min_pct_move != 0:
            conditions.append(pct_move < min_pct_move)
        if conditions:
            pct_move_valid = any(conditions)
        if not pct_move_valid:
            reasons.append("short_pct_move")
            return False
        if (
            thresholds.max_upper_wick_pct > 0
            and metrics.upper_wick_pct >= thresholds.max_upper_wick_pct
        ):
            reasons.append("short_upper_wick")
            return False
        if (
            thresholds.max_lower_wick_pct > 0
            and metrics.lower_wick_pct >= thresholds.max_lower_wick_pct
        ):
            reasons.append("short_lower_wick")
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
