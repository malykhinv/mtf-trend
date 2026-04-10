from __future__ import annotations

import argparse
import json
import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from domain.enums.timeframe import Timeframe
from utils.symbols import normalize_symbol
from vectorbt_runner.data_preparer import DataPreparer

module_logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
INPUT_DB_PATHS = (
    REPO_ROOT / ".output" / "results_prev_year_5m" / "anomaly_category_lab" / "anomaly_feature_database_with_accumulation.csv",
    REPO_ROOT / ".output" / "results_prev_year_5m" / "anomaly_category_lab" / "anomaly_feature_database.csv",
)
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "market_regime_dataset"
RAW_CACHE_DIR = OUTPUT_DIR / "raw_cache"
FUNDING_CACHE_DIR = RAW_CACHE_DIR / "funding"
PREMIUM_CACHE_DIR = RAW_CACHE_DIR / "premium_4h"
MARKETS_CACHE_PATH = RAW_CACHE_DIR / "binance_symbol_map.json"

CURRENT_CACHE_DIR = REPO_ROOT / ".output" / "cache"
PREV_CACHE_DIR = REPO_ROOT / ".output" / "cache_prev_year_5m"

BINANCE_FAPI_BASE = "https://fapi.binance.com"
HTTP_TIMEOUT_SECONDS = 20
HTTP_RETRY_ATTEMPTS = 4
HTTP_SLEEP_SECONDS = 0.20
HTTP_BACKOFF_SECONDS = 1.25
FUNDING_LOOKBACK_MS = 30 * 24 * 60 * 60 * 1000
PREMIUM_LOOKBACK_MS = 30 * 24 * 60 * 60 * 1000
FOUR_HOURS_MS = 4 * 60 * 60 * 1000


@dataclass(frozen=True, slots=True)
class RegimeRuleThresholds:
    mild_funding_abs: float = 0.00008
    hot_funding_abs: float = 0.00025
    mild_premium_abs: float = 0.00030
    hot_premium_abs: float = 0.00100
    oi_up_pct_60m: float = 0.015
    oi_hot_pct_60m: float = 0.05
    oi_down_pct_60m: float = -0.01


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


def _resolve_input_db_path() -> Path:
    for path in INPUT_DB_PATHS:
        if path.exists():
            return path
    raise FileNotFoundError("Не найден anomaly feature database")


def _load_anomalies() -> pd.DataFrame:
    frame = pd.read_csv(_resolve_input_db_path(), low_memory=False)
    frame["timestamp_ms"] = pd.to_numeric(frame["timestamp_ms"], errors="coerce")
    frame = frame.dropna(subset=["symbol", "timestamp_ms", "session_id", "dataset"]).copy()
    frame["timestamp_ms"] = frame["timestamp_ms"].astype("int64")
    frame["dataset"] = frame["dataset"].astype(str)
    frame["session_id"] = frame["session_id"].astype(str)
    frame["symbol"] = frame["symbol"].astype(str)
    if "anomaly_key" not in frame.columns:
        frame["anomaly_key"] = (
            frame["dataset"].astype(str)
            + "|"
            + frame["session_id"].astype(str)
            + "|"
            + frame["symbol"].astype(str)
            + "|"
            + frame["timestamp_ms"].astype(str)
        )
    return frame.reset_index(drop=True)


