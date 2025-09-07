import asyncio
import random

from domain.models.enums import BotState
from domain.services.rest_client import RestClient
from domain.services.symbol_registry import SymbolRegistry
import constants


async def rest_pollers(rest: RestClient, registry: SymbolRegistry) -> None:
    """Poll REST endpoints for additional metrics."""

    async def _with_backoff(func, *args):
        delay = 1.0
        while True:
            try:
                return func(*args)
            except Exception:
                await asyncio.sleep(delay * random.uniform(0.8, 1.2))
                delay = min(delay * 2, 60.0)

    async def poll_oi() -> None:
        """Poll open interest and update delta percentage metric."""

        last_oi: dict[str, float] = {}
        watched_states = {
            BotState.WATCHING,
            BotState.CONFIRMING,
            BotState.ENTERED,
        }
        while True:
            for symbol in registry.all_symbols():
                state = registry.get(symbol)
                if state.state not in watched_states:
                    continue
                oi = await _with_backoff(rest.get_open_interest, symbol)
                prev = last_oi.get(symbol)
                delta_pct = ((oi - prev) / prev * 100.0) if prev else 0.0
                last_oi[symbol] = oi

                metrics = state.metrics
                metrics.delta_oi_pct = delta_pct
                registry.update(symbol, state)
            await asyncio.sleep(
                constants.REST_POLL_SEC_OI * random.uniform(0.8, 1.2)
            )

    async def poll_taker() -> None:
        """Poll taker buy/sell volumes and store them in metrics."""

        watched_states = {
            BotState.WATCHING,
            BotState.CONFIRMING,
            BotState.ENTERED,
        }
        while True:
            for symbol in registry.all_symbols():
                state = registry.get(symbol)
                if state.state not in watched_states:
                    continue
                buy, sell = await _with_backoff(rest.get_taker_ratio, symbol)
                metrics = state.metrics
                metrics.taker_buy_volume = buy
                metrics.taker_sell_volume = sell
                registry.update(symbol, state)
            await asyncio.sleep(
                constants.REST_POLL_SEC_TAKER * random.uniform(0.8, 1.2)
            )

    async def poll_premium() -> None:
        """Poll premium index percentage and update metric."""

        watched_states = {
            BotState.WATCHING,
            BotState.CONFIRMING,
            BotState.ENTERED,
        }
        while True:
            for symbol in registry.all_symbols():
                state = registry.get(symbol)
                if state.state not in watched_states:
                    continue
                premium = await _with_backoff(rest.get_premium_pct, symbol)
                metrics = state.metrics
                metrics.premium_pct = premium
                registry.update(symbol, state)
            await asyncio.sleep(
                constants.REST_POLL_SEC_PREMIUM * random.uniform(0.8, 1.2)
            )

    await asyncio.gather(poll_oi(), poll_taker(), poll_premium())
