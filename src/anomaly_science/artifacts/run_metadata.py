from __future__ import annotations

import platform
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

from anomaly_science.contracts.audit import RunConfigRow


DEPENDENCY_NAMES = ("pandas", "polars", "pyarrow", "requests", "tqdm")


def runtime_reproducibility_rows() -> list[RunConfigRow]:
    return [
        RunConfigRow(key="git_commit", value=_git_commit(), source="runtime"),
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
