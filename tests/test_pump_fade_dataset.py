from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from anomaly_science.strategy.pump_fade.builder import (
    PUMP_FADE_ONLINE_STATE_SCHEMA_VERSION,
    _BarrierIndex,
    _block_rolling_median,
    _contiguous_block_ends,
    _contiguous_block_starts,
    _race,
    PumpFadeBuildError,
    build_pump_fade_decisions_with_quality,
    build_pump_fade_online_symbol,
    build_pump_fade_symbol,
    build_pump_fade_symbol_result,
    resolve_pump_fade_cache_universe,
)
from anomaly_science.strategy.pump_fade.config import PumpFadeDecisionConfig
from anomaly_science.strategy.pump_fade.run import (
    PumpFadeDataQualityPolicy,
    run_pump_fade_dataset_build,
)
from anomaly_science.strategy.pump_fade.nature import (
    build_pump_fade_nature_rows,
    run_pump_fade_nature_projection,
)
from anomaly_science.strategy.pump_fade.spec import PUMP_FADE_STRATEGY
from anomaly_science.strategy.pump_fade.state_lattice import (
    PUMP_FADE_STATE_LATTICE_ORDINALS,
    build_pump_fade_state_lattice_rows,
)


def _config() -> PumpFadeDecisionConfig:
    return PumpFadeDecisionConfig(
        baseline_window_minutes=30,
        baseline_min_periods=10,
        activity_ignition_multiple=10.0,
        quiet_peak_fraction=0.25,
        quiet_confirmation_minutes=5,
        maximum_period_minutes=20,
        minimum_pump_size=0.05,
        minimum_atr_multiple=3.0,
        minimum_turnover=1_000.0,
    )


@pytest.mark.parametrize(
    "config_path",
    (
        Path("research/pump_fade_nature_discovery.json"),
        Path("research/pump_fade_archetype_discovery.json"),
    ),
)
def test_active_pump_fade_model_features_are_declared_by_strategy(config_path: Path) -> None:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    configured = {
        name
        for group in ("numeric", "decision_timing_numeric", "categorical", "derived_time")
        for name in payload["features"].get(group, [])
    }
    catalog = {spec.name: spec for spec in PUMP_FADE_STRATEGY.custom_feature_catalog}

    assert len(catalog) == len(PUMP_FADE_STRATEGY.custom_feature_catalog)
    assert configured <= set(catalog)
    assert all(catalog[name].is_model_feature for name in configured)
    assert catalog["oi_available"].is_model_feature is False


def _write_market(path: Path) -> None:
    rows: list[dict[str, float | int]] = []
    price = 100.0
    for index in range(40):
        timestamp = index * 60_000
        open_ = price
        close = price + 0.02
        high = max(open_, close) + 0.04
        low = min(open_, close) - 0.04
        quote_volume = 100.0
        trade_count = 10
        if index == 14:  # last red strictly before ignition
            open_, close, high, low = 100.1, 100.0, 100.15, 99.9
            price = close
        if index == 15:
            open_, close, high, low = 100.0, 102.0, 102.2, 99.95
            quote_volume, trade_count = 1_200.0, 120
            price = close
        elif index == 16:
            open_, close, high, low = 102.0, 105.5, 106.0, 101.9
            quote_volume, trade_count = 1_200.0, 120
            price = close
        elif 17 <= index <= 21:
            open_, close, high, low = price, price - 0.01, price + 0.02, price - 0.03
            quote_volume, trade_count = 20.0, 2
            price = close
        elif index == 22:
            # Exact documented invalidation: close > anchor high, but < high*1.005.
            open_, close, high, low = price, 106.1, 106.2, price - 0.02
            quote_volume, trade_count = 100.0, 10
            price = close
        else:
            price = close
        rows.append(
            {
                "timestamp": timestamp,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "quote_volume": quote_volume,
                "trade_count": trade_count,
                "taker_buy_quote_volume": quote_volume * 0.5,
            }
        )
    pd.DataFrame(rows).to_parquet(path, index=False)


