"""Local annotation iteration runner.

The runner is intentionally infrastructure-only: it summarizes annotation
state, records reproducibility metadata, and optionally calls strategy-owned
diagnostics.  It does not implement strategy rules and it does not call any
external AI/API service.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from anomaly_science.annotation.app import AnnotationStrategyApp, choose_app, default_annotation_apps
from anomaly_science.annotation.desk.candidates import DEFAULT_TF_MINUTES, build_annotation_groups, validate_candidates
from anomaly_science.annotation.desk.labels import LabelStore, label_for_group


DEFAULT_ITERATION_ROOT = Path(".output") / "results" / "annotation_iterations"


@dataclass(frozen=True, slots=True)
class AnnotationIterationConfig:
    project_root: Path
    strategy_id: str | None = None
    out_root: Path = DEFAULT_ITERATION_ROOT
    run_trade_report: bool = True


@dataclass(frozen=True, slots=True)
class IterationPaths:
    run_dir: Path
    manifest: Path
    candidate_summary: Path
    label_progress: Path
    report_md: Path
    latest_pointer: Path


@dataclass(frozen=True, slots=True)
class StrategyIterationHook:
    strategy_id: str
    run: Callable[[Path], dict[str, Any]]


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if pd.isna(value):
        return None
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(payload), indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    return _sha256_bytes(path.read_bytes())


def _git_value(project_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip()
    return value or None


def _git_dirty(project_root: Path) -> bool | None:
    status = _git_value(project_root, "status", "--porcelain")
    if status is None:
        return None
    return bool(status)


def _stable_config_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(_jsonable(payload), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _sha256_bytes(encoded)


def _label_setups(label: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a label's setups, treating a legacy flat label as one setup."""

    setups = label.get("setups")
    if isinstance(setups, list) and setups:
        return [s for s in setups if isinstance(s, dict)]
    return [label]


def _setup_has_pump_transition(setup: dict[str, Any]) -> bool:
    if "has_pump_transition" in setup:
        return bool(setup.get("has_pump_transition"))
    return all(
        setup.get(key) is not None
        for key in ("pump_start_ms", "pump_start_price", "culmination_ms", "culmination_price")
    )


def _counter_payload(counter: Counter[str]) -> dict[str, int]:
    return {key: int(value) for key, value in sorted(counter.items())}


def summarize_annotation_state(app: AnnotationStrategyApp) -> tuple[dict[str, Any], dict[str, Any]]:
    candidates = pd.read_parquet(app.candidates_path)
    validate_candidates(candidates)
    groups = build_annotation_groups(candidates, DEFAULT_TF_MINUTES)
    labels = LabelStore(app.labels_path).read_effective()

    labeled_groups = 0
    no_setup_groups = 0
    has_level_groups = 0
    no_level_groups = 0
    no_transition_groups = 0
    has_pump_transition_groups = 0
    setup_total = 0
    family = Counter[str]()
    quality = Counter[str]()
    labeled_tfs = Counter[str]()
    source_label_hits = 0

    for group in groups:
        label = label_for_group(group, labels)
        if label is None:
            continue
        labeled_groups += 1
        if str(label.get("event_id")) != str(group["event_id"]):
            source_label_hits += 1
        # Explicit "reviewed, no setup" negative carries no setups to tally.
        if label.get("no_setup"):
            no_setup_groups += 1
            continue
        setups = _label_setups(label)
        setup_total += len(setups)
        if any(bool(s.get("has_level")) for s in setups):
            has_level_groups += 1
        else:
            no_level_groups += 1
        if any(_setup_has_pump_transition(setup) for setup in setups):
            has_pump_transition_groups += 1
        else:
            no_transition_groups += 1
        for setup in setups:
            family.update([str(setup.get("family") or "unknown")])
            quality.update([str(setup.get("quality") or "unknown")])
        labeled_tfs.update([str(label.get("selected_tf") or label.get("tf") or "unknown")])

    candidate_summary = {
        "strategy_id": app.strategy_id,
        "candidate_rows": int(len(candidates)),
        "candidate_groups": int(len(groups)),
        "symbols": int(candidates["symbol"].nunique()) if "symbol" in candidates else None,
        "tf_rows": _counter_payload(Counter(str(tf) for tf in candidates.get("tf", pd.Series(dtype=str)).tolist())),
        "first_review_start_ms": int(candidates["review_start_ms"].min()) if len(candidates) else None,
        "last_review_end_ms": int(candidates["review_end_ms"].max()) if len(candidates) else None,
        "candidates_path": app.candidates_path,
        "labels_path": app.labels_path,
        "cache_dir": app.cache_dir,
    }
    label_progress = {
        "strategy_id": app.strategy_id,
        "effective_label_rows": int(len(labels)),
        "labeled_groups": int(labeled_groups),
        "no_setup_groups": int(no_setup_groups),
        "unlabeled_groups": int(len(groups) - labeled_groups),
        "label_coverage_pct": float(100 * labeled_groups / len(groups)) if groups else 0.0,
        "has_level_groups": int(has_level_groups),
        "no_level_groups": int(no_level_groups),
        "has_pump_transition_groups": int(has_pump_transition_groups),
        "no_transition_groups": int(no_transition_groups),
        "labeled_setups": int(setup_total),
        "source_event_label_hits": int(source_label_hits),
        "family": _counter_payload(family),
        "quality": _counter_payload(quality),
        "labeled_tfs": _counter_payload(labeled_tfs),
    }
    return candidate_summary, label_progress


