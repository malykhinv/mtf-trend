from domain.models.setup_signal import SetupSignal
from utils.time import ago


def format_signal_detailed(signal: SetupSignal) -> str:
    tf_labels = {
        "1d": "1D",
        "4h": "4H",
        "1h": "1H",
        "15m": "15M"
    }

    level_emojis = {
        "low": "🌘",
        "medium": "🌓",
        "high": "🌖"
    }

    tf_lines = "\n".join([
        f"{tf_labels[tf].lower()} {signal.direction}"
        for tf in signal.confirmed_timeframes
    ])

    text = (
        f"{level_emojis[signal.confidence]} *{signal.symbol}* {signal.direction.upper()}\n\n"
        f"{tf_lines}\n\n"
        f"1 : {signal.rr}"
    )

    return text
