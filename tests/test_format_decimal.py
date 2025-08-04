import logging
import pathlib
import sys

import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

from utils.telegram import format_decimal


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_format_decimal_non_finite_returns_nan(value, caplog):
    with caplog.at_level(logging.ERROR):
        result = format_decimal(value)
    assert result == "NaN"
    assert "Non-finite decimal value" in caplog.text
