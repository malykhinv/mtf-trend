"""Emit S4 (anomalous-volume PUMP-FADE short) setups to the level desk for visual
review. Per random champion setup we draw: the pump high (anomaly resistance), the
broken swing low (structure break), the swing high (structural stop), the BOS short
entry, the scale-in add level (= pump high), and the realised exit + outcome, on the
session-shaded chart. So the eye can check the break, entry, exit, context, anomaly.
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
FADE_PAIRS = ["ASIA->EU", "LATE->ASIA", "EU->OVERLAP"]
H = 3600_000
PIVK = 2; KCONF = 10; LOOKBACK = 32; CAP = 60


def _eid(sym, ts):
    return "pf_" + hashlib.blake2b(f"{sym}|{ts}".encode(), digest_size=9).hexdigest()


def _run_end(seq, i, n):
    j = i
    while j + 1 < n and seq[j + 1] == seq[i]:
        j += 1
    return j


def _run_start(seq, i):
    j = i
    while j - 1 >= 0 and seq[j - 1] == seq[i]:
        j -= 1
    return j


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=Path(".output/results/session_break/prev_vol.parquet"))
    ap.add_argument("--n", type=int, default=350)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out-dir", type=Path, default=Path(".output/results/session_break/pump_fade_review"))
    args = ap.parse_args()
    t = pd.read_parquet(args.events)
    d = t[t.start_ts < DEV_END]
    fade = d[d.pair.isin(FADE_PAIRS)]
    base = fade[(fade.vol_z >= fade.vol_z.quantile(0.9)) & (fade.p_up == 1)]
    champ = base[(base.p_close_pos > 0.6) & (base.pump_new_high == 1) & (base.idio_ret > base.idio_ret.median())]
    champ = champ.sample(n=min(args.n, len(champ)), random_state=args.seed)
    print(f"sampling {len(champ)} champion pump-fade setups")

    rows = []; skipped = 0
    for sym, g in champ.groupby("symbol"):
        p = CACHE / f"{sym}.parquet"
        if not p.exists():
            skipped += len(g); continue
        df = resample_ohlcv_np(load_ohlcv_parquet(p), 15)
        ts = df["timestamp"]; o = df["open"]; h = df["high"]; l = df["low"]; c = df["close"]; n = len(ts)
        atr = causal_atr(high=h, low=l, close=c, window=ATR_WINDOW)
        seq = block_seq_for_ms(ts); last_pl, last_ph = causal_pivots(h, l, PIVK)
        for r in g.itertuples():
            e = int(np.searchsorted(ts, int(r.start_ts)))
            if e >= n or int(ts[e]) != int(r.start_ts) or e < LOOKBACK or e + CAP + 2 >= n:
                skipped += 1; continue
            run_hi = h[e]; ent = None
            for j in range(e + 1, min(e + KCONF, n)):
                run_hi = max(run_hi, h[j])
                if np.isfinite(last_pl[j]) and c[j] < last_pl[j]:
                    ent = j; break
            if ent is None:
                skipped += 1; continue
            entry = float(c[ent]); a = atr[ent] if atr[ent] > 0 else entry * 0.005
            swing_lo = float(last_pl[ent]); sh = last_ph[ent]
            stop = float((sh if (np.isfinite(sh) and sh > entry) else run_hi) + 0.3 * a)
            pump_hi = float(h[e - LOOKBACK:e].max())
            s1 = _run_end(seq, e, n); s2 = _run_end(seq, min(s1 + 1, n - 1), n); we = min(s2, ent + CAP)
            anom_s = _run_start(seq, e - 1); anom_e = e - 1                 # pump (anomaly) session
            entry_s = e; entry_e = s1                                       # entry session
            exit_bar = we; exit_px = float(c[we]); reason = "hold2"
            for j in range(ent + 1, we + 1):
                if h[j] >= stop:
                    exit_bar = j; exit_px = stop; reason = "stop"; break
            net = -(exit_px / entry - 1)
            rows.append({
                "candidate_schema_version": ANNOTATION_CANDIDATE_SCHEMA_VERSION,
                "event_id": _eid(r.symbol, r.start_ts), "symbol": r.symbol, "tf": "15m",
                "review_start_ms": int(ts[e] - 24 * H), "review_end_ms": int(ts[exit_bar] + 24 * H),
                "anchor_time_ms": int(ts[ent]),
                "suggested_level": pump_hi, "suggested_level_start_ms": int(ts[e - LOOKBACK]),
                "suggested_level_end_ms": int(ts[ent]),                 # pump high = anomaly resistance / scale-in add
                "range_low": swing_lo,                                  # broken swing low (structure)
                "poke_high": stop,                                      # structural stop (swing high)
                "entry_ts": int(ts[ent]), "entry_px": entry,
                "exit_ts": int(ts[exit_bar]), "exit_px": exit_px,
                "exit_reason": "win" if net > 0 else ("stop" if reason == "stop" else "loss"),
                "show_anomaly_fade_metrics": 1,
                # highlighted setup sessions (anomaly pump session + entry session)
                "hl_start_ms": [int(ts[anom_s]), int(ts[entry_s])],
                "hl_end_ms": [int(ts[anom_e]) + 15 * 60000, int(ts[entry_e]) + 15 * 60000],
                "hl_kind": ["anomaly", "entry"],
                "vol_z": float(r.vol_z), "idio_ret": float(r.idio_ret), "p_close_pos": float(r.p_close_pos),
                "p_ret": float(r.p_ret), "reltc_z": float(getattr(r, "reltc_z", np.nan)), "pair": r.pair,
                "outcome_win": int(net > 0),
            })
    frame = pd.DataFrame(rows)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "candidates.parquet").unlink(missing_ok=True)
    frame.to_parquet(args.out_dir / "candidates.parquet", index=False)
    if not (args.out_dir / "review_comments.jsonl").exists():
        (args.out_dir / "review_comments.jsonl").write_text("")
    mix = frame.exit_reason.value_counts().to_dict() if len(frame) else {}
    print(f"wrote {len(frame):,} S4 pump-fade candidates (skipped {skipped}) -> {args.out_dir/'candidates.parquet'}  {mix}")


if __name__ == "__main__":
    main()
