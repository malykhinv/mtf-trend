from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
from zoneinfo import ZoneInfo

from config.constants import VOLUME_THRESHOLD_USDT
from data.binance_client import get_binance_client
from domain.models.bar import Bar
from domain.models.mtf_profile import MTFProfile
from domain.models.timeframe import Timeframe
from utils.logger import log
from utils.str_utils import market_symbol, clean_symbol


class Loader:
    def __init__(self):
        self.binance = get_binance_client()

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

    def fetch_ohlcv(
            self,
            symbol: str,
            timeframe: Timeframe,
            limit: int = 100,
            to_time: Optional[datetime] = None
    ) -> List[Bar]:
        symbol = market_symbol(symbol)

        since = None
        if to_time:
            # Переводим datetime в миллисекунды
            since = int((to_time - timedelta(minutes=limit * self._timeframe_minutes(timeframe))).timestamp() * 1000)

        raw = self.binance.fetch_ohlcv(
            symbol.upper(),
            timeframe=timeframe.value,
            since=since,
            limit=limit
        )

        bars = []
        for entry in raw:
            belgrade_tz = ZoneInfo("Europe/Belgrade")
            ts = datetime.fromtimestamp(entry[0] / 1000, tz=timezone.utc).astimezone(belgrade_tz)
            if to_time and ts > to_time:
                continue  # Отсекаем бары строго после to_time (редкий случай)
            bar = Bar(
                timestamp=ts,
                open=entry[1],
                high=entry[2],
                low=entry[3],
                close=entry[4],
                volume=entry[5]
            )
            bars.append(bar)

        return bars

    def fetch_ohlcv_by_tfs(
            self,
            symbol: str,
            tfs: MTFProfile,
            limit: int = 100,
            to_time: Optional[datetime] = None
    ) -> Dict[Timeframe, List[Bar]]:
        return {tf: self.fetch_ohlcv(symbol, tf, limit=limit, to_time=to_time) for tf in tfs}

    @staticmethod
    def _timeframe_minutes(timeframe: Timeframe) -> int:
        """Возвращает количество минут для данного таймфрейма."""
        tf_map = {
            "1m": 1,
            "3m": 3,
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "1h": 60,
            "2h": 120,
            "4h": 240,
            "1d": 1440
        }
        return tf_map.get(timeframe.value, 1)
