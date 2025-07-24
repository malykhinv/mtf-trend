from bisect import bisect_right
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any

from config.constants import VOLUME_THRESHOLD_USDT, FLOAT_UNDEFINED
from data.binance_client import get_binance_client
from data.db import save_oi, load_oi
from domain.models.bar import Bar
from domain.models.mtf_profile import MTFProfile
from domain.models.timeframe import Timeframe
from utils.math_utils import calculate_atr
from utils.str_utils import clean_symbol
from utils.time_utils import to_local_dt


class Loader:
    """
    Класс загрузки рыночных данных с Binance: тикеры, свечи (OHLCV), фьючерсный OI. 
    Предоставляет функции для получения и фильтрации финансовых инструментов, OHLCV и OI по разным таймфреймам.
    """
    def __init__(self) -> None:
        """
        Инициализирует клиент Binance через ccxt.
        """
        self.binance = get_binance_client()

    def get_filtered_symbols(self, quote_asset: str = "USDT", min_volume_usdt: float = VOLUME_THRESHOLD_USDT) -> List[str]:
        """
        Возвращает список фьючерсных тикеров с фильтрацией по объему и статусу.
        Args:
            quote_asset (str): Котируемая валюта (по умолчанию 'USDT').
            min_volume_usdt (float): Минимальный объём торгов в USDT.
        Returns:
            List[str]: Список подходящих фьючерсных тикеров.
        """
        markets: Dict[str, Any] = self.binance.load_markets(params={"type": "future"})
        symbols: List[str] = []

        for symbol, data in markets.items():
            if not data.get("active"):
                continue
            if data.get("type") != "future":
                continue
            if not symbol.endswith(f"/{quote_asset}"):
                continue
            if "info" in data:
                quote_volume = float(data["info"].get("quoteVolume", 0))
                if quote_volume < min_volume_usdt:
                    continue
            cleaned = clean_symbol(symbol)
            symbols.append(cleaned)

        return sorted(symbols)

    def fetch_ohlcvi(
            self,
            symbol: str,
            timeframe: Timeframe,
            limit: int = 100,
            to_time: Optional[datetime] = None,
            has_oi: bool = False
    ) -> List[Bar]:
        """
        Загружает OHLCV+OI для инструмента на нужном таймфрейме.

        Args:
            symbol (str): Тикер.
            timeframe (Timeframe): Таймфрейм.
            limit (int): Количество записей.
            to_time (Optional[datetime]): Максимальная дата.
            has_oi (bool): Флаг наличия OI.
        Returns:
            List[Bar]: Список баров со всеми параметрами.
        """
        since: Optional[int] = None
        end_time: Optional[int] = None
        if to_time:
            since = int((to_time - timedelta(minutes=limit * timeframe.minutes)).timestamp() * 1000)
            end_time = int(to_time.timestamp() * 1000)

        raw: List[Any] = self.binance.fetch_ohlcv(
            symbol,
            timeframe=timeframe.value,
            since=since,
            limit=limit
        )

        if not raw:
            return []

        # OI
        if has_oi:
            raw_oi = self.fetch_oi_history(symbol, timeframe, limit=limit, since=since, end_time=end_time)
            target_ts = [to_local_dt(entry[0])  for entry in raw]

            if not raw_oi:
                current_oi = self.fetch_oi(symbol)
                last_ts = to_local_dt(raw[-1][0])
                save_oi(symbol, timeframe, last_ts, current_oi)
                oi_values = load_oi(symbol, timeframe, target_ts)
            else:
                oi_values = self._map_oi_to_tf(target_ts, raw_oi)
        else:
            oi_values = [FLOAT_UNDEFINED for _ in range(len(raw))]

        bars: List[Bar] = []
        for i, entry in enumerate(raw):
            ts = to_local_dt(entry[0])
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

        atrs = calculate_atr(bars)
        for i in range(len(atrs)):
            bars[i].atr = atrs[i]

        return bars

    def fetch_ohlcvi_by_tfs(
            self,
            symbol: str,
            tfs: MTFProfile,
            limit: int = 100,
            to_time: Optional[datetime] = None,
    ) -> Dict[Timeframe, List[Bar]]:
        """
        Загружает OHLCVI для символа по нескольким таймфреймам.
        Args:
            symbol (str): Тикер.
            tfs (MTFProfile): Профиль таймфреймов.
            limit (int): Количество баров на таймфрейм.
            to_time (Optional[datetime]): Максимальная дата.
        Returns:
            Dict[Timeframe, List[Bar]]: Словарь {таймфрейм: бары}.
        """
        return {tf: self.fetch_ohlcvi(symbol, tf, limit=limit, to_time=to_time, has_oi=tf == tfs.setup) for tf in tfs}

    def fetch_oi_history(
            self,
            symbol: str,
            timeframe: Timeframe,
            limit: int = 200,
            since: Optional[int] = None,
            end_time: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Загружает историю открытого интереса для инструмента на выбранном таймфрейме.
        Args:
            symbol (str): Тикер.
            timeframe (Timeframe): Таймфрейм.
            limit (int): Количество элементов.
            since (Optional[int]): Начальный unix-millisec (или None).
            end_time (Optional[int]): Конечный unix-millisec (или None).
        Returns:
            List[dict]: История OI.
        """
        params: Dict[str, Any] = {
            'symbol': symbol,
            'period': timeframe.value,
            'limit': limit
        }
        if since:
            params['startTime'] = since
        if end_time:
            params['endTime'] = end_time

        return self.binance.fapidata_get_openinteresthist(params)

    def fetch_oi(self, symbol: str) -> float:
        params: Dict[str, Any] = {'symbol': symbol}
        result = self.binance.fapipublic_get_openinterest(params)
        return float(result["openInterest"])

    @staticmethod
    def _map_oi_to_tf(target_timestamps: List[datetime], oi_data: List[Dict]) -> List[float]:
        oi_map = {to_local_dt(int(item['timestamp'])): float(item['sumOpenInterest']) for item in oi_data }
        sorted_ts = sorted(oi_map.keys())
        sorted_oi = [oi_map[ts] for ts in sorted_ts]
        result = []
        for ts in target_timestamps:
            idx = bisect_right(sorted_ts, ts) - 1
            result.append(sorted_oi[idx] if idx >= 0 else FLOAT_UNDEFINED)
        return result
