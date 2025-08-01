from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import schedule
import yaml
from dotenv import load_dotenv
import ccxt
from utils.market_analysis import load_btc_eth_candles
from utils.trade_logger import daily_summary, send_telegram_message
from utils.range_clusters import find_tight_range_clusters
from utils.breakout_signals import evaluate_breakout, Signal
from utils.futures_trader import FuturesTrader
from utils.risk import RiskManager

from data_collector import DataCollector
from screener import Screener
from trend_filter import TrendFilter

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


def scan_and_enter(
    avg_volume_mult: float = 1.5,
    delta_volume_mult: float = 2.0,
    tp1_rr: float = 1.5,
    tp2_rr: float = 3.0,
) -> None:
    """Run periodic scanning and entry logic.

    Parameters
    ----------
    avg_volume_mult, delta_volume_mult:
        Multipliers applied to average volume and volume change respectively
        when generating breakout signals.
    tp1_rr, tp2_rr:
        Risk-reward multiples for the first and second take profit levels.
    """
    logging.info("Running scan and entry logic")
    if screener is None or data_collector is None:
        return
    symbols = screener.screen()[:10]
    data = data_collector.collect(symbols)
    data = {s: data.get(s) for s in symbols if s in data}

    # Evaluate breakout signals for each symbol
    signals_by_symbol: dict[str, list[Signal]] = {}
    for symbol, info in data.items():
        logging.debug("Prepared data for %s", symbol)
        ohlcv = info.get("ohlcv")
        if ohlcv is None or ohlcv.empty:
            continue
        ohlcv = ohlcv.copy()
        if "timestamp" in ohlcv.columns:
            ohlcv["timestamp"] = pd.to_datetime(ohlcv["timestamp"])
            ohlcv = ohlcv.set_index("timestamp")
        clusters = find_tight_range_clusters(
            ohlcv.reset_index()[["timestamp", "high", "low", "close"]],
            atr_multiplier=0.5,
            min_bars=10,
            max_bars=30,
        )
        if clusters.empty:
            continue
        levels = clusters.iloc[[-1]][["high", "low"]]
        cvd = info.get("cvd", pd.Series(dtype="float64"))
        cvd = cvd.reindex(ohlcv.index).fillna(method="ffill").fillna(0)
        delta_oi = info.get("delta_oi", pd.Series(dtype="float64"))
        delta_oi = delta_oi.reindex(ohlcv.index).fillna(0)
        funding = float(info.get("funding_rate", 0.0))
        signals = evaluate_breakout(
            ohlcv[["open", "high", "low", "close", "volume"]],
            levels,
            cvd,
            delta_oi,
            pd.Series(dtype="float64"),
            funding,
            avg_volume_mult=avg_volume_mult,
            delta_volume_mult=delta_volume_mult,
            tp1_rr=tp1_rr,
            tp2_rr=tp2_rr,
        )
        if signals:
            signals_by_symbol[symbol] = signals

    # Filter signals based on BTC trend
    filtered_signals = (
        trend_filter.filter(signals_by_symbol) if trend_filter else signals_by_symbol
    )
    global selected_symbols
    selected_symbols = list(filtered_signals.keys())

    global open_long, open_short, trader
    if open_long and not getattr(trend_filter, "allow_long", True):
        logging.info("Conditions violated for long; cancelling long position")
        open_long = False
    if open_short and not getattr(trend_filter, "allow_short", True):
        logging.info("Conditions violated for short; cancelling short position")
        open_short = False
    if not risk_manager:
        return
    if trader is None and data_collector is not None:
        trader = FuturesTrader(
            data_collector.api_key,
            data_collector.api_secret,
            exchange_name=data_collector.config.get("api", {}).get(
                "futures_exchange", "binanceusdm"
            ),
            risk_manager=risk_manager,
        )

    for symbol, sigs in filtered_signals.items():
        sig_list = sigs if isinstance(sigs, list) else [sigs]
        for sig in sig_list:
            if not risk_manager.can_open_trade():
                continue
            try:
                trade_id, size = risk_manager.open_trade(sig.entry, sig.stop)
            except ValueError:
                continue
            side = "buy" if sig.direction == "long" else "sell"
            executed = trader.place_limit_maker_order(
                symbol,
                side,
                size,
                sig.entry,
                tp=sig.tp1,
                sl=sig.stop,
                trade_id=trade_id,
            )
            if not executed:
                executed = trader.place_market_order(
                    symbol,
                    side,
                    size,
                    tp=sig.tp1,
                    sl=sig.stop,
                    trade_id=trade_id,
                )
            if not executed:
                risk_manager.close_trade(trade_id, 0.0)
                continue
            if sig.direction == "long":
                open_long = True
                open_short = False
            else:
                open_short = True
                open_long = False
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
trader: Optional[FuturesTrader] = None
open_long: bool = False
open_short: bool = False
selected_symbols: list[str] = []

def main() -> None:
    config = load_config()
    init_logging(config["data_paths"]["log_dir"])

    load_dotenv()
    try:
        api_key = os.environ["API_KEY"]
        api_secret = os.environ["API_SECRET"]
    except KeyError as err:
        raise RuntimeError(
            f"Missing environment variable: {err.args[0]}"
        ) from err
    logging.info("API credentials loaded")

    def fetch_balance() -> float:
        exchange = ccxt.binance({"apiKey": api_key, "secret": api_secret})
        try:
            balance = exchange.fetch_balance()
            return balance["total"].get("USDT", 0.0)
        except Exception:
            logging.exception("Failed to fetch balance")
            return 0.0

    global data_collector, screener, trend_filter, risk_manager, trader
    data_collector = DataCollector(api_key, api_secret, config)
    screener = Screener(
        exchange_name=config.get("api", {}).get("futures_exchange", "binanceusdm")
    )
    trend_filter = TrendFilter()
    risk_manager = RiskManager(fetch_balance, max_open_trades=10)
    trader = None

    avg_mult = config.get("strategy", {}).get("avg_volume_mult", 1.5)
    delta_mult = config.get("strategy", {}).get("delta_volume_mult", 2.0)
    tp1_rr = config.get("strategy", {}).get("tp1_rr", 1.5)
    tp2_rr = config.get("strategy", {}).get("tp2_rr", 3.0)
    schedule.every(5).minutes.do(
        scan_and_enter,
        avg_volume_mult=avg_mult,
        delta_volume_mult=delta_mult,
        tp1_rr=tp1_rr,
        tp2_rr=tp2_rr,
    )
    schedule.every().day.at("00:00").do(daily_equity_and_risk_check)
    logging.info("Scheduler started")

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
