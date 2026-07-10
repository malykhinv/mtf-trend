"""Level-desk annotation package.

The package keeps the browser desk thin: candidate grouping, JSONL state,
iteration artifact reads, and OHLCV windows live behind explicit boundaries.
"""

from anomaly_science.annotation.desk.candidates import DEFAULT_TF_MINUTES, build_annotation_groups, validate_candidates
from anomaly_science.annotation.desk.server import LevelLabelerHandler, LevelLabelerServer, serve_level_labeler
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
