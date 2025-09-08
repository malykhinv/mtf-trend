from __future__ import annotations

from constants import RISK_PER_TRADE_USDT
from domain.models import metrics as M
from domain.models.config import ProfileConfig, RiskParams
from domain.models.trading import PositionPlan


class RiskManager:
    """Simple position sizing and risk calculations."""

    def __init__(self, config: ProfileConfig) -> None:
        self._cfg = config
        self._open_risk_usdt: float = 0.0

    def build_plan(self, symbol: str, entry_price: float, window: M.PumpWindow) -> PositionPlan | None:
        risk = self._cfg.risk
        range_pct = (window.high - window.low) / window.low
        stop_loss = self._calc_stop_loss(window.high, range_pct, risk)
        take_profit1, take_profit2, tp_total_pct = self._calc_take_profits(
            entry_price, risk
        )
        trail_start, trail_distance = self._calc_trailing(
            entry_price, range_pct, risk, tp_total_pct
        )
        quantity, tp1_qty, tp2_qty, tail_qty = self._calc_quantities(
            entry_price, risk
        )
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
            window_high=window.high,
        )

    def _calc_stop_loss(
        self, high: float, range_pct: float, risk: RiskParams
    ) -> float:
        stop_abs = high * (risk.stop_abs_pct / 100.0)
        sigma_stop = high * (risk.stop_sigma_mult * range_pct)
        return high + max(stop_abs, sigma_stop)

    def _calc_take_profits(
        self, entry_price: float, risk: RiskParams
    ) -> tuple[float, float, float]:
        tp1_pct = risk.tp1_pct / 100.0
        tp2_pct = risk.tp2_pct / 100.0
        tp_total_pct = tp1_pct + tp2_pct
        take_profit1 = entry_price * (1.0 - tp1_pct)
        take_profit2 = entry_price * (1.0 - tp_total_pct)
        return take_profit1, take_profit2, tp_total_pct

    def _calc_trailing(
        self,
        entry_price: float,
        range_pct: float,
        risk: RiskParams,
        tp_total_pct: float,
    ) -> tuple[float, float]:
        tail_pct = risk.tail_pct / 100.0
        trail_start_pct = tp_total_pct + tail_pct
        trail_start = entry_price * (1.0 - trail_start_pct)
        trail_distance = entry_price * max(
            risk.trail_abs_pct / 100.0, range_pct * risk.trail_sigma_mult
        )
        return trail_start, trail_distance

    def _calc_quantities(
        self, entry_price: float, risk: RiskParams
    ) -> tuple[float, float, float, float]:
        quantity = RISK_PER_TRADE_USDT / entry_price
        tp1_pct = risk.tp1_pct / 100.0
        tp2_pct = risk.tp2_pct / 100.0
        tail_pct = risk.tail_pct / 100.0
        total_pct = tp1_pct + tp2_pct + tail_pct
        tp1_qty = quantity * (tp1_pct / total_pct)
        tp2_qty = quantity * (tp2_pct / total_pct)
        tail_qty = quantity - tp1_qty - tp2_qty
        return quantity, tp1_qty, tp2_qty, tail_qty

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
