"""Append-only result-review annotations with explicit drawing contracts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anomaly_science.annotation.desk.clock import now_ms, utc_iso_from_ms
from anomaly_science.annotation.desk.json_io import append_jsonl, iter_jsonl_objects

RESULT_ANNOTATION_SCHEMA_VERSION = "result_trade_annotation_v1"
RESULT_ANNOTATIONS_FILENAME = "result_trade_annotations.jsonl"


@dataclass(frozen=True, slots=True)
class ResultAnnotationStore:
    path: Path

    @classmethod
    def for_run_dir(cls, run_dir: Path) -> "ResultAnnotationStore":
        return cls(run_dir / RESULT_ANNOTATIONS_FILENAME)

    def read_effective(self) -> dict[str, dict[str, Any]]:
        annotations: dict[str, dict[str, Any]] = {}
        for row in iter_jsonl_objects(self.path):
            trade_id = str(row.get("trade_id") or "")
            if not trade_id:
                raise ValueError(f"result annotation row has empty trade_id: {self.path}")
            annotations[trade_id] = row
        return annotations

    def append(
        self,
        payload: dict[str, Any],
        *,
        known_trade_ids: set[str],
    ) -> dict[str, Any]:
        trade_id = str(payload.get("trade_id") or "")
        if not trade_id:
            raise ValueError("trade_id is required")
        if trade_id not in known_trade_ids:
            raise ValueError("unknown trade_id")
        saved_at_ms = now_ms()
        row = {
            "result_annotation_schema_version": RESULT_ANNOTATION_SCHEMA_VERSION,
            "trade_id": trade_id,
            "comment": str(payload.get("comment") or ""),
            "drawings": normalize_result_drawings(payload.get("drawings") or {}),
            "saved_at_ms": saved_at_ms,
            "saved_at_utc": utc_iso_from_ms(saved_at_ms),
            "source": "browser_result_reviewer",
        }
        append_jsonl(self.path, row)
        return row


def normalize_result_drawings(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("drawings must be an object")
    drawings: dict[str, Any] = {}
    if raw.get("level") is not None:
        drawings["level"] = _normalize_level(raw["level"], "drawings.level")
    if raw.get("pump") is not None:
        drawings["pump"] = _normalize_pump(raw["pump"])
    if raw.get("exitPoint") is not None:
        drawings["exitPoint"] = _normalize_point(raw["exitPoint"], "drawings.exitPoint")
    if raw.get("zigzag") is not None:
        drawings["zigzag"] = _normalize_zigzag(raw["zigzag"])
    if raw.get("sl") is not None:
        drawings["sl"] = _normalize_sl(raw["sl"])
    return drawings


def _number(value: Any, where: str, *, positive: bool = False) -> float:
    if value is None:
        raise ValueError(f"{where} is required")
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where} must be numeric") from exc
    if not math.isfinite(out):
        raise ValueError(f"{where} must be finite")
    if positive and out <= 0:
        raise ValueError(f"{where} must be positive")
    return out


def _ms(value: Any, where: str) -> int:
    out = _number(value, where)
    if out < 0:
        raise ValueError(f"{where} must be non-negative epoch milliseconds")
    return int(round(out))


def _optional_int(value: Any, where: str) -> int | None:
    if value is None:
        return None
    return int(round(_number(value, where)))


def _normalize_level(raw: Any, where: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be an object")
    start_ms = _ms(raw.get("start_ms", raw.get("startMs")), f"{where}.start_ms")
    end_ms = _ms(raw.get("end_ms", raw.get("endMs")), f"{where}.end_ms")
    if start_ms >= end_ms:
        raise ValueError(f"{where}.start_ms must be before end_ms")
    return {
        "price": _number(raw.get("price"), f"{where}.price", positive=True),
        "start_ms": start_ms,
        "end_ms": end_ms,
        "broken": bool(raw.get("broken", False)),
    }


def _normalize_point(raw: Any, where: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be an object")
    point = {
        "ms": _ms(raw.get("ms"), f"{where}.ms"),
        "price": _number(raw.get("price"), f"{where}.price", positive=True),
    }
    idx = _optional_int(raw.get("idx"), f"{where}.idx")
    if idx is not None:
        point["idx"] = idx
    return point


def _normalize_pump(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("drawings.pump must be an object")
    if isinstance(raw.get("start"), dict) and isinstance(raw.get("high"), dict):
        start = _normalize_point(raw["start"], "drawings.pump.start")
        high = _normalize_point(raw["high"], "drawings.pump.high")
    else:
        start = {
            "ms": _ms(raw.get("start_ms", raw.get("startMs")), "drawings.pump.start_ms"),
            "price": _number(raw.get("start_price", raw.get("low")), "drawings.pump.start_price", positive=True),
        }
        high = {
            "ms": _ms(raw.get("end_ms", raw.get("endMs")), "drawings.pump.end_ms"),
            "price": _number(raw.get("high_price", raw.get("high")), "drawings.pump.high_price", positive=True),
        }
    if start["ms"] >= high["ms"]:
        raise ValueError("drawings.pump.start.ms must be before high.ms")
    if high["price"] <= start["price"]:
        raise ValueError("drawings.pump.high.price must be above start.price")
    return {"start": start, "high": high}


def _normalize_zigzag(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("points"), list):
        raise ValueError("drawings.zigzag.points must be a list")
    points = [_normalize_point(point, f"drawings.zigzag.points[{i}]") for i, point in enumerate(raw["points"])]
    points.sort(key=lambda point: point["ms"])
    return {"points": points}


def _normalize_sl(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("drawings.sl must be an object")
    out: dict[str, Any] = {"price": _number(raw.get("price"), "drawings.sl.price", positive=True)}
    if raw.get("hit_ms") is not None:
        out["hit_ms"] = _ms(raw.get("hit_ms"), "drawings.sl.hit_ms")
    if raw.get("end_ms") is not None:
        out["end_ms"] = _ms(raw.get("end_ms"), "drawings.sl.end_ms")
    return out
