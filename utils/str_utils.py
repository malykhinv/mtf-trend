def clean_symbol(symbol: str) -> str:
    """Очищает тикер от слешей и суффиксов."""
    return symbol.replace('/', '').split(':')[0]