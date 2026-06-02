from pathlib import Path

import pandas as pd

from cli.commands import _read_csv_or_empty


def test_read_csv_or_empty_treats_bom_only_file_as_empty_frame(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_bytes(b"\xef\xbb\xbf")

    frame = _read_csv_or_empty(path)

    assert isinstance(frame, pd.DataFrame)
    assert frame.empty


def test_read_csv_or_empty_reads_normal_csv(tmp_path: Path) -> None:
    path = tmp_path / "trades.csv"
    path.write_text("status,net_return\nclosed,0.01\n", encoding="utf-8")

    frame = _read_csv_or_empty(path)

    assert frame.to_dict("records") == [{"status": "closed", "net_return": 0.01}]
