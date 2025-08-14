from domain.models.signal import Signal
from utils.tradingview import tradingview_link


def format_message(signal: Signal) -> str:
    url = tradingview_link(signal.symbol)
    linked_symbol = f"[{signal.symbol}]({url})"
    return f"{linked_symbol} {signal.timeframe.value}"
