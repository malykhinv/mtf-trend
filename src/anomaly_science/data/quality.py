from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from anomaly_science.contracts.audit import AuditStatus, DataQualityRow
from anomaly_science.contracts.market import FIVE_MINUTES_MS, ONE_MINUTE_MS
from anomaly_science.contracts.methodology import MAX_FEATURE_LOOKBACK_MINUTES
from anomaly_science.data.source import DATASET_SPECS


CRITICAL = "critical"
WARNING = "warning"
INFO = "info"
WARMUP_WINDOW_MINUTES = MAX_FEATURE_LOOKBACK_MINUTES

MASK_ENFORCED_CANDLES_1M_CHECKS: frozenset[str] = frozenset({
    "candles_1m_duplicate_symbol_time",
    "candles_1m_positive_prices",
    "candles_1m_valid_ohlc",
    "candles_1m_impossible_close_return",
    "candles_1m_volume_non_negative",
    "candles_1m_quote_volume_non_negative",
    "candles_1m_taker_buy_quote_lte_quote_volume",
})


@dataclass(frozen=True, slots=True)
class DataQualityMask:
    """Explicit row-level detector exclusion mask derived from data-quality rules."""

    excluded_index: frozenset[int]
    excluded_rows: int
    reason_counts: dict[str, int]

    @property
    def has_exclusions(self) -> bool:
        return self.excluded_rows > 0