def test_builder_emits_only_new_high_decisions_with_closed_bar_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "TEST.parquet"
    _write_market(path)

    result = build_pump_fade_symbol(path, config=_config())

    assert len(result) == 1
    row = result.iloc[0]
    assert row["decision_index"] == 2
    assert row["snapshot_time_ms"] == 17 * 60_000
    assert row["feature_cutoff_time_ms"] == row["snapshot_time_ms"]
    assert row["future_start_time_ms"] == row["snapshot_time_ms"] + 60_000
    assert row["anchor_high"] == 106.0
    assert row["ignition_minute_of_hour"] == 15
    assert row["ignition_minutes_from_round_hour"] == 15
    assert row["y"] == 0
    assert row["resolution_time_ms"] == 23 * 60_000
    assert bool(row["is_nature_anchor"])
    assert bool(row["nature_label_available"])
    assert row["nature_y"] == 0
    assert row["nature_future_start_time_ms"] == 18 * 60_000
    assert row["nature_resolution_time_ms"] == 23 * 60_000
    assert bool(row["is_event_peak_decision"])
    assert row["max_1m_high_return"] == pytest.approx(106.0 / 102.0 - 1.0)
    assert row["max_1m_close_return"] == pytest.approx(105.5 / 102.0 - 1.0)
    assert 0.0 < row["event_path_efficiency"] <= 1.0
    assert "retail_frenzy_proxy" in result.columns
    assert row["cvd_schema_version"] == "pump_fade_cvd_path_v1"
    assert row["path_dynamics_schema_version"] == "pump_fade_path_dynamics_v1"
    assert row["aggtrades_dynamics_schema_version"] == "pump_fade_aggtrades_dynamics_v1"
    assert row["aggtrades_available"] == 0.0
    assert pd.isna(row["event_mean_notional_gini"])
    assert "event_return_sign_entropy" in result.columns
    assert "recent_red_fraction_3m" in result.columns
    assert "high_extension_decay_ratio" in result.columns
    assert bool(row["cvd_available"])
    assert "cvd_drawdown_from_peak" in result.columns
    assert "oi_change_60m" in result.columns
    assert pd.isna(row["price_up_oi_up_60m"])
    assert pd.isna(row["price_down_oi_down_60m"])


def test_builder_picks_up_aggtrades_sidecar_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "TEST.parquet"
    _write_market(path)
    sidecar_dir = tmp_path / "aggtrades_sidecar"
    sidecar_dir.mkdir()
    from anomaly_science.strategy.pump_fade.aggtrades_minute import (
        PUMP_FADE_AGGTRADES_MINUTE_FEATURES,
    )

    # Ignition is index 15 (t=900_000ms); event runs through index 17 (t=1_020_000ms).
    minute_timestamps = [15 * 60_000, 16 * 60_000, 17 * 60_000]
    rows = []
    for timestamp in minute_timestamps:
        row = {name: 0.0 for name in PUMP_FADE_AGGTRADES_MINUTE_FEATURES}
        row["timestamp"] = timestamp
        row["notional_gini"] = 0.42
        row["aggtrades_trade_count"] = 25.0
        rows.append(row)
    pd.DataFrame(rows).to_parquet(sidecar_dir / "TEST.parquet", index=False)

    import anomaly_science.strategy.pump_fade.builder as builder_module

    monkeypatch.setattr(builder_module, "_default_aggtrades_sidecar_dir", lambda: sidecar_dir)

    result = build_pump_fade_symbol(path, config=_config())

    assert len(result) == 1
    row = result.iloc[0]
    assert row["aggtrades_available"] == 1.0
    assert row["event_mean_notional_gini"] == pytest.approx(0.42)


def test_online_states_and_offline_labels_have_disjoint_lifecycles(
    tmp_path: Path,
) -> None:
    path = tmp_path / "TEST.parquet"
    _write_market(path)

    result = build_pump_fade_symbol_result(path, config=_config())

    assert set(
        (
            "y",
            "label_available",
            "resolution_time_ms",
            "event_peak_time_ms",
            "event_end_time_ms",
            "nature_y",
        )
    ).isdisjoint(result.online_states.columns)
    assert set(
        (
            "y",
            "label_available",
            "resolution_time_ms",
            "event_peak_time_ms",
            "event_end_time_ms",
            "nature_y",
        )
    ) <= set(result.labels.columns)
    assert set(result.online_states["online_state_schema_version"]) == {
        PUMP_FADE_ONLINE_STATE_SCHEMA_VERSION
    }
    pd.testing.assert_frame_equal(result.decisions, build_pump_fade_symbol(path, config=_config()))


