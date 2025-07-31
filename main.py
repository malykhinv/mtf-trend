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
import ccxt
from utils.ohlcv_fetcher import fetch_all_from_config
from utils.trade_logger import daily_summary, send_telegram_message
from utils.market_analysis import (
    load_btc_eth_candles,
    has_consecutive_move,
    price_above_ema,
)
from utils.cvd import get_cvd
from utils.futures_screener import screen_futures


class DataCollector:
    """Collect OHLCV data for configured symbols."""

    def __init__(self, api_key: str, api_secret: str, config: dict) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.config = config
        exchange_name = config.get("api", {}).get("futures_exchange", "binanceusdm")
        exchange_class = getattr(ccxt, exchange_name)
        self.exchange = exchange_class(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
            }
        )

    def collect(self) -> Any:
        """Fetch recent OHLCV data and metrics for all configured symbols."""
        logging.info("Collecting market data")
        end = pd.Timestamp.utcnow()
        start = end - pd.Timedelta(days=1)
        fetch_all_from_config(self.config, start, end, timeframe="5m")

        data_dir = (
            Path(self.config.get("data_paths", {}).get("data_dir", "data"))
            / "raw_data"
        )
        results: dict[str, dict[str, Any]] = {}
        for symbol in self.config.get("symbols", []):
            ohlcv_file = data_dir / f"{symbol.replace('/', '')}_5m.csv"
            df = pd.DataFrame()
            if ohlcv_file.exists():
                df = pd.read_csv(ohlcv_file, parse_dates=["timestamp"])

            try:
                cvd = get_cvd(symbol, "5m")
                vol_delta = float(df["volume"].diff().iloc[-1]) if not df.empty else 0.0
            except Exception:
                logging.exception("Failed to compute CVD/volume delta for %s", symbol)
                cvd = pd.Series(dtype="float64")
                vol_delta = 0.0

            try:
                oi_info = self.exchange.fetch_open_interest(symbol)
                open_interest = float(
                    oi_info.get("openInterestAmount")
                    or oi_info.get("openInterest")
                    or oi_info.get("openInterestValue")
                    or 0.0
                )
            except Exception:
                logging.exception("Failed to fetch open interest for %s", symbol)
                open_interest = 0.0

            try:
                fr = self.exchange.fetch_funding_rate(symbol)
                funding_rate = float(
                    fr.get("fundingRate")
                    or fr.get("info", {}).get("fundingRate", 0.0)
                )
            except Exception:
                logging.exception("Failed to fetch funding rate for %s", symbol)
                funding_rate = 0.0

            results[symbol] = {
                "ohlcv": df,
                "cvd": cvd,
                "volume_delta": vol_delta,
                "open_interest": open_interest,
                "funding_rate": funding_rate,
            }

        return results


class Screener:
    """Wrapper around :func:`utils.futures_screener.screen_futures`."""

    def __init__(self, exchange_name: str = "binanceusdm") -> None:
        self.exchange_name = exchange_name

    def screen(self, data: Any | None = None, max_symbols: int = 10) -> list[str]:
        """Return a list of symbols matching the screener criteria."""
        logging.info("Screening futures markets")
        metrics = screen_futures(
            exchange_name=self.exchange_name, max_positions=max_symbols
        )
        return [m.symbol for m in metrics]


class TrendFilter:
    """Placeholder trend filter."""

    def filter(self, data: Any) -> Any:
        logging.info("Applying trend filters")
        return data


from utils.risk import RiskManager


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
    if isinstance(screened, dict):
        symbols = list(screened.keys())[:10]
        filtered_data = {s: screened[s] for s in symbols}
    else:
        symbols = list(screened)[:10]
        filtered_data = {s: data.get(s) for s in symbols if s in data}
    global selected_symbols
    selected_symbols = symbols
    trends = trend_filter.filter(filtered_data)
    for symbol in filtered_data:
        logging.debug("Prepared data for %s", symbol)

    candles = load_btc_eth_candles()
    btc = candles.get("BTC/USDT")
    eth = candles.get("ETH/USDT")
    btc_up = has_consecutive_move(btc, "up")
    btc_down = has_consecutive_move(btc, "down")
    eth_up = has_consecutive_move(eth, "up")
    eth_down = has_consecutive_move(eth, "down")
    btc_above = price_above_ema(btc)

    global open_long, open_short
    if open_long and (btc_down or eth_down or not btc_above):
        logging.info("Conditions violated for long; cancelling long position")
        open_long = False
    if open_short and (btc_up or eth_up or btc_above):
        logging.info("Conditions violated for short; cancelling short position")
        open_short = False


def daily_equity_and_risk_check() -> None:
    """Perform daily equity and risk checks and send summary."""
    logging.info("Running daily equity and risk checks")
    summary = daily_summary()
    msg = (
        f"Daily summary\n"
        f"Winrate: {summary['winrate']:.2%}\n"
        f"Avg RR: {summary['avg_rr']:.2f}\n"
        f"Equity change: {summary['equity_change']:.2f}"
    )
    logging.info(msg)
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if token and chat_id:
        send_telegram_message(token, chat_id, msg)


# Instances are created in main() and used by scheduled jobs
data_collector: Optional[DataCollector] = None
screener: Optional[Screener] = None
trend_filter: Optional[TrendFilter] = None
risk_manager: Optional[RiskManager] = None
open_long: bool = False
open_short: bool = False
selected_symbols: list[str] = []

def main() -> None:
    config = load_config()
    init_logging(config["data_paths"]["log_dir"])

    load_dotenv()
    api_key = os.getenv("API_KEY", config["api"]["api_key"])
    api_secret = os.getenv("API_SECRET", config["api"]["api_secret"])
    logging.info("API credentials loaded")

    def fetch_balance() -> float:
        exchange = ccxt.binance({"apiKey": api_key, "secret": api_secret})
        try:
            balance = exchange.fetch_balance()
            return balance["total"].get("USDT", 0.0)
        except Exception:
            logging.exception("Failed to fetch balance")
            return 0.0

    global data_collector, screener, trend_filter, risk_manager
    data_collector = DataCollector(api_key, api_secret, config)
    screener = Screener(
        exchange_name=config.get("api", {}).get("futures_exchange", "binanceusdm")
    )
    trend_filter = TrendFilter()
    risk_manager = RiskManager(fetch_balance)

    schedule.every(5).minutes.do(scan_and_enter)
    schedule.every().day.at("00:00").do(daily_equity_and_risk_check)
    logging.info("Scheduler started")

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
