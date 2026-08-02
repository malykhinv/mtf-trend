"""Emit the VERTICAL >=20% pump-fade setups to the S4 desk (overwrites its candidates)
so the eye can judge whether real sharp pumps fade: anomaly (pump session + high),
structure break (swing low), BOS short entry, structural stop, scale-in level, exit +
outcome, with the anomaly and entry sessions highlighted.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.annotation.ohlcv import load_ohlcv_parquet, resample_ohlcv_np
from anomaly_science.annotation.schemas import ANNOTATION_CANDIDATE_SCHEMA_VERSION
from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.session_break.research.events import ATR_WINDOW
from anomaly_science.strategy.session_break.research.pump_fade_swing import causal_pivots
from anomaly_science.strategy.session_break.research.sessions import block_seq_for_ms

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
H = 3600_000; PIVK = 2; CAP = 60; LOOKBACK = 32


def _eid(sym, ts):
    return "pfv_" + hashlib.blake2b(f"{sym}|{ts}".encode(), digest_size=9).hexdigest()


def _rs(seq, i):
    j = i
    while j - 1 >= 0 and seq[j - 1] == seq[i]:
        j -= 1
    return j


def _re(seq, i, n):
    j = i
    while j + 1 < n and seq[j + 1] == seq[i]:
        j += 1
    return j


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=Path(".output/results/session_break/pump_fade_vertical.parquet"))
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out-dir", type=Path, default=Path(".output/results/session_break/pump_fade_review"))
    args = ap.parse_args()
    t = pd.read_parquet(args.events)
    t = t[t.entry_ts < DEV_END].drop_duplicates(["symbol", "entry_ts"])
    t = t.sample(n=min(args.n, len(t)), random_state=args.seed)
    print(f"sampling {len(t)} vertical pump-fade setups (win rate {t.win.mean():.2f})")

    rows = []; skipped = 0
    for sym, g in t.groupby("symbol"):
        p = CACHE / f"{sym}.parquet"
        if not p.exists():
            skipped += len(g); continue
        df = resample_ohlcv_np(load_ohlcv_parquet(p), 15)
        ts = df["timestamp"]; o = df["open"]; h = df["high"]; l = df["low"]; c = df["close"]; n = len(ts)
        atr = causal_atr(high=h, low=l, close=c, window=ATR_WINDOW)
        seq = block_seq_for_ms(ts); last_pl, last_ph = causal_pivots(h, l, PIVK)
        for r in g.itertuples():
            pst = int(np.searchsorted(ts, int(r.pump_ts)))
            ent = int(np.searchsorted(ts, int(r.entry_ts)))
            if pst >= n or ent >= n or ent <= pst or ent + 3 >= n:
                skipped += 1; continue
            pen = _re(seq, pst, n) + 1                     # pump session end (exclusive)
            entry = float(c[ent]); a = atr[ent] if atr[ent] > 0 else entry * 0.005
            run_hi = float(h[pen:ent + 1].max()); sh = last_ph[ent]
            swing_lo = float(last_pl[ent]) if np.isfinite(last_pl[ent]) else float(l[ent])
            stop = float((sh if (np.isfinite(sh) and sh > entry) else run_hi) + 0.3 * a)
            pump_hi = float(h[max(0, pst - LOOKBACK):pen].max())
            s1e = _re(seq, ent, n); s2e = _re(seq, min(s1e + 1, n - 1), n); we = min(s2e, ent + CAP)
            added = False; cstop = stop; exit_bar = we; exit_px = float(c[we]); reason = "hold2"; pstop = pump_hi + 0.5 * a
            for j in range(ent + 1, we + 1):
                if not added and h[j] >= pump_hi:
                    added = True; cstop = pstop
                if h[j] >= cstop:
                    exit_bar = j; exit_px = float(cstop); reason = "stop"; break
            net = -(exit_px / entry - 1)
            rows.append({
                "candidate_schema_version": ANNOTATION_CANDIDATE_SCHEMA_VERSION,
                "event_id": _eid(r.symbol, r.entry_ts), "symbol": r.symbol, "tf": "15m",
                "review_start_ms": int(ts[pst] - 24 * H), "review_end_ms": int(ts[exit_bar] + 24 * H),
                "anchor_time_ms": int(ts[ent]),
                "suggested_level": pump_hi, "suggested_level_start_ms": int(ts[max(0, pst - LOOKBACK)]),
                "suggested_level_end_ms": int(ts[ent]),
                "range_low": swing_lo, "poke_high": stop,
                "entry_ts": int(ts[ent]), "entry_px": entry,
                "exit_ts": int(ts[exit_bar]), "exit_px": exit_px,
                "exit_reason": "win" if net > 0 else ("stop" if reason == "stop" else "loss"),
                "show_anomaly_fade_metrics": 1,
                "hl_start_ms": [int(ts[pst]), int(ts[pen])],
                "hl_end_ms": [int(ts[pen - 1]) + 15 * 60000, int(ts[s1e]) + 15 * 60000],
                "hl_kind": ["anomaly", "entry"],
                "vol_z": float(r.vol_z), "idio_ret": float(r.idio_ret), "p_close_pos": float(r.p_close_pos),
                "p_ret": float(r.pump_mag), "reltc_z": np.nan, "pair": r.pair, "outcome_win": int(net > 0),
            })
    frame = pd.DataFrame(rows)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "candidates.parquet").unlink(missing_ok=True)
    frame.to_parquet(args.out_dir / "candidates.parquet", index=False)
    if not (args.out_dir / "review_comments.jsonl").exists():
        (args.out_dir / "review_comments.jsonl").write_text("")
    print(f"wrote {len(frame):,} vertical S4 candidates (skipped {skipped}) {frame.exit_reason.value_counts().to_dict() if len(frame) else ''}")


if __name__ == "__main__":
    main()
