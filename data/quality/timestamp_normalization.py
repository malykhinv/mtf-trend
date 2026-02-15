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


def _normalize_numeric_timestamp_to_ms(raw: pd.Series, detected_format: str | None = None) -> pd.Series:
    numeric = pd.to_numeric(raw, errors="coerce")
    if numeric.dropna().empty:
        return pd.Series(pd.NA, index=raw.index, dtype="Int64")

    detected_format = detected_format or _detect_timestamp_unit(raw)
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


def _select_best_unit_by_datetime_fallback(
    raw: pd.Series,
    datetime_fallback: pd.Series,
    default_unit: str,
    logger: Logger | None = None,
    log_prefix: str = "timestamp-normalization",
) -> str:
    fallback_dt = pd.to_datetime(datetime_fallback, errors="coerce", utc=True)
    fallback_mask = fallback_dt.notna()
    if not fallback_mask.any():
        return default_unit

    numeric = pd.to_numeric(raw, errors="coerce")
    if numeric.loc[fallback_mask].dropna().empty:
        return default_unit

    unit_to_divider = {
        "s": 1,
        "ms": 1_000,
        "us": 1_000_000,
        "ns": 1_000_000_000,
    }
    if default_unit not in unit_to_divider:
        return default_unit

    unit_scores: dict[str, float] = {}
    unit_diagnostics: dict[str, dict[str, float]] = {}
    fallback_ns = fallback_dt.astype("int64")
    modern_horizon_start = pd.Timestamp("2000-01-01", tz="UTC")
    modern_horizon_end = pd.Timestamp("2100-01-01", tz="UTC")

    for unit in unit_to_divider:
        parsed = pd.to_datetime(numeric, unit=unit, errors="coerce", utc=True)
        valid_mask = parsed.notna() & fallback_mask
        if not valid_mask.any():
            continue

        parsed_ns = parsed.astype("int64")
        score = (parsed_ns.loc[valid_mask] - fallback_ns.loc[valid_mask]).abs().median()
        if pd.isna(score):
            continue
        valid_parsed = parsed.loc[valid_mask]
        in_modern_horizon = valid_parsed.between(modern_horizon_start, modern_horizon_end, inclusive="both")
        modern_ratio = float(in_modern_horizon.mean())
        q95_score = float((parsed_ns.loc[valid_mask] - fallback_ns.loc[valid_mask]).abs().quantile(0.95))
        unit_diagnostics[unit] = {
            "median_abs_delta_ns": float(score),
            "q95_abs_delta_ns": q95_score,
            "modern_ratio": modern_ratio,
            "matched_points": float(valid_mask.sum()),
        }
        if modern_ratio < 0.8:
            continue
        unit_scores[unit] = float(score)

    if not unit_scores:
        fallback_modern_mask = fallback_dt.loc[fallback_mask].between(
            modern_horizon_start,
            modern_horizon_end,
            inclusive="both",
        )
        fallback_modern_ratio = float(fallback_modern_mask.mean()) if not fallback_modern_mask.empty else 0.0
        if fallback_modern_ratio >= 0.8:
            raw_min, raw_max = _safe_min_max(raw)
            message = (
                f"{log_prefix}: datetime fallback present but none of units {tuple(unit_to_divider)} are consistent; "
                f"raw_min={raw_min}, raw_max={raw_max}, default_unit={default_unit}, "
                f"fallback_modern_ratio={fallback_modern_ratio:.3f}, diagnostics={unit_diagnostics}"
            )
            if logger is not None:
                logger.error(message)
            raise ValueError(message)
        return default_unit

    sorted_scores = sorted(unit_scores.items(), key=lambda item: item[1])
    best_unit, best_score = sorted_scores[0]
    second_score = sorted_scores[1][1] if len(sorted_scores) > 1 else None

    is_ambiguous = second_score is not None and (
        (best_score == 0 and second_score == 0)
        or (best_score > 0 and (second_score / best_score) < 1.2)
    )
    if is_ambiguous:
        message = (
            f"{log_prefix}: ambiguous timestamp unit selection by datetime fallback; "
            f"scores={unit_scores}, default_unit={default_unit}"
        )
        if logger is not None:
            logger.warning(message)
        raise ValueError(message)

    return best_unit


