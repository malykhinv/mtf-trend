"""Модуль проекта."""

from __future__ import annotations

from logging import INFO
from pathlib import Path
from urllib.parse import quote, unquote

import pandas as pd

from domain.enums.timeframe import Timeframe
from utils.logger import get_logger


class ParquetCacheValidationError(ValueError):
    """Ошибка валидации записанного parquet-кэша."""


class ParquetStorage:
    """Класс."""
    def __init__(
        self,
        base_dir: str | Path = "cache",
        log_level: int | str = INFO,
        logs_dir: str | Path = "logs",
    ) -> None:
        self._base_dir = Path(base_dir)
        self._logger = get_logger(
            name="parquet-storage",
            level=log_level,
            logs_dir=logs_dir,
        )

    # region Приватные

    @staticmethod
    def encode_symbol_for_path(symbol: str) -> str:
        """Преобразует биржевой символ в безопасное имя каталога."""
        return quote(str(symbol), safe="")

    @staticmethod
    def decode_symbol_from_path(value: str) -> str:
        """Восстанавливает исходный биржевой символ из имени каталога."""
        return unquote(str(value))

    def _data_path(self, symbol: str, timeframe: Timeframe) -> Path:
        symbol_path = self.encode_symbol_for_path(symbol)
        return self._base_dir / symbol_path / timeframe.value / "data.parquet"

    @staticmethod
    def _ensure_utc_columns(data: pd.DataFrame) -> pd.DataFrame:
        normalized = data.copy()
        if "timestamp" not in normalized.columns:
            raise ValueError("data must contain 'timestamp' column")

        ts = pd.to_datetime(normalized["timestamp"], unit="ms", utc=True, errors="coerce")
        normalized = normalized.loc[ts.notna()].copy()
        ts = ts.loc[ts.notna()]

        normalized["timestamp"] = (ts.astype("int64") // 1_000_000).astype("int64")
        normalized["datetime"] = ts
        return normalized

    def _validate_written_cache(
        self,
        symbol: str,
        timeframe: Timeframe,
        previous_count: int,
        incoming: pd.DataFrame,
        merged: pd.DataFrame,
    ) -> None:
        path = self._data_path(symbol, timeframe)
        written = pd.read_parquet(path)
        written_ts_dtype = written["timestamp"].dtype if "timestamp" in written.columns else "<missing>"
        added_rows = max(len(merged) - previous_count, 0)

        self._logger.info(
            "parquet-cache-written-timestamp-dtype: symbol=%s timeframe=%s path=%s dtype=%s",
            symbol,
            timeframe.value,
            path,
            written_ts_dtype,
        )

        def _raise_validation_error(reason: str, **details: object) -> None:
            context_parts = [
                f"reason={reason}",
                f"symbol={symbol}",
                f"timeframe={timeframe.value}",
                f"path={path}",
                f"expected_rows={len(merged)}",
                f"actual_rows={len(written)}",
                f"previous_count={previous_count}",
                f"added_rows={added_rows}",
            ]
            context_parts.extend(f"{key}={value}" for key, value in details.items())
            raise ParquetCacheValidationError("parquet cache validation failed: " + ", ".join(context_parts))

        if len(written) != len(merged):
            if len(written) < previous_count or len(written) != previous_count + added_rows:
                _raise_validation_error(
                    "row_count_mismatch",
                    expected_formula=f"{previous_count}+{added_rows}",
                )

        domain_candidates = ["open", "high", "low", "close", "volume", "open_interest"]
        batch_domain_columns = [column for column in domain_candidates if column in incoming.columns]
        required_columns = ["timestamp", "datetime", *batch_domain_columns]
        missing_columns = [column for column in required_columns if column not in written.columns]
        if missing_columns:
            _raise_validation_error("missing_required_columns", missing_columns=missing_columns)

        if written["timestamp"].isna().any():
            _raise_validation_error("null_timestamp", null_count=int(written["timestamp"].isna().sum()))

        parsed_written_timestamps = pd.to_datetime(written["timestamp"], unit="ms", utc=True, errors="coerce")
        if parsed_written_timestamps.isna().any():
            _raise_validation_error(
                "invalid_timestamp_values",
                invalid_count=int(parsed_written_timestamps.isna().sum()),
            )

        parsed_datetime = pd.to_datetime(written["datetime"], utc=True, errors="coerce")
        if parsed_datetime.isna().any():
            _raise_validation_error("invalid_datetime_values", invalid_count=int(parsed_datetime.isna().sum()))

        incoming_timestamps = pd.to_numeric(incoming["timestamp"], errors="coerce")
        incoming_timestamps = incoming_timestamps.dropna().astype("int64")
        required_for_batch_rows = ["datetime", *batch_domain_columns]
        if not incoming_timestamps.empty and required_for_batch_rows:
            self._logger.info(
                "parquet-cache-timestamp-compare: symbol=%s timeframe=%s incoming_dtype=%s written_dtype=%s incoming_unique=%s written_unique=%s",
                symbol,
                timeframe.value,
                incoming_timestamps.dtype,
                written_ts_dtype,
                int(incoming_timestamps.nunique()),
                int(written["timestamp"].nunique()) if "timestamp" in written.columns else 0,
            )
            written_batch_rows = written[written["timestamp"].isin(incoming_timestamps)]
            if written_batch_rows.empty:
                _raise_validation_error(
                    "missing_written_batch_rows",
                    incoming_rows=len(incoming),
                    matched_rows=0,
                )
            problematic_fields: dict[str, int] = {}
            for column in required_for_batch_rows:
                null_count = int(written_batch_rows[column].isna().sum())
                if null_count > 0:
                    problematic_fields[column] = null_count
            if problematic_fields:
                _raise_validation_error(
                    "nulls_in_required_batch_columns",
                    problematic_fields=problematic_fields,
                )

            sampled_row = written_batch_rows.sample(n=1, random_state=42).iloc[0]
            sampled_timestamp = pd.to_datetime(sampled_row["timestamp"], unit="ms", utc=True, errors="coerce")
            if pd.isna(sampled_timestamp):
                _raise_validation_error(
                    "sampled_timestamp_unparseable",
                    sampled_row_timestamp=sampled_row.get("timestamp"),
                )

            sampled_null_columns = [
                column
                for column in ["timestamp", "datetime", *batch_domain_columns]
                if pd.isna(sampled_row[column])
            ]
            if sampled_null_columns:
                _raise_validation_error(
                    "sampled_row_has_nulls",
                    sampled_null_columns=sampled_null_columns,
                    sampled_timestamp=sampled_row.get("timestamp"),
                )

    # endregion Приватные

    def load(self, symbol: str, timeframe: Timeframe) -> pd.DataFrame:
        """Загружает данные из parquet-файла."""
        path = self._data_path(symbol, timeframe)
        if not path.exists():
            return pd.DataFrame()
        frame = pd.read_parquet(path)
        if frame.empty:
            return frame
        return self._ensure_utc_columns(frame)

    def get_last_timestamp(self, symbol: str, timeframe: Timeframe) -> pd.Timestamp | None:
        """Возвращает последнюю временную метку в хранилище."""
        data = self.load(symbol, timeframe)
        if data.empty or "timestamp" not in data.columns:
            return None
        return pd.to_datetime(data["timestamp"], unit="ms", utc=True).max()

    def get_last_timestamp_for_column(
        self,
        symbol: str,
        timeframe: Timeframe,
        column_name: str,
    ) -> pd.Timestamp | None:
        """Возвращает последнюю метку времени для указанной колонки."""
        data = self.load(symbol, timeframe)
        if data.empty or "timestamp" not in data.columns:
            return None

        if column_name == "timestamp":
            return pd.to_datetime(data["timestamp"], unit="ms", utc=True).max()

        if column_name not in data.columns:
            return None

        valid_rows = data[column_name].notna()
        if not valid_rows.any():
            return None

        return pd.to_datetime(data.loc[valid_rows, "timestamp"], unit="ms", utc=True).max()

    def save_incremental(self, symbol: str, timeframe: Timeframe, new_data: pd.DataFrame) -> int:
        """Дозаписывает новые данные без перезаписи старых."""
        if new_data.empty:
            return 0

        path = self._data_path(symbol, timeframe)
        path.parent.mkdir(parents=True, exist_ok=True)

        incoming_nunique_before = (
            int(pd.to_numeric(new_data["timestamp"], errors="coerce").dropna().nunique())
            if "timestamp" in new_data.columns
            else 0
        )
        incoming = self._ensure_utc_columns(new_data)
        incoming_nunique_after_ensure = int(incoming["timestamp"].nunique())

        if incoming_nunique_after_ensure < incoming_nunique_before:
            self._logger.warning(
                "parquet-cache-nunique-anomaly: stage=after_ensure_utc symbol=%s timeframe=%s before=%s after=%s",
                symbol,
                timeframe.value,
                incoming_nunique_before,
                incoming_nunique_after_ensure,
            )

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

        merged = self._ensure_utc_columns(merged)
        merged_nunique_before_dedup = int(merged["timestamp"].nunique())
        merged = merged.drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp")
        merged_nunique_after_dedup = int(merged["timestamp"].nunique()) if not merged.empty else 0

        if merged_nunique_after_dedup < merged_nunique_before_dedup:
            self._logger.warning(
                "parquet-cache-nunique-anomaly: stage=before_drop_duplicates symbol=%s timeframe=%s before=%s after=%s",
                symbol,
                timeframe.value,
                merged_nunique_before_dedup,
                merged_nunique_after_dedup,
            )
        merged.to_parquet(path, index=False)
        incoming_rows = len(incoming)
        final_rows = len(merged)
        added_rows = max(final_rows - previous_count, 0)

        try:
            self._validate_written_cache(symbol, timeframe, previous_count, incoming, merged)
        except Exception as exc:
            self._logger.error(
                "parquet-cache-validation: validation=error symbol=%s timeframe=%s path=%s previous_count=%s incoming_rows=%s added_rows=%s final_rows=%s reason=%s",
                symbol,
                timeframe.value,
                path,
                previous_count,
                incoming_rows,
                added_rows,
                final_rows,
                str(exc),
            )
            raise

        self._logger.info(
            "parquet-cache-validation: validation=ok symbol=%s timeframe=%s previous_count=%s incoming_rows=%s added_rows=%s final_rows=%s",
            symbol,
            timeframe.value,
            previous_count,
            incoming_rows,
            added_rows,
            final_rows,
        )

        return added_rows
