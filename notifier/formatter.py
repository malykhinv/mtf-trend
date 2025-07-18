from domain.models.confidence import Confidence
from domain.models.setup_signal import SetupSignal


def format_message(signal: SetupSignal) -> str:
    emoji_map = {
        Confidence.WEAK: "🌑",
        Confidence.MODERATE: "🌓",
        Confidence.STRONG: "🌕"
    }

    emoji = emoji_map.get(signal.confidence, "🌑")

    msg = f"{emoji} *{signal.symbol}*\n\n"

    if signal.is_order_signal:
        sl_pct = round(100 * abs(signal.entry - signal.sl) / signal.entry, 2)
        tp_pct = round(100 * abs(signal.tp - signal.entry) / signal.entry, 2)
        msg += f"\n\nSL {sl_pct}%"
        msg += f"\nTP {tp_pct}%"
        msg += f"\n\n1 : {round(signal.rr, 1)}"


    return msg
