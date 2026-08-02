from __future__ import annotations

import numpy as np

from anomaly_science.strategy.triple_tap.research.brkcap_foundations import (
    BrkCapStructure,
    CandleSeries,
    FoundationViolation,
    PumpTransition,
    ResistanceLevel,
    SetupFamily,
    audit_structure,
    first_close_above_level_ms,
)
from anomaly_science.strategy.triple_tap.research.brkcap_geometry import analyze_level_geometry
from anomaly_science.strategy.triple_tap.research.brkcap_discovery_review import (
    CapStructureMetrics,
    DiscoveryPolicy,
    _cap_is_structurally_coherent,
    _credible_pump,
    _main_touches,
    discover_level,
)
from anomaly_science.strategy.triple_tap.research.pump_review_candidates import _is_dump_rebound
from anomaly_science.strategy.triple_tap.research.brkcap_review_builder import _review_row
from anomaly_science.strategy.triple_tap.research.sleep_pump_review import _proposal_indices, _pump_shape_features


def _candles() -> CandleSeries:
    n = 160
    ts = np.arange(n, dtype=np.int64) * 60_000
    close = np.full(n, 100.0)
    close[120:126] = [100.0, 106.0, 112.0, 118.0, 116.0, 120.0]
    close[126:135] = [114.0, 116.0, 117.0, 118.0, 119.0, 118.0, 119.0, 119.5, 121.0]
    return CandleSeries(
        timestamp=ts,
        open=close.copy(),
        high=close + 0.5,
        low=close - 0.5,
        close=close,
    )


def test_breakout_requires_the_pump_high_to_be_the_level_anchor() -> None:
    candles = _candles()
    audit = audit_structure(
        candles,
        BrkCapStructure(
            family=SetupFamily.BREAKOUT,
            pump=PumpTransition(120 * 60_000, 99.5, 125 * 60_000, 120.5),
            level=ResistanceLevel(127 * 60_000, 134 * 60_000, 117.5),
        ),
    )
    assert FoundationViolation.BREAKOUT_LEVEL_NOT_PUMP_HIGH in audit.violations


def test_cap_level_must_be_created_after_culmination_and_below_it() -> None:
    candles = _candles()
    audit = audit_structure(
        candles,
        BrkCapStructure(
            family=SetupFamily.CAP,
            pump=PumpTransition(120 * 60_000, 99.5, 125 * 60_000, 120.5),
            level=ResistanceLevel(124 * 60_000, 134 * 60_000, 121.0),
        ),
    )
    assert FoundationViolation.CAP_LEVEL_NOT_AFTER_PULLBACK in audit.violations


def test_dump_wick_is_not_accepted_as_a_pump_start() -> None:
    candles = _candles()
    audit = audit_structure(
        candles,
        BrkCapStructure(
            family=SetupFamily.BREAKOUT,
            pump=PumpTransition(120 * 60_000, 70.0, 125 * 60_000, 120.5),
            level=ResistanceLevel(125 * 60_000, 134 * 60_000, 120.5),
        ),
    )
    assert FoundationViolation.DUMP_AT_PUMP_START in audit.violations


def test_candidate_dump_guard_is_unconditional_on_rebound_size() -> None:
    sleep = np.full(120, 100.0)
    assert _is_dump_rebound(70.0, sleep)


def test_level_ends_at_first_later_close_above() -> None:
    candles = _candles()
    first_break = first_close_above_level_ms(
        candles,
        level_price=120.0,
        level_start_ms=125 * 60_000,
    )
    assert first_break == 134 * 60_000
    audit = audit_structure(
        candles,
        BrkCapStructure(
            family=SetupFamily.BREAKOUT,
            pump=PumpTransition(120 * 60_000, 99.5, 125 * 60_000, 120.5),
            level=ResistanceLevel(125 * 60_000, 140 * 60_000, 120.5),
        ),
    )
    assert FoundationViolation.LEVEL_ALREADY_BROKEN in audit.violations


def test_clean_sleep_pump_breakout_passes_foundation_audit() -> None:
    candles = _candles()
    audit = audit_structure(
        candles,
        BrkCapStructure(
            family=SetupFamily.BREAKOUT,
            pump=PumpTransition(120 * 60_000, 99.5, 125 * 60_000, 120.5),
            level=ResistanceLevel(125 * 60_000, 134 * 60_000, 120.5),
        ),
    )
    assert audit.accepted


