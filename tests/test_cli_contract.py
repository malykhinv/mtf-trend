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


def test_build_research_dataset_cli_has_explicit_phase_boundary() -> None:
    parser = build_parser()

    dataset_args = parser.parse_args([
        "build-research-dataset",
        "broad_anomaly_v1_h30",
        "--cache-dir",
        "cache",
        "--out",
        "dataset",
        "--days",
        "9",
        "--max-phase",
        "feature_matrix",
    ])

    assert dataset_args.command == "build-research-dataset"
    assert dataset_args.strategy == "broad_anomaly_v1_h30"
    assert dataset_args.cache_dir == "cache"
    assert dataset_args.out == "dataset"
    assert dataset_args.days == 9
    assert dataset_args.max_phase == "feature_matrix"

    research_args = parser.parse_args(["run-research", "broad_anomaly_v1_h30", "--days", "9"])
    assert research_args.command == "run-research"
    assert not hasattr(research_args, "max_phase")
