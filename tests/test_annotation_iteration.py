from __future__ import annotations

import inspect
import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from anomaly_science.annotation.app import AnnotationStrategyApp
from anomaly_science.annotation.desk.result_annotations import ResultAnnotationStore
from anomaly_science.annotation.desk.structural_trades import StructuralTradeRepository
from anomaly_science.annotation.iteration import AnnotationIterationConfig, run_annotation_iteration, summarize_annotation_state
from anomaly_science.annotation.level_labeler import DEFAULT_TF_MINUTES, LABELER_HTML, LevelLabelerHandler, LevelLabelerServer
from anomaly_science.annotation.schemas import validate_label_payload


def _candidate_rows() -> list[dict[str, object]]:
    return [
        {
            "event_id": "e1_5m",
            "symbol": "AAAUSDT",
            "tf": "5m",
            "review_start_ms": 1_000,
            "review_end_ms": 10_000,
            "pump_start_ms": 2_000,
            "culmination_ms": 5_000,
        },
        {
            "event_id": "e1_15m",
            "symbol": "AAAUSDT",
            "tf": "15m",
            "review_start_ms": 1_000,
            "review_end_ms": 10_000,
            "pump_start_ms": 2_000,
            "culmination_ms": 5_000,
        },
        {
            "event_id": "e2_5m",
            "symbol": "BBBUSDT",
            "tf": "5m",
            "review_start_ms": 20_000,
            "review_end_ms": 30_000,
            "pump_start_ms": 21_000,
            "culmination_ms": 25_000,
        },
    ]


def _write_fixture_project(root: Path) -> AnnotationStrategyApp:
    base = root / ".output" / "results" / "triple_tap_v1" / "manual_pump_review"
    cache = root / ".output" / "market" / "binance_vision" / "um_futures" / "enriched_1m"
    base.mkdir(parents=True)
    cache.mkdir(parents=True)
    candidates = base / "pump_review_candidates.parquet"
    labels = base / "pump_level_labels.jsonl"
    pd.DataFrame(_candidate_rows()).to_parquet(candidates, index=False)
    return AnnotationStrategyApp(
        strategy_id="triple_tap_manual_pump_review",
        title="Triple-tap pump review",
        candidates_path=candidates,
        labels_path=labels,
        cache_dir=cache,
    )


def test_structural_trade_repository_exposes_protected_stop_ledger(tmp_path: Path) -> None:
    root = tmp_path / ".output" / "research" / "binance_usdm_trend_is_2025_v1"
    root.mkdir(parents=True)
    trade = {
        "trade_id": "BTCUSDT:event:15m",
        "symbol": "BTCUSDT",
        "tf": "15m",
        "fill_time_ms": int(pd.Timestamp("2025-09-01 09:30:00Z").timestamp() * 1_000),
        "entry_price": 100.0,
        "crossing_minute_of_day": 570,
        "stop_updates": [{"kind": "initial", "price": 99.0, "time_ms": 1}],
    }
    (root / "structural_protected_stop_trades.json").write_text(
        json.dumps([trade]), encoding="utf-8"
    )
    pd.DataFrame([{"tf": "15m", "trades": 1, "mean_net_r": 0.2}]).to_csv(
        root / "structural_protected_stop_policy_audit.csv", index=False
    )

    repository = StructuralTradeRepository(tmp_path)
    loaded = repository.trades()

    assert len(loaded) == 1
    assert loaded[0]["setup_family"] == "structural_swing_low"
    assert loaded[0]["session"] == "europe"
    assert loaded[0]["month"] == "2025-09"
    assert repository.trade_by_id(trade["trade_id"]) == loaded[0]
    assert repository.list_rows()[0]["trade_id"] == trade["trade_id"]
    assert "stop_updates" not in repository.list_rows()[0]
    assert repository.summary()[0]["trades"] == 1


def test_structural_trade_review_is_separate_and_causal(tmp_path: Path) -> None:
    root = tmp_path / ".output" / "research" / "binance_usdm_trend_is_2025_v1"
    root.mkdir(parents=True)
    fill_time_ms = int(pd.Timestamp("2025-09-01 09:30:00Z").timestamp() * 1_000)
    trade = {
        "trade_id": "BTCUSDT:event:15m",
        "symbol": "BTCUSDT",
        "tf": "15m",
        "fill_time_ms": fill_time_ms,
        "entry_price": 100.0,
    }
    (root / "structural_protected_stop_trades.json").write_text(json.dumps([trade]), encoding="utf-8")
    repository = StructuralTradeRepository(tmp_path)

    saved = repository.save_review(
        {
            "trade_id": trade["trade_id"],
            "comment": "mechanical stop is inside the visible swing",
            "correct_sl": {"anchor_time_ms": fill_time_ms - 60_000, "price": 97.0},
        }
    )

    assert saved["structural_trade_review_schema_version"] == "structural_trade_review_v1"
    assert repository.review_for_trade(trade["trade_id"])["correct_sl"]["price"] == 97.0
    assert repository.list_rows()[0]["has_result_annotation"] is True
    assert (root / "structural_trade_reviews.jsonl").exists()
    assert not (root / "result_trade_annotations.jsonl").exists()

    with pytest.raises(ValueError, match="before trade entry"):
        repository.save_review(
            {
                "trade_id": trade["trade_id"],
                "comment": "future candle must be rejected",
                "correct_sl": {"anchor_time_ms": fill_time_ms, "price": 96.0},
            }
        )
    with pytest.raises(ValueError, match="below long entry"):
        repository.save_review(
            {
                "trade_id": trade["trade_id"],
                "comment": "not a long stop",
                "correct_sl": {"anchor_time_ms": fill_time_ms - 60_000, "price": 101.0},
            }
        )


