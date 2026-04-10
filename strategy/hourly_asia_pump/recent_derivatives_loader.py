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

module_logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
ANOMALY_DB_PATHS = (
    REPO_ROOT / ".output" / "results_prev_year_5m" / "anomaly_category_lab" / "anomaly_feature_database_with_accumulation.csv",
    REPO_ROOT / ".output" / "results_prev_year_5m" / "anomaly_category_lab" / "anomaly_feature_database.csv",
)
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "recent_derivatives_loader"
RAW_DIR = OUTPUT_DIR / "raw"
MANIFEST_PATH = OUTPUT_DIR / "manifest.json"
PROGRESS_PATH = OUTPUT_DIR / "progress.csv"

BINANCE_FAPI_BASE = "https://fapi.binance.com"
HTTP_TIMEOUT_SECONDS = 20
REQUEST_SLEEP_SECONDS = 0.12
RETRY_SLEEP_SECONDS = 1.50
RETRY_ATTEMPTS = 4
MAX_LIMIT = 500
FIVE_MINUTES_MS = 5 * 60 * 1000
LOOKBACK_FOR_WINDOW_MS = 24 * 60 * 60 * 1000

ENDPOINTS = ("oi", "taker", "global_ls", "top_account", "top_position", "basis", "premium", "funding")


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


def _resolve_anomaly_db_path() -> Path:
    for path in ANOMALY_DB_PATHS:
        if path.exists():
            return path
    raise FileNotFoundError("Не найден anomaly feature database")


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


def _http_get_json(session: requests.Session, *, url: str, params: dict[str, object], logger: logging.Logger) -> Any:
    last_exc: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            response = session.get(url, params=params, timeout=HTTP_TIMEOUT_SECONDS)
            if response.status_code == 200:
                time.sleep(REQUEST_SLEEP_SECONDS)
                return response.json()
            body = response.text[:300]
            if response.status_code in {418, 429}:
                sleep_s = RETRY_SLEEP_SECONDS * attempt
                logger.warning("recent-derivatives rate-limit: status=%s sleep=%.2fs url=%s", response.status_code, sleep_s, url)
                time.sleep(sleep_s)
                continue
            raise RuntimeError(f"status={response.status_code} body={body}")
        except Exception as exc:
            last_exc = exc
            sleep_s = RETRY_SLEEP_SECONDS * attempt
            logger.warning("recent-derivatives retry: attempt=%s sleep=%.2fs url=%s cause=%s", attempt, sleep_s, url, exc)
            time.sleep(sleep_s)
    raise RuntimeError(f"HTTP fetch failed: url={url} params={params} cause={last_exc}")


def _load_recent_anomalies(days: int) -> pd.DataFrame:
    frame = pd.read_csv(_resolve_anomaly_db_path(), low_memory=False, usecols=["symbol", "timestamp_ms", "dataset", "session_id"])
    frame["timestamp_ms"] = pd.to_numeric(frame["timestamp_ms"], errors="coerce")
    frame = frame.dropna(subset=["symbol", "timestamp_ms", "dataset"]).copy()
    frame["timestamp_ms"] = frame["timestamp_ms"].astype("int64")
    now = datetime.now(timezone.utc)
    cutoff_ms = int((now - timedelta(days=days)).timestamp() * 1000)
    frame = frame[(frame["dataset"].astype(str) == "current") & (frame["timestamp_ms"] >= cutoff_ms)].copy()
    return frame.reset_index(drop=True)


def _merge_windows(event_timestamps: list[int], *, lookback_ms: int, buffer_ms: int = FIVE_MINUTES_MS) -> list[tuple[int, int]]:
    windows = sorted((max(0, ts - lookback_ms), ts + buffer_ms) for ts in event_timestamps)
    if not windows:
        return []
    merged: list[list[int]] = [[windows[0][0], windows[0][1]]]
    for start_ms, end_ms in windows[1:]:
        last = merged[-1]
        if start_ms <= last[1] + FIVE_MINUTES_MS:
            last[1] = max(last[1], end_ms)
        else:
            merged.append([start_ms, end_ms])
    return [(start_ms, end_ms) for start_ms, end_ms in merged]


