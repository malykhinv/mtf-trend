"""STAGE 0 -- high-recall review population for DUMPS (knife-catch buy-back).

Emits one review card per sharp multi-candle DROP, marked with the desk pump element
INVERTED (top -> bottom), so an expert can eyeball it BEFORE any modelling: keep it,
redraw the top/bottom, or reject (No setup). Deliberately a discovery population, NOT a
trade rule: gates are loose (drop >= DUMP_MIN over <= MAX_DUMP_BARS) and the
single-impulse cleanliness gate is DROPPED -- Stage 2 calibrates "one clean descent" and
depth/speed from the expert's keep/reject decisions. Every candidate stores the
diagnostics needed for that calibration and for the later flow model (OI collapse,
taker share, CVD, climax wick, ...).

Bottom confirmation uses a few FORWARD bars (discovery only; the Stage 3 trade trigger
will be strictly causal). Mirrors the sleep_pump / session_pump review contract
(candidates.parquet + marks.jsonl seed + empty review_labels.jsonl). DEV only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.annotation.desk.labels import LabelStore
from anomaly_science.annotation.schemas import (
    ANNOTATION_CANDIDATE_SCHEMA_VERSION, LEVEL_LABEL_SCHEMA_VERSION,
)
from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.session_break.research.reclaim import _resample

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
REVIEW_DIR = Path(".output/results/knife_catch/dump_review")
DEV_END = pd.Timestamp("2026-01-01T00:00:00Z").value // 10**6

TF = 5
TF_LABEL = "5m"
# loose, high-recall discovery gates (NOT the strategy spec)
DUMP_MIN = 0.15            # >= 15% drop top->bottom
MAX_DUMP_BARS = 24         # within <= 2h (24 * 5m): a sharp collapse, not a slow grind
MIN_DUMP_BARS = 2
LOCAL_LOW_LOOKBACK = 6     # bottom must be the local min over this many bars back...
CONFIRM_BARS = 3          # ...and these few forward bars (discovery-only look-ahead)
# SLEEP-before-dump gate (expert rule 2026-08-01): the dump must START from a long calm
# consolidation, NOT from a sharp pump high ("дамп после спячки, а не после пампа").
SLEEP_BARS = 96           # >= 8h of calm before the dump top
MAX_SLEEP_RANGE = 0.35    # the pre-dump window (incl. the top) spans <= 35% -> no pump into the top
RUNUP_BARS = 12           # 1h window to measure a sharp run-up into the top (diagnostic)
POST_BARS = 48            # show the aftermath (the buy-back) after the bottom
DEDUP_MS = 6 * 3_600_000
EMA_BARS = 240


@dataclass(frozen=True, slots=True)
class DumpCandidate:
    candidate_schema_version: str
    event_id: str
    symbol: str
    tf: str
    review_start_ms: int
    review_end_ms: int
    anchor_time_ms: int
    # desk pump element, INVERTED: pump_start = top, culmination = bottom
    pump_start_ms: int
    culmination_ms: int
    pump_start_price: float
    culmination_price: float
    drop_pct: float
    drop_bars: int
    drop_hours: float
    drop_atr_mult: float
    speed_pct_per_min: float
    # sleep-before-dump (the expert's validity rule)
    sleep_range_pct: float
    sleep_hours: float
    base_to_top_pct: float
    pre_top_runup_pct: float
    top_spike_above_sleep_pct: float   # RED FLAG (expert): dump candle upper wick poking above the sleep -> weak buy-back
    # single-impulse diagnostics (Stage 2 calibrates the gate)
    max_upretrace_frac: float
    n_upretrace: int
    path_efficiency: float
    biggest_candle_share: float
    climax_lower_wick_share: float
    climax_close_pos: float
    # context / flow at the bottom (causal to the bottom bar)
    price_vs_ema_pct: float
    oi_change_pct: float
    taker_share_drop: float
    vol_climax_ratio: float
    score: float


def confirm_bars(tf: int) -> int:
    return max(1, round(15 / tf))


def _event_id(symbol: str, top_ms: int, bottom_ms: int, tf_label: str) -> str:
    raw = f"dump_v1|{symbol}|{tf_label}|{top_ms}|{bottom_ms}".encode("utf-8")
    return "dump_" + hashlib.blake2b(raw, digest_size=10).hexdigest()


def _ema(x: np.ndarray, span: int) -> np.ndarray:
    a = 2.0 / (span + 1.0)
    out = np.empty_like(x, dtype=float); out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def _scan_symbol(path: Path, tf: int = TF) -> list[DumpCandidate]:
    # bar-windows scaled from TIME so the detector means the same thing on any timeframe
    MAX_DUMP_BARS = max(1, round(120 / tf)); MIN_DUMP_BARS = max(1, round(10 / tf))
    LOCAL_LOW_LOOKBACK = max(1, round(30 / tf)); CONFIRM_BARS = confirm_bars(tf)
    SLEEP_BARS = max(1, round(480 / tf)); RUNUP_BARS = max(1, round(60 / tf))
    POST_BARS = max(1, round(240 / tf)); EMA_BARS = max(30, round(1200 / tf))
    tf_label = f"{tf}m"
    raw = pd.read_parquet(path, columns=[
        "timestamp", "open", "high", "low", "close", "quote_volume",
        "trade_count", "taker_buy_quote_volume", "open_interest"])
    raw = raw.sort_values("timestamp").reset_index(drop=True)
    df = _resample(raw, tf)
    n = len(df)
    if n < SLEEP_BARS + MAX_DUMP_BARS + POST_BARS + 5:
        return []
    ts = df["timestamp"].to_numpy(np.int64)
    o = df["open"].to_numpy(float); h = df["high"].to_numpy(float)
    lo = df["low"].to_numpy(float); c = df["close"].to_numpy(float)
    qv = df["quote_volume"].to_numpy(float); tc = df["trade_count"].to_numpy(float)
    tbq = df["taker_buy_quote_volume"].to_numpy(float); oi = df["open_interest"].to_numpy(float)
    atr = causal_atr(high=h, low=lo, close=c, window=64)
    ema = _ema(c, EMA_BARS)
    with np.errstate(invalid="ignore", divide="ignore"):
        taker = np.where(qv > 0, tbq / qv, 0.5)
    base_qv = pd.Series(qv).rolling(EMA_BARS, min_periods=30).median().to_numpy()

    # vectorised prefilter: a bar is a bottom candidate only if it is the local min over
    # [b-LOCAL_LOW_LOOKBACK, b+CONFIRM_BARS]. Skips the expensive inner work for ~all bars.
    win = LOCAL_LOW_LOOKBACK + CONFIRM_BARS + 1
    roll_min = pd.Series(lo).rolling(win, min_periods=win).min().shift(-CONFIRM_BARS).to_numpy()
    is_bottom = np.isfinite(roll_min) & (lo <= roll_min)

    out: list[DumpCandidate] = []
    seen_times: list[int] = []
    lo_first = MAX_DUMP_BARS + SLEEP_BARS + 1     # need the full sleep window before the top
    lo_last = n - POST_BARS - 1
    for b in np.flatnonzero(is_bottom):
        b = int(b)
        if b < lo_first or b >= lo_last:
            continue
        # top = highest high in the preceding drop window
        s0 = max(0, b - MAX_DUMP_BARS)
        t = s0 + int(np.argmax(h[s0:b]))
        top = float(h[t]); bottom = float(lo[b])
        if t >= b or top <= 0:
            continue
        drop = (top - bottom) / top
        if drop < DUMP_MIN:
            continue
        drop_bars = b - t
        if drop_bars < MIN_DUMP_BARS:
            continue
        # --- SLEEP-before-dump gate: the top must sit at the end of a long CALM base,
        # not on a sharp pump high. The pre-dump window (incl. the top) must be tight.
        sw0 = t - SLEEP_BARS
        if sw0 < 0:
            continue
        sleep_hi = float(h[sw0:t + 1].max()); sleep_lo = float(lo[sw0:t + 1].min())
        sleep_range = (sleep_hi - sleep_lo) / sleep_lo if sleep_lo > 0 else np.inf
        if not (sleep_range <= MAX_SLEEP_RANGE):
            continue
        sleep_median = float(np.median(c[sw0:t]))
        base_to_top = (top - sleep_median) / sleep_median if sleep_median > 0 else np.nan
        ru0 = max(0, t - RUNUP_BARS)
        pre_runup = (top - float(lo[ru0:t + 1].min())) / top if top > 0 else np.nan
        # RED FLAG: how far the dump top's upper wick pokes ABOVE the sleep range (excl. the top bar)
        sleep_hi_excl = float(h[sw0:t].max()) if t > sw0 else top
        top_spike = (top - sleep_hi_excl) / sleep_hi_excl if sleep_hi_excl > 0 else np.nan
        # de-dup nearby bottoms (keep the deepest via score ordering later)
        if any(abs(int(ts[b]) - pt) < DEDUP_MS for pt in seen_times):
            continue

        run = top - bottom
        # single-impulse: worst upward retrace during the descent, vs total drop
        seg_h = h[t:b + 1]; seg_l = lo[t:b + 1]; seg_c = c[t:b + 1]
        run_min = np.minimum.accumulate(seg_l)
        up = (seg_h - run_min) / run
        above = (up > 0.20).astype(int)
        n_upretrace = int((np.diff(above) == 1).sum() + (1 if above[0] else 0))
        path_len = float(np.abs(np.diff(seg_c)).sum())
        path_eff = run / (path_len + 1e-9)
        seg_o = o[t:b + 1]
        cand_range = seg_h - seg_l
        down_body = np.maximum(seg_o - seg_c, 0.0)
        biggest_candle_share = float(down_body.max() / run) if run > 0 else np.nan
        # climax candle = the deepest down candle in the descent
        ci = t + int(np.argmax(down_body)) if down_body.max() > 0 else b
        crng = float(h[ci] - lo[ci])
        climax_lower_wick = (min(float(o[ci]), float(c[ci])) - lo[ci]) / crng if crng > 0 else 0.0
        climax_close_pos = (float(c[ci]) - lo[ci]) / crng if crng > 0 else 0.5

        drop_atr = run / (atr[t] + 1e-12)
        speed = drop / (drop_bars * tf)                 # frac per minute
        price_vs_ema = bottom / (ema[b] + 1e-12) - 1.0  # how far below the trend
        oi_change = oi[b] / (oi[t] + 1e-9) - 1.0        # OI collapse over the dump
        taker_drop = float(np.nanmean(taker[t:b + 1]))
        vol_climax = float(np.max(qv[t:b + 1]) / (np.nanmedian(base_qv[t:b + 1]) + 1e-9))

        seen_times.append(int(ts[b]))
        out.append(DumpCandidate(
            candidate_schema_version=ANNOTATION_CANDIDATE_SCHEMA_VERSION,
            event_id=_event_id(path.stem, int(ts[t]), int(ts[b]), tf_label),
            symbol=path.stem, tf=tf_label,
            review_start_ms=int(ts[max(0, sw0 - 12)]),          # show the sleep base
            review_end_ms=int(ts[min(n - 1, b + POST_BARS)]),
            anchor_time_ms=int(ts[b]),
            pump_start_ms=int(ts[t]), culmination_ms=int(ts[b]),
            pump_start_price=top, culmination_price=bottom,
            drop_pct=float(drop), drop_bars=int(drop_bars),
            drop_hours=float(drop_bars * tf / 60), drop_atr_mult=float(drop_atr),
            speed_pct_per_min=float(speed),
            sleep_range_pct=float(sleep_range), sleep_hours=float(SLEEP_BARS * tf / 60),
            base_to_top_pct=float(base_to_top), pre_top_runup_pct=float(pre_runup),
            top_spike_above_sleep_pct=float(top_spike),
            max_upretrace_frac=float(up.max()), n_upretrace=n_upretrace,
            path_efficiency=float(path_eff), biggest_candle_share=float(biggest_candle_share),
            climax_lower_wick_share=float(climax_lower_wick), climax_close_pos=float(climax_close_pos),
            price_vs_ema_pct=float(price_vs_ema), oi_change_pct=float(oi_change),
            taker_share_drop=float(taker_drop), vol_climax_ratio=float(vol_climax),
            score=float(drop),
        ))
    return out


def _seed_mark(cd: DumpCandidate) -> dict:
    return {
        "event_id": cd.event_id, "symbol": cd.symbol, "tf": cd.tf,
        "source_event_id": cd.event_id, "source_event_ids": [cd.event_id], "selected_tf": cd.tf,
        "has_level": False, "has_pump_transition": True,
        "setups": [{
            "family": "unknown", "quality": "bad",
            "notes": ("BOT DUMP (marked top->bottom) | Save = keep clean single-impulse capitulation "
                      "dump; redraw top/bottom if wrong; delete or No setup = reject (staircase / slow "
                      "grind / dead coin)"),
            "has_level": False, "has_pump_transition": True, "has_structure_break": False,
            "level_price": None, "level_start_ms": None, "level_end_ms": None,
            "level_broken": None, "level_touch_times_ms": None,
            "pump_start_ms": cd.pump_start_ms, "pump_start_price": cd.pump_start_price,
            "culmination_ms": cd.culmination_ms, "culmination_price": cd.culmination_price,
            "structure_break_ms": None, "structure_break_price": None,
            "structure_swing_low_ms": None, "structure_swing_low_price": None,
            "entry_ms": None, "entry_price": None, "entry_auto": None, "entry_source": None,
            "exit_ms": None, "exit_price": None, "sl_price": None, "sl_ms": None,
            "sl_hit_ms": None, "sl_auto": None, "sl_source": None, "zigzag_points": [],
        }],
        "source": "bot_dump_candidate",
        "label_schema_version": LEVEL_LABEL_SCHEMA_VERSION, "saved_at_ms": 0,
    }


def build_review(*, cache_dir: Path = CACHE, review_dir: Path = REVIEW_DIR,
                 end_ms: int = DEV_END, max_events: int = 400, max_per_symbol: int = 3,
                 limit: int = 0, refresh_existing_labels: bool = False) -> pd.DataFrame:
    rows: list[dict] = []
    files = sorted(cache_dir.glob("*.parquet"))
    if limit:
        files = files[:limit]
    for path in files:
        try:
            cands = [cd for cd in _scan_symbol(path) if cd.culmination_ms < end_ms]
        except (OSError, ValueError, KeyError):
            continue
        cands.sort(key=lambda r: r.score, reverse=True)
        rows.extend(asdict(cd) for cd in cands[:max_per_symbol])
    if not rows:
        raise ValueError("no dump review candidates")
    candidates = (pd.DataFrame(rows).sort_values("score", ascending=False).head(max_events)
                  .sort_values(["culmination_ms", "symbol"]).reset_index(drop=True))

    labels_path = review_dir / "review_labels.jsonl"
    if labels_path.exists() and labels_path.stat().st_size:
        if not refresh_existing_labels:
            raise FileExistsError(f"refusing to replace annotated queue without --refresh-existing-labels: {labels_path}")
        candidate_ids = set(candidates["event_id"])
        referenced = {str(l.get("source_event_id") or "") for l in LabelStore(labels_path).read_effective().values()}
        missing = referenced - candidate_ids
        if missing:
            raise ValueError("refresh would orphan expert labels: " + ", ".join(sorted(missing)[:5]))

    seeds = [_seed_mark(DumpCandidate(**row)) for row in candidates.to_dict("records")]
    review_dir.mkdir(parents=True, exist_ok=True)
    candidates.to_parquet(review_dir / "candidates.parquet", index=False)
    (review_dir / "marks.jsonl").write_text(
        "".join(json.dumps(s, ensure_ascii=False, separators=(",", ":")) + "\n" for s in seeds), encoding="utf-8")
    labels_path.touch(exist_ok=True)
    return candidates


def main() -> None:
    ap = argparse.ArgumentParser(description="Build high-recall dump (knife-catch) review candidates (Stage 0).")
    ap.add_argument("--cache-dir", type=Path, default=CACHE)
    ap.add_argument("--review-dir", type=Path, default=REVIEW_DIR)
    ap.add_argument("--max-events", type=int, default=400)
    ap.add_argument("--max-per-symbol", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0, help="cap symbols for a smoke test")
    ap.add_argument("--refresh-existing-labels", action="store_true")
    args = ap.parse_args()
    candidates = build_review(cache_dir=args.cache_dir, review_dir=args.review_dir,
                              max_events=args.max_events, max_per_symbol=args.max_per_symbol,
                              limit=args.limit, refresh_existing_labels=args.refresh_existing_labels)
    print(f"dump review candidates={len(candidates)} -> {args.review_dir}")
    print(f"  drop%: median={candidates.drop_pct.median():.2f}  drop_bars median={candidates.drop_bars.median():.0f}"
          f"  atr_mult median={candidates.drop_atr_mult.median():.1f}")
    print(f"  SLEEP range median={candidates.sleep_range_pct.median():.2f} (<= {MAX_SLEEP_RANGE})"
          f"  base_to_top median={candidates.base_to_top_pct.median():+.2f}  pre_runup median={candidates.pre_top_runup_pct.median():.2f}")
    print(f"  oi_change median={candidates.oi_change_pct.median():+.3f}  taker(drop) median={candidates.taker_share_drop.median():.2f}")


if __name__ == "__main__":
    main()
