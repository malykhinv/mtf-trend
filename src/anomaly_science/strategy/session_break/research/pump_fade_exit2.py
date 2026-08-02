"""Remaining exit/entry ideas on the swing-BOS fade short (champion):
  hold1   : exit at the end of session 1 (the session after the anomaly)
  hold2   : exit at the end of session 2
  partial : take 0.5 at end of session 1, rest to end of session 2
  scalein : 0.5 at the structure break, add 0.5 if price RETESTS the pump high, exit both
All keep the structural stop (above the last swing high). Risk-normalised R (floor 4%).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.annotation.ohlcv import load_ohlcv_parquet, resample_ohlcv_np
from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.session_break.research.events import ATR_WINDOW
from anomaly_science.strategy.session_break.research.pump_fade_swing import causal_pivots
from anomaly_science.strategy.session_break.research.sessions import block_seq_for_ms

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
FADE_PAIRS = ["ASIA->EU", "LATE->ASIA", "EU->OVERLAP"]
COST = 0.0008
PIVK = 2; KCONF = 10; LOOKBACK = 32; FLOOR = 0.04; CAP = 60


def run_end(seq, i, n):
    j = i
    while j + 1 < n and seq[j + 1] == seq[i]:
        j += 1
    return j


def build(champ):
    rows = []
    for sym, g in champ.groupby("symbol"):
        p = CACHE / f"{sym}.parquet"
        if not p.exists():
            continue
        df = resample_ohlcv_np(load_ohlcv_parquet(p), 15)
        ts = df["timestamp"]; o = df["open"]; h = df["high"]; l = df["low"]; c = df["close"]; n = len(ts)
        atr = causal_atr(high=h, low=l, close=c, window=ATR_WINDOW)
        seq = block_seq_for_ms(ts); last_pl, last_ph = causal_pivots(h, l, PIVK)
        for r in g.itertuples():
            e = int(np.searchsorted(ts, int(r.start_ts)))
            if e >= n or int(ts[e]) != int(r.start_ts) or e < LOOKBACK or e + CAP + 2 >= n:
                continue
            run_hi = h[e]; ent = None
            for j in range(e + 1, min(e + KCONF, n)):
                run_hi = max(run_hi, h[j])
                if np.isfinite(last_pl[j]) and c[j] < last_pl[j]:
                    ent = j; break
            if ent is None:
                continue
            entry = c[ent]; a = atr[ent] if atr[ent] > 0 else entry * 0.005
            sh = last_ph[ent]; stop = (sh if (np.isfinite(sh) and sh > entry) else run_hi) + 0.3 * a
            sd = (stop - entry) / entry
            pump_hi = float(h[e - LOOKBACK:e].max())
            s1_end = run_end(seq, e, n)                    # end of session 1
            s2_end = run_end(seq, min(s1_end + 1, n - 1), n)  # end of session 2
            row = {"symbol": sym, "week": r.week, "sd": sd, "exit_ts": int(ts[min(run_end(seq, min(run_end(seq, e, n) + 1, n - 1), n), ent + CAP)])}

            def short_to(exit_bar, half=1.0, stop_local=stop):
                for j in range(ent + 1, exit_bar + 1):
                    if h[j] >= stop_local:
                        return -(stop_local / entry - 1) - COST
                return -(c[exit_bar] / entry - 1) - COST

            row["hold1"] = short_to(min(s1_end, ent + CAP)) / max(sd, FLOOR)
            row["hold2"] = short_to(min(s2_end, ent + CAP)) / max(sd, FLOOR)
            # partial: 0.5 at end of session 1, 0.5 at end of session 2
            p1 = short_to(min(s1_end, ent + CAP)); p2 = short_to(min(s2_end, ent + CAP))
            row["partial"] = (0.5 * p1 + 0.5 * p2) / max(sd, FLOOR)
            # scale-in: 0.5 at the BOS entry (struct stop), add 0.5 if price RETESTS the pump
            # high; after the add the combined stop sits ABOVE the pump high (protects both).
            we2 = min(s2_end, ent + CAP)
            added = False; cstop = stop; exit_px = None
            pump_stop = pump_hi + 0.5 * a
            for j in range(ent + 1, we2 + 1):
                if not added and h[j] >= pump_hi:
                    added = True; cstop = pump_stop
                if h[j] >= cstop:
                    exit_px = cstop; break
            if exit_px is None:
                exit_px = c[we2]
            if added:
                pnl = 0.5 * (-(exit_px / entry - 1)) + 0.5 * (-(exit_px / pump_hi - 1)) - COST
                risk = 0.5 * (pump_stop / entry - 1) + 0.5 * (pump_stop / pump_hi - 1)   # worst-case loss/unit
                row["scalein"] = pnl / max(risk, FLOOR)
            else:
                row["scalein"] = row["hold2"]               # no retest -> plain 0.5-size hold2 (same R)
            rows.append(row)
    return pd.DataFrame(rows)


def prof(R, wk, label):
    R = np.asarray(R); R = R[np.isfinite(R)]
    if len(R) < 80:
        print(f"  {label:<12} n={len(R)} (too few)"); return
    srt = np.sort(R)[::-1]; run = R.sum(); k = 0
    for v in srt:
        run -= v; k += 1
        if run <= 0:
            break
    w = pd.DataFrame({"w": wk[:len(R)], "R": R}).groupby("w").R.sum()
    pf = R[R > 0].sum() / (-R[R < 0].sum() + 1e-9)
    print(f"  {label:<12} n={len(R):>5} win={(R>0).mean()*100:>4.1f}% exp={R.mean():>+6.3f}R "
          f"PF={pf:>4.2f} top%toNeg={k/len(R)*100:>4.1f}% posWk={(w>0).mean()*100:>3.0f}%")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=Path(".output/results/session_break/prev_vol.parquet"))
    args = ap.parse_args()
    t = pd.read_parquet(args.events)
    d = t[t.start_ts < DEV_END]
    fade = d[d.pair.isin(FADE_PAIRS)]
    base = fade[(fade.vol_z >= fade.vol_z.quantile(0.9)) & (fade.p_up == 1)]
    champ = base[(base.p_close_pos > 0.6) & (base.pump_new_high == 1) & (base.idio_ret > base.idio_ret.median())]
    print(f"champion events: {len(champ):,}  (building exit variants...)")
    tr = build(champ)
    print(f"trades: {len(tr):,}\n=== exit / entry variants (risk-normalised R) ===")
    for v in ["hold1", "hold2", "partial", "scalein"]:
        prof(tr[v].to_numpy(), tr.week.to_numpy(), v)

    # sequencing / equity stress on the scale-in base
    s = tr.dropna(subset=["scalein"]).sort_values("exit_ts")
    R = s.scalein.to_numpy()

    def path(Rseq, risk):
        eq = 1.0; peak = 1.0; dd = 0.0
        for r in Rseq:
            eq *= (1 + risk * r); peak = max(peak, eq); dd = max(dd, (peak - eq) / peak)
        return eq - 1, dd
    print("\n=== scale-in base: equity + sequencing stress ===")
    Radv = np.sort(R)
    for risk in (0.01, 0.02):
        rf, rdd = path(R, risk); af, add = path(Radv, risk)
        print(f"  risk {risk*100:.0f}%: real final={rf*100:+.0f}% maxDD={-rdd*100:.0f}%  |  ADVERSE(win-last) maxDD={-add*100:.0f}%")
    rng = np.random.default_rng(0); dds = [path(rng.permutation(R), 0.01)[1] for _ in range(1500)]
    print(f"  random-order maxDD @1%: median={-np.median(dds)*100:.0f}%  95th={-np.percentile(dds,95)*100:.0f}%")


if __name__ == "__main__":
    main()
