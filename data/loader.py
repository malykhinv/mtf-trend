from datetime import datetime
from typing import List, Dict, Set

from config.constants import VOLUME_THRESHOLD_USDT
from data.binance_client import get_binance_client
from domain.models.bar import Bar
from domain.models.timeframe import Timeframe
from utils.logger import log
from utils.str_utils import market_symbol, clean_symbol


class Loader:
    def __init__(self):
        self.binance = get_binance_client()

    def fetch_ohlcv(self, symbol: str, timeframe: Timeframe, limit: int = 100) -> List[Bar]:
        symbol = market_symbol(symbol)

        raw = self.binance.fetch_ohlcv(symbol.upper(), timeframe=timeframe.value, limit=limit)

        bars = []
        for entry in raw:
            bar = Bar(
                timestamp=datetime.fromtimestamp(entry[0] / 1000),
                open=entry[1],
                high=entry[2],
                low=entry[3],
                close=entry[4],
                volume=entry[5]
            )
            bars.append(bar)
        return bars

    def fetch_multiple_timeframes(self,
                                  symbol: str,
                                  tf_list: List[Timeframe],
                                  limit: int = 100) -> Dict[Timeframe, List[Bar]]:
        return {tf: self.fetch_ohlcv(symbol, tf, limit=limit) for tf in tf_list}

    def get_filtered_symbols(self, quote_asset: str = "USDT", min_volume_usdt: float = VOLUME_THRESHOLD_USDT) -> list:
        markets = self.binance.load_markets()
        symbols = []

        for symbol, data in markets.items():
            if not data.get("active"):
                continue
            if not symbol.endswith(f"/{quote_asset}"):
                continue

            if "quoteVolume" in data and data["quoteVolume"] is not None:
                if data["quoteVolume"] < min_volume_usdt:
                    continue

            symbol = clean_symbol(symbol)
            symbols.append(symbol)
            log(f"{symbol} добавлен в список.")

        return sorted(symbols)
