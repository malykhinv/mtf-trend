from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.artifacts.manifest import build_manifest, sha256_file, write_manifest
from anomaly_science.strategy.pump_fade.builder import (
    PUMP_FADE_LABEL_SCHEMA_VERSION,
    PUMP_FADE_NATURE_LABEL_SCHEMA_VERSION,
    PumpFadeBuildError,
)


_REQUIRED_COLUMNS = frozenset(
    {
        "label_schema_version",
        "event_id",
        "group",
        "symbol",
        "snapshot_time_ms",
        "feature_cutoff_time_ms",
        "future_start_time_ms",
        "resolution_time_ms",
        "label_available",
        "y",
        "anchor_high",
    }
)


def build_pump_fade_nature_rows(decisions: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(_REQUIRED_COLUMNS - set(decisions.columns))
    if missing:
        raise PumpFadeBuildError(f"decision dataset is missing nature inputs: {missing}")
    if decisions.empty:
        raise PumpFadeBuildError("decision dataset is empty")
    schemas = set(decisions["label_schema_version"].dropna().astype(str).unique())
    if schemas != {PUMP_FADE_LABEL_SCHEMA_VERSION}:
        raise PumpFadeBuildError(
            f"decision label schema mismatch: observed {sorted(schemas)}"
        )
    work = decisions.sort_values(
        ["snapshot_time_ms", "group"], kind="mergesort"
    ).reset_index(drop=True)
    if work[["group", "snapshot_time_ms"]].duplicated().any():
        raise PumpFadeBuildError("decision dataset contains duplicate group snapshots")
    cutoff = pd.to_numeric(work["feature_cutoff_time_ms"], errors="raise")
    snapshot = pd.to_numeric(work["snapshot_time_ms"], errors="raise")
    if (cutoff > snapshot).any():
        raise PumpFadeBuildError("decision feature cutoff exceeds snapshot time")
    high_delta = work.groupby("group", sort=False)["anchor_high"].diff()
    if (high_delta.dropna() <= 0.0).any():
        raise PumpFadeBuildError("decision anchor highs must increase strictly within an event")

    first = work.drop_duplicates("group", keep="first").copy()
    last = work.drop_duplicates("group", keep="last").set_index("group")
    group = first["group"]
    last_available = group.map(last["label_available"]).astype(bool)
    last_y = pd.to_numeric(group.map(last["y"]), errors="coerce")
    last_future_start = pd.to_numeric(
        group.map(last["future_start_time_ms"]), errors="coerce"
    )
    last_resolution = pd.to_numeric(
        group.map(last["resolution_time_ms"]), errors="coerce"
    )
    valid = last_available & last_y.notna() & last_resolution.notna()
    first["nature_label_schema_version"] = PUMP_FADE_NATURE_LABEL_SCHEMA_VERSION
    first["is_nature_anchor"] = True
    first["nature_label_available"] = valid.to_numpy()
    first["nature_y"] = last_y.where(valid).astype("Int8").to_numpy()
    first["nature_future_start_time_ms"] = last_future_start.astype("Int64").to_numpy()
    first["nature_resolution_time_ms"] = last_resolution.where(valid).astype("Int64").to_numpy()
    first["event_peak_time_ms"] = pd.to_numeric(
        group.map(last["snapshot_time_ms"]), errors="raise"
    ).astype("int64").to_numpy()
    first["is_event_peak_decision"] = (
        first["snapshot_time_ms"].to_numpy(dtype=np.int64)
        == first["event_peak_time_ms"].to_numpy(dtype=np.int64)
    )
    available = first["nature_label_available"].to_numpy(dtype=bool)
    if np.any(
        first.loc[available, "nature_future_start_time_ms"].to_numpy(dtype=np.int64)
        <= first.loc[available, "snapshot_time_ms"].to_numpy(dtype=np.int64)
    ):
        raise PumpFadeBuildError("nature future must begin after the causal anchor")
    if np.any(
        first.loc[available, "nature_resolution_time_ms"].to_numpy(dtype=np.int64)
        < first.loc[available, "nature_future_start_time_ms"].to_numpy(dtype=np.int64)
    ):
        raise PumpFadeBuildError("nature resolution precedes nature future start")
    return first.reset_index(drop=True)


def run_pump_fade_nature_projection(*, input_path: Path, output_path: Path) -> Path:
    decisions = pd.read_parquet(input_path)
    nature = build_pump_fade_nature_rows(decisions)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    nature.to_parquet(temporary, index=False)
    os.replace(temporary, output_path)
    metadata_path = output_path.with_suffix(".metadata.json")
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path.resolve()),
        "input_sha256": sha256_file(input_path),
        "input_decision_row_count": len(decisions),
        "nature_event_row_count": len(nature),
        "available_nature_label_count": int(nature["nature_label_available"].sum()),
        "decision_label_schema": PUMP_FADE_LABEL_SCHEMA_VERSION,
        "nature_label_schema": PUMP_FADE_NATURE_LABEL_SCHEMA_VERSION,
        "projection_contract": (
            "features=first_causal_decision_per_event;"
            "label=last_new_high_close_race_per_event"
        ),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    manifest = build_manifest(
        run_id=f"pump-fade-nature-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        artifact_paths=[output_path, metadata_path],
        root=output_path.parent,
    )
    write_manifest(output_path.parent / f"{output_path.stem}.manifest.json", manifest)
    return output_path


__all__ = ["build_pump_fade_nature_rows", "run_pump_fade_nature_projection"]
