import logging
from pathlib import Path
import sys

from joblib import load
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

import ai.parameter_optimizer as po


def test_load_thresholds_creates_defaults(tmp_path, caplog):
    """Когда файл порогов отсутствует, создаётся файл с дефолтами."""

    defaults = {"a": 1.0}
    path = tmp_path / "optimized_thresholds.joblib"

    with caplog.at_level(logging.INFO):
        result = po.load_thresholds(defaults, path)

    assert result == defaults
    assert path.exists()
    assert load(path) == defaults
    assert "Generated defaults and saved" in caplog.text

