"""Repository boundary between the HTTP desk and resistance artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from anomaly_science.annotation.boundary.store import BoundaryLabelStore
from anomaly_science.annotation.boundary.contracts import (
    BOUNDARY_CANDIDATE_SCHEMA_VERSION,
    BOUNDARY_TIMEFRAMES,
)
from anomaly_science.annotation.desk.candles import OhlcvWindowService
from anomaly_science.annotation.desk.candidates import json_candidate_row


@dataclass(slots=True)
class BoundaryReviewRepository:
    candidates_path: Path
    labels_path: Path
    ohlcv: OhlcvWindowService
    _candidates: pd.DataFrame | None = field(default=None, init=False, repr=False)

    def candidates(self) -> pd.DataFrame:
        if self._candidates is None:
            if not self.candidates_path.exists():
                raise ValueError(f"boundary candidates do not exist: {self.candidates_path}")
            frame = pd.read_parquet(self.candidates_path)
            required = {
                "candidate_schema_version",
                "event_id",
                "symbol",
                "tf",
                "snapshot_time_ms",
                "feature_cutoff_time_ms",
                "review_start_ms",
                "review_end_ms",
            }
            missing = required.difference(frame.columns)
            if missing:
                raise ValueError(f"boundary candidates missing columns: {sorted(missing)}")
            if not frame["candidate_schema_version"].eq(BOUNDARY_CANDIDATE_SCHEMA_VERSION).all():
                raise ValueError("unsupported boundary candidate schema")
            if frame["event_id"].duplicated().any():
                raise ValueError("boundary candidate event_id must be unique")
            if frame["feature_cutoff_time_ms"].gt(frame["snapshot_time_ms"]).any():
                raise ValueError("boundary candidate features extend past snapshot")
            if frame["review_end_ms"].ge(frame["snapshot_time_ms"]).any():
                raise ValueError("boundary candidate chart includes future candles")
            self._candidates = frame.set_index("event_id", drop=False)
        return self._candidates

    @property
    def labels(self) -> BoundaryLabelStore:
        return BoundaryLabelStore(self.labels_path)

    def event_ids(self) -> set[str]:
        return set(self.candidates().index.astype(str))

    def candidate(self, event_id: str) -> dict[str, Any] | None:
        frame = self.candidates()
        if event_id not in frame.index:
            return None
        row = frame.loc[event_id].replace({np.nan: None}).to_dict()
        return json_candidate_row(row)

    def list_payload(self) -> dict[str, Any]:
        labels = self.labels.read_effective()
        manifest_path = self.candidates_path.parent / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        rows: list[dict[str, Any]] = []
        for event_id, raw in self.candidates().iterrows():
            row = json_candidate_row(raw.replace({np.nan: None}).to_dict())
            label = labels.get(str(event_id))
            rows.append(
                {
                    "event_id": str(event_id),
                    "symbol": str(row["symbol"]),
                    "date": row.get("date"),
                    "snapshot_time_ms": int(row["snapshot_time_ms"]),
                    "sampling_stratum": row.get("sampling_stratum"),
                    "sampling_score": row.get("sampling_score"),
                    "activity_ratio": row.get("activity_ratio"),
                    "positive_move_20d": row.get("positive_move_20d"),
                    "range_expansion": row.get("range_expansion"),
                    "labeled": label is not None,
                    "status": label.get("status") if label else None,
                    "selected_tf": label.get("tf") if label else None,
                }
            )
        return {
            "candidates": rows,
            "available_tfs": list(BOUNDARY_TIMEFRAMES),
            "labels_path": str(self.labels_path),
            "manifest": manifest,
        }

    def candle_payload(self, event_id: str, tf: str) -> dict[str, Any] | None:
        candidate = self.candidate(event_id)
        if candidate is None:
            return None
        if tf not in BOUNDARY_TIMEFRAMES:
            raise ValueError("unsupported boundary timeframe")
        row = pd.Series(candidate)
        payload = self.ohlcv.event_payload(row, tf=tf)
        payload["label"] = self.labels.read_effective().get(event_id)
        payload["available_tfs"] = list(BOUNDARY_TIMEFRAMES)
        return payload

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = str(payload.get("event_id") or "")
        tf = str(payload.get("tf") or "")
        candidate = self.candidate(event_id)
        if candidate is None:
            raise ValueError("unknown boundary event_id")
        candle_payload = self.candle_payload(event_id, tf)
        assert candle_payload is not None
        return self.labels.append(
            payload,
            candidate=candidate,
            candles=candle_payload["candles"],
            allowed_tfs=set(BOUNDARY_TIMEFRAMES),
        )

    def unlabel(self, event_id: str) -> dict[str, Any]:
        return self.labels.append_unlabel(event_id, allowed_event_ids=self.event_ids())
