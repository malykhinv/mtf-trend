"""Browser launcher for annotation-capable strategy review workflows."""

from __future__ import annotations

import argparse
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from anomaly_science.annotation.desk.server import serve_level_labeler
from anomaly_science.annotation.review_questions import ReviewQuestion


@dataclass(frozen=True, slots=True)
class AnnotationStrategyApp:
    strategy_id: str
    title: str
    candidates_path: Path
    labels_path: Path
    cache_dir: Path
    marks_path: Path | None = None
    review_questions: tuple[ReviewQuestion, ...] = ()


def default_annotation_apps(project_root: Path) -> tuple[AnnotationStrategyApp, ...]:
    """Return locally materialized apps declared at the strategy registry boundary."""

    from anomaly_science.strategy.registry import annotation_apps

    return annotation_apps(project_root)


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
    parser.add_argument("--solo", action="store_true",
                        help="Serve ONLY --strategy: no other tabs, no launcher, opens straight into the desk.")
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    apps = default_annotation_apps(project_root)
    default = choose_app(apps, args.strategy)  # validates the requested id + picks default
    if args.solo:
        apps = (default,)
    url = f"http://{args.host}:{args.port}/"
    if not args.no_open:
        webbrowser.open(url)
    serve_level_labeler(
        apps=[
            (a.strategy_id, a.title, a.candidates_path, a.labels_path, a.marks_path, a.review_questions)
            for a in apps
        ],
        default_strategy_id=default.strategy_id,
        cache_dir=default.cache_dir,
        host=args.host,
        port=args.port,
        show_launcher=not args.solo,
        project_root=project_root,
    )


if __name__ == "__main__":
    main()
