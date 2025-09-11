from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, List

import websockets
from websockets.client import WebSocketClientProtocol

from constants import BINANCE_FAPI_WS
from domain.models.enums import Side
from domain.models.market_data import AggTrade, DepthSnapshot, LiquidationEvent


logger = logging.getLogger(__name__)


class WsClient:
    """Client for Binance Futures websocket streams."""

    def __init__(self, queue_maxsize: int = 0) -> None:
        self._symbols: tuple[str, ...] = tuple()
        self._agg_trades: asyncio.Queue[AggTrade | None] = asyncio.Queue(
            maxsize=queue_maxsize
        )
        self._depths: asyncio.Queue[DepthSnapshot | None] = asyncio.Queue(
            maxsize=queue_maxsize
        )
        self._liqs: asyncio.Queue[LiquidationEvent | None] = asyncio.Queue(
            maxsize=queue_maxsize
        )
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

    async def _connect_and_subscribe(self) -> None:  # pragma: no cover - network
        """Establish websocket connection and send subscription message."""

        self._ws = await websockets.connect(BINANCE_FAPI_WS)
        if self._subscribe_msg and self._ws:
            await self._ws.send(self._subscribe_msg)

    async def _consume_messages(self) -> None:  # pragma: no cover - network
        """Read messages from the websocket and dispatch them for handling."""

        assert self._ws is not None
        async for raw in self._ws:
            await self._parse_message(raw)

    async def stream(self) -> None:  # pragma: no cover - network
        attempt = 0
        delay = 1.0

        while True:
            try:
                await self._connect_and_subscribe()
                attempt = 0
                delay = 1.0

                await self._consume_messages()

            except (websockets.exceptions.WebSocketException, OSError) as exc:
                if self._ws is not None:
                    await self._ws.close()

                attempt += 1
                logger.exception(
                    "Ошибка WebSocket (%s). попытка переподключения %d :>",
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

    async def _parse_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.exception("Failed to decode websocket message")
            return

        stream = data.get("stream")
        payload = data.get("data")
        if stream is None or payload is None:
            return

        event = stream.rsplit("@", 1)[-1]
        if event not in {"aggTrade", "forceOrder"} and "depth" in stream:
            event = "depth"

        handlers: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {
            "aggTrade": self._handle_agg_trade,
            "depth": self._handle_depth,
            "forceOrder": self._handle_liquidation,
        }

        handler: Callable[[dict[str, Any]], Awaitable[None]] | None = handlers.get(event)
        if handler:
            await handler(payload)
        else:
            logger.warning("Unknown event type: %s", event)

    async def _handle_agg_trade(self, payload: dict[str, Any]) -> None:
        await self._agg_trades.put(
            AggTrade(
                symbol=payload["s"],
                price=float(payload["p"]),
                quantity=float(payload["q"]),
                timestamp=int(payload["T"]),
            )
        )

    async def _handle_depth(self, payload: dict[str, Any]) -> None:
        bids_raw = payload.get("bids") or payload.get("b") or []
        asks_raw = payload.get("asks") or payload.get("a") or []
        bids = tuple((float(p), float(q)) for p, q in bids_raw)
        asks = tuple((float(p), float(q)) for p, q in asks_raw)
        await self._depths.put(
            DepthSnapshot(
                symbol=payload.get("s", ""),
                bids=bids,
                asks=asks,
                timestamp=int(payload.get("E", 0)),
            )
        )

    async def _handle_liquidation(self, payload: dict[str, Any]) -> None:
        order = payload["o"]
        side = Side.LONG if order["S"] == "BUY" else Side.SHORT
        await self._liqs.put(
            LiquidationEvent(
                symbol=order["s"],
                side=side,
                price=float(order["ap"]),
                quantity=float(order["q"]),
                timestamp=int(order["T"]),
            )
        )

    async def next_agg_trade(self) -> AggTrade | None:
        return await self._agg_trades.get()

    async def next_depth(self) -> DepthSnapshot | None:
        return await self._depths.get()

    async def next_liquidation(self) -> LiquidationEvent | None:
        return await self._liqs.get()

    async def close(self) -> None:  # pragma: no cover - network
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        await self._agg_trades.put(None)
        await self._depths.put(None)
        await self._liqs.put(None)
