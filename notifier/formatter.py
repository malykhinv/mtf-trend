from domain.models.setup_signal import SetupSignal


def format_message(signal: SetupSignal) -> str:
    emoji_map = {
        "low": "🌑",
        "medium": "🌓",
        "high": "🌕"
    }

    emoji = emoji_map.get(signal.confidence, "🌑")
    tf_block = "\n".join(f"{tf} {signal.direction}" for tf in signal.confirmed_timeframes)

    return (
        f"{emoji} *{signal.symbol}*\n\n"
        f"1:{signal.rr} {signal.direction.upper()}\n"
        f"`{tf_block}`"
    )
