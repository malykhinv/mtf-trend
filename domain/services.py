"""Service layer working with Binance exchange.

This module provides thin wrappers around Binance Futures REST and
websocket APIs as well as several small domain specific helpers.  The
implementations intentionally avoid any dynamic dictionaries in public
interfaces and rely solely on the strongly typed models defined under
``domain.models``.

Only a very small subset of the complete trading logic is implemented –
just enough for tests and examples to interact with the exchange in a
deterministic way.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import time
from collections import deque
from dataclasses import dataclass
from hashlib import sha256
from typing import Deque, Dict, List, Tuple

import httpx
import websockets

from config.credentials import BINANCE
from constants import (
    BINANCE_FAPI_REST,
    BINANCE_FAPI_WS,
    RISK_PER_TRADE_USDT,
    TAKER_RATIO_LIMIT,
    TAKER_RATIO_PERIOD,
)
from domain.models.trading import OrderSpec, PositionPlan
from domain.models.state import SymbolState
from domain.models.market_data import AggTrade, DepthSnapshot, LiquidationEvent
from domain.models import metrics as M, trading as T, signals as S
from domain.models.enums import OrderType, Side
from domain.models.config import ProfileConfig


class Trader:
    """Minimal wrapper for Binance Futures trading endpoints."""

    _ORDER_ENDPOINT = "/fapi/v1/order"
    _CANCEL_ALL_ENDPOINT = "/fapi/v1/allOpenOrders"

    def __init__(self) -> None:
        self._client = httpx.Client(base_url=BINANCE_FAPI_REST, timeout=10.0)

    # ------------------------------------------------------------------
    def _signed_request(self, method: str, endpoint: str, params: Dict[str, str]) -> httpx.Response:
        ts = int(time.time() * 1000)
        params["timestamp"] = str(ts)
        query = "&".join(f"{k}={v}" for k, v in params.items())
        signature = hmac.new(
            BINANCE.api_secret.encode("utf-8"), query.encode("utf-8"), sha256
        ).hexdigest()
        headers = {"X-MBX-APIKEY": BINANCE.api_key}
        url = endpoint + f"?{query}&signature={signature}"
        return self._client.request(method, url, headers=headers)

    # ------------------------------------------------------------------
    def place(self, order: OrderSpec) -> None:  # pragma: no cover - network
        params: Dict[str, str] = {
            "symbol": order.symbol,
            "side": order.side.value,
            "type": order.type.value,
            "quantity": f"{order.quantity}",
        }
        if order.type is OrderType.LIMIT and order.price is not None:
            params["price"] = f"{order.price}"
            params["timeInForce"] = "GTC"
        response = self._signed_request("POST", self._ORDER_ENDPOINT, params)
        response.raise_for_status()

    # ------------------------------------------------------------------
    def cancel(self, symbol: str, all_for_symbol: bool) -> None:  # pragma: no cover - network
        if all_for_symbol:
            endpoint = self._CANCEL_ALL_ENDPOINT
            params = {"symbol": symbol}
            response = self._signed_request("DELETE", endpoint, params)
            response.raise_for_status()
        else:
            # The simplified interface allows cancelling all orders for a
            # symbol only.  For single order cancel, a more specific order id
            # would be required, which is outside the scope of this example.
            endpoint = self._CANCEL_ALL_ENDPOINT
            params = {"symbol": symbol}
            response = self._signed_request("DELETE", endpoint, params)
            response.raise_for_status()


class SymbolRegistry:
    """In memory storage of ``SymbolState`` objects."""

    def __init__(self) -> None:
        self._states: Dict[str, SymbolState] = {}

    def get(self, symbol: str) -> SymbolState:
        return self._states[symbol]

    def put(self, state: SymbolState) -> None:
        self._states[state.symbol] = state

    def update(self, symbol: str, new_state: SymbolState) -> None:
        self._states[symbol] = new_state

    def all_symbols(self) -> tuple[str, ...]:
        return tuple(self._states.keys())


class RestClient:
    """REST requests to public Binance Futures endpoints."""

    _OPEN_INTEREST_EP = "/fapi/v1/openInterest"
    _TAKER_RATIO_EP = "/futures/data/takerlongshortRatio"
    _PREMIUM_EP = "/fapi/v1/premiumIndex"
    _TICKER_EP = "/fapi/v1/ticker/24hr"

    def __init__(self) -> None:
        self._client = httpx.Client(base_url=BINANCE_FAPI_REST, timeout=10.0)

    def get_open_interest(self, symbol: str) -> float:
        r = self._client.get(self._OPEN_INTEREST_EP, params={"symbol": symbol})
        r.raise_for_status()
        data = r.json()
        return float(data["openInterest"])

    def get_taker_ratio(self, symbol: str) -> tuple[float, float]:
        params = {
            "symbol": symbol,
            "period": TAKER_RATIO_PERIOD,
            "limit": TAKER_RATIO_LIMIT,
        }
        r = self._client.get(self._TAKER_RATIO_EP, params=params)
        r.raise_for_status()
        data = r.json()
        if not data:
            return 0.0, 0.0
        item = data[0]
        return float(item["buyVol"]), float(item["sellVol"])

    def get_premium_pct(self, symbol: str) -> float:
        r = self._client.get(self._PREMIUM_EP, params={"symbol": symbol})
        r.raise_for_status()
        data = r.json()
        mark_price = float(data["markPrice"])
        index_price = float(data["indexPrice"])
        if index_price == 0:
            return 0.0
        return (mark_price / index_price - 1.0) * 100.0

    def get_24h_stats(self, symbol: str) -> tuple[float, float]:
        r = self._client.get(self._TICKER_EP, params={"symbol": symbol})
        r.raise_for_status()
        data = r.json()
        quote_volume = float(data["quoteVolume"])
        last_price = float(data["lastPrice"])
        return quote_volume, last_price


class WsClient:
    """Client for Binance Futures websocket streams."""

    def __init__(self) -> None:
        self._symbols: tuple[str, ...] = tuple()
        self._agg_trades: Deque[AggTrade] = deque()
        self._depths: Deque[DepthSnapshot] = deque()
        self._liqs: Deque[LiquidationEvent] = deque()

    def subscribe_symbols(self, symbols: tuple[str, ...]) -> None:
        self._symbols = symbols

    async def stream(self) -> None:  # pragma: no cover - network
        params: List[str] = []
        for sym in self._symbols:
            ls = sym.lower()
            params.extend([f"{ls}@aggTrade", f"{ls}@depth20@100ms", f"{ls}@forceOrder"])

        async with websockets.connect(BINANCE_FAPI_WS) as ws:
            if params:
                msg = {"method": "SUBSCRIBE", "params": params, "id": 1}
                await ws.send(json.dumps(msg))

            async for raw in ws:
                data = json.loads(raw)
                stream = data.get("stream")
                payload = data.get("data")
                if not stream or not payload:
                    continue
                if stream.endswith("aggTrade"):
                    self._agg_trades.append(
                        AggTrade(
                            symbol=payload["s"],
                            price=float(payload["p"]),
                            quantity=float(payload["q"]),
                            timestamp=int(payload["T"]),
                        )
                    )
                elif "depth" in stream:
                    bids = tuple((float(p), float(q)) for p, q in payload["bids"])
                    asks = tuple((float(p), float(q)) for p, q in payload["asks"])
                    self._depths.append(
                        DepthSnapshot(
                            symbol=payload["s"],
                            bids=bids,
                            asks=asks,
                            timestamp=int(payload["E"]),
                        )
                    )
                elif stream.endswith("forceOrder"):
                    order = payload["o"]
                    side = Side.LONG if order["S"] == "BUY" else Side.SHORT
                    self._liqs.append(
                        LiquidationEvent(
                            symbol=order["s"],
                            side=side,
                            price=float(order["ap"]),
                            quantity=float(order["q"]),
                            timestamp=int(order["T"]),
                        )
                    )

    def next_agg_trade(self) -> AggTrade | None:
        return self._agg_trades.popleft() if self._agg_trades else None

    def next_depth(self) -> DepthSnapshot | None:
        return self._depths.popleft() if self._depths else None

    def next_liquidation(self) -> LiquidationEvent | None:
        return self._liqs.popleft() if self._liqs else None


class SignalEngine:
    """Extremely small placeholder signal engine.

    In the real project this component would analyse a large amount of
    market data and produce trading signals.  For the purposes of the
    exercises we implement very small deterministic stubs that still use
    the strongly typed models and configuration objects.
    """

    def __init__(self, config: ProfileConfig) -> None:
        self._config = config

    def on_minute_close(self, symbol: str) -> S.PumpSignal | None:
        # Real logic would check z-scores and other metrics.  Here we do a
        # minimalistic placeholder returning ``None`` meaning no signal.
        return None

    def confirm_failure(self, symbol: str, window: M.PumpWindow) -> bool:
        # Placeholder – assume confirmation never fails.
        return False

    def make_entry(self, symbol: str, window: M.PumpWindow) -> S.EntrySignal | None:
        # A real implementation would check additional criteria from the
        # profile configuration.  We simply do not emit entry signals here.
        return None


class RiskManager:
    """Simple position sizing and risk calculations."""

    def __init__(self, config: ProfileConfig) -> None:
        self._cfg = config

    def build_plan(self, symbol: str, entry_price: float, window: M.PumpWindow) -> T.PositionPlan | None:
        risk = self._cfg.risk
        # Stop loss is placed above the recent high by ``stop_abs_pct``.
        stop_loss = window.high * (1.0 + risk.stop_abs_pct / 100.0)
        # Take profit levels are computed cumulatively using configuration percentages.
        take_profit1 = entry_price * (1.0 - risk.tp1_pct / 100.0)
        tp2_total_pct = risk.tp1_pct + risk.tp2_pct
        take_profit2 = entry_price * (1.0 - tp2_total_pct / 100.0)
        # Trailing starts after the tail portion moves in favor of the position.
        trail_start_pct = tp2_total_pct + risk.tail_pct
        trail_start = entry_price * (1.0 - trail_start_pct / 100.0)
        # Trailing distance is defined by the larger of absolute percent and
        # a multiple of the recent price range.
        range_pct = (window.high - window.low) / entry_price
        trail_distance = entry_price * max(risk.trail_abs_pct / 100.0, range_pct * risk.trail_sigma_mult)
        quantity = RISK_PER_TRADE_USDT / entry_price
        return PositionPlan(
            symbol=symbol,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit1=take_profit1,
            take_profit2=take_profit2,
            trail_start=trail_start,
            trail_distance=trail_distance,
            quantity=quantity,
        )

    def allow_trade(self, plan: T.PositionPlan) -> bool:
        # trivial check – ensure position size is positive
        return plan.quantity > 0


class TradeManager:
    """High level wrapper around :class:`Trader` handling position state."""

    def __init__(self, trader: Trader) -> None:
        self._trader = trader
        self._positions: Dict[str, PositionPlan] = {}

    def open_position(self, plan: T.PositionPlan) -> None:
        order = OrderSpec(
            symbol=plan.symbol,
            side=Side.SHORT,
            type=OrderType.MARKET,
            quantity=plan.quantity,
        )
        self._trader.place(order)
        self._positions[plan.symbol] = plan

    def on_tick_manage(
        self, symbol: str, price: float | None = None
    ) -> tuple[list[S.ExitSignal], T.PositionPlan | None]:
        """Manage an existing position on each price tick.

        The method checks whether any exit conditions have been met for the
        position associated with ``symbol``.  It returns a list of exit signals
        and the possibly updated position plan.  If the position is fully
        closed, ``None`` is returned for the plan.
        """

        plan = self._positions.get(symbol)
        exits: List[S.ExitSignal] = []
        if plan is None:
            return exits, None

        current_price = plan.entry_price if price is None else price

        # ------------------------------------------------------------------
        # Trailing stop management (active after both take profits executed)
        trailing_active = plan.take_profit1 <= 0.0 and plan.take_profit2 <= 0.0
        if trailing_active and current_price <= plan.trail_start:
            new_stop = min(plan.stop_loss, current_price + plan.trail_distance)
            plan = T.PositionPlan(
                symbol=plan.symbol,
                entry_price=plan.entry_price,
                stop_loss=new_stop,
                take_profit1=plan.take_profit1,
                take_profit2=plan.take_profit2,
                trail_start=current_price,
                trail_distance=plan.trail_distance,
                quantity=plan.quantity,
            )
            self._positions[symbol] = plan
            trailing_active = True

        # ------------------------------------------------------------------
        # Stop loss or trailing stop hit
        if current_price >= plan.stop_loss:
            reason = "TRAIL" if trailing_active else "STOP"
            exits.append(S.ExitSignal(symbol=symbol, reason=reason))
            del self._positions[symbol]
            return exits, None

        # ------------------------------------------------------------------
        # Take profit levels
        if plan.take_profit1 > 0.0 and current_price <= plan.take_profit1:
            exits.append(S.ExitSignal(symbol=symbol, reason="TP1"))
            plan = T.PositionPlan(
                symbol=plan.symbol,
                entry_price=plan.entry_price,
                stop_loss=plan.entry_price,  # move to break-even
                take_profit1=0.0,
                take_profit2=plan.take_profit2,
                trail_start=plan.trail_start,
                trail_distance=plan.trail_distance,
                quantity=plan.quantity,
            )
            self._positions[symbol] = plan

        if plan.take_profit2 > 0.0 and current_price <= plan.take_profit2:
            exits.append(S.ExitSignal(symbol=symbol, reason="TP2"))
            plan = T.PositionPlan(
                symbol=plan.symbol,
                entry_price=plan.entry_price,
                stop_loss=plan.stop_loss,
                take_profit1=plan.take_profit1,
                take_profit2=0.0,
                trail_start=plan.trail_start,
                trail_distance=plan.trail_distance,
                quantity=plan.quantity,
            )
            self._positions[symbol] = plan

        # ------------------------------------------------------------------
        # Activate trailing after both take profits have been executed
        trailing_active = plan.take_profit1 <= 0.0 and plan.take_profit2 <= 0.0
        if trailing_active and current_price <= plan.trail_start:
            new_stop = min(plan.stop_loss, current_price + plan.trail_distance)
            plan = T.PositionPlan(
                symbol=plan.symbol,
                entry_price=plan.entry_price,
                stop_loss=new_stop,
                take_profit1=plan.take_profit1,
                take_profit2=plan.take_profit2,
                trail_start=current_price,
                trail_distance=plan.trail_distance,
                quantity=plan.quantity,
            )
            self._positions[symbol] = plan

        return exits, plan
