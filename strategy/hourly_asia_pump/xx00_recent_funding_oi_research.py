from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from strategy.hourly_asia_pump.market_regime_dataset import _load_symbol_map, _resolve_raw_symbol
from strategy.hourly_asia_pump.static_combo import _frame_to_markdown

REPO_ROOT = Path(__file__).resolve().parents[2]
TRADES_PATH = REPO_ROOT / ".output" / "results_prev_year_5m" / "xx00_online_watchlist_backtest" / "entry_rule_trades.csv"
OI_ROOT = REPO_ROOT / ".output" / "results_prev_year_5m" / "oi_5m_backfill" / "raw"
EXISTING_FUNDING_ROOT = REPO_ROOT / ".output" / "results_prev_year_5m" / "recent_derivatives_loader" / "raw" / "funding"
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "xx00_recent_funding_oi_research"
FUNDING_CACHE_DIR = OUTPUT_DIR / "funding_cache"

EVENTS_PATH = OUTPUT_DIR / "events_enriched.csv"
SUMMARY_PATH = OUTPUT_DIR / "summary_by_group.csv"
FEATURE_COMPARE_PATH = OUTPUT_DIR / "feature_compare.csv"
COVERAGE_PATH = OUTPUT_DIR / "coverage_summary.csv"
SESSION_SUMMARY_PATH = OUTPUT_DIR / "session_summary.csv"
FUNDING_QUARTILE_PATH = OUTPUT_DIR / "funding_quartile_summary.csv"
OI_QUARTILE_PATH = OUTPUT_DIR / "oi_quartile_summary.csv"
REPORT_PATH = OUTPUT_DIR / "report.md"

RULE_ID = "launch_r010_c65_v04_p0_rr30"
RECENT_DAYS = 31
FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
HTTP_TIMEOUT_SECONDS = 20
REQUEST_SLEEP_SECONDS = 0.12


@dataclass(frozen=True, slots=True)
class EventLabelSpec:
    label_id: str
    label: str
    mask_column: str


def _write_csv_atomic(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(tmp, index=False)
    tmp.replace(path)


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


def _http_get_json(session: requests.Session, *, url: str, params: dict[str, object]) -> Any:
    response = session.get(url, params=params, timeout=HTTP_TIMEOUT_SECONDS)
    if response.status_code != 200:
        raise RuntimeError(f"status={response.status_code} body={response.text[:300]}")
    time.sleep(REQUEST_SLEEP_SECONDS)
    return response.json()


def _load_base_events() -> pd.DataFrame:
    frame = pd.read_csv(TRADES_PATH, low_memory=False)
    frame = frame[
        (frame["dataset"].astype(str) == "current")
        & (frame["rule_id"].astype(str) == RULE_ID)
    ].copy()
    numeric_columns = [
        "timestamp_ms",
        "signal_bar_timestamp_ms",
        "entry_timestamp_ms",
        "exit_return_pct",
        "m0_return_pct",
        "m0_volume_ratio",
        "m0_close_pos",
    ]
    for column in numeric_columns:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["timestamp_ms", "signal_bar_timestamp_ms", "entry_timestamp_ms"]).copy()
    frame["timestamp_ms"] = frame["timestamp_ms"].astype("int64")
    frame["signal_bar_timestamp_ms"] = frame["signal_bar_timestamp_ms"].astype("int64")
    frame["entry_timestamp_ms"] = frame["entry_timestamp_ms"].astype("int64")
    frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_ms"], unit="ms", utc=True)
    cutoff = frame["timestamp_utc"].max() - pd.Timedelta(days=RECENT_DAYS)
    frame = frame[frame["timestamp_utc"] >= cutoff].copy()

    class _Logger:
        def info(self, *args: object, **kwargs: object) -> None:
            return None

        def warning(self, *args: object, **kwargs: object) -> None:
            return None

    symbol_map = _load_symbol_map(_Logger())
    frame["raw_symbol"] = frame["symbol"].astype(str).map(lambda value: _resolve_raw_symbol(value, symbol_map) or "")
    return frame.reset_index(drop=True)


