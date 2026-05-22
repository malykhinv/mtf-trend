"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass
import gc
from logging import INFO
from pathlib import Path
import time
from urllib.parse import quote, unquote

import pandas as pd

from domain.enums.timeframe import Timeframe
from utils.logger import get_logger


class ParquetCacheValidationError(ValueError):
    """Ошибка валидации записанного parquet-кэша."""


@dataclass(frozen=True, slots=True)
class ParquetLoadResult:
    frame: pd.DataFrame
    ok: bool
    status: str
    reason: str
    path: Path
    rows: int = 0
    missing_columns: tuple[str, ...] = ()


class ParquetStorage:
    """Класс."""

    def __init__(
        self,
        base_dir: str | Path = "cache",
        log_level: int | str = INFO,
        logs_dir: str | Path = "logs",
    ) -> None:
        self._base_dir = Path(base_dir)
        self._logger = get_logger(name="parquet-storage", level=log_level, logs_dir=logs_dir)

    @staticmethod
    def encode_symbol_for_path(symbol: str) -> str:
        return quote(str(symbol), safe="")

    @staticmethod
    def decode_symbol_from_path(value: str) -> str:
        return unquote(str(value))

    def _data_path(self, symbol: str, timeframe: Timeframe) -> Path:
        return self._base_dir / self.encode_symbol_for_path(symbol) / timeframe.value / "data.parquet"

    def _delta_dir(self, symbol: str, timeframe: Timeframe) -> Path:
        return self._data_path(symbol, timeframe).parent / "delta"

    def _delta_paths(self, symbol: str, timeframe: Timeframe) -> list[Path]:
        delta_dir = self._delta_dir(symbol, timeframe)
        if not delta_dir.exists():
            return []
        return sorted(path for path in delta_dir.glob("*.parquet") if path.is_file())

    @staticmethod
    def _ensure_columns(data: pd.DataFrame) -> pd.DataFrame:
        if "timestamp" not in data.columns:
            raise ValueError("В данных нет колонки timestamp.")

        prepared = data.copy()
        prepared = prepared.loc[prepared["timestamp"].notna()].copy()
        return prepared

    def _load_delta_frame(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        start_timestamp_ms: int | None = None,
        end_timestamp_ms: int | None = None,
    ) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        filters = None
        if start_timestamp_ms is not None and end_timestamp_ms is not None:
            filters = [
                ("timestamp", ">=", int(start_timestamp_ms)),
                ("timestamp", "<=", int(end_timestamp_ms)),
            ]
        for path in self._delta_paths(symbol, timeframe):
            try:
                frame = pd.read_parquet(path, filters=filters)
            except Exception:
                try:
                    frame = pd.read_parquet(path)
                except Exception:
                    continue
                if start_timestamp_ms is not None and end_timestamp_ms is not None and "timestamp" in frame.columns:
                    timestamps = pd.to_numeric(frame["timestamp"], errors="coerce")
                    frame = frame.loc[
                        (timestamps >= int(start_timestamp_ms))
                        & (timestamps <= int(end_timestamp_ms))
                    ].copy()
            if frame.empty:
                continue
            try:
                frames.append(self._ensure_columns(frame))
            except ValueError:
                continue
        if not frames:
            return pd.DataFrame()
        merged = pd.concat(frames, ignore_index=True, sort=False)
        return (
            self._ensure_columns(merged)
            .drop_duplicates(subset=["timestamp"], keep="last")
            .sort_values("timestamp")
            .reset_index(drop=True)
        )

    @staticmethod
    def _add_ohlcv_availability_columns(data: pd.DataFrame, timeframe: Timeframe) -> pd.DataFrame:
        """Attach explicit candle availability timestamps.

        Cached OHLCV rows use exchange candle open time in `timestamp`.  The
        values `high/low/close/volume/flow` are only known after the candle
        closes, so downstream as-of code must not treat `timestamp` as the
        information-availability time.
        """
        if data.empty or "timestamp" not in data.columns:
            return data
        prepared = data.copy()
        timestamps = pd.to_numeric(prepared["timestamp"], errors="coerce")
        timeframe_ms = int(timeframe.to_milliseconds())
        prepared["candle_open_timestamp_ms"] = timestamps
        prepared["candle_close_timestamp_ms"] = timestamps + timeframe_ms
        prepared["available_timestamp_ms"] = prepared["candle_close_timestamp_ms"]
        return prepared

    def _merge_base_and_delta(self, base: pd.DataFrame, delta: pd.DataFrame) -> pd.DataFrame:
        if base.empty:
            return delta
        if delta.empty:
            return base
        merged = pd.concat([base, delta], ignore_index=True, sort=False)
        return (
            self._ensure_columns(merged)
            .drop_duplicates(subset=["timestamp"], keep="last")
            .sort_values("timestamp")
            .reset_index(drop=True)
        )

    def _validate_written_cache(
        self,
        symbol: str,
        timeframe: Timeframe,
        previous_count: int,
        incoming: pd.DataFrame,
        merged: pd.DataFrame,
        path_override: Path | None = None,
    ) -> None:
        path = path_override or self._data_path(symbol, timeframe)
        written = pd.read_parquet(path)
        added_rows = max(len(merged) - previous_count, 0)

        if len(written) < previous_count or len(written) != previous_count + added_rows:
            raise ParquetCacheValidationError(
                "Проверка parquet-кэша не прошла: "
                f"symbol={symbol}, timeframe={timeframe.value}, path={path}, "
                f"expected_rows={previous_count}+{added_rows}, actual_rows={len(written)}"
            )

        if "timestamp" not in written.columns:
            raise ParquetCacheValidationError(
                "Проверка parquet-кэша не прошла: "
                f"symbol={symbol}, timeframe={timeframe.value}, path={path}, missing_column=timestamp"
            )

        incoming_ts = set(incoming["timestamp"].dropna().tolist())
        written_ts = set(written["timestamp"].dropna().tolist())
        missing = incoming_ts - written_ts
        if missing:
            raise ParquetCacheValidationError(
                "Проверка parquet-кэша не прошла: "
                f"symbol={symbol}, timeframe={timeframe.value}, path={path}, "
                f"missing_written_batch_rows={sorted(list(missing))[:10]}"
            )

    def load_result(self, symbol: str, timeframe: Timeframe) -> ParquetLoadResult:
        path = self._data_path(symbol, timeframe)
        if not path.exists():
            delta = self._load_delta_frame(symbol, timeframe)
            if not delta.empty:
                return ParquetLoadResult(
                    frame=self._add_ohlcv_availability_columns(delta, timeframe),
                    ok=True,
                    status="ok",
                    reason="parquet_delta_loaded",
                    path=path,
                    rows=int(len(delta)),
                )
            return ParquetLoadResult(
                frame=pd.DataFrame(),
                ok=False,
                status="missing",
                reason="parquet_file_missing",
                path=path,
            )
        try:
            frame = pd.read_parquet(path)
        except Exception as exc:
            return ParquetLoadResult(
                frame=pd.DataFrame(),
                ok=False,
                status="read_failed",
                reason=f"parquet_read_failed:{type(exc).__name__}",
                path=path,
            )
        delta = self._load_delta_frame(symbol, timeframe)
        if frame.empty and delta.empty:
            return ParquetLoadResult(
                frame=frame,
                ok=False,
                status="empty",
                reason="parquet_file_empty",
                path=path,
                rows=0,
            )
        try:
            prepared = self._merge_base_and_delta(self._ensure_columns(frame), delta)
        except ValueError:
            return ParquetLoadResult(
                frame=pd.DataFrame(),
                ok=False,
                status="schema_invalid",
                reason="parquet_missing_timestamp",
                path=path,
                rows=int(len(frame)),
                missing_columns=("timestamp",),
            )
        prepared = self._add_ohlcv_availability_columns(prepared, timeframe)
        return ParquetLoadResult(
            frame=prepared,
            ok=True,
            status="ok",
            reason="parquet_loaded_with_delta" if not delta.empty else "parquet_loaded",
            path=path,
            rows=int(len(prepared)),
        )

    def load(self, symbol: str, timeframe: Timeframe) -> pd.DataFrame:
        return self.load_result(symbol, timeframe).frame

    def load_window_result(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> ParquetLoadResult:
        path = self._data_path(symbol, timeframe)
        if not path.exists():
            delta = self._load_delta_frame(
                symbol,
                timeframe,
                start_timestamp_ms=int(start_timestamp_ms),
                end_timestamp_ms=int(end_timestamp_ms),
            )
            if not delta.empty:
                return ParquetLoadResult(
                    frame=self._add_ohlcv_availability_columns(delta, timeframe),
                    ok=True,
                    status="ok",
                    reason="parquet_delta_window_loaded",
                    path=path,
                    rows=int(len(delta)),
                )
            return ParquetLoadResult(
                frame=pd.DataFrame(),
                ok=False,
                status="missing",
                reason="parquet_file_missing",
                path=path,
            )
        try:
            frame = pd.read_parquet(
                path,
                filters=[
                    ("timestamp", ">=", int(start_timestamp_ms)),
                    ("timestamp", "<=", int(end_timestamp_ms)),
                ],
            )
        except Exception:
            base = self.load_result(symbol, timeframe)
            if not base.ok or base.frame.empty or "timestamp" not in base.frame.columns:
                return base
            timestamps = pd.to_numeric(base.frame["timestamp"], errors="coerce")
            frame = base.frame.loc[
                (timestamps >= int(start_timestamp_ms))
                & (timestamps <= int(end_timestamp_ms))
            ].copy()
        delta = self._load_delta_frame(
            symbol,
            timeframe,
            start_timestamp_ms=int(start_timestamp_ms),
            end_timestamp_ms=int(end_timestamp_ms),
        )
        if frame.empty and delta.empty:
            return ParquetLoadResult(
                frame=frame,
                ok=False,
                status="empty_window",
                reason="parquet_window_empty",
                path=path,
                rows=0,
            )
        try:
            prepared = self._merge_base_and_delta(self._ensure_columns(frame), delta)
        except ValueError:
            return ParquetLoadResult(
                frame=pd.DataFrame(),
                ok=False,
                status="schema_invalid",
                reason="parquet_missing_timestamp",
                path=path,
                rows=int(len(frame)),
                missing_columns=("timestamp",),
            )
        prepared = self._add_ohlcv_availability_columns(prepared, timeframe)
        return ParquetLoadResult(
            frame=prepared,
            ok=True,
            status="ok",
            reason="parquet_window_loaded_with_delta" if not delta.empty else "parquet_window_loaded",
            path=path,
            rows=int(len(prepared)),
        )

    def get_last_timestamp(self, symbol: str, timeframe: Timeframe) -> int | None:
        data = self.load(symbol, timeframe)
        if data.empty or "timestamp" not in data.columns:
            return None

        timestamps = data["timestamp"].dropna()
        if timestamps.empty:
            return None
        return int(timestamps.max())

    def get_first_timestamp(self, symbol: str, timeframe: Timeframe) -> int | None:
        data = self.load(symbol, timeframe)
        if data.empty or "timestamp" not in data.columns:
            return None

        timestamps = data["timestamp"].dropna()
        if timestamps.empty:
            return None
        return int(timestamps.min())

    def get_last_timestamp_for_column(self, symbol: str, timeframe: Timeframe, column_name: str) -> int | None:
        data = self.load(symbol, timeframe)
        if data.empty or "timestamp" not in data.columns:
            return None

        if column_name == "timestamp":
            return self.get_last_timestamp(symbol, timeframe)

        if column_name not in data.columns:
            return None

        valid_rows = data[column_name].notna()
        if not valid_rows.any():
            return None

        timestamps = data.loc[valid_rows, "timestamp"].dropna()
        if timestamps.empty:
            return None
        return int(timestamps.max())

    def get_first_timestamp_for_column(self, symbol: str, timeframe: Timeframe, column_name: str) -> int | None:
        data = self.load(symbol, timeframe)
        if data.empty or "timestamp" not in data.columns:
            return None

        if column_name == "timestamp":
            return self.get_first_timestamp(symbol, timeframe)

        if column_name not in data.columns:
            return None

        valid_rows = data[column_name].notna()
        if not valid_rows.any():
            return None

        timestamps = data.loc[valid_rows, "timestamp"].dropna()
        if timestamps.empty:
            return None
        return int(timestamps.min())

    def save_incremental(self, symbol: str, timeframe: Timeframe, new_data: pd.DataFrame) -> int:
        if new_data.empty:
            return 0

        path = self._data_path(symbol, timeframe)
        path.parent.mkdir(parents=True, exist_ok=True)

        incoming = self._ensure_columns(new_data)
        existing = self.load(symbol, timeframe)
        previous_count = len(existing)

        if existing.empty:
            merged = incoming
        else:
            merged = pd.merge(existing, incoming, on="timestamp", how="outer", suffixes=("", "__new"))
            for col in list(merged.columns):
                if not col.endswith("__new"):
                    continue
                base_col = col[:-5]
                if base_col in merged.columns:
                    merged[base_col] = merged[col].combine_first(merged[base_col])
                    merged = merged.drop(columns=[col])
                else:
                    merged = merged.rename(columns={col: base_col})

        merged = self._ensure_columns(merged)
        merged = merged.drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp")

        tmp_path = path.with_suffix(path.suffix + ".tmp")
        merged.to_parquet(tmp_path, index=False)

        self._validate_written_cache(
            symbol=symbol,
            timeframe=timeframe,
            previous_count=previous_count,
            incoming=incoming,
            merged=merged,
            path_override=tmp_path,
        )

        for attempt in range(5):
            try:
                tmp_path.replace(path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                gc.collect()
                time.sleep(0.05 * (attempt + 1))
        return max(len(merged) - previous_count, 0)

    def save_incremental_delta(self, symbol: str, timeframe: Timeframe, new_data: pd.DataFrame) -> int:
        if new_data.empty:
            return 0

        incoming = (
            self._ensure_columns(new_data)
            .drop_duplicates(subset=["timestamp"], keep="last")
            .sort_values("timestamp")
            .reset_index(drop=True)
        )
        if incoming.empty:
            return 0

        delta_dir = self._delta_dir(symbol, timeframe)
        delta_dir.mkdir(parents=True, exist_ok=True)
        path = delta_dir / f"{time.time_ns()}.parquet"
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        incoming.to_parquet(tmp_path, index=False)

        written = pd.read_parquet(tmp_path)
        if "timestamp" not in written.columns:
            raise ParquetCacheValidationError(
                "Проверка delta parquet-кэша не прошла: "
                f"symbol={symbol}, timeframe={timeframe.value}, path={tmp_path}, missing_column=timestamp"
            )

        incoming_ts = set(incoming["timestamp"].dropna().tolist())
        written_ts = set(written["timestamp"].dropna().tolist())
        missing = incoming_ts - written_ts
        if missing:
            raise ParquetCacheValidationError(
                "Проверка delta parquet-кэша не прошла: "
                f"symbol={symbol}, timeframe={timeframe.value}, path={tmp_path}, "
                f"missing_written_batch_rows={sorted(list(missing))[:10]}"
            )

        tmp_path.replace(path)
        return int(len(incoming))
