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
    write_threshold_candidate,
)
from bot.data.diary import WorkbookDiaryBackend
import pytest
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


class _WorkbookStub:
    def __init__(self) -> None:
        self._sheet = _SheetStub()
        self.saved_path: Path | None = None
        self.closed = False

    @property
    def sheetnames(self) -> list[str]:
        return ["thresholds"]

    def __getitem__(self, key: str) -> "_SheetStub":
        if key != "thresholds":
            raise KeyError(key)
        return self._sheet

    def save(self, path: Path) -> None:
        self.saved_path = path

    def close(self) -> None:
        self.closed = True


class _SheetStub:
    def __init__(self) -> None:
        self._cells: dict[tuple[int, int], _CellStub] = {}

    def cell(self, row: int, column: int) -> "_CellStub":
        key = (row, column)
        if key not in self._cells:
            self._cells[key] = _CellStub()
        return self._cells[key]


class _CellStub:
    def __init__(self) -> None:
        self.value: float | None = None


def test_write_threshold_candidate_preserves_precision(monkeypatch, tmp_path) -> None:
    workbook = _WorkbookStub()
    monkeypatch.setattr(
        "bot.domain.anomaly_optimizer.load_workbook", lambda _path: workbook
    )

    workbook_path = tmp_path / "anomalies.xlsx"
    candidate = ThresholdCandidate(
        min_green_move_pct=3.456,
        min_volume_spike=7.891,
        min_anomaly_relative_volume=11.111,
        min_relative_volume=5.432,
        max_relative_volume=250.0,
        min_anomaly_atr_mult=2.345,
        min_atr_mult=3.876,
        min_pct_move=4.321,
        max_pct_move=34.789,
        min_anomaly_upper_wick_pct=6.789,
        max_upper_wick_pct=133.333,
        max_lower_wick_pct=83.333,
        min_rr=1.234,
        initial_deposit=1_234.56,
        position_fraction=0.333,
    )

    write_threshold_candidate(workbook_path, candidate)

    layout_index = {
        name: row_index
        for row_index, (_, name, _) in enumerate(
            WorkbookDiaryBackend._ANOMALY_THRESHOLD_LAYOUT, start=2
        )
    }
    sheet = workbook["thresholds"]

    # Baseline thresholds retain their original precision.
    baseline_row = layout_index["thresholds_min_green_move_pct"]
    assert sheet.cell(row=baseline_row, column=2).value == pytest.approx(
        candidate.min_green_move_pct
    )
    anomaly_volume_row = layout_index["thresholds_min_anomaly_relative_volume"]
    assert sheet.cell(row=anomaly_volume_row, column=2).value == pytest.approx(
        candidate.min_anomaly_relative_volume
    )

    # Adjustable thresholds are clamped and quantized to their grid metadata.
    min_rel_row = layout_index["thresholds_min_relative_volume"]
    assert sheet.cell(row=min_rel_row, column=2).value == pytest.approx(5.0)
    max_rel_row = layout_index["thresholds_max_relative_volume"]
    assert sheet.cell(row=max_rel_row, column=2).value == pytest.approx(200.0)
    max_upper_row = layout_index["thresholds_max_upper_wick_pct"]
    assert sheet.cell(row=max_upper_row, column=2).value == pytest.approx(100.0)
    max_lower_row = layout_index["thresholds_max_lower_wick_pct"]
    assert sheet.cell(row=max_lower_row, column=2).value == pytest.approx(80.0)
    min_rr_row = layout_index["thresholds_min_rr"]
    assert sheet.cell(row=min_rr_row, column=2).value == pytest.approx(1.0)

    # Non-grid fields continue to be quantized.
    deposit_row = layout_index["thresholds_initial_deposit"]
    assert sheet.cell(row=deposit_row, column=2).value == pytest.approx(1_234.6)

    assert workbook.saved_path == workbook_path
    assert workbook.closed
