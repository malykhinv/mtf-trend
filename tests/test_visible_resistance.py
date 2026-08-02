from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from anomaly_science.annotation.boundary.store import BoundaryLabelStore, normalize_boundary_label
from anomaly_science.annotation.boundary.ui import BOUNDARY_LABELER_HTML
from anomaly_science.strategy.visible_resistance.candidates import (
    BOUNDARY_CANDIDATE_SCHEMA_VERSION,
    BoundaryCandidateConfig,
    build_boundary_review_candidates,
)


def _daily_fixture() -> pd.DataFrame:
    rows = []
    dates = pd.date_range("2025-01-01", periods=55, freq="D", tz="UTC")
    for symbol_index in range(12):
        symbol = f"C{symbol_index:02d}USDT"
        for day_index, day in enumerate(dates):
            base = 100.0 + symbol_index * 3.0 + day_index * (0.08 + symbol_index * 0.002)
            range_scale = 1.0 + 0.25 * ((day_index + symbol_index) % 7 == 0)
            quote_volume = 20_000_000.0 - symbol_index * 500_000.0
            quote_volume *= 1.0 + 0.8 * ((day_index + symbol_index) % 9 == 0)
            rows.append(
                {
                    "date": day,
                    "symbol": symbol,
                    "open": base * 0.995,
                    "high": base * (1.0 + 0.012 * range_scale),
                    "low": base * (1.0 - 0.010 * range_scale),
                    "close": base,
                    "quote_volume": quote_volume,
                    "available_time_ms": int((day + pd.Timedelta(days=1)).timestamp() * 1_000),
                    "complete_daily_bar": True,
                }
            )
    return pd.DataFrame(rows)


def _master_fixture() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": [f"C{index:02d}USDT" for index in range(12)],
            "quote_asset": "USDT",
            "is_stablecoin": False,
            "is_leveraged_token": False,
        }
    )


def test_candidate_sampler_has_no_resistance_detector_or_future_window(tmp_path: Path) -> None:
    daily_path = tmp_path / "daily.parquet"
    master_path = tmp_path / "master.parquet"
    output_dir = tmp_path / "visible"
    _daily_fixture().to_parquet(daily_path, index=False)
    _master_fixture().to_parquet(master_path, index=False)

    frame = build_boundary_review_candidates(
        daily_path=daily_path,
        master_path=master_path,
        output_dir=output_dir,
        config=BoundaryCandidateConfig(
            maximum_liquid_symbols=10,
            symbol_cooldown_days=1,
            maximum_events=12,
        ),
    )

    assert len(frame) == 12
    assert frame["candidate_schema_version"].eq(BOUNDARY_CANDIDATE_SCHEMA_VERSION).all()
    assert set(frame["sampling_stratum"]) == {"salient", "neutral_control"}
    assert frame["review_end_ms"].lt(frame["snapshot_time_ms"]).all()
    assert frame["feature_cutoff_time_ms"].le(frame["snapshot_time_ms"]).all()
    assert not any("level" in column or "boundary_price" in column for column in frame.columns)
    assert (output_dir / "labels.jsonl").read_text(encoding="utf-8") == ""
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["declared_is_start"] == "2025-01-01"
    assert manifest["declared_is_end"] == "2025-12-31"
    assert manifest["full_calendar_year_available"] is False


def test_boundary_label_requires_completed_independent_reactions(tmp_path: Path) -> None:
    start = pd.Timestamp("2025-01-01", tz="UTC")
    timestamps = [int((start + pd.Timedelta(hours=index)).timestamp() * 1_000) for index in range(12)]
    candles = {
        "timestamp": timestamps,
        "open": [100.0] * 12,
        "high": [101.0, 102.0, 105.0, 103.0, 102.0, 104.8, 103.0, 101.0, 104.9, 103.0, 102.0, 101.0],
        "low": [99.0, 100.0, 102.0, 98.0, 99.0, 102.0, 97.0, 98.0, 102.0, 96.0, 97.0, 98.0],
        "close": [100.0, 101.0, 104.0, 99.0, 101.0, 104.0, 98.0, 100.0, 104.0, 97.0, 99.0, 100.0],
        "quote_volume": [1_000.0] * 12,
    }
    snapshot_ms = int((start + pd.Timedelta(hours=12)).timestamp() * 1_000)
    candidate = {
        "event_id": "event-1",
        "symbol": "AAAUSDT",
        "snapshot_time_ms": snapshot_ms,
        "feature_cutoff_time_ms": snapshot_ms,
        "review_start_ms": timestamps[0],
    }
    reactions = [
        {
            "touch_time_ms": timestamps[2],
            "touch_price": 105.0,
            "rejection_time_ms": timestamps[3],
            "rejection_price": 98.0,
        },
        {
            "touch_time_ms": timestamps[5],
            "touch_price": 104.8,
            "rejection_time_ms": timestamps[6],
            "rejection_price": 97.0,
        },
    ]
    store = BoundaryLabelStore(tmp_path / "labels.jsonl")
    saved = store.append(
        {
            "event_id": "event-1",
            "tf": "1h",
            "status": "clear_boundary",
            "comment": "two visible independent reactions",
            "reactions": reactions,
        },
        candidate=candidate,
        candles=candles,
        allowed_tfs={"1h"},
    )

    assert saved["metrics"]["reaction_count"] == 2
    assert saved["metrics"]["duration_hours"] == 3.0
    assert saved["metrics"]["minimum_rejection_pct"] > 0.06
    assert saved["metrics"]["zone_thickness_bps"] < 20
    assert store.read_effective()["event-1"]["status"] == "clear_boundary"

    with pytest.raises(ValueError, match="at least two"):
        normalize_boundary_label(
            {
                "event_id": "event-1",
                "tf": "1h",
                "status": "clear_boundary",
                "reactions": reactions[:1],
            },
            candidate=candidate,
            candles=candles,
            allowed_tfs={"1h"},
        )
    future = [dict(reactions[0]), dict(reactions[1])]
    future[1]["rejection_time_ms"] = snapshot_ms
    with pytest.raises(ValueError, match="before snapshot"):
        normalize_boundary_label(
            {
                "event_id": "event-1",
                "tf": "1h",
                "status": "clear_boundary",
                "reactions": future,
            },
            candidate=candidate,
            candles=candles,
            allowed_tfs={"1h"},
        )


def test_boundary_ui_is_dedicated_and_trade_free() -> None:
    assert "только граница и реакции · без сделок" in BOUNDARY_LABELER_HTML
    assert "первый клик — high теста, второй — low отката" in BOUNDARY_LABELER_HTML
    assert "/api/boundary/label" in BOUNDARY_LABELER_HTML
    assert "Хорошая граница" in BOUNDARY_LABELER_HTML
    assert "Нет границы" in BOUNDARY_LABELER_HTML
    assert "entry" not in BOUNDARY_LABELER_HTML.lower()
    assert "stop" not in BOUNDARY_LABELER_HTML.lower()
    assert "ms:Number(refs.xa.p2l(" in BOUNDARY_LABELER_HTML
    assert "d2c(new Date(ms))" not in BOUNDARY_LABELER_HTML
    assert "grid-template-columns:minmax(300px,43vw) minmax(0,1fr)" in BOUNDARY_LABELER_HTML


def test_calendar_is_contract_is_not_redefinable() -> None:
    with pytest.raises(ValueError, match="calendar year 2025"):
        BoundaryCandidateConfig(analysis_start=date(2025, 6, 1))