def _load_symbol_map(logger: logging.Logger) -> dict[str, str]:
    if MARKETS_CACHE_PATH.exists():
        return json.loads(MARKETS_CACHE_PATH.read_text(encoding="utf-8"))

    try:
        import ccxt  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("ccxt is required for market regime dataset") from exc

    client = ccxt.binanceusdm(
        {
            "enableRateLimit": True,
            "options": {"defaultType": "swap", "fetchCurrencies": False},
            "timeout": HTTP_TIMEOUT_SECONDS * 1000,
        }
    )
    markets = client.load_markets()
    symbol_map: dict[str, str] = {}
    for market in markets.values():
        symbol = str(market.get("symbol") or "")
        raw_id = str(market.get("id") or "")
        if not symbol or not raw_id:
            continue
        symbol_map[symbol] = raw_id
        symbol_map[normalize_symbol(symbol)] = raw_id
    MARKETS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    MARKETS_CACHE_PATH.write_text(json.dumps(symbol_map, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("market-regime: cached symbol map size=%s", len(symbol_map))
    return symbol_map


def _resolve_raw_symbol(symbol: str, symbol_map: dict[str, str]) -> str | None:
    if symbol in symbol_map:
        return symbol_map[symbol]
    normalized = normalize_symbol(symbol)
    if normalized in symbol_map:
        return symbol_map[normalized]
    if normalized.endswith("/USDT"):
        return normalized.replace("/", "")
    return None


def _http_get_json(
    *,
    session: requests.Session,
    url: str,
    params: dict[str, object],
    logger: logging.Logger,
) -> Any:
    last_exc: Exception | None = None
    for attempt in range(1, HTTP_RETRY_ATTEMPTS + 1):
        try:
            response = session.get(url, params=params, timeout=HTTP_TIMEOUT_SECONDS)
            if response.status_code == 200:
                return response.json()
            if response.status_code in {418, 429}:
                sleep_s = HTTP_BACKOFF_SECONDS * attempt
                logger.warning("market-regime rate-limit: status=%s sleep=%.2fs url=%s", response.status_code, sleep_s, url)
                time.sleep(sleep_s)
                continue
            response.raise_for_status()
        except Exception as exc:  # pragma: no cover - network variability
            last_exc = exc
            sleep_s = HTTP_BACKOFF_SECONDS * attempt
            logger.warning("market-regime http retry: attempt=%s sleep=%.2fs url=%s cause=%s", attempt, sleep_s, url, exc)
            time.sleep(sleep_s)
            continue
        finally:
            time.sleep(HTTP_SLEEP_SECONDS)
    raise RuntimeError(f"HTTP fetch failed: url={url} params={params} cause={last_exc}")


def _fetch_funding_history(
    *,
    session: requests.Session,
    raw_symbol: str,
    start_ms: int,
    end_ms: int,
    logger: logging.Logger,
) -> pd.DataFrame:
    cache_path = FUNDING_CACHE_DIR / f"{raw_symbol}.csv"
    if cache_path.exists():
        frame = pd.read_csv(cache_path)
        if not frame.empty and "funding_time_ms" in frame.columns:
            return frame

    FUNDING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    cursor = int(start_ms)
    while cursor <= end_ms:
        payload = _http_get_json(
            session=session,
            url=f"{BINANCE_FAPI_BASE}/fapi/v1/fundingRate",
            params={"symbol": raw_symbol, "startTime": cursor, "endTime": int(end_ms), "limit": 1000},
            logger=logger,
        )
        if not isinstance(payload, list) or not payload:
            break
        for item in payload:
            if not isinstance(item, dict):
                continue
            funding_time = _safe_float(item.get("fundingTime"))
            funding_rate = _safe_float(item.get("fundingRate"))
            mark_price = _safe_float(item.get("markPrice"))
            if funding_time is None or funding_rate is None:
                continue
            rows.append(
                {
                    "funding_time_ms": int(funding_time),
                    "funding_rate": float(funding_rate),
                    "mark_price": mark_price,
                }
            )
        last_ts = max(int(row["funding_time_ms"]) for row in rows) if rows else cursor
        if last_ts >= end_ms:
            break
        cursor = last_ts + 1

    frame = pd.DataFrame(rows).drop_duplicates(subset=["funding_time_ms"]).sort_values("funding_time_ms").reset_index(drop=True)
    frame.to_csv(cache_path, index=False)
    return frame


def _fetch_premium_klines(
    *,
    session: requests.Session,
    raw_symbol: str,
    start_ms: int,
    end_ms: int,
    logger: logging.Logger,
) -> pd.DataFrame:
    cache_path = PREMIUM_CACHE_DIR / f"{raw_symbol}.csv"
    if cache_path.exists():
        frame = pd.read_csv(cache_path)
        if not frame.empty and "timestamp_ms" in frame.columns:
            return frame

    PREMIUM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    cursor = int(start_ms)
    while cursor <= end_ms:
        payload = _http_get_json(
            session=session,
            url=f"{BINANCE_FAPI_BASE}/fapi/v1/premiumIndexKlines",
            params={
                "symbol": raw_symbol,
                "interval": "4h",
                "startTime": cursor,
                "endTime": int(end_ms),
                "limit": 1000,
            },
            logger=logger,
        )
        if not isinstance(payload, list) or not payload:
            break
        for item in payload:
            if not isinstance(item, list) or len(item) < 5:
                continue
            open_time = _safe_float(item[0])
            premium_open = _safe_float(item[1])
            premium_high = _safe_float(item[2])
            premium_low = _safe_float(item[3])
            premium_close = _safe_float(item[4])
            if open_time is None or premium_close is None:
                continue
            rows.append(
                {
                    "timestamp_ms": int(open_time),
                    "premium_open": premium_open,
                    "premium_high": premium_high,
                    "premium_low": premium_low,
                    "premium_close": premium_close,
                }
            )
        last_ts = max(int(row["timestamp_ms"]) for row in rows) if rows else cursor
        if last_ts >= end_ms:
            break
        cursor = last_ts + FOUR_HOURS_MS

    frame = pd.DataFrame(rows).drop_duplicates(subset=["timestamp_ms"]).sort_values("timestamp_ms").reset_index(drop=True)
    frame.to_csv(cache_path, index=False)
    return frame


def _load_oi_series_by_symbol(cache_dir: Path, symbols: list[str]) -> dict[str, pd.DataFrame]:
    preparer = DataPreparer(cache_dir)
    result: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        frame = preparer.load_symbol_data(symbol, Timeframe.M5)
        if frame.empty or "open_interest" not in frame.columns:
            continue
        scoped = frame[["timestamp", "open_interest"]].copy()
        scoped["timestamp"] = pd.to_numeric(scoped["timestamp"], errors="coerce")
        scoped["open_interest"] = pd.to_numeric(scoped["open_interest"], errors="coerce")
        scoped = scoped.dropna(subset=["timestamp", "open_interest"])
        if scoped.empty:
            continue
        result[symbol] = scoped.sort_values("timestamp").reset_index(drop=True)
    return result


def _lookup_last(series: pd.DataFrame, timestamp_col: str, value_col: str, event_ts: int) -> tuple[float | None, int | None]:
    if series.empty:
        return None, None
    timestamps = pd.to_numeric(series[timestamp_col], errors="coerce").fillna(-1).astype("int64").to_numpy()
    values = pd.to_numeric(series[value_col], errors="coerce").to_numpy(dtype="float64")
    idx = timestamps.searchsorted(int(event_ts), side="right") - 1
    if idx < 0 or idx >= len(timestamps):
        return None, None
    value = values[idx]
    if math.isnan(value):
        return None, None
    return float(value), int(timestamps[idx])


def _lookup_window_stats(series: pd.DataFrame, timestamp_col: str, value_col: str, event_ts: int, lookback_ms: int) -> dict[str, float | None]:
    if series.empty:
        return {"last": None, "mean": None, "std": None, "z": None}
    scoped = series[(pd.to_numeric(series[timestamp_col], errors="coerce") <= event_ts) & (pd.to_numeric(series[timestamp_col], errors="coerce") >= event_ts - lookback_ms)].copy()
    if scoped.empty:
        return {"last": None, "mean": None, "std": None, "z": None}
    values = pd.to_numeric(scoped[value_col], errors="coerce").dropna()
    if values.empty:
        return {"last": None, "mean": None, "std": None, "z": None}
    last = float(values.iloc[-1])
    mean = float(values.mean())
    std = float(values.std(ddof=0))
    z = None if std <= 1e-12 else float((last - mean) / std)
    return {"last": last, "mean": mean, "std": std, "z": z}


def _compute_oi_features(oi_series: pd.DataFrame, event_ts: int) -> dict[str, object]:
    if oi_series.empty:
        return {
            "oi_available": False,
            "oi_at_event": None,
            "oi_delta_15m": None,
            "oi_delta_15m_pct": None,
            "oi_delta_60m": None,
            "oi_delta_60m_pct": None,
        }

    timestamps = pd.to_numeric(oi_series["timestamp"], errors="coerce").fillna(-1).astype("int64").to_numpy()
    values = pd.to_numeric(oi_series["open_interest"], errors="coerce").to_numpy(dtype="float64")
    idx = timestamps.searchsorted(int(event_ts), side="right") - 1
    if idx < 0 or idx >= len(values) or math.isnan(values[idx]):
        return {
            "oi_available": False,
            "oi_at_event": None,
            "oi_delta_15m": None,
            "oi_delta_15m_pct": None,
            "oi_delta_60m": None,
            "oi_delta_60m_pct": None,
        }

    def _delta(back_bars: int) -> tuple[float | None, float | None]:
        prev_idx = idx - back_bars
        if prev_idx < 0 or math.isnan(values[prev_idx]):
            return None, None
        delta = float(values[idx] - values[prev_idx])
        pct = None if abs(values[prev_idx]) <= 1e-12 else float(delta / values[prev_idx])
        return delta, pct

    delta_15m, delta_15m_pct = _delta(3)
    delta_60m, delta_60m_pct = _delta(12)
    return {
        "oi_available": True,
        "oi_at_event": float(values[idx]),
        "oi_delta_15m": delta_15m,
        "oi_delta_15m_pct": delta_15m_pct,
        "oi_delta_60m": delta_60m,
        "oi_delta_60m_pct": delta_60m_pct,
    }


def _classify_stage(row: pd.Series) -> str:
    pre_acc = str(row.get("pre_accumulation_type") or "")
    context = str(row.get("context_archetype") or "")
    impulse = str(row.get("impulse_archetype") or "")
    upper_wick = _safe_float(row.get("upper_wick_frac")) or 0.0
    close_to_high = _safe_float(row.get("close_to_high_frac")) or 1.0
    range_atr = _safe_float(row.get("range_atr")) or 0.0
    volume_mult = _safe_float(row.get("volume_mult")) or 0.0

    if pre_acc == "accumulating" and context in {"context_coiled", "context_warm"} and impulse == "impulse_body_drive":
        return "early_accumulation_to_expansion"
    if context in {"context_coiled", "context_warm"} and impulse == "impulse_body_drive":
        return "spot_like_expansion_shape"
    if context == "context_overheated" and impulse == "impulse_body_drive":
        return "late_expansion_or_repricing"
    if upper_wick >= 0.35 or close_to_high <= 0.45:
        return "distribution_or_false_break_shape"
    if range_atr >= 10.0 and volume_mult <= 4.0:
        return "thin_liquidity_like"
    return "mixed_shape"


def _classify_driver(row: pd.Series, thresholds: RegimeRuleThresholds) -> str:
    funding_last = abs(_safe_float(row.get("funding_rate_last")) or 0.0)
    funding_abs_mean = abs(_safe_float(row.get("funding_abs_mean_7d")) or 0.0)
    premium_abs = abs(_safe_float(row.get("premium_close_4h")) or 0.0)
    premium_abs_mean = abs(_safe_float(row.get("premium_abs_mean_24h")) or 0.0)
    oi_available = bool(row.get("oi_available", False))
    oi_delta_60m_pct = _safe_float(row.get("oi_delta_60m_pct"))
    upper_wick = _safe_float(row.get("upper_wick_frac")) or 0.0
    volume_mult = _safe_float(row.get("volume_mult")) or 0.0
    range_atr = _safe_float(row.get("range_atr")) or 0.0

    derivatives_pressure = max(funding_last, funding_abs_mean, premium_abs, premium_abs_mean)
    if oi_available and oi_delta_60m_pct is not None:
        if oi_delta_60m_pct >= thresholds.oi_hot_pct_60m and derivatives_pressure >= thresholds.hot_funding_abs:
            return "derivatives_overheating"
        if oi_delta_60m_pct >= thresholds.oi_up_pct_60m and derivatives_pressure >= thresholds.mild_funding_abs:
            return "futures_momentum_build"
        if oi_delta_60m_pct <= thresholds.oi_down_pct_60m and derivatives_pressure >= thresholds.hot_premium_abs:
            return "short_squeeze_like"
        if abs(oi_delta_60m_pct) < thresholds.oi_up_pct_60m and derivatives_pressure < thresholds.mild_premium_abs:
            return "spot_like_or_cash_led"
    if range_atr >= 10.0 and volume_mult <= 4.0 and upper_wick >= 0.25:
        return "thin_liquidity_like"
    if derivatives_pressure >= thresholds.hot_premium_abs:
        return "leverage_pressure_unknown_oi"
    if derivatives_pressure < thresholds.mild_premium_abs:
        return "spot_like_or_cash_led"
    return "mixed_or_unknown"


def _build_trade_summary(dataset: pd.DataFrame) -> pd.DataFrame:
    if dataset.empty or "trade_triggered" not in dataset.columns or "exit_return_pct" not in dataset.columns:
        return pd.DataFrame()
    prepared = dataset.copy()
    prepared["trade_triggered"] = prepared["trade_triggered"].astype(str).str.lower().isin(["true", "1"])
    prepared["exit_return_pct"] = pd.to_numeric(prepared["exit_return_pct"], errors="coerce")
    prepared = prepared[prepared["trade_triggered"]].copy()
    if prepared.empty:
        return pd.DataFrame()
    summary = (
        prepared.groupby(["session_id", "market_side_hint", "driver_regime", "stage_regime"], as_index=False)
        .agg(
            trades=("anomaly_key", "nunique"),
            symbols=("symbol", "nunique"),
            mean_return_pct=("exit_return_pct", "mean"),
            median_return_pct=("exit_return_pct", "median"),
            win_rate=("exit_return_pct", lambda values: float((pd.Series(values) > 0).mean())),
        )
        .sort_values(["trades", "mean_return_pct"], ascending=[False, False])
        .reset_index(drop=True)
    )
    return summary


def _build_report(
    *,
    dataset: pd.DataFrame,
    coverage: pd.DataFrame,
    summary: pd.DataFrame,
    trade_summary: pd.DataFrame,
    symbol_limit: int | None,
) -> str:
    def _markdown(frame: pd.DataFrame) -> str:
        if frame.empty:
            return "_empty_"
        scoped = frame.copy()
        for column in scoped.columns:
            if pd.api.types.is_float_dtype(scoped[column]):
                scoped[column] = scoped[column].map(lambda value: f"{value:.4f}" if pd.notna(value) else "")
        header = "| " + " | ".join(map(str, scoped.columns.tolist())) + " |"
        divider = "| " + " | ".join(["---"] * len(scoped.columns)) + " |"
        rows = [
            "| " + " | ".join("" if pd.isna(value) else str(value) for value in row) + " |"
            for row in scoped.itertuples(index=False, name=None)
        ]
        return "\n".join([header, divider, *rows])

    lines = [
        "# Market Regime Dataset",
        "",
        f"- anomalies: `{len(dataset)}`",
        f"- symbol_limit: `{symbol_limit if symbol_limit is not None else 'full'}`",
        "",
        "## Coverage",
        "",
        _markdown(coverage),
        "",
        "## Driver Classes",
        "",
        _markdown(summary),
        "",
        "## Trade Summary",
        "",
        _markdown(trade_summary.head(25)),
        "",
        "## Notes",
        "",
        "- `oi_*` is optional and only available where current 5m cache already contains `open_interest`.",
        "- old-period OI is intentionally not fetched from REST because Binance returns invalid-start errors on old dates.",
        "- `funding` is historical and fetched safely by symbol.",
        "- `premium` uses historical `premiumIndexKlines` on `4h`, so it is a regime layer, not an entry-timing layer.",
        "- `taker/depth` are not included yet; the dataset marks them as unavailable rather than fabricating proxies.",
    ]
    return "\n".join(lines)


def run(*, symbol_limit: int | None = None) -> dict[str, Path]:
    logger = module_logger
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    anomalies = _load_anomalies()

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

    if anomalies.empty:
        raise ValueError("anomalies dataset is empty")

    symbol_map = _load_symbol_map(logger)
    anomalies["raw_symbol"] = anomalies["symbol"].astype(str).map(lambda value: _resolve_raw_symbol(value, symbol_map))
    anomalies["funding_available"] = False
    anomalies["premium_available"] = False
    anomalies["taker_available"] = False
    anomalies["depth_available"] = False

    session = requests.Session()
    thresholds = RegimeRuleThresholds()
    current_symbols = anomalies.loc[anomalies["dataset"].astype(str) == "current", "symbol"].drop_duplicates().astype(str).tolist()
    current_oi = _load_oi_series_by_symbol(CURRENT_CACHE_DIR, current_symbols)

    min_ts = int(anomalies["timestamp_ms"].min()) - max(FUNDING_LOOKBACK_MS, PREMIUM_LOOKBACK_MS)
    max_ts = int(anomalies["timestamp_ms"].max())

    feature_rows: list[dict[str, object]] = []
    grouped = anomalies.groupby(["symbol", "raw_symbol"], sort=True)
    total_groups = grouped.ngroups
    for group_index, ((symbol, raw_symbol), scoped) in enumerate(grouped, start=1):
        funding_frame = pd.DataFrame()
        premium_frame = pd.DataFrame()
        if isinstance(raw_symbol, str) and raw_symbol:
            try:
                funding_frame = _fetch_funding_history(
                    session=session,
                    raw_symbol=raw_symbol,
                    start_ms=min_ts,
                    end_ms=max_ts,
                    logger=logger,
                )
            except Exception as exc:
                logger.warning("market-regime funding missing: symbol=%s raw=%s cause=%s", symbol, raw_symbol, exc)
            try:
                premium_frame = _fetch_premium_klines(
                    session=session,
                    raw_symbol=raw_symbol,
                    start_ms=min_ts,
                    end_ms=max_ts,
                    logger=logger,
                )
            except Exception as exc:
                logger.warning("market-regime premium missing: symbol=%s raw=%s cause=%s", symbol, raw_symbol, exc)
        if group_index % 25 == 0 or group_index == total_groups:
            logger.info("market-regime progress: groups=%s/%s", group_index, total_groups)

        oi_frame = current_oi.get(symbol, pd.DataFrame())
        for _, row in scoped.iterrows():
            event_ts = int(row["timestamp_ms"])
            funding_stats_7d = _lookup_window_stats(funding_frame, "funding_time_ms", "funding_rate", event_ts, 7 * 24 * 60 * 60 * 1000)
            funding_stats_30d = _lookup_window_stats(funding_frame, "funding_time_ms", "funding_rate", event_ts, 30 * 24 * 60 * 60 * 1000)
            funding_last, funding_time = _lookup_last(funding_frame, "funding_time_ms", "funding_rate", event_ts)
            premium_stats_24h = _lookup_window_stats(premium_frame, "timestamp_ms", "premium_close", event_ts, 24 * 60 * 60 * 1000)
            premium_stats_30d = _lookup_window_stats(premium_frame, "timestamp_ms", "premium_close", event_ts, 30 * 24 * 60 * 60 * 1000)
            premium_last, premium_time = _lookup_last(premium_frame, "timestamp_ms", "premium_close", event_ts)
            oi_features = _compute_oi_features(oi_frame if str(row["dataset"]) == "current" else pd.DataFrame(), event_ts)

            feature_row = row.to_dict()
            feature_row.update(
                {
                    "raw_symbol": raw_symbol,
                    "funding_available": funding_last is not None,
                    "funding_time_ms": funding_time,
                    "funding_rate_last": funding_last,
                    "funding_mean_7d": funding_stats_7d["mean"],
                    "funding_abs_mean_7d": abs(funding_stats_7d["mean"]) if funding_stats_7d["mean"] is not None else None,
                    "funding_z_30d": funding_stats_30d["z"],
                    "hours_since_funding": None if funding_time is None else float((event_ts - funding_time) / 3_600_000.0),
                    "premium_available": premium_last is not None,
                    "premium_time_ms": premium_time,
                    "premium_close_4h": premium_last,
                    "premium_mean_24h": premium_stats_24h["mean"],
                    "premium_abs_mean_24h": abs(premium_stats_24h["mean"]) if premium_stats_24h["mean"] is not None else None,
                    "premium_z_30d": premium_stats_30d["z"],
                    **oi_features,
                    "taker_available": False,
                    "depth_available": False,
                }
            )
            feature_row["stage_regime"] = _classify_stage(pd.Series(feature_row))
            feature_row["driver_regime"] = _classify_driver(pd.Series(feature_row), thresholds)
            feature_rows.append(feature_row)

    dataset = pd.DataFrame(feature_rows)
    coverage = (
        dataset.groupby(["dataset", "session_id"], as_index=False)
        .agg(
            anomalies=("anomaly_key", "nunique"),
            funding_coverage=("funding_available", "mean"),
            premium_coverage=("premium_available", "mean"),
            oi_coverage=("oi_available", "mean"),
        )
    )
    summary = (
        dataset.groupby(["driver_regime", "stage_regime"], as_index=False)
        .agg(
            anomalies=("anomaly_key", "nunique"),
            symbols=("symbol", "nunique"),
            current_share=("dataset", lambda values: float((pd.Series(values).astype(str) == "current").mean())),
        )
        .sort_values(["anomalies", "symbols"], ascending=[False, False])
        .reset_index(drop=True)
    )
    trade_summary = _build_trade_summary(dataset)

    suffix = f"_top{symbol_limit}" if symbol_limit is not None and symbol_limit > 0 else ""
    paths = {
        "dataset": OUTPUT_DIR / f"market_regime_dataset{suffix}.csv",
        "coverage": OUTPUT_DIR / f"coverage{suffix}.csv",
        "summary": OUTPUT_DIR / f"driver_stage_summary{suffix}.csv",
        "trade_summary": OUTPUT_DIR / f"trade_summary{suffix}.csv",
        "report": OUTPUT_DIR / f"report{suffix}.md",
    }
    dataset.to_csv(paths["dataset"], index=False)
    coverage.to_csv(paths["coverage"], index=False)
    summary.to_csv(paths["summary"], index=False)
    trade_summary.to_csv(paths["trade_summary"], index=False)
    paths["report"].write_text(
        _build_report(
            dataset=dataset,
            coverage=coverage,
            summary=summary,
            trade_summary=trade_summary,
            symbol_limit=symbol_limit,
        ),
        encoding="utf-8",
    )
    return paths


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build market-regime dataset for hourly pump anomalies.")
    parser.add_argument("--symbol-limit", type=int, default=None, help="Limit to top-N anomaly symbols for a lighter first run.")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(symbol_limit=args.symbol_limit)