def test_one_candle_spike_is_not_a_valid_pump() -> None:
    candles = _candles()
    candles = CandleSeries(
        timestamp=candles.timestamp,
        open=candles.open,
        high=np.where(np.arange(len(candles.high)) == 121, 121.0, candles.high),
        low=candles.low,
        close=np.where(np.arange(len(candles.close)) == 121, 120.0, candles.close),
    )
    audit = audit_structure(
        candles,
        BrkCapStructure(
            family=SetupFamily.BREAKOUT,
            pump=PumpTransition(120 * 60_000, 99.5, 121 * 60_000, 121.0),
            level=ResistanceLevel(121 * 60_000, 122 * 60_000, 121.0),
        ),
    )
    assert FoundationViolation.PUMP_TOO_SHORT in audit.violations


def test_cap_rejects_descending_retraces_between_level_touches() -> None:
    n = 170
    close = np.full(n, 100.0)
    close[120:126] = [100.0, 105.0, 110.0, 115.0, 118.0, 120.0]
    close[126:146] = [116.0, 115.0, 116.0, 117.0, 117.0, 116.0, 114.0, 116.0, 117.0, 118.0, 117.0, 116.0, 110.0, 115.0, 117.0, 117.0, 116.0, 115.0, 114.0, 113.0]
    high = close + 0.5
    high[[130, 135, 140]] = 118.0
    low = close - 0.5
    low[132] = 112.0
    low[137] = 108.0
    candles = CandleSeries(
        timestamp=np.arange(n, dtype=np.int64) * 60_000,
        open=close.copy(),
        high=high,
        low=low,
        close=close,
    )
    audit = audit_structure(
        candles,
        BrkCapStructure(
            family=SetupFamily.CAP,
            pump=PumpTransition(120 * 60_000, 99.5, 125 * 60_000, 120.5),
            level=ResistanceLevel(130 * 60_000, 145 * 60_000, 118.0),
        ),
    )
    assert FoundationViolation.CAP_DESCENDING_SUPPORT in audit.violations
    assert FoundationViolation.CAP_RETRACES_NOT_COMPRESSING in audit.violations
    assert audit.review_eligible
    assert set(audit.quality_flags) >= {
        FoundationViolation.CAP_DESCENDING_SUPPORT,
        FoundationViolation.CAP_RETRACES_NOT_COMPRESSING,
    }


def test_v2_review_seed_keeps_pump_and_level_from_one_strict_structure() -> None:
    candles = _candles()
    row = _review_row(
        {
            "setup_family": "breakout",
            "symbol": "AAAUSDT",
            "tf": "1m",
            "entry_time_ms": 134 * 60_000,
            "pump_start_ms": 120 * 60_000,
            "h1_time_ms": 125 * 60_000,
            "h1": 120.5,
            "level": 120.5,
        },
        candles,
    )
    setup = row.seed_label["setups"][0]
    assert setup["pump_start_ms"] == 120 * 60_000
    assert setup["culmination_ms"] == setup["level_start_ms"] == 125 * 60_000
    assert setup["level_touch_times_ms"] == [125 * 60_000]
    assert setup["level_end_ms"] == 134 * 60_000


def test_sleep_pump_discovery_proposes_a_confirmed_sleep_to_high_transition() -> None:
    n = 300
    high = np.full(n, 100.5)
    low = np.full(n, 99.5)
    close = np.full(n, 100.0)
    low[160] = 98.0
    close[161:171] = np.linspace(100.0, 118.0, 10)
    high[170] = 120.0
    starts, culminations = _proposal_indices(high, low, close, end_idx=n - 48)
    assert any(start == 160 and culmination == 170 for start, culmination in zip(starts, culminations, strict=True))


def test_sleep_pump_shape_features_are_available_at_the_proposal_cutoff() -> None:
    open_ = np.array([100.0, 100.0, 101.0, 103.0, 106.0])
    high = np.array([101.0, 102.0, 104.0, 108.0, 110.0])
    low = np.array([99.0, 99.0, 100.0, 102.0, 105.0])
    close = np.array([100.0, 101.0, 103.0, 106.0, 109.0])
    drift, efficiency, retrace, one_bar, wick = _pump_shape_features(
        open_=open_, high=high, low=low, close=close,
        sleep_start_idx=0, pump_start_idx=1, culmination_idx=4,
        pump_low_price=99.0, culmination_price=110.0,
    )
    assert drift == 0.0
    assert efficiency > 0.9
    assert retrace == 0.0
    assert 0.0 < one_bar < 1.0
    assert 0.0 <= wick < 1.0


