"""SHORT-only pump-fade with a CONFIRMATION entry instead of a blind open. In the
first K bars of the session after the pump we wait for the buyer aggression to
exhaust and/or price to turn structurally down, then short with a TIGHT stop above
the local high. If no confirmation fires -> no trade (filters out the ones that keep
pumping = the losers). Then we discriminate winners vs losers ONLINE from entry-time
features and check whether taking the confident half gets win>0.5 AND stops being
tail-dependent (removing the top 0.5% must not flip it negative).

Triggers (within first K=8 bars):
  struct : a bar breaks below the first bar's low (structural down)
  aggr   : cumulative taker delta (2*taker_buy - qv) turns negative (buyers exhausted)
  both   : structural down AND aggression fading
Stop = running session high at entry + 0.5 ATR (tight). Exit = hold ~2 sessions.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

from anomaly_science.annotation.ohlcv import load_ohlcv_parquet, resample_ohlcv_np
from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.session_break.research.events import ATR_WINDOW

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
FADE_PAIRS = ["ASIA->EU", "LATE->ASIA", "EU->OVERLAP"]
COST = 0.0008
KCONF = 8       # confirmation window (bars)
MAXBARS = 40    # hold from entry
LOOKBACK = 32
FEATS = ["conf_bar", "runup_atr", "taker_first", "cd_at_entry", "red_frac", "entry_vs_open",
         "vol_z", "p_ret", "p_close_pos", "idio_ret", "reltc_z", "atr_pct_entry", "stop_dist"]


def sim_short_from(o, h, l, c, atr, ent, we, stop):
    entry = o[ent] if False else c[ent]      # enter at the confirmation bar close
    a = atr[ent] if atr[ent] > 0 else entry * 0.005
    sd = (stop - entry) / entry
    if sd <= 0:
        return None
    for j in range(ent + 1, we):
        if h[j] >= stop:
            return (-(stop / entry - 1) - COST), sd, "stop"
    return (-(c[we - 1] / entry - 1) - COST), sd, "time"


def build(champ, trigger):
    rows = []
    for sym, g in champ.groupby("symbol"):
        p = CACHE / f"{sym}.parquet"
        if not p.exists():
            continue
        df = resample_ohlcv_np(load_ohlcv_parquet(p), 15)
        ts = df["timestamp"]; o = df["open"]; h = df["high"]; l = df["low"]; c = df["close"]
        qv = df["quote_volume"]; tk = df.get("taker_buy_quote_volume"); n = len(ts)
        atr = causal_atr(high=h, low=l, close=c, window=ATR_WINDOW)
        for r in g.itertuples():
            e = int(np.searchsorted(ts, int(r.start_ts)))
            if e >= n or int(ts[e]) != int(r.start_ts) or e < LOOKBACK or e + MAXBARS + 2 >= n:
                continue
            first_low = l[e]; run_hi = h[e]; cd = 0.0; ent = None; nred = 0
            taker_first = (tk[e] / (qv[e] + 1e-9)) if tk is not None else np.nan
            for j in range(e, min(e + KCONF, n)):
                run_hi = max(run_hi, h[j])
                cd += (2 * (tk[j] if tk is not None else qv[j] * 0.5) - qv[j])
                if c[j] < o[j]:
                    nred += 1
                struct = l[j] < first_low and j > e
                aggr = cd < 0 and j > e
                ok = {"struct": struct, "aggr": aggr, "both": struct and aggr, "blind": j == e}[trigger]
                if ok:
                    ent = j; break
            if ent is None:
                continue
            stop = run_hi + 0.5 * atr[ent]
            res = sim_short_from(o, h, l, c, atr, ent, min(ent + MAXBARS, n), stop)
            if res is None:
                continue
            net, sd, _ = res
            rows.append({"symbol": sym, "week": r.week, "R": net / sd, "net": net,
                         "conf_bar": ent - e, "runup_atr": (run_hi - o[e]) / (atr[e] + 1e-9),
                         "taker_first": taker_first, "cd_at_entry": cd / (qv[e] * (ent - e + 1) + 1e-9),
                         "red_frac": nred / (ent - e + 1), "entry_vs_open": (c[ent] - o[e]) / o[e],
                         "vol_z": r.vol_z, "p_ret": r.p_ret, "p_close_pos": r.p_close_pos,
                         "idio_ret": r.idio_ret, "reltc_z": r.reltc_z,
                         "atr_pct_entry": atr[ent] / c[ent], "stop_dist": sd})
    return pd.DataFrame(rows)


def profile(tr, label):
    if len(tr) < 100:
        print(f"  {label:<16} n={len(tr)} (too few)"); return
    R = tr.R.to_numpy(); win = (R > 0).mean()
    srt = np.sort(R)[::-1]; run = R.sum(); k = 0
    for v in srt:
        run -= v; k += 1
        if run <= 0:
            break
    wk = tr.groupby("week").R.sum()
    pf = R[R > 0].sum() / (-R[R < 0].sum() + 1e-9)
    print(f"  {label:<16} n={len(tr):>5} win={win*100:>4.1f}% exp={R.mean():>+6.3f}R med={np.median(R):>+5.2f} "
          f"PF={pf:>4.2f} top%toNeg={k/len(R)*100:>4.1f}% posWk={(wk>0).mean()*100:>3.0f}%")


def oof(tr):
    X = tr[FEATS].to_numpy(float); y = (tr.R > 0).astype(int).to_numpy(); wk = tr.week.to_numpy()
    med = np.nanmedian(X, 0); ix = np.where(~np.isfinite(X)); X[ix] = np.take(med, ix[1])
    o = np.full(len(y), np.nan)
    for a, b in GroupKFold(5).split(X, y, wk):
        if len(np.unique(y[a])) < 2:
            continue
        m = CatBoostClassifier(depth=4, iterations=350, learning_rate=0.03, l2_leaf_reg=6, verbose=False, random_seed=0)
        m.fit(X[a], y[a]); o[b] = m.predict_proba(X[b])[:, 1]
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
    print(f"champion events: {len(champ):,}\n")
    print("=== ENTRY TRIGGER PROFILES (hold ~2 sessions, tight stop above local high) ===")
    built = {}
    for trig in ["blind", "struct", "aggr", "both"]:
        tr = build(champ, trig); built[trig] = tr
        profile(tr, trig)

    for trig in ["struct", "both"]:
        tr = built[trig]
        if len(tr) < 300:
            continue
        tr = tr.copy(); tr["p"] = oof(tr)
        m = np.isfinite(tr.p)
        auc = roc_auc_score((tr.R > 0).astype(int)[m], tr.p[m])
        print(f"\n=== ONLINE win/loss discrimination on '{trig}' entries: AUC={auc:.3f} ===")
        top = tr[tr.p >= tr.p.quantile(0.5)]      # take the confident half
        profile(top, f"{trig}+model top50%")
        top30 = tr[tr.p >= tr.p.quantile(0.7)]
        profile(top30, f"{trig}+model top30%")
        imp = pd.Series(CatBoostClassifier(depth=4, iterations=350, learning_rate=0.03, verbose=False, random_seed=0)
                        .fit(np.nan_to_num(tr[FEATS].to_numpy(float)), (tr.R > 0).astype(int)).get_feature_importance(),
                        index=FEATS).sort_values(ascending=False)
        print("   top features:", ", ".join(f"{k}={v:.0f}" for k, v in imp.head(7).items()))


if __name__ == "__main__":
    main()
