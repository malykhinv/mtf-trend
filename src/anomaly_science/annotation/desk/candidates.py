"""Candidate validation and multi-timeframe grouping for level annotation."""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
import pandas as pd

from anomaly_science.annotation.schemas import REQUIRED_CANDIDATE_COLUMNS

DEFAULT_TF_MINUTES = {
    "1m": 1,
    "3m": 3,
    "5m": 5,
    "10m": 10,
    "15m": 15,
    "1h": 60,
    "4h": 240,
}


def validate_candidates(frame: pd.DataFrame) -> None:
    missing = sorted(set(REQUIRED_CANDIDATE_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"candidate frame missing columns: {missing}")
    if frame["event_id"].duplicated().any():
        raise ValueError("candidate event_id must be unique")


def tf_minutes(tf: str, mapping: dict[str, int]) -> int:
    return int(mapping.get(str(tf), 0))


def make_group_id(symbol: str, start_ms: int, end_ms: int, event_ids: list[str]) -> str:
    raw = f"{symbol}|{start_ms}|{end_ms}|{'|'.join(sorted(event_ids))}"
    digest = hashlib.blake2b(raw.encode("utf-8"), digest_size=10).hexdigest()
    return f"grp_{digest}"


def build_annotation_groups(frame: pd.DataFrame, tf_minutes_by_id: dict[str, int]) -> list[dict[str, Any]]:
    """Group rows that represent the same market event on multiple TFs."""

    records = frame.replace({np.nan: None}).to_dict("records")
    if "annotation_group_id" in frame.columns:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in records:
            gid = str(row.get("annotation_group_id") or row["event_id"])
            grouped.setdefault(gid, []).append(row)
        return [_group_payload(gid, rows, tf_minutes_by_id) for gid, rows in grouped.items()]

    if {"pump_start_ms", "culmination_ms"}.issubset(frame.columns):
        groups: list[list[dict[str, Any]]] = []
        tolerance_ms = 6 * 60 * 60 * 1000
        sorted_frame = frame.sort_values(["symbol", "culmination_ms", "pump_start_ms"])
        for _, part in sorted_frame.groupby("symbol", sort=False):
            current: list[dict[str, Any]] = []
            current_culm: int | None = None
            for row in part.replace({np.nan: None}).to_dict("records"):
                culm = int(row.get("culmination_ms") or row.get("anchor_time_ms") or row["review_start_ms"])
                if current and current_culm is not None and abs(culm - current_culm) > tolerance_ms:
                    groups.append(current)
                    current = []
                current.append(row)
                current_culm = int(
                    np.median(
                        [
                            int(r.get("culmination_ms") or r.get("anchor_time_ms") or r["review_start_ms"])
                            for r in current
                        ]
                    )
                )
            if current:
                groups.append(current)
        return [_group_payload(None, rows, tf_minutes_by_id) for rows in groups]

    return [_group_payload(str(row["event_id"]), [row], tf_minutes_by_id) for row in records]


def _group_payload(group_id: str | None, rows: list[dict[str, Any]], tf_minutes_by_id: dict[str, int]) -> dict[str, Any]:
    variants = sorted(rows, key=lambda r: (-tf_minutes(str(r["tf"]), tf_minutes_by_id), str(r["tf"])))
    event_ids = [str(r["event_id"]) for r in variants]
    start = min(int(r["review_start_ms"]) for r in variants)
    end = max(int(r["review_end_ms"]) for r in variants)
    gid = group_id or make_group_id(str(variants[0]["symbol"]), start, end, event_ids)
    canonical = dict(variants[0])
    canonical.update(
        {
            "event_id": gid,
            "annotation_group_id": gid,
            "source_event_ids": event_ids,
            "variants": variants,
            "tf": "/".join(str(r["tf"]) for r in variants),
            "default_tf": str(variants[0]["tf"]),
            "review_start_ms": start,
            "review_end_ms": end,
        }
    )
    return canonical
