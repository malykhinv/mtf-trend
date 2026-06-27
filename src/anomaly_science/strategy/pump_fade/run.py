from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from anomaly_science.artifacts.manifest import build_manifest, write_manifest
from anomaly_science.strategy.pump_fade.builder import build_pump_fade_decisions
from anomaly_science.strategy.pump_fade.config import PumpFadeDecisionConfig


def run_pump_fade_dataset_build(
    *,
    cache_dir: Path,
    output_path: Path,
    config: PumpFadeDecisionConfig | None = None,
    limit_symbols: int | None = None,
    progress_every: int = 10,
) -> Path:
    config = config or PumpFadeDecisionConfig()
    def report(done: int, total: int, symbol: str, rows: int) -> None:
        if progress_every > 0 and (done % progress_every == 0 or done == total):
            print(
                f"pump-fade dataset: {done}/{total} symbols; last={symbol}; rows={rows}",
                flush=True,
            )

    frame = build_pump_fade_decisions(
        cache_dir=cache_dir,
        config=config,
        limit_symbols=limit_symbols,
        progress_callback=report,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False)
    os.replace(temporary, output_path)
    metadata_path = output_path.with_suffix(".metadata.json")
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "cache_dir": str(cache_dir.resolve()),
        "limit_symbols": limit_symbols,
        "row_count": len(frame),
        "event_count": int(frame["event_id"].nunique()) if not frame.empty else 0,
        "resolved_row_count": int(frame["label_available"].sum()) if not frame.empty else 0,
        "config": asdict(config),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    manifest = build_manifest(
        run_id=f"pump-fade-dataset-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        artifact_paths=[output_path, metadata_path],
        root=output_path.parent,
    )
    write_manifest(output_path.parent / f"{output_path.stem}.manifest.json", manifest)
    return output_path
