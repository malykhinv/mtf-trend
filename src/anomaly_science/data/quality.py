from __future__ import annotations

from dataclasses import asdict

import pandas as pd

from anomaly_science.contracts.audit import AuditStatus, DataQualityRow
from anomaly_science.contracts.market import FIVE_MINUTES_MS, ONE_MINUTE_MS
from anomaly_science.data.source import DATASET_SPECS


CRITICAL = "critical"
WARNING = "warning"
INFO = "info"


def rows_to_artifact(rows: list[DataQualityRow]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        payload = asdict(row)
        payload["status"] = row.status.value
        result.append(payload)
    return result


def run_data_quality(frames: dict[str, pd.DataFrame | None], *, read_errors: list[str] | None = None) -> list[DataQualityRow]:
    rows: list[DataQualityRow] = []
    for message in read_errors or []:
        rows.append(_row("source_schema", AuditStatus.FAIL, CRITICAL, 1, message))

    rows.extend(_dataset_presence_checks(frames))
    rows.extend(_candle_checks(frames.get("candles_1m"), dataset_name="candles_1m", timeframe_ms=ONE_MINUTE_MS))
    rows.extend(_candle_checks(frames.get("candles_5m"), dataset_name="candles_5m", timeframe_ms=FIVE_MINUTES_MS))
    rows.extend(_open_interest_checks(frames.get("open_interest_5m")))
    rows.extend(_liquidation_checks(frames.get("liquidations")))
    return rows


def has_critical_fail(rows: list[DataQualityRow]) -> bool:
    return any(row.status == AuditStatus.FAIL and row.severity == CRITICAL for row in rows)


def _row(check_name: str, status: AuditStatus, severity: str, affected_rows: int, message: str) -> DataQualityRow:
    return DataQualityRow(
        check_name=check_name,
        status=status,
        severity=severity,
        affected_rows=affected_rows,
        message=message,
    )


def _dataset_presence_checks(frames: dict[str, pd.DataFrame | None]) -> list[DataQualityRow]:
    rows: list[DataQualityRow] = []
    for dataset_name in ("candles_1m", "candles_5m"):
        if frames.get(dataset_name) is None:
            rows.append(_row(f"{dataset_name}_present", AuditStatus.FAIL, CRITICAL, 1, f"required dataset {dataset_name} is missing"))
        else:
            rows.append(_row(f"{dataset_name}_present", AuditStatus.PASS, INFO, 0, f"required dataset {dataset_name} is present"))
    for dataset_name in ("open_interest_5m", "liquidations"):
        if frames.get(dataset_name) is None:
            rows.append(_row(f"{dataset_name}_present", AuditStatus.WARN, WARNING, 1, f"optional MVP1 dataset {dataset_name} is missing"))
        else:
            rows.append(_row(f"{dataset_name}_present", AuditStatus.PASS, INFO, 0, f"optional MVP1 dataset {dataset_name} is present"))
    return rows


def _required_column_checks(frame: pd.DataFrame, *, dataset_name: str) -> list[DataQualityRow]:
    spec = DATASET_SPECS[dataset_name]
    missing = [name for name in spec.required_columns if name not in frame.columns]
    if missing:
        return [_row(f"{dataset_name}_required_columns", AuditStatus.FAIL, CRITICAL, len(missing), f"missing required columns: {missing}")]
    return [_row(f"{dataset_name}_required_columns", AuditStatus.PASS, INFO, 0, "required columns present")]


def _candle_checks(frame: pd.DataFrame | None, *, dataset_name: str, timeframe_ms: int) -> list[DataQualityRow]:
    if frame is None:
        return []
    rows = _required_column_checks(frame, dataset_name=dataset_name)
    if frame.empty:
        rows.append(_row(f"{dataset_name}_non_empty", AuditStatus.FAIL, CRITICAL, 0, "dataset is empty"))
        return rows
    rows.append(_row(f"{dataset_name}_non_empty", AuditStatus.PASS, INFO, 0, "dataset is non-empty"))

    if not {"symbol", "open_time_ms"}.issubset(frame.columns):
        return rows

    duplicate_count = int(frame.duplicated(subset=["symbol", "open_time_ms"]).sum())
    rows.append(_row(
        f"{dataset_name}_duplicate_symbol_time",
        AuditStatus.FAIL if duplicate_count else AuditStatus.PASS,
        CRITICAL if duplicate_count else INFO,
        duplicate_count,
        "duplicate symbol/open_time_ms rows" if duplicate_count else "no duplicate symbol/open_time_ms rows",
    ))

    if {"open", "high", "low", "close"}.issubset(frame.columns):
        non_positive = int((frame[["open", "high", "low", "close"]] <= 0).any(axis=1).sum())
        invalid_ohlc = int(((frame["high"] < frame[["open", "close"]].max(axis=1)) | (frame["low"] > frame[["open", "close"]].min(axis=1)) | (frame["low"] > frame["high"])).sum())
        rows.append(_row(
            f"{dataset_name}_positive_prices",
            AuditStatus.FAIL if non_positive else AuditStatus.PASS,
            CRITICAL if non_positive else INFO,
            non_positive,
            "non-positive OHLC rows" if non_positive else "all OHLC prices are positive",
        ))
        rows.append(_row(
            f"{dataset_name}_valid_ohlc",
            AuditStatus.FAIL if invalid_ohlc else AuditStatus.PASS,
            CRITICAL if invalid_ohlc else INFO,
            invalid_ohlc,
            "invalid OHLC ordering rows" if invalid_ohlc else "OHLC ordering is valid",
        ))
        rows.extend(_return_outlier_check(frame, dataset_name=dataset_name))

    for column in ("volume", "quote_volume"):
        if column in frame.columns:
            negative = int((frame[column] < 0).sum())
            zero = int((frame[column] == 0).sum())
            rows.append(_row(
                f"{dataset_name}_{column}_non_negative",
                AuditStatus.FAIL if negative else AuditStatus.PASS,
                CRITICAL if negative else INFO,
                negative,
                f"negative {column} rows" if negative else f"{column} is non-negative",
            ))
            rows.append(_row(
                f"{dataset_name}_{column}_zero_rows",
                AuditStatus.WARN if zero else AuditStatus.PASS,
                WARNING if zero else INFO,
                zero,
                f"zero {column} rows" if zero else f"no zero {column} rows",
            ))

    if {"taker_buy_quote_volume", "quote_volume"}.issubset(frame.columns):
        above_quote = int((frame["taker_buy_quote_volume"] > frame["quote_volume"]).sum())
        rows.append(_row(
            f"{dataset_name}_taker_buy_quote_lte_quote_volume",
            AuditStatus.FAIL if above_quote else AuditStatus.PASS,
            CRITICAL if above_quote else INFO,
            above_quote,
            "taker_buy_quote_volume exceeds quote_volume" if above_quote else "taker_buy_quote_volume <= quote_volume",
        ))

    rows.extend(_monotonic_and_gap_checks(frame, dataset_name=dataset_name, time_column="open_time_ms", expected_step_ms=timeframe_ms))
    return rows


def _return_outlier_check(frame: pd.DataFrame, *, dataset_name: str) -> list[DataQualityRow]:
    if not {"symbol", "open_time_ms", "close"}.issubset(frame.columns):
        return []
    sorted_frame = frame.sort_values(["symbol", "open_time_ms"])
    pct = sorted_frame.groupby("symbol")["close"].pct_change().abs()
    outliers = int((pct > 0.80).sum())
    return [_row(
        f"{dataset_name}_impossible_close_return",
        AuditStatus.FAIL if outliers else AuditStatus.PASS,
        CRITICAL if outliers else INFO,
        outliers,
        "absolute close-to-close return above 80%" if outliers else "no impossible close-to-close returns above 80%",
    )]


def _monotonic_and_gap_checks(frame: pd.DataFrame, *, dataset_name: str, time_column: str, expected_step_ms: int) -> list[DataQualityRow]:
    rows: list[DataQualityRow] = []
    if not {"symbol", time_column}.issubset(frame.columns):
        return rows

    non_monotonic_symbols = 0
    gap_count = 0
    for _, group in frame.groupby("symbol", sort=False):
        times = group[time_column].astype("int64")
        if not times.is_monotonic_increasing:
            non_monotonic_symbols += 1
        sorted_times = times.sort_values()
        diffs = sorted_times.diff().dropna()
        gap_count += int((diffs != expected_step_ms).sum())

    rows.append(_row(
        f"{dataset_name}_monotonic_by_symbol",
        AuditStatus.FAIL if non_monotonic_symbols else AuditStatus.PASS,
        CRITICAL if non_monotonic_symbols else INFO,
        non_monotonic_symbols,
        "symbols with non-monotonic timestamps" if non_monotonic_symbols else "timestamps are monotonic by symbol",
    ))
    rows.append(_row(
        f"{dataset_name}_expected_interval_gaps",
        AuditStatus.WARN if gap_count else AuditStatus.PASS,
        WARNING if gap_count else INFO,
        gap_count,
        f"timestamp gaps or unexpected intervals vs {expected_step_ms}ms" if gap_count else "no unexpected timestamp intervals",
    ))
    return rows


def _open_interest_checks(frame: pd.DataFrame | None) -> list[DataQualityRow]:
    if frame is None:
        return []
    rows = _required_column_checks(frame, dataset_name="open_interest_5m")
    if frame.empty:
        rows.append(_row("open_interest_5m_non_empty", AuditStatus.WARN, WARNING, 0, "open_interest_5m dataset is empty"))
        return rows
    rows.append(_row("open_interest_5m_non_empty", AuditStatus.PASS, INFO, 0, "open_interest_5m dataset is non-empty"))
    duplicate_count = int(frame.duplicated(subset=["symbol", "timestamp_ms"]).sum()) if {"symbol", "timestamp_ms"}.issubset(frame.columns) else 0
    rows.append(_row(
        "open_interest_5m_duplicate_symbol_time",
        AuditStatus.FAIL if duplicate_count else AuditStatus.PASS,
        CRITICAL if duplicate_count else INFO,
        duplicate_count,
        "duplicate symbol/timestamp_ms OI rows" if duplicate_count else "no duplicate OI rows",
    ))
    if "open_interest" in frame.columns:
        negative = int((frame["open_interest"] < 0).sum())
        rows.append(_row(
            "open_interest_5m_non_negative",
            AuditStatus.FAIL if negative else AuditStatus.PASS,
            CRITICAL if negative else INFO,
            negative,
            "negative OI rows" if negative else "OI is non-negative",
        ))
    if {"available_time_ms", "timestamp_ms"}.issubset(frame.columns):
        not_closed = int((frame["available_time_ms"] < frame["timestamp_ms"] + FIVE_MINUTES_MS).sum())
        rows.append(_row(
            "open_interest_5m_closed_bucket_asof",
            AuditStatus.FAIL if not_closed else AuditStatus.PASS,
            CRITICAL if not_closed else INFO,
            not_closed,
            "OI available before closed 5m bucket" if not_closed else "OI availability respects closed 5m buckets",
        ))
    return rows


def _liquidation_checks(frame: pd.DataFrame | None) -> list[DataQualityRow]:
    if frame is None:
        return []
    rows = _required_column_checks(frame, dataset_name="liquidations")
    if frame.empty:
        rows.append(_row("liquidations_non_empty", AuditStatus.WARN, WARNING, 0, "liquidations dataset is empty"))
        return rows
    rows.append(_row("liquidations_non_empty", AuditStatus.PASS, INFO, 0, "liquidations dataset is non-empty"))
    if "side" in frame.columns:
        bad_side = int((~frame["side"].isin(["long", "short"])).sum())
        rows.append(_row(
            "liquidations_valid_side",
            AuditStatus.FAIL if bad_side else AuditStatus.PASS,
            CRITICAL if bad_side else INFO,
            bad_side,
            "liquidation side outside {'long', 'short'}" if bad_side else "liquidation sides are valid",
        ))
    if {"event_time_ms", "available_time_ms"}.issubset(frame.columns):
        future_leak = int((frame["available_time_ms"] < frame["event_time_ms"]).sum())
        rows.append(_row(
            "liquidations_available_asof_event",
            AuditStatus.FAIL if future_leak else AuditStatus.PASS,
            CRITICAL if future_leak else INFO,
            future_leak,
            "liquidation available_time_ms before event_time_ms" if future_leak else "liquidation availability is as-of event time or later",
        ))
    for column in ("price", "quantity", "quote_quantity"):
        if column in frame.columns:
            negative_or_zero = int((frame[column] <= 0).sum()) if column == "price" else int((frame[column] < 0).sum())
            rows.append(_row(
                f"liquidations_{column}_valid",
                AuditStatus.FAIL if negative_or_zero else AuditStatus.PASS,
                CRITICAL if negative_or_zero else INFO,
                negative_or_zero,
                f"invalid liquidation {column} rows" if negative_or_zero else f"liquidation {column} values are valid",
            ))
    return rows