def test_online_prefix_is_invariant_to_future_tail_while_labels_can_change(
    tmp_path: Path,
) -> None:
    original_path = tmp_path / "ORIGINAL.parquet"
    changed_path = tmp_path / "CHANGED.parquet"
    _write_market(original_path)
    changed = pd.read_parquet(original_path)
    changed.loc[22, ["open", "high", "low", "close"]] = [
        100.2,
        100.3,
        99.8,
        99.9,
    ]
    changed.to_parquet(changed_path, index=False)

    cutoff_ms = 17 * 60_000
    original = build_pump_fade_symbol_result(
        original_path, symbol="TEST", config=_config()
    )
    changed_result = build_pump_fade_symbol_result(
        changed_path, symbol="TEST", config=_config()
    )
    original_prefix = original.online_states.loc[
        original.online_states["snapshot_time_ms"] <= cutoff_ms
    ].reset_index(drop=True)
    changed_prefix = changed_result.online_states.loc[
        changed_result.online_states["snapshot_time_ms"] <= cutoff_ms
    ].reset_index(drop=True)

    pd.testing.assert_frame_equal(original_prefix, changed_prefix)
    assert original.labels.iloc[0]["y"] == 0
    assert changed_result.labels.iloc[0]["y"] == 1
    pd.testing.assert_frame_equal(
        original_prefix,
        build_pump_fade_online_symbol(
            original_path, symbol="TEST", config=_config()
        ),
    )


def test_early_causal_qualification_is_not_removed_by_final_period_atr(tmp_path: Path) -> None:
    path = tmp_path / "TEST.parquet"
    _write_market(path)

    result = build_pump_fade_symbol(path, config=_config())

    # Five later quiet/low-range candles make the final-period median TR small.
    # The decision remains because qualification is absorbing and evaluated as-of.
    assert not result.empty
    assert result.iloc[0]["atr_mult"] >= 3.0


def test_close_race_has_no_fixed_1440_minute_horizon() -> None:
    closes = np.full(1_600, 105.0)
    closes[1_500] = 99.0
    barriers = _BarrierIndex(closes)

    label, resolution = _race(
        barriers,
        start=1,
        stop=len(closes),
        base=100.0,
        anchor_high=110.0,
    )

    assert label == 1
    assert resolution == 1_500


def test_vectorized_barrier_tree_matches_naive_search() -> None:
    rng = np.random.default_rng(31)
    values = rng.normal(size=257)
    barriers = _BarrierIndex(values)

    for _ in range(100):
        start = int(rng.integers(0, len(values) - 1))
        stop = int(rng.integers(start + 1, len(values) + 1))
        threshold = float(rng.normal())
        below = np.flatnonzero(values[start:stop] <= threshold)
        above = np.flatnonzero(values[start:stop] > threshold)
        expected_below = None if not len(below) else start + int(below[0])
        expected_above = None if not len(above) else start + int(above[0])
        expected_last_above = None if not len(above) else start + int(above[-1])

        assert barriers.first_close_at_or_below(start, stop, threshold) == expected_below
        assert barriers.first_close_above(start, stop, threshold) == expected_above
        assert barriers.last_value_above(start, stop, threshold) == expected_last_above


def test_vectorized_contiguous_boundaries_and_rolling_median_match_reference() -> None:
    timestamps = np.asarray([0, 60, 120, 300, 360, 420], dtype=np.int64) * 1_000
    values = np.asarray([1.0, 4.0, 2.0, 8.0, np.nan, 6.0])

    assert _contiguous_block_starts(timestamps, 60_000).tolist() == [0, 0, 0, 3, 3, 3]
    assert _contiguous_block_ends(timestamps, 60_000).tolist() == [2, 2, 2, 5, 5, 5]
    observed = _block_rolling_median(
        values,
        timestamps,
        window=2,
        min_periods=1,
        interval_ms=60_000,
        exclude_current=True,
    )
    expected = np.asarray([np.nan, 1.0, 2.5, np.nan, 8.0, 8.0])
    np.testing.assert_array_equal(observed, expected)


