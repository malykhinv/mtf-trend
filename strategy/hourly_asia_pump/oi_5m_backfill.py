from __future__ import annotations

import argparse
import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from strategy.hourly_asia_pump.market_regime_dataset import _load_symbol_map, _resolve_raw_symbol
from strategy.hourly_asia_pump.recent_derivatives_loader import _resolve_anomaly_db_path

module_logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "oi_5m_backfill"
RAW_DIR = OUTPUT_DIR / "raw"
MANIFEST_PATH = OUTPUT_DIR / "manifest.json"
PROGRESS_PATH = OUTPUT_DIR / "progress.csv"
SUMMARY_PATH = OUTPUT_DIR / "summary.csv"

BINANCE_FAPI_BASE = "https://fapi.binance.com"
HTTP_TIMEOUT_SECONDS = 20
REQUEST_SLEEP_SECONDS = 0.12
RETRY_SLEEP_SECONDS = 1.50
RETRY_ATTEMPTS = 4
MAX_LIMIT = 500
FIVE_MINUTES_MS = 5 * 60 * 1000


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _write_csv_atomic(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(tmp, index=False)
    tmp.replace(path)


def _append_progress(row: dict[str, object]) -> None:
    frame = pd.DataFrame([row])
    if PROGRESS_PATH.exists():
        frame.to_csv(PROGRESS_PATH, mode="a", index=False, header=False)
    else:
        frame.to_csv(PROGRESS_PATH, index=False)


def _load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        return {"symbols": {}, "updated_at_utc": None}
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _save_manifest(manifest: dict[str, Any]) -> None:
    manifest["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    _write_json_atomic(MANIFEST_PATH, manifest)


def _safe_float(value: object) -> float | None:
    if value is None or value is pd.NA:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric) or math.isinf(numeric):
        return None
    return numeric


def _http_get_json(session: requests.Session, *, url: str, params: dict[str, object], logger: logging.Logger) -> Any:
    last_exc: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            response = session.get(url, params=params, timeout=HTTP_TIMEOUT_SECONDS)
            if response.status_code == 200:
                time.sleep(REQUEST_SLEEP_SECONDS)
                return response.json()
            body = response.text[:300]
            if response.status_code == 400 and "parameter 'endTime' is invalid" in body:
                raise RuntimeError(f"status=400 body={body}")
            if response.status_code in {418, 429}:
                sleep_s = RETRY_SLEEP_SECONDS * attempt
                logger.warning("oi-backfill rate-limit: status=%s sleep=%.2fs symbol=%s", response.status_code, sleep_s, params.get("symbol"))
                time.sleep(sleep_s)
                continue
            raise RuntimeError(f"status={response.status_code} body={body}")
        except Exception as exc:
            if "parameter 'endTime' is invalid" in str(exc):
                raise
            last_exc = exc
            sleep_s = RETRY_SLEEP_SECONDS * attempt
            logger.warning("oi-backfill retry: attempt=%s sleep=%.2fs symbol=%s cause=%s", attempt, sleep_s, params.get('symbol'), exc)
            time.sleep(sleep_s)
    raise RuntimeError(f"HTTP fetch failed: url={url} params={params} cause={last_exc}")


def _load_recent_symbols(days: int) -> list[str]:
    frame = pd.read_csv(_resolve_anomaly_db_path(), low_memory=False, usecols=["symbol", "timestamp_ms", "dataset"])
    frame["timestamp_ms"] = pd.to_numeric(frame["timestamp_ms"], errors="coerce")
    frame = frame.dropna(subset=["symbol", "timestamp_ms", "dataset"]).copy()
    frame["timestamp_ms"] = frame["timestamp_ms"].astype("int64")
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    frame = frame[(frame["dataset"].astype(str) == "current") & (frame["timestamp_ms"] >= cutoff_ms)].copy()
    summary = (
        frame.groupby("symbol", as_index=False)
        .size()
        .sort_values("size", ascending=False)
    )
    return summary["symbol"].astype(str).tolist()


def _normalize_oi_frame(payload: Any) -> pd.DataFrame:
    rows = pd.DataFrame(payload if isinstance(payload, list) else [])
    if rows.empty:
        return rows
    rows = rows.rename(
        columns={
            "timestamp": "timestamp_ms",
            "sumOpenInterest": "open_interest_contracts",
            "sumOpenInterestValue": "open_interest_value",
        }
    )
    rows["timestamp_ms"] = pd.to_numeric(rows["timestamp_ms"], errors="coerce")
    rows["open_interest_contracts"] = pd.to_numeric(rows.get("open_interest_contracts"), errors="coerce")
    rows["open_interest_value"] = pd.to_numeric(rows.get("open_interest_value"), errors="coerce")
    rows = rows.dropna(subset=["timestamp_ms"]).copy()
    rows["timestamp_ms"] = rows["timestamp_ms"].astype("int64")
    return rows.sort_values("timestamp_ms").reset_index(drop=True)


def _cache_path(raw_symbol: str) -> Path:
    return RAW_DIR / f"{raw_symbol}.csv"


def _load_existing_cache(raw_symbol: str) -> pd.DataFrame:
    path = _cache_path(raw_symbol)
    if not path.exists():
        return pd.DataFrame()
    try:
        frame = pd.read_csv(path)
    except Exception:
        return pd.DataFrame()
    if frame.empty or "timestamp_ms" not in frame.columns:
        return pd.DataFrame()
    frame["timestamp_ms"] = pd.to_numeric(frame["timestamp_ms"], errors="coerce")
    frame["open_interest_contracts"] = pd.to_numeric(frame.get("open_interest_contracts"), errors="coerce")
    frame["open_interest_value"] = pd.to_numeric(frame.get("open_interest_value"), errors="coerce")
    frame = frame.dropna(subset=["timestamp_ms"]).copy()
    frame["timestamp_ms"] = frame["timestamp_ms"].astype("int64")
    return frame.drop_duplicates(subset=["timestamp_ms"]).sort_values("timestamp_ms").reset_index(drop=True)


def _save_symbol_cache(raw_symbol: str, existing: pd.DataFrame, batch: pd.DataFrame) -> pd.DataFrame:
    if existing.empty:
        merged = batch.copy()
    elif batch.empty:
        merged = existing.copy()
    else:
        merged = pd.concat([existing, batch], ignore_index=True)
    if merged.empty:
        _write_csv_atomic(_cache_path(raw_symbol), merged)
        return merged
    merged = merged.drop_duplicates(subset=["timestamp_ms"]).sort_values("timestamp_ms").reset_index(drop=True)
    _write_csv_atomic(_cache_path(raw_symbol), merged)
    return merged


def _mark_symbol(
    manifest: dict[str, Any],
    *,
    symbol: str,
    raw_symbol: str,
    status: str,
    rows: int,
    oldest_ts: int | None,
    newest_ts: int | None,
    stop_reason: str | None = None,
    note: str | None = None,
) -> None:
    manifest.setdefault("symbols", {})[symbol] = {
        "raw_symbol": raw_symbol,
        "status": status,
        "rows": rows,
        "oldest_ts": oldest_ts,
        "newest_ts": newest_ts,
        "stop_reason": stop_reason,
        "note": note,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    _save_manifest(manifest)


def _write_summary(manifest: dict[str, Any]) -> None:
    rows: list[dict[str, object]] = []
    for symbol, state in manifest.get("symbols", {}).items():
        rows.append({"symbol": symbol, **state})
    _write_csv_atomic(SUMMARY_PATH, pd.DataFrame(rows))


def _fetch_backwards_until_unavailable(
    *,
    session: requests.Session,
    raw_symbol: str,
    initial_end_ms: int,
    logger: logging.Logger,
    existing: pd.DataFrame,
) -> tuple[pd.DataFrame, str, str | None]:
    merged = existing.copy()
    cursor_end = int(initial_end_ms)
    if not merged.empty:
        oldest_existing = int(merged["timestamp_ms"].min())
        cursor_end = oldest_existing - FIVE_MINUTES_MS

    status = "done"
    stop_reason: str | None = None

    while cursor_end > 0:
        try:
            payload = _http_get_json(
                session,
                url=f"{BINANCE_FAPI_BASE}/futures/data/openInterestHist",
                params={"symbol": raw_symbol, "period": "5m", "endTime": cursor_end, "limit": MAX_LIMIT},
                logger=logger,
            )
        except RuntimeError as exc:
            message = str(exc)
            if "parameter 'endTime' is invalid" in message or "code\":-1130" in message:
                stop_reason = "endtime_invalid"
                break
            status = "error"
            stop_reason = message[:200]
            break

        batch = _normalize_oi_frame(payload)
        if batch.empty:
            stop_reason = "empty_payload"
            break

        oldest_batch = int(batch["timestamp_ms"].min())
        newest_batch = int(batch["timestamp_ms"].max())
        if not merged.empty and newest_batch >= int(merged["timestamp_ms"].min()):
            batch = batch[batch["timestamp_ms"] < int(merged["timestamp_ms"].min())].copy()
        merged = _save_symbol_cache(raw_symbol, merged, batch)
        _append_progress(
            {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "raw_symbol": raw_symbol,
                "rows_total": int(len(merged)),
                "batch_rows": int(len(batch)),
                "batch_oldest_ts": oldest_batch,
                "batch_newest_ts": newest_batch,
                "cursor_end_after": oldest_batch - FIVE_MINUTES_MS,
                "status": "chunk_saved",
            }
        )
        if batch.empty:
            stop_reason = "fully_overlapped"
            break
        if oldest_batch >= cursor_end:
            stop_reason = "no_progress"
            break
        cursor_end = oldest_batch - FIVE_MINUTES_MS

    return merged, status, stop_reason


def run(*, days: int = 31, symbol_limit: int | None = None) -> dict[str, Path]:
    logger = module_logger
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    symbols = _load_recent_symbols(days)
    if symbol_limit is not None and symbol_limit > 0:
        symbols = symbols[:symbol_limit]
    if not symbols:
        raise ValueError("No recent symbols found for OI backfill")

    manifest = _load_manifest()
    symbol_map = _load_symbol_map(logger)
    session = requests.Session()
    initial_end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    for index, symbol in enumerate(symbols, start=1):
        raw_symbol = _resolve_raw_symbol(symbol, symbol_map)
        if not raw_symbol:
            _mark_symbol(
                manifest,
                symbol=symbol,
                raw_symbol="",
                status="skipped",
                rows=0,
                oldest_ts=None,
                newest_ts=None,
                stop_reason="raw_symbol_missing",
            )
            continue

        state = manifest.get("symbols", {}).get(symbol, {})
        if state.get("status") == "done":
            continue

        logger.info("oi-backfill: %s/%s symbol=%s raw=%s", index, len(symbols), symbol, raw_symbol)
        existing = _load_existing_cache(raw_symbol)
        merged, status, stop_reason = _fetch_backwards_until_unavailable(
            session=session,
            raw_symbol=raw_symbol,
            initial_end_ms=initial_end_ms,
            logger=logger,
            existing=existing,
        )
        oldest_ts = int(merged["timestamp_ms"].min()) if not merged.empty else None
        newest_ts = int(merged["timestamp_ms"].max()) if not merged.empty else None
        _mark_symbol(
            manifest,
            symbol=symbol,
            raw_symbol=raw_symbol,
            status=status,
            rows=int(len(merged)),
            oldest_ts=oldest_ts,
            newest_ts=newest_ts,
            stop_reason=stop_reason,
        )
        _append_progress(
            {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol,
                "raw_symbol": raw_symbol,
                "rows_total": int(len(merged)),
                "oldest_ts": oldest_ts,
                "newest_ts": newest_ts,
                "status": status,
                "stop_reason": stop_reason,
            }
        )
        _write_summary(manifest)

    return {
        "manifest": MANIFEST_PATH,
        "progress": PROGRESS_PATH,
        "summary": SUMMARY_PATH,
        "raw_dir": RAW_DIR,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill recent 5m OI newest->oldest until Binance stops serving it.")
    parser.add_argument("--days", type=int, default=31)
    parser.add_argument("--symbol-limit", type=int, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    paths = run(days=args.days, symbol_limit=args.symbol_limit)
    for key, value in paths.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
