from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Dict, List, Tuple

import websockets
from websockets.client import ClientProtocol

from constants import (
    BINANCE_FAPI_WS,
    WS_PING_INTERVAL_SEC,
    WS_PING_TIMEOUT_SEC,
)
from domain.models.enums import Side
from domain.models.market_data import AggTrade, DepthSnapshot, LiquidationEvent


logger = logging.getLogger(__name__)


class _WsConnection:
    """Handle a single websocket connection for a chunk of symbols."""

    def __init__(
        self,
        symbols: tuple[str, ...],
        agg_q: asyncio.Queue[AggTrade | None],
        depth_q: asyncio.Queue[DepthSnapshot | None],
        liq_q: asyncio.Queue[LiquidationEvent | None],
        ping_interval: float = WS_PING_INTERVAL_SEC,
        ping_timeout: float = WS_PING_TIMEOUT_SEC,
    ) -> None:
        self._symbols = tuple(symbols)
        self._detail_symbols: set[str] = set()
        self._agg_q = agg_q
        self._depth_q = depth_q
        self._liq_q = liq_q
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._ws: ClientProtocol | None = None
        self._subscribe_msg: str | None = None
        self._req_id = 0
        self._update_subscribe_msg()

    # ------------------------------------------------------------------
    # Subscription helpers
    # ------------------------------------------------------------------
    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _detail_params(self, symbols: Tuple[str, ...]) -> List[str]:
        params: List[str] = []
        for sym in symbols:
            ls = sym.lower()
            params.extend([f"{ls}@depth20@100ms", f"{ls}@forceOrder"])
        return params

    def _build_params(self) -> List[str]:
        params: List[str] = []
        for sym in self._symbols:
            ls = sym.lower()
            params.append(f"{ls}@aggTrade")
            if sym in self._detail_symbols:
                params.extend([f"{ls}@depth20@100ms", f"{ls}@forceOrder"])
        return params

    def _update_subscribe_msg(self) -> None:
        params = self._build_params()
        if params:
            self._subscribe_msg = json.dumps(
                {"method": "SUBSCRIBE", "params": params, "id": self._next_id()}
            )
        else:
            self._subscribe_msg = None

    def add_detail_streams(self, symbols: tuple[str, ...]) -> None:
        new_syms = [s for s in symbols if s not in self._detail_symbols]
        if not new_syms:
            return
        self._detail_symbols.update(new_syms)
        self._update_subscribe_msg()
        if self._ws:
            msg = json.dumps(
                {
                    "method": "SUBSCRIBE",
                    "params": self._detail_params(tuple(new_syms)),
                    "id": self._next_id(),
                }
            )
            asyncio.create_task(self._ws.send(msg))

    def remove_detail_streams(self, symbols: tuple[str, ...]) -> None:
        rem_syms = [s for s in symbols if s in self._detail_symbols]
        if not rem_syms:
            return
        for s in rem_syms:
            self._detail_symbols.remove(s)
        self._update_subscribe_msg()
        if self._ws:
            msg = json.dumps(
                {
                    "method": "UNSUBSCRIBE",
                    "params": self._detail_params(tuple(rem_syms)),
                    "id": self._next_id(),
                }
            )
            asyncio.create_task(self._ws.send(msg))

    # ------------------------------------------------------------------
    # Networking
    # ------------------------------------------------------------------
    async def _connect_and_subscribe(self) -> None:  # pragma: no cover - network
        self._ws = await websockets.connect(
            BINANCE_FAPI_WS,
            close_timeout=5,
            ping_interval=self._ping_interval,
            ping_timeout=self._ping_timeout,
        )
        if self._subscribe_msg and self._ws:
            await self._ws.send(self._subscribe_msg)

    async def _consume_messages(self) -> None:  # pragma: no cover - network
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
                if self._ws:
                    with contextlib.suppress(asyncio.TimeoutError):
                        await self._ws.close()
                attempt += 1
                logger.exception(
                    "Ошибка WebSocket (%s). попытка переподключения %d",
                    exc,
                    attempt,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)
            finally:
                self._ws = None

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

        handlers: Dict[str, Callable[[Dict[str, Any]], Awaitable[None]]] = {
            "aggTrade": self._handle_agg_trade,
            "depth": self._handle_depth,
            "forceOrder": self._handle_liquidation,
        }

        handler = handlers.get(event)
        if handler:
            await handler(payload)
        else:
            logger.warning("Unknown event type: %s", event)

    async def _handle_agg_trade(self, payload: Dict[str, Any]) -> None:
        await self._agg_q.put(
            AggTrade(
                symbol=payload["s"],
                price=float(payload["p"]),
                quantity=float(payload["q"]),
                timestamp=int(payload["T"]),
            )
        )

    async def _handle_depth(self, payload: Dict[str, Any]) -> None:
        bids_raw = payload.get("bids") or payload.get("b") or []
        asks_raw = payload.get("asks") or payload.get("a") or []
        bids = tuple((float(p), float(q)) for p, q in bids_raw)
        asks = tuple((float(p), float(q)) for p, q in asks_raw)
        await self._depth_q.put(
            DepthSnapshot(
                symbol=payload.get("s", ""),
                bids=bids,
                asks=asks,
                timestamp=int(payload.get("E", 0)),
            )
        )

    async def _handle_liquidation(self, payload: Dict[str, Any]) -> None:
        order = payload["o"]
        side = Side.LONG if order["S"] == "BUY" else Side.SHORT
        await self._liq_q.put(
            LiquidationEvent(
                symbol=order["s"],
                side=side,
                price=float(order["ap"]),
                quantity=float(order["q"]),
                timestamp=int(order["T"]),
            )
        )

    async def close(self) -> None:  # pragma: no cover - network
        if self._ws and not self._ws.closed:
            with contextlib.suppress(asyncio.TimeoutError):
                await self._ws.close()
        self._ws = None


