"""Anomaly validator for market data quality checks."""

from __future__ import annotations

from datetime import timezone

import pandas as pd

from constants import SPREAD_TO_CLOSE_WARNING_THRESHOLD
from domain.enums.data_quality_severity import DataQualitySeverity
from domain.enums.timeframe import Timeframe
from domain.models.data_quality_issue import DataQualityIssue


class DataValidator:
    """Validate common anomalies in OHLCV/OI data."""

    def validate(self, symbol: str, timeframe: Timeframe, data: pd.DataFrame) -> list[DataQualityIssue]:
        if data.empty or "timestamp" not in data.columns:
            return []

        normalized = data.reset_index(drop=True)
        issues: list[DataQualityIssue] = []
        ts = pd.to_datetime(normalized["timestamp"], unit="ms", utc=True, errors="coerce")

        def add_issue(pos: int, issue_type: str, severity: DataQualitySeverity, description: str) -> None:
            if pd.isna(ts.iloc[pos]):
                return
            tstamp = ts.iloc[pos].to_pydatetime().astimezone(timezone.utc)
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
            if col in normalized.columns:
                bad = normalized[col] < 0
                for pos in normalized.index[bad.fillna(False)]:
                    add_issue(int(pos), "negative_price", DataQualitySeverity.CRITICAL, f"Negative {col} value")

        for col in ("volume", "open_interest"):
            if col in normalized.columns:
                bad = normalized[col] < 0
                for pos in normalized.index[bad.fillna(False)]:
                    add_issue(int(pos), "negative_volume", DataQualitySeverity.ERROR, f"Negative {col} value")

        if {"high", "low", "close"}.issubset(normalized.columns):
            spread = (normalized["high"] - normalized["low"]).abs()
            base = normalized["close"].abs().replace(0, pd.NA)
            ratio = spread / base
            suspicious = ratio > SPREAD_TO_CLOSE_WARNING_THRESHOLD
            for pos in normalized.index[suspicious.fillna(False)]:
                threshold_pct = int(SPREAD_TO_CLOSE_WARNING_THRESHOLD * 100)
                add_issue(
                    int(pos),
                    "suspicious_spread",
                    DataQualitySeverity.WARNING,
                    f"Spread exceeds {threshold_pct}% of close",
                )

        if "volume" in normalized.columns:
            zero_volume = normalized["volume"] == 0
            for pos in normalized.index[zero_volume.fillna(False)]:
                add_issue(int(pos), "zero_volume", DataQualitySeverity.INFO, "Zero candle volume")

        return issues
