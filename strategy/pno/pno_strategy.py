"""Strategy wrapper for PNO."""

from __future__ import annotations

from dataclasses import replace
from dataclasses import dataclass
import gc
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import io
import os
import tempfile
from typing import Any, ClassVar
import zipfile

import numpy as np
import pandas as pd
import requests

from constants import DEFAULT_CACHE_DIR
from data.exchanges.ccxt_types import CcxtAggTradePayload
from data.exchanges.ccxt_futures_client import CcxtFuturesClient
from data.storage.parquet_storage import ParquetStorage
from domain.enums.exchange import Exchange
from domain.enums.timeframe import Timeframe
from domain.models.trade_result import TradeResult
from strategy.base_strategy import BaseStrategy
from strategy.pno.config import (
    PnoParams,
    build_pno_grid,
    describe_pno_category_profile_set,
    resolve_pno_category_profiles,
    validate_pno_params,
    with_pno_risk,
)
from strategy.pno.engine import PnoEngine
from vectorbt_runner.mtf_frames import SymbolMtfFrames
from vectorbt_runner.data_preparer import DataPreparer


@dataclass
class _PnoSecondsFrameProvider:
    cache_dir: Path
    _shared_window_cache: ClassVar[dict[tuple[str, int, int], pd.DataFrame]] = {}
    _shared_day_cache: ClassVar[dict[tuple[str, str], pd.DataFrame]] = {}
    _shared_aggregated_window_cache: ClassVar[dict[tuple[str, str, int, int], pd.DataFrame]] = {}
    _TRADE_COUNT_COLUMNS: ClassVar[tuple[str, ...]] = ("number_of_trades", "trades", "trade_count")
    _TRADE_DATA_COLUMNS: ClassVar[tuple[str, ...]] = (
        "quote_volume",
        "taker_buy_volume",
        "taker_buy_quote_volume",
        "number_of_trades",
    )

    def __post_init__(self) -> None:
        self._runtime_cache_dir = Path(self.cache_dir)
        self._persistent_cache_dir = self._resolve_persistent_cache_dir(self._runtime_cache_dir)
        self._runtime_preparer = DataPreparer(self._runtime_cache_dir)
        self._persistent_preparer = DataPreparer(self._persistent_cache_dir)
        self._storage = ParquetStorage(self._persistent_cache_dir)
        self._client: CcxtFuturesClient | None = None
        self._window_cache: dict[tuple[str, int, int], pd.DataFrame] = {}
        self._day_cache: dict[tuple[str, str], pd.DataFrame] = {}
        self._aggregated_window_cache: dict[tuple[str, str, int, int], pd.DataFrame] = {}

    @staticmethod
    def _drop_symbol_cache_items(cache: dict[tuple[object, ...], pd.DataFrame], symbol: str) -> int:
        removed = 0
        for key in list(cache):
            if key and key[0] == symbol:
                cache.pop(key, None)
                removed += 1
        return removed

    def clear_runtime_caches(self, *, symbol: str | None = None) -> None:
        """Drop heavy aggTrades-derived frames after a symbol is processed.

        PNO enriches OHLCV with real aggTrades data. On a full-universe run this
        means hundreds of large second-level windows. Keeping them in process
        after the current symbol is done gives no trading benefit because the
        aggregated frames are persisted on disk, but it steadily exhausts RAM.
        """
        removed = 0
        if symbol is None:
            removed += len(self._window_cache)
            removed += len(self._day_cache)
            removed += len(self._aggregated_window_cache)
            removed += len(self._shared_window_cache)
            removed += len(self._shared_day_cache)
            removed += len(self._shared_aggregated_window_cache)
            self._window_cache.clear()
            self._day_cache.clear()
            self._aggregated_window_cache.clear()
            self._shared_window_cache.clear()
            self._shared_day_cache.clear()
            self._shared_aggregated_window_cache.clear()
        else:
            removed += self._drop_symbol_cache_items(self._window_cache, symbol)
            removed += self._drop_symbol_cache_items(self._day_cache, symbol)
            removed += self._drop_symbol_cache_items(self._aggregated_window_cache, symbol)
            removed += self._drop_symbol_cache_items(self._shared_window_cache, symbol)
            removed += self._drop_symbol_cache_items(self._shared_day_cache, symbol)
            removed += self._drop_symbol_cache_items(self._shared_aggregated_window_cache, symbol)
        if removed:
            gc.collect()

    @staticmethod
    def _resolve_persistent_cache_dir(cache_dir: Path) -> Path:
        resolved_cache_dir = cache_dir.expanduser().resolve()
        temp_root = Path(tempfile.gettempdir()).expanduser().resolve()
        if resolved_cache_dir == temp_root or temp_root in resolved_cache_dir.parents:
            return Path(os.getenv("CACHE_DIR", DEFAULT_CACHE_DIR)).expanduser().resolve()
        return resolved_cache_dir

    def load_aggregated_window(
        self,
        *,
        symbol: str,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
        target_timeframe: Timeframe,
    ) -> pd.DataFrame:
        cache_key = (symbol, target_timeframe.value, int(start_timestamp_ms), int(end_timestamp_ms))
        cached = self._aggregated_window_cache.get(cache_key)
        if cached is not None:
            return cached.copy()
        shared_cached = self._shared_aggregated_window_cache.get(cache_key)
        if shared_cached is not None:
            local_copy = shared_cached.copy()
            self._aggregated_window_cache[cache_key] = local_copy
            return local_copy.copy()
        persisted = self._load_persisted_aggregated_window(
            symbol=symbol,
            start_timestamp_ms=int(start_timestamp_ms),
            end_timestamp_ms=int(end_timestamp_ms),
            target_timeframe=target_timeframe,
        )
        if persisted is not None:
            self._aggregated_window_cache[cache_key] = persisted.copy()
            self._shared_aggregated_window_cache[cache_key] = persisted.copy()
            return persisted.copy()
        seconds_frame = self._ensure_seconds_window(
            symbol=symbol,
            start_timestamp_ms=start_timestamp_ms,
            end_timestamp_ms=end_timestamp_ms,
        )
        if seconds_frame.empty:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        aggregated = PnoEngine._aggregate_frame(
            seconds_frame,
            target_timeframe_ms=target_timeframe.to_milliseconds(),
        )
        self._aggregated_window_cache[cache_key] = aggregated.copy()
        self._shared_aggregated_window_cache[cache_key] = aggregated.copy()
        if not aggregated.empty:
            self._persist_aggregated_window(
                symbol=symbol,
                start_timestamp_ms=int(start_timestamp_ms),
                end_timestamp_ms=int(end_timestamp_ms),
                target_timeframe=target_timeframe,
                frame=aggregated,
            )
        return aggregated

    def enrich_with_trade_data(
        self,
        *,
        symbol: str,
        frame: pd.DataFrame,
        target_timeframe: Timeframe,
    ) -> pd.DataFrame:
        """Merges real aggTrades-derived trade count into an OHLCV frame.

        This keeps the exchange OHLCV prices/volume as the source of truth and only
        fills market-activity columns used by PNO flow filters.
        """
        if frame.empty or "timestamp" not in frame.columns:
            return frame
        has_full_trade_data = self._has_real_trade_count(frame) and {"quote_volume", "taker_buy_quote_volume"}.issubset(frame.columns)
        if has_full_trade_data:
            return frame
        window = self._resolve_frame_window(frame, target_timeframe=target_timeframe)
        if window is None:
            return frame
        start_timestamp_ms, end_timestamp_ms = window
        trade_frame = self.load_aggregated_window(
            symbol=symbol,
            start_timestamp_ms=start_timestamp_ms,
            end_timestamp_ms=end_timestamp_ms,
            target_timeframe=target_timeframe,
        )
        if trade_frame.empty or not self._has_real_trade_count(trade_frame):
            return frame
        return self._merge_trade_data_columns(frame=frame, trade_frame=trade_frame)

    @classmethod
    def _has_real_trade_count(cls, frame: pd.DataFrame) -> bool:
        trade_count_column = next((column for column in cls._TRADE_COUNT_COLUMNS if column in frame.columns), None)
        if trade_count_column is None:
            return False
        values = pd.to_numeric(frame[trade_count_column], errors="coerce")
        return bool(values.notna().any() and float(values.fillna(0.0).sum()) > 0.0)

    @staticmethod
    def _resolve_frame_window(frame: pd.DataFrame, *, target_timeframe: Timeframe) -> tuple[int, int] | None:
        timestamps = pd.to_numeric(frame.get("timestamp"), errors="coerce").dropna()
        if timestamps.empty:
            return None
        timeframe_ms = int(target_timeframe.to_milliseconds())
        return int(timestamps.min()), int(timestamps.max()) + max(timeframe_ms - 1, 0)

    @staticmethod
    def _empty_seconds_frame() -> pd.DataFrame:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    @classmethod
    def _merge_trade_data_columns(cls, *, frame: pd.DataFrame, trade_frame: pd.DataFrame) -> pd.DataFrame:
        trade_columns = [column for column in cls._TRADE_DATA_COLUMNS if column in trade_frame.columns]
        if not trade_columns:
            return frame
        prepared_trade_frame = trade_frame.loc[:, ["timestamp", *trade_columns]].copy()
        prepared_trade_frame["timestamp"] = pd.to_numeric(prepared_trade_frame["timestamp"], errors="coerce")
        prepared_trade_frame = prepared_trade_frame.dropna(subset=["timestamp"])
        if prepared_trade_frame.empty:
            return frame
        prepared_trade_frame["timestamp"] = prepared_trade_frame["timestamp"].astype("int64")
        for column in trade_columns:
            prepared_trade_frame[column] = pd.to_numeric(prepared_trade_frame[column], errors="coerce")
        merged = frame.copy()
        merged["timestamp"] = pd.to_numeric(merged["timestamp"], errors="coerce").astype("int64")
        merged = merged.merge(
            prepared_trade_frame,
            on="timestamp",
            how="left",
            suffixes=("", "__agg_trades"),
        )
        for column in trade_columns:
            incoming_column = f"{column}__agg_trades"
            if incoming_column not in merged.columns:
                continue
            if column in frame.columns:
                merged[column] = merged[incoming_column].combine_first(pd.to_numeric(merged[column], errors="coerce"))
                merged = merged.drop(columns=[incoming_column])
            else:
                merged = merged.rename(columns={incoming_column: column})
        return merged

    def _ensure_seconds_window(
        self,
        *,
        symbol: str,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        cache_key = (symbol, int(start_timestamp_ms), int(end_timestamp_ms))
        cached = self._window_cache.get(cache_key)
        if cached is not None:
            return cached
        shared_cached = self._shared_window_cache.get(cache_key)
        if shared_cached is not None:
            local_copy = shared_cached.copy()
            self._window_cache[cache_key] = local_copy
            return local_copy

        seconds_frame = self._runtime_preparer.load_symbol_data_range(
            symbol,
            Timeframe.S1,
            start_timestamp_ms=int(start_timestamp_ms),
            end_timestamp_ms=int(end_timestamp_ms),
        )
        if self._persistent_cache_dir != self._runtime_cache_dir:
            persistent_seconds_frame = self._persistent_preparer.load_symbol_data_range(
                symbol,
                Timeframe.S1,
                start_timestamp_ms=int(start_timestamp_ms),
                end_timestamp_ms=int(end_timestamp_ms),
            )
            if not persistent_seconds_frame.empty:
                seconds_frame = (
                    pd.concat([seconds_frame, persistent_seconds_frame], ignore_index=True)
                    .drop_duplicates(subset=["timestamp"], keep="last")
                    .sort_values("timestamp")
                    .reset_index(drop=True)
                )
        if self._frame_covers_window(
            seconds_frame,
            start_timestamp_ms=int(start_timestamp_ms),
            end_timestamp_ms=int(end_timestamp_ms),
        ) and {"taker_buy_volume", "number_of_trades"}.issubset(seconds_frame.columns):
            self._window_cache[cache_key] = seconds_frame.copy()
            self._shared_window_cache[cache_key] = seconds_frame.copy()
            return seconds_frame
        if self._sparse_frame_has_window_activity(
            seconds_frame,
            start_timestamp_ms=int(start_timestamp_ms),
            end_timestamp_ms=int(end_timestamp_ms),
        ) and {"taker_buy_volume", "number_of_trades"}.issubset(seconds_frame.columns):
            clipped = seconds_frame.loc[
                (seconds_frame["timestamp"] >= int(start_timestamp_ms))
                & (seconds_frame["timestamp"] <= int(end_timestamp_ms))
            ].copy()
            self._window_cache[cache_key] = clipped.copy()
            self._shared_window_cache[cache_key] = clipped.copy()
            return clipped
        fetched_parts: list[pd.DataFrame] = []
        newly_fetched_day_parts: list[pd.DataFrame] = []
        for utc_day in self._iter_utc_days(
            start_timestamp_ms=int(start_timestamp_ms),
            end_timestamp_ms=int(end_timestamp_ms),
        ):
            day_key = (symbol, utc_day.isoformat())
            day_frame = self._day_cache.get(day_key)
            if day_frame is None:
                day_frame = self._shared_day_cache.get(day_key)
            if day_frame is None:
                day_frame = self._fetch_seconds_for_day(symbol=symbol, utc_day=utc_day)
                self._day_cache[day_key] = day_frame
                self._shared_day_cache[day_key] = day_frame.copy()
                if not day_frame.empty:
                    newly_fetched_day_parts.append(day_frame.copy())
            else:
                self._day_cache[day_key] = day_frame.copy()
            if day_frame.empty:
                continue
            day_slice = day_frame.loc[
                (day_frame["timestamp"] >= int(start_timestamp_ms))
                & (day_frame["timestamp"] <= int(end_timestamp_ms))
            ].copy()
            if not day_slice.empty:
                fetched_parts.append(day_slice)

        if fetched_parts:
            fetched = (
                pd.concat(fetched_parts, ignore_index=True)
                .drop_duplicates(subset=["timestamp"], keep="last")
                .sort_values("timestamp")
                .reset_index(drop=True)
            )
            seconds_frame = (
                pd.concat([seconds_frame, fetched], ignore_index=True)
                .drop_duplicates(subset=["timestamp"], keep="last")
                .sort_values("timestamp")
                .reset_index(drop=True)
            )
        if newly_fetched_day_parts:
            newly_fetched = (
                pd.concat(newly_fetched_day_parts, ignore_index=True)
                .drop_duplicates(subset=["timestamp"], keep="last")
                .sort_values("timestamp")
                .reset_index(drop=True)
            )
            self._storage.save_incremental(symbol, Timeframe.S1, newly_fetched)
        self._window_cache[cache_key] = seconds_frame.copy()
        self._shared_window_cache[cache_key] = seconds_frame.copy()
        return seconds_frame

    @staticmethod
    def _frame_covers_window(
        frame: pd.DataFrame,
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> bool:
        if frame.empty:
            return False
        clipped = frame.loc[
            (frame["timestamp"] >= int(start_timestamp_ms))
            & (frame["timestamp"] <= int(end_timestamp_ms)),
            ["timestamp"],
        ].drop_duplicates()
        expected_points = ((int(end_timestamp_ms) - int(start_timestamp_ms)) // 1_000) + 1
        return len(clipped) >= expected_points

    @staticmethod
    def _sparse_frame_has_window_activity(
        frame: pd.DataFrame,
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> bool:
        if frame.empty or "timestamp" not in frame.columns:
            return False
        timestamps = pd.to_numeric(frame["timestamp"], errors="coerce")
        if timestamps.dropna().empty:
            return False
        return bool(
            (
                (timestamps >= int(start_timestamp_ms))
                & (timestamps <= int(end_timestamp_ms))
            ).any()
        )

    def _aggregated_window_path(
        self,
        *,
        symbol: str,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
        target_timeframe: Timeframe,
    ) -> Path:
        encoded_symbol = ParquetStorage.encode_symbol_for_path(symbol)
        return (
            self._persistent_cache_dir
            / encoded_symbol
            / "_pno_sparse"
            / target_timeframe.value
            / f"{int(start_timestamp_ms)}_{int(end_timestamp_ms)}.parquet"
        )

    def _load_persisted_aggregated_window(
        self,
        *,
        symbol: str,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
        target_timeframe: Timeframe,
    ) -> pd.DataFrame | None:
        path = self._aggregated_window_path(
            symbol=symbol,
            start_timestamp_ms=int(start_timestamp_ms),
            end_timestamp_ms=int(end_timestamp_ms),
            target_timeframe=target_timeframe,
        )
        if not path.exists():
            return None
        frame = pd.read_parquet(path)
        if frame.empty:
            return self._empty_seconds_frame()
        if not {"quote_volume", "taker_buy_volume", "taker_buy_quote_volume", "number_of_trades"}.issubset(frame.columns):
            return None
        return frame.sort_values("timestamp").reset_index(drop=True)

    def _persist_aggregated_window(
        self,
        *,
        symbol: str,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
        target_timeframe: Timeframe,
        frame: pd.DataFrame,
    ) -> None:
        path = self._aggregated_window_path(
            symbol=symbol,
            start_timestamp_ms=int(start_timestamp_ms),
            end_timestamp_ms=int(end_timestamp_ms),
            target_timeframe=target_timeframe,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        frame.to_parquet(tmp_path, index=False)
        tmp_path.replace(path)

    @staticmethod
    def _iter_utc_days(
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> tuple[date, ...]:
        start_day = datetime.fromtimestamp(int(start_timestamp_ms) / 1000, tz=timezone.utc).date()
        end_day = datetime.fromtimestamp(int(end_timestamp_ms) / 1000, tz=timezone.utc).date()
        days: list[date] = []
        cursor = start_day
        while cursor <= end_day:
            days.append(cursor)
            cursor += timedelta(days=1)
        return tuple(days)

    def _fetch_seconds_for_day(
        self,
        *,
        symbol: str,
        utc_day: date,
    ) -> pd.DataFrame:
        archive_frame = self._fetch_seconds_from_archive(symbol=symbol, utc_day=utc_day)
        if not archive_frame.empty:
            return archive_frame
        day_start_ms = int(datetime.combine(utc_day, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)
        day_end_ms = day_start_ms + (24 * 60 * 60 * 1000) - 1
        return self._fetch_seconds_from_live_trades(
            symbol=symbol,
            start_timestamp_ms=day_start_ms,
            end_timestamp_ms=day_end_ms,
        )

    def _fetch_seconds_from_archive(
        self,
        *,
        symbol: str,
        utc_day: date,
    ) -> pd.DataFrame:
        market_id = self._resolve_market_id(symbol)
        url = (
            "https://data.binance.vision/data/futures/um/daily/aggTrades/"
            f"{market_id}/{market_id}-aggTrades-{utc_day.isoformat()}.zip"
        )
        response = requests.get(url, timeout=60)
        if response.status_code == 404:
            return self._empty_seconds_frame()
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = archive.namelist()
            if not names:
                return self._empty_seconds_frame()
            raw = archive.read(names[0])
        trades = pd.read_csv(io.BytesIO(raw))
        if trades.empty:
            return self._empty_seconds_frame()
        return self._aggregate_agg_trades_to_seconds(trades)

    @staticmethod
    def _resolve_agg_trade_timestamp(row: CcxtAggTradePayload) -> int | None:
        raw_value = row.get("transact_time") if "transact_time" in row else row.get("T")
        try:
            return int(raw_value) if raw_value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _resolve_agg_trade_id(row: CcxtAggTradePayload) -> int | None:
        for key in ("agg_trade_id", "a"):
            raw_value = row.get(key)
            if raw_value is None:
                continue
            try:
                return int(raw_value)
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _clip_agg_trades_to_window(
        trades: pd.DataFrame,
        *,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        if trades.empty:
            return trades
        timestamp_column = "transact_time" if "transact_time" in trades.columns else "T"
        if timestamp_column not in trades.columns:
            return trades.iloc[0:0].copy()
        timestamps = pd.to_numeric(trades[timestamp_column], errors="coerce")
        return trades.loc[
            (timestamps >= int(start_timestamp_ms))
            & (timestamps <= int(end_timestamp_ms))
        ].copy()

    def _fetch_seconds_from_live_trades(
        self,
        *,
        symbol: str,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        if self._client is None:
            self._client = CcxtFuturesClient(exchange=Exchange.BINANCE)
        market_id = self._client.get_market_id(symbol)
        all_rows: list[CcxtAggTradePayload] = []
        next_from_id: int | None = None
        previous_last_id: int | None = None
        while True:
            params: dict[str, object] = {
                "symbol": market_id,
                "limit": 1000,
            }
            if next_from_id is None:
                params["startTime"] = int(start_timestamp_ms)
                params["endTime"] = int(end_timestamp_ms)
            else:
                params["fromId"] = int(next_from_id)

            rows = self._client.fetch_binance_agg_trades(symbol=symbol, params=params)
            if not rows:
                break
            if not rows:
                break

            all_rows.extend(rows)
            last_row = rows[-1]
            last_ts = self._resolve_agg_trade_timestamp(last_row)
            last_id = self._resolve_agg_trade_id(last_row)
            if last_id is None:
                break
            if previous_last_id is not None and last_id <= previous_last_id:
                break
            previous_last_id = last_id
            next_from_id = last_id + 1

            if last_ts is not None and last_ts > int(end_timestamp_ms):
                break
            if len(rows) < 1000:
                break

        if not all_rows:
            return self._empty_seconds_frame()

        trades = pd.DataFrame(all_rows)
        trades = self._clip_agg_trades_to_window(
            trades,
            start_timestamp_ms=int(start_timestamp_ms),
            end_timestamp_ms=int(end_timestamp_ms),
        )
        if trades.empty:
            return self._empty_seconds_frame()
        return self._aggregate_agg_trades_to_seconds(trades)

    def _resolve_market_id(self, symbol: str) -> str:
        if self._client is None:
            self._client = CcxtFuturesClient(exchange=Exchange.BINANCE)
        self._client._ensure_markets_loaded()
        return str(self._client._client.market_id(symbol))

    @staticmethod
    def _aggregate_agg_trades_to_seconds(trades: pd.DataFrame) -> pd.DataFrame:
        if trades.empty:
            return _PnoSecondsFrameProvider._empty_seconds_frame()
        work = trades.copy()
        timestamp_column = "transact_time" if "transact_time" in work.columns else "T"
        price_column = "price" if "price" in work.columns else "p"
        quantity_column = "quantity" if "quantity" in work.columns else "q"
        maker_column = "is_buyer_maker" if "is_buyer_maker" in work.columns else "m"

        required_columns = (timestamp_column, price_column, quantity_column, maker_column)
        if any(column not in work.columns for column in required_columns):
            return _PnoSecondsFrameProvider._empty_seconds_frame()

        work["price"] = pd.to_numeric(work[price_column], errors="coerce")
        work["quantity"] = pd.to_numeric(work[quantity_column], errors="coerce")
        work["quote_volume"] = work["price"].astype("float64") * work["quantity"].astype("float64")
        work["timestamp"] = ((pd.to_numeric(work[timestamp_column], errors="coerce") // 1000) * 1000).astype("Int64")
        work = work.loc[
            work["timestamp"].notna()
            & work["price"].notna()
            & work["quantity"].notna()
        ].copy()
        if work.empty:
            return _PnoSecondsFrameProvider._empty_seconds_frame()
        work["timestamp"] = work["timestamp"].astype("int64")
        buyer_is_maker = work[maker_column].astype(str).str.lower().isin(("true", "1"))
        work["taker_buy_volume"] = np.where(buyer_is_maker, 0.0, work["quantity"].astype("float64"))
        work["taker_buy_quote_volume"] = np.where(buyer_is_maker, 0.0, work["quote_volume"].astype("float64"))
        aggregated = (
            work.groupby("timestamp", sort=True)
            .agg(
                open=("price", "first"),
                high=("price", "max"),
                low=("price", "min"),
                close=("price", "last"),
                volume=("quantity", "sum"),
                quote_volume=("quote_volume", "sum"),
                taker_buy_volume=("taker_buy_volume", "sum"),
                taker_buy_quote_volume=("taker_buy_quote_volume", "sum"),
                number_of_trades=("quantity", "size"),
            )
            .reset_index()
        )
        return aggregated.astype(
            {
                "timestamp": "int64",
                "open": "float64",
                "high": "float64",
                "low": "float64",
                "close": "float64",
                "volume": "float64",
                "quote_volume": "float64",
                "taker_buy_volume": "float64",
                "taker_buy_quote_volume": "float64",
                "number_of_trades": "float64",
            }
        )


class PnoStrategy(BaseStrategy[PnoParams]):
    def __init__(
        self,
        *,
        deposit: float,
        risk_pct: float,
        entry_confirmation_mode_filter: str | None = None,
        category_mode_filter: str = "all",
        cache_dir: str | Path | None = None,
    ) -> None:
        self._deposit = deposit
        self._risk_pct = risk_pct
        self._entry_confirmation_mode_filter = entry_confirmation_mode_filter
        self._category_mode_filter = category_mode_filter
        self._cache_dir = Path(cache_dir) if cache_dir is not None else Path("./.output/cache")
        self._engine = PnoEngine(cache_dir=self._cache_dir)
        self._seconds_provider = _PnoSecondsFrameProvider(self._cache_dir)
        self._last_generation_diagnostics: dict[str, object] = {}

    def validate_config(self, params: PnoParams) -> None:
        validate_pno_params(params)
        self._engine.validate_config(params)

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        return self._engine.prepare_data(data)

    def generate_events(self, data: pd.DataFrame, params: PnoParams) -> list[TradeResult]:
        prepared = self._engine.prepare_data(data)
        return self._generate_events_for_profiles(
            profiles=resolve_pno_category_profiles(params, category_mode=self._category_mode_filter),
            runner=lambda profile_params: self._engine.generate_events_single_frame(frame=prepared, params=profile_params),
        )

    @staticmethod
    def _safe_metadata_float(metadata: dict[str, object], key: str) -> float | None:
        value = metadata.get(key)
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if np.isfinite(parsed) else None

    @staticmethod
    def _safe_metadata_int(metadata: dict[str, object], key: str) -> int | None:
        parsed = PnoStrategy._safe_metadata_float(metadata, key)
        return None if parsed is None else int(parsed)

    @staticmethod
    def _is_stale_level_reclaim_trade(
        *,
        trade: TradeResult,
        timestamps: np.ndarray,
        highs: np.ndarray,
        closes: np.ndarray,
    ) -> bool:
        metadata = getattr(trade, "metadata", None)
        if not isinstance(metadata, dict) or timestamps.size == 0:
            return False

        level = PnoStrategy._safe_metadata_float(metadata, "level")
        level_valid_timestamp_ms = PnoStrategy._safe_metadata_int(metadata, "level_valid_timestamp_ms")
        entry_signal_timestamp_ms = PnoStrategy._safe_metadata_int(metadata, "entry_signal_timestamp_ms")
        if level is None or level_valid_timestamp_ms is None or entry_signal_timestamp_ms is None:
            return False
        if entry_signal_timestamp_ms <= level_valid_timestamp_ms:
            return False

        epsilon = max(abs(float(level)) * 1e-6, 1e-12)
        pre_signal_window = (
            (timestamps >= int(level_valid_timestamp_ms))
            & (timestamps < int(entry_signal_timestamp_ms))
        )
        failed_reclaim = pre_signal_window & (highs > float(level) + epsilon) & (closes < float(level) - epsilon)
        return bool(np.any(failed_reclaim))

    def _filter_stale_level_reclaim_trades(
        self,
        *,
        trades: list[TradeResult],
        entry_frame: pd.DataFrame,
    ) -> list[TradeResult]:
        if not trades:
            return trades
        required_columns = {"timestamp", "high", "close"}
        if entry_frame.empty or not required_columns.issubset(entry_frame.columns):
            return trades
        timestamps = pd.to_numeric(entry_frame["timestamp"], errors="coerce").astype("float64").to_numpy()
        highs = pd.to_numeric(entry_frame["high"], errors="coerce").astype("float64").to_numpy()
        closes = pd.to_numeric(entry_frame["close"], errors="coerce").astype("float64").to_numpy()
        return [
            trade
            for trade in trades
            if not self._is_stale_level_reclaim_trade(
                trade=trade,
                timestamps=timestamps,
                highs=highs,
                closes=closes,
            )
        ]

    def generate_events_multi_tf(
        self,
        *,
        mtf_frames: SymbolMtfFrames,
        params: PnoParams,
        **context: object,
    ) -> list[TradeResult]:
        self._seconds_provider.clear_runtime_caches(symbol=params.symbol)
        try:
            engine_context = {"seconds_frame_provider": self._seconds_provider}
            engine_context.update(context)
            enriched_levels_frame = self._seconds_provider.enrich_with_trade_data(
                symbol=params.symbol,
                frame=mtf_frames.levels_frame,
                target_timeframe=params.levels_timeframe,
            )
            enriched_entry_frame = self._seconds_provider.enrich_with_trade_data(
                symbol=params.symbol,
                frame=mtf_frames.entry_frame,
                target_timeframe=params.entry_timeframe,
            )
            trades = self._generate_events_for_profiles(
                profiles=resolve_pno_category_profiles(params, category_mode=self._category_mode_filter),
                runner=lambda profile_params: self._engine.generate_events_multi_tf(
                    levels_frame=enriched_levels_frame,
                    entry_frame=enriched_entry_frame,
                    params=profile_params,
                    **engine_context,
                ),
            )
            self._seconds_provider.clear_runtime_caches(symbol=params.symbol)
            return self._filter_stale_level_reclaim_trades(
                trades=trades,
                entry_frame=enriched_entry_frame,
            )
        finally:
            self._seconds_provider.clear_runtime_caches(symbol=params.symbol)

    def prepare_symbol_context(
        self,
        *,
        symbol: str,
        mtf_frames: SymbolMtfFrames,
        params: PnoParams,
    ) -> dict[str, Any] | None:
        del symbol, mtf_frames, params
        return {"seconds_frame_provider": self._seconds_provider}

    def has_fast_stage1_candidate(
        self,
        *,
        symbol: str,
        levels_frame: pd.DataFrame,
        levels_timeframe: Timeframe,
        entry_timeframe: Timeframe,
    ) -> bool:
        if levels_frame.empty:
            return False
        for params in self.build_parameter_grid():
            runtime_params = replace(
                params,
                symbol=symbol,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
            )
            for profile in resolve_pno_category_profiles(runtime_params, category_mode=self._category_mode_filter):
                if self._engine.fast_stage1_candidate_count(
                    levels_frame=levels_frame,
                    params=profile.params,
                    levels_timeframe_ms=levels_timeframe.to_milliseconds(),
                ) > 0:
                    return True
        return False

    def build_parameter_grid(self) -> list[PnoParams]:
        grid = build_pno_grid()
        if self._entry_confirmation_mode_filter is not None:
            grid = [params for params in grid if params.entry_confirmation_mode == self._entry_confirmation_mode_filter]
        return [
            with_pno_risk(params, deposit=self._deposit, risk_pct=self._risk_pct)
            for params in grid
        ]

    def params_to_row(self, params: PnoParams) -> dict[str, int | float | str | None]:
        return {
            "pno_variant_id": params.pno_variant_id,
            "pno_category_mode": self._category_mode_filter,
            "pno_category_profile_set": describe_pno_category_profile_set(
                params,
                category_mode=self._category_mode_filter,
            ),
            "pno_entry_confirmation_mode": params.entry_confirmation_mode,
            "pno_levels_timeframe": params.levels_timeframe.value,
            "pno_entry_timeframe": params.entry_timeframe.value,
            "pno_deposit": params.pno_deposit,
            "pno_risk_pct": params.pno_risk_pct,
            "pno_r_trade": params.pno_r_trade if params.pno_r_trade is not None else params.pno_deposit * params.pno_risk_pct,
            "pno_fee_rate": params.fee_rate,
            "pno_min_data_5m": params.min_data_5m,
            "pno_min_data_1m": params.min_data_1m,
            "pno_min_stage1_leg_v1": params.min_stage1_leg_v1,
            "pno_min_stage1_leg_v5_fraction": params.min_stage1_leg_v5_fraction,
            "pno_stage1_hold_fraction": params.stage1_hold_fraction,
            "pno_pullback_min_v1": params.pullback_min_v1,
            "pno_pullback_min_pump_fraction_5m": params.pullback_min_pump_fraction_5m,
            "pno_pullback_valid_min_leg_fraction": params.pullback_valid_min_leg_fraction,
            "pno_pullback_valid_max_leg_fraction": params.pullback_valid_max_leg_fraction,
            "pno_pullback_invalid_max_leg_fraction": params.pullback_invalid_max_leg_fraction,
            "pno_pullback_valid_max_v5": params.pullback_valid_max_v5,
            "pno_pullback_invalid_max_v5": params.pullback_invalid_max_v5,
            "pno_pullback_max_age_bars": params.pullback_max_age_bars,
            "pno_structure_min_leg_bars": params.structure_min_leg_bars,
            "pno_structure_terminal_retrace_fraction": params.structure_terminal_retrace_fraction,
            "pno_stage1_min_cumulative_quote_volume": params.stage1_min_cumulative_quote_volume,
            "pno_stage1_pre_pump_ema_crosses_min": params.stage1_pre_pump_ema_crosses_min,
            "pno_stage1_barcode_max_fraction_1h": params.stage1_barcode_max_fraction_1h,
            "pno_stage1_barcode_tr_atr_fraction": params.stage1_barcode_tr_atr_fraction,
            "pno_stage1_barcode_tr_price_fraction": params.stage1_barcode_tr_price_fraction,
            "pno_stage1_min_impulse_atr_pre": params.stage1_min_impulse_atr_pre,
            "pno_stage1_min_peak_bar_tr_atr_pre": params.stage1_min_peak_bar_tr_atr_pre,
            "pno_stage1_min_volume_ratio_start": params.stage1_min_volume_ratio_start,
            "pno_stage1_min_trade_ratio_start": params.stage1_min_trade_ratio_start,
            "pno_stage1_min_volume_ratio_continue": params.stage1_min_volume_ratio_continue,
            "pno_stage1_min_trade_ratio_continue": params.stage1_min_trade_ratio_continue,
            "pno_stage1_flow_hold_bars": params.stage1_flow_hold_bars,
            "pno_stage1_flow_hold_window_bars": params.stage1_flow_hold_window_bars,
            "pno_stage1_flow_hold_min_start_fraction": params.stage1_flow_hold_min_start_fraction,
            "pno_stage1_active_context_min_start_fraction": params.stage1_active_context_min_start_fraction,
            "pno_stage1_active_context_min_baseline_ratio": params.stage1_active_context_min_baseline_ratio,
            "pno_stage1_min_path_efficiency": params.stage1_min_path_efficiency,
            "pno_stage1_max_wick_share": params.stage1_max_wick_share,
            "pno_stage1_min_body_share_mean": params.stage1_min_body_share_mean,
            "pno_stage1_max_flat_body_share": params.stage1_max_flat_body_share,
            "pno_stage1_min_body_wick_edge": params.stage1_min_body_wick_edge,
            "pno_stage1_max_micro_flat_bar_share": params.stage1_max_micro_flat_bar_share,
            "pno_stage1_max_active_high_upper_wick_share": params.stage1_max_active_high_upper_wick_share,
            "pno_stage1_max_red_body_share_5m": params.stage1_max_red_body_share_5m,
            "pno_stage1_max_counterflow_ratio_5m": params.stage1_max_counterflow_ratio_5m,
            "pno_stage1_max_red_body_share_1m": params.stage1_max_red_body_share_1m,
            "pno_stage1_max_counterflow_ratio_1m": params.stage1_max_counterflow_ratio_1m,
            "pno_stage1_min_pump_pct": params.stage1_min_pump_pct,
            "pno_stage1_min_pretrend_range_ratio_2h": params.stage1_min_pretrend_range_ratio_2h,
            "pno_stage1_pre_pump_high_max_fraction_of_leg": params.stage1_pre_pump_high_max_fraction_of_leg,
            "pno_stage3_max_post_high_wick_share": params.stage3_max_post_high_wick_share,
            "pno_stage3_max_post_high_body_overlap_rate": params.stage3_max_post_high_body_overlap_rate,
            "pno_stage3_min_post_high_5m_volume_support_fraction": params.stage3_min_post_high_5m_volume_support_fraction,
            "pno_stage3_fast_reclaim_min_post_high_5m_volume_support_fraction": (
                params.stage3_fast_reclaim_min_post_high_5m_volume_support_fraction
            ),
            "pno_stage3_fast_reclaim_max_pullback_age_bars": params.stage3_fast_reclaim_max_pullback_age_bars,
            "pno_ideal_like_impulse_enabled": params.ideal_like_impulse_enabled,
            "pno_ideal_like_min_impulse_atr_pre": params.ideal_like_min_impulse_atr_pre,
            "pno_ideal_like_min_peak_bar_tr_atr_pre": params.ideal_like_min_peak_bar_tr_atr_pre,
            "pno_ideal_like_min_volume_ratio_start": params.ideal_like_min_volume_ratio_start,
            "pno_ideal_like_min_path_efficiency": params.ideal_like_min_path_efficiency,
            "pno_ideal_like_max_wick_share": params.ideal_like_max_wick_share,
            "pno_ideal_like_min_body_share_mean": params.ideal_like_min_body_share_mean,
            "pno_ideal_like_min_body_wick_edge": params.ideal_like_min_body_wick_edge,
            "pno_ideal_like_max_micro_flat_bar_share": params.ideal_like_max_micro_flat_bar_share,
            "pno_ideal_like_max_active_high_upper_wick_share": params.ideal_like_max_active_high_upper_wick_share,
            "pno_ideal_like_max_counterflow_ratio_5m": params.ideal_like_max_counterflow_ratio_5m,
            "pno_ideal_like_relaxed_level_maturity_fraction": params.ideal_like_relaxed_level_maturity_fraction,
            "pno_ideal_like_level_latest_high_max_age_bars": params.ideal_like_level_latest_high_max_age_bars,
            "pno_ideal_like_ignore_decay_invalidation": params.ideal_like_ignore_decay_invalidation,
            "pno_level_cluster_spread_v1": params.level_cluster_spread_v1,
            "pno_level_cluster_relaxed_spread_v1": params.level_cluster_relaxed_spread_v1,
            "pno_level_latest_high_max_age_bars": params.level_latest_high_max_age_bars,
            "pno_level_max_age_bars_upper_tf": params.level_max_age_bars_upper_tf,
            "pno_level_touch_tolerance_v1": params.level_touch_tolerance_v1,
            "pno_level_low_minor_break_v1": params.level_low_minor_break_v1,
            "pno_level_low_major_break_v1": params.level_low_major_break_v1,
            "pno_level_min_maturity_fraction": params.level_min_maturity_fraction,
            "pno_level_rearm_min_distance_v1": params.level_rearm_min_distance_v1,
            "pno_max_level_touches": params.max_level_touches,
            "pno_min_score": params.min_score,
            "pno_strong_score": params.strong_score,
            "pno_slip_plan_v1_fraction": params.slip_plan_v1_fraction,
            "pno_min_tick_fraction": params.min_tick_fraction,
            "pno_max_entry_pullback_fraction": params.max_entry_pullback_fraction,
            "pno_min_entry_rr": params.min_entry_rr,
            "pno_close_above_max_entry_pos": params.close_above_max_entry_pos,
            "pno_close_above_min_entry_pos": params.close_above_min_entry_pos,
            "pno_close_above_max_pullback_fraction_of_leg": params.close_above_max_pullback_fraction_of_leg,
            "pno_close_above_max_post_high_wick_share": params.close_above_max_post_high_wick_share,
            "pno_close_above_max_active_high_upper_wick_share": params.close_above_max_active_high_upper_wick_share,
            "pno_close_above_min_active_high_close_position": params.close_above_min_active_high_close_position,
            "pno_close_above_min_post_high_alternation_rate": params.close_above_min_post_high_alternation_rate,
            "pno_close_above_min_signal_volume_vs_recent": params.close_above_min_signal_volume_vs_recent,
            "pno_close_above_min_signal_ema9_slope_3": params.close_above_min_signal_ema9_slope_3,
            "pno_close_above_min_signal_ema20_slope_3": params.close_above_min_signal_ema20_slope_3,
            "pno_close_above_min_signal_ema_spread_pct": params.close_above_min_signal_ema_spread_pct,
            "pno_close_above_min_signal_close_position_in_chop": params.close_above_min_signal_close_position_in_chop,
            "pno_close_above_choppy_overlap_threshold": params.close_above_choppy_overlap_threshold,
            "pno_tp1_share": params.tp1_share,
            "pno_be_arm_to_active_high_fraction": params.be_arm_to_active_high_fraction,
            "pno_close_above_be_start_fraction": params.close_above_be_start_fraction,
            "pno_close_above_be_step_fraction": params.close_above_be_step_fraction,
            "pno_close_above_be_step_bars": params.close_above_be_step_bars,
            "pno_close_above_be_min_fraction": params.close_above_be_min_fraction,
            "pno_be_buffer_r_fraction": params.be_buffer_r_fraction,
            "deposit": params.pno_deposit,
        }

    def consume_last_generation_diagnostics(self) -> dict[str, object]:
        diagnostics = dict(self._last_generation_diagnostics)
        stage_hits = diagnostics.get("stage_hits")
        if isinstance(stage_hits, dict):
            diagnostics["stage_hits"] = dict(stage_hits)
        stage_events = diagnostics.get("stage_events")
        if isinstance(stage_events, list):
            diagnostics["stage_events"] = [dict(item) for item in stage_events]
        stage_rejections = diagnostics.get("stage_rejections")
        if isinstance(stage_rejections, list):
            diagnostics["stage_rejections"] = [dict(item) for item in stage_rejections]
        context = diagnostics.get("context")
        if isinstance(context, dict):
            diagnostics["context"] = dict(context)
        self._last_generation_diagnostics = {}
        return diagnostics

    def _generate_events_for_profiles(
        self,
        *,
        profiles: tuple[PnoCategoryProfile, ...],
        runner: Any,
    ) -> list[TradeResult]:
        combined_diagnostics = self._empty_generation_diagnostics()
        seen_trade_keys: set[tuple[object, ...]] = set()
        trades: list[TradeResult] = []
        profile_contexts: dict[str, object] = {}
        self._engine.begin_runtime_batch()
        try:
            for profile in profiles:
                profile_trades = runner(profile.params)
                profile_diagnostics = self._engine.consume_last_generation_diagnostics()
                tagged_diagnostics = self._tag_profile_diagnostics(profile_diagnostics, profile=profile)
                self._merge_generation_diagnostics(combined_diagnostics, tagged_diagnostics)
                profile_contexts[profile.category_id] = dict(tagged_diagnostics.get("context", {}))

                for trade in profile_trades:
                    tagged_trade = self._tag_trade_result(trade, profile=profile)
                    trade_key = self._trade_dedup_key(tagged_trade, symbol=profile.params.symbol)
                    if trade_key in seen_trade_keys:
                        continue
                    seen_trade_keys.add(trade_key)
                    trades.append(tagged_trade)
        finally:
            self._engine.end_runtime_batch()

        trades.sort(key=lambda trade: (trade.entry_timestamp_ms, trade.exit_timestamp_ms))
        combined_diagnostics["trades_generated"] = len(trades)
        context = combined_diagnostics.setdefault("context", {})
        if isinstance(context, dict):
            context["category_mode"] = "multi_profile" if len(profiles) > 1 else "single_profile"
            context["category_profiles_run"] = [profile.category_id for profile in profiles]
            context["category_profile_labels"] = {
                profile.category_id: profile.label for profile in profiles
            }
            context["category_profile_priorities"] = {
                profile.category_id: profile.priority for profile in profiles
            }
            context["category_profile_contexts"] = profile_contexts
        self._last_generation_diagnostics = combined_diagnostics
        return trades

    @staticmethod
    def _empty_generation_diagnostics() -> dict[str, object]:
        return {
            "trades_generated": 0,
            "blocked_cycles": 0,
            "skipped_insufficient_data": 0,
            "trade_count_proxy_used": True,
            "stage_hits": {},
            "stage_events": [],
            "stage_rejections": [],
            "context": {},
        }

    @staticmethod
    def _trade_dedup_key(trade: TradeResult, *, symbol: str) -> tuple[object, ...]:
        metadata = trade.metadata or {}
        actual_entry = metadata.get("entry_price_actual")
        entry_level = actual_entry if isinstance(actual_entry, (int, float)) else trade.entry_price.value
        trade_symbol = str(metadata.get("symbol") or symbol)
        return (
            trade_symbol,
            int(trade.entry_timestamp_ms),
            round(float(entry_level), 10),
            metadata.get("level"),
        )

    @staticmethod
    def _tag_trade_result(trade: TradeResult, *, profile: PnoCategoryProfile) -> TradeResult:
        metadata = dict(trade.metadata or {})
        metadata["pno_category_id"] = profile.category_id
        metadata["pno_category_label"] = profile.label
        metadata["pno_category_priority"] = profile.priority
        metadata["pno_profile_variant_id"] = profile.params.pno_variant_id
        return replace(trade, metadata=metadata)

    @staticmethod
    def _tag_profile_diagnostics(
        diagnostics: dict[str, object],
        *,
        profile: PnoCategoryProfile,
    ) -> dict[str, object]:
        tagged = dict(diagnostics)
        stage_events = tagged.get("stage_events")
        if isinstance(stage_events, list):
            tagged["stage_events"] = [
                {
                    **dict(event),
                    "pno_category_id": profile.category_id,
                    "pno_category_label": profile.label,
                    "pno_category_priority": profile.priority,
                }
                for event in stage_events
            ]
        stage_rejections = tagged.get("stage_rejections")
        if isinstance(stage_rejections, list):
            tagged["stage_rejections"] = [
                {
                    **dict(event),
                    "pno_category_id": profile.category_id,
                    "pno_category_label": profile.label,
                    "pno_category_priority": profile.priority,
                }
                for event in stage_rejections
            ]
        context = tagged.get("context")
        if isinstance(context, dict):
            tagged["context"] = {
                **context,
                "pno_category_id": profile.category_id,
                "pno_category_label": profile.label,
                "pno_category_priority": profile.priority,
                "pno_profile_variant_id": profile.params.pno_variant_id,
            }
        return tagged

    @staticmethod
    def _merge_generation_diagnostics(
        combined: dict[str, object],
        incoming: dict[str, object],
    ) -> None:
        for key in ("blocked_cycles", "skipped_insufficient_data"):
            combined[key] = int(combined.get(key, 0)) + int(incoming.get(key, 0))

        combined["trade_count_proxy_used"] = bool(combined.get("trade_count_proxy_used", True)) and bool(
            incoming.get("trade_count_proxy_used", True)
        )

        combined_stage_hits = combined.setdefault("stage_hits", {})
        incoming_stage_hits = incoming.get("stage_hits")
        if isinstance(combined_stage_hits, dict) and isinstance(incoming_stage_hits, dict):
            for stage_id, hit_count in incoming_stage_hits.items():
                combined_stage_hits[str(stage_id)] = int(combined_stage_hits.get(str(stage_id), 0)) + int(hit_count)

        combined_stage_events = combined.setdefault("stage_events", [])
        incoming_stage_events = incoming.get("stage_events")
        if isinstance(combined_stage_events, list) and isinstance(incoming_stage_events, list):
            combined_stage_events.extend(dict(event) for event in incoming_stage_events)

        combined_stage_rejections = combined.setdefault("stage_rejections", [])
        incoming_stage_rejections = incoming.get("stage_rejections")
        if isinstance(combined_stage_rejections, list) and isinstance(incoming_stage_rejections, list):
            combined_stage_rejections.extend(dict(event) for event in incoming_stage_rejections)

        combined_context = combined.setdefault("context", {})
        incoming_context = incoming.get("context")
        if isinstance(combined_context, dict) and isinstance(incoming_context, dict):
            stage_order = incoming_context.get("stage_order")
            if stage_order is not None:
                combined_context.setdefault("stage_order", list(stage_order))
            for passthrough_key in (
                "trade_count_proxy",
                "quote_volume_proxy",
                "symbol",
            ):
                if passthrough_key in incoming_context:
                    combined_context.setdefault(passthrough_key, incoming_context[passthrough_key])
