"""Emit a RANDOM sample of reclaim setups to the level desk for visual review,
annotated for the deep-revert thesis. Draws the poked prior-session high (level),
range mid + LOW (targets), poke high, AND the realized trade: entry (short at the
return-bar next open), stop (2 closes above the high), and the exit where the trade
actually resolved (reached low / stopped / timed out). Random (not score-ranked).
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.annotation.schemas import ANNOTATION_CANDIDATE_SCHEMA_VERSION
from anomaly_science.strategy.session_break.research.reclaim import _resample

DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
H = 3600_000
HORIZON = 48


def _eid(sym, ts):
    return "rcl_" + hashlib.blake2b(f"{sym}|{ts}".encode(), digest_size=9).hexdigest()


def _sim_trade(df, break_ts, ref_high, ref_mid):
    """Return (entry_ts, entry_px, exit_ts, exit_px, reason) for the short. Primary
    take = the range MID (reaching it = win); stop = 2 closes above the level."""
    ts = df["timestamp"].to_numpy(np.int64); o = df["open"].to_numpy(float)
    c = df["close"].to_numpy(float); lo = df["low"].to_numpy(float)
    idx = int(np.searchsorted(ts, int(break_ts)))
    if idx >= len(ts) or int(ts[idx]) != int(break_ts) or idx + 2 >= len(ts):
        return None
    ei = idx + 1
    entry_px = float(o[ei]); we = min(ei + HORIZON, len(ts))
    above = 0
    for j in range(ei, we):
        if lo[j] <= ref_mid:                      # take at the MID (favourable, checked first)
            return int(ts[ei]), entry_px, int(ts[j]), float(ref_mid), "mid"
        above = above + 1 if c[j] > ref_high else 0
        if above >= 2:                            # 2 closes above the level -> stop
            return int(ts[ei]), entry_px, int(ts[j]), float(c[j]), "stop"
    return int(ts[ei]), entry_px, int(ts[we - 1]), float(c[we - 1]), "time"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=Path(".output/results/session_break/rlabel_tf15_sametype.parquet"))
    ap.add_argument("--tf", type=int, default=15)
    ap.add_argument("--n", type=int, default=400)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out-dir", type=Path, default=Path(".output/results/session_break/reclaim_review"))
    args = ap.parse_args()
    t = pd.read_parquet(args.events)
    t = t[t.break_ts < DEV_END].drop_duplicates(["symbol", "break_ts"])
    t = t.sample(n=min(args.n, len(t)), random_state=args.seed).reset_index(drop=True)
    print(f"random sample n={len(t)}  deep(reach-low)={int((t.label_deep==1).sum())} "
          f"above={int((t.label_deep==0).sum())} mid-only={int((t.label_deep==-1).sum())}")

    rows = []; skipped = 0
    for sym, g in t.groupby("symbol"):
        p = CACHE / f"{sym}.parquet"
        if not p.exists():
            skipped += len(g); continue
        raw = pd.read_parquet(p, columns=["timestamp", "open", "high", "low", "close",
                                          "volume", "quote_volume", "trade_count",
                                          "taker_buy_quote_volume", "open_interest"])
        df = _resample(raw.sort_values("timestamp").reset_index(drop=True), args.tf)
        for r in g.itertuples():
            sim = _sim_trade(df, r.break_ts, float(r.ref_high), float(r.ref_mid))
            if sim is None:
                skipped += 1; continue
            entry_ts, entry_px, exit_ts, exit_px, reason = sim
            stop_px = entry_px * (1 + float(r.risk_frac)) if np.isfinite(r.risk_frac) else float(r.poke_high)
            rows.append({
                "candidate_schema_version": ANNOTATION_CANDIDATE_SCHEMA_VERSION,
                "event_id": _eid(r.symbol, r.break_ts), "symbol": r.symbol, "tf": f"{args.tf}m",
                "review_start_ms": int(r.ref_start_ts - 24 * H),
                "review_end_ms": int(exit_ts + 24 * H),
                "anchor_time_ms": int(r.break_ts),
                "suggested_level": float(r.ref_high),
                "suggested_level_start_ms": int(r.ref_start_ts),
                "suggested_level_end_ms": int(r.break_ts),
                "score": float(r.score) if "score" in t.columns and np.isfinite(getattr(r, "score", np.nan)) else 0.0,
                "range_mid": float(r.ref_mid), "range_low": float(r.ref_low), "poke_high": float(r.poke_high),
                "poke_ts": int(r.poke_ts),
                # realized trade geometry (drawn on the desk)
                "entry_ts": int(entry_ts), "entry_px": float(entry_px),
                "exit_ts": int(exit_ts), "exit_px": float(exit_px), "exit_reason": reason,
                "stop_px": float(stop_px),
                "outcome_deep": int(r.label_deep), "outcome_MID": int(r.label),
                "prior_up_imp": float(r.prior_up_imp), "ema_dist": float(r.ema_dist),
                "atr_pct": float(r.atr_pct), "sess_ampl_pct": float(r.sess_ampl_pct),
                "poke_rvol": float(r.poke_rvol),
                # held-level context
                "interv_below_low": int(getattr(r, "interv_below_low", 0)),
                "interv_min_frac": float(getattr(r, "interv_min_frac", 0.0)),
                "interv_high_frac": float(getattr(r, "interv_high_frac", 0.0)),
                "interv_hold_bars": int(getattr(r, "interv_hold_bars", 0)),
                "interv_poke_count": int(getattr(r, "interv_poke_count", 0)),
                "interv_poke_depth": float(getattr(r, "interv_poke_depth", 0.0)),
                "interv_touch_count": int(getattr(r, "interv_touch_count", 0)),
                "interv_close_top": float(getattr(r, "interv_close_top", 0.0)),
                "level_swing": float(getattr(r, "level_swing", 0.0)),
                "approach_from": float(getattr(r, "approach_from", 0.0)),
                "approach_bars": int(getattr(r, "approach_bars", 0)),
            })
    frame = pd.DataFrame(rows)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "candidates.parquet").unlink(missing_ok=True)
    frame.to_parquet(args.out_dir / "candidates.parquet", index=False)
    if not (args.out_dir / "review_comments.jsonl").exists():
        (args.out_dir / "review_comments.jsonl").write_text("")
    rc = frame.exit_reason.value_counts().to_dict() if len(frame) else {}
    print(f"wrote {len(frame):,} RANDOM candidates (skipped {skipped}) -> {args.out_dir/'candidates.parquet'}  exit mix={rc}")


if __name__ == "__main__":
    main()
