from datetime import datetime
from typing import List, Dict

from config.settings.constants import VOLUME_THRESHOLD_USDT
from data.binance_client import get_binance_client
from domain.models.bar import Bar
from utils.logger import log


class Loader:
    def __init__(self):
        self.binance = get_binance_client()

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> List[Bar]:
        if '/' not in symbol:
            symbol = symbol.replace("USDT", "/USDT")

        raw = self.binance.fetch_ohlcv(symbol.upper(), timeframe=timeframe, limit=limit)

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

    def fetch_multiple_timeframes(self, symbol: str, tf_map: Dict[str, str], limit: int = 100) -> Dict[str, List[Bar]]:
        return {
            tf: self.fetch_ohlcv(symbol, tf_map[tf], limit=limit)
            for tf in tf_map
        }

    def get_filtered_symbols(self, quote_asset: str = "USDT", min_volume_usdt: float = VOLUME_THRESHOLD_USDT) -> list:
        log("Загружаю список рынков с Binance.")
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

            symbol_code = symbol.replace("/", "")
            symbols.append(symbol_code)
            log(f"{symbol_code} добавлен в список.")

        return sorted(symbols)
