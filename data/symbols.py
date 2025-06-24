import ccxt

from utils.logger import log


def get_filtered_symbols(quote_asset: str = "USDT", min_volume_usdt: float = 50_000_000) -> list:
    exchange = ccxt.binance({"enableRateLimit": True})
    markets = exchange.load_markets()
    symbols = []

    for symbol, data in markets.items():
        if not data.get("active"):
            continue
        if not symbol.endswith(f"/{quote_asset}"):
            continue

        if "quoteVolume" in data and data["quoteVolume"] is not None:
            if data["quoteVolume"] < min_volume_usdt:
                continue

        symbol = symbol.replace("/", "")
        log(f"{symbol} added")
        symbols.append(symbol)

    return sorted(symbols)
