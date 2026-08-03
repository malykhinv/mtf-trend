"""HTTP server for the browser level desk."""

from __future__ import annotations

import argparse
import json
import threading
import traceback
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pandas as pd

from anomaly_science.annotation.boundary.repository import BoundaryReviewRepository
from anomaly_science.annotation.boundary.ui import BOUNDARY_LABELER_HTML
from anomaly_science.annotation.desk.candidates import DEFAULT_TF_MINUTES, build_annotation_groups, validate_candidates
from anomaly_science.annotation.desk.candles import OhlcvWindowService
from anomaly_science.annotation.desk.clock import utc_stamp
from anomaly_science.annotation.desk.iterations import IterationArtifactRepository
from anomaly_science.annotation.desk.labels import LabelStore, label_for_group
from anomaly_science.annotation.desk.result_annotations import ResultAnnotationStore
from anomaly_science.annotation.desk.structural_trades import StructuralTradeRepository
from anomaly_science.annotation.desk.ui import LABELER_HTML, LAUNCHER_HTML
from anomaly_science.annotation.review_questions import ReviewQuestion, questions_payload, validate_review_answers
from anomaly_science.annotation.schemas import ANNOTATION_CANDIDATE_SCHEMA_VERSION


def json_response(handler: BaseHTTPRequestHandler, payload: object, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


@dataclass
class StrategyContext:
    strategy_id: str
    title: str
    candidates: pd.DataFrame
    by_event: pd.DataFrame
    groups: list
    group_by_id: dict
    allowed_event_ids: set
    label_store: LabelStore
    seed_marks: dict[str, dict[str, Any]]
    review_questions: tuple[ReviewQuestion, ...]
    iteration_artifacts: IterationArtifactRepository


class LevelLabelerServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address,
        handler_cls,
        *,
        apps: list[tuple] | None = None,
        cache_dir: Path,
        tf_minutes: dict[str, int],
        default_strategy_id: str | None = None,
        show_launcher: bool = False,
        project_root: Path | None = None,
        candidates_path: Path | None = None,
        labels_path: Path | None = None,
        strategy_id: str = "manual_level_annotation",
        strategy_title: str = "Manual level annotation",
    ):
        super().__init__(server_address, handler_cls)
        if apps is None:  # legacy single-strategy construction
            if candidates_path is None or labels_path is None:
                raise ValueError("provide apps or candidates_path+labels_path")
            apps = [(strategy_id, strategy_title, candidates_path, labels_path)]
        self.cache_dir = cache_dir
        self.tf_minutes = tf_minutes
        self.show_launcher = show_launcher
        self.project_root = (project_root or Path.cwd()).resolve()
        self.structural_trades = StructuralTradeRepository(self.project_root)

        self.iteration_status: dict[str, object] = {
            "state": "idle",
            "message": "ready",
            "started_at_utc": None,
            "finished_at_utc": None,
            "run_dir": None,
            "error": None,
            "steps": [],
        }
        self.iteration_lock = threading.Lock()
        self.iteration_thread: threading.Thread | None = None
        self.labels_lock = threading.Lock()
        self.result_annotations_lock = threading.Lock()
        self.boundary_labels_lock = threading.Lock()
        self.ohlcv = OhlcvWindowService(cache_dir, tf_minutes)
        boundary_root = self.project_root / ".output" / "research" / "visible_resistance_is_2025_v1"
        self.boundary = BoundaryReviewRepository(
            candidates_path=boundary_root / "candidates.parquet",
            labels_path=boundary_root / "labels.jsonl",
            ohlcv=self.ohlcv,
        )

        self.strategies: dict[str, StrategyContext] = {}
        self.strategy_order: list[str] = []
        for app_spec in apps:
            if len(app_spec) not in (4, 5, 6):
                raise ValueError(
                    "app must provide strategy id, title, candidates path, labels path, "
                    "optional marks path, and optional review questions"
                )
            strategy_id, title, candidates_path, labels_path = app_spec[:4]
            marks_path = app_spec[4] if len(app_spec) >= 5 else None
            review_questions = tuple(app_spec[5]) if len(app_spec) == 6 else ()
            if not all(isinstance(question, ReviewQuestion) for question in review_questions):
                raise ValueError("review questions must contain ReviewQuestion values")
            candidates = pd.read_parquet(candidates_path)
            validate_candidates(candidates)
            if "candidate_schema_version" not in candidates.columns:
                candidates["candidate_schema_version"] = ANNOTATION_CANDIDATE_SCHEMA_VERSION
            by_event = candidates.set_index("event_id", drop=False)
            groups = build_annotation_groups(candidates, self.tf_minutes)
            group_by_id = {str(g["event_id"]): g for g in groups}
            seed_marks = LabelStore(marks_path).read_effective() if marks_path is not None and marks_path.exists() else {}
            self.strategies[strategy_id] = StrategyContext(
                strategy_id=strategy_id,
                title=title,
                candidates=candidates,
                by_event=by_event,
                groups=groups,
                group_by_id=group_by_id,
                allowed_event_ids=set(str(x) for x in by_event.index) | set(group_by_id),
                label_store=LabelStore(labels_path),
                seed_marks=seed_marks,
                review_questions=review_questions,
                iteration_artifacts=IterationArtifactRepository(self.project_root, strategy_id),
            )
            self.strategy_order.append(strategy_id)
        if not self.strategy_order:
            raise ValueError("at least one annotation strategy is required")
        self.default_strategy_id = default_strategy_id or self.strategy_order[0]

    def strategy(self, strategy_id: str | None) -> StrategyContext:
        if strategy_id and strategy_id in self.strategies:
            return self.strategies[strategy_id]
        return self.strategies[self.default_strategy_id]

    def available_tfs(self) -> list[str]:
        return [
            str(tf)
            for tf, minutes in sorted(self.tf_minutes.items(), key=lambda item: (int(item[1]), str(item[0])))
            if int(minutes) > 0
        ]

    def strategy_payload(self, ctx: StrategyContext) -> dict[str, Any]:
        return {
            "strategy_id": ctx.strategy_id,
            "title": ctx.title,
            "candidate_count": len(ctx.groups),
            "candidate_rows": int(len(ctx.candidates)),
            "available_tfs": self.available_tfs(),
            "labels_path": str(ctx.label_store.path),
            "seed_marks": len(ctx.seed_marks),
            "review_questions": questions_payload(ctx.review_questions),
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

    def _resolve_ctx(self, parsed) -> None:
        strategy_id = parse_qs(parsed.query).get("strategy", [None])[0]
        self.ctx = self.server.strategy(strategy_id)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        self._resolve_ctx(parsed)
        if parsed.path == "/":
            self._html_response(LAUNCHER_HTML if self.server.show_launcher else LABELER_HTML)
            return
        if parsed.path == "/labeler":
            self._html_response(LABELER_HTML)
            return
        if parsed.path == "/boundary":
            self._html_response(BOUNDARY_LABELER_HTML)
            return
        if parsed.path == "/plotly.min.js":
            self._plotly_response()
            return
        if parsed.path == "/api/candidates":
            self._json_or_error(
                lambda: {"candidates": self._candidate_payloads(), "available_tfs": self.server.available_tfs()}
            )
            return
        if parsed.path == "/api/strategies":
            json_response(self, {"strategies": [
                self.server.strategy_payload(self.server.strategy(sid)) for sid in self.server.strategy_order
            ]})
            return
        if parsed.path == "/api/labels":
            self._json_or_error(self._labels_payload)
            return
        if parsed.path == "/api/iteration/status":
            with self.server.iteration_lock:
                json_response(self, dict(self.server.iteration_status))
            return
        if parsed.path == "/api/iteration/latest":
            self._json_or_error(self.ctx.iteration_artifacts.latest_payload)
            return
        if parsed.path == "/api/iteration/trades":
            self._json_or_error(lambda: {"trades": self.ctx.iteration_artifacts.latest_trades()})
            return
        if parsed.path == "/api/iteration/trade_candles":
            self._handle_trade_candles(parsed.query)
            return
        if parsed.path == "/api/futures_structural/trades":
            self._json_or_error(
                lambda: {
                    "trades": self.server.structural_trades.list_rows(),
                    "summary": self.server.structural_trades.summary(),
                }
            )
            return
        if parsed.path == "/api/futures_structural/trade_candles":
            self._handle_structural_trade_candles(parsed.query)
            return
        if parsed.path == "/api/boundary/candidates":
            self._json_or_error(self.server.boundary.list_payload)
            return
        if parsed.path == "/api/boundary/candles":
            self._handle_boundary_candles(parsed.query)
            return
        if parsed.path == "/api/candles":
            self._handle_event_candles(parsed.query)
            return
        if parsed.path == "/api/candles_range":
            self._handle_candles_range(parsed.query)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        self._resolve_ctx(parsed)
        if parsed.path == "/api/iteration/start":
            self._start_iteration()
            return
        if parsed.path == "/api/result_annotation":
            self._save_result_annotation()
            return
        if parsed.path == "/api/futures_structural/review":
            self._save_structural_trade_review()
            return
        if parsed.path == "/api/boundary/label":
            self._save_boundary_label()
            return
        if parsed.path == "/api/boundary/unlabel":
            self._unlabel_boundary()
            return
        if parsed.path == "/api/unlabel":
            self._unlabel_event()
            return
        if parsed.path == "/api/label":
            self._save_label()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _html_response(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _plotly_response(self) -> None:
        import plotly

        path = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/javascript")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_payload(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(n).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def _json_or_error(self, producer) -> None:
        try:
            payload = producer()
            json_response(self, payload)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            json_response(self, {"error": str(exc)}, 500)
            return

    def _labels_payload(self) -> dict[str, Any]:
        with self.server.labels_lock:
            labels = self.ctx.label_store.read_effective_list()
        return {"labels": labels}

    def _candidate_payloads(self) -> list[dict[str, Any]]:
        with self.server.labels_lock:
            labels = self.ctx.label_store.read_effective()
        payload = []
        for group in self.ctx.groups:
            row = dict(group)
            label = label_for_group(row, labels)
            seed_label = label_for_group(row, self.ctx.seed_marks)
            row["labeled"] = label is not None
            row["label"] = label
            row["seed_label"] = seed_label
            payload.append(row)
        return payload

    def _handle_event_candles(self, query: str) -> None:
        qs = parse_qs(query)
        event_id = qs.get("event_id", [""])[0]
        tf = qs.get("tf", [""])[0]
        row = self._candidate_row(event_id, tf)
        if row is None:
            json_response(self, {"error": "unknown event_id"}, 404)
            return
        try:
            payload = self.server.ohlcv.event_payload(row, tf=tf or None)
        except ValueError as exc:
            json_response(self, {"error": str(exc)}, 400)
            return
        except (OSError, KeyError, TypeError) as exc:
            json_response(self, {"error": str(exc)}, 500)
            return
        json_response(self, payload)

    def _handle_candles_range(self, query: str) -> None:
        qs = parse_qs(query)
        symbol = qs.get("symbol", [""])[0]
        tf = qs.get("tf", [""])[0]
        if not symbol or not tf:
            json_response(self, {"error": "symbol and tf are required"}, 400)
            return
        try:
            start_ms = int(qs.get("start_ms", ["0"])[0])
            end_ms = int(qs.get("end_ms", ["0"])[0])
        except ValueError:
            json_response(self, {"error": "start_ms/end_ms must be integers"}, 400)
            return
        try:
            payload = self.server.ohlcv.candles_range(symbol, tf, start_ms, end_ms)
        except ValueError as exc:
            json_response(self, {"error": str(exc)}, 400)
            return
        except (OSError, KeyError, TypeError) as exc:
            json_response(self, {"error": str(exc)}, 500)
            return
        json_response(self, payload)

    def _handle_trade_candles(self, query: str) -> None:
        qs = parse_qs(query)
        trade_id = qs.get("trade_id", [""])[0]
        if not trade_id:
            json_response(self, {"error": "trade_id is required"}, 400)
            return
        try:
            payload = self._trade_candles_payload(trade_id)
        except ValueError as exc:
            json_response(self, {"error": str(exc)}, 400)
            return
        except (OSError, KeyError, TypeError) as exc:
            json_response(self, {"error": str(exc)}, 500)
            return
        if payload is None:
            json_response(self, {"error": "unknown trade_id"}, 404)
            return
        json_response(self, payload)

    def _handle_structural_trade_candles(self, query: str) -> None:
        trade_id = parse_qs(query).get("trade_id", [""])[0]
        if not trade_id:
            json_response(self, {"error": "trade_id is required"}, 400)
            return
        try:
            trade = self.server.structural_trades.trade_by_id(trade_id)
            if trade is None:
                json_response(self, {"error": "unknown structural trade_id"}, 404)
                return
            with self.server.result_annotations_lock:
                review = self.server.structural_trades.review_for_trade(trade_id)
            payload = self.server.ohlcv.trade_payload(trade=trade, result_annotation=review)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            json_response(self, {"error": str(exc)}, 500)
            return
        json_response(self, payload)

    def _handle_boundary_candles(self, query: str) -> None:
        qs = parse_qs(query)
        event_id = qs.get("event_id", [""])[0]
        tf = qs.get("tf", [""])[0]
        if not event_id:
            json_response(self, {"error": "event_id is required"}, 400)
            return
        try:
            payload = self.server.boundary.candle_payload(event_id, tf)
        except ValueError as exc:
            json_response(self, {"error": str(exc)}, 400)
            return
        except (OSError, KeyError, TypeError) as exc:
            json_response(self, {"error": str(exc)}, 500)
            return
        if payload is None:
            json_response(self, {"error": "unknown boundary event_id"}, 404)
            return
        json_response(self, payload)

    def _start_iteration(self) -> None:
        with self.server.iteration_lock:
            if self.server.iteration_status.get("state") == "running":
                json_response(self, {"ok": True, "status": dict(self.server.iteration_status)})
                return
            self.server.iteration_status = {
                "state": "running",
                "message": "starting local iteration",
                "started_at_utc": utc_stamp(),
                "finished_at_utc": None,
                "run_dir": None,
                "error": None,
                "steps": [{"at": utc_stamp(), "message": "queued"}],
            }

        iteration_strategy_id = self.ctx.strategy_id

        def worker() -> None:
            try:
                with self.server.iteration_lock:
                    self.server.iteration_status["message"] = "summarizing labels and running strategy diagnostics"
                    self.server.iteration_status["steps"].append({"at": utc_stamp(), "message": "running"})
                from anomaly_science.annotation.iteration import AnnotationIterationConfig, run_annotation_iteration

                paths = run_annotation_iteration(
                    AnnotationIterationConfig(
                        project_root=self.server.project_root,
                        strategy_id=iteration_strategy_id,
                        run_trade_report=True,
                    )
                )
                with self.server.iteration_lock:
                    self.server.iteration_status.update(
                        {
                            "state": "complete",
                            "message": "iteration complete",
                            "finished_at_utc": utc_stamp(),
                            "run_dir": str(paths.run_dir),
                            "error": None,
                        }
                    )
                    self.server.iteration_status["steps"].append({"at": utc_stamp(), "message": "complete"})
            except Exception as exc:  # noqa: BLE001 - API status must preserve the failure
                with self.server.iteration_lock:
                    self.server.iteration_status.update(
                        {
                            "state": "error",
                            "message": "iteration failed",
                            "finished_at_utc": utc_stamp(),
                            "error": str(exc),
                            "traceback": traceback.format_exc(),
                        }
                    )
                    self.server.iteration_status["steps"].append({"at": utc_stamp(), "message": f"error: {exc}"})

        thread = threading.Thread(target=worker, name="annotation-iteration", daemon=True)
        self.server.iteration_thread = thread
        thread.start()
        with self.server.iteration_lock:
            json_response(self, {"ok": True, "status": dict(self.server.iteration_status)})

    def _unlabel_event(self) -> None:
        try:
            payload = self._read_payload()
            event_id = str(payload.get("event_id") or "")
            event_ids = self._event_ids_for_unlabel(event_id)
            with self.server.labels_lock:
                self.ctx.label_store.append_unlabels(event_ids, allowed_event_ids=self.ctx.allowed_event_ids)
        except (ValueError, KeyError) as exc:
            json_response(self, {"error": str(exc)}, 400)
            return
        except OSError as exc:
            json_response(self, {"error": str(exc)}, 500)
            return
        json_response(self, {"ok": True, "label": None})

    def _save_label(self) -> None:
        try:
            payload = self._read_payload()
            validate_review_answers(payload, self.ctx.review_questions)
            with self.server.labels_lock:
                row = self.ctx.label_store.append_label(payload, allowed_event_ids=self.ctx.allowed_event_ids)
        except ValueError as exc:
            json_response(self, {"error": str(exc)}, 400)
            return
        except OSError as exc:
            json_response(self, {"error": str(exc)}, 500)
            return
        json_response(self, {"ok": True, "label": row})

    def _save_result_annotation(self) -> None:
        try:
            run_dir = self.ctx.iteration_artifacts.latest_run_dir()
            if run_dir is None:
                raise ValueError("no iteration run exists")
            payload = self._read_payload()
            trades = self.ctx.iteration_artifacts.read_trades(run_dir)
            known_trade_ids = {str(trade.get("trade_id")) for trade in trades}
            with self.server.result_annotations_lock:
                row = ResultAnnotationStore.for_run_dir(run_dir).append(payload, known_trade_ids=known_trade_ids)
        except ValueError as exc:
            json_response(self, {"error": str(exc)}, 400)
            return
        except OSError as exc:
            json_response(self, {"error": str(exc)}, 500)
            return
        json_response(self, {"ok": True, "annotation": row})

    def _save_structural_trade_review(self) -> None:
        try:
            payload = self._read_payload()
            with self.server.result_annotations_lock:
                row = self.server.structural_trades.save_review(payload)
        except ValueError as exc:
            json_response(self, {"error": str(exc)}, 400)
            return
        except OSError as exc:
            json_response(self, {"error": str(exc)}, 500)
            return
        json_response(self, {"ok": True, "review": row})

    def _save_boundary_label(self) -> None:
        try:
            payload = self._read_payload()
            with self.server.boundary_labels_lock:
                row = self.server.boundary.save(payload)
        except ValueError as exc:
            json_response(self, {"error": str(exc)}, 400)
            return
        except (OSError, KeyError, TypeError) as exc:
            json_response(self, {"error": str(exc)}, 500)
            return
        json_response(self, {"ok": True, "label": row})

    def _unlabel_boundary(self) -> None:
        try:
            payload = self._read_payload()
            event_id = str(payload.get("event_id") or "")
            with self.server.boundary_labels_lock:
                row = self.server.boundary.unlabel(event_id)
        except ValueError as exc:
            json_response(self, {"error": str(exc)}, 400)
            return
        except OSError as exc:
            json_response(self, {"error": str(exc)}, 500)
            return
        json_response(self, {"ok": True, "label": row})

    def _event_ids_for_unlabel(self, event_id: str) -> list[str]:
        if event_id not in self.ctx.allowed_event_ids:
            raise ValueError("unknown event_id")
        event_ids = [event_id]
        if event_id in self.ctx.group_by_id:
            event_ids.extend(str(x) for x in self.ctx.group_by_id[event_id].get("source_event_ids", []))
        return list(dict.fromkeys(event_ids))

    def _candidate_row(self, event_id: str, tf: str) -> pd.Series | None:
        if event_id in self.ctx.group_by_id:
            group = self.ctx.group_by_id[event_id]
            variants = group.get("variants", [])
            selected = None
            if tf:
                selected = next((v for v in variants if str(v["tf"]) == tf), None)
            if selected is None and variants:
                selected = variants[0]
            if selected is None:
                return None
            source_id = str(selected["event_id"])
            if source_id not in self.ctx.by_event.index:
                return None
            row = self.ctx.by_event.loc[source_id].copy()
            row["review_start_ms"] = group["review_start_ms"]
            row["review_end_ms"] = group["review_end_ms"]
            return row
        if event_id in self.ctx.by_event.index:
            return self.ctx.by_event.loc[event_id]
        return None

    def _trade_candles_payload(self, trade_id: str) -> dict[str, Any] | None:
        found = self.ctx.iteration_artifacts.latest_trade_by_id(trade_id)
        if found is None:
            return None
        run_dir, trade = found
        with self.server.result_annotations_lock:
            annotations = ResultAnnotationStore.for_run_dir(run_dir).read_effective()
        return self.server.ohlcv.trade_payload(trade=trade, result_annotation=annotations.get(trade_id))


def serve_level_labeler(
    *,
    candidates_path: Path | None = None,
    labels_path: Path | None = None,
    cache_dir: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    tf_minutes: dict[str, int] | None = None,
    strategy_id: str = "manual_level_annotation",
    strategy_title: str = "Manual level annotation",
    show_launcher: bool = False,
    project_root: Path | None = None,
    apps: list[tuple] | None = None,
    default_strategy_id: str | None = None,
) -> None:
    """Serve one or more annotation strategies from a single desk.

    Pass ``apps`` (list of ``(strategy_id, title, candidates_path, labels_path,
    optional_marks_path, optional_review_questions)``) for a multi-strategy desk switchable in the UI; or the single
    ``candidates_path``/``labels_path`` form for a one-strategy desk.
    """
    if apps is None:
        if candidates_path is None or labels_path is None:
            raise ValueError("provide either apps or candidates_path+labels_path")
        apps = [(strategy_id, strategy_title, candidates_path, labels_path)]
    server = LevelLabelerServer(
        (host, port),
        LevelLabelerHandler,
        apps=apps,
        cache_dir=cache_dir,
        tf_minutes=tf_minutes or DEFAULT_TF_MINUTES,
        default_strategy_id=default_strategy_id,
        show_launcher=show_launcher,
        project_root=project_root,
    )
    print(f"serving level labeler on http://{host}:{port}", flush=True)
    for app_spec in apps:
        sid, _title, cpath, lpath = app_spec[:4]
        print(f"  strategy {sid}: candidates={cpath}  labels={lpath}", flush=True)
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve manual market-structure level annotation UI.")
    parser.add_argument("positional", nargs="*", type=Path, help="Optional candidates labels cache_dir paths.")
    parser.add_argument("--candidates", type=Path, default=None)
    parser.add_argument("--labels", type=Path, default=None)
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    candidates_path = args.candidates
    labels_path = args.labels
    cache_dir = args.cache_dir
    if args.positional:
        if len(args.positional) != 3:
            parser.error("expected either --candidates/--labels/--cache-dir or three positional paths")
        candidates_path, labels_path, cache_dir = args.positional
    if candidates_path is None or labels_path is None or cache_dir is None:
        parser.error("--candidates, --labels, and --cache-dir are required")
    serve_level_labeler(
        candidates_path=candidates_path,
        labels_path=labels_path,
        cache_dir=cache_dir,
        host=args.host,
        port=args.port,
    )
