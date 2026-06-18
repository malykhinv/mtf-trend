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
    "strategy",
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


def test_canonical_project_docs_are_present() -> None:
    methodology = Path("docs/research_methodology_core.md")
    strategy = Path("research/STRATEGY_SPEC.md")
    state = Path("research/RESEARCH_STATE.md")

    assert methodology.is_file()
    assert strategy.is_file()
    assert state.is_file()

    methodology_text = methodology.read_text(encoding="utf-8")
    strategy_text = strategy.read_text(encoding="utf-8")
    state_text = state.read_text(encoding="utf-8")

    assert "Core" in methodology_text
    assert "Strategy" in methodology_text
    assert "strategy_family = anomaly" in strategy_text
    assert "canonical_spec = docs/strategies/anomaly_strategy.md" in strategy_text
    assert "legacy_quarantine" in state_text
    assert "online 1m anomaly state" in state_text
