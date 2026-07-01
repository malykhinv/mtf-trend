from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

import pandas as pd

from anomaly_science.artifacts.manifest import build_manifest, sha256_file, write_manifest
from anomaly_science.strategy.pump_fade.builder import (
    PUMP_FADE_LABEL_SCHEMA_VERSION,
    PUMP_FADE_ONLINE_STATE_SCHEMA_VERSION,
    PumpFadeBuildError,
)


PUMP_FADE_STATE_LATTICE_SCHEMA_VERSION = "pump_fade_new_high_state_lattice_v1"
PUMP_FADE_STATE_LATTICE_ORDINALS = (1, 2, 3, 5, 8, 13)


def build_pump_fade_state_lattice_rows(supervised: pd.DataFrame) -> pd.DataFrame:
    """Select registered causal new-high states without future filtering."""
    required = {
        "online_state_schema_version", "label_schema_version", "group", "symbol",
        "snapshot_time_ms", "feature_cutoff_time_ms", "future_start_time_ms",
        "resolution_time_ms", "label_available", "y",
    }
    missing = sorted(required - set(supervised.columns))
    if missing:
        raise PumpFadeBuildError(f"supervised dataset is missing state-lattice inputs: {missing}")
    if supervised.empty:
        raise PumpFadeBuildError("supervised dataset is empty")
    online_schemas = set(supervised["online_state_schema_version"].dropna().astype(str).unique())
    label_schemas = set(supervised["label_schema_version"].dropna().astype(str).unique())
    if online_schemas != {PUMP_FADE_ONLINE_STATE_SCHEMA_VERSION}:
        raise PumpFadeBuildError(f"online state schema mismatch: {sorted(online_schemas)}")
    if label_schemas != {PUMP_FADE_LABEL_SCHEMA_VERSION}:
        raise PumpFadeBuildError(f"decision label schema mismatch: {sorted(label_schemas)}")
    work = supervised.sort_values(["group", "snapshot_time_ms"], kind="mergesort").copy()
    if work[["group", "snapshot_time_ms"]].duplicated().any():
        raise PumpFadeBuildError("supervised dataset contains duplicate group snapshots")
    work["state_ordinal"] = work.groupby("group", sort=False).cumcount() + 1
    work["state_lattice_schema_version"] = PUMP_FADE_STATE_LATTICE_SCHEMA_VERSION
    work["is_registered_state_lattice"] = work["state_ordinal"].isin(
        PUMP_FADE_STATE_LATTICE_ORDINALS
    )
    lattice = work.loc[work["is_registered_state_lattice"]].copy()
    if lattice.empty:
        raise PumpFadeBuildError("registered state lattice selected no rows")
    if (lattice["feature_cutoff_time_ms"] > lattice["snapshot_time_ms"]).any():
        raise PumpFadeBuildError("state-lattice feature cutoff exceeds snapshot time")
    available = lattice["label_available"].astype(bool)
    if (lattice.loc[available, "future_start_time_ms"] <= lattice.loc[available, "snapshot_time_ms"]).any():
        raise PumpFadeBuildError("state-lattice future must begin after snapshot")
    return lattice.sort_values(
        ["snapshot_time_ms", "symbol", "group"], kind="mergesort"
    ).reset_index(drop=True)


def run_pump_fade_state_lattice_projection(*, input_path: Path, output_path: Path) -> Path:
    supervised = pd.read_parquet(input_path)
    lattice = build_pump_fade_state_lattice_rows(supervised)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    lattice.to_parquet(temporary, index=False)
    os.replace(temporary, output_path)
    metadata_path = output_path.with_suffix(".metadata.json")
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path.resolve()),
        "input_sha256": sha256_file(input_path),
        "input_row_count": len(supervised),
        "input_event_count": int(supervised["group"].nunique()),
        "state_lattice_row_count": len(lattice),
        "state_lattice_event_count": int(lattice["group"].nunique()),
        "state_lattice_schema_version": PUMP_FADE_STATE_LATTICE_SCHEMA_VERSION,
        "registered_state_ordinals": list(PUMP_FADE_STATE_LATTICE_ORDINALS),
        "selection_contract": "event-local new-high ordinal known at snapshot; no label or future-outcome filter",
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    manifest = build_manifest(
        run_id="pump-fade-state-lattice-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        artifact_paths=(output_path, metadata_path),
        root=output_path.parent,
    )
    write_manifest(output_path.parent / f"{output_path.stem}.manifest.json", manifest)
    return output_path


__all__ = [
    "PUMP_FADE_STATE_LATTICE_ORDINALS",
    "PUMP_FADE_STATE_LATTICE_SCHEMA_VERSION",
    "build_pump_fade_state_lattice_rows",
    "run_pump_fade_state_lattice_projection",
]
