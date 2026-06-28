from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from anomaly_science.strategy.pump_fade.builder import (
    _BarrierIndex,
    _race,
    PumpFadeBuildError,
    build_pump_fade_symbol,
    build_pump_fade_symbol_result,
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
    assert "oi_change_60m" in result.columns


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
        ]
    )

    assert result == 0
    assert captured["limit_symbols"] == 30
    assert captured["progress_every"] == 5


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
    assert (tmp_path / "decisions.metadata.json").is_file()
    assert (tmp_path / "decisions.manifest.json").is_file()
    metadata = json.loads((tmp_path / "decisions.metadata.json").read_text(encoding="utf-8"))
    assert metadata["strategy"]["strategy_contract_version"] == "horizon_free_event_strategy_v1"
    assert metadata["strategy"]["execution_policy_version"] == "pump_fade_structural_execution_v1"
    assert metadata["strategy"]["target_policies"][0]["close_fraction_grid"] == [0.25, 0.5, 0.75, 1.0]
    assert "max_1m_high_return" in metadata["strategy"]["custom_feature_names"]


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
