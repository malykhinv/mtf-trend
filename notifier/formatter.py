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
        f"• {tf_labels[tf]} — тренд {signal.direction.upper()}"
        for tf in signal.confirmed_timeframes
    ])

    text = (
        f"{level_emojis[signal.confidence]} *{signal.symbol}* `{signal.direction.upper()}`\n"
        f"*RR:* `{signal.rr}`\n\n"
        f"*Подтверждение:*\n{tf_lines}\n\n"
        f"_{ago(signal.timestamp)}_"
    )

    return text