def rows_to_artifact(rows: list[DataQualityRow]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        payload = asdict(row)
        payload["status"] = row.status.value
        result.append({key: _csv_value(value) for key, value in payload.items()})
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



def build_candles_1m_data_quality_mask(frame: pd.DataFrame | None) -> DataQualityMask:
    """Build the shared row-level pre-trigger data-quality mask for 1m candles.

    This mask is the concrete enforcement layer for detector candidates. It removes
    rows with row-local bad OHLC/volume payloads, duplicate symbol/time keys,
    impossible close-to-close jumps, first candles after raw timestamp gaps, and the
    rolling warm-up context after such gaps. Dataset-level schema/presence failures
    remain blocking failures and are handled separately by
    ``has_detector_blocking_quality_fail``.
    """
    if frame is None or frame.empty:
        return DataQualityMask(excluded_index=frozenset(), excluded_rows=0, reason_counts={})
    if not {"symbol", "open_time_ms"}.issubset(frame.columns):
        return DataQualityMask(excluded_index=frozenset(), excluded_rows=0, reason_counts={})

    reason_by_index: dict[int, set[str]] = {}

    def mark(mask: pd.Series, reason: str) -> None:
        for index in frame.index[mask.fillna(False)]:
            reason_by_index.setdefault(int(index), set()).add(reason)

    duplicate_mask = frame.duplicated(subset=["symbol", "open_time_ms"], keep=False)
    mark(duplicate_mask, "duplicate_symbol_time")

    if {"open", "high", "low", "close"}.issubset(frame.columns):
        mark((frame[["open", "high", "low", "close"]] <= 0).any(axis=1), "non_positive_ohlc")
        mark(
            (frame["high"] < frame[["open", "close"]].max(axis=1))
            | (frame["low"] > frame[["open", "close"]].min(axis=1))
            | (frame["low"] > frame["high"]),
            "invalid_ohlc",
        )
        sorted_frame = frame.sort_values(["symbol", "open_time_ms"])
        pct = sorted_frame.groupby("symbol")["close"].pct_change().abs()
        mark(pct.reindex(frame.index) > 0.80, "impossible_close_return")

    for column in ("volume", "quote_volume"):
        if column in frame.columns:
            mark(frame[column] < 0, f"negative_{column}")

    if {"taker_buy_quote_volume", "quote_volume"}.issubset(frame.columns):
        mark(frame["taker_buy_quote_volume"] > frame["quote_volume"], "taker_buy_quote_gt_quote_volume")

    gap_masked = _warmup_filtered_index(frame, time_column="open_time_ms", warmup_minutes=WARMUP_WINDOW_MINUTES)
    for index in gap_masked:
        reason_by_index.setdefault(int(index), set()).add("technical_noise_or_warmup_after_gap")

    reason_counts: dict[str, int] = {}
    for reasons in reason_by_index.values():
        for reason in reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return DataQualityMask(
        excluded_index=frozenset(reason_by_index),
        excluded_rows=len(reason_by_index),
        reason_counts=dict(sorted(reason_counts.items())),
    )


def apply_data_quality_mask(frame: pd.DataFrame, mask: DataQualityMask) -> pd.DataFrame:
    if not mask.excluded_index:
        return frame.copy()
    keep = ~frame.index.map(lambda index: int(index) in mask.excluded_index)
    return frame.loc[keep].copy()


def has_detector_blocking_quality_fail(rows: list[DataQualityRow]) -> bool:
    """Return true for data-quality failures that a row mask cannot safely repair."""
    for row in rows:
        if row.status != AuditStatus.FAIL or row.severity != CRITICAL:
            continue
        if row.check_name in MASK_ENFORCED_CANDLES_1M_CHECKS:
            continue
        return True
    return False


def data_quality_mask_audit_row(mask: DataQualityMask) -> DataQualityRow:
    reason_summary = ";".join(f"{reason}={count}" for reason, count in mask.reason_counts.items())
    return _row(
        "candles_1m_pre_trigger_quality_mask",
        AuditStatus.WARN if mask.has_exclusions else AuditStatus.PASS,
        WARNING if mask.has_exclusions else INFO,
        mask.excluded_rows,
        (
            f"pre-trigger data-quality mask excluded {mask.excluded_rows} candles before strategy.generate_triggers; {reason_summary}"
            if mask.has_exclusions
            else "pre-trigger data-quality mask had no candle rows to exclude"
        ),
        excluded_from_detector=mask.has_exclusions,
        excluded_from_ml_dataset=mask.has_exclusions,
        reason="pre_trigger_quality_mask" if mask.has_exclusions else "",
    )


def filter_warmup_window_rows(
    frame: pd.DataFrame,
    *,
    time_column: str = "open_time_ms",
    warmup_minutes: int = WARMUP_WINDOW_MINUTES,
) -> pd.DataFrame:
    """Remove rows inside symbol-specific warm-up windows after raw data gaps.

    The detector and ML builders must not consume rolling-window context immediately
    after an exchange/API gap. The first candle after the gap and all following rows
    until ``gap_start + warmup_minutes`` are filtered before trigger generation.
    """
    if frame.empty or not {"symbol", time_column}.issubset(frame.columns):
        return frame.copy()
    if type(warmup_minutes) is not int or warmup_minutes <= 0:
        raise ValueError("warmup_minutes must be a positive int")

    excluded = _warmup_filtered_index(frame, time_column=time_column, warmup_minutes=warmup_minutes)
    if not excluded:
        return frame.copy()
    keep = ~frame.index.map(lambda index: int(index) in excluded)
    return frame.loc[keep].copy()


def _warmup_filtered_index(
    frame: pd.DataFrame,
    *,
    time_column: str,
    warmup_minutes: int,
) -> frozenset[int]:
    if frame.empty or not {"symbol", time_column}.issubset(frame.columns):
        return frozenset()
    if type(warmup_minutes) is not int or warmup_minutes <= 0:
        raise ValueError("warmup_minutes must be a positive int")

    excluded: set[int] = set()
    warmup_ms = warmup_minutes * ONE_MINUTE_MS
    for _, group in frame.sort_values(["symbol", time_column]).groupby("symbol", sort=False):
        previous_time_ms: int | None = None
        active_warmup_end_ms: int | None = None
        for index in group.index:
            current_time_ms = int(frame.at[index, time_column])
            if active_warmup_end_ms is not None:
                if current_time_ms < active_warmup_end_ms:
                    excluded.add(int(index))
                    continue
                active_warmup_end_ms = None
                previous_time_ms = current_time_ms
                continue
            if previous_time_ms is not None and current_time_ms - previous_time_ms > 3 * ONE_MINUTE_MS:
                excluded.add(int(index))
                active_warmup_end_ms = current_time_ms + warmup_ms
                previous_time_ms = current_time_ms
                continue
            previous_time_ms = current_time_ms
    return frozenset(excluded)


def has_critical_fail(rows: list[DataQualityRow]) -> bool:
    return any(row.status == AuditStatus.FAIL and row.severity == CRITICAL for row in rows)


def _row(
    check_name: str,
    status: AuditStatus,
    severity: str,
    affected_rows: int,
    message: str,
    *,
    symbol: str = "",
    timestamp_ms: int | None = None,
    previous_timestamp_ms: int | None = None,
    gap_minutes: float | None = None,
    technical_noise_shock: bool | None = None,
    excluded_from_detector: bool | None = None,
    excluded_from_ml_dataset: bool | None = None,
    reason: str = "",
) -> DataQualityRow:
    return DataQualityRow(
        check_name=check_name,
        status=status,
        severity=severity,
        affected_rows=affected_rows,
        message=message,
        symbol=symbol,
        timestamp_ms=timestamp_ms,
        previous_timestamp_ms=previous_timestamp_ms,
        gap_minutes=gap_minutes,
        technical_noise_shock=technical_noise_shock,
        excluded_from_detector=excluded_from_detector,
        excluded_from_ml_dataset=excluded_from_ml_dataset,
        reason=reason,
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
    if dataset_name == "candles_1m":
        rows.extend(_technical_noise_shock_checks(frame))
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


def _technical_noise_shock_checks(frame: pd.DataFrame) -> list[DataQualityRow]:
    rows: list[DataQualityRow] = []
    if not {"symbol", "open_time_ms"}.issubset(frame.columns):
        return rows

    shock_rows: list[DataQualityRow] = []
    for symbol, group in frame.sort_values(["symbol", "open_time_ms"]).groupby("symbol", sort=False):
        times = group["open_time_ms"].astype("int64")
        previous_times = times.shift(1)
        gaps_ms = times - previous_times
        mask = gaps_ms > 3 * ONE_MINUTE_MS
        for index in group.index[mask.fillna(False)]:
            timestamp_ms = int(frame.at[index, "open_time_ms"])
            previous_timestamp_ms = int(previous_times.loc[index])
            gap_minutes = float((timestamp_ms - previous_timestamp_ms) / ONE_MINUTE_MS)
            shock_rows.append(_row(
                "candles_1m_technical_noise_shock",
                AuditStatus.WARN,
                WARNING,
                1,
                "first 1m candle after raw timestamp gap > 3 minutes; excluded from detector, ML dataset, and warm-up context",
                symbol=str(symbol),
                timestamp_ms=timestamp_ms,
                previous_timestamp_ms=previous_timestamp_ms,
                gap_minutes=gap_minutes,
                technical_noise_shock=True,
                excluded_from_detector=True,
                excluded_from_ml_dataset=True,
                reason="api_maintenance_gap_aftershock",
            ))

    rows.extend(_warmup_window_rows(frame, shock_rows))

    rows.append(_row(
        "candles_1m_technical_noise_shock_detected",
        AuditStatus.WARN if shock_rows else AuditStatus.PASS,
        WARNING if shock_rows else INFO,
        len(shock_rows),
        "technical aftershock candles detected and marked for exclusion" if shock_rows else "no first candles after raw timestamp gaps > 3 minutes",
        technical_noise_shock=bool(shock_rows),
        excluded_from_detector=bool(shock_rows),
        excluded_from_ml_dataset=bool(shock_rows),
        reason="api_maintenance_gap_aftershock" if shock_rows else "",
    ))
    rows.extend(shock_rows)
    return rows



def _warmup_window_rows(frame: pd.DataFrame, shock_rows: list[DataQualityRow]) -> list[DataQualityRow]:
    rows: list[DataQualityRow] = []
    if not shock_rows or not {"symbol", "open_time_ms"}.issubset(frame.columns):
        return rows
    for shock in shock_rows:
        if shock.timestamp_ms is None:
            continue
        start_ms = int(shock.timestamp_ms)
        end_ms = start_ms + WARMUP_WINDOW_MINUTES * ONE_MINUTE_MS
        mask = (
            (frame["symbol"] == shock.symbol)
            & (frame["open_time_ms"].astype("int64") >= start_ms)
            & (frame["open_time_ms"].astype("int64") < end_ms)
        )
        affected_rows = int(mask.sum())
        rows.append(_row(
            "candles_1m_warmup_window",
            AuditStatus.WARN,
            WARNING,
            affected_rows,
            f"{WARMUP_WINDOW_MINUTES}m rolling-context warm-up window after raw timestamp gap; excluded from trigger generation",
            symbol=shock.symbol,
            timestamp_ms=start_ms,
            previous_timestamp_ms=shock.previous_timestamp_ms,
            gap_minutes=shock.gap_minutes,
            technical_noise_shock=True,
            excluded_from_detector=True,
            excluded_from_ml_dataset=True,
            reason="warmup_after_data_gap",
        ))
    return rows


def _csv_value(value: object) -> object:
    return "" if value is None else value


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
