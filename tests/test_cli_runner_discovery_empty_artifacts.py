from pathlib import Path

import pandas as pd
import pytest

from cli.commands import _read_csv_or_empty
from cli.parser import build_parser


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


def test_runner_discovery_cli_exposes_only_days_flag() -> None:
    parser = build_parser()

    args = parser.parse_args(["run-htf-ltf-runner-discovery", "--days", "2"])

    assert args.command == "run-htf-ltf-runner-discovery"
    assert args.days == 2
    assert not hasattr(args, "targeted_ltf_backfill")
    assert not hasattr(args, "targeted_backfill_min_htf_quote_ratio")


def test_runner_discovery_cli_rejects_removed_tuning_flags() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["run-htf-ltf-runner-discovery", "--targeted-ltf-backfill", "false"])
