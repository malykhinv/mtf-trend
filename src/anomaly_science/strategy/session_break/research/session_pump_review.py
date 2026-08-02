"""STAGE 0 -- high-recall review population for the session-anchored pump anomaly.

Emits one review card per PRIOR-session pump anomaly, marked with the desk `pump`
element (base -> culmination), so an expert can eyeball it BEFORE any modelling:
keep it, redraw the pump boundaries, or reject (No setup). This is deliberately a
discovery population, NOT a trade admission rule: gates are intentionally loose
(pump >= 15%, vol & trades >= 3x same-type-session baselines) and the single-cycle
cleanliness gate is DROPPED on purpose -- Stage 2 will calibrate "one clean cycle"
and "near the high" from the expert's keep/reject decisions, not from guessed
thresholds. Every candidate stores the diagnostics needed for that calibration.

Mirrors the sleep_pump_review contract (candidates.parquet + marks.jsonl seed +
empty review_labels.jsonl). DEV only. Code lives in the session_break research pkg;
the desk UI / label schema stay in anomaly_science.annotation.
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
from anomaly_science.annotation.ohlcv import load_ohlcv_parquet
from anomaly_science.annotation.schemas import (
    ANNOTATION_CANDIDATE_SCHEMA_VERSION, LEVEL_LABEL_SCHEMA_VERSION,
)
from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.session_break.research.reclaim import _block_runs, _resample
from anomaly_science.strategy.session_break.research.sessions import (
    BLOCK_BY_SEQ, block_seq_for_ms, utc_day_for_ms,
)

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
REVIEW_DIR = Path(".output/results/session_break/session_pump_review")
DEV_END = pd.Timestamp("2026-01-01T00:00:00Z").value // 10**6

TF = 5
TF_LABEL = "5m"
LOOKBACKS = (6, 9, 12, 16)
# loose, high-recall discovery gates (NOT the strategy spec)
PUMP_MIN = 0.15
VOL_MULT = 3.0
NEAR_MAX = 0.25            # keep only cards where the next session opens within 25% of the peak
LEFT_CONTEXT_BARS = 96     # bars of context shown before the pump start
POST_BARS = 12             # small tail after the current session for context
DEDUP_MS = 12 * 3_600_000


@dataclass(frozen=True, slots=True)
class SessionPumpCandidate:
    candidate_schema_version: str
    event_id: str
    symbol: str
    tf: str
    review_start_ms: int
    review_end_ms: int
    anchor_time_ms: int
    pump_start_ms: int
    culmination_ms: int
    pump_start_price: float
    culmination_price: float
    pump_pct: float
    pump_bars: int
    session: str
    interval_hours: float
    vol_ratio: float
    tc_ratio: float
    same_vol_z: float
    # single-cycle diagnostics (Stage 2 calibrates the gate from expert decisions)
    maxdd_frac: float
    n_retrace: int
    path_efficiency: float
    # near-high diagnostic
    gap_to_peak: float
    # OI / flow context across the pump session
    oi_build_pct: float
    taker_share: float
    score: float


def _event_id(symbol: str, pump_start_ms: int, culmination_ms: int) -> str:
    raw = f"session_pump_v1|{symbol}|{TF_LABEL}|{pump_start_ms}|{culmination_ms}".encode("utf-8")
    return "sesspump_" + hashlib.blake2b(raw, digest_size=10).hexdigest()


def _scan_symbol(path: Path) -> list[SessionPumpCandidate]:
    raw = pd.read_parquet(path, columns=[
        "timestamp", "open", "high", "low", "close", "quote_volume",
        "trade_count", "taker_buy_quote_volume", "open_interest"])
    raw = raw.sort_values("timestamp").reset_index(drop=True)
    df = _resample(raw, TF)
    n = len(df)
    if n < 400:
        return []
    ts = df["timestamp"].to_numpy(np.int64)
    o = df["open"].to_numpy(float); h = df["high"].to_numpy(float)
    lo = df["low"].to_numpy(float); c = df["close"].to_numpy(float)
    qv = df["quote_volume"].to_numpy(float); tc = df["trade_count"].to_numpy(float)
    tbq = df["taker_buy_quote_volume"].to_numpy(float); oi = df["open_interest"].to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        taker = np.where(qv > 0, tbq / qv, 0.5)

    day = utc_day_for_ms(ts); seq = block_seq_for_ms(ts)
    runs = _block_runs(day, seq)                     # ordered (day, seq, st, en)
    if len(runs) < 6 * max(LOOKBACKS):
        return []
    # index runs by session type; the anomaly window feeding a same-type session is
    # the gap between it and the PREVIOUS same-type session (excludes both sessions).
    by_type: dict[int, list[int]] = {}
    for ri, (d, sq, st, en) in enumerate(runs):
        by_type.setdefault(int(sq), []).append(ri)

    out: list[SessionPumpCandidate] = []
    for sq, positions in by_type.items():
        if len(positions) < max(LOOKBACKS) + 2:
            continue
        # interval volume/trades feeding each same-type session at list position m (m>=1)
        iv_qv = np.full(len(positions), np.nan); iv_tc = np.full(len(positions), np.nan)
        for m in range(1, len(positions)):
            a = runs[positions[m - 1]][3]            # end (exclusive) of previous same-type session
            b = runs[positions[m]][2]                # start of the current same-type session
            if b - a >= 3:
                iv_qv[m] = float(qv[a:b].sum()); iv_tc[m] = float(tc[a:b].sum())

        for m in range(max(LOOKBACKS) + 1, len(positions)):
            cur = positions[m]; prev = positions[m - 1]
            aw_lo = runs[prev][3]; aw_hi = runs[cur][2]      # anomaly window [prev.en, cur.st)
            sst, sen = runs[cur][2], runs[cur][3]            # current session bars
            if aw_hi - aw_lo < 12 or sen - 1 + POST_BARS >= n or not np.isfinite(iv_qv[m]):
                continue
            # pump inside the anomaly window
            pk = aw_lo + int(np.argmax(h[aw_lo:aw_hi]))
            base_i = aw_lo + int(np.argmin(lo[aw_lo:pk + 1]))
            base = float(lo[base_i]); peak = float(h[pk])
            if base <= 0 or pk <= base_i:
                continue
            pump = (peak - base) / base
            if pump < PUMP_MIN:
                continue
            # activity anomaly vs the same inter-session intervals over prior days
            hist = iv_qv[max(1, m - max(LOOKBACKS)):m]; hist = hist[np.isfinite(hist)]
            thist = iv_tc[max(1, m - max(LOOKBACKS)):m]; thist = thist[np.isfinite(thist)]
            if len(hist) < max(LOOKBACKS) or len(thist) < max(LOOKBACKS):
                continue
            vol_ratio = float(min(iv_qv[m] / (np.median(hist[-k:]) + 1e-9) for k in LOOKBACKS))
            tc_ratio = float(min(iv_tc[m] / (np.median(thist[-k:]) + 1e-9) for k in LOOKBACKS))
            if vol_ratio < VOL_MULT or tc_ratio < VOL_MULT:
                continue
            logq = np.log(hist + 1)
            same_vol_z = float((np.log(iv_qv[m] + 1) - logq.mean()) / (logq.std() + 1e-9))
            sc_open = float(o[sst]); gap = (peak - sc_open) / peak
            if not (0 <= gap <= NEAR_MAX):
                continue

            # single-cycle diagnostics (retrace vs total run), STORED not gated
            run = peak - base
            run_max = np.maximum.accumulate(h[base_i:pk + 1])
            dd = (run_max - lo[base_i:pk + 1]) / run
            above = (dd > 0.20).astype(int)
            n_retrace = int((np.diff(above) == 1).sum() + (1 if above[0] else 0))
            path_len = float(np.abs(np.diff(c[base_i:pk + 1])).sum())
            efficiency = run / (path_len + 1e-9)
            oi_build = float(oi[aw_hi - 1] / (oi[aw_lo] + 1e-9) - 1.0)
            taker_share = float(np.nanmean(taker[aw_lo:aw_hi]))

            review_start = int(ts[max(0, base_i - LEFT_CONTEXT_BARS)])
            review_end = int(ts[min(n - 1, sen - 1 + POST_BARS)])
            out.append(SessionPumpCandidate(
                candidate_schema_version=ANNOTATION_CANDIDATE_SCHEMA_VERSION,
                event_id=_event_id(path.stem, int(ts[base_i]), int(ts[pk])),
                symbol=path.stem, tf=TF_LABEL,
                review_start_ms=review_start, review_end_ms=review_end,
                anchor_time_ms=int(ts[pk]),
                pump_start_ms=int(ts[base_i]), culmination_ms=int(ts[pk]),
                pump_start_price=base, culmination_price=peak,
                pump_pct=float(pump), pump_bars=int(pk - base_i),
                session=BLOCK_BY_SEQ[int(sq)].name,
                interval_hours=float((aw_hi - aw_lo) * TF / 60),
                vol_ratio=vol_ratio, tc_ratio=tc_ratio, same_vol_z=same_vol_z,
                maxdd_frac=float(dd.max()), n_retrace=n_retrace, path_efficiency=float(efficiency),
                gap_to_peak=float(gap), oi_build_pct=oi_build, taker_share=taker_share,
                score=float(pump),
            ))
    return out


def _seed_mark(cd: SessionPumpCandidate) -> dict:
    return {
        "event_id": cd.event_id, "symbol": cd.symbol, "tf": cd.tf,
        "source_event_id": cd.event_id, "source_event_ids": [cd.event_id], "selected_tf": cd.tf,
        "has_level": False, "has_pump_transition": True,
        "setups": [{
            "family": "unknown", "quality": "bad",
            "notes": ("BOT session-pump anomaly | Save = keep valid clean single-cycle pump near the high; "
                      "redraw pump if base/peak are wrong; delete pump or No setup = reject"),
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
        "source": "bot_session_pump_candidate",
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
        kept: list[SessionPumpCandidate] = []; times: list[int] = []
        for cd in cands:
            if any(abs(cd.culmination_ms - t) < DEDUP_MS for t in times):
                continue
            kept.append(cd); times.append(cd.culmination_ms)
            if len(kept) >= max_per_symbol:
                break
        rows.extend(asdict(cd) for cd in kept)
    if not rows:
        raise ValueError("no session-pump review candidates")
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

    seeds = [_seed_mark(SessionPumpCandidate(**row)) for row in candidates.to_dict("records")]
    review_dir.mkdir(parents=True, exist_ok=True)
    candidates.to_parquet(review_dir / "candidates.parquet", index=False)
    (review_dir / "marks.jsonl").write_text(
        "".join(json.dumps(s, ensure_ascii=False, separators=(",", ":")) + "\n" for s in seeds), encoding="utf-8")
    labels_path.touch(exist_ok=True)
    return candidates


def main() -> None:
    ap = argparse.ArgumentParser(description="Build high-recall session-pump review candidates (Stage 0).")
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
    print(f"session-pump review candidates={len(candidates)} -> {args.review_dir}")
    print("  by session:", candidates.session.value_counts().to_dict())
    print(f"  pump%: median={candidates.pump_pct.median():.2f}  vol_ratio median={candidates.vol_ratio.median():.1f}"
          f"  interval_h median={candidates.interval_hours.median():.1f}")


if __name__ == "__main__":
    main()
