"""Emit S3 (session-streak) events to the level desk for visual review. Draws the
event session's entry (short at open) and exit (session close), on top of the
session shading that shows the preceding down-streak. Random sample, biased to the
streaks that matter (>= 3 same-type down sessions).
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.annotation.schemas import ANNOTATION_CANDIDATE_SCHEMA_VERSION
from anomaly_science.strategy.session_break.research.session_streak import sessions_frame

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
H = 3600_000


def _eid(sym, ts):
    return "stk_" + hashlib.blake2b(f"{sym}|{ts}".encode(), digest_size=9).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=Path(".output/results/session_break/streak.parquet"))
    ap.add_argument("--tf", type=int, default=15)
    ap.add_argument("--min-k", type=int, default=3)
    ap.add_argument("--n", type=int, default=400)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out-dir", type=Path, default=Path(".output/results/session_break/streak_review"))
    args = ap.parse_args()
    t = pd.read_parquet(args.events)
    t = t[(t.start_ts < DEV_END) & (t.streak_len >= args.min_k)].drop_duplicates(["symbol", "start_ts"])
    t = t.sample(n=min(args.n, len(t)), random_state=args.seed).reset_index(drop=True)
    print(f"sample n={len(t)} (streak>={args.min_k})  cont={t.cont.mean():.3f}")

    rows = []; skipped = 0
    for sym, g in t.groupby("symbol"):
        p = CACHE / f"{sym}.parquet"
        if not p.exists():
            skipped += len(g); continue
        s = sessions_frame(p, args.tf)
        if len(s) == 0:
            skipped += len(g); continue
        s = s.set_index("start_ts")
        for r in g.itertuples():
            if int(r.start_ts) not in s.index:
                skipped += 1; continue
            row = s.loc[int(r.start_ts)]
            op = float(row.open); cl = float(row.close); end_ts = int(row.end_ts)
            k = int(r.streak_len)
            streak_start = int(r.start_ts) - k * 8 * H          # rough: back over the streak for the review window
            rows.append({
                "candidate_schema_version": ANNOTATION_CANDIDATE_SCHEMA_VERSION,
                "event_id": _eid(r.symbol, r.start_ts), "symbol": r.symbol, "tf": f"{args.tf}m",
                "review_start_ms": streak_start - 12 * H,
                "review_end_ms": end_ts + 24 * H,
                "anchor_time_ms": int(r.start_ts),
                # the momentum trade: short at the event session open, cover at its close
                "entry_ts": int(r.start_ts), "entry_px": op,
                "exit_ts": end_ts, "exit_px": cl,
                "exit_reason": "win" if cl < op else "stop",
                # streak context
                "streak_len": k, "streak_ret": float(r.streak_ret), "ev_ret": float(r.ev_ret),
                "seq": int(r.seq), "cont": int(r.cont),
                "mfe_short": float(r.mfe_short), "mae_short": float(r.mae_short),
            })
    frame = pd.DataFrame(rows)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "candidates.parquet").unlink(missing_ok=True)
    frame.to_parquet(args.out_dir / "candidates.parquet", index=False)
    if not (args.out_dir / "review_comments.jsonl").exists():
        (args.out_dir / "review_comments.jsonl").write_text("")
    print(f"wrote {len(frame):,} S3 candidates (skipped {skipped}) -> {args.out_dir/'candidates.parquet'}")


if __name__ == "__main__":
    main()
