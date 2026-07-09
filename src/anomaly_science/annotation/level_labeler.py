"""Reusable browser UI for manual horizontal-level annotation on OHLCV events."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd

from anomaly_science.annotation.ohlcv import load_ohlcv_parquet, resample_ohlcv_np
from anomaly_science.annotation.schemas import (
    ANNOTATION_CANDIDATE_SCHEMA_VERSION,
    LEVEL_LABEL_SCHEMA_VERSION,
    REQUIRED_CANDIDATE_COLUMNS,
    validate_label_payload,
)

DEFAULT_TF_MINUTES = {
    "1m": 1,
    "3m": 3,
    "5m": 5,
    "10m": 10,
    "15m": 15,
    "1h": 60,
    "4h": 240,
}


def _read_labels(path: Path) -> dict[str, dict]:
    labels: dict[str, dict] = {}
    if not path.exists():
        return labels
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        labels[str(row["event_id"])] = row
    return labels


def _json_response(handler: BaseHTTPRequestHandler, payload: object, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def validate_candidates(frame: pd.DataFrame) -> None:
    missing = sorted(set(REQUIRED_CANDIDATE_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"candidate frame missing columns: {missing}")
    if frame["event_id"].duplicated().any():
        raise ValueError("candidate event_id must be unique")


def _tf_minutes(tf: str, mapping: dict[str, int]) -> int:
    return int(mapping.get(str(tf), 0))


def _make_group_id(symbol: str, start_ms: int, end_ms: int, event_ids: list[str]) -> str:
    raw = f"{symbol}|{start_ms}|{end_ms}|{'|'.join(sorted(event_ids))}"
    digest = hashlib.blake2b(raw.encode("utf-8"), digest_size=10).hexdigest()
    return f"grp_{digest}"


def _build_annotation_groups(frame: pd.DataFrame, tf_minutes: dict[str, int]) -> list[dict]:
    """Group rows that represent the same underlying market event on multiple TFs."""

    records = frame.replace({np.nan: None}).to_dict("records")
    if "annotation_group_id" in frame.columns:
        grouped: dict[str, list[dict]] = {}
        for row in records:
            gid = str(row.get("annotation_group_id") or row["event_id"])
            grouped.setdefault(gid, []).append(row)
        return [_group_payload(gid, rows, tf_minutes) for gid, rows in grouped.items()]

    if {"pump_start_ms", "culmination_ms"}.issubset(frame.columns):
        groups: list[list[dict]] = []
        tolerance_ms = 6 * 60 * 60 * 1000
        for symbol, part in frame.sort_values(["symbol", "culmination_ms", "pump_start_ms"]).groupby("symbol", sort=False):
            current: list[dict] = []
            current_culm: int | None = None
            for row in part.replace({np.nan: None}).to_dict("records"):
                culm = int(row.get("culmination_ms") or row.get("anchor_time_ms") or row["review_start_ms"])
                if current and current_culm is not None and abs(culm - current_culm) > tolerance_ms:
                    groups.append(current)
                    current = []
                current.append(row)
                current_culm = int(np.median([int(r.get("culmination_ms") or r.get("anchor_time_ms") or r["review_start_ms"]) for r in current]))
            if current:
                groups.append(current)
        return [_group_payload(None, rows, tf_minutes) for rows in groups]

    return [_group_payload(str(row["event_id"]), [row], tf_minutes) for row in records]


def _group_payload(group_id: str | None, rows: list[dict], tf_minutes: dict[str, int]) -> dict:
    variants = sorted(rows, key=lambda r: (-_tf_minutes(str(r["tf"]), tf_minutes), str(r["tf"])))
    event_ids = [str(r["event_id"]) for r in variants]
    start = min(int(r["review_start_ms"]) for r in variants)
    end = max(int(r["review_end_ms"]) for r in variants)
    gid = group_id or _make_group_id(str(variants[0]["symbol"]), start, end, event_ids)
    canonical = dict(variants[0])
    canonical.update(
        {
            "event_id": gid,
            "annotation_group_id": gid,
            "source_event_ids": event_ids,
            "variants": variants,
            "tf": "/".join(str(r["tf"]) for r in variants),
            "default_tf": str(variants[0]["tf"]),
            "review_start_ms": start,
            "review_end_ms": end,
        }
    )
    return canonical


class LevelLabelerServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address,
        handler_cls,
        *,
        candidates_path: Path,
        labels_path: Path,
        cache_dir: Path,
        tf_minutes: dict[str, int],
        strategy_id: str = "manual_level_annotation",
        strategy_title: str = "Manual level annotation",
        show_launcher: bool = False,
    ):
        super().__init__(server_address, handler_cls)
        self.candidates_path = candidates_path
        self.labels_path = labels_path
        self.cache_dir = cache_dir
        self.tf_minutes = tf_minutes
        self.strategy_id = strategy_id
        self.strategy_title = strategy_title
        self.show_launcher = show_launcher
        self.candidates = pd.read_parquet(candidates_path)
        validate_candidates(self.candidates)
        if "candidate_schema_version" not in self.candidates.columns:
            self.candidates["candidate_schema_version"] = ANNOTATION_CANDIDATE_SCHEMA_VERSION
        self.by_event = self.candidates.set_index("event_id", drop=False)
        self.groups = _build_annotation_groups(self.candidates, self.tf_minutes)
        self.group_by_id = {str(g["event_id"]): g for g in self.groups}
        self.cache: dict[tuple[str, str], dict[str, np.ndarray]] = {}

    def strategy_payload(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "title": self.strategy_title,
            "candidate_count": len(self.groups),
            "candidate_rows": int(len(self.candidates)),
            "labels_path": str(self.labels_path),
            "inputs": [
                {"id": "level_price", "label": "horizontal level price", "required_for_level": True},
                {"id": "level_start_ms", "label": "level start time", "required_for_level": True},
                {"id": "level_end_ms", "label": "level end time", "required_for_level": True},
                {"id": "pump_start", "label": "approximate pump start", "required_for_level": False},
                {"id": "culmination", "label": "pump culmination", "required_for_level": False},
                {"id": "entry", "label": "possible entry", "required_for_level": False},
                {"id": "exit", "label": "possible exit", "required_for_level": False},
                {"id": "notes", "label": "free-form trader comment", "required_for_level": False},
            ],
        }


class LevelLabelerHandler(BaseHTTPRequestHandler):
    server: LevelLabelerServer

    def log_message(self, fmt: str, *args) -> None:
        print(fmt % args, flush=True)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            html = LAUNCHER_HTML if self.server.show_launcher else LABELER_HTML
            body = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/labeler":
            body = LABELER_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/plotly.min.js":
            import plotly

            path = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"
            body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/candidates":
            labels = _read_labels(self.server.labels_path)
            payload = []
            for group in self.server.groups:
                row = dict(group)
                member_ids = [str(x) for x in row.get("source_event_ids", [])]
                label = labels.get(str(row["event_id"]))
                if label is None:
                    label = next((labels[x] for x in member_ids if x in labels), None)
                row["labeled"] = label is not None
                row["label"] = label
                payload.append(row)
            _json_response(self, {"candidates": payload})
            return
        if parsed.path == "/api/strategies":
            _json_response(self, {"strategies": [self.server.strategy_payload()]})
            return
        if parsed.path == "/api/labels":
            _json_response(self, {"labels": list(_read_labels(self.server.labels_path).values())})
            return
        if parsed.path == "/api/candles":
            qs = parse_qs(parsed.query)
            event_id = qs.get("event_id", [""])[0]
            tf = qs.get("tf", [""])[0]
            row = self._candidate_row(event_id, tf)
            if row is None:
                _json_response(self, {"error": "unknown event_id"}, 404)
                return
            _json_response(self, self._candles_payload(row))
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/label":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        n = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(n).decode("utf-8"))
        payload["label_schema_version"] = LEVEL_LABEL_SCHEMA_VERSION
        payload["saved_at_ms"] = int(time.time() * 1000)
        try:
            validate_label_payload(payload)
            if payload["event_id"] not in self.server.by_event.index and payload["event_id"] not in self.server.group_by_id:
                raise ValueError("unknown event_id")
        except ValueError as exc:
            _json_response(self, {"error": str(exc)}, 400)
            return
        self.server.labels_path.parent.mkdir(parents=True, exist_ok=True)
        with self.server.labels_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n")
        _json_response(self, {"ok": True, "label": payload})

    def _candidate_row(self, event_id: str, tf: str) -> pd.Series | None:
        if event_id in self.server.group_by_id:
            group = self.server.group_by_id[event_id]
            variants = group.get("variants", [])
            selected = None
            if tf:
                selected = next((v for v in variants if str(v["tf"]) == tf), None)
            if selected is None and variants:
                selected = variants[0]
            if selected is None:
                return None
            source_id = str(selected["event_id"])
            if source_id not in self.server.by_event.index:
                return None
            return self.server.by_event.loc[source_id]
        if event_id in self.server.by_event.index:
            return self.server.by_event.loc[event_id]
        return None

    def _candles_payload(self, row: pd.Series) -> dict:
        symbol, tf = str(row.symbol), str(row.tf)
        key = (symbol, tf)
        if key not in self.server.cache:
            minutes = self.server.tf_minutes.get(tf)
            if minutes is None:
                raise ValueError(f"unknown tf {tf!r}")
            base = load_ohlcv_parquet(self.server.cache_dir / f"{symbol}.parquet")
            self.server.cache[key] = base if minutes == 1 else resample_ohlcv_np(base, minutes)
        frame = self.server.cache[key]
        ts = frame["timestamp"]
        a = int(np.searchsorted(ts, int(row.review_start_ms), side="left"))
        b = int(np.searchsorted(ts, int(row.review_end_ms), side="right"))
        event = row.replace({np.nan: None}).to_dict()
        marker_cols = [c for c in row.index if c.endswith("_ms") or c.endswith("_price")]
        event["marker_columns"] = {c: event.get(c) for c in marker_cols if event.get(c) is not None}
        return {
            "event": event,
            "candles": {
                "timestamp": ts[a:b].astype(int).tolist(),
                "open": frame["open"][a:b].astype(float).tolist(),
                "high": frame["high"][a:b].astype(float).tolist(),
                "low": frame["low"][a:b].astype(float).tolist(),
                "close": frame["close"][a:b].astype(float).tolist(),
                "quote_volume": frame["quote_volume"][a:b].astype(float).tolist(),
            },
        }


def serve_level_labeler(
    *,
    candidates_path: Path,
    labels_path: Path,
    cache_dir: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    tf_minutes: dict[str, int] | None = None,
    strategy_id: str = "manual_level_annotation",
    strategy_title: str = "Manual level annotation",
    show_launcher: bool = False,
) -> None:
    if not candidates_path.exists():
        raise FileNotFoundError(candidates_path)
    server = LevelLabelerServer(
        (host, port),
        LevelLabelerHandler,
        candidates_path=candidates_path,
        labels_path=labels_path,
        cache_dir=cache_dir,
        tf_minutes=tf_minutes or DEFAULT_TF_MINUTES,
        strategy_id=strategy_id,
        strategy_title=strategy_title,
        show_launcher=show_launcher,
    )
    print(f"labeler: http://{host}:{port}", flush=True)
    print(f"candidates: {candidates_path}", flush=True)
    print(f"labels append-only JSONL: {labels_path}", flush=True)
    server.serve_forever()


LAUNCHER_HTML = r"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Annotation Browser</title>
  <style>
    :root {
      --bg:#111318; --panel:#171a20; --panel-soft:#20242c; --text:#e7e3d8;
      --muted:#9b9a93; --line:#2a2e36; --accent:#8aa0d8; --accent-soft:#222b45;
      --good:#7dbb91; --bad:#d18495;
    }
    * { box-sizing:border-box; }
    * { scrollbar-width: thin; scrollbar-color: #555d6b #15171c; }
    *::-webkit-scrollbar { width: 9px; height: 9px; }
    *::-webkit-scrollbar-track { background: #15171c; }
    *::-webkit-scrollbar-thumb { background: #555d6b; border-radius: 0; }
    *::-webkit-scrollbar-thumb:hover { background: #687181; }
    body {
      margin:0; min-height:100vh; font-family:Inter, "IBM Plex Sans", Segoe UI, Arial, sans-serif;
      color:var(--text); background:var(--bg);
    }
    main { max-width:960px; margin:0 auto; padding:64px 24px; }
    .kicker { color:var(--accent); letter-spacing:.10em; text-transform:uppercase; font-weight:760; font-size:12px; }
    h1 { margin:12px 0 10px; font-size:42px; line-height:1.05; letter-spacing:-.035em; font-weight:760; }
    .sub { color:var(--muted); max-width:740px; line-height:1.5; }
    #strategies { display:grid; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); gap:14px; margin-top:30px; }
    .card { border-radius:10px; padding:22px; background:var(--panel); box-shadow:none; }
    .card h2 { margin:0 0 10px; font-size:19px; letter-spacing:-.015em; }
    .metric { display:inline-block; margin:6px 6px 0 0; padding:5px 9px; border-radius:7px; color:var(--muted); background:var(--panel-soft); font-size:12px; }
    .metric b { color:var(--text); }
    .inputs { margin-top:16px; padding-top:14px; border-top:1px solid var(--line); color:var(--muted); font-size:13px; line-height:1.55; }
    a.button { display:inline-block; margin-top:18px; color:#101217; background:var(--accent); text-decoration:none; font-weight:760; padding:10px 14px; border-radius:8px; }
    .empty { color:var(--bad); border-radius:8px; padding:14px; background:#24171c; }
  </style>
</head>
<body>
  <main>
    <div class="kicker">Anomaly science annotation</div>
    <h1>Choose a supported labeling strategy</h1>
    <p class="sub">This launcher only lists strategies with a real candidate artifact and a declared browser input schema. No placeholder strategies are exposed.</p>
    <section id="strategies"></section>
  </main>
  <script>
    function esc(x) { return String(x ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); }
    async function boot() {
      const r = await fetch('/api/strategies');
      const data = await r.json();
      const root = document.getElementById('strategies');
      if (!data.strategies.length) {
        root.innerHTML = '<div class="empty">No annotation-capable strategies are registered.</div>';
        return;
      }
      root.innerHTML = data.strategies.map(s => `
        <article class="card">
          <h2>${esc(s.title)}</h2>
          <span class="metric">events <b>${s.candidate_count}</b></span>
          <span class="metric">rows <b>${s.candidate_rows}</b></span>
          <div class="inputs"><b>Inputs:</b><br>${s.inputs.map(i => esc(i.label)).join('<br>')}</div>
          <a class="button" href="/labeler?strategy=${encodeURIComponent(s.strategy_id)}">Open annotation desk</a>
        </article>
      `).join('');
    }
    boot();
  </script>
</body>
</html>
"""


