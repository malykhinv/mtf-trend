"""(3) RISK-NORMALISED view of the swing-BOS pump-fade short: every trade risks a
FIXED 1% of equity (size ~ 1/stop_distance), so high-ATR coins no longer dominate.
We sweep a stop-distance FLOOR (caps leverage on tiny structural stops) and, for each,
report expectancy, profit concentration (remove top-X% to go negative + top-decile
share), a compounded equity curve (final %, max drawdown), and weekly stability.
Answers: does risk-normalisation flatten the tail-concentration to something tradeable?
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

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
FADE_PAIRS = ["ASIA->EU", "LATE->ASIA", "EU->OVERLAP"]
COST = 0.0008
PIVK = 2; KCONF = 10; MAXBARS = 40; LOOKBACK = 32; TARGET_ATR = 2.5
FLOORS = [0.004, 0.008, 0.015, 0.025, 0.04]
RISK = 0.01   # 1% of equity risked per trade


def build(champ):
    rows = []
    for sym, g in champ.groupby("symbol"):
        p = CACHE / f"{sym}.parquet"
        if not p.exists():
            continue
        df = resample_ohlcv_np(load_ohlcv_parquet(p), 15)
        ts = df["timestamp"]; o = df["open"]; h = df["high"]; l = df["low"]; c = df["close"]; n = len(ts)
        atr = causal_atr(high=h, low=l, close=c, window=ATR_WINDOW)
        last_pl, last_ph = causal_pivots(h, l, PIVK)
        for r in g.itertuples():
            e = int(np.searchsorted(ts, int(r.start_ts)))
            if e >= n or int(ts[e]) != int(r.start_ts) or e < LOOKBACK or e + MAXBARS + 2 >= n:
                continue
            run_hi = h[e]; ent = None
            for j in range(e + 1, min(e + KCONF, n)):
                run_hi = max(run_hi, h[j])
                if np.isfinite(last_pl[j]) and c[j] < last_pl[j]:
                    ent = j; break
            if ent is None:
                continue
            entry = c[ent]; a = atr[ent] if atr[ent] > 0 else entry * 0.005
            sh = last_ph[ent]
            stop = (sh if (np.isfinite(sh) and sh > entry) else run_hi) + 0.3 * a
            sd = (stop - entry) / entry
            tgt = entry - TARGET_ATR * a
            we = min(ent + MAXBARS, n); res = None
            for j in range(ent + 1, we):
                if h[j] >= stop:
                    res = -(stop / entry - 1) - COST; break
                if l[j] <= tgt:
                    res = -(tgt / entry - 1) - COST; break
            if res is None:
                res = -(c[we - 1] / entry - 1) - COST
            rows.append({"symbol": sym, "week": r.week, "exit_ts": int(ts[min(ent + MAXBARS, n - 1)]),
                         "net_pct": res, "sd": sd})
    return pd.DataFrame(rows).sort_values("exit_ts").reset_index(drop=True)


def analyse(tr, floor):
    sd = np.maximum(tr.sd.to_numpy(), floor)
    R = tr.net_pct.to_numpy() / sd                 # risk units (1R = risk% of equity)
    # concentration
    srt = np.sort(R)[::-1]; run = R.sum(); k = 0
    for v in srt:
        run -= v; k += 1
        if run <= 0:
            break
    top10 = srt[:max(1, len(R) // 10)][srt[:max(1, len(R) // 10)] > 0].sum() / R[R > 0].sum() * 100
    # compounded equity at fixed RISK per trade (sequential by exit)
    eq = 1.0; peak = 1.0; dd = 0.0
    for r in R:
        eq *= (1 + RISK * r); peak = max(peak, eq); dd = max(dd, (peak - eq) / peak)
    wk = pd.DataFrame({"w": tr.week, "R": R}).groupby("w").R.sum()
    print(f"  floor {floor*100:>4.1f}%: exp={R.mean():>+6.3f}R  PF={R[R>0].sum()/(-R[R<0].sum()+1e-9):.2f}  "
          f"top%toNeg={k/len(R)*100:>4.1f}%  top10%share={top10:>3.0f}%  "
          f"final={ (eq-1)*100:>+7.0f}%  maxDD={-dd*100:>5.0f}%  posWk={(wk>0).mean()*100:>3.0f}%")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=Path(".output/results/session_break/prev_vol.parquet"))
    args = ap.parse_args()
    t = pd.read_parquet(args.events)
    d = t[t.start_ts < DEV_END]
    fade = d[d.pair.isin(FADE_PAIRS)]
    base = fade[(fade.vol_z >= fade.vol_z.quantile(0.9)) & (fade.p_up == 1)]
    champ = base[(base.p_close_pos > 0.6) & (base.pump_new_high == 1) & (base.idio_ret > base.idio_ret.median())]
    print(f"champion events: {len(champ):,}  (building...)")
    tr = build(champ)
    print(f"trades: {len(tr):,}  win={ (tr.net_pct>0).mean()*100:.1f}%  "
          f"median stop_dist={tr.sd.median()*100:.2f}%\n")
    print(f"=== RISK-NORMALISED (1% risk/trade), stop-distance floor sweep ===")
    for f in FLOORS:
        analyse(tr, f)
    print("\n(top%toNeg = % of top trades to remove before total<0; higher=less tail-dependent.")
    print(" top10%share = share of gross profit from the best 10% of trades; lower=broader.)")


if __name__ == "__main__":
    main()
