from __future__ import annotations

import ast
from pathlib import Path


FORBIDDEN_ROOT_MODULES = {
    "cli",
    "config",
    "data",
    "domain",
    "legacy_quarantine",
    "research_tools",
    "short_project",
    "simulation",
    "strategy",
    "utils",
    "vectorbt_runner",
}


def _imported_roots(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.append(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.append(node.module.split(".", 1)[0])

    return roots


def test_anomaly_science_does_not_import_legacy() -> None:
    offenders: list[str] = []

    for path in Path("src/anomaly_science").rglob("*.py"):
        for root in _imported_roots(path):
            if root in FORBIDDEN_ROOT_MODULES:
                offenders.append(f"{path}: imports legacy root module '{root}'")

    assert not offenders, "\n".join(offenders)
