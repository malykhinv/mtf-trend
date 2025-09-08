from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

import httpx
import logging

from constants import RISK_PER_TRADE_USDT, BINANCE_FAPI_REST
from domain.models import metrics as M
from domain.models.config import ProfileConfig, RiskParams
from domain.models.trading import PositionPlan

logger = logging.getLogger(__name__)


class RiskManager:
    """Simple position sizing and risk calculations."""

    def __init__(
        self, config: ProfileConfig, http_client: httpx.AsyncClient | None = None
    ) -> None:
        self._cfg = config
        self._open_risk_usdt: float = 0.0
        self._client = http_client or httpx.AsyncClient(timeout=10.0)
        self._own_client = http_client is None

    async def build_plan(
        self, symbol: str, entry_price: float, window: M.PumpWindow
    ) -> PositionPlan | None:
        try:
            filters = await self._get_symbol_filters(symbol)
            step_size = Decimal(filters["LOT_SIZE"]["stepSize"])
            tick_size = Decimal(filters["PRICE_FILTER"]["tickSize"])

            risk = self._cfg.risk
            range_pct = (window.high - window.low) / window.low
            stop_loss = self._round(
                self._calc_stop_loss(window.high, range_pct, risk), tick_size
            )
            take_profit1, take_profit2, tp_total_pct = self._calc_take_profits(
                entry_price, risk
            )
            take_profit1 = self._round(take_profit1, tick_size)
            take_profit2 = self._round(take_profit2, tick_size)
            trail_start, trail_distance = self._calc_trailing(
                entry_price, range_pct, risk, tp_total_pct
            )
            trail_start = self._round(trail_start, tick_size)
            trail_distance = self._round(trail_distance, tick_size)
            quantity, tp1_qty, tp2_qty, tail_qty = self._calc_quantities(
                entry_price, risk
            )
            quantity = self._round(quantity, step_size)
            tp1_qty = self._round(tp1_qty, step_size)
            tp2_qty = self._round(tp2_qty, step_size)
            tail_qty = self._round(quantity - tp1_qty - tp2_qty, step_size)
            logger.info(
                (
                    "Built plan for %s: SL=%.4f TP1=%.4f TP2=%.4f trail_start=%.4f "
                    "trail_distance=%.4f"
                ),
                symbol,
                stop_loss,
                take_profit1,
                take_profit2,
                trail_start,
                trail_distance,
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
        except Exception:
            logger.exception("Ошибка build_plan %s :>", symbol)
            return None

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

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()

    async def _get_symbol_filters(self, symbol: str) -> dict:
        try:
            resp = await self._client.get(
                BINANCE_FAPI_REST + "/fapi/v1/exchangeInfo",
                params={"symbol": symbol},
            )
            resp.raise_for_status()
            info = resp.json()["symbols"][0]["filters"]
            return {f["filterType"]: f for f in info}
        except Exception:
            logger.exception("Failed to fetch symbol filters %s", symbol)
            raise

    @staticmethod
    def _round(value: float, step: Decimal) -> float:
        return float(Decimal(str(value)).quantize(step, rounding=ROUND_DOWN))

    def allow_trade(self, plan: PositionPlan) -> bool:
        try:
            required_margin = plan.entry_price * plan.quantity
            logger.info("Required margin for %s: %.2f", plan.symbol, required_margin)
            if required_margin > RISK_PER_TRADE_USDT:
                logger.warning(
                    "Trade %s rejected: margin %.2f exceeds per-trade limit %.2f",
                    plan.symbol,
                    required_margin,
                    RISK_PER_TRADE_USDT,
                )
                return False
            if self._open_risk_usdt + required_margin > self._cfg.max_margin_usdt:
                logger.warning(
                    "Trade %s rejected: total margin %.2f exceeds max %.2f",
                    plan.symbol,
                    self._open_risk_usdt + required_margin,
                    self._cfg.max_margin_usdt,
                )
                return False
            self._open_risk_usdt += required_margin
            return True
        except Exception:
            logger.exception("Ошибка allow_trade %s :>", plan.symbol)
            return False

    def release(self, plan: PositionPlan) -> None:
        try:
            margin = plan.entry_price * plan.quantity
            self._open_risk_usdt = max(0.0, self._open_risk_usdt - margin)
        except Exception:
            logger.exception("Ошибка release %s :>", plan.symbol)
