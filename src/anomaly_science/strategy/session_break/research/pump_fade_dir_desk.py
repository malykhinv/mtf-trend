"""Emit the dual-direction RR=1 pump setups to the S4 desk: ASIA/EU -> SHORT (fade),
offhours/US -> LONG (continuation, pullback entry). Draws pump peak + base, structure
entry, RR=1 target/stop (+-0.5*pump), realised exit + outcome, sessions highlighted.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.annotation.ohlcv import load_ohlcv_parquet, resample_ohlcv_np
from anomaly_science.annotation.schemas import ANNOTATION_CANDIDATE_SCHEMA_VERSION
from anomaly_science.strategy.session_break.research.pump_fade_swing import causal_pivots
from anomaly_science.strategy.session_break.research.sessions import block_seq_for_ms

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
STATES = Path(".output/results/pump_fade_lifecycle_v3/online_states.parquet")
DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
H = 3600_000; PIVK = 2; KCONF = 12; PEAK_WIN = 8; HORIZON = 48; REWARD_MIN = 0.03
SHORT_SESS = {"asia", "eu"}; LONG_SESS = {"offhours", "us"}


def _eid(sym, ts):
    return "pfd_" + hashlib.blake2b(f"{sym}|{ts}".encode(), digest_size=9).hexdigest()


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
    ap.add_argument("--n", type=int, default=320)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out-dir", type=Path, default=Path(".output/results/session_break/pump_fade_review"))
    args = ap.parse_args()
    # use the MECHANISM's pump boundaries: the peak decision (max anchor_high) gives the
    # true pump peak + base_level + pump_size; event_peak_time_ms locates it in time.
    st = pd.read_parquet(STATES, columns=["event_id", "symbol", "ignition_time_ms", "base_level",
                                          "session", "anchor_high", "pump_size"])
    lb = pd.read_parquet(str(STATES).replace(".parquet", ".labels.parquet"),
                         columns=["event_id", "event_peak_time_ms", "event_end_time_ms"]).drop_duplicates("event_id")
    st = st.loc[st.groupby("event_id").anchor_high.idxmax()].merge(lb, on="event_id", how="left")
    st = st[(st.ignition_time_ms < DEV_END) & st.session.isin(SHORT_SESS | LONG_SESS) & (st.pump_size >= 2 * REWARD_MIN)]
    st = st.sample(n=min(args.n * 3, len(st)), random_state=args.seed)   # oversample; many won't qualify

    rows = []
    for sym, g in st.groupby("symbol"):
        p = CACHE / f"{sym}.parquet"
        if not p.exists():
            continue
        df = resample_ohlcv_np(load_ohlcv_parquet(p), 15)
        ts = df["timestamp"]; h = df["high"]; l = df["low"]; c = df["close"]; n = len(ts)
        seq = block_seq_for_ms(ts); last_pl, last_ph = causal_pivots(h, l, PIVK)
        for r in g.itertuples():
            ign = int(r.ignition_time_ms); base = float(r.base_level); peak = float(r.anchor_high)
            pump = float(r.pump_size); reward = 0.5 * pump
            if reward < REWARD_MIN or not (peak > base > 0):
                continue
            # peak bar = the mechanism's event_peak_time_ms (fallback: ignition bar)
            pk_ms = int(r.event_peak_time_ms) if np.isfinite(r.event_peak_time_ms) else ign
            pk_bar = int(np.searchsorted(ts, pk_ms, side="right")) - 1
            bi = int(np.searchsorted(ts, ign, side="right")) - 1
            if bi < 40 or pk_bar <= bi or pk_bar + HORIZON + 4 >= n:
                continue
            is_long = r.session in LONG_SESS
            ent = None
            if not is_long:                              # SHORT: break of swing low
                for j in range(pk_bar + 1, min(pk_bar + KCONF, n)):
                    if np.isfinite(last_pl[j]) and c[j] < last_pl[j]:
                        ent = j; break
            else:                                        # LONG: pullback then reclaim
                plb = None
                for j in range(pk_bar + 1, min(pk_bar + 2 * KCONF, n)):
                    if plb is None:
                        if np.isfinite(last_pl[j]) and (j - pk_bar) >= PIVK and last_pl[j] > base:
                            plb = j
                    elif np.isfinite(last_ph[j]) and c[j] > last_ph[j]:
                        ent = j; break
            if ent is None or ent + HORIZON + 2 >= n:
                continue
            entry = float(c[ent])
            tgt = entry * (1 + reward) if is_long else entry * (1 - reward)
            stp = entry * (1 - reward) if is_long else entry * (1 + reward)
            we = min(ent + HORIZON, n); exit_bar = we; exit_px = float(c[we]); win = None
            for j in range(ent + 1, we):
                if is_long:
                    if l[j] <= stp: exit_bar, exit_px, win = j, stp, 0; break
                    if h[j] >= tgt: exit_bar, exit_px, win = j, tgt, 1; break
                else:
                    if h[j] >= stp: exit_bar, exit_px, win = j, stp, 0; break
                    if l[j] <= tgt: exit_bar, exit_px, win = j, tgt, 1; break
            if win is None:
                win = int((c[we] < entry) if not is_long else (c[we] > entry))
            anom_s = _rs(seq, bi); anom_e = _re(seq, bi, n); ent_s = ent; ent_e = _re(seq, ent, n)
            rows.append({
                "candidate_schema_version": ANNOTATION_CANDIDATE_SCHEMA_VERSION,
                "event_id": _eid(sym, ts[ent]), "symbol": sym, "tf": "15m",
                "review_start_ms": int(ts[max(0, anom_s)] - 18 * H), "review_end_ms": int(ts[exit_bar] + 18 * H),
                "anchor_time_ms": int(ts[ent]),
                "suggested_level": peak, "suggested_level_start_ms": int(ts[bi]), "suggested_level_end_ms": int(ts[ent]),
                "range_low": base, "range_mid": float(tgt), "poke_high": float(stp),
                "entry_ts": int(ts[ent]), "entry_px": entry, "exit_ts": int(ts[exit_bar]), "exit_px": float(exit_px),
                "exit_reason": "win" if win else "loss",
                "show_anomaly_fade_metrics": 1, "pair": f"{r.session}·{'LONG' if is_long else 'SHORT'}",
                "vol_z": np.nan, "p_ret": float(pump), "p_close_pos": np.nan, "idio_ret": np.nan,
                "outcome_win": int(win), "direction": 1 if is_long else -1,
                "hl_start_ms": [int(ts[anom_s]), int(ts[ent_s])],
                "hl_end_ms": [int(ts[anom_e]) + 15 * 60000, int(ts[ent_e]) + 15 * 60000],
                "hl_kind": ["anomaly", "entry"],
            })
    frame = pd.DataFrame(rows).drop_duplicates("event_id").reset_index(drop=True)
    if len(frame) > args.n:
        frame = frame.sample(n=args.n, random_state=1).reset_index(drop=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "candidates.parquet").unlink(missing_ok=True)
    frame.to_parquet(args.out_dir / "candidates.parquet", index=False)
    if not (args.out_dir / "review_comments.jsonl").exists():
        (args.out_dir / "review_comments.jsonl").write_text("")
    mix = frame.groupby("pair").outcome_win.agg(["size", "mean"]).round(2).to_dict("index") if len(frame) else {}
    print(f"wrote {len(frame):,} dual-direction candidates: {mix}")


if __name__ == "__main__":
    main()