def test_desk_structural_trade_tab_draws_risk_reward_and_every_stop_update() -> None:
    assert '<nav id="workspaceTabs" role="tablist"' in LABELER_HTML
    assert 'id="workspaceTabLabeling" role="tab"' in LABELER_HTML
    assert 'id="workspaceTabResults" role="tab"' in LABELER_HTML
    assert 'id="workspaceTabFutures" role="tab"' in LABELER_HTML
    assert "function setWorkspaceMode(mode)" in LABELER_HTML
    assert "body.workspace-futures #eventPanel" in LABELER_HTML
    assert "body.workspace-futures .iteration-review-only" in LABELER_HTML
    assert "grid-template-columns: 430px minmax(0, 1fr)" in LABELER_HTML
    assert "showFuturesStructural()" in LABELER_HTML
    assert "/api/futures_structural/trades" in LABELER_HTML
    assert "resultTrade.initial_stop" in LABELER_HTML
    assert "resultTrade.mfe_price" in LABELER_HTML
    assert "fillcolor:'rgba(209,132,149,.18)'" in LABELER_HTML
    assert "fillcolor:'rgba(125,187,145,.14)'" in LABELER_HTML
    assert "resultTrade.stop_updates.slice(1).forEach" in LABELER_HTML
    assert "text:`SL" in LABELER_HTML
    assert "${fmtPrice(Number(update.price))}`" in LABELER_HTML
    assert 'id="futuresReviewComment"' in LABELER_HTML
    assert 'id="correctSlBtn"' in LABELER_HTML
    assert "function saveStructuralTradeReview()" in LABELER_HTML
    assert "/api/futures_structural/review" in LABELER_HTML
    assert "anchorTimeMs >= Number(resultTrade && resultTrade.fill_time_ms)" in LABELER_HTML
    assert "CORRECT SL" in LABELER_HTML


def test_annotation_iteration_summarizes_group_labels_and_tombstones(tmp_path: Path) -> None:
    app = _write_fixture_project(tmp_path)
    labels = [
        {"event_id": "e1_5m", "has_level": True, "family": "cap", "quality": "good", "selected_tf": "5m"},
        {"event_id": "e1_5m", "unlabeled": True},
        {"event_id": "e1_15m", "has_level": False, "family": "breakout", "quality": "bad", "selected_tf": "15m"},
    ]
    app.labels_path.write_text("\n".join(json.dumps(row) for row in labels) + "\n", encoding="utf-8")

    candidate_summary, label_progress = summarize_annotation_state(app)

    assert candidate_summary["candidate_rows"] == 3
    assert candidate_summary["candidate_groups"] == 2
    assert label_progress["effective_label_rows"] == 1
    assert label_progress["labeled_groups"] == 1
    assert label_progress["unlabeled_groups"] == 1
    assert label_progress["source_event_label_hits"] == 1
    assert label_progress["no_level_groups"] == 1
    assert label_progress["has_pump_transition_groups"] == 0
    assert label_progress["no_transition_groups"] == 1
    assert label_progress["family"] == {"breakout": 1}
    assert label_progress["labeled_tfs"] == {"15m": 1}




def test_annotation_iteration_counts_pump_transition_from_drawn_pump_fields(tmp_path: Path) -> None:
    app = _write_fixture_project(tmp_path)
    labels = [
        {
            "event_id": "e1_5m",
            "selected_tf": "5m",
            "setups": [
                {"family": "cap", "quality": "good", "has_level": False, "has_pump_transition": False},
                {
                    "family": "structure_break",
                    "quality": "ok",
                    "has_level": False,
                    "has_pump_transition": True,
                    "pump_start_ms": 2_000,
                    "pump_start_price": 1.0,
                    "culmination_ms": 5_000,
                    "culmination_price": 1.2,
                },
            ],
        },
        {
            "event_id": "e2_5m",
            "selected_tf": "5m",
            "setups": [{"family": "cap", "quality": "bad", "has_level": False, "has_pump_transition": False}],
        },
    ]
    app.labels_path.write_text("\n".join(json.dumps(row) for row in labels) + "\n", encoding="utf-8")

    _, label_progress = summarize_annotation_state(app)

    assert label_progress["labeled_groups"] == 2
    assert label_progress["has_pump_transition_groups"] == 1
    assert label_progress["no_transition_groups"] == 1
    assert label_progress["labeled_setups"] == 3


def test_run_annotation_iteration_writes_versioned_artifacts(tmp_path: Path) -> None:
    app = _write_fixture_project(tmp_path)
    app.labels_path.write_text(
        json.dumps({"event_id": "e2_5m", "has_level": True, "family": "cap", "quality": "ok", "selected_tf": "5m"}) + "\n",
        encoding="utf-8",
    )

    paths = run_annotation_iteration(
        AnnotationIterationConfig(project_root=tmp_path, run_trade_report=False),
        hooks={},
    )

    assert paths.manifest.is_file()
    assert paths.candidate_summary.is_file()
    assert paths.label_progress.is_file()
    assert paths.report_md.is_file()
    latest = json.loads(paths.latest_pointer.read_text(encoding="utf-8"))
    manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
    assert latest["run_id"] == manifest["run_id"]
    assert manifest["no_external_api_tokens_required"] is True
    assert manifest["input_hashes"]["candidates_sha256"]
    assert manifest["input_hashes"]["labels_sha256"]


