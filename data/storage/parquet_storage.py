"""Модуль проекта."""

from __future__ import annotations

from logging import INFO
from pathlib import Path
from urllib.parse import quote, unquote

import pandas as pd

from data.quality.timestamp_normalization import normalize_timestamp_series
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

    def _normalize_raw_utc_columns(self, data: pd.DataFrame) -> pd.DataFrame:
        normalized = data.copy()
        if "timestamp" not in normalized.columns:
            raise ValueError("data must contain 'timestamp' column")

        timestamp_ms, ts = normalize_timestamp_series(
            timestamp_series=normalized["timestamp"],
            datetime_fallback=normalized.get("datetime"),
            logger=self._logger,
            log_prefix="parquet-storage-normalization[raw]",
        )
        normalized = normalized.loc[ts.notna()].copy()
        ts = ts.loc[ts.notna()]

        normalized["timestamp"] = timestamp_ms.loc[ts.index].astype("int64")
        normalized["datetime"] = pd.to_datetime(ts, errors="coerce", utc=True)
        return normalized

    def _normalize_canonical_utc_columns(self, data: pd.DataFrame) -> pd.DataFrame:
        normalized = data.copy()
        if "timestamp" not in normalized.columns:
            raise ValueError("data must contain 'timestamp' column")

        timestamp_ms = pd.to_numeric(normalized["timestamp"], errors="coerce").astype("Int64")
        ts = pd.to_datetime(timestamp_ms, unit="ms", errors="coerce", utc=True)

        valid_mask = timestamp_ms.notna() & ts.notna()
        normalized = normalized.loc[valid_mask].copy()
        ts = ts.loc[valid_mask]
        timestamp_ms = timestamp_ms.loc[valid_mask]

        normalized["timestamp"] = timestamp_ms.astype("int64")
        normalized["datetime"] = ts
        return normalized

    def _ensure_utc_columns(self, data: pd.DataFrame, mode: str) -> pd.DataFrame:
        if mode == "raw":
            return self._normalize_raw_utc_columns(data)
        if mode == "canonical":
            return self._normalize_canonical_utc_columns(data)
        raise ValueError(f"Unsupported normalization mode: {mode}")

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

        parsed_written_timestamps = pd.to_datetime(written["timestamp"], unit="ms", errors="coerce", utc=True)
        if parsed_written_timestamps.isna().any():
            _raise_validation_error(
                "invalid_timestamp_values",
                invalid_count=int(parsed_written_timestamps.isna().sum()),
            )

        parsed_datetime = pd.to_datetime(written["datetime"], errors="coerce", utc=True)
        if parsed_datetime.isna().any():
            _raise_validation_error("invalid_datetime_values", invalid_count=int(parsed_datetime.isna().sum()))

        written_from_datetime_ms = (parsed_datetime.astype("int64") // 1_000_000).astype("Int64")
        written_timestamp_numeric = pd.to_numeric(written["timestamp"], errors="coerce")
        written_timestamp_int = written_timestamp_numeric.round().astype("Int64")
        unit_mismatch_mask = (
            written_timestamp_int.notna()
            & parsed_datetime.notna()
            & (written_timestamp_int != written_from_datetime_ms)
        )
        if unit_mismatch_mask.any():
            mismatch_sample = written.loc[unit_mismatch_mask, ["timestamp", "datetime"]].head(5).to_dict("records")
            _raise_validation_error(
                "timestamp_datetime_unit_mismatch",
                mismatch_count=int(unit_mismatch_mask.sum()),
                mismatch_sample=mismatch_sample,
            )

        incoming_raw_timestamp_numeric = pd.to_numeric(incoming["timestamp"], errors="coerce")
        written_raw_timestamp_numeric = pd.to_numeric(written["timestamp"], errors="coerce")

        incoming_timestamp_ms, _ = normalize_timestamp_series(
            timestamp_series=incoming["timestamp"],
            datetime_fallback=incoming.get("datetime"),
            logger=self._logger,
            log_prefix="parquet-cache-incoming-timestamp-normalization",
        )
        written_timestamp_ms = pd.to_numeric(written["timestamp"], errors="coerce").astype("Int64")

        incoming_timestamps = incoming_timestamp_ms.dropna().astype("int64")
        written_timestamps = written_timestamp_ms.dropna().astype("int64")

        incoming_timestamp_index = pd.Index(incoming_timestamps.unique())
        written_timestamp_index = pd.Index(written_timestamps.unique())
        intersection_timestamps = incoming_timestamp_index.intersection(written_timestamp_index)

        incoming_raw_non_na = incoming_raw_timestamp_numeric.dropna()
        written_raw_non_na = written_raw_timestamp_numeric.dropna()
        incoming_raw_min = int(incoming_raw_non_na.min()) if not incoming_raw_non_na.empty else None
        incoming_raw_max = int(incoming_raw_non_na.max()) if not incoming_raw_non_na.empty else None
        incoming_min = int(incoming_timestamp_index.min()) if not incoming_timestamp_index.empty else None
        incoming_max = int(incoming_timestamp_index.max()) if not incoming_timestamp_index.empty else None
        written_raw_min = int(written_raw_non_na.min()) if not written_raw_non_na.empty else None
        written_raw_max = int(written_raw_non_na.max()) if not written_raw_non_na.empty else None
        written_min = int(written_timestamp_index.min()) if not written_timestamp_index.empty else None
        written_max = int(written_timestamp_index.max()) if not written_timestamp_index.empty else None
        intersection_count = int(len(intersection_timestamps))

        required_for_batch_rows = ["datetime", *batch_domain_columns]
        if not incoming_timestamps.empty and required_for_batch_rows:
            self._logger.info(
                "parquet-cache-timestamp-compare: symbol=%s timeframe=%s incoming_dtype=%s written_dtype=%s incoming_unique=%s written_unique=%s incoming_raw_min=%s incoming_raw_max=%s incoming_min=%s incoming_max=%s written_raw_min=%s written_raw_max=%s written_min=%s written_max=%s intersection_count=%s",
                symbol,
                timeframe.value,
                incoming_timestamps.dtype,
                written_ts_dtype,
                int(incoming_timestamps.nunique()),
                int(written["timestamp"].nunique()) if "timestamp" in written.columns else 0,
                incoming_raw_min,
                incoming_raw_max,
                incoming_min,
                incoming_max,
                written_raw_min,
                written_raw_max,
                written_min,
                written_max,
                intersection_count,
            )

            missing_timestamps = incoming_timestamp_index.difference(written_timestamp_index)
            if not missing_timestamps.empty:
                mismatch_sample_limit = 10
                missing_values_sample = [int(value) for value in missing_timestamps[:mismatch_sample_limit]]
                reason = "missing_written_batch_rows"
                if len(written) == len(merged) and previous_count == 0:
                    reason = "timestamp_canonicalization_mismatch"
                _raise_validation_error(
                    reason,
                    incoming_rows=len(incoming),
                    incoming_unique=int(len(incoming_timestamp_index)),
                    written_unique=int(len(written_timestamp_index)),
                    intersection_count=intersection_count,
                    missing_values_sample=missing_values_sample,
                )

            written_batch_mask = written_timestamp_ms.isin(intersection_timestamps).fillna(False)
            written_batch_rows = written.loc[written_batch_mask]
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
            sampled_timestamp = pd.to_datetime(sampled_row["timestamp"], unit="ms", errors="coerce", utc=True)
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
        return self._ensure_utc_columns(frame, mode="canonical")

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
        incoming = self._ensure_utc_columns(new_data, mode="raw")
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

        merged = self._ensure_utc_columns(merged, mode="canonical")
        merged_rechecked = self._ensure_utc_columns(merged, mode="canonical")
        if not merged["timestamp"].reset_index(drop=True).equals(merged_rechecked["timestamp"].reset_index(drop=True)):
            merged_ts = merged["timestamp"].reset_index(drop=True)
            merged_rechecked_ts = merged_rechecked["timestamp"].reset_index(drop=True)
            mismatch_mask = merged_ts.ne(merged_rechecked_ts) & ~(merged_ts.isna() & merged_rechecked_ts.isna())
            mismatch_sample = pd.DataFrame(
                {"merged": merged_ts, "merged_rechecked": merged_rechecked_ts}
            ).loc[mismatch_mask].head(10).to_dict("records")
            merged_dt = merged.get("datetime")
            merged_rechecked_dt = merged_rechecked.get("datetime")
            raise ParquetCacheValidationError(
                "parquet cache validation failed: reason=ensure_utc_non_idempotent, "
                f"symbol={symbol}, timeframe={timeframe.value}, path={path}, "
                f"normalizer_mode=canonical, "
                f"timestamp_dtype_before={merged_ts.dtype}, timestamp_dtype_after={merged_rechecked_ts.dtype}, "
                f"datetime_dtype_before={getattr(merged_dt, 'dtype', '<missing>')}, "
                f"datetime_dtype_after={getattr(merged_rechecked_dt, 'dtype', '<missing>')}, "
                f"timestamp_sample_before={merged_ts.head(5).tolist()}, timestamp_sample_after={merged_rechecked_ts.head(5).tolist()}, "
                f"datetime_sample_before={merged_dt.head(5).tolist() if merged_dt is not None else '<missing>'}, "
                f"datetime_sample_after={merged_rechecked_dt.head(5).tolist() if merged_rechecked_dt is not None else '<missing>'}, "
                f"merged_min={merged_ts.min()}, merged_max={merged_ts.max()}, "
                f"merged_rechecked_min={merged_rechecked_ts.min()}, merged_rechecked_max={merged_rechecked_ts.max()}, "
                f"mismatch_sample={mismatch_sample}"
            )
        merged = merged_rechecked
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


    def migrate_cache_file(self, symbol: str, timeframe: Timeframe) -> int:
        """Мигрирует существующий parquet-файл в канонический UTC ms-формат."""
        path = self._data_path(symbol, timeframe)
        if not path.exists():
            return 0

        raw = pd.read_parquet(path)
        if raw.empty:
            raw.to_parquet(path, index=False)
            return 0

        normalized_raw = self._ensure_utc_columns(raw, mode="raw")
        canonical = self._ensure_utc_columns(normalized_raw, mode="canonical")
        canonical = canonical.drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp")

        canonical_rechecked = self._ensure_utc_columns(canonical, mode="canonical")
        if not canonical["timestamp"].reset_index(drop=True).equals(canonical_rechecked["timestamp"].reset_index(drop=True)):
            raise ParquetCacheValidationError(
                "parquet cache validation failed: reason=migration_non_idempotent, "
                f"symbol={symbol}, timeframe={timeframe.value}, path={path}, normalizer_mode=canonical"
            )

        canonical_rechecked.to_parquet(path, index=False)
        self._validate_written_cache(
            symbol=symbol,
            timeframe=timeframe,
            previous_count=0,
            incoming=normalized_raw,
            merged=canonical_rechecked,
        )
        return len(canonical_rechecked)

