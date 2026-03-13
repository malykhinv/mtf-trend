"""Публичный API пакета ``vectorbt_runner`` с ленивыми импортами."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vectorbt_runner.backtest_runner import BacktestRunner
    from vectorbt_runner.backtest_summary import BacktestSummary
    from vectorbt_runner.data_preparer import DataPreparer
    from vectorbt_runner.mtf_frames import SymbolMtfFrames
    from vectorbt_runner.vectorbt_inputs import VectorbtInputs

__all__ = [
    "BacktestRunner",
    "BacktestSummary",
    "DataPreparer",
    "SymbolMtfFrames",
    "VectorbtInputs",
]

_MODULE_BY_NAME = {
    "BacktestRunner": "vectorbt_runner.backtest_runner",
    "BacktestSummary": "vectorbt_runner.backtest_summary",
    "DataPreparer": "vectorbt_runner.data_preparer",
    "SymbolMtfFrames": "vectorbt_runner.mtf_frames",
    "VectorbtInputs": "vectorbt_runner.vectorbt_inputs",
}


def __getattr__(name: str) -> Any:
    module_name = _MODULE_BY_NAME.get(name)
    if module_name is None:
        raise AttributeError(f"module 'vectorbt_runner' has no attribute {name!r}")
    module = import_module(module_name)
    return getattr(module, name)