def test_nature_projection_uses_first_features_and_final_new_high_label(
    tmp_path: Path,
) -> None:
    path = tmp_path / "TEST.parquet"
    _write_market(path)
    decisions = build_pump_fade_symbol(
        path,
        config=replace(_config(), minimum_pump_size=0.01),
    )

    nature = build_pump_fade_nature_rows(decisions)

    assert len(decisions) == 2
    assert len(nature) == 1
    assert nature.iloc[0]["snapshot_time_ms"] == decisions.iloc[0]["snapshot_time_ms"]
    assert nature.iloc[0]["nature_y"] == decisions.iloc[-1]["y"]
    assert (
        nature.iloc[0]["nature_future_start_time_ms"]
        == decisions.iloc[-1]["future_start_time_ms"]
    )
    assert nature.iloc[0]["event_peak_time_ms"] == decisions.iloc[-1]["snapshot_time_ms"]
    assert nature.iloc[0]["pump_duration_min"] == 2.0
    assert pd.isna(nature.iloc[0]["fade_duration_min"])


def test_nature_projection_emits_label_only_fade_duration_geometry(
    tmp_path: Path,
) -> None:
    path = tmp_path / "TEST.parquet"
    _write_market(path)
    decisions = build_pump_fade_symbol(
        path, config=replace(_config(), minimum_pump_size=0.01)
    )
    last_index = decisions.index[-1]
    decisions.loc[last_index, "y"] = 1
    decisions.loc[last_index, "resolution_time_ms"] = 20 * 60_000
    decisions.loc[last_index, "label_available"] = True

    nature = build_pump_fade_nature_rows(decisions)
    row = nature.iloc[0]

    assert row["pump_duration_min"] == 2.0
    assert row["fade_duration_min"] == 3.0
    assert row["total_pump_fade_duration_min"] == 5.0
    assert row["pump_to_fade_duration_ratio"] == pytest.approx(2.0 / 3.0)
    assert row["duration_asymmetry"] == pytest.approx(-0.2)
    assert row["duration_asymmetry_log_weighted"] == pytest.approx(
        -0.2 * np.log1p(5.0)
    )


def test_state_lattice_uses_only_registered_causal_event_ordinals(tmp_path: Path) -> None:
    path = tmp_path / "TEST.parquet"
    _write_market(path)
    supervised = build_pump_fade_symbol(
        path, config=replace(_config(), minimum_pump_size=0.01)
    )

    lattice = build_pump_fade_state_lattice_rows(supervised)

    assert set(lattice["state_ordinal"]) <= set(PUMP_FADE_STATE_LATTICE_ORDINALS)
    assert lattice["is_registered_state_lattice"].all()
    assert (lattice["feature_cutoff_time_ms"] <= lattice["snapshot_time_ms"]).all()
    assert (lattice["future_start_time_ms"] > lattice["snapshot_time_ms"]).all()


def test_nature_projection_run_writes_reproducible_artifacts(tmp_path: Path) -> None:
    market_path = tmp_path / "TEST.parquet"
    _write_market(market_path)
    decisions = build_pump_fade_symbol(market_path, config=_config())
    input_path = tmp_path / "decisions.parquet"
    output_path = tmp_path / "nature.parquet"
    decisions.to_parquet(input_path, index=False)

    run_pump_fade_nature_projection(input_path=input_path, output_path=output_path)

    assert output_path.is_file()
    assert (tmp_path / "nature.metadata.json").is_file()
    assert (tmp_path / "nature.manifest.json").is_file()


def test_close_race_invalidates_at_anchor_high_without_buffer() -> None:
    barriers = _BarrierIndex(np.asarray([105.0, 110.1, 99.0]))

    label, resolution = _race(
        barriers,
        start=1,
        stop=3,
        base=100.0,
        anchor_high=110.0,
    )

    assert label == 0
    assert resolution == 1


def test_unresolved_row_is_censored_at_first_market_data_gap(tmp_path: Path) -> None:
    path = tmp_path / "TEST.parquet"
    _write_market(path)
    frame = pd.read_parquet(path)
    frame.loc[22:, "timestamp"] += 60_000
    frame.to_parquet(path, index=False)

    result = build_pump_fade_symbol(path, config=_config())

    assert len(result) == 1
    assert not bool(result.iloc[0]["label_available"])
    assert pd.isna(result.iloc[0]["y"])
    assert pd.isna(result.iloc[0]["resolution_time_ms"])
    assert not bool(result.iloc[0]["nature_label_available"])
    assert pd.isna(result.iloc[0]["nature_y"])


