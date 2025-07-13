def market_symbol(symbol: str) -> str:
    # TODO
    # if '/' not in symbol:
    #     return symbol.replace("USDT", "/USDT")
    # else:
    return symbol

def clean_symbol(symbol: str) -> str:
    return symbol.replace('/', '')