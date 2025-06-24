from domain.models.setup_signal import SetupSignal
from utils.time import ago


def format_signal_detailed(signal: SetupSignal) -> str:
    level_emojis = {
        "low": "🌘",
        "medium": "🌓",
        "high": "🌖"
    }

    tf_lines = "\n".join([f"{tf} {signal.direction}" for tf in signal.confirmed_timeframes])

    text = (
        f"{level_emojis[signal.confidence]} *{signal.symbol}* {signal.direction.upper()}\n\n"
        f"{tf_lines}\n\n"
        f"1 : {signal.rr}"
    )

    return text
