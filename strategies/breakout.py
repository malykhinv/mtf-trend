from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskRewardResult:
    """Detailed result of risk-reward evaluation."""

    rr: float
    meets_min_rr: bool
    meets_min_tp: bool
    tp1: float
    tp2: float


def check_breakout(last_price: float, level_top: float) -> bool:
    """Return ``True`` when ``last_price`` breaks above ``level_top``."""

    return last_price >= level_top


def compute_rr(
    entry_price: float,
    l_pullback: float,
    h_main: float,
    *,
    min_rr: float,
    min_tp_pct: float,
) -> RiskRewardResult:
    """Compute risk-reward metrics and validate thresholds."""

    if entry_price <= 0:
        raise ValueError("entry_price должен быть положительным")
    if min_rr < 0:
        raise ValueError("min_rr не может быть отрицательным")
    if min_tp_pct < 0:
        raise ValueError("min_tp_pct не может быть отрицательным")

    stop_distance = entry_price - l_pullback
    if stop_distance <= 0:
        raise ValueError("entry_price должен быть больше L_pullback")

    profit_distance = h_main - entry_price
    rr = profit_distance / stop_distance

    tp1 = h_main
    tp2 = h_main + (2.0 / 3.0) * (h_main - l_pullback)

    meets_min_rr = rr > min_rr or abs(rr - min_rr) < 1e-9
    min_tp_value = (tp1 - entry_price) / entry_price
    meets_min_tp = min_tp_value >= min_tp_pct

    return RiskRewardResult(
        rr=rr,
        meets_min_rr=meets_min_rr,
        meets_min_tp=meets_min_tp,
        tp1=tp1,
        tp2=tp2,
    )


__all__ = ["RiskRewardResult", "check_breakout", "compute_rr"]
