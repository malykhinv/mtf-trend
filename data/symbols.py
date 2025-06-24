import ccxt

def get_filtered_symbols(quote_asset: str = "USDT", min_volume_usd: float = 1_000_000) -> list:
    exchange = ccxt.binance({"enableRateLimit": True})
    markets = exchange.load_markets()
    symbols = []

    for symbol, data in markets.items():
        if not data.get("active"):
            continue
        if not symbol.endswith(f"/{quote_asset}"):
            continue

        if "quoteVolume" in data and data["quoteVolume"] is not None:
            if data["quoteVolume"] < min_volume_usd:
                continue

        symbols.append(symbol.replace("/", ""))

    return sorted(symbols)