def test_lower_support_uses_one_low_between_each_neighbouring_level_touch() -> None:
    close = np.array([90.0, 100.0, 92.0, 95.0, 100.0, 94.0, 96.0, 100.0, 90.0])
    candles = CandleSeries(
        timestamp=np.arange(len(close), dtype=np.int64) * 60_000,
        open=close.copy(),
        high=np.array([91.0, 100.0, 93.0, 96.0, 100.0, 95.0, 97.0, 100.0, 91.0]),
        low=np.array([89.0, 99.0, 90.0, 91.0, 99.0, 91.0, 93.0, 99.0, 89.0]),
        close=close,
    )
    geometry = analyze_level_geometry(candles, level_price=100.0, start_ms=0, end_ms=7 * 60_000)

    assert [touch.index for touch in geometry.touches] == [1, 4, 7]
    assert [retrace.index for retrace in geometry.retraces] == [2, 5]
    assert geometry.has_pairwise_retraces
    assert geometry.ascending_support is True
    assert geometry.retrace_depths_non_increasing is True


def test_terminal_pullback_after_the_last_level_touch_is_part_of_support() -> None:
    close = np.array([90.0, 100.0, 92.0, 100.0, 95.0, 94.0])
    candles = CandleSeries(
        timestamp=np.arange(len(close), dtype=np.int64) * 60_000,
        open=close.copy(),
        high=np.array([91.0, 100.0, 93.0, 100.0, 96.0, 95.0]),
        low=np.array([89.0, 99.0, 90.0, 99.0, 94.0, 93.0]),
        close=close,
    )
    geometry = analyze_level_geometry(
        candles,
        level_price=100.0,
        start_ms=0,
        end_ms=5 * 60_000,
        terminal_retrace_stop_index=6,
    )

    assert geometry.terminal_retrace is not None
    assert geometry.terminal_retrace.index == 5
    assert [point.index for point in geometry.support_points] == [2, 5]
    assert geometry.support_contact_indices
    assert geometry.body_above_support_share is not None
    assert 0.0 <= geometry.body_above_support_share <= 1.0


def test_high_recall_discovery_finds_repeated_resistance_without_future_candles() -> None:
    n = 20
    close = np.full(n, 100.0)
    close[3:6] = [95.0, 102.0, 108.0]
    close[6:16] = [104.0, 101.0, 106.0, 109.0, 104.0, 103.0, 107.0, 109.0, 105.0, 106.0]
    high = close + 0.5
    high[[5, 9, 13]] = [110.0, 109.8, 110.1]
    low = close - 0.5
    low[7] = 98.0
    low[11] = 100.0
    candles = CandleSeries(
        timestamp=np.arange(n, dtype=np.int64) * 60_000,
        open=close.copy(), high=high, low=low, close=close,
    )
    level = discover_level(
        candles,
        pump_start_index=3,
        culmination_index=5,
        snapshot_index=15,
        policy=DiscoveryPolicy(main_touch_merge_bars=2),
    )

    assert level is not None
    assert level.touch_indices == (5, 9, 13)
    assert level.broken is False


def test_discovery_contract_keeps_only_main_touches_and_rejects_weak_pumps() -> None:
    high = np.array([100.0, 110.0, 109.5, 110.2, 109.8, 110.4])
    assert _main_touches((1, 3, 5), high, merge_bars=3) == (5,)

    policy = DiscoveryPolicy()
    clean = {
        "pump_pct": 0.50,
        "pump_bars": 60,
        "pump_path_efficiency": 0.60,
        "pump_retrace_share": 0.10,
        "pump_single_bar_range_share": 0.20,
        "pump_low_price": 100.0,
        "culmination_price": 150.0,
    }
    assert _credible_pump(clean, policy)
    assert _cap_is_structurally_coherent(
        CapStructureMetrics(position_in_pump=0.75, initial_pullback_share=0.30, height_to_initial_pullback=0.60, overhead_resistance_layers=1),
        policy,
    )

    weak = dict(clean, pump_path_efficiency=0.25, pump_retrace_share=0.45)
    assert not _credible_pump(weak, policy)
    assert not _cap_is_structurally_coherent(
        CapStructureMetrics(position_in_pump=0.45, initial_pullback_share=0.30, height_to_initial_pullback=0.60, overhead_resistance_layers=1),
        policy,
    )
    assert not _cap_is_structurally_coherent(
        CapStructureMetrics(position_in_pump=0.75, initial_pullback_share=0.30, height_to_initial_pullback=0.30, overhead_resistance_layers=1),
        policy,
    )
    assert not _cap_is_structurally_coherent(
        CapStructureMetrics(position_in_pump=0.75, initial_pullback_share=0.30, height_to_initial_pullback=0.60, overhead_resistance_layers=4),
        policy,
    )