def _load_oi_cache(raw_symbol: str) -> pd.DataFrame:
    path = OI_ROOT / f"{raw_symbol}.csv"
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path, low_memory=False)
    if frame.empty:
        return frame
    frame["timestamp_ms"] = pd.to_numeric(frame["timestamp_ms"], errors="coerce")
    frame["open_interest_value"] = pd.to_numeric(frame.get("open_interest_value"), errors="coerce")
    frame["open_interest_contracts"] = pd.to_numeric(frame.get("open_interest_contracts"), errors="coerce")
    frame = frame.dropna(subset=["timestamp_ms"]).copy()
    frame["timestamp_ms"] = frame["timestamp_ms"].astype("int64")
    return frame.drop_duplicates(subset=["timestamp_ms"]).sort_values("timestamp_ms").reset_index(drop=True)


def _load_existing_funding(raw_symbol: str) -> pd.DataFrame:
    candidates = [
        FUNDING_CACHE_DIR / f"{raw_symbol}.csv",
        EXISTING_FUNDING_ROOT / f"{raw_symbol}.csv",
    ]
    for path in candidates:
        if path.exists():
            frame = pd.read_csv(path, low_memory=False)
            if frame.empty:
                continue
            frame["timestamp_ms"] = pd.to_numeric(frame["timestamp_ms"], errors="coerce")
            frame["funding_rate"] = pd.to_numeric(frame.get("funding_rate"), errors="coerce")
            frame["mark_price"] = pd.to_numeric(frame.get("mark_price"), errors="coerce")
            frame = frame.dropna(subset=["timestamp_ms"]).copy()
            frame["timestamp_ms"] = frame["timestamp_ms"].astype("int64")
            return frame.drop_duplicates(subset=["timestamp_ms"]).sort_values("timestamp_ms").reset_index(drop=True)
    return pd.DataFrame()


def _fetch_full_funding_range(
    *,
    session: requests.Session,
    raw_symbol: str,
    start_ms: int,
    end_ms: int,
) -> pd.DataFrame:
    payload = _http_get_json(
        session,
        url=FUNDING_URL,
        params={
            "symbol": raw_symbol,
            "startTime": int(start_ms),
            "endTime": int(end_ms),
            "limit": 1000,
        },
    )
    frame = pd.DataFrame(payload if isinstance(payload, list) else [])
    if frame.empty:
        return frame
    frame = frame.rename(columns={"fundingTime": "timestamp_ms", "fundingRate": "funding_rate", "markPrice": "mark_price"})
    frame["timestamp_ms"] = pd.to_numeric(frame["timestamp_ms"], errors="coerce")
    frame["funding_rate"] = pd.to_numeric(frame.get("funding_rate"), errors="coerce")
    frame["mark_price"] = pd.to_numeric(frame.get("mark_price"), errors="coerce")
    frame = frame.dropna(subset=["timestamp_ms"]).copy()
    frame["timestamp_ms"] = frame["timestamp_ms"].astype("int64")
    if "symbol" not in frame.columns:
        frame["symbol"] = raw_symbol
    return frame.drop_duplicates(subset=["timestamp_ms"]).sort_values("timestamp_ms").reset_index(drop=True)


def _ensure_funding_cache(
    *,
    session: requests.Session,
    raw_symbol: str,
    start_ms: int,
    end_ms: int,
) -> pd.DataFrame:
    existing = _load_existing_funding(raw_symbol)
    if not existing.empty:
        oldest = int(existing["timestamp_ms"].min())
        newest = int(existing["timestamp_ms"].max())
        if oldest <= start_ms and newest >= end_ms:
            return existing
    fetched = _fetch_full_funding_range(session=session, raw_symbol=raw_symbol, start_ms=start_ms, end_ms=end_ms)
    if existing.empty:
        merged = fetched.copy()
    elif fetched.empty:
        merged = existing.copy()
    else:
        merged = pd.concat([existing, fetched], ignore_index=True)
    if merged.empty:
        return merged
    merged = merged.drop_duplicates(subset=["timestamp_ms"]).sort_values("timestamp_ms").reset_index(drop=True)
    _write_csv_atomic(FUNDING_CACHE_DIR / f"{raw_symbol}.csv", merged)
    return merged


def _pct_change(current: object, previous: object) -> float | None:
    left = _safe_float(current)
    right = _safe_float(previous)
    if left is None or right is None or right == 0.0:
        return None
    return left / right - 1.0


