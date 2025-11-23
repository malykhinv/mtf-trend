from dataclasses import dataclass, replace
from datetime import datetime

from crypto_screener.config.config import cfg
from crypto_screener.domain.models.context import Context
from crypto_screener.utils.time import utc_now


@dataclass(frozen=True)
class FuturesSymbol:
    symbol: str
    listing_time: datetime
    volume_usdt_24h: float
    trades_24h: int
    context: Context = Context.F



def set_contexts(
        symbols: list[FuturesSymbol],
        listing_period_days: int,
) -> list[FuturesSymbol]:
    symbols_list = list(symbols)
    btc_symbol = next((item for item in symbols_list if item.symbol.startswith("BTC")), None)
    btc_volume = btc_symbol.volume_usdt_24h if btc_symbol else 0
    btc_trades = btc_symbol.trades_24h if btc_symbol else 0
    now = utc_now()

    enriched: list[FuturesSymbol] = []
    for symbol in symbols_list:
        listing_age_days = (now - symbol.listing_time).days
        if symbol.trades_24h > btc_trades or symbol.volume_usdt_24h > btc_volume:
            context = Context.A
        elif symbol.volume_usdt_24h > cfg.CONTEXT_B_VOLUME_MIN:
            context = Context.B
        elif symbol.trades_24h > 0.5 * btc_trades:
            context = Context.C
        elif listing_age_days <= listing_period_days:
            context = Context.D
        elif symbol.trades_24h > cfg.CONTEXT_E_TRADES_MIN:
            context = Context.E
        else:
            context = Context.F

        enriched.append(replace(symbol, context=context))

    return enriched
