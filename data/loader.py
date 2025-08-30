from bisect import bisect_right
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
import threading


from utils.decorator import log_duration_ms

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
        # {(symbol, timeframe, limit): (timestamp, bars)}
        self._ohlcv_cache: Dict[tuple, tuple] = {}
        self._cache_lock = threading.Lock()
        self.client_lock = threading.Lock()

    @log_duration_ms
    def clear_cache(self) -> None:
        """Очистка кэша OHLCV."""
        with self._cache_lock:
            self._ohlcv_cache.clear()

    @log_duration_ms
    def get_filtered_symbols(self, min_volume_usdt: float = VOLUME_THRESHOLD_USDT) -> List[str]:
        """Возвращает список тикеров, подходящих по объёму торгов и активности."""
        with self.client_lock:
            markets: Dict[str, Any] = self.binance.load_markets()
        symbols: List[str] = []
        for symbol, data in markets.items():
            if not data.get('linear'):
                continue
            if not data.get('active'):
                continue
            if not symbol.endswith('USDT'):
                continue

            if 'quoteVolume' in data and data['quoteVolume'] is not None:
                if data['quoteVolume'] < min_volume_usdt:
                    continue

            symbol = clean_symbol(symbol)
            symbols.append(symbol)

        return sorted(symbols)

    @log_duration_ms
    def fetch_ohlcvi(
            self,
            symbol: str,
            timeframe: Timeframe,
            limit: int = 500,
            to_time: Optional[datetime] = None,
            has_oi: bool = False,
            use_cache: bool = False,
            ttl_minutes: int = 0,
    ) -> List[Bar]:
        """Загружает OHLCV и OI для инструмента на заданном таймфрейме."""
        cache_key = (symbol, timeframe, limit, has_oi)
        now = datetime.now()
        if use_cache:
            with self._cache_lock:
                if self._is_cache_valid(use_cache, cache_key, now, ttl_minutes):
                    return self._ohlcv_cache[cache_key][1]

        since, end_time = self._calculate_time_bounds(to_time, timeframe, limit)

        raw = self._fetch_ohlcv(symbol, timeframe, since, limit)
        if not raw:
            return []

        target_ts = [to_local_dt(entry[0]) for entry in raw]
        oi_values = self._get_oi_values(symbol, timeframe, limit, since, end_time, to_time, has_oi, target_ts)

        tbq_values = self._get_tbq_values(symbol, timeframe, limit, since, end_time, target_ts)
        bars = self._build_bars(raw, oi_values, tbq_values, to_time)

        atrs = calculate_atr(bars)
        for i in range(len(atrs)):
            bars[i].atr = atrs[i]

        if use_cache:
            with self._cache_lock:
                self._ohlcv_cache[cache_key] = (now, bars)
        return bars

    @log_duration_ms
    def _fetch_ohlcv(self, symbol, timeframe, since, limit):
        with self.client_lock:
            return self.binance.fetch_ohlcv(
                symbol,
                timeframe=timeframe.value,
                since=since,
                limit=limit
            )

    @log_duration_ms
    def _is_cache_valid(self, use_cache: bool, cache_key: tuple, now: datetime, ttl_minutes: int) -> bool:
        return use_cache and cache_key in self._ohlcv_cache and \
            now - self._ohlcv_cache[cache_key][0] < timedelta(minutes=ttl_minutes)

    @log_duration_ms
    def _calculate_time_bounds(self, to_time: Optional[datetime], timeframe: Timeframe, limit: int) -> tuple:
        if not to_time:
            return None, None
        since = int((to_time - timedelta(minutes=limit * timeframe.minutes)).timestamp() * 1000)
        end_time = int(to_time.timestamp() * 1000)
        return since, end_time

    @log_duration_ms
    def _get_oi_values(
            self,
            symbol: str,
            timeframe: Timeframe,
            limit: int,
            since: Optional[int],
            end_time: Optional[int],
            to_time: Optional[datetime],
            has_oi: bool,
            target_ts: List[datetime]
    ) -> List[float]:
        if not has_oi:
            return [FLOAT_UNDEFINED for _ in range(len(target_ts))]

        oi_limit = min(limit, 500)
        raw_oi = self.fetch_oi_history(symbol, timeframe, limit=oi_limit, since=since, end_time=end_time)

        if not raw_oi and timeframe.minutes < Timeframe.M5.minutes:
            tf_m5 = Timeframe.M5
            m5_since, m5_end_time = self._calculate_time_bounds(to_time, tf_m5, limit)
            raw_oi = self.fetch_oi_history(symbol, tf_m5, limit=oi_limit, since=m5_since, end_time=m5_end_time)

        if raw_oi:
            return self._map_oi_to_tf(target_ts, raw_oi)
        else:
            current_oi = self.fetch_oi(symbol)
            last_ts = target_ts[-1]
            save_oi(symbol, timeframe, last_ts, current_oi)
            return load_oi(symbol, timeframe, target_ts)

    @log_duration_ms
    def _build_bars(
            self,
            raw: List[Any],
            oi_values: List[float],
            tbq_values: List[float],
            to_time: Optional[datetime]
    ) -> List[Bar]:
        bars = []
        for i, entry in enumerate(raw):
            ts = to_local_dt(entry[0])
            if to_time and ts > to_time:
                continue
            oi_value = oi_values[i] if i < len(oi_values) else FLOAT_UNDEFINED
            tbq_value = tbq_values[i] if i < len(tbq_values) else FLOAT_UNDEFINED
            bar = Bar(
                timestamp=ts,
                open=entry[1],
                high=entry[2],
                low=entry[3],
                close=entry[4],
                volume=entry[5],
                tbq=tbq_value,
                oi=oi_value
            )
            bars.append(bar)
        return bars

    @log_duration_ms
    def fetch_ohlcvi_by_tfs(
            self,
            symbol: str,
            tfs: MTFProfile,
            limit: int = 500,
            to_time: Optional[datetime] = None,
    ) -> Dict[Timeframe, List[Bar]]:
        """Загружает OHLCVI для символа сразу по нескольким таймфреймам."""
        tfs_list = list(tfs)
        result: Dict[Timeframe, List[Bar]] = {}
        for tf in tfs_list:
            bars = self.fetch_ohlcvi(
                symbol,
                tf,
                limit=limit,
                to_time=to_time,
                has_oi=tf == tfs.setup,
            )
            result[tf] = bars
        return result

    @log_duration_ms
    def fetch_oi_history(
            self,
            symbol: str,
            timeframe: Timeframe,
            limit: int = 500,
            since: Optional[int] = None,
            end_time: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Получает историю открытого интереса на выбранном таймфрейме."""
        params: Dict[str, Any] = {
            'symbol': symbol,
            'period': timeframe.value,
            'limit': limit
        }
        if since:
            params['startTime'] = since
        if end_time:
            params['endTime'] = end_time

        with self.client_lock:
            return self.binance.fapidata_get_openinteresthist(params)

    @log_duration_ms
    def fetch_oi(self, symbol: str) -> float:
        params: Dict[str, Any] = {'symbol': symbol}
        with self.client_lock:
            result = self.binance.fapipublic_get_openinterest(params)
        return float(result["openInterest"])

    @staticmethod
    @log_duration_ms
    def _map_oi_to_tf(target_timestamps: List[datetime], oi_data: List[Dict]) -> List[float]:
        """Соотносит значения OI с целевыми временными метками."""
        oi_map = {to_local_dt(int(item['timestamp'])): float(item['sumOpenInterest']) for item in oi_data }
        sorted_ts = sorted(oi_map.keys())
        sorted_oi = [oi_map[ts] for ts in sorted_ts]
        result = []
        for ts in target_timestamps:
            idx = bisect_right(sorted_ts, ts) - 1
            result.append(sorted_oi[idx] if idx >= 0 else FLOAT_UNDEFINED)
        return result

    @log_duration_ms
    def _get_tbq_values(
            self,
            symbol: str,
            timeframe: Timeframe,
            limit: int,
            since: Optional[int],
            end_time: Optional[int],
            target_ts: List[datetime],
    ) -> List[float]:
        """
        Возвращает массив TBQ (takerBuyQuoteVolume / quoteAssetVolume) в диапазоне target_ts.
        """
        klines = self._fetch_klines_raw(symbol, timeframe, limit, since, end_time)
        if not klines:
            return [FLOAT_UNDEFINED for _ in target_ts]

        # Binance raw kline: [ openTime, o, h, l, c, baseVol, closeTime, quoteVol, trades, takerBuyBaseVol, takerBuyQuoteVol, ignore ]
        tbq_map = {}
        for k in klines:
            try:
                open_ts = to_local_dt(int(k[0]))
                quote_vol = float(k[7])
                tbq_vol = float(k[10])
                tbq = tbq_vol / quote_vol if quote_vol > 0 else FLOAT_UNDEFINED
                tbq_map[open_ts] = tbq
            except Exception:
                continue

        return [tbq_map.get(ts, FLOAT_UNDEFINED) for ts in target_ts]

    @log_duration_ms
    def _fetch_klines_raw(self, symbol: str, timeframe: Timeframe, limit: int,
                          since: Optional[int], end_time: Optional[int]) -> List[List]:
        params: Dict[str, Any] = {
            'symbol': symbol,
            'interval': timeframe.value,
            'limit': limit
        }
        if since:
            params['startTime'] = since
        if end_time:
            params['endTime'] = end_time
        with self.client_lock:
            return self.binance.fapipublic_get_klines(params)
