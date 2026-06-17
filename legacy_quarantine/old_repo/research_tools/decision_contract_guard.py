"""Guard that the rolling decision contract has no duplicate executable paths.

Run after architecture/parity patches:

    python -m research_tools.decision_contract_guard

This is intentionally a source-level guard. It fails when old live/backtest-only
signal decision helpers or post-hoc category rematch paths are reintroduced.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RESEARCH_TOOLS = REPO_ROOT / "research_tools"
SELF = Path(__file__).resolve()
CORE = RESEARCH_TOOLS / "pump_decision_core.py"

BANNED_SUBSTRINGS: tuple[str, ...] = (
    "def _rolling_runner_matches",
    "def _runner_candidate_matches",
    "def _rolling_runner_category_setup",
    "def _evaluate_rolling_profile",
    "def evaluate_baseline_free_prefilter",
    "def _with_runner_candidate_categories",
    "rolling_htf_then_first_category_qualified_30s_confirm",
    "baseline_free_prefilter",
)

# The matcher can exist only inside the shared core. Adapters must consume the
# final DecisionVerdict instead of rematching categories after the fact.
CORE_ONLY_SUBSTRINGS: tuple[str, ...] = (
    "match_rolling_categories(",
)


def _python_sources() -> list[Path]:
    return sorted(
        path
        for path in RESEARCH_TOOLS.rglob("*.py")
        if "__pycache__" not in path.parts and path.resolve() != SELF
    )


def main() -> int:
    violations: list[str] = []
    for path in _python_sources():
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(REPO_ROOT)
        for needle in BANNED_SUBSTRINGS:
            if needle in text:
                violations.append(f"{rel}: banned legacy decision path: {needle}")
        if path.resolve() != CORE.resolve():
            for needle in CORE_ONLY_SUBSTRINGS:
                if needle in text:
                    violations.append(f"{rel}: shared-core-only symbol used outside pump_decision_core.py: {needle}")
    if violations:
        print("Decision contract guard failed:")
        for item in violations:
            print(f"- {item}")
        return 1
    print("Decision contract guard passed: no duplicate rolling decision paths found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
