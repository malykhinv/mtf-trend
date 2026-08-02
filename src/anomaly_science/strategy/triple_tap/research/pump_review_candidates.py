"""Build high-recall pump candidates for manual level annotation.

This is a strategy adapter only. The browser UI and label schema live in the
generic ``anomaly_science.annotation`` package.
"""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.annotation.ohlcv import load_ohlcv_parquet, resample_ohlcv_np
from anomaly_science.annotation.schemas import ANNOTATION_CANDIDATE_SCHEMA_VERSION
from anomaly_science.strategy.pump_long.research.context import DEV_END_MS
from anomaly_science.strategy.triple_tap.detect import CACHE_1M
from anomaly_science.strategy.triple_tap.research.brkcap_foundations import FOUNDATION_POLICY
from anomaly_science.strategy.triple_tap.research.economics import RES

REVIEW_DIR = RES / "manual_pump_review"
CANDIDATES_OUT = REVIEW_DIR / "pump_review_candidates.parquet"
LABELS_OUT = REVIEW_DIR / "pump_level_labels.jsonl"
TF_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "1h": 60, "4h": 240}
DEFAULT_TFS = ("5m", "10m", "15m")

SLEEP_BARS = 96
PUMP_LOOKBACK_BARS = 64
POST_BARS = 192
LEFT_CONTEXT_BARS = 96
MIN_PUMP_PCT = 0.14
MAX_SLEEP_RANGE_PCT = 0.12
MIN_PUMP_VOL_OVER_SLEEP = 2.0
MIN_PUMP_TRADES_OVER_SLEEP = 1.5
# A transient low below an established base is a dump->rebound, not a pump.
# This is unconditional: a large rebound does not retroactively turn the dump
# into sleep. The threshold is shared with the versioned foundation contract.
MAX_DUMP_BELOW_BASE = FOUNDATION_POLICY.max_start_below_sleep_median_pct


@dataclass(frozen=True)
class PumpReviewCandidate:
    candidate_schema_version: str
    event_id: str
    symbol: str
    tf: str
    review_start_ms: int
    review_end_ms: int
    anchor_time_ms: int
    pump_start_ms: int
    culmination_ms: int
    pump_low_price: float
    culmination_price: float
    pump_pct: float
    pump_bars: int
    pump_hours: float
    pump_path_eff: float
    sleep_range_pct: float
    pump_over_sleep_vol: float
    pump_over_sleep_trades: float
    post_min_low: float
    post_max_high: float
    post_range_pct: float
    suggested_level: float
    suggested_level_start_ms: int
    suggested_level_end_ms: int
    score: float


def _event_id(symbol: str, tf: str, pump_start_ms: int, culmination_ms: int) -> str:
    raw = f"{symbol}|{tf}|{pump_start_ms}|{culmination_ms}".encode("utf-8")
    return hashlib.blake2b(raw, digest_size=10).hexdigest()


def _path_eff(close: np.ndarray, start: int, end: int) -> float:
    if end <= start:
        return np.nan
    path = float(np.sum(np.abs(np.diff(close[start:end + 1]))))
    disp = abs(float(close[end] - close[start]))
    return disp / path if path > 0 else np.nan


def _ratio(a: float, b: float) -> float:
    return a / b if np.isfinite(a) and np.isfinite(b) and b > 0 else np.nan


def _is_dump_rebound(pump_low: float, sleep_close: np.ndarray) -> bool:
    """Whether the proposed start is a dump below the established sleep base."""

    if len(sleep_close) == 0:
        return True
    base_ref = float(np.median(sleep_close))
    if not (np.isfinite(base_ref) and base_ref > 0 and np.isfinite(pump_low) and pump_low > 0):
        return True
    return (base_ref - pump_low) / base_ref > MAX_DUMP_BELOW_BASE


def _suggest_level(high: np.ndarray, low: np.ndarray, start: int, end: int, culmination: float) -> float:
    if end <= start:
        return np.nan
    upper = high[start:end]
    upper = upper[upper < culmination * 0.995]
    if len(upper) == 0:
        upper = high[start:end]
    level = float(np.quantile(upper, 0.90))
    return level if level > float(np.min(low[start:end])) else np.nan


