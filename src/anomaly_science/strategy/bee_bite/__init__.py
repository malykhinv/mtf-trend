"""Bee-bite (Wyckoff spring) strategy: long the liquidity-grab reclaim at a
post-pump range low, targeting a new high.

Setup logic ported (as principles, not code) from the `codex/bee-bite-only`
branch: a coin pumps from dormancy on high flow, stalls into a tight range at
the high, then price SWEEPS below the range low (a stop-run) and RECLAIMS back
inside - the entry - with the stop under the sweep low and the target a new
high. No liquidation data, so the grab is read purely from candles (sweep wick,
depth, reclaim speed, absorption volume). Runs on the shared research harness.
"""

from anomaly_science.strategy.bee_bite.spring import build_spring_outcomes, report

__all__ = ["build_spring_outcomes", "report"]
