"""Read-only access to annotation iteration artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anomaly_science.annotation.desk.json_io import read_json_list, read_json_object
from anomaly_science.annotation.desk.result_annotations import ResultAnnotationStore


@dataclass(frozen=True, slots=True)
class IterationArtifactRepository:
    project_root: Path
    strategy_id: str

    def latest_path(self) -> Path:
        return (
            self.project_root
            / ".output"
            / "results"
            / "annotation_iterations"
            / self.strategy_id
            / "latest.json"
        )

    def latest_run_dir(self) -> Path | None:
        latest_path = self.latest_path()
        if not latest_path.exists():
            return None
        latest = read_json_object(latest_path)
        raw_run_dir = latest.get("run_dir")
        if raw_run_dir is None:
            raise ValueError(f"latest iteration pointer is missing run_dir: {latest_path}")
        run_dir = Path(str(raw_run_dir))
        return run_dir if run_dir.is_absolute() else (self.project_root / run_dir).resolve()

    def latest_payload(self) -> dict[str, Any]:
        latest_path = self.latest_path()
        if not latest_path.exists():
            return {"exists": False}
        latest = read_json_object(latest_path)
        run_dir = self.latest_run_dir()
        if run_dir is None:
            return {"exists": False}

        payload: dict[str, Any] = {"exists": True, "latest": latest}
        for key in ("manifest", "candidate_summary", "label_progress", "iteration_report"):
            raw_path = latest.get(key)
            if raw_path is None and key == "candidate_summary":
                raw_path = run_dir / "candidate_summary.json"
            if raw_path is None:
                payload[key] = None
                continue
            path = Path(str(raw_path))
            if not path.is_absolute():
                path = self.project_root / path
            if key == "iteration_report":
                payload[key] = path.read_text(encoding="utf-8") if path.exists() else None
            else:
                payload[key] = read_json_object(path) if path.is_file() else None

        dashboard = run_dir / "trade_report" / "dashboard.json"
        if dashboard.exists():
            payload["dashboard"] = read_json_object(dashboard)
        report = run_dir / "trade_report" / "triple_tap_current_trade_report.json"
        if report.exists():
            payload["trade_report"] = read_json_object(report)
        return payload

    def trades_path(self, run_dir: Path) -> Path:
        return run_dir / "trade_report" / "trades.json"

    def read_trades(self, run_dir: Path) -> list[dict[str, Any]]:
        return read_json_list(self.trades_path(run_dir))

    def latest_trades(self) -> list[dict[str, Any]]:
        run_dir = self.latest_run_dir()
        if run_dir is None:
            return []
        annotations = ResultAnnotationStore.for_run_dir(run_dir).read_effective()
        trades = self.read_trades(run_dir)
        for trade in trades:
            ann = annotations.get(str(trade.get("trade_id")))
            trade["has_result_annotation"] = ann is not None
            trade["result_comment"] = ann.get("comment") if ann else ""
        return trades

    def latest_trade_by_id(self, trade_id: str) -> tuple[Path, dict[str, Any]] | None:
        run_dir = self.latest_run_dir()
        if run_dir is None:
            return None
        for trade in self.read_trades(run_dir):
            if str(trade.get("trade_id")) == trade_id:
                return run_dir, trade
        return None
