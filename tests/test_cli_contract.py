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


def test_cli_does_not_expose_catboost_thread_count_overrides(capsys) -> None:
    parser = build_parser()

    for command in (
        "run-mvp1-prediction",
        "run-archetype-discovery",
        "run-pump-fade-oi-incremental",
        "run-binary-weekly-probability",
    ):
        try:
            parser.parse_args([command, "--help"])
        except SystemExit as exc:
            assert exc.code == 0
        help_text = capsys.readouterr().out
        assert "--catboost-thread-count" not in help_text


def test_causal_regime_atlas_cli_requires_explicit_frozen_protocol() -> None:
    args = build_parser().parse_args(
        [
            "run-causal-regime-atlas",
            "--input",
            "nature.parquet",
            "--config",
            "regimes.json",
            "--out",
            "atlas",
        ]
    )

    assert args.command == "run-causal-regime-atlas"
    assert args.input == "nature.parquet"
    assert args.config == "regimes.json"
    assert args.out == "atlas"
    assert args.allow_dirty_development is False


def test_binary_weekly_probability_cli_requires_explicit_frozen_protocol() -> None:
    args = build_parser().parse_args(
        [
            "run-binary-weekly-probability",
            "--input",
            "nature.parquet",
            "--config",
            "probability.json",
            "--out",
            "probability",
        ]
    )

    assert args.command == "run-binary-weekly-probability"
    assert args.input == "nature.parquet"
    assert args.config == "probability.json"
    assert args.out == "probability"
    assert args.allow_dirty_development is False


def test_pump_fade_state_probability_cli_requires_family_protocol() -> None:
    args = build_parser().parse_args(
        [
            "run-pump-fade-state-probability",
            "--input", "state_lattice.parquet",
            "--config", "state_probability.json",
            "--out", "state_probability",
        ]
    )

    assert args.command == "run-pump-fade-state-probability"
    assert args.allow_dirty_development is False


def test_pump_fade_event_memory_and_interaction_commands_require_protocols() -> None:
    parser = build_parser()
    for command in (
        "run-pump-fade-event-memory-probability",
        "run-pump-fade-interaction-atlas-family",
    ):
        args = parser.parse_args(
            [
                command,
                "--nature", "nature.parquet",
                "--state-lattice", "state.parquet",
                "--config", "protocol.json",
                "--out", "result",
            ]
        )
        assert args.command == command
        assert args.allow_dirty_development is False


def test_event_scoped_symbol_positioning_cli_is_explicit_and_resumable() -> None:
    parser = build_parser()
    archive = parser.parse_args(
        [
            "build-event-scoped-symbol-metrics",
            "--nature", "nature.parquet",
            "--state-lattice", "state.parquet",
            "--out", "metrics",
            "--resume",
        ]
    )
    attach = parser.parse_args(
        [
            "build-pump-fade-symbol-positioning-context",
            "--input", "nature.parquet",
            "--metrics-dir", "metrics",
            "--out", "nature.symbol-positioning.parquet",
        ]
    )

    assert archive.resume is True
    assert archive.workers == 16
    assert attach.command == "build-pump-fade-symbol-positioning-context"


def test_drawdown_ladder_stage0_cli_exposes_bounded_is_build() -> None:
    args = build_parser().parse_args(
        [
            "build-drawdown-ladder-stage0",
            "--source-dir",
            "cache",
            "--out",
            "stage0",
            "--workers",
            "6",
            "--max-inflight-symbols",
            "12",
            "--limit-symbols",
            "30",
        ]
    )

    assert args.command == "build-drawdown-ladder-stage0"
    assert args.source_dir == "cache"
    assert args.out == "stage0"
    assert args.workers == 6
    assert args.max_inflight_symbols == 12
    assert args.limit_symbols == 30


def test_drawdown_structural_ev_cli_separates_build_from_analysis() -> None:
    parser = build_parser()
    build = parser.parse_args(
        [
            "build-drawdown-structural-ev",
            "--structural",
            "probability",
            "--stage1",
            "stage1.parquet",
            "--outcomes",
            "outcomes.parquet",
            "--out",
            "ev",
        ]
    )
    analyze = parser.parse_args(
        ["analyze-drawdown-structural-ev", "--ev-dir", "ev"]
    )

    assert build.structural == "probability"
    assert build.stage1 == "stage1.parquet"
    assert build.outcomes == "outcomes.parquet"
    assert analyze.ev_dir == "ev"


def test_mirrored_rally_stage0_cli_exposes_same_bounded_is_contract() -> None:
    args = build_parser().parse_args(
        [
            "build-mirrored-rally-stage0",
            "--source-dir",
            "cache",
            "--out",
            "mirror",
            "--workers",
            "5",
            "--max-inflight-symbols",
            "10",
            "--limit-symbols",
            "20",
        ]
    )

    assert args.command == "build-mirrored-rally-stage0"
    assert args.source_dir == "cache"
    assert args.out == "mirror"
    assert args.workers == 5
    assert args.max_inflight_symbols == 10
    assert args.limit_symbols == 20


def test_drawdown_mirror_comparison_cli_keeps_frozen_statistics() -> None:
    args = build_parser().parse_args(
        [
            "compare-drawdown-mirror-stage0",
            "--long",
            "long-stage0",
            "--mirror",
            "short-stage0",
            "--out",
            "comparison",
        ]
    )

    assert args.command == "compare-drawdown-mirror-stage0"
    assert args.long == "long-stage0"
    assert args.mirror == "short-stage0"
    assert args.out == "comparison"


def test_prior_non_drawdown_control_cli_is_bounded_and_is_only() -> None:
    args = build_parser().parse_args(
        [
            "build-drawdown-prior-control",
            "--source-dir",
            "cache",
            "--long-stage0",
            "long",
            "--out",
            "controls",
            "--workers",
            "6",
            "--max-inflight-symbols",
            "12",
            "--limit-symbols",
            "10",
        ]
    )

    assert args.command == "build-drawdown-prior-control"
    assert args.source_dir == "cache"
    assert args.long_stage0 == "long"
    assert args.out == "controls"
    assert args.workers == 6
    assert args.max_inflight_symbols == 12
    assert args.limit_symbols == 10


def test_prior_control_comparison_cli_has_no_tunable_statistics() -> None:
    args = build_parser().parse_args(
        [
            "compare-drawdown-prior-control",
            "--long",
            "long",
            "--control",
            "control",
            "--out",
            "result",
        ]
    )

    assert args.command == "compare-drawdown-prior-control"
    assert args.long == "long"
    assert args.control == "control"
    assert args.out == "result"
