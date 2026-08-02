"""Repository for the futures structural-stop ledger and its manual reviews."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from anomaly_science.annotation.desk.structural_trade_reviews import StructuralTradeReviewStore


@dataclass(slots=True)
class StructuralTradeRepository:
    project_root: Path
    _trades_cache: list[dict[str, Any]] | None = field(default=None, init=False, repr=False)
    _summary_cache: list[dict[str, Any]] | None = field(default=None, init=False, repr=False)

    @property
    def root(self) -> Path:
        return self.project_root / ".output" / "research" / "binance_usdm_trend_is_2025_v1"

    @property
    def trades_path(self) -> Path:
        return self.root / "structural_protected_stop_trades.json"

    @property
    def audit_path(self) -> Path:
        return self.root / "structural_protected_stop_policy_audit.csv"

    @property
    def reviews(self) -> StructuralTradeReviewStore:
        return StructuralTradeReviewStore.for_run_dir(self.root)

    def trades(self) -> list[dict[str, Any]]:
        if self._trades_cache is not None:
            return self._trades_cache
        if not self.trades_path.exists():
            return []
        payload = json.loads(self.trades_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"structural trade ledger must be a JSON list: {self.trades_path}")
        rows: list[dict[str, Any]] = []
        for raw in payload:
            if not isinstance(raw, dict):
                raise ValueError(f"structural trade ledger contains a non-object row: {self.trades_path}")
            trade = dict(raw)
            fill = datetime.fromtimestamp(int(trade["fill_time_ms"]) / 1_000, tz=UTC)
            minute = int(trade.get("crossing_minute_of_day", fill.hour * 60 + fill.minute))
            trade["setup_family"] = "structural_swing_low"
            trade["session"] = "asia" if minute < 480 else ("europe" if minute < 960 else "us")
            trade["day"] = fill.strftime("%Y-%m-%d")
            trade["month"] = fill.strftime("%Y-%m")
            iso_calendar = fill.isocalendar()
            trade["week"] = f"{iso_calendar.year}-W{iso_calendar.week:02d}"
            rows.append(trade)
        self._trades_cache = rows
        return self._trades_cache

    def trade_by_id(self, trade_id: str) -> dict[str, Any] | None:
        return next((trade for trade in self.trades() if str(trade.get("trade_id")) == trade_id), None)

    def list_rows(self) -> list[dict[str, Any]]:
        reviews = self.reviews.read_effective()
        columns = (
            "trade_id",
            "symbol",
            "tf",
            "net_r",
            "outcome",
            "setup_family",
            "session",
            "day",
            "month",
            "week",
        )
        rows = [{column: trade.get(column) for column in columns} for trade in self.trades()]
        for row in rows:
            review = reviews.get(str(row["trade_id"]))
            row["has_result_annotation"] = bool(
                review and (str(review.get("comment") or "").strip() or review.get("correct_sl") is not None)
            )
        return rows

    def review_for_trade(self, trade_id: str) -> dict[str, Any] | None:
        return self.reviews.read_effective().get(trade_id)

    def save_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        known_trades = {str(trade["trade_id"]): trade for trade in self.trades()}
        return self.reviews.append(payload, known_trades=known_trades)

    def summary(self) -> list[dict[str, Any]]:
        if self._summary_cache is not None:
            return self._summary_cache
        if not self.audit_path.exists():
            return []
        frame = pd.read_csv(self.audit_path).astype(object)
        self._summary_cache = frame.where(pd.notna(frame), None).to_dict("records")
        return self._summary_cache
