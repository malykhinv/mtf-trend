from __future__ import annotations

from dataclasses import asdict, replace

import numpy as np
import pandas as pd
import pytest

from anomaly_science.strategy.residual_absorption.market_impulse import (
    MarketImpulseEvent,
    build_market_factor_snapshots,
    detect_market_impulse_events,
)
from anomaly_science.strategy.residual_absorption.residual_response import (
    build_residual_response_profiles,
)
from anomaly_science.strategy.residual_absorption.response_memory import (
    ResponseMemoryRecord,
    build_response_memory_features,
)
from anomaly_science.strategy.residual_absorption.response_outcomes import (
    build_response_outcome_records,
)
from anomaly_science.strategy.residual_absorption.stage1_outcome_build import (
    build_symbol_outcome_records,
)
from anomaly_science.strategy.residual_absorption.stage1_falsification import (
    cluster_bootstrap_primary,
    weighted_median,
    weighted_spearman,
)
from anomaly_science.strategy.residual_absorption.stage1_robustness import (
    within_event_permutation_placebo,
)
from anomaly_science.strategy.residual_absorption.stage1_response_build import (
    central_factor_order_statistics,
)
from anomaly_science.strategy.residual_absorption.stage2_local_features import (
    compute_local_window_features,
)
from anomaly_science.strategy.residual_absorption.spec import (
    MarketImpulseSpec,
    ResidualResponseSpec,
    ResponseOutcomeSpec,
)
from anomaly_science.validation.research_split import ResearchSplitError


def _ms(value: str) -> int:
    return int(pd.Timestamp(value).timestamp() * 1_000)


def _spec() -> MarketImpulseSpec:
    return MarketImpulseSpec(
        same_type_baseline_sessions=2,
        minimum_baseline_snapshots=4,
        minimum_cross_section_symbols=3,
        primary_absolute_robust_z=3.0,
        primary_directional_breadth=0.60,
        event_cooldown_minutes=60,
    )


def _snapshot_rows(timestamp: str, alt_return: float, *, btc: float, eth: float) -> list[dict[str, object]]:
    snapshot = _ms(timestamp)
    rows: list[dict[str, object]] = []
    for index, symbol in enumerate(("AAAUSDT", "BBBUSDT", "CCCUSDT")):
        value = alt_return + index * 0.00001
        rows.append(
            {
                "symbol": symbol,
                "snapshot_time_ms": snapshot,
                "feature_cutoff_time_ms": snapshot,
                "core_eligible": True,
                "return_5m": value / 3.0,
                "return_15m": value,
                "return_30m": value * 1.5,
            }
        )
    for symbol, value in (("BTCUSDT", btc), ("ETHUSDT", eth)):
        rows.append(
            {
                "symbol": symbol,
                "snapshot_time_ms": snapshot,
                "feature_cutoff_time_ms": snapshot,
                "core_eligible": True,
                "return_5m": value / 3.0,
                "return_15m": value,
                "return_30m": value * 1.5,
            }
        )
    return rows


def _factor_fixture() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for day, values in (
        ("2025-08-01", (-0.002, -0.001, 0.001)),
        ("2025-08-02", (-0.0015, 0.0005, 0.002)),
    ):
        for minute, value in zip((5, 10, 15), values, strict=True):
            rows.extend(
                _snapshot_rows(
                    f"{day}T08:{minute:02d}:00Z",
                    value,
                    btc=value,
                    eth=value,
                )
            )
    rows.extend(
        _snapshot_rows(
            "2025-08-03T08:10:00Z",
            0.020,
            btc=0.010,
            eth=-0.005,
        )
    )
    rows.extend(
        _snapshot_rows(
            "2025-08-03T08:15:00Z",
            0.025,
            btc=0.015,
            eth=0.005,
        )
    )
    rows.extend(
        _snapshot_rows(
            "2025-08-04T08:10:00Z",
            0.030,
            btc=0.020,
            eth=0.010,
        )
    )
    return pd.DataFrame(rows)


