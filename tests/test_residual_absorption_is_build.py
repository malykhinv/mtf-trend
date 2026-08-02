from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd

from anomaly_science.strategy.residual_absorption.is_build import (
    build_cross_section_stage1,
    build_symbol_5m_is,
)
from anomaly_science.strategy.residual_absorption.spec import ResidualAbsorptionResearchSpec
from anomaly_science.universe.session_liquidity import SessionLiquidityUniverseConfig


def _minute_rows(
    *,
    day: str,
    quote_volume: float,
    price_slope: float,
) -> list[dict[str, float | int]]:
    start = pd.Timestamp(f"{day}T08:00:00Z")
    return [
        {
            "timestamp": int((start + pd.Timedelta(minutes=offset)).timestamp() * 1_000),
            "close": 100.0 + price_slope * offset,
            "quote_volume": quote_volume,
        }
        for offset in range(300)
    ]


def _write_symbol(path: Path, *, quote_volume: float, price_slope: float) -> None:
    rows: list[dict[str, float | int]] = []
    rows.extend(_minute_rows(day="2025-08-01", quote_volume=quote_volume, price_slope=price_slope))
    rows.extend(_minute_rows(day="2025-08-02", quote_volume=quote_volume, price_slope=price_slope))
    rows.extend(_minute_rows(day="2025-08-03", quote_volume=2 * quote_volume, price_slope=price_slope))
    pd.DataFrame(rows).to_parquet(path, index=False)


def _small_spec() -> ResidualAbsorptionResearchSpec:
    universe = SessionLiquidityUniverseConfig(
        history_same_type_sessions=3,
        minimum_core_history_sessions=2,
        minimum_expansion_history_sessions=2,
        core_top_n=5,
        expansion_top_n=2,
        expansion_liquidity_rank_ceiling=5,
    )
    return replace(ResidualAbsorptionResearchSpec(), universe=universe)


def test_symbol_5m_build_has_prior_session_and_elapsed_baselines(tmp_path: Path) -> None:
    source = tmp_path / "AAAUSDT.parquet"
    target = tmp_path / "out" / "AAAUSDT.parquet"
    _write_symbol(source, quote_volume=100.0, price_slope=0.01)

    stats = build_symbol_5m_is(
        source,
        output_path=target,
        spec=_small_spec(),
    )
    frame = pd.read_parquet(target)
    day3 = frame.loc[frame["utc_day"].eq(frame["utc_day"].max())]

    assert stats.minute_rows == 900
    assert stats.five_minute_rows == 180
    assert stats.last_snapshot_time_ms < pd.Timestamp("2026-01-01T00:00:00Z").timestamp() * 1_000
    assert day3.iloc[0]["prior_session_history_count"] == 2
    assert day3.iloc[0]["prior_session_quote_volume_median"] == 30_000.0
    assert day3.iloc[0]["prior_elapsed_quote_volume_median"] == 500.0
    assert day3.iloc[0]["current_session_quote_volume"] == 1_000.0
    assert day3.iloc[0]["current_activity_ratio"] == 2.0
    assert frame["feature_cutoff_time_ms"].le(frame["snapshot_time_ms"]).all()


def test_symbol_5m_build_is_invariant_to_oos_tail(tmp_path: Path) -> None:
    source = tmp_path / "AAAUSDT.parquet"
    _write_symbol(source, quote_volume=100.0, price_slope=0.01)
    base_path = tmp_path / "base.parquet"
    build_symbol_5m_is(source, output_path=base_path, spec=_small_spec())

    frame = pd.read_parquet(source)
    oos = pd.DataFrame(
        _minute_rows(day="2026-01-01", quote_volume=10**12, price_slope=10**6)
    )
    pd.concat([frame, oos], ignore_index=True).to_parquet(source, index=False)
    replay_path = tmp_path / "replay.parquet"
    build_symbol_5m_is(source, output_path=replay_path, spec=_small_spec())

    pd.testing.assert_frame_equal(pd.read_parquet(base_path), pd.read_parquet(replay_path))


def test_cross_section_build_ranks_core_and_writes_compact_factor(tmp_path: Path) -> None:
    symbol_dir = tmp_path / "symbols"
    output_dir = tmp_path / "stage1"
    symbol_dir.mkdir()
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    spec = _small_spec()
    for index, symbol in enumerate(
        ("AAAUSDT", "BBBUSDT", "CCCUSDT", "BTCUSDT", "ETHUSDT"),
        start=1,
    ):
        source = raw_dir / f"{symbol}.parquet"
        _write_symbol(source, quote_volume=100.0 * index, price_slope=0.005 * index)
        build_symbol_5m_is(
            source,
            output_path=symbol_dir / f"{symbol}.parquet",
            spec=spec,
        )

    cross_path, factor_path = build_cross_section_stage1(
        symbol_dir=symbol_dir,
        output_dir=output_dir,
        spec=spec,
    )
    cross = pd.read_parquet(cross_path)
    factor = pd.read_parquet(factor_path)
    last_snapshot = cross["snapshot_time_ms"].max()
    latest = cross.loc[cross["snapshot_time_ms"].eq(last_snapshot)]
    latest_factor = factor.loc[factor["snapshot_time_ms"].eq(last_snapshot)].iloc[0]

    assert latest["core_eligible"].sum() == 5
    assert latest_factor["cross_section_symbol_count"] == 3
    assert latest_factor["feature_cutoff_time_ms"] <= latest_factor["snapshot_time_ms"]
    assert cross["candidate_eligible"].dtype == bool
