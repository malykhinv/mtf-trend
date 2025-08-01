from __future__ import annotations

import logging
import os
import sqlite3
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
from utils.range_clusters import find_tight_range_clusters
from utils.breakout_signals import evaluate_breakout, Signal
from utils.futures_trader import FuturesTrader


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

    def collect(self, symbols: Optional[list[str]] = None) -> Any:
        """Fetch recent OHLCV data and metrics for the given symbols.

        Parameters
        ----------
        symbols:
            Optional list of symbols to collect data for. If not provided,
            ``config['symbols']`` is used.
        """
        logging.info("Collecting market data")
        end = pd.Timestamp.utcnow()
        start = end - pd.Timedelta(days=1)
        symbols = symbols or self.config.get("symbols", [])
        fetch_all_from_config({**self.config, "symbols": symbols}, start, end, timeframe="5m")

        data_dir = (
            Path(self.config.get("data_paths", {}).get("data_dir", "data"))
            / "raw_data"
        )
        results: dict[str, dict[str, Any]] = {}
        for symbol in symbols:
            ohlcv_file = data_dir / f"{symbol.replace('/', '')}_5m.csv"
            df = pd.DataFrame()
            if ohlcv_file.exists():
                df = pd.read_csv(ohlcv_file, parse_dates=["timestamp"])

            db_path = Path(self.config.get("data_paths", {}).get("data_dir", "data")) / "market_data.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(db_path)
            df.to_sql(symbol.replace('/', '_'), conn, if_exists='replace', index=False)
            conn.close()

            try:
                cvd = get_cvd(symbol, "5m")
                vol_delta = float(df["volume"].diff().iloc[-1]) if not df.empty else 0.0
            except Exception:
                logging.exception("Failed to compute CVD/volume delta for %s", symbol)
                cvd = pd.Series(dtype="float64")
                vol_delta = 0.0

            try:
                limit = len(df) if not df.empty else 100
                oi_hist = self.exchange.fetch_open_interest_history(
                    symbol, timeframe="5m", limit=limit
                )
                oi_df = pd.DataFrame(oi_hist)
                if not oi_df.empty:
                    oi_df["timestamp"] = pd.to_datetime(oi_df["timestamp"], unit="ms")
                    oi_col = next(
                        (
                            c
                            for c in [
                                "openInterest",
                                "openInterestAmount",
                                "openInterestValue",
                            ]
                            if c in oi_df.columns
                        ),
                        None,
                    )
                    if oi_col is not None:
                        oi_series = oi_df.set_index("timestamp")[oi_col].astype(float)
                        if not df.empty:
                            oi_series = oi_series.reindex(df["timestamp"]).fillna(method="ffill")
                        delta_oi = oi_series.diff().fillna(0)
                    else:
                        oi_series = pd.Series(dtype="float64")
                        delta_oi = pd.Series(dtype="float64")
                else:
                    oi_series = pd.Series(dtype="float64")
                    delta_oi = pd.Series(dtype="float64")
            except Exception:
                logging.exception(
                    "Failed to fetch open interest history for %s", symbol
                )
                oi_series = pd.Series(dtype="float64")
                delta_oi = pd.Series(dtype="float64")

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
                "open_interest": oi_series,
                "delta_oi": delta_oi,
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
    """Filter trading signals based on broader market trend.

    The filter inspects recent 5 minute candles for BTC and ETH to decide
    whether long or short signals should be allowed.  Long signals are
    rejected when either coin shows a consecutive down move for at least
    five minutes or when the latest close is below its 20 period EMA.
    Short signals are rejected when BTC or ETH has moved up for at least
    five consecutive minutes.  Only signals that pass these checks are
    returned.  The decisions are stored on the instance as ``allow_long``
    and ``allow_short`` for reuse elsewhere.
    """

    def filter(self, data: Any) -> Any:
        logging.info("Applying trend filters")

        # Load recent BTC and ETH candles to determine the broader trend
        candles = load_btc_eth_candles()
        btc = candles.get("BTC/USDT")
        eth = candles.get("ETH/USDT")

        # Determine simple trend characteristics
        btc_up = has_consecutive_move(btc, "up")
        btc_down = has_consecutive_move(btc, "down")
        eth_up = has_consecutive_move(eth, "up")
        eth_down = has_consecutive_move(eth, "down")
        btc_above = price_above_ema(btc)
        eth_above = price_above_ema(eth)

        allow_long = not (btc_down or eth_down or not btc_above or not eth_above)
        allow_short = not (btc_up or eth_up)
        self.allow_long = allow_long
        self.allow_short = allow_short

        filtered: dict[str, Any] = {}
        for symbol, signals in (data or {}).items():
            # ``signals`` may be a list of Signal objects or a single Signal.
            # We normalise to a list to simplify processing.
            sig_list = signals if isinstance(signals, list) else [signals]
            passed = []
            for sig in sig_list:
                direction = getattr(sig, "direction", None)
                if direction == "long" and not allow_long:
                    continue
                if direction == "short" and not allow_short:
                    continue
                passed.append(sig)
            if passed:
                # Preserve original structure (list vs single object)
                filtered[symbol] = passed if isinstance(signals, list) else passed[0]

        return filtered


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

    # Filter signals based on BTC/ETH trend
    filtered_signals = trend_filter.filter(signals_by_symbol)
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
    if trader is None:
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