def test_market_impulse_uses_prior_completed_same_type_sessions() -> None:
    snapshots = build_market_factor_snapshots(_factor_fixture(), spec=_spec())
    by_time = {row.snapshot_time_ms: row for row in snapshots}
    first_impulse = by_time[_ms("2025-08-03T08:10:00Z")]
    second_same_session = by_time[_ms("2025-08-03T08:15:00Z")]

    assert first_impulse.baseline_snapshot_count == 6
    assert first_impulse.factor_robust_z_15m is not None
    assert first_impulse.factor_robust_z_15m > 3.0
    assert first_impulse.direction == 1
    assert first_impulse.directional_breadth_up_15m == pytest.approx(1.0)
    assert first_impulse.reference_support_count == 1
    assert first_impulse.market_impulse_candidate is True
    assert first_impulse.reference_confirmed_candidate is True
    assert second_same_session.baseline_snapshot_count == 6
    assert second_same_session.factor_baseline_median_15m == pytest.approx(
        first_impulse.factor_baseline_median_15m
    )


def test_market_impulse_collapses_adjacent_candidates_but_not_next_day() -> None:
    snapshots = build_market_factor_snapshots(_factor_fixture(), spec=_spec())
    events = detect_market_impulse_events(snapshots, spec=_spec())

    assert [event.snapshot_time_ms for event in events] == [
        _ms("2025-08-03T08:10:00Z"),
        _ms("2025-08-04T08:10:00Z"),
    ]
    assert events[0].reference_support_count == 1
    assert events[0].reference_confirmed is True


def test_market_impulse_prefix_is_invariant_to_future_tail() -> None:
    frame = _factor_fixture()
    cutoff = _ms("2025-08-03T08:15:00Z")
    base = [
        row
        for row in build_market_factor_snapshots(frame, spec=_spec())
        if row.snapshot_time_ms <= cutoff
    ]
    changed = frame.copy()
    changed.loc[changed["snapshot_time_ms"].gt(cutoff), "return_15m"] = -999.0
    changed.loc[changed["snapshot_time_ms"].gt(cutoff), "return_5m"] = 999.0
    replay = [
        row
        for row in build_market_factor_snapshots(changed, spec=_spec())
        if row.snapshot_time_ms <= cutoff
    ]

    assert [asdict(row) for row in replay] == [asdict(row) for row in base]


def test_market_impulse_builder_physically_rejects_oos_rows() -> None:
    frame = pd.DataFrame(
        _snapshot_rows(
            "2026-01-01T08:10:00Z",
            0.02,
            btc=0.01,
            eth=0.01,
        )
    )
    with pytest.raises(ResearchSplitError, match="partition=oos"):
        build_market_factor_snapshots(frame, spec=_spec())


def _response_fixture() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for day, values in (
        ("2025-08-01", (-0.004, -0.002, 0.002)),
        ("2025-08-02", (-0.003, 0.001, 0.004)),
    ):
        for minute, factor in zip((5, 10, 15), values, strict=True):
            snapshot = _ms(f"{day}T08:{minute:02d}:00Z")
            for symbol, response in (
                ("AAAUSDT", 0.5 * factor),
                ("BBBUSDT", factor),
                ("CCCUSDT", factor),
            ):
                rows.append(
                    {
                        "symbol": symbol,
                        "snapshot_time_ms": snapshot,
                        "feature_cutoff_time_ms": snapshot,
                        "core_eligible": True,
                        "candidate_eligible": False,
                        "return_15m": response,
                    }
                )
    event_snapshot = _ms("2025-08-03T08:10:00Z")
    for symbol, response, candidate in (
        ("AAAUSDT", 0.002, True),
        ("BBBUSDT", 0.020, False),
        ("CCCUSDT", 0.020, False),
    ):
        rows.append(
            {
                "symbol": symbol,
                "snapshot_time_ms": event_snapshot,
                "feature_cutoff_time_ms": event_snapshot,
                "core_eligible": True,
                "candidate_eligible": candidate,
                "return_15m": response,
            }
        )
    return pd.DataFrame(rows)


def _response_event() -> MarketImpulseEvent:
    snapshot = _ms("2025-08-03T08:10:00Z")
    return MarketImpulseEvent(
        event_id=f"market_impulse:{snapshot}:+1",
        schema_version="market_impulse_v1",
        snapshot_time_ms=snapshot,
        feature_cutoff_time_ms=snapshot,
        utc_day=snapshot // 86_400_000,
        session_seq=1,
        session_name="EU",
        direction=1,
        factor_return_15m=0.020,
        factor_robust_z_15m=5.0,
        directional_breadth_15m=1.0,
        cross_section_symbol_count=3,
        reference_support_count=1,
        reference_confirmed=True,
    )


