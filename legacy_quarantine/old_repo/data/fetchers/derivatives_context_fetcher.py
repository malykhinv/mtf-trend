"""Fetch optional Binance derivatives context for anomaly research artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from urllib.parse import quote

import pandas as pd

from constants import DEFAULT_LOG_LEVEL, DEFAULT_LOGS_DIR
from data.exchanges.ccxt_futures_client import CcxtFuturesClient
from data.quality.deduplicator import Deduplicator
from domain.models.reporting.symbol_fetch_result import SymbolFetchResult
from utils.logger import get_logger


@dataclass(frozen=True, slots=True)
class DerivativesContextSpec:
    name: str
    path_parts: tuple[str, ...]
    columns: tuple[str, ...]
    interval_ms: int
    availability_lag_ms: int
    period: str = "5m"
    limit: int = 500


DERIVATIVES_CONTEXT_1M_MS = 60 * 1000
DERIVATIVES_CONTEXT_5M_MS = 5 * 60 * 1000
DERIVATIVES_CONTEXT_8H_MS = 8 * 60 * 60 * 1000

DERIVATIVES_CONTEXT_FETCH_SPECS: tuple[DerivativesContextSpec, ...] = (
    DerivativesContextSpec(
        name="funding",
        path_parts=("funding_rate",),
        columns=("timestamp", "available_timestamp_ms", "funding_rate"),
        interval_ms=DERIVATIVES_CONTEXT_8H_MS,
        availability_lag_ms=0,
        period="8h",
        limit=1000,
    ),
    DerivativesContextSpec(
        name="premium",
        path_parts=("premium_index", "5m"),
        columns=("timestamp", "available_timestamp_ms", "open", "high", "low", "close"),
        interval_ms=DERIVATIVES_CONTEXT_5M_MS,
        availability_lag_ms=DERIVATIVES_CONTEXT_5M_MS,
        period="5m",
    ),
    DerivativesContextSpec(
        name="mark",
        path_parts=("mark_price", "1m"),
        columns=("timestamp", "available_timestamp_ms", "open", "high", "low", "close"),
        interval_ms=DERIVATIVES_CONTEXT_1M_MS,
        availability_lag_ms=DERIVATIVES_CONTEXT_1M_MS,
        period="1m",
    ),
    DerivativesContextSpec(
        name="global_ls",
        path_parts=("global_long_short_account_ratio", "5m"),
        columns=("timestamp", "available_timestamp_ms", "long_short_ratio", "long_account", "short_account"),
        interval_ms=DERIVATIVES_CONTEXT_5M_MS,
        availability_lag_ms=DERIVATIVES_CONTEXT_5M_MS,
        period="5m",
    ),
    DerivativesContextSpec(
        name="top_account_ls",
        path_parts=("top_long_short_account_ratio", "5m"),
        columns=("timestamp", "available_timestamp_ms", "long_short_ratio", "long_account", "short_account"),
        interval_ms=DERIVATIVES_CONTEXT_5M_MS,
        availability_lag_ms=DERIVATIVES_CONTEXT_5M_MS,
        period="5m",
    ),
    DerivativesContextSpec(
        name="top_position_ls",
        path_parts=("top_long_short_position_ratio", "5m"),
        columns=("timestamp", "available_timestamp_ms", "long_short_ratio", "long_account", "short_account"),
        interval_ms=DERIVATIVES_CONTEXT_5M_MS,
        availability_lag_ms=DERIVATIVES_CONTEXT_5M_MS,
        period="5m",
    ),
    DerivativesContextSpec(
        name="taker_ls",
        path_parts=("taker_long_short_ratio", "5m"),
        columns=("timestamp", "available_timestamp_ms", "buy_sell_ratio", "buy_vol", "sell_vol"),
        interval_ms=DERIVATIVES_CONTEXT_5M_MS,
        availability_lag_ms=DERIVATIVES_CONTEXT_5M_MS,
        period="5m",
    ),
)


class DerivativesContextFetcher:
    _MAX_EXCHANGE_LOOKBACK_MS = 30 * 24 * 60 * 60 * 1000

    def __init__(
        self,
        *,
        exchange_client: CcxtFuturesClient,
        cache_dir: str | Path,
        log_level: int | str = DEFAULT_LOG_LEVEL,
        logs_dir: str | Path = DEFAULT_LOGS_DIR,
    ) -> None:
        self._exchange_client = exchange_client
        self._cache_dir = Path(cache_dir)
        self._logger = get_logger(self.__class__.__name__, level=log_level, logs_dir=logs_dir)
        self._deduplicator = Deduplicator()

    @staticmethod
    def _symbol_dir(symbol: str) -> str:
        return quote(str(symbol), safe="")

    def _path(self, symbol: str, spec: DerivativesContextSpec) -> Path:
        path = self._cache_dir / self._symbol_dir(symbol)
        for part in spec.path_parts:
            path /= part
        return path / "data.parquet"

    @staticmethod
    def _empty_prepared_frame(columns: tuple[str, ...]) -> pd.DataFrame:
        return pd.DataFrame(columns=list(columns))

    @classmethod
    def _prepare_frame(
        cls,
        frame: pd.DataFrame,
        spec: DerivativesContextSpec,
        *,
        drop_legacy_without_availability: bool = False,
    ) -> pd.DataFrame:
        columns = spec.columns
        if frame.empty:
            return cls._empty_prepared_frame(columns)
        missing = [column for column in columns if column not in frame.columns]
        if missing:
            if drop_legacy_without_availability and missing == ["available_timestamp_ms"]:
                return cls._empty_prepared_frame(columns)
            raise ValueError(f"derivatives_context_missing_columns:{','.join(missing)}")
        prepared = frame.loc[:, list(columns)].copy()
        for column in columns:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
        prepared.dropna(subset=["timestamp", "available_timestamp_ms"], inplace=True)
        prepared = prepared.loc[prepared["available_timestamp_ms"].ge(prepared["timestamp"])]
        prepared.drop_duplicates("timestamp", keep="last", inplace=True)
        prepared.sort_values(["timestamp", "available_timestamp_ms"], inplace=True)
        prepared.reset_index(drop=True, inplace=True)
        return prepared

    def _save_incremental(self, symbol: str, spec: DerivativesContextSpec, incoming: pd.DataFrame) -> int:
        incoming = self._prepare_frame(incoming, spec)
        if incoming.empty:
            return 0
        path = self._path(symbol, spec)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = self._prepare_frame(pd.read_parquet(path), spec, drop_legacy_without_availability=True)
            previous_count = len(existing)
            merged = pd.concat([existing, incoming], ignore_index=True)
        else:
            previous_count = 0
            merged = incoming
        merged = self._prepare_frame(merged, spec)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        merged.to_parquet(tmp_path, index=False)
        written = pd.read_parquet(tmp_path)
        if len(written) != len(merged) or "timestamp" not in written.columns:
            raise ValueError(f"derivatives_context_cache_validation_failed:{path}")
        tmp_path.replace(path)
        return max(len(merged) - previous_count, 0)

    def _cached_timestamp_bounds(self, symbol: str, spec: DerivativesContextSpec) -> tuple[int | None, int | None]:
        path = self._path(symbol, spec)
        if not path.exists():
            return None, None
        frame = self._prepare_frame(pd.read_parquet(path), spec, drop_legacy_without_availability=True)
        if frame.empty:
            return None, None
        timestamps = frame["timestamp"].dropna()
        if timestamps.empty:
            return None, None
        return int(timestamps.min()), int(timestamps.max())

    def fetch_symbol(self, symbol: str, start_timestamp_ms: int, end_timestamp_ms: int) -> int:
        added_rows = 0
        for spec in DERIVATIVES_CONTEXT_FETCH_SPECS:
            min_supported_start_ms = int(time.time() * 1000) - self._MAX_EXCHANGE_LOOKBACK_MS + spec.interval_ms
            requested_start_ms = max(int(start_timestamp_ms), min_supported_start_ms)
            requested_start_ms = ((requested_start_ms + spec.interval_ms - 1) // spec.interval_ms) * spec.interval_ms
            end_ms = (int(end_timestamp_ms) // spec.interval_ms) * spec.interval_ms
            if requested_start_ms > end_ms:
                self._logger.debug(
                    "Derivatives context %s %s: after exchange lookback clamp nothing to fetch.",
                    symbol,
                    spec.name,
                )
                continue

            cached_first_ms, cached_last_ms = self._cached_timestamp_bounds(symbol, spec)
            segments: list[tuple[int, int, str]] = []
            if cached_first_ms is None or cached_last_ms is None:
                segments.append((requested_start_ms, end_ms, "full"))
            else:
                prefix_end_ms = min(end_ms, cached_first_ms - spec.interval_ms)
                if requested_start_ms <= prefix_end_ms:
                    segments.append((requested_start_ms, prefix_end_ms, "prefix"))
                suffix_start_ms = max(requested_start_ms, cached_last_ms + spec.interval_ms)
                if suffix_start_ms <= end_ms:
                    segments.append((suffix_start_ms, end_ms, "suffix"))
            if not segments:
                self._logger.debug("Derivatives context %s %s: cache is up to date.", symbol, spec.name)
                continue

            rows: list[pd.DataFrame] = []
            for segment_start_ms, segment_stop_ms, segment_kind in segments:
                self._logger.debug(
                    "Derivatives context %s %s: fetching %s segment %s..%s.",
                    symbol,
                    spec.name,
                    segment_kind,
                    segment_start_ms,
                    segment_stop_ms,
                )
                cursor_ms = segment_start_ms
                while cursor_ms <= segment_stop_ms:
                    chunk_end_ms = min(
                        segment_stop_ms,
                        cursor_ms + spec.interval_ms * spec.limit - 1,
                    )
                    data = self._exchange_client.fetch_binance_derivatives_context(
                        symbol=symbol,
                        source=spec.name,
                        start_timestamp_ms=cursor_ms,
                        end_timestamp_ms=chunk_end_ms,
                        period=spec.period,
                        limit=spec.limit,
                    )
                    if not data.empty:
                        rows.append(data)
                        last_timestamp = int(data["timestamp"].max())
                        cursor_ms = max(last_timestamp + spec.interval_ms, chunk_end_ms + 1)
                    else:
                        cursor_ms = chunk_end_ms + 1
            data = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=list(spec.columns))
            data = self._deduplicator.deduplicate(data)
            added_rows += self._save_incremental(symbol, spec, data)
        return added_rows

    def fetch_many(
        self,
        symbols: list[str],
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> dict[str, SymbolFetchResult]:
        results: dict[str, SymbolFetchResult] = {}
        total = len(symbols)
        for index, symbol in enumerate(symbols, start=1):
            try:
                self._logger.warning("Derivatives context: %s/%s %s", index, total, symbol)
                added_rows = self.fetch_symbol(symbol, start_timestamp_ms, end_timestamp_ms)
                results[symbol] = SymbolFetchResult.ok(added_rows)
            except Exception as exc:
                message = f"derivatives_context_error: {symbol}: {type(exc).__name__}: {exc}"
                self._logger.exception(message)
                results[symbol] = SymbolFetchResult.error(message)
        return results
