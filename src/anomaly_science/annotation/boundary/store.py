"""Append-only labels and derived geometry for visible resistance."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from anomaly_science.annotation.desk.clock import now_ms, utc_iso_from_ms
from anomaly_science.annotation.desk.json_io import append_jsonl, iter_jsonl_objects

BOUNDARY_LABEL_SCHEMA_VERSION = "visible_resistance_label_v1"
BOUNDARY_STATUSES = frozenset({"clear_boundary", "borderline", "no_boundary"})


@dataclass(frozen=True, slots=True)
class BoundaryLabelStore:
    path: Path

    def read_effective(self) -> dict[str, dict[str, Any]]:
        labels: dict[str, dict[str, Any]] = {}
        for row in iter_jsonl_objects(self.path):
            event_id = str(row.get("event_id") or "")
            if not event_id:
                raise ValueError(f"boundary label row has empty event_id: {self.path}")
            if row.get("unlabeled") is True:
                labels.pop(event_id, None)
            else:
                labels[event_id] = row
        return labels

    def append(
        self,
        payload: dict[str, Any],
        *,
        candidate: dict[str, Any],
        candles: dict[str, list[Any]],
        allowed_tfs: set[str],
    ) -> dict[str, Any]:
        normalized = normalize_boundary_label(
            payload,
            candidate=candidate,
            candles=candles,
            allowed_tfs=allowed_tfs,
        )
        saved_at_ms = now_ms()
        row = {
            **normalized,
            "label_schema_version": BOUNDARY_LABEL_SCHEMA_VERSION,
            "source": "browser_visible_resistance_reviewer",
            "saved_at_ms": saved_at_ms,
            "saved_at_utc": utc_iso_from_ms(saved_at_ms),
        }
        append_jsonl(self.path, row)
        return row

    def append_unlabel(self, event_id: str, *, allowed_event_ids: set[str]) -> dict[str, Any]:
        if event_id not in allowed_event_ids:
            raise ValueError("unknown boundary event_id")
        saved_at_ms = now_ms()
        row = {
            "event_id": event_id,
            "unlabeled": True,
            "label_schema_version": BOUNDARY_LABEL_SCHEMA_VERSION,
            "source": "browser_visible_resistance_reviewer",
            "saved_at_ms": saved_at_ms,
            "saved_at_utc": utc_iso_from_ms(saved_at_ms),
        }
        append_jsonl(self.path, row)
        return row


def normalize_boundary_label(
    payload: dict[str, Any],
    *,
    candidate: dict[str, Any],
    candles: dict[str, list[Any]],
    allowed_tfs: set[str],
) -> dict[str, Any]:
    event_id = str(payload.get("event_id") or "")
    if event_id != str(candidate["event_id"]):
        raise ValueError("boundary label event_id does not match candidate")
    status = str(payload.get("status") or "")
    if status not in BOUNDARY_STATUSES:
        raise ValueError(f"boundary status must be one of {sorted(BOUNDARY_STATUSES)}")
    tf = str(payload.get("tf") or "")
    if tf not in allowed_tfs:
        raise ValueError("unsupported boundary annotation timeframe")
    reactions = _normalize_reactions(payload.get("reactions"), candles, candidate)
    if status == "clear_boundary" and len(reactions) < 2:
        raise ValueError("clear boundary requires at least two independent reactions")
    if status == "borderline" and not reactions:
        raise ValueError("borderline boundary requires at least one marked reaction")
    if status == "no_boundary" and reactions:
        raise ValueError("no_boundary label must not contain reactions")
    return {
        "event_id": event_id,
        "symbol": str(candidate["symbol"]),
        "snapshot_time_ms": int(candidate["snapshot_time_ms"]),
        "feature_cutoff_time_ms": int(candidate["feature_cutoff_time_ms"]),
        "tf": tf,
        "status": status,
        "comment": str(payload.get("comment") or ""),
        "reactions": reactions,
        "metrics": boundary_metrics(reactions, candles) if reactions else {},
    }


def _normalize_reactions(
    raw: Any,
    candles: dict[str, list[Any]],
    candidate: dict[str, Any],
) -> list[dict[str, Any]]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ValueError("reactions must be a list")
    timestamp = np.asarray(candles["timestamp"], dtype=np.int64)
    high = np.asarray(candles["high"], dtype=np.float64)
    low = np.asarray(candles["low"], dtype=np.float64)
    index_by_time = {int(value): index for index, value in enumerate(timestamp)}
    snapshot_ms = int(candidate["snapshot_time_ms"])
    review_start_ms = int(candidate["review_start_ms"])
    reactions: list[dict[str, Any]] = []
    previous_rejection_ms: int | None = None
    for position, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"reaction {position} must be an object")
        touch_ms = _integer_ms(item.get("touch_time_ms"), f"reaction {position} touch_time_ms")
        rejection_ms = _integer_ms(item.get("rejection_time_ms"), f"reaction {position} rejection_time_ms")
        if not (review_start_ms <= touch_ms < rejection_ms < snapshot_ms):
            raise ValueError(f"reaction {position} must be fully observed before snapshot")
        if previous_rejection_ms is not None and touch_ms <= previous_rejection_ms:
            raise ValueError("reactions must be independent and non-overlapping")
        touch_index = index_by_time.get(touch_ms)
        rejection_index = index_by_time.get(rejection_ms)
        if touch_index is None or rejection_index is None:
            raise ValueError(f"reaction {position} timestamps must match candles on selected timeframe")
        touch_price = _positive_number(item.get("touch_price"), f"reaction {position} touch_price")
        rejection_price = _positive_number(item.get("rejection_price"), f"reaction {position} rejection_price")
        if not math.isclose(touch_price, float(high[touch_index]), rel_tol=1e-9, abs_tol=1e-12):
            raise ValueError(f"reaction {position} touch must snap to candle high")
        if not math.isclose(rejection_price, float(low[rejection_index]), rel_tol=1e-9, abs_tol=1e-12):
            raise ValueError(f"reaction {position} rejection must snap to candle low")
        if rejection_price >= touch_price:
            raise ValueError(f"reaction {position} rejection must move below its test")
        reactions.append(
            {
                "touch_time_ms": touch_ms,
                "touch_price": touch_price,
                "rejection_time_ms": rejection_ms,
                "rejection_price": rejection_price,
            }
        )
        previous_rejection_ms = rejection_ms
    return reactions


def boundary_metrics(
    reactions: list[dict[str, Any]],
    candles: dict[str, list[Any]],
) -> dict[str, Any]:
    timestamp = np.asarray(candles["timestamp"], dtype=np.int64)
    high = np.asarray(candles["high"], dtype=np.float64)
    low = np.asarray(candles["low"], dtype=np.float64)
    close = np.asarray(candles["close"], dtype=np.float64)
    index_by_time = {int(value): index for index, value in enumerate(timestamp)}
    touch_prices = np.asarray([float(item["touch_price"]) for item in reactions])
    zone_lower = float(np.min(touch_prices))
    zone_upper = float(np.max(touch_prices))
    zone_mid = float(np.median(touch_prices))
    zone_thickness_bps = float((zone_upper / zone_lower - 1.0) * 10_000.0)
    reaction_pct: list[float] = []
    reaction_atr: list[float] = []
    approach_atr: list[float] = []
    separation_hours: list[float] = []
    prior_rejection_index: int | None = None

    previous_close = np.r_[close[0], close[:-1]]
    true_range_pct = np.maximum.reduce(
        [
            (high - low) / np.maximum(close, 1e-12),
            np.abs(high - previous_close) / np.maximum(close, 1e-12),
            np.abs(low - previous_close) / np.maximum(close, 1e-12),
        ]
    )
    for reaction in reactions:
        touch_index = index_by_time[int(reaction["touch_time_ms"])]
        rejection_index = index_by_time[int(reaction["rejection_time_ms"])]
        amplitude = (float(reaction["touch_price"]) - float(reaction["rejection_price"])) / float(
            reaction["touch_price"]
        )
        baseline_start = max(0, touch_index - 96)
        baseline = true_range_pct[baseline_start:touch_index]
        baseline_atr = float(np.median(baseline)) if len(baseline) else float("nan")
        reaction_pct.append(float(amplitude))
        reaction_atr.append(float(amplitude / baseline_atr) if baseline_atr > 0 else float("nan"))
        approach_start = prior_rejection_index if prior_rejection_index is not None else baseline_start
        preceding_low = float(np.min(low[approach_start : touch_index + 1]))
        approach = (float(reaction["touch_price"]) - preceding_low) / float(reaction["touch_price"])
        approach_atr.append(float(approach / baseline_atr) if baseline_atr > 0 else float("nan"))
        if prior_rejection_index is not None:
            separation_hours.append(
                float((int(reaction["touch_time_ms"]) - int(timestamp[prior_rejection_index])) / 3_600_000)
            )
        prior_rejection_index = rejection_index

    first_touch_ms = int(reactions[0]["touch_time_ms"])
    first_touch_index = index_by_time[first_touch_ms]
    pad = max((zone_upper - zone_lower) * 0.5, zone_mid * 0.001)
    prior_overlap = np.nonzero(
        (high[:first_touch_index] >= zone_lower - pad)
        & (low[:first_touch_index] <= zone_upper + pad)
    )[0]
    left_anchor_index = int(prior_overlap[-1]) if len(prior_overlap) else 0
    left_space_hours = float((first_touch_ms - int(timestamp[left_anchor_index])) / 3_600_000)

    return {
        "reaction_count": len(reactions),
        "zone_lower": zone_lower,
        "zone_upper": zone_upper,
        "zone_mid": zone_mid,
        "zone_thickness_bps": zone_thickness_bps,
        "duration_hours": float(
            (int(reactions[-1]["touch_time_ms"]) - first_touch_ms) / 3_600_000
        ),
        "left_space_hours": left_space_hours,
        "left_space_censored_by_window": not len(prior_overlap),
        "minimum_rejection_pct": float(np.min(reaction_pct)),
        "median_rejection_pct": float(np.median(reaction_pct)),
        "minimum_rejection_atr": _finite_min(reaction_atr),
        "median_rejection_atr": _finite_median(reaction_atr),
        "minimum_approach_atr": _finite_min(approach_atr),
        "median_approach_atr": _finite_median(approach_atr),
        "minimum_separation_hours": float(np.min(separation_hours)) if separation_hours else None,
    }


def _finite_min(values: list[float]) -> float | None:
    finite = [value for value in values if math.isfinite(value)]
    return float(min(finite)) if finite else None


def _finite_median(values: list[float]) -> float | None:
    finite = [value for value in values if math.isfinite(value)]
    return float(np.median(finite)) if finite else None


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
