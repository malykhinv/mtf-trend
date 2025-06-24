from datetime import datetime
from typing import List, Dict

from domain.models.bar import Bar


class Loader:
    def __init__(self, binance):
        self.binance = binance

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
