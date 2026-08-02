"""Reproducible foundation audit for expert-reviewed BRK/CAP marks."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from anomaly_science.annotation.ohlcv import load_ohlcv_parquet, resample_ohlcv_np
from anomaly_science.strategy.triple_tap.research.brkcap_foundations import (
    BrkCapStructure,
    CandleSeries,
    PumpTransition,
    ResistanceLevel,
    SetupFamily,
    audit_structure,
)


DEFAULT_REVIEW_DIR = Path(".output/results/triple_tap_v1/bot_brkcap_review")
DEFAULT_CACHE_DIR = Path(".output/market/binance_vision/um_futures/enriched_1m")
GEOMETRY_FIELDS = (
    "family",
    "has_level",
    "has_pump_transition",
    "level_price",
    "level_start_ms",
    "level_end_ms",
    "pump_start_ms",
    "pump_start_price",
    "culmination_ms",
    "culmination_price",
)


def _effective_rows(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        event_id = str(row["event_id"])
        if row.get("deleted"):
            rows.pop(event_id, None)
        else:
            rows[event_id] = row
    return rows


def _first_setup(row: dict[str, Any]) -> dict[str, Any] | None:
    setups = row.get("setups")
    if not isinstance(setups, list) or not setups or not isinstance(setups[0], dict):
        return None
    return setups[0]


def _was_edited(original: dict[str, Any], reviewed: dict[str, Any]) -> bool:
    original_setup = _first_setup(original)
    reviewed_setup = _first_setup(reviewed)
    if original_setup is None or reviewed_setup is None:
        return original != reviewed
    if original_setup.get("notes") != reviewed_setup.get("notes"):
        return True
    return any(original_setup.get(field) != reviewed_setup.get(field) for field in GEOMETRY_FIELDS)


def _to_structure(setup: dict[str, Any]) -> BrkCapStructure | None:
    required_pump = ("pump_start_ms", "pump_start_price", "culmination_ms", "culmination_price")
    if any(setup.get(field) is None for field in required_pump):
        return None
    family_raw = str(setup.get("family") or "")
    if family_raw not in {SetupFamily.BREAKOUT.value, SetupFamily.CAP.value}:
        return None
    level = None
    if all(setup.get(field) is not None for field in ("level_start_ms", "level_end_ms", "level_price")):
        level = ResistanceLevel(
            start_ms=int(setup["level_start_ms"]),
            end_ms=int(setup["level_end_ms"]),
            price=float(setup["level_price"]),
        )
    return BrkCapStructure(
        family=SetupFamily(family_raw),
        pump=PumpTransition(
            start_ms=int(setup["pump_start_ms"]),
            start_price=float(setup["pump_start_price"]),
            culmination_ms=int(setup["culmination_ms"]),
            culmination_price=float(setup["culmination_price"]),
        ),
        level=level,
    )


def _candles(cache_dir: Path, symbol: str, tf: str) -> CandleSeries:
    minutes = int(tf[:-1]) if tf.endswith("m") else int(tf[:-1]) * 60
    frame = load_ohlcv_parquet(cache_dir / f"{symbol}.parquet")
    if minutes != 1:
        frame = resample_ohlcv_np(frame, minutes)
    return CandleSeries(
        timestamp=np.asarray(frame["timestamp"], dtype=np.int64),
        open=np.asarray(frame["open"], dtype=np.float64),
        high=np.asarray(frame["high"], dtype=np.float64),
        low=np.asarray(frame["low"], dtype=np.float64),
        close=np.asarray(frame["close"], dtype=np.float64),
    )


def build_review_audit(
    *,
    review_dir: Path = DEFAULT_REVIEW_DIR,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    symbol_prefixes: tuple[str, ...] = ("A", "B"),
) -> dict[str, Any]:
    """Audit only explicitly edited rows in the declared calibration prefixes."""

    originals = _effective_rows(review_dir / "marks.jsonl")
    reviews = _effective_rows(review_dir / "review_comments.jsonl")
    records: list[dict[str, Any]] = []
    candle_cache: dict[tuple[str, str], CandleSeries] = {}

    for reviewed in reviews.values():
        symbol = str(reviewed.get("symbol") or "")
        if not symbol.startswith(symbol_prefixes):
            continue
        source_event_id = str(reviewed.get("source_event_id") or "")
        original = originals.get(source_event_id)
        if original is None or not _was_edited(original, reviewed):
            continue
        machine_setup = _first_setup(original)
        reviewed_setup = _first_setup(reviewed)
        if machine_setup is None or reviewed_setup is None:
            continue
        tf = str(reviewed.get("selected_tf") or reviewed.get("tf") or original.get("tf"))
        key = (symbol, tf)
        if key not in candle_cache:
            candle_cache[key] = _candles(cache_dir, symbol, tf)
        candles = candle_cache[key]
        machine_structure = _to_structure(machine_setup)
        reviewed_structure = _to_structure(reviewed_setup)
        machine_audit = audit_structure(candles, machine_structure) if machine_structure else None
        reviewed_audit = audit_structure(candles, reviewed_structure) if reviewed_structure else None
        records.append(
            {
                "symbol": symbol,
                "tf": tf,
                "source_event_id": source_event_id,
                "review_event_id": str(reviewed.get("event_id")),
                "comment": str(reviewed_setup.get("notes") or ""),
                "machine": asdict(machine_audit) if machine_audit else None,
                "reviewed": asdict(reviewed_audit) if reviewed_audit else None,
                "geometry_changes": {
                    field: {"machine": machine_setup.get(field), "reviewed": reviewed_setup.get(field)}
                    for field in GEOMETRY_FIELDS
                    if machine_setup.get(field) != reviewed_setup.get(field)
                },
            }
        )

    records.sort(key=lambda row: (row["symbol"], row["tf"], row["source_event_id"]))
    violation_counts: dict[str, int] = {}
    for row in records:
        for violation in (row.get("machine") or {}).get("violations", []):
            violation_counts[str(violation)] = violation_counts.get(str(violation), 0) + 1
    return {
        "scope": {"symbol_prefixes": list(symbol_prefixes), "edited_rows_only": True},
        "reviewed_cases": len(records),
        "machine_violation_counts": dict(sorted(violation_counts.items())),
        "records": records,
    }


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# BRK/CAP foundation audit — A/B calibration set",
        "",
        "This report audits price geometry only. It does not inspect outcomes or PnL.",
        "Prefixes C–Z remain outside the calibration set.",
        "",
        f"Edited cases: **{payload['reviewed_cases']}**",
        "",
        "## Machine violations",
        "",
        "| Violation | Cases |",
        "|---|---:|",
    ]
    for violation, count in payload["machine_violation_counts"].items():
        lines.append(f"| `{violation}` | {count} |")
    lines += [
        "",
        "## Cases",
        "",
        "| Symbol | TF | Machine violations | First invalidating close | Comment |",
        "|---|---:|---|---:|---|",
    ]
    for row in payload["records"]:
        machine = row.get("machine") or {}
        violations = ", ".join(f"`{item}`" for item in machine.get("violations", [])) or "—"
        first_break = (machine.get("metrics") or {}).get("first_close_above_level_ms") or "—"
        comment = row["comment"].replace("\n", " / ").replace("|", "\\|")
        lines.append(f"| {row['symbol']} | {row['tf']} | {violations} | {first_break} | {comment} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit edited BRK/CAP review marks against foundation contracts.")
    parser.add_argument("--review-dir", type=Path, default=DEFAULT_REVIEW_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--symbol-prefix", action="append", default=None)
    parser.add_argument("--out-json", type=Path, default=None)
    parser.add_argument("--out-md", type=Path, default=None)
    args = parser.parse_args()
    payload = build_review_audit(
        review_dir=args.review_dir,
        cache_dir=args.cache_dir,
        symbol_prefixes=tuple(args.symbol_prefix or ("A", "B")),
    )
    out_json = args.out_json or args.review_dir / "foundation_audit_ab.json"
    out_md = args.out_md or args.review_dir / "foundation_audit_ab.md"
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    out_md.write_text(_render_markdown(payload), encoding="utf-8")
    print(f"foundation audit -> {out_json}")
    print(f"foundation report -> {out_md}")


if __name__ == "__main__":
    main()
