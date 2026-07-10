"""Strict JSON helpers for level-desk artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def read_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"expected JSON list: {path}")
    rows: list[dict[str, Any]] = []
    for i, row in enumerate(data):
        if not isinstance(row, dict):
            raise ValueError(f"expected JSON object at {path}[{i}]")
        rows.append(row)
    return rows


def dumps_json_line(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"


def iter_jsonl_objects(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL row {path}:{line_no}: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"expected JSON object at {path}:{line_no}")
        rows.append(row)
    return rows


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(dumps_json_line(row))