def default_iteration_hooks() -> dict[str, StrategyIterationHook]:
    from anomaly_science.strategy.registry import annotation_iteration_hooks

    return {
        strategy_id: StrategyIterationHook(strategy_id=strategy_id, run=hook)
        for strategy_id, hook in annotation_iteration_hooks().items()
    }


def _paths(out_root: Path, strategy_id: str, run_id: str) -> IterationPaths:
    run_dir = out_root / strategy_id / run_id
    return IterationPaths(
        run_dir=run_dir,
        manifest=run_dir / "iteration_manifest.json",
        candidate_summary=run_dir / "candidate_summary.json",
        label_progress=run_dir / "label_progress.json",
        report_md=run_dir / "iteration_report.md",
        latest_pointer=out_root / strategy_id / "latest.json",
    )


def _render_report(
    *,
    app: AnnotationStrategyApp,
    run_id: str,
    candidate_summary: dict[str, Any],
    label_progress: dict[str, Any],
    trade_report: dict[str, Any] | None,
) -> str:
    lines = [
        f"# Annotation iteration: {app.strategy_id}",
        "",
        f"Run: `{run_id}`",
        "",
        "## Label progress",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Candidate groups | {candidate_summary['candidate_groups']} |",
        f"| Candidate rows | {candidate_summary['candidate_rows']} |",
        f"| Labeled groups | {label_progress['labeled_groups']} |",
        f"| Reviewed, no setup | {label_progress.get('no_setup_groups', 0)} |",
        f"| Unlabeled groups | {label_progress['unlabeled_groups']} |",
        f"| Coverage | {label_progress['label_coverage_pct']:.1f}% |",
        f"| Has level | {label_progress['has_level_groups']} |",
        f"| No level | {label_progress['no_level_groups']} |",
        f"| Has pump transition | {label_progress['has_pump_transition_groups']} |",
        f"| No transition | {label_progress['no_transition_groups']} |",
        "",
        "## Label distributions",
        "",
        f"- Family: `{label_progress['family']}`",
        f"- Quality: `{label_progress['quality']}`",
        f"- TF: `{label_progress['labeled_tfs']}`",
    ]
    if trade_report is not None:
        base = trade_report.get("combined_portfolio", {})
        population = trade_report.get("population", {})
        lines += [
            "",
            "## Current strategy trade report",
            "",
            "| Metric | Value |",
            "|---|---:|",
            f"| Raw detector candidates | {population.get('raw_detector_candidates')} |",
            f"| Forward-scored candidates | {population.get('forward_scored_candidates')} |",
            f"| Selected before portfolio | {population.get('selected_before_portfolio')} |",
            f"| Taken trades | {base.get('trades')} |",
            f"| Not opened | {population.get('portfolio_not_opened')} |",
            f"| Return | {base.get('return_pct', 0):+.1f}% |",
            f"| Max DD | {base.get('max_drawdown_pct', 0):.1f}% |",
            f"| Win rate | {base.get('win_rate_pct', 0):.1f}% |",
            f"| Trading days | {base.get('trading_days')} |",
            f"| Positive days | {base.get('positive_days')} |",
        ]
    return "\n".join(lines) + "\n"


