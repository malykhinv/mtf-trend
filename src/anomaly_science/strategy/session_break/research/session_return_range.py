"""Strategy #2 -- RETURN TO A DISTANT PRIOR RANGE (magnet / gap-fill).

Idea (user): a prior session forms a range R1. Price then moves AWAY from it (a
clear gap), consolidating a comparable local range R2. When a candle BREAKS OUT of
R2 toward R1, does price go on to REACH R1 (fill the gap back to the old range)?
The canonical case is: price dumped below an old range, then buys back UP to it (a
LONG). We also keep the mirror (broke down toward a range below = short bias).

We predict, AT THE BREAKOUT CANDLE'S CLOSE, whether price returns to R1 -- metrics +
discrimination only, no entry simulation yet.

Event conditions (all causal at the breakout bar t):
  * R1 = a prior-session range within a lookback window, on the far side of price;
  * GAP: distance from price to R1's near edge > max(|R1|, |R2|)  (a real gap);
  * COMPARABLE: 0.5 <= |R1|/|R2| <= 2;
  * BREAKOUT: close[t] exits the local range R2 toward R1 by >= 0.5 * ATR.
Outcome: within HORIZON bars, price touches R1's near edge => returned (1) else (0).

Also emits desk candidates (R1 band + breakout + return target) for visual review.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

from anomaly_science.annotation.ohlcv import load_ohlcv_parquet, resample_ohlcv_np
from anomaly_science.annotation.schemas import ANNOTATION_CANDIDATE_SCHEMA_VERSION
from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.session_break.research.events import ATR_WINDOW
from anomaly_science.strategy.session_break.research.reclaim import _block_runs
from anomaly_science.strategy.session_break.research.sessions import block_seq_for_ms, utc_day_for_ms

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
H = 3600_000
R2_BARS = 16          # local range lookback (~1 session on 15m)
LOOKBACK_BARS = 400   # how far back to look for a prior-session range R1
HORIZON = 96          # bars to reach R1 after the breakout


def build_symbol(path: Path, tf_min: int) -> pd.DataFrame:
    base = load_ohlcv_parquet(path)
    df = base if tf_min == 1 else resample_ohlcv_np(base, tf_min)
    ts = df["timestamp"]; o = df["open"]; h = df["high"]; lo = df["low"]; c = df["close"]
    qv = df["quote_volume"]; tc = df["trade_count"]
    n = len(ts)
    if n < 300:
        return pd.DataFrame()
    atr = causal_atr(high=h, low=lo, close=c, window=ATR_WINDOW)
    day = utc_day_for_ms(ts); seq = block_seq_for_ms(ts)
    runs = _block_runs(day, seq)   # (day, seq, start, end)
    # prior-session ranges as arrays
    sruns = [(st, en, float(lo[st:en].min()), float(h[st:en].max()), int(ts[st])) for (_, _, st, en) in runs if en - st >= 3]
    starts = np.array([r[0] for r in sruns]) if sruns else np.array([0])
    rows = []
    # iterate candidate breakout bars
    for t in range(R2_BARS + 5, n - 2):
        a0 = atr[t]
        if not (np.isfinite(a0) and a0 > 0):
            continue
        r2_hi = float(h[t - R2_BARS:t].max()); r2_lo = float(lo[t - R2_BARS:t].min())
        r2 = r2_hi - r2_lo
        if r2 <= 0:
            continue
        px = float(c[t])
        up_break = c[t] > r2_hi + 0.5 * a0            # broke local range up
        dn_break = c[t] < r2_lo - 0.5 * a0            # broke local range down
        if not (up_break or dn_break):
            continue
        # find a prior-session range on the far side, within the lookback, ending before t
        best = None
        lo_bound = ts[t] - LOOKBACK_BARS * tf_min * 60_000
        for (sst, sen, L1, Hh, sts) in sruns:
            if sen >= t - R2_BARS:      # must be fully in the past (before the local window)
                continue
            if sts < lo_bound:
                continue
            r1 = Hh - L1
            if r1 <= 0:
                continue
            ratio = r1 / r2
            if not (0.5 <= ratio <= 2.0):
                continue
            if up_break:
                # R1 above, price gapped below it
                near = L1
                gap = near - px
                if gap <= max(r1, r2):
                    continue
                if gap > 6 * max(r1, r2):
                    continue
                if best is None or near < best[0]:      # nearest above
                    best = (near, L1, Hh, r1, sts, +1)
            else:
                near = Hh
                gap = px - near
                if gap <= max(r1, r2):
                    continue
                if gap > 6 * max(r1, r2):
                    continue
                if best is None or near > best[0]:      # nearest below
                    best = (near, L1, Hh, r1, sts, -1)
        if best is None:
            continue
        near, L1, Hh, r1, r1_sts, direction = best
        # OUTCOME: within HORIZON, does price touch R1's near edge?
        we = min(t + 1 + HORIZON, n)
        if direction > 0:
            reached = bool((h[t + 1:we] >= near).any())
        else:
            reached = bool((lo[t + 1:we] <= near).any())
        gap = abs(near - px)
        rows.append({
            "symbol": path.stem, "break_ts": int(ts[t]), "direction": int(direction),
            "week": pd.Timestamp(ts[t], unit="ms", tz="UTC").strftime("%G-W%V"),
            "returned": int(reached),
            # geometry (desk)
            "r1_low": L1, "r1_high": Hh, "r1_near": near, "r1_start_ts": int(r1_sts),
            "px": px, "r2_hi": r2_hi, "r2_lo": r2_lo,
            # causal features
            "gap_atr": gap / a0, "gap_over_r1": gap / r1, "r1_over_r2": r1 / r2,
            "r1_atr": r1 / a0, "r2_atr": r2 / a0, "r1_age_bars": (t - starts[starts < t][-1]) if (starts < t).any() else 0,
            "brk_body": abs(c[t] - o[t]) / (h[t] - lo[t] + 1e-12),
            "brk_atr": (c[t] - o[t]) / a0 * direction,          # breakout strength toward R1
            "brk_rvol": qv[t] / (np.median(qv[max(0, t - 96):t]) + 1e-9),
            "brk_rtrades": tc[t] / (np.median(tc[max(0, t - 96):t]) + 1e-9),
            "mom_5": (c[t] / c[t - 5] - 1.0) * direction,
        })
    return pd.DataFrame(rows)


def _one(a):
    p, tf = a
    try:
        return build_symbol(Path(p), tf)
    except Exception:
        return pd.DataFrame()


def _eid(sym, ts):
    return "rtr_" + hashlib.blake2b(f"{sym}|{ts}".encode(), digest_size=9).hexdigest()


def emit_desk(t: pd.DataFrame, out_dir: Path, tf_min: int, n: int, seed: int) -> None:
    d = t[t.break_ts < DEV_END].drop_duplicates(["symbol", "break_ts"])
    d = d.sample(n=min(n, len(d)), random_state=seed)
    rows = []
    for r in d.itertuples():
        rows.append({
            "candidate_schema_version": ANNOTATION_CANDIDATE_SCHEMA_VERSION,
            "event_id": _eid(r.symbol, r.break_ts), "symbol": r.symbol, "tf": f"{tf_min}m",
            "review_start_ms": int(r.r1_start_ts - 12 * H), "review_end_ms": int(r.break_ts + 48 * H),
            "anchor_time_ms": int(r.break_ts),
            # draw R1 band as the range mid/low/high, breakout price as entry, near edge = target
            "suggested_level": float(r.r1_near), "suggested_level_start_ms": int(r.r1_start_ts),
            "suggested_level_end_ms": int(r.break_ts),
            "range_mid": float(0.5 * (r.r1_low + r.r1_high)), "range_low": float(r.r1_low),
            "r1_high": float(r.r1_high),
            "entry_ts": int(r.break_ts), "entry_px": float(r.px),
            "exit_ts": int(r.break_ts), "exit_px": float(r.r1_near),
            "exit_reason": "returned" if r.returned else "no",
            "direction": int(r.direction), "returned": int(r.returned),
            "gap_atr": float(r.gap_atr), "r1_over_r2": float(r.r1_over_r2), "brk_rvol": float(r.brk_rvol),
        })
    frame = pd.DataFrame(rows)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "candidates.parquet").unlink(missing_ok=True)
    frame.to_parquet(out_dir / "candidates.parquet", index=False)
    if not (out_dir / "review_comments.jsonl").exists():
        (out_dir / "review_comments.jsonl").write_text("")
    print(f"wrote {len(frame):,} S2 desk candidates -> {out_dir/'candidates.parquet'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", type=int, default=15)
    ap.add_argument("--out", type=Path, default=Path(".output/results/session_break/return_range.parquet"))
    ap.add_argument("--desk-dir", type=Path, default=Path(".output/results/session_break/return_range_review"))
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    files = sorted(glob.glob(str(CACHE / "*.parquet")))
    print(f"tf={args.tf}m symbols={len(files)}")
    frames = []; done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_one, (f, args.tf)): f for f in files}
        for fut in as_completed(futs):
            r = fut.result()
            if len(r):
                frames.append(r)
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(files)} events={sum(len(x) for x in frames):,}")
    t = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    t.to_parquet(args.out)
    dev = t[t.break_ts < DEV_END].copy()
    print(f"\nwrote {len(t):,} events ({len(dev):,} DEV) | base returned={dev.returned.mean():.3f} "
          f"| up={int((dev.direction>0).sum()):,} down={int((dev.direction<0).sum()):,}")
    for dr, lbl in [(1, "UP (buy back to range above)"), (-1, "DOWN (sell back to range below)")]:
        s = dev[dev.direction == dr]
        if len(s) < 100:
            continue
        print(f"  {lbl}: n={len(s):,} returned={s.returned.mean():.3f}")

    feats = ["gap_atr", "gap_over_r1", "r1_over_r2", "r1_atr", "r2_atr", "r1_age_bars",
             "brk_body", "brk_atr", "brk_rvol", "brk_rtrades", "mom_5"]
    d = dev.dropna(subset=feats)
    X = d[feats].to_numpy(float); y = d.returned.to_numpy(int); wk = d.week.to_numpy()
    med = np.nanmedian(X, 0); ix = np.where(~np.isfinite(X)); X[ix] = np.take(med, ix[1])
    o = np.full(len(y), np.nan)
    for tr, te in GroupKFold(5).split(X, y, wk):
        if len(np.unique(y[tr])) < 2:
            continue
        m = CatBoostClassifier(depth=4, iterations=300, learning_rate=0.04, l2_leaf_reg=6, verbose=False, random_seed=0)
        m.fit(X[tr], y[tr]); o[te] = m.predict_proba(X[te])[:, 1]
    mk = np.isfinite(o)
    print(f"\n=== return discrimination: week-CV AUC={roc_auc_score(y[mk], o[mk]):.3f} (base {y.mean():.3f}) ===")
    thr = np.nanpercentile(o[mk], 90)
    print(f"  top-decile return rate={y[mk & (o>=thr)].mean():.3f}")
    imp = pd.Series(CatBoostClassifier(depth=4, iterations=300, learning_rate=0.04, verbose=False, random_seed=0)
                    .fit(X, y).get_feature_importance(), index=feats).sort_values(ascending=False)
    print(imp.to_string(float_format=lambda x: f"{x:.1f}"))

    emit_desk(t, args.desk_dir, args.tf, 400, 7)


if __name__ == "__main__":
    main()