def _normalize_endpoint_frame(endpoint: str, payload: Any) -> pd.DataFrame:
    if endpoint == "premium":
        rows: list[dict[str, object]] = []
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, list) or len(item) < 5:
                    continue
                rows.append(
                    {
                        "timestamp_ms": int(item[0]),
                        "premium_open": _safe_float(item[1]),
                        "premium_high": _safe_float(item[2]),
                        "premium_low": _safe_float(item[3]),
                        "premium_close": _safe_float(item[4]),
                    }
                )
        return pd.DataFrame(rows)

    rows = pd.DataFrame(payload if isinstance(payload, list) else [])
    if rows.empty:
        return rows
    if endpoint == "oi":
        rows = rows.rename(columns={"timestamp": "timestamp_ms", "sumOpenInterest": "open_interest_contracts", "sumOpenInterestValue": "open_interest_value"})
    elif endpoint == "taker":
        rows = rows.rename(columns={"timestamp": "timestamp_ms", "buySellRatio": "taker_buy_sell_ratio", "buyVol": "taker_buy_vol", "sellVol": "taker_sell_vol"})
    elif endpoint == "global_ls":
        rows = rows.rename(columns={"timestamp": "timestamp_ms", "longShortRatio": "global_long_short_ratio", "longAccount": "global_long_account", "shortAccount": "global_short_account"})
    elif endpoint == "top_account":
        rows = rows.rename(columns={"timestamp": "timestamp_ms", "longShortRatio": "top_account_long_short_ratio", "longAccount": "top_account_long", "shortAccount": "top_account_short"})
    elif endpoint == "top_position":
        rows = rows.rename(columns={"timestamp": "timestamp_ms", "longShortRatio": "top_position_long_short_ratio", "longAccount": "top_position_long", "shortAccount": "top_position_short"})
    elif endpoint == "basis":
        rows = rows.rename(columns={"timestamp": "timestamp_ms", "basisRate": "basis_rate", "annualizedBasisRate": "annualized_basis_rate", "futuresPrice": "futures_price", "indexPrice": "index_price"})

    if "timestamp_ms" in rows.columns:
        rows["timestamp_ms"] = pd.to_numeric(rows["timestamp_ms"], errors="coerce")
        rows = rows.dropna(subset=["timestamp_ms"]).copy()
        rows["timestamp_ms"] = rows["timestamp_ms"].astype("int64")
    return rows


def _fetch_oi_full_recent(
    *,
    session: requests.Session,
    raw_symbol: str,
    start_ms: int,
    end_ms: int,
    logger: logging.Logger,
) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    cursor_end = int(end_ms)
    while cursor_end >= start_ms:
        payload = _http_get_json(
            session,
            url=f"{BINANCE_FAPI_BASE}/futures/data/openInterestHist",
            params={"symbol": raw_symbol, "period": "5m", "endTime": cursor_end, "limit": MAX_LIMIT},
            logger=logger,
        )
        batch = _normalize_endpoint_frame("oi", payload)
        if batch.empty:
            break
        batch = batch[(batch["timestamp_ms"] >= start_ms) & (batch["timestamp_ms"] <= end_ms)].copy()
        if batch.empty:
            break
        rows.append(batch)
        oldest_ts = int(batch["timestamp_ms"].min())
        if oldest_ts <= start_ms:
            break
        cursor_end = oldest_ts - FIVE_MINUTES_MS
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True).drop_duplicates(subset=["timestamp_ms"]).sort_values("timestamp_ms").reset_index(drop=True)


