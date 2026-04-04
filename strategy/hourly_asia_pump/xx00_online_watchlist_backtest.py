from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import pandas as pd

from domain.enums.timeframe import Timeframe
from strategy.hourly_asia_pump.static_combo import _frame_to_markdown, _summarize_events
from strategy.hourly_asia_pump.unified_edge import _simulate_equity_risk_metrics
from vectorbt_runner.data_preparer import DataPreparer

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / ".output" / "results_prev_year_5m" / "xx00_online_watchlist_backtest"
CURRENT_CACHE_DIR = REPO_ROOT / ".output" / "cache"
PREV_CACHE_DIR = REPO_ROOT / ".output" / "cache_prev_year_5m"

OLD_OPPORTUNITIES_PATH = OUTPUT_DIR / "old_hourly_opportunities.parquet"
THRESHOLDS_PATH = OUTPUT_DIR / "session_thresholds.csv"
TRADES_PATH = OUTPUT_DIR / "entry_rule_trades.csv"
COVERAGE_PATH = OUTPUT_DIR / "coverage_summary.csv"
TRADES_OLD_PATH = OUTPUT_DIR / "entry_rule_trades_old.csv"
TRADES_CURRENT_PATH = OUTPUT_DIR / "entry_rule_trades_current.csv"
COVERAGE_OLD_PATH = OUTPUT_DIR / "coverage_summary_old.csv"
COVERAGE_CURRENT_PATH = OUTPUT_DIR / "coverage_summary_current.csv"
COMBO_SUMMARY_PATH = OUTPUT_DIR / "combo_summary.csv"
SELECTED_PATH = OUTPUT_DIR / "selected_combos.csv"
SELECTED_TRADES_PATH = OUTPUT_DIR / "selected_trades.csv"
AUDIT_PATH = OUTPUT_DIR / "lookahead_audit.csv"
REPORT_PATH = OUTPUT_DIR / "report.md"

ONE_MINUTE_MS = 60_000
ONE_HOUR_MS = 60 * ONE_MINUTE_MS
FEE_RATE = 0.0004
CURRENT_LOOKBACK_DAYS = 365
MAX_HOLD_MINUTES = 120


@dataclass(frozen=True, slots=True)
class SessionSpec:
    session_id: str
    start_hour_utc: int
    end_hour_utc: int


@dataclass(frozen=True, slots=True)
class EntryRule:
    rule_id: str
    min_m0_return_pct: float
    min_m0_close_pos: float
    min_m0_volume_ratio: float
    require_break_prev240: bool
    rr_target: float = 3.0


@dataclass(frozen=True, slots=True)
class WatchlistRule:
    rule_id: str
    label: str


def _session_specs() -> tuple[SessionSpec, ...]:
    return (
        SessionSpec("asia", 0, 8),
        SessionSpec("europe", 8, 16),
        SessionSpec("america", 16, 24),
    )


def _entry_rules() -> tuple[EntryRule, ...]:
    rules: list[EntryRule] = []
    for min_m0_return_pct in (0.01, 0.015):
        for min_m0_close_pos in (0.65, 0.80):
            for min_m0_volume_ratio in (4.0, 8.0):
                for require_break_prev240 in (False, True):
                    rule_id = (
                        f"launch_r{int(min_m0_return_pct * 1000):03d}"
                        f"_c{int(min_m0_close_pos * 100):02d}"
                        f"_v{int(min_m0_volume_ratio):02d}"
                        f"_p{int(require_break_prev240)}"
                        "_rr30"
                    )
                    rules.append(
                        EntryRule(
                            rule_id=rule_id,
                            min_m0_return_pct=min_m0_return_pct,
                            min_m0_close_pos=min_m0_close_pos,
                            min_m0_volume_ratio=min_m0_volume_ratio,
                            require_break_prev240=require_break_prev240,
                        )
                    )
    return tuple(rules)


def _watchlist_rules() -> tuple[WatchlistRule, ...]:
    return (
        WatchlistRule("wl_all", "All XX:00 hours"),
        WatchlistRule("wl_above_ema200", "Above EMA200"),
        WatchlistRule("wl_drift_hot", "Above EMA200 + drift hot"),
        WatchlistRule("wl_hot_range", "Above EMA200 + wide range + strong 60m return"),
        WatchlistRule("wl_union_hot", "Above EMA200 + (hot range or drift hot)"),
        WatchlistRule("wl_pressure_hot", "Above EMA200 + 60m return hot + volume pressure hot"),
        WatchlistRule("wl_hot_range_ema_stack", "Above EMA200 + EMA stack + hot range"),
        WatchlistRule("wl_strict_union", "Above EMA200 + EMA stack + slope up + strict union"),
        WatchlistRule("wl_near_high_union", "Above EMA200 + near 60m highs + union"),
    )


def _safe_pct(current: float | None, reference: float | None) -> float | None:
    if current is None or reference is None:
        return None
    current_float = float(current)
    reference_float = float(reference)
    if reference_float == 0.0:
        return None
    return (current_float / reference_float) - 1.0


def _close_pos(high_price: float, low_price: float, close_price: float) -> float:
    bar_range = high_price - low_price
    if bar_range <= 0.0:
        return 0.5
    return float((close_price - low_price) / bar_range)


def _session_from_hour(hour_utc: int) -> str:
    if 0 <= hour_utc < 8:
        return "asia"
    if 8 <= hour_utc < 16:
        return "europe"
    return "america"


def _safe_label(value: object) -> str:
    text = str(value)
    return text.encode("ascii", errors="ignore").decode("ascii") or "<non-ascii>"


def _calendar_months_from_timestamps(values: pd.Series) -> list[str]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return []
    start = pd.to_datetime(int(clean.min()), unit="ms", utc=True).tz_localize(None).to_period("M")
    end = pd.to_datetime(int(clean.max()), unit="ms", utc=True).tz_localize(None).to_period("M")
    return [str(period) for period in pd.period_range(start=start, end=end, freq="M")]


