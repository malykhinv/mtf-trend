from __future__ import annotations

from anomaly_science.cli import build_parser


def test_low_level_cli_help_uses_canonical_strategy_artifacts(capsys) -> None:
    parser = build_parser()

    try:
        parser.parse_args(["run-mvp1-prediction", "--help"])
    except SystemExit as exc:
        assert exc.code == 0

    help_text = capsys.readouterr().out
    assert "strategy_state_1m.csv" in help_text
    assert "strategy_outcome_labels.csv" in help_text
    assert "strategy_feature_matrix.csv" in help_text
    assert "anomaly_state_1m.csv" not in help_text
    assert "anomaly_outcome_labels.csv" not in help_text
