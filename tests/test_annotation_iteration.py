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

from anomaly_science.annotation.app import AnnotationStrategyApp
from anomaly_science.annotation.desk.result_annotations import ResultAnnotationStore
from anomaly_science.annotation.iteration import AnnotationIterationConfig, run_annotation_iteration, summarize_annotation_state
from anomaly_science.annotation.level_labeler import DEFAULT_TF_MINUTES, LABELER_HTML, LevelLabelerHandler, LevelLabelerServer


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
            "exitPoint": {"ms": 120_000, "price": 1.35},
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
            "exitPoint": {"ms": 120_000, "price": 1.35},
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
    assert "level = null; pump = null; entry = null; sl = null; exitPoint = null; zigzag = null; zzDraft = null; setups = [];" in LABELER_HTML
    assert "clearChart();\n    toast(payload.error, 4200);" in LABELER_HTML
    assert "if (payload.error) {\n    toast(payload.error, 4200);\n    refreshSelect(document.getElementById('tfSelect'));" in LABELER_HTML
    assert "document.getElementById('iterationStatus').innerHTML = `<b>error</b>" in LABELER_HTML


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
    # values and defaults to cap/good, so nothing carries over between setups/events.
    assert "fam.value = s.family || 'cap'" in LABELER_HTML
    assert "qual.value = s.quality || 'good'" in LABELER_HTML


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
    assert "const AUTOSAVE_DELAY_MS" in LABELER_HTML
    assert "function scheduleAutosave()" in LABELER_HTML
    assert "async function flushAutosave" in LABELER_HTML
    assert "await flushAutosave({finishDraft:true, quiet:false})" in LABELER_HTML
    assert "function navigateToEventId" in LABELER_HTML
    assert "d.onclick = () => navigateToEventId(c.event_id);" in LABELER_HTML
    assert "loadEvent(idx + 1)" not in LABELER_HTML


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
