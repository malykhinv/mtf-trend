from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import ccxt

from utils.ohlcv_fetcher import fetch_all_from_config
from utils.cvd import get_cvd


class DataCollector:
    """Собирает свечные данные OHLCV для инструментов из настроек."""

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
        """Получает свежие свечные данные и метрики по указанным инструментам.

        Можно передать свой список символов. Если ничего не передавать,
        берём список из настроек.
        """
        logging.info("Collecting market data")
        end = pd.Timestamp.utcnow()
        start = end - pd.Timedelta(days=1)
        symbols = symbols or self.config.get("symbols", [])
        fetch_all_from_config({**self.config, "symbols": symbols}, start, end, timeframe="1m")

        data_dir = (
            Path(self.config.get("data_paths", {}).get("data_dir", "data"))
            / "raw_data"
        )
        results: dict[str, dict[str, Any]] = {}
        for symbol in symbols:
            ohlcv_file = data_dir / f"{symbol.replace('/', '')}_1m.csv"
            df = pd.DataFrame()
            if ohlcv_file.exists():
                df = pd.read_csv(ohlcv_file, parse_dates=["timestamp"])
                df.to_csv(data_dir / f"{symbol.replace('/', '')}.csv", index=False)

            db_path = Path(self.config.get("data_paths", {}).get("data_dir", "data")) / "market_data.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(db_path)
            df.to_sql(symbol.replace('/', '_'), conn, if_exists='replace', index=False)
            conn.close()

            try:
                cvd = get_cvd(symbol, "1m")
                vol_delta = (
                    float(df["volume"].astype(float).diff().iloc[-1])
                    if not df.empty
                    else 0.0
                )
            except Exception:
                logging.exception("Failed to compute CVD/volume delta for %s", symbol)
                cvd = pd.Series(dtype="float64")
                vol_delta = 0.0

            try:
                limit = len(df) if not df.empty else 100
                oi_hist = self.exchange.fetch_open_interest_history(
                    symbol, timeframe="1m", limit=limit
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
                            oi_series = oi_series.reindex(df["timestamp"]).fillna(method="ffill")  # type: ignore[call-overload]
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
