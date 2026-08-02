from __future__ import annotations

import pandas as pd
import pytest

from anomaly_science.strategy.pump_wave_short.wave_review import (
    IS_END_EXCLUSIVE_MS,
    PumpWaveReviewConfig,
    SOURCE_COLUMNS,
    build_wave_population,
    sample_wave_review_queue,
)


HOUR_MS = 3_600_000


def _row(
    event: str,
    *,
    chain: str,
    ignition_ms: int,
    symbol: str = "TESTUSDT",
    anchor: float = 110.0,
    base: float = 100.0,
) -> dict[str, object]:
    snapshot_ms = ignition_ms + 5 * 60_000
    return {
        "online_state_schema_version": "pump_fade_online_state_v4",
        "event_id": event,
        "recurrence_chain_id": chain,
        "symbol": symbol,
        "ignition_time_ms": ignition_ms,
        "snapshot_time_ms": snapshot_ms,
        "feature_cutoff_time_ms": snapshot_ms,
        "is_nature_anchor": True,
        "base_level": base,
        "anchor_high": anchor,
        "current_close": anchor * 0.99,
        "pump_size": anchor / base - 1.0,
        "turnover": 500_000.0,
        "atr_mult": 4.0,
        "act_now": 12.0,
        "act_now_over_peak": 0.8,
        "session": "EU",
        "n_prior_24h": 1,
        "n_prior_48h": 1,
        "min_since_last_prior": 60.0,
    }


def _frame() -> pd.DataFrame:
    start = int(pd.Timestamp("2025-08-01T00:00:00Z").timestamp() * 1_000)
    return pd.DataFrame(
        [
            _row("a", chain="chain-a", ignition_ms=start, anchor=110.0),
            _row("b", chain="chain-a", ignition_ms=start + HOUR_MS, anchor=116.0),
            _row("c", chain="chain-a", ignition_ms=start + 2 * HOUR_MS, anchor=121.0),
            _row("d", chain="chain-a", ignition_ms=start + 3 * HOUR_MS, anchor=123.0),
            _row("other", chain="chain-b", ignition_ms=start, symbol="OTHERUSDT"),
        ],
        columns=SOURCE_COLUMNS,
    )


def test_population_selects_only_causal_second_and_third_recurrences() -> None:
    population = build_wave_population(_frame())

    assert population["source_event_id"].tolist() == ["b", "c"]
    assert population["wave_ordinal"].tolist() == [2, 3]
    assert (
        population["feature_cutoff_time_ms"] <= population["selection_snapshot_time_ms"]
    ).all()
    assert population["review_end_ms"].lt(IS_END_EXCLUSIVE_MS).all()
    assert population["untouched_2026_row_used"].eq(False).all()


def test_future_tail_rows_cannot_change_existing_wave_candidates() -> None:
    original = build_wave_population(_frame())
    changed = _frame()
    future = _row(
        "future",
        chain="chain-a",
        ignition_ms=IS_END_EXCLUSIVE_MS + HOUR_MS,
        anchor=1_000_000.0,
    )
    changed = pd.concat([changed, pd.DataFrame([future], columns=SOURCE_COLUMNS)], ignore_index=True)
    mutated = build_wave_population(changed)

    pd.testing.assert_frame_equal(original, mutated)


def test_future_columns_are_rejected_instead_of_silently_ignored() -> None:
    frame = _frame().assign(y=1)

    with pytest.raises(ValueError, match="forbidden future columns"):
        build_wave_population(frame)


def test_review_sample_is_deterministic_and_stratified_without_outcomes() -> None:
    rows = []
    start = int(pd.Timestamp("2025-09-01T00:00:00Z").timestamp() * 1_000)
    for chain_index in range(8):
        chain = f"chain-{chain_index}"
        for ordinal in range(3):
            rows.append(
                _row(
                    f"{chain}-{ordinal}",
                    chain=chain,
                    ignition_ms=start + chain_index * 4 * HOUR_MS + ordinal * HOUR_MS,
                    symbol=f"S{chain_index}",
                    anchor=110.0 + ordinal,
                )
            )
    population = build_wave_population(pd.DataFrame(rows, columns=SOURCE_COLUMNS))
    config = PumpWaveReviewConfig(per_month_ordinal=3)

    first = sample_wave_review_queue(population, config=config)
    second = sample_wave_review_queue(population.sample(frac=1.0, random_state=91), config=config)

    assert len(first) == 6
    assert first.groupby("wave_ordinal").size().to_dict() == {2: 3, 3: 3}
    assert first["event_id"].tolist() == second["event_id"].tolist()
