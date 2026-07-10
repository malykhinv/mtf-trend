"""Compatibility facade for the level-desk annotation UI.

New code should import from :mod:`anomaly_science.annotation.desk`.  This
module preserves the historical public entry points used by scripts and tests.
"""

from __future__ import annotations

from anomaly_science.annotation.desk import (
    DEFAULT_TF_MINUTES,
    LABELER_HTML,
    LAUNCHER_HTML,
    LevelLabelerHandler,
    LevelLabelerServer,
    build_annotation_groups,
    serve_level_labeler,
    validate_candidates,
)
from anomaly_science.annotation.desk.server import main

_build_annotation_groups = build_annotation_groups

__all__ = [
    "DEFAULT_TF_MINUTES",
    "LABELER_HTML",
    "LAUNCHER_HTML",
    "LevelLabelerHandler",
    "LevelLabelerServer",
    "_build_annotation_groups",
    "build_annotation_groups",
    "main",
    "serve_level_labeler",
    "validate_candidates",
]


if __name__ == "__main__":
    main()
