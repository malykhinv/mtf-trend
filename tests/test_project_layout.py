from __future__ import annotations

from pathlib import Path


EXPECTED_LAYER_DIRS = {
    "contracts",
    "data",
    "universe",
    "events",
    "state",
    "future",
    "features",
    "atlas",
    "validation",
    "decision",
    "simulation",
    "live",
    "artifacts",
    "audit",
}


def test_clean_source_tree_exists() -> None:
    root = Path("src/anomaly_science")

    assert (root / "__init__.py").is_file()
    assert (root / "cli.py").is_file()

    missing = sorted(
        layer
        for layer in EXPECTED_LAYER_DIRS
        if not (root / layer / "__init__.py").is_file()
    )
    assert missing == []


def test_rebirth_plan_is_present() -> None:
    path = Path("docs/ANOMALY_SCIENCE_REBIRTH_PLAN.md")

    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "Stage 0" in text
    assert "Stage 1" in text
    assert "legacy_quarantine" in text
    assert "online 1m state" in text
