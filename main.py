"""Main entry point for the trading bot.

This module loads configuration from ``config.yaml`` and wires together the
different building blocks of the project.  It initialises exchange clients,
starts the strategy loops and exposes the loaded configuration via the
``CONFIG`` global so that other modules can access the settings.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List

import yaml

import exchanges
from exchanges.binance import BinanceExchange
from exchanges.bybit import BybitExchange
from risk import risk_control
from strategies import funding_arbitrage as strategy
from ai.parameter_optimizer import periodic_optimization
from utils.telegram import notify_close, notify_open

# Global configuration dictionary that other modules can import.
CONFIG: Dict[str, Any] = {}

# Mapping of exchange name to instantiated client.
CLIENTS: Dict[str, exchanges.BaseExchange] = {}

# Whitelisted symbols per exchange.
WHITELISTS: Dict[str, List[str]] = {}

# Track monitoring tasks for open positions so they can be cancelled on
# shutdown if necessary.
POSITION_TASKS: Dict[str, asyncio.Task] = {}


def load_config(path: str = "config.yaml") -> None:
    """Load YAML configuration into the global ``CONFIG`` variable.

    Parameters
    ----------
    path:
        Path to the configuration file. Defaults to ``config.yaml`` in the
        current working directory.
    """
    global CONFIG
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as f:
        CONFIG = yaml.safe_load(f) or {}
    CONFIG["thresholds"] = strategy.get_thresholds(
        CONFIG.get("thresholds", {})
    )


def initialize_bot() -> None:
    """Configure exchange clients and risk management."""

    api_keys = CONFIG.get("api_keys", {})
    print(f"Initializing bot with API keys: {list(api_keys.keys())}")

    # Instantiate exchange clients if credentials are provided.  Missing keys
    # simply result in the respective client not being created which keeps the
    # bot operational for the remaining exchanges.
    binance_key = api_keys.get("binance")
    binance_secret = api_keys.get("secret") or api_keys.get("binance_secret")
    if binance_key and binance_secret:
        CLIENTS["binance"] = BinanceExchange(binance_key, binance_secret)

    bybit_key = api_keys.get("bybit")
    bybit_secret = api_keys.get("bybit_secret")
    if bybit_key and bybit_secret:
        CLIENTS["bybit"] = BybitExchange(bybit_key, bybit_secret)

    # Load whitelists per exchange.  ``bot.whitelist`` can either be a mapping
    # of exchange names to symbol lists or a simple list applied to all
    # configured exchanges.
    bot_cfg = CONFIG.get("bot", {})
    wl_cfg = bot_cfg.get("whitelist", {})
    if isinstance(wl_cfg, dict):
        WHITELISTS.update({k: list(v) for k, v in wl_cfg.items()})
    else:
        symbols = list(wl_cfg) if isinstance(wl_cfg, list) else []
        for name in CLIENTS:
            WHITELISTS[name] = symbols

    # Configure risk management based on the loaded configuration.
    risk_control.configure(CONFIG.get("risk", {}), bot_cfg.get("deposit_size"))


def start_processing_loops() -> None:
    """Start the strategy, risk and optimisation loops."""

    poll_interval = CONFIG.get("bot", {}).get("poll_interval", 5)

    def _update_thresholds(new: Dict[str, float]) -> None:
        if new:
            CONFIG.setdefault("thresholds", {}).update(new)

    async def monitor_position(exchange_name: str, symbol: str, quantity: float) -> None:
        """Monitor an open position until exit conditions trigger."""
        exchanges._current = CLIENTS[exchange_name]
        position_id = f"{exchange_name}:{symbol}"
        try:
            await strategy.monitor_neutral_position(
                symbol,
                quantity,
                CONFIG.get("thresholds", {}),
                poll_interval,
                position_id=position_id,
            )
        except Exception as exc:
            notify_close(
                position_id,
                f"Error on {exchange_name} {symbol}: {exc}",
            )
            raise
        else:
            entry = strategy._positions.get(symbol, {})
            pnl = entry.get("pnl", 0.0)
            reasons = entry.get("exit_reasons")
            notify_close(
                position_id,
                f"Closed {symbol} on {exchange_name} PnL:{pnl} reasons:{reasons}",
            )
        finally:
            POSITION_TASKS.pop(position_id, None)
            strategy._positions.pop(symbol, None)

    async def scan_loop() -> None:
        """Continuously scan markets for entry opportunities."""
        while True:
            thresholds = CONFIG.get("thresholds", {})
            trade_value = thresholds.get("min_trade_size", 0.0) or 1.0

            for name, client in CLIENTS.items():
                exchanges._current = client
                for symbol in WHITELISTS.get(name, []):
                    if risk_control.is_paused() or risk_control.is_symbol_open(symbol):
                        continue
                    try:
                        base_metrics = await strategy.get_market_metrics(symbol, 1.0)
                    except Exception as exc:
                        print(f"Metrics error {name} {symbol}: {exc}")
                        continue
                    price = base_metrics.futures_price
                    quantity = trade_value / price if price else 0.0
                    try:
                        metrics = await strategy.get_market_metrics(symbol, quantity)
                    except Exception as exc:
                        print(f"Metrics error {name} {symbol}: {exc}")
                        continue

                    if strategy.check_entry_conditions(
                        symbol, quantity, metrics, thresholds
                    ):
                        try:
                            await strategy.open_neutral_position(symbol, quantity)
                            notify_open(
                                f"{name}:{symbol}",
                                f"Opened {symbol} on {name} qty {quantity}",
                            )
                            task = asyncio.create_task(
                                monitor_position(name, symbol, quantity)
                            )
                            POSITION_TASKS[f"{name}:{symbol}"] = task
                        except Exception as exc:
                            print(f"Open error {name} {symbol}: {exc}")
            await asyncio.sleep(poll_interval)

    async def risk_loop() -> None:
        """Periodically check whether trading should be paused."""
        while True:
            if risk_control.is_paused():
                print("Trading paused due to risk limits")
            await asyncio.sleep(poll_interval)

    async def optimisation_loop() -> None:
        await periodic_optimization(on_update=_update_thresholds)

    async def runner() -> None:
        tasks = [
            asyncio.create_task(scan_loop()),
            asyncio.create_task(risk_loop()),
            asyncio.create_task(optimisation_loop()),
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(runner())


def main() -> None:
    load_config()
    initialize_bot()
    start_processing_loops()


if __name__ == "__main__":  # pragma: no cover - script entry point
    try:
        main()
    except KeyboardInterrupt:
        print("Bot stopped.")
