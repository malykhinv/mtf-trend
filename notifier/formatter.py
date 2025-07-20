from domain.models.confidence import Confidence
from domain.models.setup_signal import SetupSignal
from utils.float_utils import is_defined


def format_message(signal: SetupSignal) -> str:
    emoji_map = {
        Confidence.WEAK: "🌑",
        Confidence.MODERATE: "🌓",
        Confidence.STRONG: "🌕"
    }

    emoji = emoji_map.get(signal.confidence, "🌑")

    url = f"https://www.tradingview.com/symbols/{signal.symbol.replace('USDT', '')}USDT.P/"
    linked_symbol = f"[{signal.symbol}]({url})"

    msg = f"{emoji} {linked_symbol} +{round(signal.price_growth_pct)}%\n\n"

    details = []
    if is_defined(signal.atr_growth_pct):
        details.append(f"ATR +{round(signal.atr_growth_pct)}%")
    if is_defined(signal.volume_growth_x):
        details.append(f"Объём ×{round(signal.volume_growth_x)}")

    if details:
        msg += "\n".join(details)

    if signal.is_order_signal:
        sl_pct = round(100 * abs(signal.entry - signal.sl) / signal.entry, 2)
        tp_pct = round(100 * abs(signal.tp - signal.entry) / signal.entry, 2)
        msg += f"\n\nSL {sl_pct}%"
        msg += f"\nTP {tp_pct}%"
        msg += f"\n\n1 : {round(signal.rr, 1)}"

    return msg
