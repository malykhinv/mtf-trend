def tradingview_link(symbol: str) -> str:
    """Возвращает ссылку на Superchart TradingView для указанной монеты."""
    base = symbol.replace('USDT', '')
    return f"https://www.tradingview.com/chart/?symbol=BINANCE%3A{base}USDT"