def _scan_symbol_tf(
    symbol: str,
    base: dict[str, np.ndarray],
    tf: str,
    *,
    end_ms: int,
    max_per_symbol_tf: int,
) -> list[PumpReviewCandidate]:
    minutes = TF_MINUTES[tf]
    frame = base if minutes == 1 else resample_ohlcv_np(base, minutes)
    ts = frame["timestamp"]
    high = frame["high"]
    low = frame["low"]
    close = frame["close"]
    qv = frame["quote_volume"]
    tc = frame["trade_count"]
    n = len(ts)
    if n < SLEEP_BARS + PUMP_LOOKBACK_BARS + POST_BARS + 10:
        return []
    rows: list[PumpReviewCandidate] = []
    stop = min(int(np.searchsorted(ts, end_ms, side="left")), n - POST_BARS - 1)
    for c_idx in range(SLEEP_BARS + PUMP_LOOKBACK_BARS, stop):
        look0 = c_idx - PUMP_LOOKBACK_BARS
        p_idx = look0 + int(np.argmin(low[look0:c_idx + 1]))
        if p_idx >= c_idx:
            continue
        pump_low = float(low[p_idx])
        culmination = float(high[c_idx])
        if pump_low <= 0:
            continue
        pump_pct = (culmination - pump_low) / pump_low
        if pump_pct < MIN_PUMP_PCT:
            continue
        sl0 = max(0, p_idx - SLEEP_BARS)
        if p_idx - sl0 < max(12, SLEEP_BARS // 4):
            continue
        sleep_low = float(np.min(low[sl0:p_idx]))
        sleep_high = float(np.max(high[sl0:p_idx]))
        sleep_range = (sleep_high - sleep_low) / sleep_low if sleep_low > 0 else np.nan
        if not (np.isfinite(sleep_range) and sleep_range <= MAX_SLEEP_RANGE_PCT):
            continue
        # Measure the pump from the established pre-pump base, not the dump wick.
        if _is_dump_rebound(pump_low, close[sl0:p_idx]):
            continue
        vol_step = _ratio(float(np.mean(qv[p_idx:c_idx + 1])), float(np.mean(qv[sl0:p_idx])))
        trade_step = _ratio(float(np.mean(tc[p_idx:c_idx + 1])), float(np.mean(tc[sl0:p_idx])))
        if not (
            np.isfinite(vol_step)
            and vol_step >= MIN_PUMP_VOL_OVER_SLEEP
            and np.isfinite(trade_step)
            and trade_step >= MIN_PUMP_TRADES_OVER_SLEEP
        ):
            continue
        post_start = c_idx + 1
        post_end = min(n, c_idx + POST_BARS)
        post_min = float(np.min(low[post_start:post_end]))
        post_max = float(np.max(high[post_start:post_end]))
        post_range = (post_max - post_min) / post_min if post_min > 0 else np.nan
        eff = _path_eff(close, p_idx, c_idx)
        suggested = _suggest_level(high, low, post_start, post_end, culmination)
        score = float(pump_pct * min(vol_step, 20.0) * min(trade_step, 20.0) * (eff if np.isfinite(eff) else 0.5))
        review_start = int(ts[max(0, p_idx - LEFT_CONTEXT_BARS)])
        review_end = int(ts[post_end - 1])
        rows.append(
            PumpReviewCandidate(
                candidate_schema_version=ANNOTATION_CANDIDATE_SCHEMA_VERSION,
                event_id=_event_id(symbol, tf, int(ts[p_idx]), int(ts[c_idx])),
                symbol=symbol,
                tf=tf,
                review_start_ms=review_start,
                review_end_ms=review_end,
                anchor_time_ms=int(ts[c_idx]),
                pump_start_ms=int(ts[p_idx]),
                culmination_ms=int(ts[c_idx]),
                pump_low_price=pump_low,
                culmination_price=culmination,
                pump_pct=float(pump_pct),
                pump_bars=int(c_idx - p_idx),
                pump_hours=float((c_idx - p_idx) * minutes / 60),
                pump_path_eff=float(eff),
                sleep_range_pct=float(sleep_range),
                pump_over_sleep_vol=float(vol_step),
                pump_over_sleep_trades=float(trade_step),
                post_min_low=post_min,
                post_max_high=post_max,
                post_range_pct=float(post_range),
                suggested_level=float(suggested),
                suggested_level_start_ms=int(ts[post_start]),
                suggested_level_end_ms=review_end,
                score=score,
            )
        )
    rows.sort(key=lambda r: (r.score, r.pump_pct), reverse=True)
    dedup: list[PumpReviewCandidate] = []
    used: list[int] = []
    for row in rows:
        if any(abs(row.culmination_ms - t) < 12 * 3_600_000 for t in used):
            continue
        dedup.append(row)
        used.append(row.culmination_ms)
        if len(dedup) >= max_per_symbol_tf:
            break
    return dedup


def build_pump_review_candidates(
    *,
    cache_dir: Path = CACHE_1M,
    out_path: Path = CANDIDATES_OUT,
    tfs: tuple[str, ...] = DEFAULT_TFS,
    end_ms: int = DEV_END_MS,
    max_events: int = 600,
    max_per_symbol_tf: int = 2,
) -> pd.DataFrame:
    rows: list[dict] = []
    symbols = sorted(p.stem for p in cache_dir.glob("*.parquet"))
    for i, symbol in enumerate(symbols, 1):
        try:
            base = load_ohlcv_parquet(cache_dir / f"{symbol}.parquet")
            for tf in tfs:
                rows.extend(asdict(r) for r in _scan_symbol_tf(symbol, base, tf, end_ms=end_ms, max_per_symbol_tf=max_per_symbol_tf))
        except Exception as exc:
            print(f"skip {symbol}: {exc}", flush=True)
        if i % 100 == 0 or i == len(symbols):
            print(f"{i}/{len(symbols)} symbols, raw candidates={len(rows)}", flush=True)
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError("no pump review candidates found")
    frame = (
        frame.sort_values(["score", "pump_pct"], ascending=False)
        .drop_duplicates("event_id")
        .head(max_events)
        .sort_values(["culmination_ms", "symbol", "tf"])
        .reset_index(drop=True)
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out_path, index=False)
    print(f"saved {len(frame)} pump review candidates -> {out_path}", flush=True)
    return frame


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=CANDIDATES_OUT)
    parser.add_argument("--max-events", type=int, default=600)
    parser.add_argument("--max-per-symbol-tf", type=int, default=2)
    parser.add_argument("--tf", action="append", choices=sorted(TF_MINUTES), default=None)
    args = parser.parse_args()
    build_pump_review_candidates(
        out_path=args.out,
        tfs=tuple(args.tf) if args.tf else DEFAULT_TFS,
        max_events=args.max_events,
        max_per_symbol_tf=args.max_per_symbol_tf,
    )


if __name__ == "__main__":
    main()
