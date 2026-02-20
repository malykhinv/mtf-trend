"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProfitableVariant:
    rank: int
    lookback: int
    volume_mult: float
    retest_window: int
    retest_zone: float
    min_rr: float
    sl_mode: str
    tp2_mult: float
    profit_factor: float
    pnl_percent: float
    win_rate: float
    trades_count: int
    max_dd: float
    sl_count: int
    be_count: int
    tp1_be_count: int
    tp2_count: int
