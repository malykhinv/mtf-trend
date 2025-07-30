from domain.models.setup_signal import SetupSignal
from domain.models.timeframe import Timeframe
from domain.models.setup_log_column import SetupLogColumn
from utils.tradingview import tradingview_link


HEADERS = [c.value for c in SetupLogColumn]


def row_from_signal(signal: SetupSignal, setup_tf: Timeframe) -> list:
    """Возвращает список значений для строки журнала в порядке ``HEADERS``."""
    return [
        signal.timestamp.isoformat(),
        signal.symbol,
        getattr(signal.side, 'value', str(signal.side)),
        getattr(signal.confidence, 'value', str(signal.confidence)),
        signal.entry,
        signal.sl,
        signal.tp,
        signal.rr,
        setup_tf.value,
        signal.price_growth_pct,
        signal.volume_growth_x,
        signal.atr_growth_pct,
        tradingview_link(signal.symbol),
        None,
        None,
    ]