LABELER_HTML = r"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Market Level Labeler</title>
  <script src="/plotly.min.js"></script>
  <style>
    :root {
      --bg:#111318; --surface:#171a20; --surface-2:#20242c; --surface-3:#262b34;
      --text:#e7e3d8; --muted:#9b9a93; --faint:#73777f; --line:#2a2e36;
      --accent:#8aa0d8; --accent-soft:#222b45; --green:#7dbb91; --orange:#c99a62;
      --red:#d18495; --purple:#a597d6; --shadow:rgba(0,0,0,.22);
    }
    * { box-sizing: border-box; }
    * { scrollbar-width: thin; scrollbar-color: #555d6b #15171c; }
    *::-webkit-scrollbar { width: 9px; height: 9px; }
    *::-webkit-scrollbar-track { background: #15171c; }
    *::-webkit-scrollbar-thumb { background: #555d6b; border-radius: 0; }
    *::-webkit-scrollbar-thumb:hover { background: #687181; }
    body {
      margin: 0; font-family: Inter, "IBM Plex Sans", Segoe UI, Arial, sans-serif;
      background: var(--bg); color: var(--text);
    }
    #top {
      height: 62px; display: grid; grid-template-columns: auto 1fr auto; gap: 16px; align-items: center; padding: 9px 14px;
      background: rgba(17,19,24,.92); backdrop-filter: blur(16px);
      border-bottom: 1px solid rgba(42,46,54,.95);
      position: sticky; top: 0; z-index: 2;
    }
    .brand {
      display: flex; align-items: center; gap: 10px; letter-spacing: -.015em;
      font-size: 15px; color: var(--text); font-weight: 760;
    }
    .actions { display: flex; gap: 6px; align-items: center; justify-content: flex-end; }
    .action-group { display:flex; gap:4px; align-items:center; }
    .action-spacer { width:16px; flex:0 0 16px; }
    .center { min-width: 0; display: flex; align-items: center; gap: 9px; }
    button, select, input, textarea {
      background: var(--surface); color: var(--text);
      border: 0; border-radius: 7px; padding: 8px 10px; outline: none;
      box-shadow: inset 0 0 0 1px rgba(42,46,54,.95);
    }
    select { padding-inline-end: 28px; }
    button { font-weight: 720; cursor: pointer; }
    button.icon { width:32px; height:32px; padding:0; display:grid; place-items:center; color:var(--text); }
    button.icon svg { width:16px; height:16px; stroke:currentColor; fill:none; stroke-width:1.5; stroke-linecap:round; stroke-linejoin:round; }
    button.text-action { height:32px; padding:0 10px; font-size:12px; }
    button.tool.active { background: var(--accent-soft); color: var(--accent); box-shadow: inset 0 0 0 1px rgba(138,160,216,.50); }
    button.tool.pending { background: rgba(201,154,98,.14); color: var(--orange); box-shadow: inset 0 0 0 1px rgba(201,154,98,.40); }
    button:hover { cursor: pointer; background: var(--surface-2); }
    button:disabled { opacity:.38; cursor:default; }
    button:disabled:hover { background: var(--surface); }
    button.ghost { color: var(--muted); }
    button.primary { background: var(--accent); color: #101217; box-shadow: none; }
    button.primary:hover { background: #9aaddf; }
    button.danger { background: #251922; color: var(--red); box-shadow: inset 0 0 0 1px rgba(209,132,149,.20); }
    #wrap { display: grid; grid-template-columns: 370px minmax(0, 1fr); height: calc(100vh - 62px); }
    body.focus #wrap { grid-template-columns: 0 minmax(0, 1fr); }
    body.focus #side { padding: 0; border: 0; overflow: hidden; }
    #side { overflow: auto; background: rgba(17,19,24,.60); padding: 12px; }
    #chartwrap { position: relative; height: calc(100vh - 62px); min-width: 0; overflow:hidden; }
    #chart { position:absolute; inset:0; cursor: grab; }
    #chart:active { cursor: grabbing; }
    body.drawing #chart, body.drawing #chart * { cursor: crosshair !important; }
    #ovl { position:absolute; inset:0; pointer-events:none; z-index:3; }
    body.drawing #ovl { pointer-events:auto; cursor: crosshair; }
    #yzone { position:absolute; z-index:4; cursor: ns-resize; }
    #xzone { position:absolute; z-index:4; cursor: ew-resize; }
    body.drawing #yzone, body.drawing #xzone { pointer-events:none; }
    #keysFab {
      position:absolute; left:10px; top:8px; z-index:5; width:26px; height:26px;
      display:grid; place-items:center; border-radius:7px;
      background:rgba(32,36,44,.85); color:var(--faint); cursor:default; user-select:none;
    }
    #keysFab > svg { width:15px; height:15px; stroke:currentColor; fill:none; stroke-width:1; stroke-linecap:round; stroke-linejoin:round; }
    #keysFab:hover { color:var(--text); }
    #keysPop {
      display:none; position:absolute; left:0; top:32px; width:460px;
      background:var(--surface); border-radius:10px; padding:12px 14px;
      box-shadow:0 18px 44px rgba(0,0,0,.45), inset 0 0 0 1px rgba(42,46,54,.95);
    }
    #keysFab:hover #keysPop { display:block; }
    .title { font-size: 15px; font-weight: 720; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 120px; letter-spacing:-.01em; }
    .pill { color: var(--muted); border-radius: 7px; padding: 5px 9px; font-size: 12px; background: var(--surface-2); }
    .progress { color: var(--accent); background: var(--accent-soft); }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .field { margin-bottom: 10px; }
    .field label { display:block; font-size: 11px; color: var(--muted); margin-bottom: 5px; text-transform: uppercase; letter-spacing: .045em; }
    .field input, .field select { width: 100%; }
    textarea { width: 100%; height: 62px; resize: vertical; }
    .hint { color: var(--muted); font-size: 12px; line-height: 1.35; margin-bottom: 12px; }
    .panel {
      border-radius: 10px; background: var(--surface);
      padding: 14px; margin-bottom: 10px; box-shadow: none;
    }
    .panel-title { font-size: 11px; color: var(--accent); text-transform: uppercase; letter-spacing: .075em; margin-bottom: 10px; font-weight: 760; }
    .metricbar { display:flex; flex-wrap:wrap; gap:10px; margin-bottom:8px; }
    .metric { border-radius: 6px; padding: 4px 8px; color: var(--muted); font-size: 11px; background:var(--surface-2); }
    .metric b { color: var(--text); }
    .row {
      padding: 10px 11px; margin-bottom: 6px; border-radius: 7px;
      background: var(--surface);
    }
    .row.active { background: var(--accent-soft); box-shadow: inset 2px 0 0 var(--accent); }
    .row.labeled { opacity: .56; }
    .row-head { display:flex; justify-content:space-between; gap:8px; font-weight:760; }
    .row-badges { display:flex; gap:8px; margin-top:5px; flex-wrap:wrap; }
    .badge { font-size:10px; color:var(--faint); }
    .badge.hot { color: var(--orange); }
    .small { font-size: 12px; color: var(--muted); }
    .quiet-help { font-size:10px; color:var(--faint); line-height:1.35; }
    .tool-row { display:flex; gap:5px; flex-wrap:wrap; margin-bottom:10px; align-items:center; }
    .manual-list { display:flex; flex-direction:column; gap:4px; margin-top:8px; }
    .manual-item { display:flex; align-items:center; justify-content:space-between; gap:8px; color:var(--muted); background:var(--surface-2); border-radius:6px; padding:5px 7px; font-size:11px; }
    .manual-item.active { color:var(--text); background:var(--accent-soft); }
    .manual-item button { width:22px; height:22px; padding:0; box-shadow:none; background:transparent; color:var(--faint); }
    .manual-item .auto-tag { font-size:9px; color:var(--faint); text-transform:uppercase; letter-spacing:.05em; }
    .event-controls { display:flex; gap:8px; align-items:center; margin-bottom:10px; }
    .hover-zone { color:var(--faint); background:var(--surface-2); border-radius:7px; padding:7px 9px; font-size:11px; user-select:none; }
    .hover-zone.active { color:var(--text); background:var(--accent-soft); }
    .native-select-hidden { display:none !important; }
    .select-ui { position:relative; min-width:112px; }
    .select-button { width:100%; height:32px; display:flex; align-items:center; justify-content:space-between; gap:10px; padding:0 9px; background:var(--surface-2); color:var(--text); border-radius:7px; box-shadow:inset 0 0 0 1px rgba(42,46,54,.95); font-size:12px; }
    .select-button::after { content:"⌄"; color:var(--faint); font-size:12px; }
    .select-menu { position:absolute; left:0; right:0; top:36px; z-index:6; display:none; background:var(--surface); border-radius:8px; box-shadow:0 16px 36px rgba(0,0,0,.32), inset 0 0 0 1px rgba(42,46,54,.95); padding:4px; max-height:220px; overflow:auto; }
    .select-ui.open .select-menu { display:block; }
    .select-option { padding:7px 8px; border-radius:6px; font-size:12px; color:var(--muted); }
    .select-option:hover { background:var(--surface-2); color:var(--text); }
    .select-option.selected { color:var(--accent); background:var(--accent-soft); }
    .keys-panel { display:grid; grid-template-columns:1fr 1fr; gap:4px 14px; margin-top:2px; }
    .keys-panel .k-row { display:flex; align-items:baseline; gap:8px; font-size:11px; color:var(--muted); }
    .keys-panel .k { display:inline-block; min-width:22px; padding:1px 6px; border-radius:4px; background:var(--surface-2); color:var(--text); font-family: "IBM Plex Mono","JetBrains Mono",Consolas,monospace; font-size:10px; text-align:center; box-shadow: inset 0 0 0 1px rgba(42,46,54,.95); }
    .keys-heading { font-size:10px; color:var(--faint); text-transform:uppercase; letter-spacing:.075em; margin:8px 0 4px; }
    #toast { position: fixed; right: 16px; bottom: 16px; background: #17241d; color: var(--green); padding: 10px 14px; border-radius: 8px; display:none; box-shadow: 0 12px 34px var(--shadow); z-index:9; }
    @media (max-width: 900px) {
      #top { grid-template-columns: 1fr auto; height: auto; }
      .center { grid-column: 1 / -1; order: 3; }
      #wrap { grid-template-columns: 360px minmax(360px, 1fr); }
    }
  </style>
</head>
<body>
  <div id="top">
    <div class="brand">Level desk</div>
    <div class="center">
      <div id="title" class="title"></div>
      <span id="progress" class="pill progress"></span>
    </div>
    <div class="actions">
      <div class="action-group">
        <button class="ghost icon" onclick="prevEvent()" title="Previous candidate  ←">
          <svg viewBox="0 0 20 20"><path d="M12.5 4L6.5 10l6 6"/></svg>
        </button>
        <button class="ghost icon" onclick="nextEvent()" title="Next candidate  →">
          <svg viewBox="0 0 20 20"><path d="M7.5 4l6 6-6 6"/></svg>
        </button>
        <button class="ghost icon" onclick="nextUnlabeled()" title="Next unlabeled  U">
          <svg viewBox="0 0 20 20"><path d="M10 3l7 7-7 7-7-7z"/></svg>
        </button>
        <button class="ghost icon" onclick="resetAnnotations()" title="Reset annotations to saved state  Z">
          <svg viewBox="0 0 20 20"><path d="M13.5 16H9a5 5 0 1 1 0-10h6.5"/><path d="M12.5 3l3 3-3 3"/></svg>
        </button>
        <button class="ghost icon" onclick="resetView()" title="Reset zoom  0">
          <svg viewBox="0 0 20 20"><path d="M4 8V4h4"/><path d="M16 8V4h-4"/><path d="M4 12v4h4"/><path d="M16 12v4h-4"/></svg>
        </button>
        <button class="ghost icon" onclick="toggleFocus()" title="Focus chart  F">
          <svg viewBox="0 0 20 20"><path d="M3 8V3h5"/><path d="M17 8V3h-5"/><path d="M3 12v5h5"/><path d="M17 12v5h-5"/></svg>
        </button>
        <span class="action-spacer"></span>
        <button class="danger text-action" onclick="saveNoSleepPump()" title="No sleep-pump transition  T">No transition</button>
        <button class="danger text-action" onclick="saveNoLevel()" title="No valid level  N">No level</button>
        <span class="action-spacer"></span>
        <button class="primary text-action" onclick="saveLevel()" title="Save level  S">Save</button>
      </div>
    </div>
  </div>
  <div id="wrap">
    <div id="side">
      <div class="panel">
        <div class="panel-title">Event tape</div>
        <div class="event-controls">
          <input id="jump" type="number" min="1" style="display:none" onkeydown="jumpKey(event)">
          <select id="tfSelect" onchange="changeTf()"></select>
          <div id="defaultLinesHover" class="hover-zone">auto lines</div>
        </div>
        <div id="metricbar" class="metricbar"></div>
        <div class="quiet-help">
          Wheel = zoom X at cursor · Shift = Y · Ctrl = both · LMB-drag an axis to scale it · double-click axis to auto-fit.
        </div>
      </div>
      <div class="panel">
        <div class="panel-title">Annotation</div>
        <div class="tool-row">
          <button class="ghost icon tool" id="toolLevel" onclick="toggleTool('level')" title="Level: one click snaps to the candle high wick. It extends right until the first close above it.  L">
            <svg viewBox="0 0 20 20"><circle cx="4" cy="10" r="1.4" fill="currentColor" stroke="none"/><path d="M6 10h11" stroke-dasharray="2.5 2"/></svg>
          </button>
          <button class="ghost icon tool" id="toolPump" onclick="toggleTool('pump')" title="Pump: click start candle (snaps to low), click culmination candle (snaps to high).  P">
            <svg viewBox="0 0 20 20"><path d="M4 16L16 4"/><path d="M4 16v-5"/><path d="M16 4h-5"/><rect x="4" y="4" width="12" height="12" stroke-dasharray="2 2" opacity=".45"/></svg>
          </button>
          <button class="ghost icon tool" id="toolSwingHigh" onclick="toggleTool('swhigh')" title="Swing high: snaps to candle high  H">
            <svg viewBox="0 0 20 20"><path d="M3 15l5-8 4 6 5-9"/><circle cx="8" cy="7" r="1.6" fill="currentColor" stroke="none"/></svg>
          </button>
          <button class="ghost icon tool" id="toolSwingLow" onclick="toggleTool('swlow')" title="Swing low: snaps to candle low  J">
            <svg viewBox="0 0 20 20"><path d="M3 5l5 8 4-6 5 9"/><circle cx="8" cy="13" r="1.6" fill="currentColor" stroke="none"/></svg>
          </button>
          <button class="ghost icon tool" id="toolExit" onclick="toggleTool('exit')" disabled title="Draw a level first, then mark the possible exit point.  E">
            <svg viewBox="0 0 20 20"><path d="M5 5l10 10"/><path d="M15 5L5 15"/></svg>
          </button>
          <button class="text-action" id="slBtn" onclick="setOptimalSl()" disabled title="Auto stop-loss: low of the entry candle, ray cut at first touch  X">Auto SL</button>
        </div>
        <div class="quiet-help" style="margin-bottom:10px">
          Level snaps to the selected candle high wick and ends itself at the first close above it; entry is that same breakout close. Swings drag only between their neighbours.
        </div>
        <div class="grid">
          <div class="field"><label>family</label><select id="family"><option value="cap">cap</option><option value="breakout">breakout</option><option value="unknown">unknown</option></select></div>
          <div class="field"><label>price snap</label><select id="snapMode"><option value="wick">wick</option><option value="ohlc">OHLC</option><option value="off">off</option></select></div>
        </div>
        <div class="grid">
          <div class="field"><label>quality</label><select id="quality"><option value="good">good</option><option value="ok">ok</option><option value="bad">bad</option></select></div>
          <div class="field"><label>show only</label><select id="filter" onchange="renderList()"><option value="all">all</option><option value="unlabeled">unlabeled</option><option value="labeled">labeled</option></select></div>
        </div>
        <div class="field"><label>symbol / TF search</label><input id="search" placeholder="e.g. AVNT 15m" oninput="renderList()"></div>
        <div class="field"><label>notes</label><textarea id="notes" placeholder="why valid / why no level / what you see"></textarea></div>
        <div class="field"><label>latest saved comment</label><textarea id="savedNotes" readonly></textarea></div>
        <div class="hint" id="objectsText"></div>
      </div>
      <div id="list"></div>
    </div>
    <div id="chartwrap">
      <div id="chart"></div>
      <svg id="ovl"></svg>
      <div id="keysFab">
        <svg viewBox="0 0 20 20"><rect x="2" y="6" width="16" height="9" rx="1.5"/><path d="M5 9h1M8.5 9h1M12 9h1M15 9h0.01"/><path d="M6 12h8"/></svg>
        <div id="keysPop">
          <div class="keys-heading">Drawing</div>
          <div class="keys-panel">
            <div class="k-row"><span class="k">L</span><span>level high-wick snap (1 click)</span></div>
            <div class="k-row"><span class="k">P</span><span>pump rectangle (2 clicks)</span></div>
            <div class="k-row"><span class="k">H</span><span>swing high</span></div>
            <div class="k-row"><span class="k">J</span><span>swing low</span></div>
            <div class="k-row"><span class="k">E</span><span>exit point</span></div>
            <div class="k-row"><span class="k">X</span><span>auto stop-loss</span></div>
            <div class="k-row"><span class="k">Del</span><span>remove selected</span></div>
            <div class="k-row"><span class="k">Esc</span><span>cancel tool</span></div>
            <div class="k-row"><span class="k">Z</span><span>reset annotations</span></div>
          </div>
          <div class="keys-heading">Chart</div>
          <div class="keys-panel">
            <div class="k-row"><span class="k">Drag</span><span>pan chart</span></div>
            <div class="k-row"><span class="k">Wheel</span><span>zoom X at cursor</span></div>
            <div class="k-row"><span class="k">⇧+Wh</span><span>zoom Y at cursor</span></div>
            <div class="k-row"><span class="k">^+Wh</span><span>zoom both</span></div>
            <div class="k-row"><span class="k">Axis</span><span>LMB-drag to scale</span></div>
            <div class="k-row"><span class="k">2×ax</span><span>auto-fit axis</span></div>
            <div class="k-row"><span class="k">0</span><span>reset view</span></div>
          </div>
          <div class="keys-heading">Navigation</div>
          <div class="keys-panel">
            <div class="k-row"><span class="k">←</span><span>previous event</span></div>
            <div class="k-row"><span class="k">→</span><span>next event</span></div>
            <div class="k-row"><span class="k">U</span><span>next unlabeled</span></div>
            <div class="k-row"><span class="k">[ ]</span><span>cycle timeframe</span></div>
            <div class="k-row"><span class="k">S</span><span>save level</span></div>
            <div class="k-row"><span class="k">N</span><span>save no level</span></div>
            <div class="k-row"><span class="k">T</span><span>save no transition</span></div>
            <div class="k-row"><span class="k">F</span><span>focus chart</span></div>
          </div>
        </div>
      </div>
      <div id="yzone" title="Drag to scale price · double-click to auto-fit"></div>
      <div id="xzone" title="Drag to scale time · double-click to fit all"></div>
    </div>
  </div>
  <div id="toast"></div>
<script>
let candidates = [], visible = [], idx = 0, current = null, selectedTf = null;
let xRange = null, yRange = null, yAuto = true, barMs = 60000;
let tool = null, drawStep = 0, pending = null;
let level = null;       // {price, start_ms, end_ms(derived), broken}
let pump = null;        // {start:{idx,ms,price}, high:{idx,ms,price}}
let entry = null;       // auto: {idx,ms,price}
let sl = null;          // {price, hit_ms|null, end_ms}
let exitPoint = null;   // {ms, price}
let swings = [];        // [{id, type:'high'|'low', ms, price}]
let swingSeq = 0;
let selectedObj = null;
let shapeBindings = [];
let showDefaultLines = false;
let relayoutGuard = false;
let chartHandlersAttached = false;

function gd() { return document.getElementById('chart'); }
function iso(ms) { return new Date(ms).toISOString().slice(0,16).replace('T',' '); }
function fmtPct(x) { return Number.isFinite(x) ? (100*x).toFixed(1)+'%' : 'n/a'; }
function fmtPrice(x) {
  const v = Number(x);
  if (!Number.isFinite(v)) return '';
  const a = Math.abs(v);
  const digits = a >= 100 ? 2 : a >= 1 ? 4 : a >= 0.01 ? 5 : a >= 0.0001 ? 6 : 8;
  return v.toFixed(digits).replace(/\.?0+$/, '');
}
function priceTickFormat(values) {
  const nums = values.map(Number).filter(Number.isFinite);
  if (!nums.length) return '.4f';
  const mid = nums[Math.floor(nums.length / 2)] || nums[0];
  const a = Math.abs(mid);
  const digits = a >= 100 ? 2 : a >= 1 ? 3 : a >= 0.01 ? 4 : a >= 0.0001 ? 5 : 6;
  return '.' + digits + 'f';
}
function displaySymbol(symbol) {
  return String(symbol || '').replace(/USDT$/, '').replace(/USDC$/, '');
}
function toast(msg) { const t=document.getElementById('toast'); t.innerText=msg; t.style.display='block'; setTimeout(()=>t.style.display='none',1600); }
function esc(x) {
  return String(x ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}
function pms(v) {
  if (v == null) return NaN;
  if (typeof v === 'number') return v;
  const s = String(v).replace(' ', 'T');
  return Date.parse(/Z$|[+-]\d\d:?\d\d$/.test(s) ? s : s + 'Z');
}

/* ---------- custom selects ---------- */
function enhanceSelect(select) {
  if (!select || select.closest('[style*="display:none"]')) return;
  let ui = select.nextElementSibling && select.nextElementSibling.classList.contains('select-ui') ? select.nextElementSibling : null;
  if (!ui) {
    ui = document.createElement('div');
    ui.className = 'select-ui';
    ui.innerHTML = '<button type="button" class="select-button"></button><div class="select-menu"></div>';
    select.insertAdjacentElement('afterend', ui);
    select.classList.add('native-select-hidden');
    ui.querySelector('.select-button').addEventListener('click', ev => {
      ev.stopPropagation();
      document.querySelectorAll('.select-ui.open').forEach(x => { if (x !== ui) x.classList.remove('open'); });
      ui.classList.toggle('open');
    });
  }
  const btn = ui.querySelector('.select-button');
  const menu = ui.querySelector('.select-menu');
  btn.textContent = select.options[select.selectedIndex] ? select.options[select.selectedIndex].textContent : '';
  menu.innerHTML = '';
  Array.from(select.options).forEach((opt, i) => {
    const item = document.createElement('div');
    item.className = 'select-option' + (i === select.selectedIndex ? ' selected' : '');
    item.textContent = opt.textContent;
    item.onclick = ev => {
      ev.stopPropagation();
      select.selectedIndex = i;
      select.dispatchEvent(new Event('change'));
      ui.classList.remove('open');
      refreshSelect(select);
    };
    menu.appendChild(item);
  });
}
function refreshSelect(select) { if (select) enhanceSelect(select); }
function enhanceVisibleSelects() { document.querySelectorAll('select').forEach(enhanceSelect); }
document.addEventListener('click', () => document.querySelectorAll('.select-ui.open').forEach(x => x.classList.remove('open')));

/* ---------- data helpers ---------- */
function searchBlob(c) {
  const variants = c.variants || [c];
  return [c.symbol, c.tf, c.event_id, ...variants.flatMap(v => [v.tf, v.event_id])].join(' ').toLowerCase();
}
function computeBarMs() {
  const ts = current.candles.timestamp;
  if (ts.length < 2) { barMs = 60000; return; }
  const diffs = [];
  for (let i=1; i<Math.min(ts.length, 60); i++) diffs.push(ts[i]-ts[i-1]);
  diffs.sort((a,b)=>a-b);
  barMs = diffs[Math.floor(diffs.length/2)] || 60000;
}
function nearestCandle(ms) {
  const ts = current.candles.timestamp;
  let best = 0, bd = Infinity;
  for (let i=0; i<ts.length; i++) {
    const d = Math.abs(ts[i] - ms);
    if (d <= bd) { bd = d; best = i; }
  }
  return best;
}
function nearestCloseTime(ms) {
  const ts = current.candles.timestamp;
  let best = ts.length ? ts[0]+barMs : ms, bd = Infinity;
  for (let i=0; i<ts.length; i++) {
    const close = ts[i] + barMs;
    const d = Math.abs(close - ms);
    if (d < bd) { bd = d; best = close; }
  }
  return best;
}
function snapPrice(ms, y) {
  const mode = document.getElementById('snapMode').value;
  if (mode === 'off') return y;
  const c = current.candles, i = nearestCandle(ms);
  const vals = mode === 'wick' ? [c.high[i], c.low[i]] : [c.open[i], c.high[i], c.low[i], c.close[i]];
  let price = vals[0], bd = Math.abs(vals[0] - y);
  for (const v of vals) {
    const d = Math.abs(v - y);
    if (d < bd) { bd = d; price = v; }
  }
  return price;
}
function levelHighAnchor(ms) {
  const c = current.candles, i = nearestCandle(ms);
  return {idx: i, ms: c.timestamp[i], price: c.high[i]};
}
function levelShapeStartMs(lvl) {
  return lvl.start_ms;
}
function chartDataEndMs() {
  const c = current && current.candles;
  return c && c.timestamp.length ? c.timestamp[c.timestamp.length-1] + barMs : Date.now();
}
function visibleChartRightMs() {
  const r = plotRefs();
  if (!r || !r.xa || !r.xa.range || r.xa.range.length < 2) return NaN;
  return pms(r.xa.range[1]);
}
function unbrokenLevelEndMs() {
  const dataEnd = chartDataEndMs();
  const visibleRight = visibleChartRightMs();
  return Number.isFinite(visibleRight) ? Math.max(dataEnd, visibleRight) : dataEnd;
}
function levelShapeEndMs(lvl) {
  if (!lvl) return chartDataEndMs();
  const end = Number.isFinite(Number(lvl.end_ms)) ? Number(lvl.end_ms) : chartDataEndMs();
  return lvl.broken ? end : unbrokenLevelEndMs();
}

/* ---------- derived objects: level end + entry + stop ---------- */
function levelBreakIdx(price, startMs) {
  const c = current.candles;
  for (let i=0; i<c.timestamp.length; i++) {
    if (c.timestamp[i] < startMs) continue;
    if (c.close[i] > price) return i;
  }
  return null;
}
function computeEntry() {
  entry = null;
  if (!level || !current) { sl = null; updateSlButton(); syncToolButtons(); return; }
  const c = current.candles;
  const i = levelBreakIdx(level.price, level.start_ms);
  if (i != null) {
    level.end_ms = c.timestamp[i] + barMs;
    level.broken = true;
    entry = {idx:i, ms:c.timestamp[i], price:c.close[i]};
  } else {
    level.end_ms = chartDataEndMs();
    level.broken = false;
  }
  if (!entry) sl = null;
  else if (sl) recomputeSlRay();
  updateSlButton();
  syncToolButtons();
}
function recomputeSlRay() {
  if (!sl || !entry || !current) return;
  const c = current.candles;
  sl.hit_ms = null;
  sl.end_ms = c.timestamp[c.timestamp.length-1] + barMs;
  for (let j=entry.idx+1; j<c.timestamp.length; j++) {
    if (c.low[j] <= sl.price) { sl.hit_ms = c.timestamp[j]; sl.end_ms = c.timestamp[j] + barMs; break; }
  }
}
function setOptimalSl() {
  if (!entry || !current) { toast('draw a level first — entry appears on close above it'); return; }
  sl = {price: current.candles.low[entry.idx]};
  recomputeSlRay();
  selectedObj = 'sl';
  renderObjects();
  draw();
  toast('stop-loss at entry-candle low');
}
function updateSlButton() {
  document.getElementById('slBtn').disabled = !entry;
}

/* ---------- tools ---------- */
function toggleTool(name) {
  if (name === 'exit' && !level) { toast('draw a level first — exit is tied to an existing level'); return; }
  tool = tool === name ? null : name;
  drawStep = 0; pending = null;
  clearGhost();
  syncToolButtons();
  if (tool === 'level') toast('level: click the cap candle — price snaps to its high wick');
  if (tool === 'pump') toast('pump: click the start candle, then the culmination');
  if (tool === 'swhigh') toast('swing high: click a candle');
  if (tool === 'swlow') toast('swing low: click a candle');
  if (tool === 'exit') toast('exit: click the exit point');
}
function cancelTool() {
  tool = null; drawStep = 0; pending = null;
  selectedObj = null;
  clearGhost();
  syncToolButtons();
  renderObjects();
  draw();
}
function syncToolButtons() {
  const map = {level:'toolLevel', pump:'toolPump', swhigh:'toolSwingHigh', swlow:'toolSwingLow', exit:'toolExit'};
  if (!level && tool === 'exit') { tool = null; drawStep = 0; pending = null; clearGhost(); }
  for (const [name, id] of Object.entries(map)) {
    const btn = document.getElementById(id);
    const disabled = name === 'exit' && !level;
    btn.disabled = disabled;
    btn.classList.toggle('active', !disabled && tool === name);
    btn.classList.toggle('pending', !disabled && tool === name && drawStep === 1);
  }
  document.body.classList.toggle('drawing', tool !== null);
}

/* ---------- coordinate transforms ---------- */
function plotRefs() {
  const fl = gd()._fullLayout;
  return fl && fl.xaxis && fl.yaxis ? {xa: fl.xaxis, ya: fl.yaxis, fl} : null;
}
function xToPx(ms) {
  const r = plotRefs(); if (!r) return 0;
  const a = pms(r.xa.range[0]), b = pms(r.xa.range[1]);
  return r.xa._offset + (ms - a) / (b - a) * r.xa._length;
}
function yToPx(p) {
  const r = plotRefs(); if (!r) return 0;
  const a = Number(r.ya.range[0]), b = Number(r.ya.range[1]);
  return r.ya._offset + (1 - (p - a) / (b - a)) * r.ya._length;
}
function eventDataPoint(ev) {
  const r = plotRefs(); if (!r) return null;
  const bb = gd().getBoundingClientRect();
  const px = ev.clientX - bb.left, py = ev.clientY - bb.top;
  const inX = px >= r.xa._offset && px <= r.xa._offset + r.xa._length;
  const inY = py >= r.ya._offset && py <= r.ya._offset + r.ya._length;
  const a = pms(r.xa.range[0]), b = pms(r.xa.range[1]);
  const ya = Number(r.ya.range[0]), yb = Number(r.ya.range[1]);
  return {
    ms: a + (px - r.xa._offset) / r.xa._length * (b - a),
    price: ya + (1 - (py - r.ya._offset) / r.ya._length) * (yb - ya),
    inPrice: inX && inY, px, py
  };
}

/* ---------- ghost preview overlay ---------- */
function sizeOverlay() {
  const svg = document.getElementById('ovl');
  const w = gd().clientWidth, h = gd().clientHeight;
  if (svg.getAttribute('width') != w) svg.setAttribute('width', w);
  if (svg.getAttribute('height') != h) svg.setAttribute('height', h);
}
function clearGhost() { document.getElementById('ovl').innerHTML = ''; }
function ghostDot(x, y, color) {
  return `<circle cx="${x}" cy="${y}" r="3.5" fill="${color}" stroke="rgba(17,19,24,.9)" stroke-width="1"/>`;
}
function renderGhost(pt) {
  if (!tool || !current || !pt || !pt.inPrice) { clearGhost(); return; }
  sizeOverlay();
  const r = plotRefs(); if (!r) return;
  const x0 = r.xa._offset, x1 = r.xa._offset + r.xa._length;
  const y0 = r.ya._offset, y1 = r.ya._offset + r.ya._length;
  const c = current.candles;
  let html = '';
  if (tool === 'level') {
    const anchor = levelHighAnchor(pt.ms);
    const price = anchor.price;
    const startMs = anchor.ms;
    const brk = levelBreakIdx(price, startMs);
    const endMs = brk != null ? c.timestamp[brk] + barMs : unbrokenLevelEndMs();
    const ax = xToPx(startMs), bx = xToPx(endMs), gy = yToPx(price);
    html += `<line x1="${ax}" y1="${gy}" x2="${bx}" y2="${gy}" stroke="#8aa0d8" stroke-width="1" stroke-dasharray="5 3"/>`;
    html += `<line x1="${ax}" y1="${y0}" x2="${ax}" y2="${y1}" stroke="rgba(138,160,216,.30)" stroke-width="1" stroke-dasharray="3 3"/>`;
    html += ghostDot(ax, gy, '#8aa0d8');
    if (brk != null) {
      const ey = yToPx(c.close[brk]);
      html += ghostDot(bx, gy, '#7dbb91');
      html += `<text x="${bx+6}" y="${ey-6}" fill="#7dbb91" font-size="11">entry ${fmtPrice(c.close[brk])}</text>`;
    }
    html += `<text x="${ax+6}" y="${gy-6}" fill="#8aa0d8" font-size="11">high ${fmtPrice(price)}</text>`;
  }
  if (tool === 'swhigh' || tool === 'swlow') {
    const i = nearestCandle(pt.ms);
    const isHigh = tool === 'swhigh';
    const price = isHigh ? c.high[i] : c.low[i];
    const color = isHigh ? '#c99a62' : '#7fb4d8';
    const gx = xToPx(c.timestamp[i]), gy = yToPx(price);
    html += `<line x1="${gx}" y1="${y0}" x2="${gx}" y2="${y1}" stroke="rgba(155,154,147,.25)" stroke-width="1" stroke-dasharray="3 3"/>`;
    html += ghostDot(gx, gy, color);
    html += `<text x="${gx+8}" y="${gy + (isHigh ? -8 : 14)}" fill="${color}" font-size="11">${isHigh ? 'high' : 'low'} ${fmtPrice(price)}</text>`;
  }
  if (tool === 'pump') {
    const i = nearestCandle(pt.ms);
    if (drawStep === 0) {
      const gx = xToPx(c.timestamp[i]), gy = yToPx(c.low[i]);
      html += `<line x1="${gx}" y1="${y0}" x2="${gx}" y2="${y1}" stroke="rgba(125,187,145,.28)" stroke-width="1" stroke-dasharray="3 3"/>`;
      html += ghostDot(gx, gy, '#7dbb91');
      html += `<text x="${gx+8}" y="${gy+12}" fill="#7dbb91" font-size="11">low ${fmtPrice(c.low[i])}</text>`;
    } else {
      const ax = xToPx(pending.ms), ay = yToPx(pending.price);
      const bx = xToPx(c.timestamp[i]), by = yToPx(c.high[i]);
      html += `<rect x="${Math.min(ax,bx)}" y="${Math.min(ay,by)}" width="${Math.abs(bx-ax)}" height="${Math.abs(by-ay)}" fill="rgba(125,187,145,.10)" stroke="#7dbb91" stroke-width="1" stroke-dasharray="4 3"/>`;
      html += ghostDot(ax, ay, '#7dbb91') + ghostDot(bx, by, '#7dbb91');
      const movePct = pending.price > 0 ? (c.high[i]/pending.price - 1) * 100 : 0;
      html += `<text x="${bx+8}" y="${by-6}" fill="#7dbb91" font-size="11">high ${fmtPrice(c.high[i])} (+${movePct.toFixed(1)}%)</text>`;
    }
  }
  if (tool === 'exit') {
    const i = nearestCandle(pt.ms);
    const price = snapPrice(pt.ms, pt.price);
    const gx = xToPx(c.timestamp[i]), gy = yToPx(price);
    html += ghostDot(gx, gy, '#a597d6');
    html += `<text x="${gx+8}" y="${gy+4}" fill="#a597d6" font-size="11">${fmtPrice(price)}</text>`;
  }
  document.getElementById('ovl').innerHTML = html;
}

/* ---------- tool clicks ---------- */
function handleToolClick(pt) {
  const c = current.candles;
  if (tool === 'level') {
    const anchor = levelHighAnchor(pt.ms);
    level = {price: anchor.price, start_ms: anchor.ms, end_ms: null, broken: false};
    selectedObj = 'level';
    finishTool();
    computeEntry();
  } else if (tool === 'swhigh' || tool === 'swlow') {
    const i = nearestCandle(pt.ms);
    const type = tool === 'swhigh' ? 'high' : 'low';
    const s = {id: 'sw' + (++swingSeq), type, ms: c.timestamp[i], price: type === 'high' ? c.high[i] : c.low[i]};
    swings.push(s);
    swings.sort((a,b) => a.ms - b.ms);
    selectedObj = 'swing:' + s.id;
    finishTool();
  } else if (tool === 'pump') {
    const i = nearestCandle(pt.ms);
    if (drawStep === 0) {
      pending = {idx: i, ms: c.timestamp[i], price: c.low[i]};
      drawStep = 1;
      syncToolButtons();
      toast('pump start fixed — click the culmination candle');
      renderGhost(pt);
      return;
    }
    let a = pending.idx, b = i;
    if (a === b) b = Math.min(c.timestamp.length - 1, a + 1);
    if (a > b) { const t = a; a = b; b = t; }
    pump = {
      start: {idx: a, ms: c.timestamp[a], price: c.low[a]},
      high:  {idx: b, ms: c.timestamp[b], price: c.high[b]}
    };
    selectedObj = 'pump';
    finishTool();
  } else if (tool === 'exit') {
    const i = nearestCandle(pt.ms);
    exitPoint = {ms: c.timestamp[i], price: snapPrice(pt.ms, pt.price)};
    selectedObj = 'exit';
    finishTool();
  }
  renderObjects();
  draw();
}
function finishTool() {
  tool = null; drawStep = 0; pending = null;
  clearGhost();
  syncToolButtons();
}

/* ---------- object list / selection / delete ---------- */
function renderObjects() {
  const rows = [];
  if (level) rows.push({id:'level', del:true, label:`level ${fmtPrice(level.price)} · ${iso(level.start_ms)} → ${iso(level.end_ms)}`});
  if (pump) {
    const move = pump.start.price > 0 ? pump.high.price/pump.start.price - 1 : NaN;
    rows.push({id:'pump', del:true, label:`pump +${fmtPct(move)} · ${fmtPrice(pump.start.price)} → ${fmtPrice(pump.high.price)}`});
  }
  if (entry) rows.push({id:'entry', del:false, auto:true, label:`entry ${iso(entry.ms)} @ ${fmtPrice(entry.price)}`});
  if (sl && entry) {
    const risk = (entry.price - sl.price) / entry.price;
    const hit = sl.hit_ms ? `hit ${iso(sl.hit_ms)}` : 'not hit';
    rows.push({id:'sl', del:true, label:`stop ${fmtPrice(sl.price)} · risk ${fmtPct(risk)} · ${hit}`});
  }
  if (exitPoint) rows.push({id:'exit', del:true, label:`exit ${iso(exitPoint.ms)} @ ${fmtPrice(exitPoint.price)}`});
  for (const s of [...swings].sort((a,b) => a.ms - b.ms)) {
    rows.push({id:'swing:'+s.id, del:true, label:`swing ${s.type} ${iso(s.ms)} @ ${fmtPrice(s.price)}`});
  }
  const el = document.getElementById('objectsText');
  if (!rows.length) { el.innerText = 'Nothing drawn yet. L = level, P = pump, H/J = swings.'; return; }
  el.innerHTML = `<div class="manual-list">${rows.map(row => `
    <div class="manual-item ${selectedObj === row.id ? 'active' : ''}" onclick="selectObj('${row.id}')">
      <span>${esc(row.label)}</span>
      ${row.auto ? '<span class="auto-tag">auto</span>' : ''}
      ${row.del ? `<button type="button" onclick="event.stopPropagation(); deleteObj('${row.id}')" title="Delete">×</button>` : ''}
    </div>
  `).join('')}</div>`;
}
function selectObj(id) { selectedObj = id; renderObjects(); draw(); }
function deleteObj(id) {
  if (id === 'level') { level = null; computeEntry(); }
  if (id === 'pump') pump = null;
  if (id === 'sl') sl = null;
  if (id === 'exit') exitPoint = null;
  if (id.startsWith('swing:')) { const sid = id.slice(6); swings = swings.filter(s => s.id !== sid); }
  if (selectedObj === id) selectedObj = null;
  renderObjects();
  draw();
}
function deleteSelected() { if (selectedObj && selectedObj !== 'entry') deleteObj(selectedObj); }
function resetAnnotations() {
  if (!visible.length) return;
  loadEvent(idx);
  toast('annotations reset to saved state');
}
function swingNeighbors(id) {
  const sorted = [...swings].sort((a,b) => a.ms - b.ms);
  const i = sorted.findIndex(s => s.id === id);
  return {prev: i > 0 ? sorted[i-1] : null, next: i < sorted.length-1 ? sorted[i+1] : null};
}
function placeSwingAt(s, rawMs) {
  const c = current.candles;
  let j = nearestCandle(rawMs);
  const {prev, next} = swingNeighbors(s.id);
  const jPrev = prev ? nearestCandle(prev.ms) + 1 : 0;
  const jNext = next ? nearestCandle(next.ms) - 1 : c.timestamp.length - 1;
  if (jPrev > jNext) return false;
  j = Math.max(jPrev, Math.min(jNext, j));
  s.ms = c.timestamp[j];
  s.price = s.type === 'high' ? c.high[j] : c.low[j];
  return true;
}

/* ---------- candidates list ---------- */
async function loadCandidates() {
  const r = await fetch('/api/candidates');
  candidates = (await r.json()).candidates;
  enhanceVisibleSelects();
  initDefaultLineHover();
  renderList();
  if (visible.length) loadEvent(0);
}
function filtered() {
  const f = document.getElementById('filter').value;
  const q = document.getElementById('search').value.trim().toLowerCase();
  return candidates.filter(c => (f==='all' || (f==='labeled' ? c.labeled : !c.labeled)) && (!q || searchBlob(c).includes(q)));
}
function renderList() {
  visible = filtered();
  const el = document.getElementById('list');
  el.innerHTML = '';
  visible.forEach((c, i) => {
    const d = document.createElement('div');
    d.className = 'row' + (i===idx ? ' active' : '') + (c.labeled ? ' labeled' : '');
    d.onclick = () => loadEvent(i);
    const variants = c.variants || [c];
    const pumpBadge = c.pump_pct == null ? '' : `<span class="badge hot">${fmtPct(c.pump_pct)}</span>`;
    const tfs = variants.map(v => esc(v.tf)).join('/');
    const note = c.label && c.label.notes ? `<div class="small">note: ${esc(String(c.label.notes).slice(0,90))}</div>` : '';
    const multi = variants.length > 1 ? tfs : variants[0].tf;
    d.innerHTML = `<div class="row-head"><span>${i+1}. ${esc(displaySymbol(c.symbol))}</span><span class="small">${esc(multi)}</span></div>
      <div class="row-badges">${pumpBadge}<span class="badge">${iso(c.review_start_ms)}</span></div>${note}`;
    el.appendChild(d);
  });
  document.getElementById('progress').innerText = `${candidates.filter(c=>c.labeled).length}/${candidates.length} labeled`;
  document.getElementById('jump').max = String(Math.max(1, visible.length));
}
async function loadEvent(i) {
  idx = Math.max(0, Math.min(i, visible.length - 1));
  renderList();
  const group = visible[idx];
  selectedTf = group.default_tf || (group.variants && group.variants[0] && group.variants[0].tf) || group.tf;
  populateTfSelect(group);
  xRange = null; yRange = null; yAuto = true;
  tool = null; drawStep = 0; pending = null; selectedObj = null;
  clearGhost();
  const r = await fetch('/api/candles?event_id=' + encodeURIComponent(group.event_id) + '&tf=' + encodeURIComponent(selectedTf));
  current = await r.json();
  current.group = group;
  computeBarMs();
  const saved = group.label || {};
  level = (saved.level_price != null && saved.level_start_ms != null)
    ? {price:+saved.level_price, start_ms:+saved.level_start_ms, end_ms:null, broken:false} : null;
  swings = Array.isArray(saved.swing_points)
    ? saved.swing_points
        .filter(s => s && Number.isFinite(Number(s.ms)) && Number.isFinite(Number(s.price)) && (s.type === 'high' || s.type === 'low'))
        .map(s => ({id:'sw' + (++swingSeq), type:s.type, ms:Number(s.ms), price:Number(s.price)}))
        .sort((a,b) => a.ms - b.ms)
    : [];
  pump = (saved.pump_start_ms != null && saved.pump_start_price != null && saved.culmination_ms != null && saved.culmination_price != null)
    ? {start:{idx:nearestCandle(+saved.pump_start_ms), ms:+saved.pump_start_ms, price:+saved.pump_start_price},
       high:{idx:nearestCandle(+saved.culmination_ms), ms:+saved.culmination_ms, price:+saved.culmination_price}} : null;
  exitPoint = (saved.exit_ms != null && saved.exit_price != null) ? {ms:+saved.exit_ms, price:+saved.exit_price} : null;
  sl = null;
  computeEntry();
  if (saved.sl_price != null && entry) { sl = {price:+saved.sl_price}; recomputeSlRay(); }
  document.getElementById('notes').value = '';
  document.getElementById('savedNotes').value = saved.notes || '';
  document.getElementById('jump').value = String(idx + 1);
  syncToolButtons();
  renderObjects();
  updateMetrics();
  draw();
}
function populateTfSelect(group) {
  const el = document.getElementById('tfSelect');
  el.innerHTML = '';
  for (const v of (group.variants || [group])) {
    const opt = document.createElement('option');
    opt.value = String(v.tf);
    opt.textContent = `${v.tf}  ${fmtPct(v.pump_pct)}`;
    el.appendChild(opt);
  }
  el.value = selectedTf;
  refreshSelect(el);
}
async function changeTf() {
  if (!visible.length) return;
  selectedTf = document.getElementById('tfSelect').value;
  const group = visible[idx];
  const r = await fetch('/api/candles?event_id=' + encodeURIComponent(group.event_id) + '&tf=' + encodeURIComponent(selectedTf));
  current = await r.json();
  current.group = group;
  computeBarMs();
  const keepSl = sl ? sl.price : null;
  computeEntry();
  if (keepSl != null && entry) { sl = {price: keepSl}; recomputeSlRay(); }
  updateMetrics();
  refreshSelect(document.getElementById('tfSelect'));
  if (yAuto) { draw(); fitY(); } else draw();
  renderObjects();
}
function changeTfBy(delta) {
  const el = document.getElementById('tfSelect');
  if (!el.options.length) return;
  const next = Math.max(0, Math.min(el.options.length - 1, el.selectedIndex + delta));
  if (next === el.selectedIndex) return;
  el.selectedIndex = next;
  changeTf();
}
function initDefaultLineHover() {
  const el = document.getElementById('defaultLinesHover');
  if (!el || el.dataset.ready) return;
  el.dataset.ready = '1';
  el.addEventListener('mouseenter', () => { showDefaultLines = true; el.classList.add('active'); draw(); });
  el.addEventListener('mouseleave', () => { showDefaultLines = false; el.classList.remove('active'); draw(); });
}
function updateMetrics() {
  if (!current) return;
  const g = current.group || visible[idx], e = current.event;
  const items = [
    ['sym', displaySymbol(e.symbol)],
    ['tf', e.tf],
    ['TFs', (g.variants || [g]).map(v => v.tf).join('/')],
    ['pump', fmtPct(e.pump_pct)],
    ['vol', 'x' + Number(e.pump_over_sleep_vol || 0).toFixed(1)],
    ['trd', 'x' + Number(e.pump_over_sleep_trades || 0).toFixed(1)]
  ];
  document.getElementById('metricbar').innerHTML = items.map(([k,v]) => `<span class="metric">${esc(k)} <b>${esc(v)}</b></span>`).join('');
}

/* ---------- chart shapes & annotations ---------- */
function shapes() {
  if (!current) return [];
  const e = current.event, out = [];
  shapeBindings = [];
  function add(shape, binding=null) { out.push(shape); shapeBindings.push(binding); }
  if (showDefaultLines) {
    for (const [k,v] of Object.entries(e.marker_columns || {})) {
      if (k.endsWith('_ms') && Number.isFinite(Number(v))) {
        add({type:'line', layer:'below', editable:false, x0:new Date(v), x1:new Date(v), yref:'paper', y0:0, y1:1, line:{color:'#5f6673', width:1}});
      }
    }
    if (Number.isFinite(Number(e.suggested_level)) && Number.isFinite(Number(e.suggested_level_start_ms)) && Number.isFinite(Number(e.suggested_level_end_ms))) {
      add({type:'line', layer:'below', editable:false, x0:new Date(e.suggested_level_start_ms), x1:new Date(e.suggested_level_end_ms), y0:Number(e.suggested_level), y1:Number(e.suggested_level), line:{color:'#5f6673', width:1}});
    }
  }
  if (pump) {
    const sel = selectedObj === 'pump';
    add({type:'rect', layer:'below', editable:false,
      x0:new Date(Math.min(pump.start.ms, pump.high.ms)), x1:new Date(Math.max(pump.start.ms, pump.high.ms) + barMs),
      y0:Math.min(pump.start.price, pump.high.price), y1:Math.max(pump.start.price, pump.high.price),
      line:{color: sel ? '#a9d8b9' : '#7dbb91', width: 1},
      fillcolor:'rgba(125,187,145,.08)'}, {kind:'pump'});
  }
  if (level) {
    const sel = selectedObj === 'level';
    add({type:'line', layer:'above', editable:true,
      x0:new Date(levelShapeStartMs(level)), x1:new Date(levelShapeEndMs(level)),
      y0:level.price, y1:level.price,
      line:{color: sel ? '#c8d4ff' : '#8aa0d8', width: 1}}, {kind:'level'});
    if (sel) {
      const c = current.candles;
      const yr = Math.max(...c.high) - Math.min(...c.low);
      const dy = Math.max(Math.abs(level.price) * 0.0012, yr * 0.004);
      const dx = Math.max(barMs * 0.25, 60000);
      for (const t of [levelShapeStartMs(level), levelShapeEndMs(level)]) {
        add({type:'rect', layer:'above', editable:false,
             x0:new Date(t - dx), x1:new Date(t + dx),
             y0:level.price - dy, y1:level.price + dy,
             line:{color:'#c8d4ff', width:1}, fillcolor:'rgba(200,212,255,.20)'});
      }
    }
  }
  if (sl && entry) {
    const sel = selectedObj === 'sl';
    add({type:'line', layer:'above', editable:false,
      x0:new Date(entry.ms), x1:new Date(sl.end_ms),
      y0:sl.price, y1:sl.price,
      line:{color: sel ? '#e7a9b6' : '#d18495', width: 1, dash:'solid'}}, {kind:'sl'});
  }
  if (exitPoint) {
    const sel = selectedObj === 'exit';
    const c = current.candles;
    const yr = Math.max(...c.high) - Math.min(...c.low);
    const dy = Math.max(Math.abs(exitPoint.price) * 0.0015, yr * 0.006);
    const dx = Math.max(barMs * 0.5, 2*60000);
    add({type:'circle', layer:'above', editable:true,
      x0:new Date(exitPoint.ms - dx), x1:new Date(exitPoint.ms + dx),
      y0:exitPoint.price - dy, y1:exitPoint.price + dy,
      line:{color: sel ? '#c9bdf0' : '#a597d6', width: 1}, fillcolor:'rgba(165,151,214,.10)'}, {kind:'exit'});
  }
  for (const s of swings) {
    const sel = selectedObj === 'swing:' + s.id;
    const c = current.candles;
    const yr = Math.max(...c.high) - Math.min(...c.low);
    const dy = Math.max(Math.abs(s.price) * 0.0015, yr * 0.006);
    const dx = Math.max(barMs * 0.5, 2*60000);
    const base = s.type === 'high' ? '#c99a62' : '#7fb4d8';
    const hot = s.type === 'high' ? '#e8c299' : '#a9d2ea';
    add({type:'circle', layer:'above', editable:true,
      x0:new Date(s.ms - dx), x1:new Date(s.ms + dx),
      y0:s.price - dy, y1:s.price + dy,
      line:{color: sel ? hot : base, width: 1},
      fillcolor: s.type === 'high' ? 'rgba(201,154,98,.10)' : 'rgba(127,180,216,.10)'}, {kind:'swing', id:s.id});
  }
  return out;
}
function annotations() {
  if (!current) return [];
  const out = [];
  if (level) {
    out.push({x:new Date(levelShapeEndMs(level)), y:level.price, text:fmtPrice(level.price),
      showarrow:false, xanchor:'left', yanchor:'middle', xshift:6,
      font:{size:11, color:'#8aa0d8'}, bgcolor:'rgba(34,43,69,.80)', borderpad:3});
  }
  if (pump && pump.start.price > 0) {
    const move = pump.high.price / pump.start.price - 1;
    out.push({x:new Date(pump.high.ms + barMs), y:pump.high.price, text:`+${(move*100).toFixed(1)}%`,
      showarrow:false, xanchor:'left', yanchor:'bottom',
      font:{size:11, color:'#7dbb91'}, bgcolor:'rgba(23,36,29,.80)', borderpad:3});
  }
  if (entry) {
    out.push({x:new Date(entry.ms), y:entry.price, text:'entry',
      showarrow:true, ax:0, ay:26, arrowcolor:'#7dbb91', arrowwidth:1, arrowhead:2,
      font:{size:11, color:'#7dbb91'}, bgcolor:'rgba(23,36,29,.80)', borderpad:3});
  }
  if (sl && entry && entry.price > 0) {
    const risk = (entry.price - sl.price) / entry.price;
    out.push({x:new Date(entry.ms), y:sl.price, text:`SL −${(risk*100).toFixed(1)}%`,
      showarrow:false, xanchor:'right', yanchor:'middle', xshift:-6,
      font:{size:11, color:'#d18495'}, bgcolor:'rgba(37,25,34,.80)', borderpad:3});
  }
  return out;
}
function draw() {
  if (!current) return;
  const c = current.candles, x = c.timestamp.map(t => new Date(t)), e = current.event;
  document.getElementById('title').innerText = `${displaySymbol(e.symbol)} ${e.tf}`;
  const candle = {type:'candlestick', x, open:c.open, high:c.high, low:c.low, close:c.close, name:'price', xaxis:'x', yaxis:'y',
    increasing:{line:{color:'#7dbb91', width:1}, fillcolor:'rgba(125,187,145,.42)'},
    decreasing:{line:{color:'#c99a62', width:1}, fillcolor:'rgba(201,154,98,.40)'},
    hoverinfo:'none', hovertemplate:'<extra></extra>'};
  const vol = {type:'bar', x, y:c.quote_volume, name:'volume', marker:{color:'#6f7890'}, xaxis:'x', yaxis:'y2', opacity:0.32, hoverinfo:'none', hovertemplate:'<extra></extra>'};
  const title = `pump ${fmtPct(e.pump_pct)} / vol x${Number(e.pump_over_sleep_vol||0).toFixed(1)} / trades x${Number(e.pump_over_sleep_trades||0).toFixed(1)}`;
  const spike = {showspikes:true, spikemode:'across', spikesnap:'cursor', spikedash:'dot', spikethickness:1, spikecolor:'#4a5160'};
  const layout = {
    paper_bgcolor:'#111318', plot_bgcolor:'#171a20', font:{color:'#e7e3d8'},
    margin:{l:56,r:28,t:42,b:38}, title:{text:title, x:0.99, xanchor:'right', font:{size:13}},
    dragmode:'pan', hovermode:false, hoverdistance:-1, spikedistance:-1,
    xaxis:Object.assign({rangeslider:{visible:false}, gridcolor:'#252a32', linecolor:'#2a2e36', zeroline:false}, spike),
    yaxis:Object.assign({domain:[0.24,1], gridcolor:'#252a32', linecolor:'#2a2e36', zeroline:false, title:'price', hoverformat:'.6g', tickformat:priceTickFormat(c.close)}, spike),
    yaxis2:{domain:[0,0.17], gridcolor:'#252a32', linecolor:'#2a2e36', zeroline:false, title:'volume', hoverformat:'.4g', rangemode:'tozero', fixedrange:true},
    shapes: shapes(), annotations: annotations(), showlegend:false, bargap:0.42
  };
  if (xRange) layout.xaxis.range = xRange;
  if (yRange) layout.yaxis.range = yRange;
  Plotly.react('chart', [candle, vol], layout, {responsive:true, displayModeBar:false, displaylogo:false, scrollZoom:false, editable:false, edits:{shapePosition:true}});
  attachChartHandlers();
  positionZones();
}

/* ---------- axis zones: LMB-drag to scale, dblclick to fit ---------- */
function positionZones() {
  const r = plotRefs(); if (!r) return;
  const yz = document.getElementById('yzone'), xz = document.getElementById('xzone');
  yz.style.left = '0px';
  yz.style.width = Math.max(0, r.xa._offset - 1) + 'px';
  yz.style.top = r.ya._offset + 'px';
  yz.style.height = r.ya._length + 'px';
  const mb = r.fl.margin ? r.fl.margin.b : 38;
  xz.style.left = r.xa._offset + 'px';
  xz.style.width = r.xa._length + 'px';
  xz.style.top = (r.fl.height - mb) + 'px';
  xz.style.height = mb + 'px';
}
function clampYRange(r) {
  let a = Number(r[0]), b = Number(r[1]);
  if (!Number.isFinite(a) || !Number.isFinite(b) || b <= a) return r;
  if (a < 0) a = 0;
  if (b <= a) b = a + Math.max(Math.abs(b), 1e-9) * 0.01;
  return [a, b];
}
function guardedRelayout(update) {
  relayoutGuard = true;
  const p = Plotly.relayout('chart', update);
  const done = () => { relayoutGuard = false; };
  if (p && p.finally) p.finally(done); else setTimeout(done, 0);
}
function currentAxisRanges() {
  const r = plotRefs();
  return {
    x: xRange || (r ? r.xa.range : null),
    y: yRange || (r ? r.ya.range : null)
  };
}
function fitY() {
  if (!current) return;
  const c = current.candles;
  if (!c.timestamp.length) return;
  let a, b;
  if (xRange) { a = pms(xRange[0]); b = pms(xRange[1]); }
  else { a = c.timestamp[0]; b = c.timestamp[c.timestamp.length-1] + barMs; }
  let lo = Infinity, hi = -Infinity;
  for (let i=0; i<c.timestamp.length; i++) {
    const t = c.timestamp[i];
    if (t + barMs < a || t > b) continue;
    if (c.low[i] < lo) lo = c.low[i];
    if (c.high[i] > hi) hi = c.high[i];
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) return;
  const pad = Math.max((hi - lo) * 0.08, hi * 0.0005);
  yRange = clampYRange([lo - pad, hi + pad]);
  guardedRelayout({'yaxis.range': yRange});
}
function bindAxisZone(el, axis) {
  el.addEventListener('mousedown', e => {
    if (e.button !== 0) return;
    e.preventDefault(); e.stopPropagation();
    const startPx = axis === 'y' ? e.clientY : e.clientX;
    const ranges = currentAxisRanges();
    if (axis === 'y' && !ranges.y) return;
    if (axis === 'x' && !ranges.x) return;
    const startRange = axis === 'y'
      ? [Number(ranges.y[0]), Number(ranges.y[1])]
      : [pms(ranges.x[0]), pms(ranges.x[1])];
    function move(me) {
      const d = (axis === 'y' ? me.clientY : me.clientX) - startPx;
      const factor = Math.exp((axis === 'y' ? d : -d) * 0.005);
      if (axis === 'y') {
        yAuto = false;
        const mid = (startRange[0] + startRange[1]) / 2;
        const half = (startRange[1] - startRange[0]) / 2 * factor;
        yRange = clampYRange([mid - half, mid + half]);
        guardedRelayout({'yaxis.range': yRange});
      } else {
        const b = startRange[1];
        const span = (startRange[1] - startRange[0]) * factor;
        xRange = [new Date(b - span).toISOString(), new Date(b).toISOString()];
        guardedRelayout({'xaxis.range': xRange});
        if (yAuto) fitY();
      }
    }
    function up() {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
    }
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  });
  el.addEventListener('dblclick', e => {
    e.preventDefault(); e.stopPropagation();
    if (axis === 'y') { yAuto = true; fitY(); }
    else { xRange = null; yAuto = true; guardedRelayout({'xaxis.autorange': true}); setTimeout(fitY, 60); }
  });
}

/* ---------- wheel zoom (TV: X at cursor; Shift=Y; Ctrl=both) ---------- */
function zoomAroundAnchor(a, b, anchor, factor) {
  return [anchor - (anchor - a) * factor, anchor + (b - anchor) * factor];
}
function chartWheelZoom(ev) {
  if (!current) return;
  ev.preventDefault();
  const factor = ev.deltaY < 0 ? 0.85 : 1.18;
  const ranges = currentAxisRanges();
  const r = plotRefs();
  const bb = gd().getBoundingClientRect();
  let fracX = 0.5, fracY = 0.5;
  if (r) {
    fracX = Math.min(1, Math.max(0, (ev.clientX - bb.left - r.xa._offset) / (r.xa._length || 1)));
    fracY = Math.min(1, Math.max(0, 1 - (ev.clientY - bb.top - r.ya._offset) / (r.ya._length || 1)));
  }
  const zoomBoth = ev.ctrlKey || ev.metaKey;
  const zoomY = ev.shiftKey || zoomBoth;
  const zoomX = zoomBoth || !ev.shiftKey;
  const update = {};
  if (zoomX && ranges.x) {
    const a = pms(ranges.x[0]), b = pms(ranges.x[1]);
    const anchor = a + (b - a) * fracX;
    const [na, nb] = zoomAroundAnchor(a, b, anchor, factor);
    xRange = [new Date(na).toISOString(), new Date(nb).toISOString()];
    update['xaxis.range'] = xRange;
  }
  if (zoomY && ranges.y) {
    yAuto = false;
    const a = Number(ranges.y[0]), b = Number(ranges.y[1]);
    const anchor = a + (b - a) * fracY;
    yRange = clampYRange(zoomAroundAnchor(a, b, anchor, factor));
    update['yaxis.range'] = yRange;
  }
  guardedRelayout(update);
  if (zoomX && !zoomY && yAuto) fitY();
}
function resetView() {
  xRange = null; yRange = null; yAuto = true;
  guardedRelayout({'xaxis.autorange': true, 'yaxis.autorange': true, 'yaxis2.autorange': true});
  setTimeout(fitY, 60);
}

/* ---------- chart event wiring ---------- */
let downPos = null;
function attachChartHandlers() {
  if (chartHandlersAttached) return;
  chartHandlersAttached = true;
  const el = gd();
  el.on('plotly_relayout', ev => {
    if (relayoutGuard) return;
    let xChanged = false;
    if (ev['xaxis.range[0]'] && ev['xaxis.range[1]']) { xRange = [ev['xaxis.range[0]'], ev['xaxis.range[1]']]; xChanged = true; }
    if (ev['yaxis.range[0]'] != null && ev['yaxis.range[1]'] != null && !yAuto) {
      yRange = [Number(ev['yaxis.range[0]']), Number(ev['yaxis.range[1]'])];
      const cl = clampYRange(yRange);
      if (cl[0] !== yRange[0] || cl[1] !== yRange[1]) {
        const span = yRange[1] - yRange[0];
        yRange = [0, span]; // slide the window up to the zero floor instead of squashing it
        guardedRelayout({'yaxis.range': yRange});
      }
    }
    if (ev['xaxis.autorange']) { xRange = null; yAuto = true; }
    if (ev['yaxis.autorange']) { yRange = null; yAuto = true; }
    syncDraggedShapes(ev);
    if (xChanged && yAuto) fitY();
    positionZones();
  });
  const wrap = document.getElementById('chartwrap');
  const surface = document.getElementById('ovl');
  wrap.addEventListener('wheel', chartWheelZoom, {passive:false});
  surface.addEventListener('pointerdown', e => {
    if (!tool || !current || e.button !== 0) return;
    const pt = eventDataPoint(e);
    if (!pt || !pt.inPrice) return;
    e.preventDefault();
    e.stopPropagation();
    downPos = {x:e.clientX, y:e.clientY, pointerId:e.pointerId};
    if (surface.setPointerCapture) surface.setPointerCapture(e.pointerId);
    renderGhost(pt);
  }, true);
  surface.addEventListener('pointerup', e => {
    if (!tool || !current || !downPos) return;
    e.preventDefault();
    e.stopPropagation();
    const wasClick = Math.hypot(e.clientX - downPos.x, e.clientY - downPos.y) < 8;
    const pointerId = downPos.pointerId;
    downPos = null;
    if (surface.releasePointerCapture && pointerId != null) {
      try { surface.releasePointerCapture(pointerId); } catch (_) {}
    }
    if (!wasClick) return;
    const pt = eventDataPoint(e);
    if (!pt || !pt.inPrice) { toast('click inside the price panel'); return; }
    handleToolClick(pt);
  }, true);
  surface.addEventListener('pointermove', e => {
    if (!tool || !current) return;
    renderGhost(eventDataPoint(e));
  }, true);
  surface.addEventListener('pointerleave', () => { if (tool && !downPos) clearGhost(); }, true);
  bindAxisZone(document.getElementById('yzone'), 'y');
  bindAxisZone(document.getElementById('xzone'), 'x');
  new ResizeObserver(() => positionZones()).observe(document.getElementById('chartwrap'));
}
function syncDraggedShapes(ev) {
  const changed = new Set();
  for (const key of Object.keys(ev)) {
    const m = key.match(/^shapes\[(\d+)\]\./);
    if (m) changed.add(Number(m[1]));
  }
  if (!changed.size) return;
  const shapesNow = gd()._fullLayout && gd()._fullLayout.shapes ? gd()._fullLayout.shapes : [];
  let dirty = false;
  for (const i of changed) {
    const binding = shapeBindings[i];
    const shape = shapesNow[i];
    if (!binding || !shape) continue;
    if (binding.kind === 'level' && level) {
      const rawA = pms(shape.x0), rawB = pms(shape.x1);
      const anchor = levelHighAnchor(Math.min(rawA, rawB));
      level = {price: anchor.price, start_ms: anchor.ms, end_ms: null, broken: false};
      selectedObj = 'level';
      computeEntry();
      dirty = true;
    }
    if (binding.kind === 'exit' && exitPoint) {
      exitPoint = {
        ms: (pms(shape.x0) + pms(shape.x1)) / 2,
        price: (Number(shape.y0) + Number(shape.y1)) / 2
      };
      selectedObj = 'exit';
      dirty = true;
    }
    if (binding.kind === 'swing') {
      const s = swings.find(x => x.id === binding.id);
      if (s) {
        const rawMs = (pms(shape.x0) + pms(shape.x1)) / 2;
        if (!placeSwingAt(s, rawMs)) toast('swing is pinned between its neighbours');
        swings.sort((a,b) => a.ms - b.ms);
        selectedObj = 'swing:' + s.id;
        dirty = true;
      }
    }
  }
  if (dirty) { renderObjects(); draw(); }
}

/* ---------- save ---------- */
function payload(hasLevel) {
  const e = current.event;
  const group = current.group || visible[idx];
  const withLevel = hasLevel && !!level;
  const out = {
    event_id: group.event_id, symbol: e.symbol, tf: e.tf, has_level: withLevel,
    source_event_id: e.event_id,
    source_event_ids: group.source_event_ids || [e.event_id],
    selected_tf: e.tf,
    level_price: withLevel ? level.price : null,
    level_start_ms: withLevel ? level.start_ms : null,
    level_end_ms: withLevel ? level.end_ms : null,
    family: document.getElementById('family').value,
    quality: document.getElementById('quality').value,
    notes: document.getElementById('notes').value,
    pump_start_ms: pump ? pump.start.ms : null,
    pump_start_price: pump ? pump.start.price : null,
    culmination_ms: pump ? pump.high.ms : null,
    culmination_price: pump ? pump.high.price : null,
    entry_ms: entry ? entry.ms : null,
    entry_price: entry ? entry.price : null,
    entry_auto: entry ? true : null,
    exit_ms: exitPoint ? exitPoint.ms : null,
    exit_price: exitPoint ? exitPoint.price : null,
    sl_price: (sl && entry) ? sl.price : null,
    sl_ms: (sl && entry) ? entry.ms : null,
    sl_hit_ms: (sl && entry) ? sl.hit_ms : null,
    sl_auto: (sl && entry) ? true : null,
    swing_points: [...swings].sort((a,b) => a.ms - b.ms).map(s => ({id:s.id, type:s.type, ms:Math.round(s.ms), price:Number(s.price), manual:true})),
    level_broken: level ? !!level.broken : null,
    source: 'browser_level_labeler'
  };
  return out;
}
async function postLabel(p) {
  const r = await fetch('/api/label', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(p)});
  const j = await r.json();
  if (!j.ok) { alert(JSON.stringify(j)); return; }
  const c = candidates.find(x => x.event_id === p.event_id); if (c) { c.labeled = true; c.label = j.label; }
  toast('saved'); renderList(); nextEvent();
}
function saveLevel() {
  if (!level) { toast('no level drawn — press L and draw it, or save as no level [N]'); return; }
  postLabel(payload(true));
}
function saveNoLevel() { postLabel(payload(false)); }
function saveNoSleepPump() {
  const p = payload(false);
  p.quality = 'bad';
  p.rejection_reason = 'no_sleep_pump_transition';
  const note = document.getElementById('notes').value.trim();
  p.notes = note ? `no_sleep_pump_transition | ${note}` : 'no_sleep_pump_transition';
  postLabel(p);
}

/* ---------- navigation ---------- */
function nextEvent() { if (idx < visible.length - 1) loadEvent(idx + 1); }
function prevEvent() { if (idx > 0) loadEvent(idx - 1); }
function nextUnlabeled() {
  const pos = visible.findIndex((c, i) => i > idx && !c.labeled);
  if (pos >= 0) loadEvent(pos);
  else {
    const wrap = visible.findIndex(c => !c.labeled);
    if (wrap >= 0) loadEvent(wrap); else toast('all visible candidates labeled');
  }
}
function jumpKey(e) {
  if (e.key === 'Enter') {
    const n = Number(document.getElementById('jump').value);
    if (Number.isFinite(n)) loadEvent(n - 1);
  }
}
function toggleFocus() {
  document.body.classList.toggle('focus');
  setTimeout(() => { Plotly.Plots.resize(gd()); positionZones(); }, 80);
}
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === 'Delete' || e.key === 'Backspace') { deleteSelected(); return; }
  if (e.key === 'Escape') { cancelTool(); return; }
  if (e.key === '0') { resetView(); return; }
  const k = e.key.toLowerCase();
  if (k === 'l') toggleTool('level');
  if (k === 'p') toggleTool('pump');
  if (k === 'h') toggleTool('swhigh');
  if (k === 'j') toggleTool('swlow');
  if (k === 'e') toggleTool('exit');
  if (k === 'x') setOptimalSl();
  if (k === 'z') resetAnnotations();
  if (e.key === '[') changeTfBy(-1);
  if (e.key === ']') changeTfBy(1);
  if (e.key === 'ArrowRight') nextEvent();
  if (e.key === 'ArrowLeft') prevEvent();
  if (k === 's') saveLevel();
  if (k === 'n') saveNoLevel();
  if (k === 't') saveNoSleepPump();
  if (k === 'u') nextUnlabeled();
  if (k === 'f') toggleFocus();
});
loadCandidates();
</script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve_level_labeler(
        candidates_path=args.candidates,
        labels_path=args.labels,
        cache_dir=args.cache_dir,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
