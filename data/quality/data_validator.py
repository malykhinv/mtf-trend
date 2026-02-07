"""Anomaly validator for market data quality checks."""

from __future__ import annotations

from datetime import timezone

import pandas as pd

from domain.enums.data_quality_severity import DataQualitySeverity
from domain.enums.timeframe import Timeframe
from domain.models.data_quality_issue import DataQualityIssue


class DataValidator:
    """Validate common anomalies in OHLCV/OI data."""

    def validate(self, symbol: str, timeframe: Timeframe, data: pd.DataFrame) -> list[DataQualityIssue]:
        if data.empty or "timestamp" not in data.columns:
            return []

        issues: list[DataQualityIssue] = []
        ts = pd.to_datetime(data["timestamp"], unit="ms", utc=True, errors="coerce")

        def add_issue(index: int, issue_type: str, severity: DataQualitySeverity, description: str) -> None:
            tstamp = ts.iloc[index].to_pydatetime().astimezone(timezone.utc)
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
            if col in data.columns:
                bad = data[col] < 0
                for idx in data.index[bad.fillna(False)]:
                    add_issue(int(idx), "negative_price", DataQualitySeverity.CRITICAL, f"Negative {col} value")

        for col in ("volume", "open_interest"):
            if col in data.columns:
                bad = data[col] < 0
                for idx in data.index[bad.fillna(False)]:
                    add_issue(int(idx), "negative_volume", DataQualitySeverity.ERROR, f"Negative {col} value")

        if {"high", "low", "close"}.issubset(data.columns):
            spread = (data["high"] - data["low"]).abs()
            base = data["close"].abs().replace(0, pd.NA)
            ratio = spread / base
            suspicious = ratio > 0.3
            for idx in data.index[suspicious.fillna(False)]:
                add_issue(int(idx), "suspicious_spread", DataQualitySeverity.WARNING, "Spread exceeds 30% of close")

        if "volume" in data.columns:
            zero_volume = data["volume"] == 0
            for idx in data.index[zero_volume.fillna(False)]:
                add_issue(int(idx), "zero_volume", DataQualitySeverity.INFO, "Zero candle volume")

        return issues