def _positive_trade_share(frame: pd.DataFrame, top_n: int) -> float:
    if frame.empty:
        return 0.0
    positive = pd.to_numeric(frame["exit_return_pct"], errors="coerce").clip(lower=0.0).sort_values(ascending=False)
    total = float(positive.sum())
    if total <= 0.0:
        return 0.0
    return float(positive.head(top_n).sum() / total)


def _positive_symbol_share(frame: pd.DataFrame, top_n: int) -> float:
    if frame.empty:
        return 0.0
    grouped = (
        frame.assign(pos_return=pd.to_numeric(frame["exit_return_pct"], errors="coerce").clip(lower=0.0))
        .groupby("symbol", as_index=False)["pos_return"]
        .sum()
        .sort_values("pos_return", ascending=False)
    )
    total = float(grouped["pos_return"].sum())
    if total <= 0.0:
        return 0.0
    return float(grouped["pos_return"].head(top_n).sum() / total)


def _bucket_value(value: object, q1: float, q2: float, *, inverse: bool = False) -> str:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return "mid"
    value_float = float(numeric)
    if inverse:
        if value_float <= q1:
            return "hot"
        if value_float <= q2:
            return "warm"
        return "cold"
    if value_float <= q1:
        return "cold"
    if value_float <= q2:
        return "warm"
    return "hot"


def _coerce_bool_series(series: pd.Series) -> pd.Series:
    return (
        series.astype(str)
        .str.strip()
        .str.lower()
        .map({"true": True, "false": False, "1": True, "0": False, "yes": True, "no": False})
        .fillna(False)
        .astype(bool)
    )


def _assign_watchlist_flags(row: dict[str, object]) -> dict[str, bool]:
    above_ema200 = bool(row.get("above_ema200", False))
    ema_stack = bool(row.get("ema_stack_bullish", False))
    ema200_slope_up = bool(row.get("ema200_slope_up", False))
    range_bucket = str(row.get("range_bucket", "mid"))
    drift_bucket = str(row.get("drift_bucket", "mid"))
    ret_bucket = str(row.get("return60_bucket", "mid"))
    volume_bucket = str(row.get("volume_pressure_bucket", "mid"))
    dist_bucket = str(row.get("dist_prev60_bucket", "mid"))

    hot_range = range_bucket == "hot" and ret_bucket == "hot"
    drift_hot = drift_bucket == "hot"
    volume_hot = volume_bucket == "hot"
    volume_at_least_warm = volume_bucket in {"warm", "hot"}
    return_at_least_warm = ret_bucket in {"warm", "hot"}
    union_hot = hot_range or drift_hot

    return {
        "wl_all": True,
        "wl_above_ema200": above_ema200,
        "wl_drift_hot": above_ema200 and drift_hot,
        "wl_hot_range": above_ema200 and hot_range,
        "wl_union_hot": above_ema200 and union_hot,
        "wl_pressure_hot": above_ema200 and ret_bucket == "hot" and volume_hot,
        "wl_hot_range_ema_stack": above_ema200 and ema_stack and hot_range,
        "wl_strict_union": above_ema200 and ema_stack and ema200_slope_up and (hot_range or (drift_hot and volume_at_least_warm)),
        "wl_near_high_union": above_ema200 and dist_bucket == "hot" and (hot_range or (drift_hot and return_at_least_warm)),
    }


def _net_long_return(entry_price: float, exit_price: float) -> float:
    if entry_price <= 0.0 or exit_price <= 0.0:
        return 0.0
    return float((exit_price * (1.0 - FEE_RATE) / (entry_price * (1.0 + FEE_RATE))) - 1.0)


def _simulate_fixed_rr_exit(
    *,
    timestamps: list[int],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    signal_idx: int,
    entry_price: float,
    stop_price: float,
    rr_target: float,
) -> tuple[int, float, str]:
    risk = entry_price - stop_price
    if risk <= 0.0:
        return signal_idx, entry_price, "invalid"
    target_price = entry_price + (risk * rr_target)
    last_idx = min(len(closes) - 1, signal_idx + MAX_HOLD_MINUTES)
    for idx in range(signal_idx + 1, last_idx + 1):
        low_price = float(lows[idx])
        high_price = float(highs[idx])
        if low_price <= stop_price:
            return idx, stop_price, "stop"
        if high_price >= target_price:
            return idx, target_price, "tp"
    return last_idx, float(closes[last_idx]), "time_exit"


def _current_cutoff_ms() -> int | None:
    preparer = DataPreparer(CURRENT_CACHE_DIR)
    max_timestamp_ms: int | None = None
    for symbol in preparer.list_symbols(Timeframe.M1):
        frame = preparer.load_symbol_data(symbol, Timeframe.M1)
        if frame.empty:
            continue
        timestamp_value = pd.to_numeric(frame["timestamp"], errors="coerce").dropna()
        if timestamp_value.empty:
            continue
        candidate = int(timestamp_value.max())
        if max_timestamp_ms is None or candidate > max_timestamp_ms:
            max_timestamp_ms = candidate
    if max_timestamp_ms is None:
        return None
    return int(max_timestamp_ms - (CURRENT_LOOKBACK_DAYS * 24 * ONE_HOUR_MS))


