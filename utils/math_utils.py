from config.constants import FLOAT_UNDEFINED


def calculate_atr(bars):
    if len(bars) > 0:
        return sum(abs(b.high - b.low) for b in bars) / len(bars)
    else:
        return FLOAT_UNDEFINED