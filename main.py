"""Main entry point for the trading bot.

This module loads configuration from ``config.yaml``, initializes
background processing loops, and exposes the loaded configuration via the
``CONFIG`` global so that other modules can access the settings.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict

import yaml

from risk import risk_control
from strategies.funding_arbitrage import get_thresholds
from ai.parameter_optimizer import periodic_optimization

# Global configuration dictionary that other modules can import.
CONFIG: Dict[str, Any] = {}


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
    CONFIG["thresholds"] = get_thresholds(CONFIG.get("thresholds", {}))


def initialize_bot() -> None:
    """Placeholder for any initialization logic using ``CONFIG``."""
    api_keys = CONFIG.get("api_keys", {})
    print(f"Initializing bot with API keys: {list(api_keys.keys())}")
    risk_control.configure(
        CONFIG.get("risk", {}), CONFIG.get("bot", {}).get("deposit_size")
    )


def start_processing_loops() -> None:
    """Start asynchronous processing loops."""

    threshold_version = 0

    def _update_thresholds(new: Dict[str, float]) -> None:
        nonlocal threshold_version
        if new:
            CONFIG.setdefault("thresholds", {}).update(new)
            threshold_version += 1

    async def price_loop() -> None:
        local_version = threshold_version
        while True:
            if local_version != threshold_version:
                local_version = threshold_version
                print(f"Thresholds updated: {CONFIG.get('thresholds')}")
            print(f"Processing with thresholds: {CONFIG.get('thresholds')}")
            await asyncio.sleep(CONFIG.get("bot", {}).get("poll_interval", 1))

    async def risk_loop() -> None:
        local_version = threshold_version
        while True:
            if local_version != threshold_version:
                local_version = threshold_version
                print("Risk loop acknowledged threshold update")
            print(f"Checking risk limits: {CONFIG.get('risk')}")
            await asyncio.sleep(CONFIG.get("bot", {}).get("poll_interval", 1))

    async def optimization_loop() -> None:
        await periodic_optimization(on_update=_update_thresholds)

    async def runner() -> None:
        await asyncio.gather(price_loop(), risk_loop(), optimization_loop())

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
