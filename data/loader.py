from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Any

from config.constants import VOLUME_THRESHOLD_USDT, FLOAT_UNDEFINED, BELGRADE_TZ
from data.binance_client import get_binance_client
from domain.models.bar import Bar
from domain.models.mtf_profile import MTFProfile
from domain.models.timeframe import Timeframe
from utils.math_utils import calculate_atr
from utils.str_utils import clean_symbol


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
        Возвращает отсортированный список тикеров (символов), подходящих по объёму торгов и активным рынкам.
        Args:
            quote_asset (str): Котируемая валюта (по умолчанию 'USDT').
            min_volume_usdt (float): Минимальный объём торгов в USDT.
        Returns:
            List[str]: Отсортированный список тикеров, удовлетворяющих условиям.
        """
        markets: Dict[str, Any] = self.binance.load_markets()
        symbols: List[str] = []

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

        # OI
        if has_oi:
            if timeframe.minutes < Timeframe.M5.minutes:
                timeframe = Timeframe.M5
                since = None
                end_time = None
                if to_time:
                    since = int(
                        (to_time - timedelta(minutes=limit * timeframe.minutes)).timestamp() * 1000)
                    end_time = int(to_time.timestamp() * 1000)
                raw_oi: List[Dict] = self.fetch_oi(
                    symbol=symbol,
                    timeframe=timeframe,
                    since=since,
                    end_time=end_time,
                    limit=limit
                )
                target_ts: List[datetime] = [
                    datetime.fromtimestamp(entry[0] / 1000, tz=timezone.utc).astimezone(BELGRADE_TZ)
                    for entry in raw
                ]
                oi_values: List[float] = self._map_oi_to_tf(target_ts, raw_oi)
            else:
                raw_oi: List[Dict] = self.fetch_oi(
                    symbol=symbol,
                    timeframe=timeframe,
                    since=since,
                    end_time=end_time,
                    limit=limit
                )
                oi_values = [float(entry["sumOpenInterest"]) for entry in raw_oi]
        else:
            oi_values = [FLOAT_UNDEFINED for _ in range(len(raw))]

        bars: List[Bar] = []
        for i, entry in enumerate(raw):
            ts = datetime.fromtimestamp(entry[0] / 1000, tz=timezone.utc).astimezone(BELGRADE_TZ)
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
        for i in range(0, len(atrs)):
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

    def fetch_oi(
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

    @staticmethod
    def _map_oi_to_tf(target_timestamps: List[datetime], oi_data: List[Dict]) -> List[float]:
        """
        Сопоставляет значения OI по ближайшему времени для конкретного набора баров.
        Args:
            target_timestamps (List[datetime]): Список меток времени баров.
            oi_data (List[dict]): Данные по OI.
        Returns:
            List[float]: Массив значений OI (один на каждый бар).
        """
        oi_map: Dict[datetime, float] = {
            datetime.fromtimestamp(int(item['timestamp']) / 1000, tz=timezone.utc).astimezone(BELGRADE_TZ): float(
                item['sumOpenInterest'])
            for item in oi_data
        }
        sorted_ts: List[datetime] = sorted(oi_map.keys())
        sorted_oi: List[float] = [oi_map[ts] for ts in sorted_ts]

        # Интерполяция: ближайшее предыдущее значение
        result: List[float] = []
        for ts in target_timestamps:
            idx = bisect_right(sorted_ts, ts) - 1
            if idx >= 0:
                result.append(sorted_oi[idx])
            else:
                result.append(FLOAT_UNDEFINED)
        return result
