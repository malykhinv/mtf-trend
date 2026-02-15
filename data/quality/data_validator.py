"""Модуль проекта."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from constants import SPREAD_TO_CLOSE_WARNING_THRESHOLD
from domain.enums.data_quality_severity import DataQualitySeverity
from domain.enums.timeframe import Timeframe
from domain.models.data_quality_issue import DataQualityIssue


class DataValidator:
    """Класс."""
    @staticmethod
    def validate(symbol: str, timeframe: Timeframe, data: pd.DataFrame) -> list[DataQualityIssue]:
        """Проверяет данные и собирает найденные проблемы."""
        if data.empty:
            return []

        normalized = data.reset_index(drop=True)
        issues: list[DataQualityIssue] = []

        def add_dataset_issue(issue_type: str, severity: DataQualitySeverity, description: str) -> None:
            issues.append(
                DataQualityIssue(
                    symbol=symbol,
                    timeframe=timeframe,
                    issue_type=issue_type,
                    severity=severity,
                    timestamp=datetime.fromtimestamp(0, tz=timezone.utc),
                    description=description,
                )
            )

        if "timestamp" not in normalized.columns:
            add_dataset_issue(
                "missing_timestamp_column",
                DataQualitySeverity.ERROR,
                "Отсутствует колонка timestamp: данные нельзя валидировать по времени, дальнейшие проверки остановлены.",
            )
            return issues

        ts = pd.to_datetime(normalized["timestamp"], unit="ms", errors="coerce")
        if ts.notna().sum() == 0:
            add_dataset_issue(
                "invalid_timestamp_series",
                DataQualitySeverity.ERROR,
                "Колонка timestamp не распознана (все значения NaT): данные нельзя валидировать по времени, дальнейшие проверки остановлены.",
            )
            return issues

        numeric_columns = {
            col: pd.to_numeric(normalized[col], errors="coerce")
            for col in ("open", "high", "low", "close", "volume", "open_interest")
            if col in normalized.columns
        }

        def add_issue(pos: int, issue_type: str, severity: DataQualitySeverity, description: str) -> None:
            if pd.isna(ts.iloc[pos]):
                return
            tstamp = ts.iloc[pos].to_pydatetime()
            issues.append(
                DataQualityIssue(
                    symbol=symbol,
                    timeframe=timeframe,
                    issue_type=issue_type,
                    severity=severity,
                    timestamp=tstamp,
                    description=description,
                )
            )

        for col in ("open", "high", "low", "close"):
            if col in numeric_columns:
                price_series = numeric_columns[col]
                bad_mask = price_series.lt(0) & price_series.notna()
                for pos in normalized.index[bad_mask]:
                    add_issue(int(pos), "negative_price", DataQualitySeverity.CRITICAL, f"Negative {col} value")

        issue_type_by_column = {
            "volume": "negative_volume",
            "open_interest": "negative_open_interest",
        }
        for col, issue_type in issue_type_by_column.items():
            if col in numeric_columns:
                numeric_series = numeric_columns[col]
                bad_mask = numeric_series.lt(0) & numeric_series.notna()
                for pos in normalized.index[bad_mask]:
                    add_issue(int(pos), issue_type, DataQualitySeverity.ERROR, f"Negative {col} value")

        if {"high", "low", "close"}.issubset(numeric_columns):
            spread = (numeric_columns["high"] - numeric_columns["low"]).abs()
            base = numeric_columns["close"].abs().replace(0, pd.NA)
            ratio = spread / base
            suspicious_mask = ratio.gt(SPREAD_TO_CLOSE_WARNING_THRESHOLD) & ratio.notna()
            for pos in normalized.index[suspicious_mask]:
                threshold_pct = int(SPREAD_TO_CLOSE_WARNING_THRESHOLD * 100)
                add_issue(
                    int(pos),
                    "suspicious_spread",
                    DataQualitySeverity.WARNING,
                    f"Spread exceeds {threshold_pct}% of close",
                )

        if "volume" in numeric_columns:
            volume_series = numeric_columns["volume"]
            zero_volume_mask = volume_series.eq(0) & volume_series.notna()
            for pos in normalized.index[zero_volume_mask]:
                add_issue(int(pos), "zero_volume", DataQualitySeverity.INFO, "Zero candle volume")

        for col, series in numeric_columns.items():
            if series.notna().sum() == 0:
                valid_ts_positions = normalized.index[ts.notna()]
                if len(valid_ts_positions) == 0:
                    continue
                add_issue(
                    int(valid_ts_positions[0]),
                    f"invalid_{col}_series",
                    DataQualitySeverity.ERROR,
                    f"Column {col} could not be parsed as numeric values",
                )

        return issues