def _hour_rows_for_symbol(
    *,
    dataset: str,
    symbol: str,
    frame: pd.DataFrame,
    current_cutoff_ms: int | None,
) -> list[dict[str, object]]:
    if frame.empty:
        return []

    timestamps = pd.to_numeric(frame["timestamp"], errors="coerce").astype("int64").tolist()
    opens = pd.to_numeric(frame["open"], errors="coerce").astype(float).tolist()
    highs = pd.to_numeric(frame["high"], errors="coerce").astype(float).tolist()
    lows = pd.to_numeric(frame["low"], errors="coerce").astype(float).tolist()
    closes = pd.to_numeric(frame["close"], errors="coerce").astype(float).tolist()
    volumes = pd.to_numeric(frame["volume"], errors="coerce").astype(float).tolist()

    close_series = pd.Series(closes, dtype="float64")
    ema50 = close_series.ewm(span=50, adjust=False).mean().tolist()
    ema200 = close_series.ewm(span=200, adjust=False).mean().tolist()
    prev60_high_series = pd.Series(highs, dtype="float64").shift(1).rolling(60).max()
    prev60_low_series = pd.Series(lows, dtype="float64").shift(1).rolling(60).min()
    prev240_high_series = pd.Series(highs, dtype="float64").shift(1).rolling(240).max()

    rows: list[dict[str, object]] = []
    for idx, timestamp_ms in enumerate(timestamps):
        timestamp_ms_int = int(timestamp_ms)
        total_minutes = timestamp_ms_int // ONE_MINUTE_MS
        if (total_minutes % 60) != 0:
            continue
        if idx < 240 or (idx + MAX_HOLD_MINUTES) >= len(closes):
            continue
        if dataset == "current" and current_cutoff_ms is not None and timestamp_ms_int < current_cutoff_ms:
            continue

        hour_utc = int((total_minutes // 60) % 24)
        session_id = _session_from_hour(hour_utc)
        prev60_high = float(prev60_high_series.iloc[idx])
        prev60_low = float(prev60_low_series.iloc[idx])
        prev240_high = float(prev240_high_series.iloc[idx])
        if pd.isna(prev60_high) or pd.isna(prev60_low) or pd.isna(prev240_high):
            continue

        pre_idx = idx - 1
        pre_close = float(closes[pre_idx])
        m0_open = float(opens[idx])
        if m0_open <= 0.0:
            continue
        pre_range_pct_60m = float((prev60_high - prev60_low) / m0_open)
        pre_drift_pct_60m = abs(float((pre_close / float(opens[idx - 60])) - 1.0))
        pre_return_60m_pct = float((pre_close / float(closes[idx - 60])) - 1.0)
        pre_return_30m_pct = float((pre_close / float(closes[idx - 30])) - 1.0)

        last30_volumes = [float(value) for value in volumes[idx - 30 : idx] if float(value) > 0.0]
        last5_volumes = [float(value) for value in volumes[idx - 5 : idx] if float(value) > 0.0]
        prev25_volumes = [float(value) for value in volumes[idx - 30 : idx - 5] if float(value) > 0.0]
        if not last30_volumes or not last5_volumes or not prev25_volumes:
            continue
        pre_volume_median_30 = float(pd.Series(last30_volumes, dtype="float64").median())
        pre_volume_last5_vs_prev25 = float(
            pd.Series(last5_volumes, dtype="float64").median() / pd.Series(prev25_volumes, dtype="float64").median()
        )
        if pre_volume_median_30 <= 0.0:
            continue

        last30_up_volume = float(
            sum(volumes[pos] for pos in range(idx - 30, idx) if float(closes[pos]) > float(opens[pos]))
        )
        last30_total_volume = float(sum(volumes[idx - 30 : idx]))
        pre_up_volume_share_30m = (last30_up_volume / last30_total_volume) if last30_total_volume > 0.0 else None

        m0_high = float(highs[idx])
        m0_low = float(lows[idx])
        m0_close = float(closes[idx])
        m0_range = max(1e-12, m0_high - m0_low)
        m0_return_pct = float((m0_close / m0_open) - 1.0)
        m0_close_pos = _close_pos(m0_high, m0_low, m0_close)
        m0_body_frac_range = float(max(0.0, m0_close - m0_open) / m0_range)
        m0_volume_ratio = float(volumes[idx] / pre_volume_median_30)

        timestamp_utc = pd.to_datetime(timestamp_ms_int, unit="ms", utc=True, errors="coerce")
        row = {
            "dataset": dataset,
            "symbol": symbol,
            "session_id": session_id,
            "timestamp_ms": timestamp_ms_int,
            "timestamp_utc": timestamp_utc,
            "month_utc": timestamp_utc.strftime("%Y-%m") if pd.notna(timestamp_utc) else None,
            "date_utc": timestamp_utc.strftime("%Y-%m-%d") if pd.notna(timestamp_utc) else None,
            "hour_utc": hour_utc,
            "opportunity_id": f"{dataset}|{symbol}|{timestamp_ms_int}",
            "pre_close_vs_ema200_pct": _safe_pct(pre_close, float(ema200[pre_idx])),
            "pre_ema50_vs_ema200_pct": _safe_pct(float(ema50[pre_idx]), float(ema200[pre_idx])),
            "pre_ema200_slope_60m_pct": _safe_pct(float(ema200[pre_idx]), float(ema200[idx - 60])),
            "pre_base_range_pct_60m": pre_range_pct_60m,
            "pre_base_drift_pct_60m": pre_drift_pct_60m,
            "pre_return_30m_pct": pre_return_30m_pct,
            "pre_return_60m_pct": pre_return_60m_pct,
            "pre_volume_last5_vs_prev25": pre_volume_last5_vs_prev25,
            "pre_up_volume_share_30m": pre_up_volume_share_30m,
            "pre_dist_to_prev60_high_pct": _safe_pct(prev60_high, pre_close),
            "prev60_high": prev60_high,
            "prev240_high": prev240_high,
            "m0_open": m0_open,
            "m0_high": m0_high,
            "m0_low": m0_low,
            "m0_close": m0_close,
            "m0_return_pct": m0_return_pct,
            "m0_close_pos": m0_close_pos,
            "m0_body_frac_range": m0_body_frac_range,
            "m0_volume_ratio": m0_volume_ratio,
            "m0_close_above_prev60": bool(m0_close > prev60_high),
            "m0_close_above_prev240": bool(m0_close > prev240_high),
            "above_ema200": bool((_safe_pct(pre_close, float(ema200[pre_idx])) or 0.0) > 0.0),
            "ema_stack_bullish": bool((_safe_pct(float(ema50[pre_idx]), float(ema200[pre_idx])) or 0.0) > 0.0),
            "ema200_slope_up": bool((_safe_pct(float(ema200[pre_idx]), float(ema200[idx - 60])) or 0.0) > 0.0),
        }
        rows.append(row)
    return rows


def _build_old_opportunities(*, current_cutoff_ms: int | None) -> pd.DataFrame:
    if OLD_OPPORTUNITIES_PATH.exists():
        return pd.read_parquet(OLD_OPPORTUNITIES_PATH)

    preparer = DataPreparer(PREV_CACHE_DIR)
    symbols = preparer.list_symbols(Timeframe.M1)
    rows: list[dict[str, object]] = []
    total_symbols = len(symbols)
    for index, symbol in enumerate(symbols, start=1):
        frame = preparer.load_symbol_data(symbol, Timeframe.M1)
        rows.extend(_hour_rows_for_symbol(dataset="old", symbol=symbol, frame=frame, current_cutoff_ms=current_cutoff_ms))
        if index == 1 or index % 10 == 0 or index == total_symbols:
            print(f"xx00-online-watchlist: old-opportunities {index}/{total_symbols} symbol={symbol} rows={len(rows)}")
    opportunities = pd.DataFrame(rows)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    opportunities.to_parquet(OLD_OPPORTUNITIES_PATH, index=False)
    return opportunities


def _compute_thresholds(old_opportunities: pd.DataFrame) -> pd.DataFrame:
    if THRESHOLDS_PATH.exists():
        return pd.read_csv(THRESHOLDS_PATH)

    rows: list[dict[str, object]] = []
    for session_id, scoped in old_opportunities.groupby("session_id", sort=True):
        rows.append(
            {
                "session_id": str(session_id),
                "range_q1": float(pd.to_numeric(scoped["pre_base_range_pct_60m"], errors="coerce").quantile(1.0 / 3.0)),
                "range_q2": float(pd.to_numeric(scoped["pre_base_range_pct_60m"], errors="coerce").quantile(2.0 / 3.0)),
                "drift_q1": float(pd.to_numeric(scoped["pre_base_drift_pct_60m"], errors="coerce").quantile(1.0 / 3.0)),
                "drift_q2": float(pd.to_numeric(scoped["pre_base_drift_pct_60m"], errors="coerce").quantile(2.0 / 3.0)),
                "ret60_q1": float(pd.to_numeric(scoped["pre_return_60m_pct"], errors="coerce").quantile(1.0 / 3.0)),
                "ret60_q2": float(pd.to_numeric(scoped["pre_return_60m_pct"], errors="coerce").quantile(2.0 / 3.0)),
                "volume_q1": float(pd.to_numeric(scoped["pre_volume_last5_vs_prev25"], errors="coerce").quantile(1.0 / 3.0)),
                "volume_q2": float(pd.to_numeric(scoped["pre_volume_last5_vs_prev25"], errors="coerce").quantile(2.0 / 3.0)),
                "dist_q1": float(pd.to_numeric(scoped["pre_dist_to_prev60_high_pct"], errors="coerce").quantile(1.0 / 3.0)),
                "dist_q2": float(pd.to_numeric(scoped["pre_dist_to_prev60_high_pct"], errors="coerce").quantile(2.0 / 3.0)),
                "old_hourly_rows": int(len(scoped)),
                "old_symbols": int(scoped["symbol"].astype(str).nunique()),
            }
        )
    thresholds = pd.DataFrame(rows).sort_values("session_id").reset_index(drop=True)
    thresholds.to_csv(THRESHOLDS_PATH, index=False)
    return thresholds


def _threshold_map(thresholds: pd.DataFrame) -> dict[str, dict[str, float]]:
    return {
        str(row["session_id"]): {
            "range_q1": float(row["range_q1"]),
            "range_q2": float(row["range_q2"]),
            "drift_q1": float(row["drift_q1"]),
            "drift_q2": float(row["drift_q2"]),
            "ret60_q1": float(row["ret60_q1"]),
            "ret60_q2": float(row["ret60_q2"]),
            "volume_q1": float(row["volume_q1"]),
            "volume_q2": float(row["volume_q2"]),
            "dist_q1": float(row["dist_q1"]),
            "dist_q2": float(row["dist_q2"]),
        }
        for _, row in thresholds.iterrows()
    }


def _annotate_row_with_buckets(row: dict[str, object], session_thresholds: dict[str, float]) -> dict[str, object]:
    row["range_bucket"] = _bucket_value(row.get("pre_base_range_pct_60m"), session_thresholds["range_q1"], session_thresholds["range_q2"])
    row["drift_bucket"] = _bucket_value(row.get("pre_base_drift_pct_60m"), session_thresholds["drift_q1"], session_thresholds["drift_q2"])
    row["return60_bucket"] = _bucket_value(row.get("pre_return_60m_pct"), session_thresholds["ret60_q1"], session_thresholds["ret60_q2"])
    row["volume_pressure_bucket"] = _bucket_value(row.get("pre_volume_last5_vs_prev25"), session_thresholds["volume_q1"], session_thresholds["volume_q2"])
    row["dist_prev60_bucket"] = _bucket_value(
        row.get("pre_dist_to_prev60_high_pct"),
        session_thresholds["dist_q1"],
        session_thresholds["dist_q2"],
        inverse=True,
    )
    return row


def _scan_single_dataset(
    *,
    dataset: str,
    cache_dir: Path,
    threshold_by_session: dict[str, dict[str, float]],
    current_cutoff_ms: int | None,
    symbols_subset: list[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    entry_rules = _entry_rules()
    coverage_rows: list[dict[str, object]] = []
    trade_rows: list[dict[str, object]] = []
    coverage_accumulator: dict[tuple[str, str], dict[str, float]] = {}

    preparer = DataPreparer(cache_dir)
    symbols = symbols_subset if symbols_subset is not None else preparer.list_symbols(Timeframe.M1)
    total_symbols = len(symbols)
    for index, symbol in enumerate(symbols, start=1):
        frame = preparer.load_symbol_data(symbol, Timeframe.M1)
        if frame.empty:
            continue
        base_rows = _hour_rows_for_symbol(dataset=dataset, symbol=symbol, frame=frame, current_cutoff_ms=current_cutoff_ms)
        if not base_rows:
            continue

        timestamps = pd.to_numeric(frame["timestamp"], errors="coerce").astype("int64").tolist()
        opens = pd.to_numeric(frame["open"], errors="coerce").astype(float).tolist()
        highs = pd.to_numeric(frame["high"], errors="coerce").astype(float).tolist()
        lows = pd.to_numeric(frame["low"], errors="coerce").astype(float).tolist()
        closes = pd.to_numeric(frame["close"], errors="coerce").astype(float).tolist()
        timestamp_to_idx = {int(timestamp): idx for idx, timestamp in enumerate(timestamps)}

        for row in base_rows:
            session_id = str(row["session_id"])
            session_thresholds = threshold_by_session[session_id]
            annotated = _annotate_row_with_buckets(row.copy(), session_thresholds)
            watchlist_flags = _assign_watchlist_flags(annotated)
            for watchlist_id, passed in watchlist_flags.items():
                coverage_key = (session_id, watchlist_id)
                if coverage_key not in coverage_accumulator:
                    coverage_accumulator[coverage_key] = {
                        "hourly_opportunities": 0.0,
                        "watchlist_passes": 0.0,
                    }
                coverage_accumulator[coverage_key]["hourly_opportunities"] += 1.0
                if passed:
                    coverage_accumulator[coverage_key]["watchlist_passes"] += 1.0

            timestamp_ms = int(annotated["timestamp_ms"])
            signal_idx = timestamp_to_idx.get(timestamp_ms)
            if signal_idx is None:
                continue

            for rule in entry_rules:
                if not bool(annotated["m0_close_above_prev60"]):
                    continue
                if rule.require_break_prev240 and not bool(annotated["m0_close_above_prev240"]):
                    continue
                if float(annotated["m0_return_pct"]) < rule.min_m0_return_pct:
                    continue
                if float(annotated["m0_close_pos"]) < rule.min_m0_close_pos:
                    continue
                if float(annotated["m0_volume_ratio"]) < rule.min_m0_volume_ratio:
                    continue

                fill_idx = signal_idx + 1
                if fill_idx >= len(opens):
                    continue
                entry_price = float(opens[fill_idx])
                stop_price = float(annotated["m0_low"])
                if stop_price >= entry_price:
                    continue

                exit_idx, exit_price, exit_reason = _simulate_fixed_rr_exit(
                    timestamps=timestamps,
                    highs=highs,
                    lows=lows,
                    closes=closes,
                    signal_idx=signal_idx,
                    entry_price=entry_price,
                    stop_price=stop_price,
                    rr_target=rule.rr_target,
                )
                trade_rows.append(
                    {
                        **annotated,
                        **watchlist_flags,
                        "rule_id": rule.rule_id,
                        "min_m0_return_pct": rule.min_m0_return_pct,
                        "min_m0_close_pos": rule.min_m0_close_pos,
                        "min_m0_volume_ratio": rule.min_m0_volume_ratio,
                        "require_break_prev240": rule.require_break_prev240,
                        "rr_target": rule.rr_target,
                        "signal_bar_timestamp_ms": timestamp_ms,
                        "entry_timestamp_ms": int(timestamp_ms + ONE_MINUTE_MS),
                        "exit_timestamp_ms": int(timestamps[exit_idx] + ONE_MINUTE_MS),
                        "entry_price": entry_price,
                        "stop_price": stop_price,
                        "initial_risk_pct": float((entry_price - stop_price) / entry_price),
                        "exit_price": float(exit_price),
                        "exit_reason": exit_reason,
                        "exit_return_pct": _net_long_return(entry_price, float(exit_price)),
                        "entry_delay_minutes": 1,
                        "signal_close_price": float(annotated["m0_close"]),
                        "entry_gap_vs_signal_close_pct": float((entry_price / float(annotated["m0_close"])) - 1.0),
                    }
                )
        if index == 1 or index % 10 == 0 or index == total_symbols:
            print(
                f"xx00-online-watchlist: scan {dataset} {index}/{total_symbols} "
                f"symbol={_safe_label(symbol)} trades={len(trade_rows)}"
            )

    for (session_id, watchlist_id), stats in sorted(coverage_accumulator.items()):
        hourly_opportunities = int(stats["hourly_opportunities"])
        watchlist_passes = int(stats["watchlist_passes"])
        coverage_rows.append(
            {
                "session_id": session_id,
                "dataset": dataset,
                "watchlist_rule_id": watchlist_id,
                "hourly_opportunities": hourly_opportunities,
                "watchlist_passes": watchlist_passes,
                "watchlist_pass_rate": (watchlist_passes / hourly_opportunities) if hourly_opportunities > 0 else 0.0,
            }
        )
    return pd.DataFrame(trade_rows), pd.DataFrame(coverage_rows)


def _scan_all_datasets(
    *,
    thresholds: pd.DataFrame,
    current_cutoff_ms: int | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if TRADES_PATH.exists() and COVERAGE_PATH.exists():
        trades = pd.read_csv(TRADES_PATH, low_memory=False)
        for watchlist in _watchlist_rules():
            if watchlist.rule_id in trades.columns:
                trades[watchlist.rule_id] = _coerce_bool_series(trades[watchlist.rule_id])
        return trades, pd.read_csv(COVERAGE_PATH, low_memory=False)

    threshold_by_session = _threshold_map(thresholds)
    partial_specs = (
        ("old", PREV_CACHE_DIR, TRADES_OLD_PATH, COVERAGE_OLD_PATH),
        ("current", CURRENT_CACHE_DIR, TRADES_CURRENT_PATH, COVERAGE_CURRENT_PATH),
    )
    partial_trades: list[pd.DataFrame] = []
    partial_coverage: list[pd.DataFrame] = []
    for dataset, cache_dir, trades_path, coverage_path in partial_specs:
        if trades_path.exists() and coverage_path.exists():
            trades_part = pd.read_csv(trades_path, low_memory=False)
            for watchlist in _watchlist_rules():
                if watchlist.rule_id in trades_part.columns:
                    trades_part[watchlist.rule_id] = _coerce_bool_series(trades_part[watchlist.rule_id])
            coverage_part = pd.read_csv(coverage_path, low_memory=False)
        else:
            trades_part, coverage_part = _scan_single_dataset(
                dataset=dataset,
                cache_dir=cache_dir,
                threshold_by_session=threshold_by_session,
                current_cutoff_ms=current_cutoff_ms,
            )
            trades_part.to_csv(trades_path, index=False)
            coverage_part.to_csv(coverage_path, index=False)
        partial_trades.append(trades_part)
        partial_coverage.append(coverage_part)

    trades = pd.concat(partial_trades, ignore_index=True) if partial_trades else pd.DataFrame()
    coverage = pd.concat(partial_coverage, ignore_index=True) if partial_coverage else pd.DataFrame()
    trades.to_csv(TRADES_PATH, index=False)
    coverage.to_csv(COVERAGE_PATH, index=False)
    return trades, coverage


def _calendar_months_for_dataset(frame: pd.DataFrame, dataset: str) -> list[str]:
    scoped = frame[frame["dataset"].astype(str) == dataset].copy()
    return _calendar_months_from_timestamps(pd.to_numeric(scoped["entry_timestamp_ms"], errors="coerce"))


def _summarize_trade_slice(frame: pd.DataFrame, *, calendar_months: Sequence[str]) -> dict[str, object]:
    summary = _summarize_events(frame, calendar_months=calendar_months) or {}
    equity_3, _ = _simulate_equity_risk_metrics(frame, calendar_months=calendar_months, risk_fraction=0.03)
    equity_5, _ = _simulate_equity_risk_metrics(frame, calendar_months=calendar_months, risk_fraction=0.05)
    summary.update(
        {
            "equity_annualized_return_pct_3": float(equity_3.get("equity_annualized_return_pct", 0.0)),
            "equity_total_return_pct_3": float(equity_3.get("equity_total_return_pct", 0.0)),
            "equity_max_drawdown_pct_3": float(equity_3.get("equity_max_drawdown_pct", 0.0)),
            "equity_annualized_return_pct_5": float(equity_5.get("equity_annualized_return_pct", 0.0)),
            "equity_total_return_pct_5": float(equity_5.get("equity_total_return_pct", 0.0)),
            "equity_max_drawdown_pct_5": float(equity_5.get("equity_max_drawdown_pct", 0.0)),
        }
    )
    for top_n in (1, 3, 5, 10):
        summary[f"top{top_n}_trade_share"] = _positive_trade_share(frame, top_n)
        summary[f"top{top_n}_symbol_share"] = _positive_symbol_share(frame, top_n)
    return summary


def _combo_summary(trades: pd.DataFrame, coverage: pd.DataFrame) -> pd.DataFrame:
    if COMBO_SUMMARY_PATH.exists():
        return pd.read_csv(COMBO_SUMMARY_PATH, low_memory=False)

    rows: list[dict[str, object]] = []
    watchlists = _watchlist_rules()
    for session_id in [spec.session_id for spec in _session_specs()]:
        session_trades = trades[trades["session_id"].astype(str) == session_id].copy()
        if session_trades.empty:
            continue
        old_months = _calendar_months_for_dataset(session_trades, "old")
        current_months = _calendar_months_for_dataset(session_trades, "current")
        for watchlist in watchlists:
            old_coverage = coverage[
                (coverage["session_id"].astype(str) == session_id)
                & (coverage["dataset"].astype(str) == "old")
                & (coverage["watchlist_rule_id"].astype(str) == watchlist.rule_id)
            ].copy()
            current_coverage = coverage[
                (coverage["session_id"].astype(str) == session_id)
                & (coverage["dataset"].astype(str) == "current")
                & (coverage["watchlist_rule_id"].astype(str) == watchlist.rule_id)
            ].copy()
            for rule_id, rule_slice in session_trades.groupby("rule_id", sort=True):
                scoped = rule_slice[_coerce_bool_series(rule_slice[watchlist.rule_id])].copy()
                old = scoped[scoped["dataset"].astype(str) == "old"].copy()
                current = scoped[scoped["dataset"].astype(str) == "current"].copy()
                old_summary = _summarize_trade_slice(old, calendar_months=old_months)
                current_summary = _summarize_trade_slice(current, calendar_months=current_months)
                old_coverage_row = old_coverage.iloc[0].to_dict() if not old_coverage.empty else {}
                current_coverage_row = current_coverage.iloc[0].to_dict() if not current_coverage.empty else {}
                rows.append(
                    {
                        "session_id": session_id,
                        "watchlist_rule_id": watchlist.rule_id,
                        "watchlist_label": watchlist.label,
                        "rule_id": str(rule_id),
                        "old_hourly_opportunities": int(old_coverage_row.get("hourly_opportunities", 0)),
                        "old_watchlist_passes": int(old_coverage_row.get("watchlist_passes", 0)),
                        "old_watchlist_pass_rate": float(old_coverage_row.get("watchlist_pass_rate", 0.0)),
                        "current_hourly_opportunities": int(current_coverage_row.get("hourly_opportunities", 0)),
                        "current_watchlist_passes": int(current_coverage_row.get("watchlist_passes", 0)),
                        "current_watchlist_pass_rate": float(current_coverage_row.get("watchlist_pass_rate", 0.0)),
                        "old_trades": int(old_summary.get("trades_count", 0)),
                        "old_trades_per_year": float(old_summary.get("trades_per_year", 0.0)),
                        "old_win_rate": float(old_summary.get("win_rate", 0.0)),
                        "old_mean_return_pct": float(old_summary.get("mean_return_pct", 0.0)),
                        "old_equity_annualized_return_pct_3": float(old_summary.get("equity_annualized_return_pct_3", 0.0)),
                        "old_equity_annualized_return_pct_5": float(old_summary.get("equity_annualized_return_pct_5", 0.0)),
                        "old_equity_max_drawdown_pct_3": float(old_summary.get("equity_max_drawdown_pct_3", 0.0)),
                        "old_equity_max_drawdown_pct_5": float(old_summary.get("equity_max_drawdown_pct_5", 0.0)),
                        "current_trades": int(current_summary.get("trades_count", 0)),
                        "current_trades_per_year": float(current_summary.get("trades_per_year", 0.0)),
                        "current_win_rate": float(current_summary.get("win_rate", 0.0)),
                        "current_mean_return_pct": float(current_summary.get("mean_return_pct", 0.0)),
                        "current_equity_annualized_return_pct_3": float(current_summary.get("equity_annualized_return_pct_3", 0.0)),
                        "current_equity_annualized_return_pct_5": float(current_summary.get("equity_annualized_return_pct_5", 0.0)),
                        "current_equity_max_drawdown_pct_3": float(current_summary.get("equity_max_drawdown_pct_3", 0.0)),
                        "current_equity_max_drawdown_pct_5": float(current_summary.get("equity_max_drawdown_pct_5", 0.0)),
                        "current_positive_months_count": int(current_summary.get("positive_months_count", 0)),
                        "current_non_positive_months_count": int(current_summary.get("non_positive_months_count", 0)),
                        "current_top1_trade_share": float(current_summary.get("top1_trade_share", 0.0)),
                        "current_top3_trade_share": float(current_summary.get("top3_trade_share", 0.0)),
                        "current_top5_trade_share": float(current_summary.get("top5_trade_share", 0.0)),
                        "current_top10_trade_share": float(current_summary.get("top10_trade_share", 0.0)),
                        "current_top1_symbol_share": float(current_summary.get("top1_symbol_share", 0.0)),
                        "current_top3_symbol_share": float(current_summary.get("top3_symbol_share", 0.0)),
                        "current_top5_symbol_share": float(current_summary.get("top5_symbol_share", 0.0)),
                        "current_top10_symbol_share": float(current_summary.get("top10_symbol_share", 0.0)),
                    }
                )
    summary = pd.DataFrame(rows)
    summary.to_csv(COMBO_SUMMARY_PATH, index=False)
    return summary


def _select_best_old(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return summary
    enriched = summary.copy()
    enriched["old_min_trade_gate"] = pd.to_numeric(enriched["old_trades"], errors="coerce") >= 8
    enriched["old_positive_gate"] = pd.to_numeric(enriched["old_equity_annualized_return_pct_3"], errors="coerce") > 0.0
    enriched["old_dd_gate"] = pd.to_numeric(enriched["old_equity_max_drawdown_pct_5"], errors="coerce") <= 0.25
    enriched["old_gate_pass_count"] = enriched[["old_min_trade_gate", "old_positive_gate", "old_dd_gate"]].astype(int).sum(axis=1)
    selected_rows: list[pd.Series] = []
    for _, scoped in enriched.groupby("session_id", sort=True):
        passed = scoped[
            scoped["old_min_trade_gate"].astype(bool)
            & scoped["old_positive_gate"].astype(bool)
            & scoped["old_dd_gate"].astype(bool)
        ].copy()
        pool = passed if not passed.empty else scoped.copy()
        selected_rows.append(
            pool.sort_values(
                [
                    "old_gate_pass_count",
                    "old_equity_annualized_return_pct_3",
                    "old_trades",
                    "old_win_rate",
                ],
                ascending=[False, False, False, False],
            ).iloc[0]
        )
    selected = pd.DataFrame(selected_rows).reset_index(drop=True)
    selected.to_csv(SELECTED_PATH, index=False)
    return selected


def _selected_trades(trades: pd.DataFrame, selected: pd.DataFrame) -> pd.DataFrame:
    if selected.empty or trades.empty:
        return pd.DataFrame()
    rows: list[pd.DataFrame] = []
    for _, row in selected.iterrows():
        scoped = trades[
            (trades["session_id"].astype(str) == str(row["session_id"]))
            & (trades["rule_id"].astype(str) == str(row["rule_id"]))
            & (_coerce_bool_series(trades[str(row["watchlist_rule_id"])]))
        ].copy()
        scoped["selected_watchlist_rule_id"] = str(row["watchlist_rule_id"])
        scoped["selected_watchlist_label"] = str(row["watchlist_label"])
        rows.append(scoped)
    selected_trades = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    selected_trades.to_csv(SELECTED_TRADES_PATH, index=False)
    return selected_trades


def _lookahead_audit(selected_trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if selected_trades.empty:
        rows.append(
            {
                "no_posthoc_m5_universe": True,
                "watchlist_source": "raw_m1_hourly_scan",
                "thresholds_frozen_on_old_only": True,
                "watchlist_uses_only_pre_hour_data": True,
                "entry_uses_only_m0_close": True,
                "entry_fill_is_next_open": True,
                "same_bar_resolution": "stop_first",
            }
        )
    else:
        for (session_id, watchlist_rule_id, rule_id), scoped in selected_trades.groupby(
            ["session_id", "selected_watchlist_rule_id", "rule_id"],
            sort=True,
        ):
            entry_ts = pd.to_numeric(scoped["entry_timestamp_ms"], errors="coerce")
            signal_ts = pd.to_numeric(scoped["signal_bar_timestamp_ms"], errors="coerce")
            gap = pd.to_numeric(scoped["entry_gap_vs_signal_close_pct"], errors="coerce").dropna()
            rows.append(
                {
                    "session_id": str(session_id),
                    "watchlist_rule_id": str(watchlist_rule_id),
                    "rule_id": str(rule_id),
                    "trades": int(len(scoped)),
                    "all_entries_after_signal": bool((entry_ts > signal_ts).all()),
                    "min_signal_to_entry_minutes": float(((entry_ts - signal_ts) / ONE_MINUTE_MS).min()) if not scoped.empty else None,
                    "mean_entry_gap_vs_signal_close_pct": float(gap.mean()) if not gap.empty else 0.0,
                    "no_posthoc_m5_universe": True,
                    "watchlist_source": "raw_m1_hourly_scan",
                    "thresholds_frozen_on_old_only": True,
                    "watchlist_uses_only_pre_hour_data": True,
                    "entry_uses_only_m0_close": True,
                    "entry_fill_is_next_open": True,
                    "same_bar_resolution": "stop_first",
                }
            )
    audit = pd.DataFrame(rows)
    audit.to_csv(AUDIT_PATH, index=False)
    return audit


def _build_report(
    *,
    thresholds: pd.DataFrame,
    coverage: pd.DataFrame,
    selected: pd.DataFrame,
    audit: pd.DataFrame,
) -> str:
    selected_watchlists = selected["watchlist_rule_id"].astype(str).unique().tolist() if not selected.empty else []
    lines = [
        "# XX:00 Online Watchlist Honest Backtest",
        "",
        "Goal:",
        "- remove the post-hoc M5 universe leak;",
        "- build watchlists from raw 1m data before each full hour;",
        "- evaluate the launch only after HH:00 closes;",
        "- select on old only and read current as the honest test.",
        "",
        "Frozen session thresholds from old hourly rows:",
        _frame_to_markdown(
            thresholds,
            columns=[
                "session_id",
                "range_q1",
                "range_q2",
                "drift_q1",
                "drift_q2",
                "ret60_q1",
                "ret60_q2",
                "volume_q1",
                "volume_q2",
                "dist_q1",
                "dist_q2",
                "old_hourly_rows",
                "old_symbols",
            ],
        ),
        "",
        "Coverage for selected watchlists:",
        _frame_to_markdown(
            coverage[coverage["watchlist_rule_id"].astype(str).isin(selected_watchlists)].copy(),
            columns=[
                "session_id",
                "dataset",
                "watchlist_rule_id",
                "hourly_opportunities",
                "watchlist_passes",
                "watchlist_pass_rate",
            ],
        ),
        "",
        "Best old-selected combo per session, with current as the honest read:",
        _frame_to_markdown(
            selected,
            columns=[
                "session_id",
                "watchlist_rule_id",
                "rule_id",
                "old_trades",
                "old_equity_annualized_return_pct_3",
                "old_equity_max_drawdown_pct_5",
                "current_trades",
                "current_trades_per_year",
                "current_win_rate",
                "current_mean_return_pct",
                "current_equity_annualized_return_pct_3",
                "current_equity_annualized_return_pct_5",
                "current_equity_max_drawdown_pct_5",
                "current_positive_months_count",
                "current_non_positive_months_count",
                "current_top1_trade_share",
                "current_top3_trade_share",
                "current_top5_symbol_share",
            ],
        ),
        "",
        "Lookahead audit:",
        _frame_to_markdown(
            audit,
            columns=[
                "session_id",
                "watchlist_rule_id",
                "rule_id",
                "trades",
                "all_entries_after_signal",
                "min_signal_to_entry_minutes",
                "mean_entry_gap_vs_signal_close_pct",
                "no_posthoc_m5_universe",
                "thresholds_frozen_on_old_only",
                "watchlist_uses_only_pre_hour_data",
                "entry_fill_is_next_open",
                "same_bar_resolution",
            ],
        ),
        "",
        "Remaining caveats:",
        "- this removes the main event-universe leak, but the watchlist families are still research-driven, not a pristine untouched idea;",
        "- slippage beyond next-open fill is still not modeled;",
        "- old uses a smaller symbol universe than current, so cross-period selection is conservative but not perfectly apples-to-apples.",
        "",
    ]
    return "\n".join(lines)


def run() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    current_cutoff_ms = _current_cutoff_ms()
    old_opportunities = _build_old_opportunities(current_cutoff_ms=current_cutoff_ms)
    thresholds = _compute_thresholds(old_opportunities)
    trades, coverage = _scan_all_datasets(thresholds=thresholds, current_cutoff_ms=current_cutoff_ms)
    summary = _combo_summary(trades, coverage)
    selected = _select_best_old(summary)
    selected_trades = _selected_trades(trades, selected)
    audit = _lookahead_audit(selected_trades)
    report = _build_report(thresholds=thresholds, coverage=coverage, selected=selected, audit=audit)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(report.encode("ascii", errors="ignore").decode("ascii"))


if __name__ == "__main__":
    run()
