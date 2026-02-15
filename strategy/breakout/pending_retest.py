"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

from strategy.breakout.pending_breakout import PendingBreakout


@dataclass(slots=True)
class PendingRetest:
    breakout: PendingBreakout
    retest_idx: int
    retest_start_idx: int
    retest_end_idx: int | None
    retest_low: float
    retest_high: float
    confirmation_end_idx: int
    volume_before: float
    volume_after: float
    volume_threshold: float
    volume_filter_passed: bool