class WsClient:
    """Multiplex websocket client respecting Binance stream limits."""

    def __init__(
        self,
        queue_maxsize: int = 0,
        ping_interval: float = WS_PING_INTERVAL_SEC,
        ping_timeout: float = WS_PING_TIMEOUT_SEC,
    ) -> None:
        self._agg_trades: asyncio.Queue[AggTrade | None] = asyncio.Queue(
            maxsize=queue_maxsize
        )
        self._depths: asyncio.Queue[DepthSnapshot | None] = asyncio.Queue(
            maxsize=queue_maxsize
        )
        self._liqs: asyncio.Queue[LiquidationEvent | None] = asyncio.Queue(
            maxsize=queue_maxsize
        )
        self._conns: list[_WsConnection] = []
        self._symbol_to_conn: dict[str, _WsConnection] = {}
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def subscribe_symbols(self, symbols: tuple[str, ...]) -> None:
        chunk_size = 66
        self._conns.clear()
        self._symbol_to_conn.clear()
        for i in range(0, len(symbols), chunk_size):
            chunk = tuple(symbols[i : i + chunk_size])
            conn = _WsConnection(
                chunk,
                self._agg_trades,
                self._depths,
                self._liqs,
                ping_interval=self._ping_interval,
                ping_timeout=self._ping_timeout,
            )
            self._conns.append(conn)
            for sym in chunk:
                self._symbol_to_conn[sym] = conn

    def add_detail_streams(self, symbols: tuple[str, ...]) -> None:
        for sym in symbols:
            conn = self._symbol_to_conn.get(sym)
            if conn:
                conn.add_detail_streams((sym,))

    def remove_detail_streams(self, symbols: tuple[str, ...]) -> None:
        for sym in symbols:
            conn = self._symbol_to_conn.get(sym)
            if conn:
                conn.remove_detail_streams((sym,))

    async def stream(self) -> None:  # pragma: no cover - network
        await asyncio.gather(*(c.stream() for c in self._conns))

    async def next_agg_trade(self) -> AggTrade | None:
        return await self._agg_trades.get()

    async def next_depth(self) -> DepthSnapshot | None:
        return await self._depths.get()

    async def next_liquidation(self) -> LiquidationEvent | None:
        return await self._liqs.get()

    async def close(self) -> None:  # pragma: no cover - network
        await asyncio.gather(*(c.close() for c in self._conns))
        await self._agg_trades.put(None)
        await self._depths.put(None)
        await self._liqs.put(None)