def _fetch_endpoint_windows(
    *,
    session: requests.Session,
    endpoint: str,
    raw_symbol: str,
    windows: list[tuple[int, int]],
    logger: logging.Logger,
) -> pd.DataFrame:
    if endpoint == "funding":
        start_ms = min(start for start, _ in windows)
        end_ms = max(end for _, end in windows)
        payload = _http_get_json(
            session,
            url=f"{BINANCE_FAPI_BASE}/fapi/v1/fundingRate",
            params={"symbol": raw_symbol, "startTime": start_ms, "endTime": end_ms, "limit": 1000},
            logger=logger,
        )
        frame = pd.DataFrame(payload if isinstance(payload, list) else [])
        if frame.empty:
            return frame
        frame = frame.rename(columns={"fundingTime": "timestamp_ms", "fundingRate": "funding_rate", "markPrice": "mark_price"})
        frame["timestamp_ms"] = pd.to_numeric(frame["timestamp_ms"], errors="coerce")
        frame["funding_rate"] = pd.to_numeric(frame["funding_rate"], errors="coerce")
        frame["mark_price"] = pd.to_numeric(frame["mark_price"], errors="coerce")
        return frame.dropna(subset=["timestamp_ms"]).astype({"timestamp_ms": "int64"}).drop_duplicates(subset=["timestamp_ms"]).sort_values("timestamp_ms").reset_index(drop=True)

    rows: list[pd.DataFrame] = []
    for start_ms, end_ms in windows:
        cursor_end = int(end_ms)
        while cursor_end >= start_ms:
            if endpoint == "premium":
                url = f"{BINANCE_FAPI_BASE}/fapi/v1/premiumIndexKlines"
                params = {"symbol": raw_symbol, "interval": "5m", "endTime": cursor_end, "limit": MAX_LIMIT}
            elif endpoint == "taker":
                url = f"{BINANCE_FAPI_BASE}/futures/data/takerlongshortRatio"
                params = {"symbol": raw_symbol, "period": "5m", "endTime": cursor_end, "limit": MAX_LIMIT}
            elif endpoint == "global_ls":
                url = f"{BINANCE_FAPI_BASE}/futures/data/globalLongShortAccountRatio"
                params = {"symbol": raw_symbol, "period": "5m", "endTime": cursor_end, "limit": MAX_LIMIT}
            elif endpoint == "top_account":
                url = f"{BINANCE_FAPI_BASE}/futures/data/topLongShortAccountRatio"
                params = {"symbol": raw_symbol, "period": "5m", "endTime": cursor_end, "limit": MAX_LIMIT}
            elif endpoint == "top_position":
                url = f"{BINANCE_FAPI_BASE}/futures/data/topLongShortPositionRatio"
                params = {"symbol": raw_symbol, "period": "5m", "endTime": cursor_end, "limit": MAX_LIMIT}
            elif endpoint == "basis":
                url = f"{BINANCE_FAPI_BASE}/futures/data/basis"
                params = {"pair": raw_symbol, "contractType": "PERPETUAL", "period": "5m", "endTime": cursor_end, "limit": MAX_LIMIT}
            else:
                raise ValueError(f"Unsupported endpoint: {endpoint}")

            payload = _http_get_json(session, url=url, params=params, logger=logger)
            batch = _normalize_endpoint_frame(endpoint, payload)
            if batch.empty:
                break
            batch = batch[(batch["timestamp_ms"] >= start_ms) & (batch["timestamp_ms"] <= end_ms)].copy()
            if batch.empty:
                break
            rows.append(batch)
            oldest_ts = int(batch["timestamp_ms"].min())
            if oldest_ts <= start_ms:
                break
            cursor_end = oldest_ts - FIVE_MINUTES_MS
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True).drop_duplicates(subset=["timestamp_ms"]).sort_values("timestamp_ms").reset_index(drop=True)


def _endpoint_cache_path(endpoint: str, raw_symbol: str) -> Path:
    return RAW_DIR / endpoint / f"{raw_symbol}.csv"


