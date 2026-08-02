"""Emit the top-quality reclaim short setups as level-desk candidates for visual
review. The MID-vs-ABOVE ranker is strong (AUC 0.73) but the naive trade R:R is
poor -- so the reviewer's eye is the next lever. We show the highest-confidence
setups (capped per day) with the poked level drawn, so they can judge what raises
winner precision.

Candidate schema: ohlcv_level_candidate_v1 (event_id, symbol, tf, review window,
anchor, suggested_level). Extra columns (ref_mid, poke_high, score, label) ride
along for context.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.annotation.schemas import ANNOTATION_CANDIDATE_SCHEMA_VERSION

DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
TF_LABEL = {60: "1h", 30: "30m", 15: "15m", 5: "5m"}
H = 3600_000


def _eid(sym, ts):
    return "rcl_" + hashlib.blake2b(f"{sym}|{ts}".encode(), digest_size=9).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scored", type=Path, default=Path(".output/results/session_break/rlabel_tf60_scored.parquet"))
    ap.add_argument("--per-day", type=int, default=0,
                    help="max setups per UTC day; 0 = ALL trades (honest live population)")
    ap.add_argument("--min-score", type=float, default=0.0,
                    help="optional FIXED live-executable score threshold (0 = keep all)")
    ap.add_argument("--out-dir", type=Path, default=Path(".output/results/session_break/reclaim_review"))
    args = ap.parse_args()
    t = pd.read_parquet(args.scored)
    t = t[(t.break_ts < DEV_END) & t.score.notna()].copy()
    t = t.sort_values("score", ascending=False).drop_duplicates(["symbol", "break_ts"])
    tf = int(t.tf.iloc[0]); tflabel = TF_LABEL.get(tf, f"{tf}m")

    # A live rule is a FIXED causal filter, not a hindsight per-day top-N. Default:
    # emit EVERY tradeable setup (all the trades the strategy fires). Optionally
    # apply a fixed score threshold that IS executable live.
    if args.min_score > 0:
        t = t[t.score >= args.min_score]
    if args.per_day > 0:
        t["utc_day"] = t.break_ts // (24 * H)
        t = t.sort_values("score", ascending=False).groupby("utc_day", group_keys=False).head(args.per_day)
    t = t.reset_index(drop=True)
    n_days = int(t.break_ts.nunique() and (t.break_ts // (24 * H)).nunique())
    print(f"emitting ALL {len(t):,} tradeable setups  min_score={args.min_score}  "
          f"MID-rate={t.label.mean():.3f}  ~{len(t)/max(1,n_days):.0f}/day over {n_days} days")

    rows = []
    for r in t.itertuples():
        rows.append({
            "candidate_schema_version": ANNOTATION_CANDIDATE_SCHEMA_VERSION,
            "event_id": _eid(r.symbol, r.break_ts),
            "symbol": r.symbol,
            "tf": tflabel,
            "review_start_ms": int(r.ref_start_ts - 24 * H),
            "review_end_ms": int(r.break_ts + 24 * H),
            "anchor_time_ms": int(r.break_ts),          # the return bar = decision point
            "suggested_level": float(r.ref_high),        # the poked prior-session high
            "suggested_level_start_ms": int(r.ref_start_ts),
            "suggested_level_end_ms": int(r.break_ts),
            "score": float(r.score),
            # context ride-alongs
            "range_mid": float(r.ref_mid),
            "range_low": float(r.ref_low),
            "poke_high": float(r.poke_high),
            "poke_ts": int(r.poke_ts),
            "outcome_MID": int(r.label),                 # 1=reached mid, 0=consolidated above (hindsight, for calibration)
            "gross_r": float(r.gross_r) if np.isfinite(r.gross_r) else np.nan,
        })
    frame = pd.DataFrame(rows)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "candidates.parquet").unlink(missing_ok=True)
    frame.to_parquet(args.out_dir / "candidates.parquet", index=False)
    lp = args.out_dir / "review_comments.jsonl"
    if not lp.exists():
        lp.write_text("")
    per_day = t.assign(_d=t.break_ts // (24 * H)).groupby("_d").size()
    print(f"wrote {len(frame):,} candidates -> {args.out_dir / 'candidates.parquet'}")
    print(f"per-day: median {int(per_day.median())} / max {int(per_day.max())} setups "
          f"over {len(per_day)} days")


if __name__ == "__main__":
    main()
