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
        parsed = pd.to_datetime(raw, utc=True, errors="coerce")
    else:
        detected_format = _detect_timestamp_unit(raw)
        if detected_format == "datetime-like":
            parsed = pd.to_datetime(raw, utc=True, errors="coerce")
        else:
            numeric = pd.to_numeric(raw, errors="coerce")
            parsed = pd.to_datetime(numeric, unit=detected_format, utc=True, errors="coerce")

    if datetime_fallback is not None:
        fallback_dt = pd.to_datetime(datetime_fallback, utc=True, errors="coerce")
        parsed = parsed.where(parsed.notna(), fallback_dt)

    parsed_notna_mask = parsed.notna()
    parsed_notna = int(parsed_notna_mask.sum())
    timestamp_ms = pd.Series(pd.NA, index=parsed.index, dtype="Int64")
    timestamp_ms.loc[parsed_notna_mask] = (
        parsed.loc[parsed_notna_mask].astype("int64") // 1_000_000
    ).astype("Int64")
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