def test_pump_fade_cli_routes_progress_to_dataset_builder(
    monkeypatch, tmp_path: Path
) -> None:
    import anomaly_science.cli as cli

    captured: dict[str, object] = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return kwargs["output_path"]

    monkeypatch.setattr(cli, "run_pump_fade_dataset_build", fake_run)
    output = tmp_path / "decisions.parquet"

    result = cli.main(
        [
            "build-pump-fade-dataset",
            "--cache-dir",
            str(tmp_path),
            "--out",
            str(output),
            "--limit-symbols",
            "30",
            "--progress-every",
            "5",
            "--max-inflight-symbols",
            "8",
        ]
    )

    assert result == 0
    assert captured["limit_symbols"] == 30
    assert captured["progress_every"] == 5
    assert captured["max_inflight_symbols"] == 8


def test_builder_materializes_invalid_market_rows_as_audited_gaps(tmp_path: Path) -> None:
    path = tmp_path / "TEST.parquet"
    _write_market(path)
    frame = pd.read_parquet(path)
    frame.loc[3, "high"] = frame.loc[3, "low"] - 1.0
    frame.to_parquet(path, index=False)

    result = build_pump_fade_symbol_result(path, config=_config())

    assert result.quality["dropped_row_count"] == 1
    assert result.quality["invalid_ohlc_row_count"] == 1
    assert result.quality["status"] == "OK_WITH_DROPPED_ROWS"


