"""Централизованный расчёт уровней сделки bee_bite."""

from __future__ import annotations

from dataclasses import dataclass

from domain.enums.position_side import PositionSide


PROFILE_TP1_SHARE: dict[str, float] = {
    "A": 0.7,  # Conservative
    "B": 0.6,  # Balanced
    "C": 0.5,  # Aggressive
}


@dataclass(frozen=True, slots=True)
class BeeBiteTradePlan:
    stop_loss: float
    stop_distance: float
    tp1: float
    tp2: float
    trailing_mode: bool
    be_stop: float


def resolve_profile_tp1_share(profile_id: str | None, fallback: float = 0.5) -> float:
    raw = PROFILE_TP1_SHARE.get((profile_id or "").upper(), fallback)
    return min(max(raw, 0.05), 0.95)


def build_bee_bite_trade_plan(
    *,
    side: PositionSide,
    entry_price: float,
    atr_bg: float,
    stop_loss: float,
    high_pump: float | None,
    low_before_pump: float | None,
    be_offset_ratio: float,
    trailing_tp2_atr_multiplier: float = 10.0,
) -> BeeBiteTradePlan | None:
    if atr_bg <= 0:
        return None

    if side == PositionSide.LONG:
        stop_distance = entry_price - stop_loss
    else:
        stop_distance = stop_loss - entry_price
    if stop_distance <= 0:
        return None

    if side == PositionSide.LONG:
        tp1 = entry_price + stop_distance
        fixed_tp2 = high_pump if high_pump is not None and (high_pump - entry_price) >= atr_bg else None
        trailing_tp2 = entry_price + trailing_tp2_atr_multiplier * atr_bg
        be_stop = entry_price * (1 + be_offset_ratio)
    else:
        tp1 = entry_price - stop_distance
        fixed_tp2 = low_before_pump if low_before_pump is not None and (entry_price - low_before_pump) >= atr_bg else None
        trailing_tp2 = entry_price - trailing_tp2_atr_multiplier * atr_bg
        be_stop = entry_price * (1 - be_offset_ratio)

    trailing_mode = fixed_tp2 is None
    tp2 = trailing_tp2 if trailing_mode else fixed_tp2
    return BeeBiteTradePlan(
        stop_loss=stop_loss,
        stop_distance=stop_distance,
        tp1=tp1,
        tp2=tp2,
        trailing_mode=trailing_mode,
        be_stop=be_stop,
    )