def _check_parsed_vs_fallback_consistency(
    raw: pd.Series,
    parsed: pd.Series,
    datetime_fallback: pd.Series,
    detected_format: str,
    logger: Logger | None = None,
    log_prefix: str = "timestamp-normalization",
) -> None:
    fallback_dt = pd.to_datetime(datetime_fallback, errors="coerce", utc=True)
    mask = parsed.notna() & fallback_dt.notna()
    if not mask.any():
        return

    parsed_ns = parsed.loc[mask].astype("int64")
    fallback_ns = fallback_dt.loc[mask].astype("int64")
    abs_delta_ns = (parsed_ns - fallback_ns).abs()

    median_abs_delta_ns = float(abs_delta_ns.median())
    q95_abs_delta_ns = float(abs_delta_ns.quantile(0.95))
    one_year_ns = float(pd.Timedelta(days=365).value)

    modern_horizon_start = pd.Timestamp("2000-01-01", tz="UTC")
    modern_horizon_end = pd.Timestamp("2100-01-01", tz="UTC")
    fallback_modern_ratio = float(
        fallback_dt.loc[mask].between(modern_horizon_start, modern_horizon_end, inclusive="both").mean()
    )
    parsed_modern_ratio = float(
        parsed.loc[mask].between(modern_horizon_start, modern_horizon_end, inclusive="both").mean()
    )

    if fallback_modern_ratio >= 0.8 and (parsed_modern_ratio < 0.2 or median_abs_delta_ns > one_year_ns):
        raw_min, raw_max = _safe_min_max(raw)
        message = (
            f"{log_prefix}: timestamp normalization rejected by datetime fallback consistency; "
            f"raw_min={raw_min}, raw_max={raw_max}, detected_format={detected_format}, "
            f"matched_points={int(mask.sum())}, median_abs_delta_ns={median_abs_delta_ns:.0f}, "
            f"q95_abs_delta_ns={q95_abs_delta_ns:.0f}, fallback_modern_ratio={fallback_modern_ratio:.3f}, "
            f"parsed_modern_ratio={parsed_modern_ratio:.3f}"
        )
        if logger is not None:
            logger.error(message)
        raise ValueError(message)


def normalize_timestamp_series(
    timestamp_series: pd.Series,
    datetime_fallback: pd.Series | None = None,
    logger: Logger | None = None,
    log_prefix: str = "timestamp-normalization",
) -> tuple[pd.Series, pd.Series]:
    """Нормализует временные метки к UTC.

    Возвращает:
    * parsed: timezone-aware серия `datetime64[ns, UTC]`.
    * timestamp_ms: unix timestamp в миллисекундах (UTC) как `Int64`.
    """
    raw = timestamp_series.copy()
    raw_dtype = str(raw.dtype)
    raw_min, raw_max = _safe_min_max(raw)
    nunique_before = int(raw.dropna().nunique())

    if is_datetime64_any_dtype(raw):
        detected_format = "datetime-like"
        parsed = pd.to_datetime(raw, errors="coerce", utc=True)
    else:
        detected_format = _detect_timestamp_unit(raw)
        if datetime_fallback is not None:
            detected_format = _select_best_unit_by_datetime_fallback(
                raw=raw,
                datetime_fallback=datetime_fallback,
                default_unit=detected_format,
                logger=logger,
                log_prefix=log_prefix,
            )
        timestamp_ms = _normalize_numeric_timestamp_to_ms(raw, detected_format=detected_format)
        parsed = pd.to_datetime(timestamp_ms, unit="ms", errors="coerce", utc=True)

    if datetime_fallback is not None:
        fallback_dt = pd.to_datetime(datetime_fallback, errors="coerce", utc=True)
        _check_parsed_vs_fallback_consistency(
            raw=raw,
            parsed=parsed,
            datetime_fallback=fallback_dt,
            detected_format=detected_format,
            logger=logger,
            log_prefix=log_prefix,
        )
        parsed = parsed.where(parsed.notna(), fallback_dt)

    timestamp_ms = pd.Series(pd.NA, index=parsed.index, dtype="Int64")
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
