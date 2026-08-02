"""Struct-confirmation SHORT entry (break of the first bar's low, tight stop above
the local high) x a fixed ATR-target sweep x ONLINE ranking by EXPECTED R (regression)
rather than P(win). Goal: win>0.5, positive expectancy, and low tail-dependence at
once. Taker-aggression triggers are impossible (taker data absent), so entry is
purely structural.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, CatBoostClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

from anomaly_science.annotation.ohlcv import load_ohlcv_parquet, resample_ohlcv_np
from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.session_break.research.events import ATR_WINDOW

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
FADE_PAIRS = ["ASIA->EU", "LATE->ASIA", "EU->OVERLAP"]
COST = 0.0008
KCONF = 8
MAXBARS = 40
LOOKBACK = 32
TGTS = [1.0, 1.5, 2.0, 2.5, 3.0]
FEATS = ["conf_bar", "runup_atr", "red_frac", "entry_vs_open", "vol_z", "p_ret",
         "p_close_pos", "idio_ret", "reltc_z", "atr_pct_entry", "stop_dist"]


def build(champ):
    rows = []
    for sym, g in champ.groupby("symbol"):
        p = CACHE / f"{sym}.parquet"
        if not p.exists():
            continue
        df = resample_ohlcv_np(load_ohlcv_parquet(p), 15)
        ts = df["timestamp"]; o = df["open"]; h = df["high"]; l = df["low"]; c = df["close"]; n = len(ts)
        atr = causal_atr(high=h, low=l, close=c, window=ATR_WINDOW)
        for r in g.itertuples():
            e = int(np.searchsorted(ts, int(r.start_ts)))
            if e >= n or int(ts[e]) != int(r.start_ts) or e < LOOKBACK or e + MAXBARS + 2 >= n:
                continue
            first_low = l[e]; run_hi = h[e]; ent = None; nred = 0
            for j in range(e, min(e + KCONF, n)):
                run_hi = max(run_hi, h[j])
                if c[j] < o[j]:
                    nred += 1
                if l[j] < first_low and j > e:
                    ent = j; break
            if ent is None:
                continue
            entry = c[ent]; a = atr[ent] if atr[ent] > 0 else entry * 0.005
            stop = run_hi + 0.5 * a; sd = (stop - entry) / entry
            if sd <= 0:
                continue
            we = min(ent + MAXBARS, n)
            row = {"symbol": sym, "week": r.week, "conf_bar": ent - e,
                   "runup_atr": (run_hi - o[e]) / (atr[e] + 1e-9), "red_frac": nred / (ent - e + 1),
                   "entry_vs_open": (entry - o[e]) / o[e], "vol_z": r.vol_z, "p_ret": r.p_ret,
                   "p_close_pos": r.p_close_pos, "idio_ret": r.idio_ret, "reltc_z": r.reltc_z,
                   "atr_pct_entry": a / entry, "stop_dist": sd}
            # outcome per target (short): hit target (win) or stop (loss) or timeout
            for tg in TGTS:
                tgt = entry - tg * a; res = None
                for j in range(ent + 1, we):
                    if h[j] >= stop:
                        res = (-(stop / entry - 1) - COST); break
                    if l[j] <= tgt:
                        res = (-(tgt / entry - 1) - COST); break
                if res is None:
                    res = -(c[we - 1] / entry - 1) - COST
                row[f"R_{tg}"] = res / sd
            rows.append(row)
    return pd.DataFrame(rows)


def prof(R, wk, label):
    R = np.asarray(R)
    srt = np.sort(R)[::-1]; run = R.sum(); k = 0
    for v in srt:
        run -= v; k += 1
        if run <= 0:
            break
    wdf = pd.DataFrame({"w": wk, "R": R}).groupby("w").R.sum()
    pf = R[R > 0].sum() / (-R[R < 0].sum() + 1e-9)
    print(f"  {label:<22} n={len(R):>5} win={(R>0).mean()*100:>4.1f}% exp={R.mean():>+6.3f}R "
          f"PF={pf:>4.2f} top%toNeg={k/len(R)*100:>4.1f}% posWk={(wdf>0).mean()*100:>3.0f}%")


def oof_reg(tr, ycol):
    X = tr[FEATS].to_numpy(float); y = tr[ycol].to_numpy(float); wk = tr.week.to_numpy()
    X = np.nan_to_num(X, nan=np.nanmedian(X)); o = np.full(len(y), np.nan)
    for a, b in GroupKFold(5).split(X, y, wk):
        m = CatBoostRegressor(depth=4, iterations=350, learning_rate=0.03, l2_leaf_reg=6, verbose=False, random_seed=0)
        m.fit(X[a], y[a]); o[b] = m.predict(X[b])
    return o


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=Path(".output/results/session_break/prev_vol.parquet"))
    args = ap.parse_args()
    t = pd.read_parquet(args.events)
    d = t[t.start_ts < DEV_END]
    fade = d[d.pair.isin(FADE_PAIRS)]
    base = fade[(fade.vol_z >= fade.vol_z.quantile(0.9)) & (fade.p_up == 1)]
    champ = base[(base.p_close_pos > 0.6) & (base.pump_new_high == 1) & (base.idio_ret > base.idio_ret.median())]
    print(f"champion events: {len(champ):,}  (building struct-confirmation trades...)")
    tr = build(champ)
    print(f"struct-confirmed trades: {len(tr):,}\n")

    print("=== struct entry x ATR target (tight stop above local high) ===")
    for tg in TGTS:
        prof(tr[f"R_{tg}"].to_numpy(), tr.week.to_numpy(), f"target {tg} ATR")

    # rank by EXPECTED R (regression) vs by P(win); use the target that looked best
    for tg in [1.5, 2.0]:
        y = tr[f"R_{tg}"]
        print(f"\n=== target {tg} ATR: rank by predicted E[R] (OOF regression) ===")
        pr = oof_reg(tr, f"R_{tg}"); tr2 = tr.assign(pr=pr)
        for qq, lbl in [(0.5, "top50% by E[R]"), (0.7, "top30% by E[R]"), (0.8, "top20% by E[R]")]:
            sel = tr2[tr2.pr >= tr2.pr.quantile(qq)]
            prof(sel[f"R_{tg}"].to_numpy(), sel.week.to_numpy(), lbl)
        # compare: rank by P(win)
        yb = (y > 0).astype(int)
        Xf = np.nan_to_num(tr[FEATS].to_numpy(float)); wk = tr.week.to_numpy(); pw = np.full(len(yb), np.nan)
        for a, b in GroupKFold(5).split(Xf, yb, wk):
            if len(np.unique(yb.values[a])) < 2:
                continue
            m = CatBoostClassifier(depth=4, iterations=350, learning_rate=0.03, verbose=False, random_seed=0)
            m.fit(Xf[a], yb.values[a]); pw[b] = m.predict_proba(Xf[b])[:, 1]
        sel = tr.assign(pw=pw)
        sel = sel[sel.pw >= np.nanquantile(pw, 0.7)]
        prof(sel[f"R_{tg}"].to_numpy(), sel.week.to_numpy(), "top30% by P(win) [compare]")


if __name__ == "__main__":
    main()
