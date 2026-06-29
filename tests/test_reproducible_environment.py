from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_reproducible_environment_entrypoint_files_exist() -> None:
    assert Path("requirements/test.txt").is_file()
    assert Path("requirements/dev.txt").is_file()
    assert Path("constraints/research-minimums.txt").is_file()
    assert Path("scripts/check_environment.py").is_file()


def test_environment_check_allow_missing_is_bootstrap_safe() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_environment.py", "--scope", "test", "--allow-missing"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "python_executable=" in result.stdout
    assert "dependency_scope=test" in result.stdout
