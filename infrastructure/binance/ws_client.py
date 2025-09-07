from __future__ import annotations

import json
from collections import deque
from typing import Deque, List

import websockets

from constants import BINANCE_FAPI_WS
from domain.models.enums import Side
from domain.models.market_data import AggTrade, DepthSnapshot, LiquidationEvent


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
