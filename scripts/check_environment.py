from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import platform
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Dependency:
    import_name: str
    package_name: str
    scope: str


RUNTIME_DEPENDENCIES: tuple[Dependency, ...] = (
    Dependency("pandas", "pandas", "runtime"),
    Dependency("polars", "polars", "runtime"),
    Dependency("pyarrow", "pyarrow", "runtime"),
    Dependency("numpy", "numpy", "runtime"),
    Dependency("sklearn", "scikit-learn", "runtime"),
    Dependency("catboost", "catboost", "runtime"),
    Dependency("requests", "requests", "runtime"),
    Dependency("tqdm", "tqdm", "runtime"),
)

TEST_DEPENDENCIES: tuple[Dependency, ...] = (
    Dependency("pytest", "pytest", "test"),
)

DEV_DEPENDENCIES: tuple[Dependency, ...] = (
    Dependency("ruff", "ruff", "dev"),
)


DEPENDENCIES_BY_SCOPE: dict[str, tuple[Dependency, ...]] = {
    "runtime": RUNTIME_DEPENDENCIES,
    "test": RUNTIME_DEPENDENCIES + TEST_DEPENDENCIES,
    "dev": RUNTIME_DEPENDENCIES + TEST_DEPENDENCIES + DEV_DEPENDENCIES,
}


def _dependency_version(package_name: str) -> str:
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return "UNKNOWN"


def check_dependencies(scope: str) -> list[str]:
    missing: list[str] = []
    for dep in DEPENDENCIES_BY_SCOPE[scope]:
        found = importlib.util.find_spec(dep.import_name) is not None
        status = "OK" if found else "MISSING"
        version = _dependency_version(dep.package_name) if found else "-"
        print(f"{status:7} {dep.scope:7} {dep.package_name:16} import={dep.import_name} version={version}")
        if not found:
            missing.append(dep.package_name)
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the local anomaly_science Python environment.")
    parser.add_argument(
        "--scope",
        choices=tuple(DEPENDENCIES_BY_SCOPE),
        default="runtime",
        help="Dependency scope to verify. Default: runtime.",
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Return exit code 0 even when dependencies are missing; useful for CI/bootstrap diagnostics.",
    )
    args = parser.parse_args(argv)

    print(f"python_executable={sys.executable}")
    print(f"python_version={platform.python_version()}")
    print(f"dependency_scope={args.scope}")

    if sys.version_info < (3, 10):
        print("ERROR python>=3.10 is required")
        return 0 if args.allow_missing else 1

    missing = check_dependencies(args.scope)
    if missing:
        print("missing_dependencies=" + ",".join(missing))
        return 0 if args.allow_missing else 1

    print("environment_status=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