def _label_specs() -> tuple[EventLabelSpec, ...]:
    return (
        EventLabelSpec("all", "All events", "mask_all"),
        EventLabelSpec("stop", "False launch / stop", "is_stop"),
        EventLabelSpec("tp", "Continuation / tp", "is_tp"),
        EventLabelSpec("positive", "Positive trade", "is_positive"),
    )


def _enrich_events() -> pd.DataFrame:
    if EVENTS_PATH.exists():
        return pd.read_csv(EVENTS_PATH, low_memory=False)

    events = _load_base_events()
    events = events[events["raw_symbol"].astype(str) != ""].copy()
    if events.empty:
        enriched = pd.DataFrame()
        _write_csv_atomic(EVENTS_PATH, enriched)
        return enriched

    start_ms = int(events["timestamp_ms"].min()) - (8 * 60 * 60 * 1000)
    end_ms = int(events["timestamp_ms"].max()) + (1 * 60 * 60 * 1000)
    funding_session = requests.Session()
    funding_cache: dict[str, pd.DataFrame] = {}
    oi_cache: dict[str, pd.DataFrame] = {}
    rows: list[dict[str, object]] = []

    for row in events.itertuples(index=False):
        raw_symbol = str(row.raw_symbol)
        if raw_symbol not in funding_cache:
            funding_cache[raw_symbol] = _ensure_funding_cache(
                session=funding_session,
                raw_symbol=raw_symbol,
                start_ms=start_ms,
                end_ms=end_ms,
            )
        if raw_symbol not in oi_cache:
            oi_cache[raw_symbol] = _load_oi_cache(raw_symbol)

        funding = funding_cache[raw_symbol]
        oi = oi_cache[raw_symbol]
        signal_ts = int(row.signal_bar_timestamp_ms)

        if funding.empty or oi.empty:
            continue

        funding_past = funding[funding["timestamp_ms"] <= signal_ts].copy()
        oi_past = oi[oi["timestamp_ms"] <= signal_ts].copy()
        if funding_past.empty or oi_past.empty:
            continue

        last_funding = funding_past.iloc[-1]
        funding_interval_ms = pd.to_numeric(funding_past["timestamp_ms"], errors="coerce").diff().dropna().median()
        funding_interval_hours = float(funding_interval_ms / 3_600_000.0) if pd.notna(funding_interval_ms) else None
        since_last_funding_min = (signal_ts - int(last_funding["timestamp_ms"])) / 60_000.0
        is_exact_funding_window = since_last_funding_min >= 0.0 and since_last_funding_min <= 5.0

        oi_now = oi_past.iloc[-1]
        oi_minus_5m = oi_past.iloc[-2] if len(oi_past) >= 2 else None
        oi_minus_15m = oi_past.iloc[-4] if len(oi_past) >= 4 else None
        oi_minus_60m = oi_past.iloc[-13] if len(oi_past) >= 13 else None

        rows.append(
            {
                "symbol": row.symbol,
                "raw_symbol": raw_symbol,
                "session_id": row.session_id,
                "timestamp_ms": int(row.timestamp_ms),
                "timestamp_utc": row.timestamp_utc,
                "signal_bar_timestamp_ms": signal_ts,
                "hour_utc": int(pd.Timestamp(row.timestamp_utc).hour),
                "m0_return_pct": _safe_float(row.m0_return_pct),
                "m0_close_pos": _safe_float(row.m0_close_pos),
                "m0_volume_ratio": _safe_float(row.m0_volume_ratio),
                "exit_reason": str(row.exit_reason),
                "exit_return_pct": _safe_float(row.exit_return_pct),
                "is_stop": str(row.exit_reason) == "stop",
                "is_tp": str(row.exit_reason) == "tp",
                "is_positive": _safe_float(row.exit_return_pct) is not None and float(row.exit_return_pct) > 0.0,
                "mask_all": True,
                "last_funding_timestamp_ms": int(last_funding["timestamp_ms"]),
                "last_funding_rate": _safe_float(last_funding.get("funding_rate")),
                "abs_last_funding_rate": abs(float(last_funding["funding_rate"])) if pd.notna(last_funding.get("funding_rate")) else None,
                "since_last_funding_min": since_last_funding_min,
                "funding_interval_hours": funding_interval_hours,
                "is_exact_funding_window": is_exact_funding_window,
                "oi_timestamp_ms": int(oi_now["timestamp_ms"]),
                "oi_age_min": (signal_ts - int(oi_now["timestamp_ms"])) / 60_000.0,
                "oi_value_now": _safe_float(oi_now.get("open_interest_value")),
                "oi_chg_5m_pct": _pct_change(oi_now.get("open_interest_value"), oi_minus_5m.get("open_interest_value") if oi_minus_5m is not None else None),
                "oi_chg_15m_pct": _pct_change(oi_now.get("open_interest_value"), oi_minus_15m.get("open_interest_value") if oi_minus_15m is not None else None),
                "oi_chg_60m_pct": _pct_change(oi_now.get("open_interest_value"), oi_minus_60m.get("open_interest_value") if oi_minus_60m is not None else None),
            }
        )

    enriched = pd.DataFrame(rows)
    _write_csv_atomic(EVENTS_PATH, enriched)
    return enriched


