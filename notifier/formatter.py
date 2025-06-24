from domain.models.setup_signal import SetupSignal


def format_signal(signal: SetupSignal) -> str:
    confidence_prefix = {
        "low": "🟡 Уровень: слабый",
        "medium": "🟠 Уровень: средний",
        "high": "🔴 Уровень: сильный"
    }.get(signal.confidence, "")

    tf_lines = "\n".join([f"• {tf.upper()} — подтверждён тренд" for tf in signal.confirmed_timeframes])

    return (
        f"Сигнал по {signal.symbol} — {signal.direction.upper()}\n"
        f"{confidence_prefix}\n"
        f"{tf_lines}\n"
        f"RR: {signal.rr}"
    )
