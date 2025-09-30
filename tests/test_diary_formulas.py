from datetime import datetime
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_openpyxl_stub() -> None:
    workbook_module = types.ModuleType("openpyxl.workbook")
    defined_name_module = types.ModuleType("openpyxl.workbook.defined_name")

    class DefinedName:  # pragma: no cover - simple stub
        def __init__(self, name: str, attr_text: str) -> None:
            self.name = name
            self.attr_text = attr_text

    defined_name_module.DefinedName = DefinedName
    workbook_module.defined_name = defined_name_module

    utils_module = types.ModuleType("openpyxl.utils")
    cell_module = types.ModuleType("openpyxl.utils.cell")

    def get_column_letter(index: int) -> str:  # pragma: no cover - stub not used in tests
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

    openpyxl_module = types.ModuleType("openpyxl")

    class Workbook:  # pragma: no cover - simple stub
        ...

    def load_workbook(path: Path):  # pragma: no cover - simple stub
        raise NotImplementedError("load_workbook is not available in the test stub")

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


_install_openpyxl_stub()

from bot.data.diary import (
    AnomalyRow,
    WorkbookDiaryBackend,
)
from bot.domain.models.anomaly import AnomalyThresholdSnapshot
from bot.domain.models.bar import BarMetrics, BreakDirection
from bot.domain.models.exchange import Exchange
from bot.domain.models.timeframe import Timeframe


def _dummy_metrics() -> BarMetrics:
    return BarMetrics(
        pct_move=1.0,
        relative_volume=2.0,
        atr_mult=3.0,
        upper_wick_pct=4.0,
        body_pct=5.0,
        lower_wick_pct=6.0,
        pct_to_low_break=7.0,
        pct_to_high_break=8.0,
        break_direction=BreakDirection.NONE,
    )


def _dummy_thresholds() -> AnomalyThresholdSnapshot:
    return AnomalyThresholdSnapshot(
        min_green_move_pct=0.0,
        min_volume_spike=0.0,
        min_relative_volume=0.0,
        min_atr_mult=0.0,
        min_upper_wick_pct=0.0,
    )


class DummyWorkbookDiaryBackend(WorkbookDiaryBackend):
    def __init__(self) -> None:  # pragma: no cover - avoids heavy workbook setup
        pass


def test_anomaly_threshold_columns_reference_expected_cells(tmp_path: Path) -> None:
    backend = DummyWorkbookDiaryBackend()
    metrics = _dummy_metrics()
    thresholds = _dummy_thresholds()
    row = AnomalyRow(
        timestamp=datetime(2024, 1, 1),
        exchange=Exchange.BINANCE,
        symbol="BTCUSDT",
        timeframe=Timeframe.M15,
        bar_id="bar-id",
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=100.0,
        metrics=metrics,
        thresholds=thresholds,
    )

    values = backend._anomaly_values(row, row_index=2)
    header_to_value = dict(zip(backend._ANOMALIES_HEADERS, values))
    threshold_cells = backend._ANOMALY_THRESHOLD_CELL_MAP

    for header, cell in threshold_cells.items():
        if header in backend._ANOMALIES_HEADERS:
            assert (
                header_to_value[header] == f"={cell}"
            ), f"Header {header} should reference {cell}"


class _DummyCell:
    def __init__(self, row: int, column: int) -> None:
        self.row = row
        self._column = column
        self.value = None

    @property
    def column_letter(self) -> str:
        column = self._column
        letters = []
        while column:
            column, remainder = divmod(column - 1, 26)
            letters.append(chr(ord("A") + remainder))
        return "".join(reversed(letters))


class _DummySheet:
    def __init__(self, title: str) -> None:
        self.title = title
        self._cells: dict[tuple[int, int], _DummyCell] = {}

    def _column_index_from_letter(self, letters: str) -> int:
        index = 0
        for letter in letters.upper():
            index = index * 26 + (ord(letter) - ord("A") + 1)
        return index

    def cell(self, row: int, column: int) -> _DummyCell:
        key = (row, column)
        if key not in self._cells:
            self._cells[key] = _DummyCell(row, column)
        return self._cells[key]

    def __getitem__(self, key: str) -> _DummyCell:
        letters = "".join(filter(str.isalpha, key))
        numbers = "".join(filter(str.isdigit, key))
        return self.cell(row=int(numbers), column=self._column_index_from_letter(letters))


class _DummyWorkbook:
    def __init__(self) -> None:
        self._sheets: dict[str, _DummySheet] = {}
        self.defined_names: dict[str, object] = {}

    @property
    def sheetnames(self) -> list[str]:
        return list(self._sheets.keys())

    def __getitem__(self, key: str) -> _DummySheet:
        return self._sheets[key]

    def create_sheet(self, title: str) -> _DummySheet:
        sheet = _DummySheet(title)
        self._sheets[title] = sheet
        return sheet


def _column_letter_from_index(index: int) -> str:
    letters = []
    while index:
        index, remainder = divmod(index - 1, 26)
        letters.append(chr(ord("A") + remainder))
    return "".join(reversed(letters))


def test_anomaly_threshold_summary_formulas_cover_full_column() -> None:
    backend = DummyWorkbookDiaryBackend()
    workbook = _DummyWorkbook()

    backend._ensure_anomaly_threshold_sheet(workbook)

    sheet = workbook[backend._ANOMALY_THRESHOLD_SHEET_NAME]
    summary_row_start = len(backend._ANOMALY_THRESHOLD_LAYOUT) + 2

    long_column_letter = _column_letter_from_index(
        backend._ANOMALIES_HEADERS.index("long_equity_pct") + 1
    )
    short_column_letter = _column_letter_from_index(
        backend._ANOMALIES_HEADERS.index("short_equity_pct") + 1
    )

    long_value_cell = sheet.cell(row=summary_row_start, column=2)
    short_value_cell = sheet.cell(row=summary_row_start + 1, column=2)

    assert long_value_cell.value == (
        f"=AVERAGE(anomalies!${long_column_letter}$2:${long_column_letter}$1048576)"
    )
    assert short_value_cell.value == (
        f"=AVERAGE(anomalies!${short_column_letter}$2:${short_column_letter}$1048576)"
    )
