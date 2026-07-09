"""Browser launcher for annotation-capable strategy review workflows."""

from __future__ import annotations

import argparse
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from anomaly_science.annotation.level_labeler import serve_level_labeler


@dataclass(frozen=True, slots=True)
class AnnotationStrategyApp:
    strategy_id: str
    title: str
    candidates_path: Path
    labels_path: Path
    cache_dir: Path


def default_annotation_apps(project_root: Path) -> tuple[AnnotationStrategyApp, ...]:
    """Return annotation workflows that have concrete local artifacts."""

    base = project_root / ".output" / "results" / "triple_tap_v1" / "manual_pump_review"
    cache_dir = project_root / ".output" / "market" / "binance_vision" / "um_futures" / "enriched_1m"
    app = AnnotationStrategyApp(
        strategy_id="triple_tap_manual_pump_review",
        title="Triple-tap pump review: cap / breakout level labeling",
        candidates_path=base / "pump_review_candidates.parquet",
        labels_path=base / "pump_level_labels.jsonl",
        cache_dir=cache_dir,
    )
    return (app,) if app.candidates_path.exists() and app.cache_dir.exists() else ()


def choose_app(apps: tuple[AnnotationStrategyApp, ...], strategy_id: str | None) -> AnnotationStrategyApp:
    if not apps:
        raise RuntimeError("no annotation-capable strategy apps found; build candidate artifacts first")
    if strategy_id is None:
        return apps[0]
    for app in apps:
        if app.strategy_id == strategy_id:
            return app
    known = ", ".join(app.strategy_id for app in apps)
    raise ValueError(f"unknown annotation strategy {strategy_id!r}; known: {known}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch the browser annotation app.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--strategy", default=None, help="Annotation strategy id. Defaults to the first supported app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="Start the server without opening the system browser.")
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    app = choose_app(default_annotation_apps(project_root), args.strategy)
    url = f"http://{args.host}:{args.port}/"
    if not args.no_open:
        webbrowser.open(url)
    serve_level_labeler(
        candidates_path=app.candidates_path,
        labels_path=app.labels_path,
        cache_dir=app.cache_dir,
        host=args.host,
        port=args.port,
        strategy_id=app.strategy_id,
        strategy_title=app.title,
        show_launcher=True,
    )


if __name__ == "__main__":
    main()
