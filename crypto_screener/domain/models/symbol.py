from dataclasses import dataclass, replace
from datetime import datetime
from typing import Iterable, Mapping

from crypto_screener.config import ppo_cfg
from crypto_screener.domain.models.capitalization import Capitalization
from crypto_screener.domain.models.context import Context
from crypto_screener.utils.time import utc_now


@dataclass(frozen=True)
class FuturesSymbol:
    symbol: str
    listing_time: datetime
    volume_usdt_24h: float
    trades_24h: int
    capitalization: Capitalization = Capitalization.LOW
    context: Context = Context.FROZEN


def extract_base_symbol(symbol: str) -> str:
    return symbol.split("/")[0].split(":")[0].lower()


def calculate_capitalization(market_cap: float) -> Capitalization:
    if market_cap >= ppo_cfg.CAPITALIZATION_HIGH_MIN:
        return Capitalization.HIGH
    if market_cap >= ppo_cfg.CAPITALIZATION_MIDDLE_MIN:
        return Capitalization.MIDDLE
    if market_cap >= ppo_cfg.CAPITALIZATION_LOW_MIN:
        return Capitalization.LOW
    return Capitalization.LOW


def assign_capitalizations(
        symbols: Iterable[FuturesSymbol],
        market_caps: Mapping[str, float],
) -> list[FuturesSymbol]:
    enriched: list[FuturesSymbol] = []
    for symbol in symbols:
        base_symbol = extract_base_symbol(symbol.symbol)
        market_cap = market_caps.get(base_symbol, 0)
        capitalization = calculate_capitalization(market_cap)
        enriched.append(replace(symbol, capitalization=capitalization))
    return enriched


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
        if listing_age_days <= listing_period_days:
            context = Context.LISTING
        elif symbol.capitalization == Capitalization.HIGH:
            if symbol.trades_24h > btc_trades and symbol.volume_usdt_24h > btc_volume:
                context = Context.HIGH_CAP_A
            elif symbol.volume_usdt_24h > ppo_cfg.CONTEXT_VOLUME_MIN:
                context = Context.MIDDLE_CAP_B
            elif symbol.trades_24h > 0.5 * btc_trades:
                context = Context.MIDDLE_CAP_C
            else:
                context = Context.FROZEN
        elif symbol.capitalization == Capitalization.MIDDLE:
            if symbol.trades_24h > btc_trades or symbol.volume_usdt_24h > btc_volume:
                context = Context.MIDDLE_CAP_A
            elif symbol.volume_usdt_24h > ppo_cfg.CONTEXT_VOLUME_MIN:
                context = Context.MIDDLE_CAP_B
            elif symbol.trades_24h > 0.5 * btc_trades:
                context = Context.MIDDLE_CAP_C
            else:
                context = Context.FROZEN
        else:
            if symbol.trades_24h > btc_trades or symbol.volume_usdt_24h > btc_volume:
                context = Context.LOW_CAP_A
            elif symbol.volume_usdt_24h > ppo_cfg.CONTEXT_VOLUME_MIN:
                context = Context.LOW_CAP_B
            elif symbol.trades_24h > 0.5 * btc_trades:
                context = Context.LOW_CAP_C
            elif symbol.trades_24h > ppo_cfg.CONTEXT_TRADES_MIN:
                context = Context.LOW_CAP_D
            else:
                context = Context.FROZEN

        enriched.append(replace(symbol, context=context))

    return enriched
