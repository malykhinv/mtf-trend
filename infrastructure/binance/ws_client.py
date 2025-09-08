from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from typing import Any, Deque, List

import websockets
from websockets.client import WebSocketClientProtocol

from constants import BINANCE_FAPI_WS
from domain.models.enums import Side
from domain.models.market_data import AggTrade, DepthSnapshot, LiquidationEvent


logger = logging.getLogger(__name__)


class WsClient:
    """Client for Binance Futures websocket streams."""

    def __init__(self) -> None:
        self._symbols: tuple[str, ...] = tuple()
        self._agg_trades: Deque[AggTrade] = deque()
        self._depths: Deque[DepthSnapshot] = deque()
        self._liqs: Deque[LiquidationEvent] = deque()
        self._ws: WebSocketClientProtocol | None = None
        # Pre-built subscribe message sent on connect/reconnect.  It is
        # created once in ``subscribe_symbols`` so that the network loop
        # does not rebuild JSON on every reconnect.
        self._subscribe_msg: str | None = None

    def subscribe_symbols(self, symbols: tuple[str, ...]) -> None:
        """Store symbols and build subscription payload.

        The websocket requires a subscription message with all stream names.
        We pre-build this JSON once to reuse it on reconnects.
        """

        self._symbols = symbols
        params = self._build_params(symbols)
        if params:
            self._subscribe_msg = json.dumps(
                {"method": "SUBSCRIBE", "params": params, "id": 1}
            )
        else:
            self._subscribe_msg = None

    async def stream(self) -> None:  # pragma: no cover - network
        attempt = 0
        delay = 1.0

        while True:
            try:
                self._ws = await websockets.connect(BINANCE_FAPI_WS)
                if self._subscribe_msg:
                    await self._ws.send(self._subscribe_msg)

                attempt = 0
                delay = 1.0

                async for raw in self._ws:
                    self._parse_message(raw)

            except (websockets.exceptions.WebSocketException, OSError) as exc:
                if self._ws and not self._ws.closed:
                    await self._ws.close()

                attempt += 1
                logger.exception(
                    ":< ошибка WebSocket (%s). попытка переподключения %d :>",
                    exc,
                    attempt,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)
            finally:
                self._ws = None


    def _build_params(self, symbols: tuple[str, ...]) -> List[str]:
        params: List[str] = []
        for sym in symbols:
            ls = sym.lower()
            params.extend(
                [f"{ls}@aggTrade", f"{ls}@depth20@100ms", f"{ls}@forceOrder"]
            )
        return params

    def _parse_message(self, raw: str) -> None:
        data = json.loads(raw)
        stream = data.get("stream")
        payload = data.get("data")
        if not stream or not payload:
            return
        if stream.endswith("aggTrade"):
            self._handle_agg_trade(payload)
        elif "depth" in stream:
            self._handle_depth(payload)
        elif stream.endswith("forceOrder"):
            self._handle_liquidation(payload)

    def _handle_agg_trade(self, payload: dict[str, Any]) -> None:
        self._agg_trades.append(
            AggTrade(
                symbol=payload["s"],
                price=float(payload["p"]),
                quantity=float(payload["q"]),
                timestamp=int(payload["T"]),
            )
        )

    def _handle_depth(self, payload: dict[str, Any]) -> None:
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

    def _handle_liquidation(self, payload: dict[str, Any]) -> None:
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

    async def close(self) -> None:  # pragma: no cover - network
        if self._ws and not self._ws.closed:
            await self._ws.close()
            self._ws = None
