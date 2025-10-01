from datetime import datetime
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

openpyxl_module = types.ModuleType("openpyxl")
workbook_module = types.ModuleType("openpyxl.workbook")
defined_name_module = types.ModuleType("openpyxl.workbook.defined_name")


class Workbook:  # pragma: no cover - simple stub
    ...


def load_workbook(*_args, **_kwargs):  # pragma: no cover - simple stub
    raise NotImplementedError("Workbook access is not required for this test")


class DefinedName:  # pragma: no cover - simple stub
    def __init__(self, name: str, attr_text: str) -> None:
        self.name = name
        self.attr_text = attr_text


defined_name_module.DefinedName = DefinedName
workbook_module.defined_name = defined_name_module

utils_module = types.ModuleType("openpyxl.utils")
cell_module = types.ModuleType("openpyxl.utils.cell")


def get_column_letter(index: int) -> str:  # pragma: no cover - copied stub
    letters = []
    while index:
        index, remainder = divmod(index - 1, 26)
        letters.append(chr(ord("A") + remainder))
    return "".join(reversed(letters))


cell_module.get_column_letter = get_column_letter
utils_module.cell = cell_module

worksheet_module = types.ModuleType("openpyxl.worksheet")
worksheet_worksheet_module = types.ModuleType("openpyxl.worksheet.worksheet")


class Worksheet:  # pragma: no cover - simple stub
    ...


worksheet_worksheet_module.Worksheet = Worksheet
worksheet_module.worksheet = worksheet_worksheet_module

openpyxl_module.Workbook = Workbook
openpyxl_module.load_workbook = load_workbook
openpyxl_module.workbook = workbook_module
openpyxl_module.utils = utils_module
openpyxl_module.worksheet = worksheet_module

sys.modules.setdefault("openpyxl", openpyxl_module)
sys.modules.setdefault("openpyxl.workbook", workbook_module)
sys.modules.setdefault("openpyxl.workbook.defined_name", defined_name_module)
sys.modules.setdefault("openpyxl.utils", utils_module)
sys.modules.setdefault("openpyxl.utils.cell", cell_module)
sys.modules.setdefault("openpyxl.worksheet", worksheet_module)
sys.modules.setdefault("openpyxl.worksheet.worksheet", worksheet_worksheet_module)

from bot.domain.anomaly_optimizer import (
    AnomalySample,
    ThresholdCandidate,
    evaluate_candidate,
)
from pytest import approx
from bot.domain.models.bar import BarMetrics, BreakDirection
from bot.domain.models.exchange import Exchange
from bot.domain.models.timeframe import Timeframe


def _sample(*, break_direction: BreakDirection) -> AnomalySample:
    metrics = BarMetrics(
        pct_move=5.0,
        relative_volume=3.0,
        atr_mult=2.0,
        upper_wick_pct=1.0,
        body_pct=1.0,
        lower_wick_pct=1.0,
        pct_to_low_break=0.0,
        pct_to_high_break=0.0,
        break_direction=break_direction,
    )
    return AnomalySample(
        timestamp=datetime(2024, 1, 1),
        exchange=Exchange.BINANCE,
        symbol="BTCUSDT",
        timeframe=Timeframe.M15,
        bar_id="bar",
        open=100.0,
        high=110.0,
        low=95.0,
        close=100.0,
        volume=1_000.0,
        metrics=metrics,
    )


def _candidate() -> ThresholdCandidate:
    return ThresholdCandidate(
        min_green_move_pct=0.0,
        min_volume_spike=0.0,
        min_anomaly_relative_volume=0.0,
        min_relative_volume=1.0,
        max_relative_volume=10.0,
        min_anomaly_atr_mult=0.0,
        min_atr_mult=0.0,
        min_pct_move=0.0,
        max_pct_move=10.0,
        min_anomaly_upper_wick_pct=0.0,
        max_upper_wick_pct=10.0,
        max_lower_wick_pct=10.0,
        min_rr=0.0,
        initial_deposit=1_000.0,
        position_fraction=0.5,
    )


def test_evaluate_candidate_scales_returns_by_equity() -> None:
    samples = [_sample(break_direction=BreakDirection.HIGH_FIRST) for _ in range(2)]
    candidate = _candidate()

    evaluation = evaluate_candidate(samples, candidate)

    assert evaluation.executed_trades == 2
    assert evaluation.long_final_equity == approx(1_102.5)
    assert evaluation.score == approx(102.5)
    assert evaluation.average_trade_return_pct == approx(51.25)
