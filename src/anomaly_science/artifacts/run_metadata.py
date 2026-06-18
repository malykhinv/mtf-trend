from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Mapping, Sequence

from anomaly_science.contracts.audit import RunConfigRow


DEPENDENCY_NAMES = ("pandas", "polars", "pyarrow", "requests", "tqdm")
INTERNAL_TIME_TYPE = "pl.Datetime[ms, UTC]"
MAX_FEATURE_LOOKBACK_MINUTES = 1440
TRIGGER_DEDUPLICATION_POLICY = "same_symbol_detection_time_within_strategy_horizon_suppressed"


def runtime_reproducibility_rows(
    *,
    data_paths: Sequence[Path | None] = (),
    config: object | None = None,
    extra_config: Mapping[str, object] | None = None,
) -> list[RunConfigRow]:
    return [
        RunConfigRow(key="git_commit", value=_git_commit(), source="runtime"),
        RunConfigRow(key="data_snapshot_hash", value=_data_snapshot_hash(data_paths), source="runtime"),
        RunConfigRow(key="config_hash", value=_config_hash(config=config, extra_config=extra_config), source="runtime"),
        RunConfigRow(key="internal_time_type", value=INTERNAL_TIME_TYPE, source="runtime"),
        RunConfigRow(key="max_feature_lookback_minutes", value=str(MAX_FEATURE_LOOKBACK_MINUTES), source="runtime"),
        RunConfigRow(key="trigger_deduplication_policy", value=TRIGGER_DEDUPLICATION_POLICY, source="runtime"),
        RunConfigRow(key="run_timestamp_utc", value=datetime.now(timezone.utc).isoformat(), source="runtime"),
        RunConfigRow(key="python_version", value=platform.python_version(), source="runtime"),
        RunConfigRow(key="python_executable", value=sys.executable, source="runtime"),
        RunConfigRow(key="dependency_versions", value=_dependency_versions(), source="runtime"),
    ]


def _git_commit() -> str:
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        reason = (result.stderr or result.stdout).strip().replace("\n", " ")
        return f"UNAVAILABLE:{reason or 'git rev-parse failed'}"
    return result.stdout.strip()


def _dependency_versions() -> str:
    pairs: list[str] = []
    for name in DEPENDENCY_NAMES:
        try:
            version = metadata.version(name)
        except metadata.PackageNotFoundError:
            version = "UNAVAILABLE:not_installed"
        pairs.append(f"{name}=={version}")
    return ";".join(pairs)


def _data_snapshot_hash(data_paths: Sequence[Path | None]) -> str:
    paths = tuple(path for path in data_paths if path is not None)
    if not paths:
        return "NO_INPUT_DATA"
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: str(item)):
        _hash_path(path=path, digest=digest)
    return digest.hexdigest()


def _hash_path(*, path: Path, digest: "hashlib._Hash") -> None:
    if not path.exists():
        raise FileNotFoundError(f"data snapshot path does not exist: {path}")
    resolved = path.resolve()
    if resolved.is_file():
        _hash_file(path=resolved, logical_name=resolved.name, digest=digest)
        return
    for file_path in sorted((item for item in resolved.rglob("*") if item.is_file()), key=lambda item: str(item.relative_to(resolved))):
        _hash_file(path=file_path, logical_name=f"{resolved.name}/{file_path.relative_to(resolved).as_posix()}", digest=digest)


def _hash_file(*, path: Path, logical_name: str, digest: "hashlib._Hash") -> None:
    digest.update(logical_name.encode("utf-8"))
    digest.update(b"\0")
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    digest.update(b"\0")


def _config_hash(*, config: object | None, extra_config: Mapping[str, object] | None) -> str:
    payload: dict[str, object] = {}
    if config is not None:
        if is_dataclass(config):
            payload["config"] = asdict(config)
        elif isinstance(config, Mapping):
            payload["config"] = dict(config)
        else:
            payload["config"] = repr(config)
    if extra_config:
        payload["extra_config"] = dict(extra_config)
    normalized = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
