"""Append-only event-label state for the level desk."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anomaly_science.annotation.desk.clock import now_ms
from anomaly_science.annotation.desk.json_io import append_jsonl, iter_jsonl_objects
from anomaly_science.annotation.schemas import LEVEL_LABEL_SCHEMA_VERSION, validate_label_payload


@dataclass(frozen=True, slots=True)
class LabelStore:
    path: Path

    def read_effective(self) -> dict[str, dict[str, Any]]:
        labels: dict[str, dict[str, Any]] = {}
        for row in iter_jsonl_objects(self.path):
            event_id = str(row.get("event_id") or "")
            if not event_id:
                raise ValueError(f"label row has empty event_id: {self.path}")
            if row.get("unlabeled") is True:
                labels.pop(event_id, None)
                continue
            labels[event_id] = row
        return labels

    def read_effective_list(self) -> list[dict[str, Any]]:
        return list(self.read_effective().values())

    def append_label(self, payload: dict[str, Any], *, allowed_event_ids: set[str]) -> dict[str, Any]:
        row = dict(payload)
        row["event_id"] = str(row.get("event_id") or "")
        if row["event_id"] not in allowed_event_ids:
            raise ValueError("unknown event_id")
        row["label_schema_version"] = LEVEL_LABEL_SCHEMA_VERSION
        row["saved_at_ms"] = now_ms()
        validate_label_payload(row)
        append_jsonl(self.path, row)
        return row

    def append_unlabels(self, event_ids: list[str], *, allowed_event_ids: set[str]) -> list[dict[str, Any]]:
        saved_at_ms = now_ms()
        rows: list[dict[str, Any]] = []
        for event_id in dict.fromkeys(str(x) for x in event_ids):
            if event_id not in allowed_event_ids:
                raise ValueError("unknown event_id")
            row = {
                "event_id": event_id,
                "unlabeled": True,
                "source": "browser_level_labeler",
                "label_schema_version": LEVEL_LABEL_SCHEMA_VERSION,
                "saved_at_ms": saved_at_ms,
            }
            append_jsonl(self.path, row)
            rows.append(row)
        return rows


def label_for_group(group: dict[str, Any], labels: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    group_id = str(group["event_id"])
    if group_id in labels:
        return labels[group_id]
    for event_id in group.get("source_event_ids", []):
        row = labels.get(str(event_id))
        if row is not None:
            return row
    return None
