"""Level-desk annotation package with a lazy HTTP-server boundary."""

from anomaly_science.annotation.desk.candidates import DEFAULT_TF_MINUTES, build_annotation_groups, validate_candidates
from anomaly_science.annotation.desk.ui import LABELER_HTML, LAUNCHER_HTML

__all__ = [
    "DEFAULT_TF_MINUTES",
    "LABELER_HTML",
    "LAUNCHER_HTML",
    "LevelLabelerHandler",
    "LevelLabelerServer",
    "build_annotation_groups",
    "serve_level_labeler",
    "validate_candidates",
]


def __getattr__(name: str):
    if name in {"LevelLabelerHandler", "LevelLabelerServer", "serve_level_labeler"}:
        from anomaly_science.annotation.desk import server

        return getattr(server, name)
    raise AttributeError(name)
