from json import loads
from typing import Any, Iterable
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from crypto_screener.domain.models.symbol import (
    FuturesSymbol,
    assign_capitalizations,
    extract_base_symbol,
)


# region Private.
def _load_markets(page: int) -> list[dict[str, Any]]:
    params = urlencode({
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": 250,
        "page": page,
        "sparkline": "false",
    })
    request = Request(f"https://api.coingecko.com/api/v3/coins/markets?{params}")
    with urlopen(request, timeout=10) as response:
        return loads(response.read().decode())


# endregion

def fetch_market_caps(base_symbols: Iterable[str]) -> dict[str, float]:
    market_caps: dict[str, float] = {}
    remaining = set(base_symbols)
    page = 1

    while remaining and page <= 20:
        try:
            markets = _load_markets(page)
        except (URLError, TimeoutError, ValueError):
            break

        if not markets:
            break

        for market in markets:
            symbol = str(market.get("symbol", "")).lower()
            if symbol not in remaining:
                continue

            market_cap = market.get("market_cap")
            if market_cap is None:
                continue

            market_caps[symbol] = max(float(market_cap), market_caps.get(symbol, 0))

        remaining = {symbol for symbol in remaining if symbol not in market_caps}
        page += 1

    return market_caps


def enrich_symbols_capitalization(symbols: Iterable[FuturesSymbol]) -> list[FuturesSymbol]:
    symbols_list = list(symbols)
    base_symbols = {extract_base_symbol(symbol.symbol) for symbol in symbols_list}
    market_caps = fetch_market_caps(base_symbols)
    return assign_capitalizations(symbols_list, market_caps)
