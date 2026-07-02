from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from anomaly_science.strategy.pump_long.ev import (
    apply_exposure_rule,
    build_pump_long_report,
    summarize_arm,
)


def _trade(
    *, event: str, chain: str, entry_ms: int, exit_ms: int, net: float,
    stop_variant: str = "event_base", ordinal: int = 1, y: int = 0,
    snapshot_ms: int | None = None,
) -> dict:
    return {
        "event_id": event,
        "group": event,
        "symbol": "TEST",
        "snapshot_time_ms": entry_ms if snapshot_ms is None else snapshot_ms,
        "decision_index": ordinal,
        "recurrence_chain_id": chain,
        "n_prior_48h": 0,
        "last_prior_faded": float("nan"),
        "frac_prior_faded_48h": float("nan"),
        "min_since_last_prior": float("nan"),
        "stop_variant": stop_variant,
        "entry_eligible": True,
        "status": "filled",
        "entry_time_ms": entry_ms,
        "exit_time_ms": exit_ms,
        "entry_price": 100.0,
        "exit_price": 100.0 * (1.0 + net),
        "gross_return": net + 0.002,
        "net_return": net,
        "net_r": net / 0.05,
        "y": y,
        "label_available": True,
    }


def test_exposure_rule_skips_overlapping_entries_in_same_chain() -> None:
    outcomes = pd.DataFrame(
        [
            _trade(event="A:1", chain="c1", entry_ms=0, exit_ms=3_600_000, net=0.01),
            # Overlaps the open c1 position -> skipped.
            _trade(event="A:2", chain="c1", entry_ms=1_800_000, exit_ms=5_400_000, net=0.02),
            # Different chain overlapping in time -> kept.
            _trade(event="B:1", chain="c2", entry_ms=1_800_000, exit_ms=5_400_000, net=0.03),
            # Same chain after the first position closed -> kept.
            _trade(event="A:3", chain="c1", entry_ms=7_200_000, exit_ms=9_000_000, net=0.04),
        ]
    )

    kept = apply_exposure_rule(outcomes)

    assert sorted(kept["event_id"]) == ["A:1", "A:3", "B:1"]


def test_summarize_arm_reports_tail_dependence_and_weekly_consistency() -> None:
    week_ms = 7 * 24 * 3_600_000
    rows = []
    # 19 small losers spread over weeks, one huge winner.
    for i in range(19):
        rows.append(
            _trade(event=f"E:{i}", chain=f"c{i}", entry_ms=i * week_ms,
                   exit_ms=i * week_ms + 60_000, net=-0.005)
        )
    rows.append(
        _trade(event="E:big", chain="cbig", entry_ms=19 * week_ms,
               exit_ms=19 * week_ms + 60_000, net=0.50)
    )
    trades = pd.DataFrame(rows)

    summary = summarize_arm(trades, arm="blind", stop_variant="event_base",
                            bootstrap_iterations=200)

    assert summary.trades == 20
    assert summary.ev_net_return > 0.0  # mean dragged up by the single winner
    assert summary.ev_without_top_1pct < 0.0  # removing 1 top trade flips sign
    assert summary.positive_week_share == pytest.approx(1.0 / 20.0)
    assert summary.max_drawdown_return_sum == pytest.approx(19 * 0.005)


def test_report_splits_periods_and_emits_oracle_arm() -> None:
    dev_ms = int(pd.Timestamp("2025-07-01T00:00:00Z").value // 1_000_000)
    oos_ms = int(pd.Timestamp("2026-02-01T00:00:00Z").value // 1_000_000)
    outcomes = pd.DataFrame(
        [
            _trade(event="D:1", chain="d1", entry_ms=dev_ms, exit_ms=dev_ms + 60_000, net=0.02, y=0),
            _trade(event="D:2", chain="d2", entry_ms=dev_ms + 86_400_000,
                   exit_ms=dev_ms + 86_460_000, net=-0.01, y=1),
            _trade(event="O:1", chain="o1", entry_ms=oos_ms, exit_ms=oos_ms + 60_000, net=0.05, y=0),
        ]
    )

    summary, strata = build_pump_long_report(outcomes, period="development",
                                             bootstrap_iterations=50)

    blind = summary.loc[summary["arm"] == "blind"].iloc[0]
    oracle = summary.loc[summary["arm"] == "oracle_continuation"].iloc[0]
    assert blind["trades"] == 2  # OOS trade excluded
    assert oracle["trades"] == 1
    assert oracle["ev_net_return"] == pytest.approx(0.02)
    assert set(strata["axis"]) == {"ordinal", "chain_position", "last_prior_faded"}
