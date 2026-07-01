from __future__ import annotations

from datetime import datetime, timezone
import io
import json
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd
import pytest

from anomaly_science.market_context.perp_crowding import (
    PERP_CROWDING_ARCHIVE_SCHEMA_VERSION,
    attach_perp_crowding_context,
    build_perp_crowding_features,
    perp_crowding_feature_names,
)
from anomaly_science.market_context.perp_crowding_archive import (
    derive_event_perp_crowding_scope,
    parse_funding_rate_zip,
    parse_premium_index_zip,
)
from anomaly_science.strategy.pump_fade.market_context import (
    PUMP_FADE_PERP_CROWDING_CONTEXT,
)
from anomaly_science.strategy.pump_fade.market_context_probability import (
    load_pump_fade_market_context_probability_config,
)


def _zip_csv(name: str, content: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, content)
    return buffer.getvalue()


def _premium(symbol: str = "TESTUSDT", rows: int = 300) -> pd.DataFrame:
    available = np.arange(1, rows + 1, dtype=np.int64) * 60_000
    return pd.DataFrame(
        {
            "symbol": symbol,
            "premium_source_time_ms": available - 60_000,
            "premium_available_time_ms": available,
            "premium_close": np.linspace(-0.002, 0.003, rows),
            "archive_schema_version": PERP_CROWDING_ARCHIVE_SCHEMA_VERSION,
        }
    )


def _funding(symbol: str = "TESTUSDT") -> pd.DataFrame:
    source = np.array([60_000, 8 * 60 * 60_000, 16 * 60 * 60_000, 24 * 60 * 60_000])
    return pd.DataFrame(
        {
            "symbol": symbol,
            "funding_source_time_ms": source,
            "funding_available_time_ms": source + 60_000,
            "funding_rate": [0.0001, 0.0002, 0.0003, 0.0004],
            "funding_interval_hours": 8.0,
            "archive_schema_version": PERP_CROWDING_ARCHIVE_SCHEMA_VERSION,
        }
    )


def test_premium_and_funding_archive_parsers_apply_availability_contract() -> None:
    premium = parse_premium_index_zip(
        _zip_csv(
            "TESTUSDT-1m.csv",
            "open_time,close_time,close\n0,59999,0.001\n",
        ),
        symbol="TESTUSDT",
    )
    funding = parse_funding_rate_zip(
        _zip_csv(
            "TESTUSDT-fundingRate.csv",
            "calc_time,funding_interval_hours,last_funding_rate\n1000,8,0.0001\n",
        ),
        symbol="TESTUSDT",
        publication_lag_minutes=1,
    )

    assert premium.iloc[0]["premium_available_time_ms"] == 60_000
    assert funding.iloc[0]["funding_available_time_ms"] == 61_000
    assert premium.iloc[0]["archive_schema_version"] == PERP_CROWDING_ARCHIVE_SCHEMA_VERSION


def test_perp_crowding_features_and_join_are_causal_complete_case() -> None:
    config = PUMP_FADE_PERP_CROWDING_CONTEXT
    premium_source = _premium(rows=1_600)
    premium_features, funding_features = build_perp_crowding_features(
        premium_source, _funding(), symbol="TESTUSDT", config=config
    )
    snapshot = 1_500 * 60_000
    rows = pd.DataFrame(
        {
            "symbol": ["TESTUSDT"],
            "snapshot_time_ms": [snapshot],
            "ignition_time_ms": [1_480 * 60_000],
        }
    )

    result = attach_perp_crowding_context(
        rows, (("TESTUSDT", premium_features, funding_features),), config=config
    ).iloc[0]

    assert bool(result["perp_crowding_complete"])
    assert result["premium_available_time_ms"] <= snapshot
    assert result["ignition_premium_available_time_ms"] <= result["ignition_time_ms"]
    assert result["funding_available_time_ms"] <= snapshot
    expected_change = premium_source.iloc[1499]["premium_close"] - premium_source.iloc[1494]["premium_close"]
    assert result["symbol_premium_change_5m"] == pytest.approx(expected_change)
    assert result["symbol_premium_change_since_ignition"] == pytest.approx(
        premium_source.iloc[1499]["premium_close"] - premium_source.iloc[1479]["premium_close"]
    )
    assert result["symbol_funding_rate_change"] == pytest.approx(0.0001)
    assert result["symbol_funding_rate_mean_3"] == pytest.approx(0.0003)


def test_perp_crowding_prefix_ignores_future_archive_tail() -> None:
    config = PUMP_FADE_PERP_CROWDING_CONTEXT
    cutoff = 280
    base = _premium(rows=300)
    altered = base.copy()
    altered.loc[altered.index >= cutoff, "premium_close"] = 10.0
    base_features = build_perp_crowding_features(
        base.iloc[:cutoff], _funding(), symbol="TESTUSDT", config=config
    )
    altered_features = build_perp_crowding_features(
        altered.iloc[:cutoff], _funding(), symbol="TESTUSDT", config=config
    )

    pd.testing.assert_frame_equal(base_features[0], altered_features[0])
    pd.testing.assert_frame_equal(base_features[1], altered_features[1])


def test_partial_perp_archive_preserves_premium_and_marks_incomplete() -> None:
    config = PUMP_FADE_PERP_CROWDING_CONTEXT
    premium_features, funding_features = build_perp_crowding_features(
        _premium(),
        _funding().iloc[0:0],
        symbol="TESTUSDT",
        config=config,
    )
    rows = pd.DataFrame(
        {
            "symbol": ["TESTUSDT"],
            "snapshot_time_ms": [280 * 60_000],
            "ignition_time_ms": [260 * 60_000],
        }
    )

    result = attach_perp_crowding_context(
        rows,
        (("TESTUSDT", premium_features, funding_features),),
        config=config,
    ).iloc[0]

    assert bool(result["has_symbol_premium_context"])
    assert not bool(result["has_symbol_funding_context"])
    assert not bool(result["perp_crowding_complete"])
    assert np.isfinite(result["symbol_premium_index"])
    assert pd.isna(result["symbol_funding_rate"])


def test_perp_crowding_scope_includes_registered_history(tmp_path: Path) -> None:
    def ts(value: str) -> int:
        return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp() * 1000)

    path = tmp_path / "rows.parquet"
    pd.DataFrame(
        {
            "symbol": ["TESTUSDT"],
            "snapshot_time_ms": [ts("2026-01-02T01:00:00")],
            "ignition_time_ms": [ts("2026-01-02T00:30:00")],
        }
    ).to_parquet(path, index=False)
    scope = derive_event_perp_crowding_scope(
        (path,),
        context=PUMP_FADE_PERP_CROWDING_CONTEXT,
        symbol_column="symbol",
        snapshot_time_column="snapshot_time_ms",
        anchor_time_column="ignition_time_ms",
    )[0]

    assert scope.premium_days == (
        datetime(2026, 1, 1).date(),
        datetime(2026, 1, 2).date(),
    )
    assert "2025-12" in scope.funding_months
    assert "2026-01" in scope.funding_months


def test_registered_perp_crowding_protocol_matches_core_family() -> None:
    path = Path("research/pump_fade_perp_crowding_probability.json")
    config = load_pump_fade_market_context_probability_config(path)
    raw = json.loads(path.read_text(encoding="utf-8"))

    assert config.context_features == perp_crowding_feature_names(
        PUMP_FADE_PERP_CROWDING_CONTEXT
    )
    assert config.required_true_columns == ("perp_crowding_complete",)
    assert raw["context_features"] == list(config.context_features)
