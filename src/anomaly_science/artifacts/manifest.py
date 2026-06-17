from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ArtifactManifestEntry:
    name: str
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ArtifactManifest:
    run_id: str
    created_at_utc: str
    artifacts: tuple[ArtifactManifestEntry, ...]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(*, run_id: str, artifact_paths: list[Path], root: Path) -> ArtifactManifest:
    entries = []
    for path in sorted(artifact_paths, key=lambda item: item.name):
        entries.append(
            ArtifactManifestEntry(
                name=path.name,
                path=str(path.relative_to(root)),
                sha256=sha256_file(path),
                size_bytes=path.stat().st_size,
            )
        )
    return ArtifactManifest(
        run_id=run_id,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
        artifacts=tuple(entries),
    )


def write_manifest(path: Path, manifest: ArtifactManifest) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(asdict(manifest), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)
    return path
