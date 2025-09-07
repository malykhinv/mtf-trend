from __future__ import annotations

from constants import RISK_PER_TRADE_USDT
from domain.models import metrics as M
from domain.models.config import ProfileConfig
from domain.models.trading import PositionPlan


class RiskManager:
    """Simple position sizing and risk calculations."""

    def __init__(self, config: ProfileConfig) -> None:
        self._cfg = config
        self._open_risk_usdt: float = 0.0

    def build_plan(self, symbol: str, entry_price: float, window: M.PumpWindow) -> PositionPlan | None:
        risk = self._cfg.risk
        # Stop loss uses the greater of absolute percent and a range based sigma.
        range_pct = (window.high - window.low) / window.low
        stop_abs = window.high * (risk.stop_abs_pct / 100.0)
        sigma_stop = window.high * (risk.stop_sigma_mult * range_pct)
        stop_loss = window.high + max(stop_abs, sigma_stop)
        # Take profit levels are computed cumulatively using configuration percentages.
        take_profit1 = entry_price * (1.0 - risk.tp1_pct / 100.0)
        tp2_total_pct = risk.tp1_pct + risk.tp2_pct
        take_profit2 = entry_price * (1.0 - tp2_total_pct / 100.0)
        # Trailing starts after the tail portion moves in favor of the position.
        trail_start_pct = tp2_total_pct + risk.tail_pct
        trail_start = entry_price * (1.0 - trail_start_pct / 100.0)
        # Trailing distance is defined by the larger of absolute percent and
        # a multiple of the recent price range.
        trail_distance = entry_price * max(
            risk.trail_abs_pct / 100.0, range_pct * risk.trail_sigma_mult
        )
        quantity = RISK_PER_TRADE_USDT / entry_price
        total = risk.tp1_pct + risk.tp2_pct + risk.tail_pct
        tp1_qty = quantity * (risk.tp1_pct / total)
        tp2_qty = quantity * (risk.tp2_pct / total)
        tail_qty = quantity - tp1_qty - tp2_qty
        return PositionPlan(
            symbol=symbol,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit1=take_profit1,
            take_profit2=take_profit2,
            trail_start=trail_start,
            trail_distance=trail_distance,
            quantity=quantity,
            tp1_qty=tp1_qty,
            tp2_qty=tp2_qty,
            tail_qty=tail_qty,
        )

    def allow_trade(self, plan: PositionPlan) -> bool:
        required_margin = plan.entry_price * plan.quantity
        if required_margin > RISK_PER_TRADE_USDT:
            return False
        if self._open_risk_usdt + required_margin > self._cfg.max_margin_usdt:
            return False
        self._open_risk_usdt += required_margin
        return True

    def release(self, plan: PositionPlan) -> None:
        margin = plan.entry_price * plan.quantity
        self._open_risk_usdt = max(0.0, self._open_risk_usdt - margin)
