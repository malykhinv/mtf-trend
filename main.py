from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import schedule
import yaml
from dotenv import load_dotenv
from utils.ohlcv_fetcher import fetch_all_from_config


class DataCollector:
    """Collect OHLCV data for configured symbols."""

    def __init__(self, api_key: str, api_secret: str, config: dict) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.config = config

    def collect(self) -> Any:
        """Fetch recent OHLCV data for all configured symbols."""
        logging.info("Collecting market data")
        end = pd.Timestamp.utcnow()
        start = end - pd.Timedelta(days=1)
        fetch_all_from_config(self.config, start, end)
        return {}


class Screener:
    """Placeholder screener."""

    def screen(self, data: Any) -> Any:
        logging.info("Screening data")
        return data


class TrendFilter:
    """Placeholder trend filter."""

    def filter(self, data: Any) -> Any:
        logging.info("Applying trend filters")
        return data


class RiskManager:
    """Placeholder risk manager."""

    def evaluate(self, data: Any) -> None:
        logging.info("Evaluating risk")


def load_config(path: str | Path = "config.yaml") -> dict:
    """Load configuration from a YAML file."""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def init_logging(log_dir: str | Path) -> None:
    """Configure basic logging."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_file = Path(log_dir) / "app.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
    )


def scan_and_enter() -> None:
    """Run periodic scanning and entry logic."""
    logging.info("Running scan and entry logic")
    data = data_collector.collect()
    screened = screener.screen(data)
    trends = trend_filter.filter(screened)
    risk_manager.evaluate(trends)


def daily_equity_and_risk_check() -> None:
    """Perform daily equity and risk checks."""
    logging.info("Running daily equity and risk checks")


# Instances are created in main() and used by scheduled jobs
data_collector: Optional[DataCollector] = None
screener: Optional[Screener] = None
trend_filter: Optional[TrendFilter] = None
risk_manager: Optional[RiskManager] = None

def main() -> None:
    config = load_config()
    init_logging(config["data_paths"]["log_dir"])

    load_dotenv()
    api_key = os.getenv("API_KEY", config["api"]["api_key"])
    api_secret = os.getenv("API_SECRET", config["api"]["api_secret"])
    logging.info("API credentials loaded")

    global data_collector, screener, trend_filter, risk_manager
    data_collector = DataCollector(api_key, api_secret, config)
    screener = Screener()
    trend_filter = TrendFilter()
    risk_manager = RiskManager()

    schedule.every(5).minutes.do(scan_and_enter)
    schedule.every().day.at("00:00").do(daily_equity_and_risk_check)
    logging.info("Scheduler started")

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
