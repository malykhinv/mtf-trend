from __future__ import annotations

import subprocess
import sys


def test_main_doctor() -> None:
    result = subprocess.run(
        [sys.executable, "main.py", "doctor"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "anomaly_science bootstrap ok" in result.stdout
