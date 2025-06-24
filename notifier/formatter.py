from domain.models.setup_signal import SetupSignal


def format_message(signal: SetupSignal) -> str:
    emoji_map = {
        "low": "🌑",
        "medium": "🌓",
        "high": "🌕"
    }

    emoji = emoji_map.get(signal.confidence, "🌑")
    tf_block = "\n".join(f"{tf} {signal.direction}" for tf in signal.confirmed_timeframes)

    msg = (
        f"{emoji} *{signal.symbol}*\n\n"
        f"{signal.text}\n\n"
        f"1:{round(signal.rr, 1)} {signal.direction.upper()}\n\n"
        f"`{tf_block}`"
    )

    if signal.is_order_signal:
        sl_pct = round(100 * abs(signal.entry - signal.sl) / signal.entry, 2)
        tp_pct = round(100 * abs(signal.tp - signal.entry) / signal.entry, 2)
        msg += f"\n\nSL {sl_pct}%\n"
        msg += f"TP {tp_pct}%"

    return msg
