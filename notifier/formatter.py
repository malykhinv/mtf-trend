from domain.models.confidence import Confidence
from domain.models.setup_signal import SetupSignal


def format_message(signal: SetupSignal) -> str:
    emoji_map = {
        Confidence.WEAK: "🌑",
        Confidence.MODERATE: "🌓",
        Confidence.STRONG: "🌕"
    }

    emoji = emoji_map.get(signal.confidence, "🌑")

    msg = (
        f"{emoji} *{signal.symbol}*\n\n"
        f"{signal.text}\n\n"
        f"1:{round(signal.rr, 1)} {signal.side.value.upper()}"
    )

    if signal.is_order_signal:
        sl_pct = round(100 * abs(signal.entry - signal.sl) / signal.entry, 2)
        tp_pct = round(100 * abs(signal.tp - signal.entry) / signal.entry, 2)
        msg += f"\n\nSL {sl_pct}%\n"
        msg += f"TP {tp_pct}%"

    return msg
