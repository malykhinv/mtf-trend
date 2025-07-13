from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
from zoneinfo import ZoneInfo

from config.constants import VOLUME_THRESHOLD_USDT, FLOAT_UNDEFINED
from data.binance_client import get_binance_client
from domain.models.bar import Bar
from domain.models.mtf_profile import MTFProfile
from domain.models.timeframe import Timeframe
from utils.logger import log
from utils.str_utils import clean_symbol


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

    def fetch_ohlcvi(
            self,
            symbol: str,
            timeframe: Timeframe,
            limit: int = 100,
            to_time: Optional[datetime] = None,
            has_oi: bool = False
    ) -> List[Bar]:
        since = None
        if to_time:
            since = int((to_time - timedelta(minutes=limit * self._timeframe_minutes(timeframe))).timestamp() * 1000)

        raw = self.binance.fetch_ohlcv(
            symbol,
            timeframe=timeframe.value,
            since=since,
            limit=limit
        )

        # OI
        if has_oi:
            raw_oi = self.fetch_oi(
                symbol=symbol,
                timeframe=timeframe,
                since=since,
                limit=limit
            )
            oi_values = [float(entry["sumOpenInterest"]) for entry in raw_oi]
        else:
            oi_values = [FLOAT_UNDEFINED for _ in range(len(raw))]

        bars = []
        for i, entry in enumerate(raw):
            belgrade_tz = ZoneInfo("Europe/Belgrade")
            ts = datetime.fromtimestamp(entry[0] / 1000, tz=timezone.utc).astimezone(belgrade_tz)
            if to_time and ts > to_time:
                continue
            oi_value = oi_values[i] if i < len(oi_values) else FLOAT_UNDEFINED
            bar = Bar(
                timestamp=ts,
                open=entry[1],
                high=entry[2],
                low=entry[3],
                close=entry[4],
                volume=entry[5],
                oi=oi_value
            )
            bars.append(bar)

        return bars

    def fetch_ohlcvi_by_tfs(
            self,
            symbol: str,
            tfs: MTFProfile,
            limit: int = 100,
            to_time: Optional[datetime] = None,
    ) -> Dict[Timeframe, List[Bar]]:
        return {tf: self.fetch_ohlcvi(symbol, tf, limit=limit, to_time=to_time, has_oi=tf == tfs.setup) for tf in tfs}

    def fetch_oi(
            self,
            symbol: str,
            timeframe: Timeframe,
            limit: int = 250,
            since: Optional[int] = None
    ):
        params = {
            'symbol': symbol,
            'period': timeframe.value,
            'limit': limit
        }
        if since:
            params['since'] = since

        return self.binance.fapidata_get_openinteresthist(params)

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
