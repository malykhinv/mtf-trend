"""Trade-plan helpers for post-pump absorption."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PostPumpAbsorptionTradePlan:
    stop_loss: float
    stop_distance: float
    tp1: float
    tp2: float
    be_stop: float


def build_post_pump_absorption_trade_plan(
    *,
    entry_price: float,
    stop_loss: float,
    range_low: float,
    range_high: float,
    be_buffer_pct: float,
) -> PostPumpAbsorptionTradePlan | None:
    if entry_price <= 0.0:
        return None
    if stop_loss >= entry_price:
        return None
    if range_high <= range_low:
        return None

    stop_distance = entry_price - stop_loss
    range_mid = (range_low + range_high) / 2.0
    tp1 = range_mid
    tp2 = range_high
    if tp1 <= entry_price or tp2 <= entry_price:
        return None

    return PostPumpAbsorptionTradePlan(
        stop_loss=stop_loss,
        stop_distance=stop_distance,
        tp1=tp1,
        tp2=tp2,
        be_stop=entry_price * (1.0 + be_buffer_pct),
    )