def _json_request(url: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def test_api_returns_json_error_for_invalid_label_state(tmp_path: Path) -> None:
    candidates_path = tmp_path / "candidates.parquet"
    labels_path = tmp_path / "labels.jsonl"
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    pd.DataFrame(_candidate_rows()[:1]).to_parquet(candidates_path, index=False)
    labels_path.write_text("not-json\n", encoding="utf-8")

    server = LevelLabelerServer(
        ("127.0.0.1", 0),
        LevelLabelerHandler,
        candidates_path=candidates_path,
        labels_path=labels_path,
        cache_dir=cache_dir,
        tf_minutes=DEFAULT_TF_MINUTES,
        project_root=tmp_path,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        try:
            _json_request(f"{base}/api/candidates")
        except urllib.error.HTTPError as exc:
            assert exc.code == 500
            body = json.loads(exc.read().decode("utf-8"))
            assert "invalid JSONL row" in body["error"]
        else:  # pragma: no cover - the assertion above is the expected path
            raise AssertionError("invalid label JSONL must return a JSON API error")
    finally:
        server.shutdown()
        server.server_close()


def test_api_returns_json_error_for_missing_market_cache(tmp_path: Path) -> None:
    candidates_path = tmp_path / "candidates.parquet"
    labels_path = tmp_path / "labels.jsonl"
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    pd.DataFrame(_candidate_rows()[:1]).to_parquet(candidates_path, index=False)

    server = LevelLabelerServer(
        ("127.0.0.1", 0),
        LevelLabelerHandler,
        candidates_path=candidates_path,
        labels_path=labels_path,
        cache_dir=cache_dir,
        tf_minutes=DEFAULT_TF_MINUTES,
        project_root=tmp_path,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        try:
            _json_request(f"{base}/api/candles?event_id=e1_5m&tf=5m")
        except urllib.error.HTTPError as exc:
            assert exc.code == 500
            body = json.loads(exc.read().decode("utf-8"))
            assert "AAAUSDT.parquet" in body["error"]
        else:  # pragma: no cover - the assertion above is the expected path
            raise AssertionError("missing OHLCV cache must return a JSON API error")
    finally:
        server.shutdown()
        server.server_close()


def test_event_candles_can_switch_to_any_available_timeframe(tmp_path: Path) -> None:
    candidates_path = tmp_path / "candidates.parquet"
    labels_path = tmp_path / "labels.jsonl"
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    pd.DataFrame(
        [
        {
            "event_id": "evt_15m",
            "symbol": "AAAUSDT",
            "tf": "15m",
            "review_start_ms": 0,
            "review_end_ms": 300_000,
            "discovery_touch_times_ms": np.array([60_000, 180_000], dtype=np.int64),
            "discovery_quality_flags": np.array([], dtype=object),
            }
        ]
    ).to_parquet(candidates_path, index=False)
    pd.DataFrame(
        {
            "timestamp": np.arange(0, 600_000, 60_000, dtype=np.int64),
            "open": np.arange(10, dtype=float) + 1.0,
            "high": np.arange(10, dtype=float) + 1.2,
            "low": np.arange(10, dtype=float) + 0.8,
            "close": np.arange(10, dtype=float) + 1.1,
            "quote_volume": np.arange(10, dtype=float) + 100.0,
            "trade_count": np.arange(10, dtype=float) + 1_000.0,
        }
    ).to_parquet(cache_dir / "AAAUSDT.parquet", index=False)

    server = LevelLabelerServer(
        ("127.0.0.1", 0),
        LevelLabelerHandler,
        candidates_path=candidates_path,
        labels_path=labels_path,
        cache_dir=cache_dir,
        tf_minutes=DEFAULT_TF_MINUTES,
        project_root=tmp_path,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        candidates_payload = _json_request(f"{base}/api/candidates")
        assert candidates_payload["available_tfs"] == ["1m", "3m", "5m", "10m", "15m", "30m", "1h", "4h", "1d"]
        assert candidates_payload["candidates"][0]["default_tf"] == "15m"

        candles = _json_request(f"{base}/api/candles?event_id=evt_15m&tf=1m")
        assert candles["event"]["event_id"] == "evt_15m"
        assert candles["event"]["source_tf"] == "15m"
        assert candles["event"]["tf"] == "1m"
        assert candles["event"]["discovery_touch_times_ms"] == [60_000, 180_000]
        assert candles["event"]["discovery_quality_flags"] == []
        assert candles["candles"]["timestamp"] == [0, 60_000, 120_000, 180_000, 240_000, 300_000]

        try:
            _json_request(f"{base}/api/candles?event_id=evt_15m&tf=2m")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            body = json.loads(exc.read().decode("utf-8"))
            assert "unknown tf" in body["error"]
        else:  # pragma: no cover - the assertion above is the expected path
            raise AssertionError("unsupported timeframe must be rejected")
    finally:
        server.shutdown()
        server.server_close()


def test_candidate_api_serializes_parquet_array_diagnostics(tmp_path: Path) -> None:
    candidates_path = tmp_path / "candidates.parquet"
    labels_path = tmp_path / "labels.jsonl"
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    row = _candidate_rows()[0]
    row.update(
        {
            "discovery_touch_times_ms": np.array([2_000, 3_000], dtype=np.int64),
            "discovery_quality_flags": np.array([], dtype=object),
        }
    )
    pd.DataFrame([row]).to_parquet(candidates_path, index=False)

    server = LevelLabelerServer(
        ("127.0.0.1", 0),
        LevelLabelerHandler,
        candidates_path=candidates_path,
        labels_path=labels_path,
        cache_dir=cache_dir,
        tf_minutes=DEFAULT_TF_MINUTES,
        project_root=tmp_path,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        candidate = _json_request(f"{base}/api/candidates")["candidates"][0]
        assert candidate["discovery_touch_times_ms"] == [2_000, 3_000]
        assert candidate["discovery_quality_flags"] == []
    finally:
        server.shutdown()
        server.server_close()


def test_result_trade_drawings_are_saved_and_reloaded(tmp_path: Path) -> None:
    strategy_id = "triple_tap_manual_pump_review"
    candidates_path = tmp_path / "candidates.parquet"
    labels_path = tmp_path / "labels.jsonl"
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    pd.DataFrame(
        [
            {
                "event_id": "evt1",
                "symbol": "AAAUSDT",
                "tf": "1m",
                "review_start_ms": 1_000,
                "review_end_ms": 120_000,
            }
        ]
    ).to_parquet(candidates_path, index=False)
    pd.DataFrame(
        {
            "timestamp": np.arange(0, 180_000, 60_000, dtype=np.int64),
            "open": [1.0, 1.1, 1.2],
            "high": [1.2, 1.3, 1.4],
            "low": [0.9, 1.0, 1.1],
            "close": [1.1, 1.2, 1.3],
            "quote_volume": [10.0, 11.0, 12.0],
            "trade_count": [100.0, 110.0, 120.0],
        }
    ).to_parquet(cache_dir / "AAAUSDT.parquet", index=False)

    run_dir = tmp_path / ".output" / "results" / "annotation_iterations" / strategy_id / "run1"
    trade_dir = run_dir / "trade_report"
    trade_dir.mkdir(parents=True)
    trade = {
        "trade_id": "trd_00000",
        "symbol": "AAAUSDT",
        "tf": "1m",
        "fill_time_ms": 60_000,
        "exit_time_ms": 120_000,
        "entry_price": 1.2,
        "level": 1.15,
        "stop": 1.0,
        "take": 1.5,
        "net_r": 1.25,
        "outcome": "win",
        "setup_family": "cap",
        "session": "asia",
        "day": "2025-01-01",
    }
    (trade_dir / "trades.json").write_text(json.dumps([trade]), encoding="utf-8")
    latest_dir = tmp_path / ".output" / "results" / "annotation_iterations" / strategy_id
    latest_dir.mkdir(parents=True, exist_ok=True)
    (latest_dir / "latest.json").write_text(
        json.dumps({"run_id": "run1", "run_dir": str(run_dir)}),
        encoding="utf-8",
    )

    server = LevelLabelerServer(
        ("127.0.0.1", 0),
        LevelLabelerHandler,
        candidates_path=candidates_path,
        labels_path=labels_path,
        cache_dir=cache_dir,
        tf_minutes=DEFAULT_TF_MINUTES,
        strategy_id=strategy_id,
        project_root=tmp_path,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        first_drawings = {
            "level": {"startMs": 60_000, "endMs": 120_000, "price": 1.15},
            "pump": {"startMs": 0, "endMs": 60_000, "low": 1.0, "high": 1.3},
        }
        _json_request(
            f"{base}/api/result_annotation",
            {"trade_id": "trd_00000", "comment": "first note", "drawings": first_drawings},
        )
        second_drawings = {
            "level": {"startMs": 0, "endMs": 120_000, "price": 1.2},
            "sl": {"price": 1.05, "hit_ms": 120_000, "end_ms": 180_000},
        }
        _json_request(
            f"{base}/api/result_annotation",
            {"trade_id": "trd_00000", "comment": "final note", "drawings": second_drawings},
        )
        try:
            _json_request(
                f"{base}/api/result_annotation",
                {"trade_id": "missing", "comment": "bad", "drawings": {}},
            )
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
        else:  # pragma: no cover - the assertion above is the expected path
            raise AssertionError("unknown trade_id must be rejected")
        try:
            _json_request(
                f"{base}/api/result_annotation",
                {
                    "trade_id": "trd_00000",
                    "comment": "bad",
                    "drawings": {"level": {"startMs": 0, "endMs": 120_000, "price": -1}},
                },
            )
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
        else:  # pragma: no cover - the assertion above is the expected path
            raise AssertionError("invalid result drawing price must be rejected")
        try:
            _json_request(
                f"{base}/api/result_annotation",
                {"trade_id": "trd_00000", "comment": "bad", "drawings": []},
            )
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
        else:  # pragma: no cover - the assertion above is the expected path
            raise AssertionError("invalid result drawings type must be rejected")
        try:
            _json_request(
                f"{base}/api/result_annotation",
                {
                    "trade_id": "trd_00000",
                    "comment": "bad",
                    "drawings": {"swings": [{"ms": 60_000, "price": 1.3, "kind": "high"}]},
                },
            )
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
        else:  # pragma: no cover - the assertion above is the expected path
            raise AssertionError("unknown result drawing keys must be rejected")
        try:
            _json_request(
                f"{base}/api/result_annotation",
                {
                    "trade_id": "trd_00000",
                    "comment": "bad",
                    "drawings": {"exitPoint": {"ms": 60_000, "price": 1.2}},
                },
            )
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
        else:  # pragma: no cover - the assertion above is the expected path
            raise AssertionError("removed exit drawing must be rejected")
        try:
            _json_request(
                f"{base}/api/result_annotation",
                {
                    "trade_id": "trd_00000",
                    "comment": "bad",
                    "drawings": {"level": {"startMs": 0.5, "endMs": 120_000, "price": 1.0}},
                },
            )
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
        else:  # pragma: no cover - the assertion above is the expected path
            raise AssertionError("fractional result drawing timestamps must be rejected")
        try:
            _json_request(
                f"{base}/api/result_annotation",
                {
                    "trade_id": "trd_00000",
                    "comment": "bad",
                    "drawings": {"level": {"startMs": 0, "endMs": 120_000, "price": 1.0, "broken": "false"}},
                },
            )
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
        else:  # pragma: no cover - the assertion above is the expected path
            raise AssertionError("non-boolean result level broken flag must be rejected")

        trades = _json_request(f"{base}/api/iteration/trades")["trades"]
        assert trades[0]["has_result_annotation"] is True
        assert trades[0]["result_comment"] == "final note"

        candles = _json_request(f"{base}/api/iteration/trade_candles?trade_id=trd_00000")
        assert candles["result_annotation"]["comment"] == "final note"
        assert candles["result_annotation"]["result_annotation_schema_version"] == "result_trade_annotation_v1"
        assert candles["result_annotation"]["drawings"] == {
            "level": {"start_ms": 0, "end_ms": 120_000, "price": 1.2, "broken": False},
            "sl": {"price": 1.05, "hit_ms": 120_000, "end_ms": 180_000},
        }
        assert len(candles["candles"]["timestamp"]) == 3

        rows = [
            json.loads(line)
            for line in (run_dir / "result_trade_annotations.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert len(rows) == 2
        assert rows[0]["drawings"]["level"] == {"start_ms": 60_000, "end_ms": 120_000, "price": 1.15, "broken": False}
        assert rows[0]["drawings"]["pump"] == {
            "start": {"ms": 0, "price": 1.0},
            "high": {"ms": 60_000, "price": 1.3},
        }
        assert isinstance(rows[1]["saved_at_ms"], int)
        assert rows[1]["saved_at_utc"].endswith("Z")
    finally:
        server.shutdown()
        server.server_close()


def test_result_annotation_store_normalizes_legacy_rows_on_read(tmp_path: Path) -> None:
    path = tmp_path / "result_trade_annotations.jsonl"
    path.write_text(
        json.dumps(
            {
                "trade_id": "trd_legacy",
                "comment": "legacy note",
                "drawings": {
                    "level": {"startMs": 1_000, "endMs": 5_000, "price": "1.25"},
                    "pump": {"startMs": 1_000, "endMs": 4_000, "low": 1.0, "high": 1.5},
                    "swings": [{"ms": 3_000, "price": 1.4, "kind": "high"}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    row = ResultAnnotationStore(path).read_effective()["trd_legacy"]

    assert row["drawings"]["level"] == {"start_ms": 1_000, "end_ms": 5_000, "price": 1.25, "broken": False}
    assert row["drawings"]["pump"] == {
        "start": {"ms": 1_000, "price": 1.0},
        "high": {"ms": 4_000, "price": 1.5},
    }


def test_plotly_axis_date_parser_does_not_force_local_ranges_to_utc() -> None:
    match = re.search(r"function pms\(v\) \{(?P<body>.*?)\n\}", LABELER_HTML, re.S)
    assert match is not None
    body = match.group("body")
    assert "s + 'Z'" not in body
    assert "Date.parse(s)" in body
    assert "v instanceof Date" in body


def test_result_review_reloads_saved_stop_loss() -> None:
    assert "const savedSl = d.sl && Number.isFinite(Number(d.sl.price)) ? d.sl : null" in LABELER_HTML
    assert "sl = savedSl ? {price:Number(savedSl.price), hit_ms:savedSl.hit_ms ?? null, end_ms:savedSl.end_ms ?? null} : null" in LABELER_HTML
    assert "function slStartMs()" in LABELER_HTML
    assert "function slEndMs()" in LABELER_HTML
    assert "sl: sl ? {price:sl.price, hit_ms:sl.hit_ms ?? null, end_ms:slEndMs()} : null" in LABELER_HTML
    assert "if (!entry && !resultMode) sl = null" in LABELER_HTML
    assert "if (sl) {" in LABELER_HTML


def test_read_api_errors_do_not_poison_browser_state() -> None:
    assert "if (data.error) {" in LABELER_HTML
    assert "document.getElementById('list').innerHTML = `<div class=\"hint\">${esc(data.error)}</div>`;" in LABELER_HTML
    assert "document.getElementById('progress').innerText = '0/0 labeled';" in LABELER_HTML
    assert "function clearChart()" in LABELER_HTML
    assert "clearChart();\n    toast(data.error, 4200);" in LABELER_HTML
    assert "if (payload.error) {\n    current = null;" in LABELER_HTML
    assert "level = null; pumps = []; pump = null; entry = null; sl = null; zigzag = null; zzDraft = null; setups = [];" in LABELER_HTML
    assert "clearChart();\n    toast(payload.error, 4200);" in LABELER_HTML
    assert "if (payload.error) {\n    selectedTf = previousTf;\n    toast(payload.error, 4200);\n    populateTfButtons(group);" in LABELER_HTML
    assert "document.getElementById('iterationStatus').innerHTML = `<b>error</b>" in LABELER_HTML


def test_timeframe_switcher_uses_buttons_and_preserves_keyboard_navigation() -> None:
    assert '<div id="tfButtons" class="tf-switcher"' in LABELER_HTML
    assert "grid-template-columns:repeat(4, minmax(0, 1fr))" in LABELER_HTML
    assert "grid-template-rows:repeat(2, 26px)" in LABELER_HTML
    assert "function populateTfButtons(group)" in LABELER_HTML
    assert "button.dataset.tf = tf;" in LABELER_HTML
    assert "changeTf(eventTfs[next]);" in LABELER_HTML
    assert 'id="tfSelect"' not in LABELER_HTML


def test_drawing_toolbar_removes_exit_and_previews_zone_snapping() -> None:
    assert 'id="toolExit"' not in LABELER_HTML
    assert "exitPoint" not in LABELER_HTML
    assert ">Auto SL<" not in LABELER_HTML
    assert 'id="slBtn"' in LABELER_HTML
    assert 'id="toolZone"' in LABELER_HTML
    assert "function zoneExtreme(ms, y)" in LABELER_HTML
    assert "function zoneDraft(first, second)" in LABELER_HTML
    assert "boundary_mode:'extrema'" in LABELER_HTML
    assert 'id="zoneBoundary"' not in LABELER_HTML
    assert "click to place" in LABELER_HTML
    assert "if (k === 'o') toggleTool('zone');" in LABELER_HTML


def test_discovery_candidates_are_ranked_before_warned_candidates() -> None:
    assert "const discoveryTierRank = {A:0, B:1, C:2};" in LABELER_HTML
    assert "if (leftTier !== rightTier) return leftTier - rightTier;" in LABELER_HTML


def test_discovery_support_metrics_are_visible_in_the_event_tape() -> None:
    assert "discovery_support_contact_count" in LABELER_HTML
    assert "discovery_body_above_support_share" in LABELER_HTML


def test_overlay_uses_plotly_axis_transforms_not_manual_range_math() -> None:
    # The drawing overlay must map coordinates through Plotly's own axis functions
    # (l2p/d2c/p2l) so it stays pixel-locked to the candles regardless of how the
    # visible range is serialised. Parsing range strings by hand desyncs the
    # overlay from the candles by the browser timezone offset.
    assert "r.xa.l2p(xMsToLinear(r.xa, ms)) + r.xa._offset" in LABELER_HTML
    assert "r.ya.l2p(r.ya.d2c(p)) + r.ya._offset" in LABELER_HTML
    assert "r.xa.p2l(px - r.xa._offset)" in LABELER_HTML
    # Re-applying a captured/zoomed range must not re-serialise it to UTC ISO,
    # which would shift the whole view by the timezone offset.
    for assignment in re.findall(r"xRange = \[[^\]]*\]", LABELER_HTML):
        assert "toISOString" not in assignment, assignment


def test_level_and_pump_cannot_be_dragged_only_deleted() -> None:
    # Once placed, level and pump are immutable: no hit-test / drag handles remain
    # for them anywhere (they can still be removed via the object list or reset).
    for part in ("levelStart", "levelLine", "pumpStart", "pumpHigh", "pumpBox"):
        assert f"'{part}'" not in LABELER_HTML, part


def test_annotation_fields_are_grouped_and_search_has_its_own_panel() -> None:
    # The dropdowns are split by what they describe: family/quality assess the
    # event, price-snap/show-only are chart/tooling controls. Event fields must
    # come before the chart fields, and each group carries its own label.
    assert ">event assessment<" in LABELER_HTML
    assert ">chart<" in LABELER_HTML
    event_idx = LABELER_HTML.index(">event assessment<")
    chart_idx = LABELER_HTML.index(">chart<", event_idx)
    fam_idx = LABELER_HTML.index('id="family"')
    qual_idx = LABELER_HTML.index('id="quality"')
    snap_idx = LABELER_HTML.index('id="snapMode"')
    filter_idx = LABELER_HTML.index('id="filter"')
    assert event_idx < fam_idx < chart_idx and event_idx < qual_idx < chart_idx
    assert chart_idx < snap_idx and chart_idx < filter_idx

    # The candidate search lives in its own panel above the list, not mixed into
    # the annotation fields.
    search_idx = LABELER_HTML.index('id="search"')
    panel_idx = LABELER_HTML.rindex('class="panel search-panel"', 0, search_idx)
    list_idx = LABELER_HTML.index('id="list"')
    assert panel_idx < search_idx < list_idx
    assert "symbol / TF search" not in LABELER_HTML


def test_family_and_quality_are_loaded_per_setup() -> None:
    # family/quality are a per-setup assessment: applying a setup reflects its saved
    # values and defaults to unknown/bad, so nothing carries over between setups/events.
    assert "fam.value = s.family || 'unknown'" in LABELER_HTML
    assert "qual.value = s.quality || 'bad'" in LABELER_HTML


def test_structure_break_family_and_zigzag_tool_exist() -> None:
    # New "structure break" family and the swing-zigzag tool that replaces the old
    # separate swing-high / swing-low tools.
    assert 'value="structure_break"' in LABELER_HTML
    assert "toggleTool('zigzag')" in LABELER_HTML
    assert "toggleTool('swhigh')" not in LABELER_HTML
    assert "toggleTool('swlow')" not in LABELER_HTML
    # right-click finishes the zigzag polyline
    assert "finishZigzag" in LABELER_HTML
    assert "'contextmenu'" in LABELER_HTML


def test_multiple_setups_per_event_are_supported() -> None:
    # The label carries a setups[] array and the UI can switch/add/delete setups.
    assert '"setups": serializedSetups' not in LABELER_HTML  # (js object, not json)
    assert "setups: serializedSetups" in LABELER_HTML
    for fn in ("function switchSetup(", "function addSetup(", "function deleteSetup(", "function commitActiveSetup("):
        assert fn in LABELER_HTML, fn


def test_multiple_ordered_pump_waves_and_automatic_sideways_are_supported() -> None:
    assert "let pumps = [];" in LABELER_HTML
    assert "function normalizedPumps(" in LABELER_HTML
    assert "function sidewaysSegments(" in LABELER_HTML
    assert "function sleepSegment(" in LABELER_HTML
    assert "text:'SLEEP'" in LABELER_HTML
    assert "pumps = ordered;" in LABELER_HTML
    assert "pump_waves: pumpWaves" in LABELER_HTML
    assert "sideways_segments: sideways" in LABELER_HTML
    assert "text:`W${index + 1}" in LABELER_HTML


def test_multi_pump_schema_requires_ordered_non_overlapping_waves_and_exact_sideways() -> None:
    setup = {
        "family": "unknown",
        "quality": "good",
        "has_level": False,
        "has_pump_transition": True,
        "has_structure_break": False,
        "pump_start_ms": 1_000,
        "pump_start_price": 1.0,
        "culmination_ms": 2_000,
        "culmination_price": 1.2,
        "pump_waves": [
            {"wave_ordinal": 1, "start_ms": 1_000, "start_price": 1.0, "culmination_ms": 2_000, "culmination_price": 1.2},
            {"wave_ordinal": 2, "start_ms": 3_000, "start_price": 1.1, "culmination_ms": 4_000, "culmination_price": 1.4},
        ],
        "sideways_segments": [
            {"after_wave_ordinal": 1, "start_ms": 2_000, "end_ms": 3_000, "lower_price": 1.05, "upper_price": 1.22}
        ],
    }
    payload = {"event_id": "waves", "symbol": "AAAUSDT", "tf": "3m", "setups": [setup]}
    validate_label_payload(payload)

    setup["pump_waves"][1]["start_ms"] = 2_000
    with pytest.raises(ValueError, match="strictly separated"):
        validate_label_payload(payload)

    setup["pump_waves"][1]["start_ms"] = 3_000
    setup["sideways_segments"][0]["end_ms"] = 3_001
    with pytest.raises(ValueError, match="next pump start"):
        validate_label_payload(payload)


def test_structure_break_auto_sl_uses_last_zigzag_low() -> None:
    assert "zigzagLastSwingLow" in LABELER_HTML
    assert "family === 'structure_break'" in LABELER_HTML


def test_no_level_and_no_transition_are_inferred_from_missing_drawings() -> None:
    # There are no separate negative-action buttons anymore: Save persists the
    # current setup and the absence of pump/level objects is the signal.
    assert ">No transition<" not in LABELER_HTML
    assert ">No level<" not in LABELER_HTML
    assert "function saveNoLevel(" not in LABELER_HTML
    assert "function saveNoSleepPump(" not in LABELER_HTML
    assert "if (k === 'n') saveNoLevel" not in LABELER_HTML
    assert "if (k === 't') saveNoSleepPump" not in LABELER_HTML
    assert "function saveLabel(" in LABELER_HTML
    assert "no pump drawn => no transition; no level drawn => no level" in LABELER_HTML
    assert "has_pump_transition: hasPump" in LABELER_HTML


def test_structure_break_entry_is_derived_from_zigzag_without_creating_manual_level() -> None:
    # Structure-break entry is the close above the last zigzag swing high.  It is
    # rendered/serialized as structure data, not by silently creating a level object
    # that would corrupt the no-level annotation signal.
    assert "function activeEntryLevel()" in LABELER_HTML
    assert "return structureBreakAnchorFrom(zigzag)" in LABELER_HTML
    assert "level = {price:high.price" not in LABELER_HTML
    assert "structure_break_ms" in LABELER_HTML
    assert "entry_source: entryObj ? (withLevel ? 'level_break' : 'structure_break')" in LABELER_HTML
    assert "sl_source: slRay ? (structure && s.slPrice == null ? 'last_swing_low' : 'annotated')" in LABELER_HTML


def test_zigzag_endpoint_swings_are_counted_for_structure_break() -> None:
    # The last swing-low of a manually drawn downtrend can be the final zigzag
    # point. Requiring both neighbours would incorrectly pick the previous low
    # and place the structure-break stop too high.
    assert "for (let i=0; i<pts.length; i++)" in LABELER_HTML
    assert "endpoints count" in LABELER_HTML
    assert "protected stop must be L2, not L1" in LABELER_HTML


def test_save_recomputes_derived_structure_entry_before_serialization() -> None:
    match = re.search(r"function currentLabelPayload\(options=\{\}\) \{(?P<body>.*?)\n\}", LABELER_HTML, re.S)
    assert match is not None
    body = match.group("body")
    assert "computeEntry();" in body
    assert body.count("commitActiveSetup();") >= 2
    assert "function saveLabel()" in LABELER_HTML


def test_level_labeler_autosaves_and_navigation_flushes_edits() -> None:
    # Autosave and navigation must be serialized through one queue. Otherwise a
    # slow manual save / fast navigation click can append labels out of order and
    # make the UI appear to ignore clicks or lose recently drawn objects.
    assert "let saveQueue = Promise.resolve()" in LABELER_HTML
    assert "let seedSignatures = new Map();" in LABELER_HTML
    assert "seedSignatures.get(eventId) === sig" in LABELER_HTML
    assert "const AUTOSAVE_DELAY_MS" in LABELER_HTML
    assert "function scheduleAutosave()" in LABELER_HTML
    assert "async function flushAutosave" in LABELER_HTML
    assert "await flushAutosave({finishDraft:true, quiet:false})" in LABELER_HTML
    assert "function navigateToEventId" in LABELER_HTML
    assert "function currentVisibleIndex()" in LABELER_HTML
    # Back-to-back clicks must accumulate: deltas base off the optimistic pending
    # target (updated synchronously) rather than the still-stale current position,
    # which only refreshes after the async flush + candle fetch settle.
    assert "let pendingNavIndex = null;" in LABELER_HTML
    assert "pendingNavIndex != null ? pendingNavIndex : currentVisibleIndex()" in LABELER_HTML
    assert "d.onclick = () => navigateToEventId(c.event_id);" in LABELER_HTML
    assert "loadEvent(idx + 1)" not in LABELER_HTML


def test_level_labeler_timeframe_dropdown_uses_all_available_tfs() -> None:
    assert "availableTfs = Array.isArray(data.available_tfs)" in LABELER_HTML
    assert "for (const tf of availableTfs)" in LABELER_HTML
    assert "for (const v of variants)" in LABELER_HTML
    assert "selected_tf: e.tf" in LABELER_HTML


def test_daily_top_hold_focus_is_explicit_and_deterministic() -> None:
    # Outside the plot, context is muted and the pump plus accepted top hold
    # remains legible. Hovering the price plot restores the full context.
    assert "function dailyHoldFocusTrace(candles, range)" in LABELER_HTML
    assert "daily-hold-focus-candles" in LABELER_HTML
    assert "function setDailyHoldFocus(active)" in LABELER_HTML
    assert "wrap.addEventListener('pointermove', () => {" in LABELER_HTML
    assert "wrap.addEventListener('pointerleave', () => {" in LABELER_HTML
    assert "Plotly.restyle(chart, {opacity:active ? 1 : 0.24}, [0]);" in LABELER_HTML
    assert "Plotly.restyle(chart, {opacity:active ? 0 : 1}, [2]);" in LABELER_HTML


def test_level_labeler_ignores_stale_async_candle_loads() -> None:
    assert "let loadToken = 0" in LABELER_HTML
    assert "const token = ++loadToken" in LABELER_HTML
    assert "if (token !== loadToken) return" in LABELER_HTML


def test_label_file_reads_and_writes_are_locked() -> None:
    handler_source = inspect.getsource(LevelLabelerHandler)
    server_source = inspect.getsource(LevelLabelerServer)
    assert "self.labels_lock = threading.Lock()" in server_source
    assert handler_source.count("with self.server.labels_lock") >= 4


def test_level_segment_is_cut_by_a_candle_that_closes_above_it() -> None:
    # A horizontal level (resistance) stays valid until a later candle *closes*
    # above it; a mere body/wick touch must not truncate the segment early.
    match = re.search(r"function levelAutoEndMs\(price, startMs\) \{(?P<body>.*?)\n\}", LABELER_HTML, re.S)
    assert match is not None
    body = match.group("body")
    assert "c.close[i] > price" in body
    assert "candleBodyCrosses" not in LABELER_HTML


def test_manual_level_touches_are_toggleable_and_persisted() -> None:
    # A new level begins with its anchor as a touch; subsequent left-clicks
    # toggle candles and RMB closes the touch-selection phase.
    assert "touches: [anchor.ms]" in LABELER_HTML
    assert "tool === 'level_touches'" in LABELER_HTML
    assert "function finishLevelTouches()" in LABELER_HTML
    assert "level_touch_times_ms" in LABELER_HTML
    assert "finishLevelTouches();" in LABELER_HTML


def test_lower_support_uses_only_retraces_between_neighbouring_level_touches() -> None:
    assert "function pullbackLows(touches)" in LABELER_HTML
    assert "for (let pair = 1; pair < ordered.length; pair++)" in LABELER_HTML
    assert "for (let i = lowIdx + 1; i < right.i; i++)" in LABELER_HTML
    assert "terminal pullback" in LABELER_HTML
    assert "if (c.low[lowIdx] < level.price)" in LABELER_HTML
    assert "const lows = pullbackLows(tt), fit = fitSlope(lows);" in LABELER_HTML


def test_manual_level_touch_schema_requires_sorted_timestamps_inside_level() -> None:
    payload = {
        "event_id": "e1_5m",
        "symbol": "AAAUSDT",
        "tf": "5m",
        "setups": [
            {
                "family": "breakout",
                "quality": "ok",
                "has_level": True,
                "has_pump_transition": False,
                "has_structure_break": False,
                "level_price": 1.2,
                "level_start_ms": 1_000,
                "level_end_ms": 5_000,
                "level_touch_times_ms": [1_000, 3_000, 5_000],
            }
        ],
    }
    validate_label_payload(payload)
    payload["setups"][0]["level_touch_times_ms"] = [3_000, 1_000]
    with pytest.raises(ValueError, match="strictly increasing"):
        validate_label_payload(payload)