def _coverage_summary(base_events: pd.DataFrame, enriched: pd.DataFrame) -> pd.DataFrame:
    rows = [
        {
            "metric": "base_recent_events",
            "value": int(len(base_events)),
        },
        {
            "metric": "base_recent_symbols",
            "value": int(base_events["symbol"].nunique()),
        },
        {
            "metric": "enriched_events",
            "value": int(len(enriched)),
        },
        {
            "metric": "enriched_symbols",
            "value": int(enriched["symbol"].nunique()) if not enriched.empty else 0,
        },
        {
            "metric": "coverage_share",
            "value": float(len(enriched) / len(base_events)) if len(base_events) > 0 else 0.0,
        },
    ]
    frame = pd.DataFrame(rows)
    _write_csv_atomic(COVERAGE_PATH, frame)
    return frame


def _summary_by_group(enriched: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if enriched.empty:
        frame = pd.DataFrame(rows)
        _write_csv_atomic(SUMMARY_PATH, frame)
        return frame

    abs_rate_q75 = pd.to_numeric(enriched["abs_last_funding_rate"], errors="coerce").quantile(0.75)
    group_masks = {
        "all": pd.Series(True, index=enriched.index),
        "exact_funding_window": enriched["is_exact_funding_window"].astype(bool),
        "non_funding_window": ~enriched["is_exact_funding_window"].astype(bool),
        "high_abs_funding_q75": pd.to_numeric(enriched["abs_last_funding_rate"], errors="coerce") >= abs_rate_q75,
        "exact_and_high_abs_funding": enriched["is_exact_funding_window"].astype(bool)
        & (pd.to_numeric(enriched["abs_last_funding_rate"], errors="coerce") >= abs_rate_q75),
    }
    for group_id, mask in group_masks.items():
        scoped = enriched[mask].copy()
        if scoped.empty:
            continue
        rows.append(
            {
                "group_id": group_id,
                "events": int(len(scoped)),
                "stop_rate": float(scoped["is_stop"].mean()),
                "tp_rate": float(scoped["is_tp"].mean()),
                "positive_rate": float(scoped["is_positive"].mean()),
                "mean_exit_return_pct": float(pd.to_numeric(scoped["exit_return_pct"], errors="coerce").mean()),
                "median_abs_last_funding_rate": float(pd.to_numeric(scoped["abs_last_funding_rate"], errors="coerce").median()),
                "median_since_last_funding_min": float(pd.to_numeric(scoped["since_last_funding_min"], errors="coerce").median()),
                "median_oi_chg_5m_pct": float(pd.to_numeric(scoped["oi_chg_5m_pct"], errors="coerce").median()),
                "median_oi_chg_15m_pct": float(pd.to_numeric(scoped["oi_chg_15m_pct"], errors="coerce").median()),
                "median_oi_chg_60m_pct": float(pd.to_numeric(scoped["oi_chg_60m_pct"], errors="coerce").median()),
            }
        )
    frame = pd.DataFrame(rows)
    _write_csv_atomic(SUMMARY_PATH, frame)
    return frame


def _feature_compare(enriched: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if enriched.empty:
        frame = pd.DataFrame(rows)
        _write_csv_atomic(FEATURE_COMPARE_PATH, frame)
        return frame

    features = [
        ("abs_last_funding_rate", "Absolute last funding rate"),
        ("since_last_funding_min", "Minutes since last funding"),
        ("oi_chg_5m_pct", "OI change 5m"),
        ("oi_chg_15m_pct", "OI change 15m"),
        ("oi_chg_60m_pct", "OI change 60m"),
    ]
    for spec in _label_specs():
        scoped = enriched[enriched[spec.mask_column].astype(bool)].copy()
        if scoped.empty:
            continue
        for column, label in features:
            values = pd.to_numeric(scoped[column], errors="coerce").dropna()
            if values.empty:
                continue
            rows.append(
                {
                    "label_id": spec.label_id,
                    "label": spec.label,
                    "feature_id": column,
                    "feature_label": label,
                    "count": int(len(values)),
                    "mean_value": float(values.mean()),
                    "median_value": float(values.median()),
                }
            )
    frame = pd.DataFrame(rows)
    _write_csv_atomic(FEATURE_COMPARE_PATH, frame)
    return frame


def _session_summary(enriched: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if enriched.empty:
        frame = pd.DataFrame(rows)
        _write_csv_atomic(SESSION_SUMMARY_PATH, frame)
        return frame
    for session_id, scoped in enriched.groupby("session_id", sort=True):
        masks = {
            "all": pd.Series(True, index=scoped.index),
            "exact_funding_window": scoped["is_exact_funding_window"].astype(bool),
            "non_funding_window": ~scoped["is_exact_funding_window"].astype(bool),
        }
        for group_id, mask in masks.items():
            subset = scoped[mask].copy()
            if subset.empty:
                continue
            rows.append(
                {
                    "session_id": str(session_id),
                    "group_id": group_id,
                    "events": int(len(subset)),
                    "stop_rate": float(subset["is_stop"].mean()),
                    "tp_rate": float(subset["is_tp"].mean()),
                    "positive_rate": float(subset["is_positive"].mean()),
                    "mean_exit_return_pct": float(pd.to_numeric(subset["exit_return_pct"], errors="coerce").mean()),
                }
            )
    frame = pd.DataFrame(rows)
    _write_csv_atomic(SESSION_SUMMARY_PATH, frame)
    return frame


def _funding_quartile_summary(enriched: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if enriched.empty:
        frame = pd.DataFrame(rows)
        _write_csv_atomic(FUNDING_QUARTILE_PATH, frame)
        return frame
    scoped = enriched.copy()
    ranks = pd.to_numeric(scoped["abs_last_funding_rate"], errors="coerce").rank(method="first")
    scoped["funding_bucket"] = pd.qcut(ranks, 4, labels=["q1", "q2", "q3", "q4"])
    for funding_bucket, bucket_frame in scoped.groupby("funding_bucket", observed=False, sort=True):
        if bucket_frame.empty:
            continue
        rows.append(
            {
                "funding_bucket": str(funding_bucket),
                "events": int(len(bucket_frame)),
                "stop_rate": float(bucket_frame["is_stop"].mean()),
                "tp_rate": float(bucket_frame["is_tp"].mean()),
                "positive_rate": float(bucket_frame["is_positive"].mean()),
                "mean_exit_return_pct": float(pd.to_numeric(bucket_frame["exit_return_pct"], errors="coerce").mean()),
                "exact_funding_window_rate": float(bucket_frame["is_exact_funding_window"].mean()),
            }
        )
    frame = pd.DataFrame(rows)
    _write_csv_atomic(FUNDING_QUARTILE_PATH, frame)
    return frame


def _oi_quartile_summary(enriched: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if enriched.empty:
        frame = pd.DataFrame(rows)
        _write_csv_atomic(OI_QUARTILE_PATH, frame)
        return frame
    values = pd.to_numeric(enriched["oi_chg_60m_pct"], errors="coerce")
    top_cut = values.quantile(0.75)
    bottom_cut = values.quantile(0.25)
    groups = {
        "oi60_top_quartile": values >= top_cut,
        "oi60_bottom_quartile": values <= bottom_cut,
    }
    for group_id, mask in groups.items():
        scoped = enriched[mask].copy()
        if scoped.empty:
            continue
        rows.append(
            {
                "group_id": group_id,
                "events": int(len(scoped)),
                "stop_rate": float(scoped["is_stop"].mean()),
                "tp_rate": float(scoped["is_tp"].mean()),
                "positive_rate": float(scoped["is_positive"].mean()),
                "mean_exit_return_pct": float(pd.to_numeric(scoped["exit_return_pct"], errors="coerce").mean()),
            }
        )
    frame = pd.DataFrame(rows)
    _write_csv_atomic(OI_QUARTILE_PATH, frame)
    return frame


def _build_report(
    *,
    coverage: pd.DataFrame,
    summary: pd.DataFrame,
    feature_compare: pd.DataFrame,
    session_summary: pd.DataFrame,
    funding_quartile_summary: pd.DataFrame,
    oi_quartile_summary: pd.DataFrame,
) -> str:
    lines = [
        "# XX:00 Recent Funding/OI Research",
        "",
        "Goal:",
        "- test whether recent honest XX:00 false launches look like funding-driven spikes;",
        "- use only information available by HH:01;",
        "- work on a recent period where derivatives data exists.",
        "",
        "Method:",
        "- base universe: current recent 31d, honest XX:00 launch entries from the broad base rule;",
        "- label false launches as `exit_reason == stop` and continuations as `exit_reason == tp`;",
        "- funding features come from the last known funding record at or before the signal;",
        "- OI features use only the latest 5m OI sample at or before the signal;",
        "- no post-event derivatives data is used for the feature side.",
        "",
        "Coverage:",
        _frame_to_markdown(coverage, columns=["metric", "value"]),
        "",
        "Group summary:",
        _frame_to_markdown(
            summary,
            columns=[
                "group_id",
                "events",
                "stop_rate",
                "tp_rate",
                "positive_rate",
                "mean_exit_return_pct",
                "median_abs_last_funding_rate",
                "median_since_last_funding_min",
                "median_oi_chg_60m_pct",
            ],
        ),
        "",
        "Session summary:",
        _frame_to_markdown(
            session_summary,
            columns=[
                "session_id",
                "group_id",
                "events",
                "stop_rate",
                "tp_rate",
                "positive_rate",
                "mean_exit_return_pct",
            ],
        ),
        "",
        "Funding-rate quartiles:",
        _frame_to_markdown(
            funding_quartile_summary,
            columns=[
                "funding_bucket",
                "events",
                "stop_rate",
                "tp_rate",
                "positive_rate",
                "mean_exit_return_pct",
                "exact_funding_window_rate",
            ],
        ),
        "",
        "OI 60m quartile contrast:",
        _frame_to_markdown(
            oi_quartile_summary,
            columns=[
                "group_id",
                "events",
                "stop_rate",
                "tp_rate",
                "positive_rate",
                "mean_exit_return_pct",
            ],
        ),
        "",
        "Feature comparison by outcome:",
        _frame_to_markdown(
            feature_compare,
            columns=[
                "label_id",
                "feature_id",
                "count",
                "mean_value",
                "median_value",
            ],
        ),
        "",
        "Limits:",
        "- this is recent-only because derivatives coverage is recent-only;",
        "- funding cadence is inferred from observed history per symbol, not from future records;",
        "- labels use future trade outcomes only for research grouping, not for feature construction;",
        "- some symbols still remain uncovered if they have no derivatives cache or no past funding record in range.",
        "",
    ]
    return "\n".join(lines)


def run() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    base_events = _load_base_events()
    enriched = _enrich_events()
    coverage = _coverage_summary(base_events, enriched)
    summary = _summary_by_group(enriched)
    feature_compare = _feature_compare(enriched)
    session_summary = _session_summary(enriched)
    funding_quartile_summary = _funding_quartile_summary(enriched)
    oi_quartile_summary = _oi_quartile_summary(enriched)
    report = _build_report(
        coverage=coverage,
        summary=summary,
        feature_compare=feature_compare,
        session_summary=session_summary,
        funding_quartile_summary=funding_quartile_summary,
        oi_quartile_summary=oi_quartile_summary,
    )
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(report.encode("ascii", errors="ignore").decode("ascii"))


if __name__ == "__main__":
    run()
