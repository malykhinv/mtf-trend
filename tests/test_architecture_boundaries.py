from __future__ import annotations

import ast
from pathlib import Path


SOURCE_ROOT = Path("src/anomaly_science")
COMPOSITION_ROOTS = {SOURCE_ROOT / "cli.py"}
CONCRETE_STRATEGY_PREFIXES = (
    "anomaly_science.strategy.anomaly",
    "anomaly_science.strategy.pump_fade",
    "anomaly_science.strategy.pump_long",
)
ALLOWED_CORE_STRATEGY_PORTS = {
    "anomaly_science.strategy.registry",
    "anomaly_science.strategy.metadata",
    "anomaly_science.strategy.defaults",
}
LEGACY_ARTIFACT_COMPATIBILITY_FILES = {
    SOURCE_ROOT / "contracts" / "artifacts.py",
    SOURCE_ROOT / "contracts" / "features.py",
    SOURCE_ROOT / "features" / "catalog.py",
    SOURCE_ROOT / "features" / "matrix.py",
}


def _imports(path: Path) -> tuple[tuple[int, str], ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    rows: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            rows.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            rows.extend((node.lineno, alias.name) for alias in node.names)
    return tuple(rows)


def test_core_does_not_import_concrete_strategy_modules() -> None:
    offenders: list[str] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        if "strategy" in path.relative_to(SOURCE_ROOT).parts or path in COMPOSITION_ROOTS:
            continue
        for line, module in _imports(path):
            if module.startswith(CONCRETE_STRATEGY_PREFIXES):
                offenders.append(f"{path}:{line}: imports concrete strategy module {module!r}")
    assert offenders == [], "\n".join(offenders)


def test_core_only_uses_registry_and_metadata_strategy_ports() -> None:
    offenders: list[str] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        if "strategy" in path.relative_to(SOURCE_ROOT).parts or path in COMPOSITION_ROOTS:
            continue
        for line, module in _imports(path):
            if module.startswith("anomaly_science.strategy") and module not in ALLOWED_CORE_STRATEGY_PORTS:
                offenders.append(f"{path}:{line}: imports non-port strategy module {module!r}")
    assert offenders == [], "\n".join(offenders)


def test_core_does_not_dispatch_on_strategy_name_or_family_strings() -> None:
    offenders: list[str] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        if "strategy" in path.relative_to(SOURCE_ROOT).parts or path in COMPOSITION_ROOTS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            receiver = node.func.value
            if (
                node.func.attr in {"startswith", "endswith"}
                and isinstance(receiver, ast.Name)
                and receiver.id in {"strategy_name", "strategy_family"}
            ):
                offenders.append(f"{path}:{node.lineno}: string-based strategy dispatch")
    assert offenders == [], "\n".join(offenders)


def test_strategy_execution_facade_contains_no_simulation_implementation() -> None:
    path = SOURCE_ROOT / "strategy" / "pump_long" / "execution.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    implementations = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    assert implementations == []


def test_core_feature_builder_contains_no_anomaly_geometry_implementation() -> None:
    path = SOURCE_ROOT / "features" / "matrix.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    assert "_relaxed_geometry_features" not in names
    assert "_RelaxedGeometryFeatures" not in names


def test_strategy_identifiers_do_not_leak_into_core() -> None:
    offenders: list[str] = []
    tokens = ("broad_anomaly", "post_anomaly", "post_pump", "pump_fade", "pump_long")
    for path in SOURCE_ROOT.rglob("*.py"):
        if "strategy" in path.relative_to(SOURCE_ROOT).parts or path in COMPOSITION_ROOTS:
            continue
        text = path.read_text(encoding="utf-8")
        for token in tokens:
            if token not in text:
                continue
            if token == "post_pump" and path in LEGACY_ARTIFACT_COMPATIBILITY_FILES:
                continue
            offenders.append(f"{path}: contains strategy identifier {token!r}")
    assert offenders == [], "\n".join(offenders)
