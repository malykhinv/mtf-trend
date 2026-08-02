"""Append-only manual reviews for the futures structural-stop experiment."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from anomaly_science.annotation.desk.clock import now_ms, utc_iso_from_ms
from anomaly_science.annotation.desk.json_io import append_jsonl, iter_jsonl_objects

STRUCTURAL_TRADE_REVIEW_SCHEMA_VERSION = "structural_trade_review_v1"
STRUCTURAL_TRADE_REVIEWS_FILENAME = "structural_trade_reviews.jsonl"


@dataclass(frozen=True, slots=True)
class StructuralTradeReviewStore:
    """Persist trader judgment separately from labels and iteration artifacts."""

    path: Path

    @classmethod
    def for_run_dir(cls, run_dir: Path) -> "StructuralTradeReviewStore":
        return cls(run_dir / STRUCTURAL_TRADE_REVIEWS_FILENAME)

    def read_effective(self) -> dict[str, dict[str, Any]]:
        reviews: dict[str, dict[str, Any]] = {}
        for raw in iter_jsonl_objects(self.path):
            trade_id = str(raw.get("trade_id") or "")
            if not trade_id:
                raise ValueError(f"structural trade review has empty trade_id: {self.path}")
            row = dict(raw)
            row["correct_sl"] = normalize_correct_sl(raw.get("correct_sl"))
            reviews[trade_id] = row
        return reviews

    def append(
        self,
        payload: dict[str, Any],
        *,
        known_trades: Mapping[str, dict[str, Any]],
    ) -> dict[str, Any]:
        trade_id = str(payload.get("trade_id") or "")
        if not trade_id:
            raise ValueError("trade_id is required")
        trade = known_trades.get(trade_id)
        if trade is None:
            raise ValueError("unknown structural trade_id")

        correct_sl = normalize_correct_sl(payload.get("correct_sl"))
        if correct_sl is not None:
            fill_time_ms = _integer_ms(trade.get("fill_time_ms"), "trade.fill_time_ms")
            entry_price = _positive_number(trade.get("entry_price"), "trade.entry_price")
            if correct_sl["anchor_time_ms"] >= fill_time_ms:
                raise ValueError("correct_sl anchor candle must start before trade entry")
            if correct_sl["price"] >= entry_price:
                raise ValueError("correct_sl price must be below long entry price")

        saved_at_ms = now_ms()
        row = {
            "structural_trade_review_schema_version": STRUCTURAL_TRADE_REVIEW_SCHEMA_VERSION,
            "trade_id": trade_id,
            "comment": str(payload.get("comment") or ""),
            "correct_sl": correct_sl,
            "saved_at_ms": saved_at_ms,
            "saved_at_utc": utc_iso_from_ms(saved_at_ms),
            "source": "browser_futures_structural_reviewer",
        }
        append_jsonl(self.path, row)
        return row


def normalize_correct_sl(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("correct_sl must be an object or null")
    return {
        "anchor_time_ms": _integer_ms(raw.get("anchor_time_ms"), "correct_sl.anchor_time_ms"),
        "price": _positive_number(raw.get("price"), "correct_sl.price"),
    }


def _positive_number(value: Any, where: str) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{where} must be a positive number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where} must be a positive number") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{where} must be a positive finite number")
    return number


def _integer_ms(value: Any, where: str) -> int:
    number = _positive_number(value, where)
    rounded = round(number)
    if number != rounded:
        raise ValueError(f"{where} must be integer epoch milliseconds")
    return int(rounded)
