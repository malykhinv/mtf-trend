from enum import Enum

class SetupLogColumn(str, Enum):
    """Названия столбцов в журнале сетапов."""

    TIMESTAMP = 'Timestamp'
    SYMBOL = 'Symbol'
    SIDE = 'Side'
    CONFIDENCE = 'Confidence'
    ENTRY = 'Entry'
    SL = 'SL'
    TP = 'TP'
    RR = 'RR'
    TF = 'TF'
    PRICE_GROWTH_PCT = 'price_growth_pct'
    VOLUME_GROWTH_X = 'volume_growth_x'
    ATR_GROWTH_PCT = 'atr_growth_pct'
    TRADINGVIEW = 'tradingview'
    MAIN_HIGH_CROSSED = 'main_high_crossed'
    CORRECTION_LOW_CROSSED = 'correction_low_crossed'