def _response_spec() -> ResidualResponseSpec:
    return ResidualResponseSpec(
        beta_history_same_type_sessions=2,
        beta_short_history_sessions=1,
        minimum_beta_observations=4,
        minimum_short_beta_observations=3,
    )


def test_residual_profile_uses_leave_one_out_factor_and_prior_sessions() -> None:
    rows = build_residual_response_profiles(
        _response_fixture(),
        events=[_response_event()],
        spec=_response_spec(),
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.symbol == "AAAUSDT"
    assert row.factor_excludes_symbol is True
    assert row.history_observation_count == 6
    assert row.short_history_observation_count == 3
    assert row.beta_15m == pytest.approx(0.5)
    assert row.current_leave_one_out_factor_return_15m == pytest.approx(0.020)
    assert row.expected_symbol_return_15m == pytest.approx(0.010)
    assert row.response_residual_15m == pytest.approx(-0.008)
    assert row.direction_adjusted_underreaction_15m == pytest.approx(0.008)


def test_residual_profile_is_invariant_to_future_tail() -> None:
    frame = _response_fixture()
    base = build_residual_response_profiles(
        frame,
        events=[_response_event()],
        spec=_response_spec(),
    )
    future = frame.copy()
    future["snapshot_time_ms"] += 10 * 24 * 60 * 60 * 1_000
    future["feature_cutoff_time_ms"] = future["snapshot_time_ms"]
    future["return_15m"] = 999.0
    replay = build_residual_response_profiles(
        pd.concat([frame, future], ignore_index=True),
        events=[_response_event()],
        spec=_response_spec(),
    )

    assert [asdict(row) for row in replay] == [asdict(row) for row in base]


def _current_response_profile():
    return build_residual_response_profiles(
        _response_fixture(),
        events=[_response_event()],
        spec=_response_spec(),
    )[0]


def _memory_record(
    *,
    event_id: str,
    event_time: str,
    resolution_time: str | None,
    direction: int,
    session_seq: int,
    catchup_60m: float,
) -> ResponseMemoryRecord:
    resolved = resolution_time is not None
    return ResponseMemoryRecord(
        event_id=event_id,
        symbol="AAAUSDT",
        event_snapshot_time_ms=_ms(event_time),
        impulse_direction=direction,
        session_seq=session_seq,
        initial_direction_adjusted_underreaction_15m=0.005 if direction > 0 else 0.003,
        resolution_time_ms=None if resolution_time is None else _ms(resolution_time),
        catchup_residual_change_15m=catchup_60m / 4 if resolved else None,
        catchup_residual_change_30m=catchup_60m / 2 if resolved else None,
        catchup_residual_change_60m=catchup_60m if resolved else None,
        catchup_residual_change_120m=catchup_60m * 1.2 if resolved else None,
        terminal_direction_adjusted_residual_120m=-0.001 if resolved else None,
        maximum_catchup_residual_change_120m=max(catchup_60m, 0.0) if resolved else None,
        zero_crossed_expected_response_120m=(catchup_60m > 0.0) if resolved else None,
        time_to_half_catchup_minutes=30.0 if resolved and catchup_60m > 0.0 else None,
    )


def test_response_memory_uses_only_resolutions_available_at_snapshot() -> None:
    current = _current_response_profile()
    records = [
        _memory_record(
            event_id="prior-positive",
            event_time="2025-08-01T08:00:00Z",
            resolution_time="2025-08-01T10:00:00Z",
            direction=1,
            session_seq=1,
            catchup_60m=0.010,
        ),
        _memory_record(
            event_id="prior-negative",
            event_time="2025-08-02T00:00:00Z",
            resolution_time="2025-08-02T02:00:00Z",
            direction=-1,
            session_seq=0,
            catchup_60m=-0.002,
        ),
        _memory_record(
            event_id="resolves-after-current",
            event_time="2025-08-03T07:00:00Z",
            resolution_time="2025-08-03T09:00:00Z",
            direction=1,
            session_seq=1,
            catchup_60m=999.0,
        ),
        _memory_record(
            event_id="still-unresolved",
            event_time="2025-08-03T07:30:00Z",
            resolution_time=None,
            direction=1,
            session_seq=1,
            catchup_60m=0.0,
        ),
    ]

    features = build_response_memory_features(records, current=current)
    assert features.resolved_count_20 == 2
    assert features.unresolved_prior_count == 2
    assert features.same_direction_resolved_count_20 == 1
    assert features.same_session_resolved_count_20 == 1
    assert features.median_catchup_residual_change_60m_20 == pytest.approx(0.004)
    assert features.positive_catchup_rate_60m_20 == pytest.approx(0.5)
    assert features.current_underreaction_minus_history_median == pytest.approx(0.004)

    mutated = list(records)
    mutated[2] = replace(
        mutated[2],
        catchup_residual_change_60m=-999.0,
        catchup_residual_change_120m=-999.0,
        maximum_catchup_residual_change_120m=-999.0,
    )
    assert asdict(build_response_memory_features(mutated, current=current)) == asdict(features)


def test_unresolved_response_memory_rejects_finalized_outcome() -> None:
    with pytest.raises(ValueError, match="cannot expose"):
        ResponseMemoryRecord(
            event_id="leaky",
            symbol="AAAUSDT",
            event_snapshot_time_ms=_ms("2025-08-01T08:00:00Z"),
            impulse_direction=1,
            session_seq=1,
            initial_direction_adjusted_underreaction_15m=0.01,
            resolution_time_ms=None,
            catchup_residual_change_15m=1.0,
            catchup_residual_change_30m=None,
            catchup_residual_change_60m=None,
            catchup_residual_change_120m=None,
            terminal_direction_adjusted_residual_120m=None,
            maximum_catchup_residual_change_120m=None,
            zero_crossed_expected_response_120m=None,
            time_to_half_catchup_minutes=None,
        )


def _future_price_fixture() -> pd.DataFrame:
    event_time = _response_event().snapshot_time_ms
    rows: list[dict[str, object]] = []
    for step in range(25):
        snapshot = event_time + step * 5 * 60_000
        factor_return = step * 0.001
        catchup = step * 0.001
        returns = {
            "AAAUSDT": 0.5 * factor_return + catchup,
            "BBBUSDT": factor_return,
            "CCCUSDT": factor_return,
        }
        for symbol, value in returns.items():
            rows.append(
                {
                    "symbol": symbol,
                    "snapshot_time_ms": snapshot,
                    "feature_cutoff_time_ms": snapshot,
                    "close": 100.0 * (1.0 + value),
                    "core_eligible": True,
                }
            )
    return pd.DataFrame(rows)


def test_response_outcome_freezes_beta_and_event_time_factor_membership() -> None:
    current = _current_response_profile()
    records = build_response_outcome_records(
        _future_price_fixture(),
        profiles=[current],
        spec=ResponseOutcomeSpec(minimum_factor_symbols=2),
    )

    assert len(records) == 1
    record = records[0]
    assert record.resolution_time_ms == current.snapshot_time_ms + 120 * 60_000
    assert record.catchup_residual_change_15m == pytest.approx(0.003)
    assert record.catchup_residual_change_30m == pytest.approx(0.006)
    assert record.catchup_residual_change_60m == pytest.approx(0.012)
    assert record.catchup_residual_change_120m == pytest.approx(0.024)
    assert record.terminal_direction_adjusted_residual_120m == pytest.approx(0.016)
    assert record.zero_crossed_expected_response_120m is True
    assert record.time_to_half_catchup_minutes == pytest.approx(20.0)


def test_response_outcome_ignores_prices_after_registered_resolution() -> None:
    current = _current_response_profile()
    frame = _future_price_fixture()
    base = build_response_outcome_records(
        frame,
        profiles=[current],
        spec=ResponseOutcomeSpec(minimum_factor_symbols=2),
    )
    tail = frame.loc[frame["snapshot_time_ms"].eq(frame["snapshot_time_ms"].max())].copy()
    tail["snapshot_time_ms"] += 5 * 60_000
    tail["feature_cutoff_time_ms"] = tail["snapshot_time_ms"]
    tail["close"] = 10**9
    replay = build_response_outcome_records(
        pd.concat([frame, tail], ignore_index=True),
        profiles=[current],
        spec=ResponseOutcomeSpec(minimum_factor_symbols=2),
    )

    assert [asdict(row) for row in replay] == [asdict(row) for row in base]


def test_scalable_response_outcome_matches_direct_frozen_factor_path() -> None:
    current = _current_response_profile()
    frame = _future_price_fixture()
    direct = build_response_outcome_records(
        frame,
        profiles=[current],
        spec=ResponseOutcomeSpec(minimum_factor_symbols=2),
    )[0]
    event_prices = frame.loc[
        frame["snapshot_time_ms"].eq(current.snapshot_time_ms)
    ].set_index("symbol")["close"]
    rows: list[dict[str, object]] = []
    for path_time, group in frame.loc[
        frame["snapshot_time_ms"].gt(current.snapshot_time_ms)
    ].groupby("snapshot_time_ms"):
        factor_returns = [
            float(row.close / event_prices.loc[row.symbol] - 1.0)
            for row in group.itertuples(index=False)
        ]
        rows.append(
            {
                "path_time_ms": int(path_time),
                **central_factor_order_statistics(factor_returns),
            }
        )
    aaa_prices = frame.loc[frame["symbol"].eq("AAAUSDT")].set_index(
        "snapshot_time_ms"
    )["close"]
    scalable = build_symbol_outcome_records(
        pd.DataFrame([asdict(current)]),
        prices=aaa_prices,
        factor_path_stats={
            current.snapshot_time_ms: pd.DataFrame(rows).set_index("path_time_ms")
        },
        core_event_times={current.snapshot_time_ms},
        spec=ResponseOutcomeSpec(minimum_factor_symbols=2),
    )[0]

    for name, expected in asdict(direct).items():
        actual = getattr(scalable, name)
        if isinstance(expected, float):
            assert actual == pytest.approx(expected)
        else:
            assert actual == expected


def test_cluster_bootstrap_resamples_whole_clusters() -> None:
    frame = pd.DataFrame(
        {
            "direction_adjusted_underreaction_15m": [
                value for value in range(1, 21) for _ in range(2)
            ],
            "catchup_residual_change_60m": [
                value / 10_000 for value in range(1, 21) for _ in range(2)
            ],
            "utc_day": [cluster for cluster in range(10) for _ in range(4)],
        }
    )
    result = cluster_bootstrap_primary(
        frame,
        cluster_column="utc_day",
        repetitions=200,
        seed=7,
    )

    assert result["cluster_count"] == 10
    assert result["spearman_95pct_percentile_ci"][0] > 0.99
    assert result["positive_underreaction_median_60m_95pct_percentile_ci"][0] > 0.0


def test_weighted_estimands_match_expanded_sample() -> None:
    values = np.asarray([1.0, 2.0, 3.0, 4.0])
    weights = np.asarray([1.0, 2.0, 1.0, 2.0])
    expanded = np.repeat(values, weights.astype(int))

    assert weighted_median(values, weights) == np.median(expanded)
    assert weighted_spearman(values, values[::-1], np.ones(4)) == pytest.approx(-1.0)


def test_within_event_placebo_breaks_symbol_pairing() -> None:
    rows = []
    for event in range(20):
        for rank in range(10):
            rows.append(
                {
                    "event_id": f"event-{event}",
                    "direction_adjusted_underreaction_15m": float(rank),
                    "catchup_residual_change_60m": float(rank) / 10_000,
                }
            )
    result = within_event_permutation_placebo(
        pd.DataFrame(rows),
        repetitions=200,
        seed=11,
    )

    assert result["observed_spearman"] == pytest.approx(1.0)
    assert result["observed_spearman"] > result["null_spearman_95th_percentile"]
    assert result["spearman_one_sided_randomization_p"] < 0.01


def test_local_activity_window_is_invariant_to_future_tail() -> None:
    snapshot = _ms("2025-08-03T08:30:00Z")
    rows = []
    for offset in range(-30, 6):
        rows.append(
            {
                "timestamp": snapshot + offset * 60_000,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0 + offset / 100.0,
                "quote_volume": 1_000.0 if offset < 0 else 10**12,
                "trade_count": 10.0,
                "taker_buy_quote_volume": 600.0,
                "open_interest": 1_000.0 + offset,
                "long_liquidations_vol": 0.0,
                "short_liquidations_vol": 1.0,
                "oi_available": True,
                "missing_oi_flag": False,
                "liquidation_available": True,
                "missing_liquidation_flag": False,
            }
        )
    frame = pd.DataFrame(rows)
    base = compute_local_window_features(
        frame.loc[frame["timestamp"].lt(snapshot)],
        snapshot_time_ms=snapshot,
        window_minutes=15,
    )
    replay = compute_local_window_features(
        frame,
        snapshot_time_ms=snapshot,
        window_minutes=15,
    )

    assert replay == base
    assert replay["local_15m_minute_count"] == 15
    assert replay["local_15m_quote_volume"] == 15_000.0
    assert replay["local_15m_taker_imbalance"] == pytest.approx(0.2)