def test_dataset_run_writes_data_quality_artifact(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _write_market(cache_dir / "TEST.parquet")
    output = tmp_path / "decisions.parquet"

    run_pump_fade_dataset_build(
        cache_dir=cache_dir,
        output_path=output,
        config=_config(),
        progress_every=0,
    )

    quality = pd.read_csv(tmp_path / "decisions.data_quality.csv")
    assert quality.loc[0, "symbol"] == "TEST"
    assert quality.loc[0, "status"] == "OK"
    assert quality.loc[0, "oi_covered_decision_row_count"] == 0
    assert (tmp_path / "decisions.metadata.json").is_file()
    assert (tmp_path / "decisions.manifest.json").is_file()
    metadata = json.loads((tmp_path / "decisions.metadata.json").read_text(encoding="utf-8"))
    assert metadata["strategy"]["strategy_contract_version"] == "horizon_free_event_strategy_v1"
    assert metadata["strategy"]["execution_policy_version"] == "pump_fade_structural_execution_v1"
    assert metadata["strategy"]["target_policies"][0]["close_fraction_grid"] == [0.25, 0.5, 0.75, 1.0]
    assert "max_1m_high_return" in metadata["strategy"]["custom_feature_names"]
    assert metadata["oi_covered_row_count"] == 0
    assert metadata["oi_covered_row_fraction"] == 0.0
    assert metadata["max_inflight_symbols"] == 2
    assert metadata["worker_scheduling_contract"] == "bounded_inflight_symbol_pool_v1"
    assert metadata["lifecycle_contract"] == "online_states_and_offline_labels_separate_v1"
    online = pd.read_parquet(output)
    labels = pd.read_parquet(tmp_path / "decisions.labels.parquet")
    supervised = pd.read_parquet(tmp_path / "decisions.supervised.parquet")
    assert "y" not in online
    assert "y" in labels
    assert "y" in supervised
    strategy_quality = pd.read_csv(tmp_path / "strategy_data_quality.csv")
    missingness = pd.read_csv(tmp_path / "feature_missingness_report.csv")
    rejection_summary = pd.read_csv(tmp_path / "dataset_rejection_summary.csv")
    assert strategy_quality.loc[0, "strategy_name"] == "pump_fade_close_race_v1"
    oi_state = missingness.loc[
        missingness["feature_name"] == "price_up_oi_up_60m"
    ].iloc[0]
    assert oi_state["missing_fraction"] == 1.0
    assert oi_state["availability_flag"] == "oi_available"
    assert oi_state["status"] == "MISSING_OPTIONAL"
    prior_memory = missingness.loc[
        missingness["feature_name"] == "prior_1_current_vs_peak"
    ].iloc[0]
    assert prior_memory["availability_flag"] == "has_prior_1_resolved"
    assert prior_memory["status"] == "MISSING_OPTIONAL"
    assert rejection_summary.iloc[-1]["check_name"] == "run_valid"
    assert rejection_summary.iloc[-1]["status"] == "PASS"


def test_parallel_symbol_builder_is_deterministic(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _write_market(cache_dir / "AAA.parquet")
    _write_market(cache_dir / "BBB.parquet")

    sequential = build_pump_fade_decisions_with_quality(
        cache_dir=cache_dir, config=_config(), workers=1
    )
    parallel = build_pump_fade_decisions_with_quality(
        cache_dir=cache_dir, config=_config(), workers=2, max_inflight_symbols=2
    )

    pd.testing.assert_frame_equal(parallel[0], sequential[0])
    pd.testing.assert_frame_equal(parallel[1], sequential[1])


def test_parallel_symbol_builder_rejects_unbounded_or_underfilled_inflight_pool(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _write_market(cache_dir / "AAA.parquet")
    _write_market(cache_dir / "BBB.parquet")

    with pytest.raises(PumpFadeBuildError, match="max_inflight_symbols must be positive"):
        build_pump_fade_decisions_with_quality(
            cache_dir=cache_dir,
            config=_config(),
            workers=2,
            max_inflight_symbols=0,
        )

    with pytest.raises(PumpFadeBuildError, match="greater than or equal to workers"):
        build_pump_fade_decisions_with_quality(
            cache_dir=cache_dir,
            config=_config(),
            workers=2,
            max_inflight_symbols=1,
        )


def test_dataset_builder_excludes_delivery_contracts_from_perpetual_universe(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _write_market(cache_dir / "AAAUSDT.parquet")
    _write_market(cache_dir / "BTCUSDT_250627.parquet")

    decisions, quality = build_pump_fade_decisions_with_quality(
        cache_dir=cache_dir, config=_config()
    )
    universe = resolve_pump_fade_cache_universe(cache_dir)

    assert set(decisions["symbol"]) == {"AAAUSDT"}
    assert quality["symbol"].tolist() == ["AAAUSDT"]
    assert universe.excluded_delivery_symbols == ("BTCUSDT_250627",)


def test_dataset_run_fails_closed_when_dropped_rows_exceed_policy(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    path = cache_dir / "TEST.parquet"
    _write_market(path)
    frame = pd.read_parquet(path)
    frame.loc[3, "quote_volume"] = float("nan")
    frame.to_parquet(path, index=False)
    output = tmp_path / "decisions.parquet"
    output.write_bytes(b"stale artifact")

    with pytest.raises(PumpFadeBuildError, match="dropped market row fraction"):
        run_pump_fade_dataset_build(
            cache_dir=cache_dir,
            output_path=output,
            config=_config(),
            quality_policy=PumpFadeDataQualityPolicy(
                max_rejected_symbol_fraction=1.0,
                max_dropped_market_row_fraction=0.0,
            ),
            progress_every=0,
        )

    assert not output.exists()
    quality = pd.read_csv(tmp_path / "decisions.data_quality.csv")
    assert quality.loc[0, "dropped_row_count"] == 1
    rejection_summary = pd.read_csv(tmp_path / "dataset_rejection_summary.csv")
    assert rejection_summary.iloc[-1]["check_name"] == "run_valid"
    assert rejection_summary.iloc[-1]["status"] == "FAIL"


def test_dataset_builder_rejects_empty_cache(tmp_path: Path) -> None:
    with pytest.raises(PumpFadeBuildError, match="no parquet symbol caches"):
        run_pump_fade_dataset_build(
            cache_dir=tmp_path,
            output_path=tmp_path / "decisions.parquet",
            config=_config(),
            progress_every=0,
        )


def test_dataset_progress_is_console_encoding_safe_for_unicode_symbol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _write_market(cache_dir / "币安人生USDT.parquet")
    messages: list[str] = []

    def ascii_only_print(message: str, *, flush: bool) -> None:
        del flush
        message.encode("cp1252")
        messages.append(message)

    monkeypatch.setattr("builtins.print", ascii_only_print)

    run_pump_fade_dataset_build(
        cache_dir=cache_dir,
        output_path=tmp_path / "decisions.parquet",
        config=_config(),
        progress_every=1,
    )

    assert messages == ["pump-fade dataset: 1/1 symbols; rows=1"]
