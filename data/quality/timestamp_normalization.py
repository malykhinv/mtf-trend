"""Модуль нормализации временных меток."""

from __future__ import annotations

from logging import Logger

import pandas as pd
from pandas.api.types import is_datetime64_any_dtype


def _detect_timestamp_unit(raw: pd.Series) -> str:
    numeric = pd.to_numeric(raw, errors="coerce").dropna()
    if numeric.empty:
        return "datetime-like"

    abs_max = float(numeric.abs().max())
    if abs_max >= 1e17:
        return "ns"
    if abs_max >= 1e14:
        return "us"
    if abs_max >= 1e11:
        return "ms"
    return "s"


def _safe_min_max(raw: pd.Series) -> tuple[object, object]:
    non_na = raw.dropna()
    if non_na.empty:
        return None, None
    try:
        return non_na.min(), non_na.max()
    except TypeError:
        as_string = non_na.astype(str)
        return as_string.min(), as_string.max()


def _normalize_numeric_timestamp_to_ms(raw: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(raw, errors="coerce")
    if numeric.dropna().empty:
        return pd.Series(pd.NA, index=raw.index, dtype="Int64")

    detected_format = _detect_timestamp_unit(raw)
    if detected_format == "ns":
        normalized = numeric // 1_000_000
    elif detected_format == "us":
        normalized = numeric // 1_000
    elif detected_format == "ms":
        normalized = numeric
    elif detected_format == "s":
        normalized = numeric * 1_000
    else:
        normalized = pd.Series(pd.NA, index=raw.index, dtype="float64")

    return normalized.round().astype("Int64")


def normalize_timestamp_series(
    timestamp_series: pd.Series,
    datetime_fallback: pd.Series | None = None,
    logger: Logger | None = None,
    log_prefix: str = "timestamp-normalization",
) -> tuple[pd.Series, pd.Series]:
    """Нормализует временные метки к UTC datetime и int64 milliseconds."""
    raw = timestamp_series.copy()
    raw_dtype = str(raw.dtype)
    raw_min, raw_max = _safe_min_max(raw)
    nunique_before = int(raw.dropna().nunique())

    if is_datetime64_any_dtype(raw):
        detected_format = "datetime-like"
        parsed = pd.to_datetime(raw, errors="coerce")
        timestamp_ms = pd.Series(pd.NA, index=parsed.index, dtype="Int64")
        parsed_notna_mask = parsed.notna()
        timestamp_ms.loc[parsed_notna_mask] = (
            parsed.loc[parsed_notna_mask].astype("int64") // 1_000_000
        ).astype("Int64")
    else:
        detected_format = _detect_timestamp_unit(raw)
        timestamp_ms = _normalize_numeric_timestamp_to_ms(raw)
        parsed = pd.to_datetime(timestamp_ms, unit="ms", errors="coerce")

    if datetime_fallback is not None:
        fallback_dt = pd.to_datetime(datetime_fallback, errors="coerce")
        parsed = parsed.where(parsed.notna(), fallback_dt)
        parsed_notna_mask = parsed.notna()
        timestamp_ms.loc[parsed_notna_mask] = (
            parsed.loc[parsed_notna_mask].astype("int64") // 1_000_000
        ).astype("Int64")

    parsed_notna_mask = parsed.notna()
    parsed_notna = int(parsed_notna_mask.sum())
    nunique_after = int(timestamp_ms.dropna().nunique())

    if logger is not None:
        logger.info(
            "%s: raw_dtype=%s raw_min=%s raw_max=%s detected_format=%s parsed_notna=%s nunique_before=%s nunique_after=%s",
            log_prefix,
            raw_dtype,
            raw_min,
            raw_max,
            detected_format,
            parsed_notna,
            nunique_before,
            nunique_after,
        )

    return timestamp_ms, parsed