def run_annotation_iteration(
    config: AnnotationIterationConfig,
    *,
    hooks: dict[str, StrategyIterationHook] | None = None,
) -> IterationPaths:
    project_root = config.project_root.resolve()
    apps = default_annotation_apps(project_root)
    app = choose_app(apps, config.strategy_id)
    out_root = (project_root / config.out_root).resolve() if not config.out_root.is_absolute() else config.out_root

    config_payload = {
        "project_root": project_root,
        "strategy_id": app.strategy_id,
        "run_trade_report": config.run_trade_report,
        "candidates_path": app.candidates_path,
        "labels_path": app.labels_path,
        "cache_dir": app.cache_dir,
    }
    config_hash = _stable_config_hash(config_payload)
    started_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{started_at}_{config_hash[:10]}"
    paths = _paths(out_root, app.strategy_id, run_id)

    candidate_summary, label_progress = summarize_annotation_state(app)
    trade_report: dict[str, Any] | None = None
    active_hooks = hooks if hooks is not None else default_iteration_hooks()
    hook = active_hooks.get(app.strategy_id)
    if config.run_trade_report and hook is not None:
        trade_report = hook.run(paths.run_dir)

    manifest = {
        "run_id": run_id,
        "created_at_utc": started_at,
        "strategy": asdict(app),
        "config_hash": config_hash,
        "git_commit": _git_value(project_root, "rev-parse", "HEAD"),
        "git_dirty": _git_dirty(project_root),
        "input_hashes": {
            "candidates_sha256": _sha256_file(app.candidates_path),
            "labels_sha256": _sha256_file(app.labels_path),
        },
        "artifacts": {
            "candidate_summary": paths.candidate_summary,
            "label_progress": paths.label_progress,
            "iteration_report": paths.report_md,
            "training": paths.run_dir / "training" if trade_report is not None else None,
            "trade_report": paths.run_dir / "trade_report" if trade_report is not None else None,
        },
        "no_external_api_tokens_required": True,
    }
    _write_json(paths.candidate_summary, candidate_summary)
    _write_json(paths.label_progress, label_progress)
    paths.report_md.write_text(
        _render_report(
            app=app,
            run_id=run_id,
            candidate_summary=candidate_summary,
            label_progress=label_progress,
            trade_report=trade_report,
        ),
        encoding="utf-8",
    )
    _write_json(paths.manifest, manifest)
    _write_json(
        paths.latest_pointer,
        {
            "strategy_id": app.strategy_id,
            "run_id": run_id,
            "run_dir": paths.run_dir,
            "manifest": paths.manifest,
            "candidate_summary": paths.candidate_summary,
            "label_progress": paths.label_progress,
            "iteration_report": paths.report_md,
        },
    )
    print(f"annotation iteration written -> {paths.run_dir}", flush=True)
    print(f"latest pointer -> {paths.latest_pointer}", flush=True)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local annotation/research iteration.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--strategy", default=None, help="Annotation strategy id. Defaults to the first supported app.")
    parser.add_argument("--out-root", type=Path, default=DEFAULT_ITERATION_ROOT)
    parser.add_argument("--skip-trade-report", action="store_true", help="Only write annotation progress artifacts.")
    args = parser.parse_args()
    run_annotation_iteration(
        AnnotationIterationConfig(
            project_root=args.project_root,
            strategy_id=args.strategy,
            out_root=args.out_root,
            run_trade_report=not args.skip_trade_report,
        )
    )


if __name__ == "__main__":
    main()