def _mark_done(manifest: dict[str, Any], symbol: str, endpoint: str, *, rows: int, status: str, note: str | None = None) -> None:
    symbol_state = manifest.setdefault("symbols", {}).setdefault(symbol, {})
    symbol_state[endpoint] = {
        "status": status,
        "rows": rows,
        "note": note,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    _save_manifest(manifest)


def run(*, days: int = 14, endpoints: tuple[str, ...] = ENDPOINTS, symbol_limit: int | None = None) -> dict[str, Path]:
    logger = module_logger
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    anomalies = _load_recent_anomalies(days)
    if anomalies.empty:
        raise ValueError("Нет current anomalies в recent window")
    if symbol_limit is not None and symbol_limit > 0:
        top_symbols = (
            anomalies.groupby("symbol", as_index=False)
            .size()
            .sort_values("size", ascending=False)
            .head(symbol_limit)["symbol"]
            .astype(str)
            .tolist()
        )
        anomalies = anomalies[anomalies["symbol"].isin(top_symbols)].copy()

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    symbol_map = _load_symbol_map(logger)
    manifest = _load_manifest()
    session = requests.Session()

    grouped = anomalies.groupby("symbol", sort=True)
    total_symbols = grouped.ngroups
    for index, (symbol, scoped) in enumerate(grouped, start=1):
        raw_symbol = _resolve_raw_symbol(symbol, symbol_map)
        if not raw_symbol:
            _mark_done(manifest, symbol, "symbol_map", rows=0, status="skipped", note="raw_symbol_missing")
            continue

        event_timestamps = sorted(pd.to_numeric(scoped["timestamp_ms"], errors="coerce").dropna().astype("int64").tolist())
        windows = _merge_windows(event_timestamps, lookback_ms=LOOKBACK_FOR_WINDOW_MS)
        logger.info("recent-derivatives: symbol=%s raw=%s %s/%s windows=%s", symbol, raw_symbol, index, total_symbols, len(windows))

        for endpoint in endpoints:
            endpoint_state = manifest.get("symbols", {}).get(symbol, {}).get(endpoint, {})
            if endpoint_state.get("status") == "done" and _endpoint_cache_path(endpoint, raw_symbol).exists():
                continue
            try:
                if endpoint == "oi":
                    frame = _fetch_oi_full_recent(
                        session=session,
                        raw_symbol=raw_symbol,
                        start_ms=cutoff_ms,
                        end_ms=now_ms,
                        logger=logger,
                    )
                else:
                    frame = _fetch_endpoint_windows(
                        session=session,
                        endpoint=endpoint,
                        raw_symbol=raw_symbol,
                        windows=windows,
                        logger=logger,
                    )
                _write_csv_atomic(_endpoint_cache_path(endpoint, raw_symbol), frame)
                _mark_done(manifest, symbol, endpoint, rows=int(len(frame)), status="done")
                _append_progress(
                    {
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                        "symbol": symbol,
                        "raw_symbol": raw_symbol,
                        "endpoint": endpoint,
                        "status": "done",
                        "rows": int(len(frame)),
                    }
                )
            except Exception as exc:  # pragma: no cover - network variability
                _mark_done(manifest, symbol, endpoint, rows=0, status="error", note=str(exc))
                _append_progress(
                    {
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                        "symbol": symbol,
                        "raw_symbol": raw_symbol,
                        "endpoint": endpoint,
                        "status": "error",
                        "rows": 0,
                        "note": str(exc),
                    }
                )
                logger.warning("recent-derivatives skip endpoint: symbol=%s endpoint=%s cause=%s", symbol, endpoint, exc)
                continue

    summary_rows = []
    for endpoint in endpoints:
        endpoint_dir = RAW_DIR / endpoint
        rows = 0
        files = 0
        if endpoint_dir.exists():
            for csv_path in endpoint_dir.glob("*.csv"):
                files += 1
                try:
                    rows += len(pd.read_csv(csv_path))
                except Exception:
                    continue
        summary_rows.append({"endpoint": endpoint, "files": files, "rows": rows})
    summary = pd.DataFrame(summary_rows)
    summary_path = OUTPUT_DIR / "summary.csv"
    summary.to_csv(summary_path, index=False)
    return {
        "manifest": MANIFEST_PATH,
        "progress": PROGRESS_PATH,
        "summary": summary_path,
        "raw_dir": RAW_DIR,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load recent derivatives metrics for pump regime research.")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--endpoints", type=str, default=",".join(ENDPOINTS))
    parser.add_argument("--symbol-limit", type=int, default=None)
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    selected = tuple(endpoint.strip() for endpoint in str(args.endpoints).split(",") if endpoint.strip())
    run(days=int(args.days), endpoints=selected, symbol_limit=args.symbol_limit)
