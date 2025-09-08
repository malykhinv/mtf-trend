from __future__ import annotations

import httpx

from constants import BINANCE_FAPI_REST, TAKER_RATIO_LIMIT, TAKER_RATIO_PERIOD


class RestClient:
    """REST requests to public Binance Futures endpoints."""

    _OPEN_INTEREST_EP = "/fapi/v1/openInterest"
    _TAKER_RATIO_EP = "/futures/data/takerlongshortRatio"
    _PREMIUM_EP = "/fapi/v1/premiumIndex"
    _TICKER_EP = "/fapi/v1/ticker/24hr"
    _BOOK_TICKER_EP = "/fapi/v1/ticker/bookTicker"
    _DEPTH_EP = "/fapi/v1/depth"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(base_url=BINANCE_FAPI_REST, timeout=10.0)

    async def get_open_interest(self, symbol: str) -> float:
        r = await self._client.get(self._OPEN_INTEREST_EP, params={"symbol": symbol})
        r.raise_for_status()
        data = r.json()
        return float(data["openInterest"])

    async def get_taker_ratio(self, symbol: str) -> tuple[float, float]:
        params = {
            "symbol": symbol,
            "period": TAKER_RATIO_PERIOD,
            "limit": TAKER_RATIO_LIMIT,
        }
        r = await self._client.get(self._TAKER_RATIO_EP, params=params)
        r.raise_for_status()
        data = r.json()
        if not data:
            return 0.0, 0.0
        item = data[0]
        return float(item["buyVol"]), float(item["sellVol"])

    async def get_premium_pct(self, symbol: str) -> float:
        r = await self._client.get(self._PREMIUM_EP, params={"symbol": symbol})
        r.raise_for_status()
        data = r.json()
        mark_price = float(data["markPrice"])
        index_price = float(data["indexPrice"])
        if index_price == 0:
            return 0.0
        return (mark_price / index_price - 1.0) * 100.0

    async def get_24h_stats(self, symbol: str) -> tuple[float, float]:
        r = await self._client.get(self._TICKER_EP, params={"symbol": symbol})
        r.raise_for_status()
        data = r.json()
        quote_volume = float(data["quoteVolume"])
        last_price = float(data["lastPrice"])
        return quote_volume, last_price

    async def fetch_all_tickers(self) -> list[tuple[str, float, float]]:
        r = await self._client.get(self._TICKER_EP)
        r.raise_for_status()
        data = r.json()

        tickers: list[tuple[str, float, float]] = []
        for item in data:
            symbol = item["symbol"]
            if "bidPrice" in item and "askPrice" in item:
                bid = float(item["bidPrice"])
                ask = float(item["askPrice"])
            else:
                r_book = await self._client.get(self._BOOK_TICKER_EP, params={"symbol": symbol})
                r_book.raise_for_status()
                book = r_book.json()
                bid = float(book.get("bidPrice", 0.0))
                ask = float(book.get("askPrice", 0.0))
            tickers.append((symbol, bid, ask))

        return tickers

    async def get_depth(self, symbol: str) -> tuple[tuple[float, float], ...]:
        r = await self._client.get(self._DEPTH_EP, params={"symbol": symbol, "limit": 10})
        r.raise_for_status()
        data = r.json()
        bids = tuple((float(p), float(q)) for p, q in data.get("bids", []))
        return bids

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def close(self) -> None:
        await self.aclose()
