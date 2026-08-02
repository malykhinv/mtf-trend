"""HYPOTHESIS (user): the session just before the current one (e.g. ASIA before the
opening EU) had ANOMALOUS volume vs several prior sessions -> a manipulated pump that
sucked in FOMO retail -> the current session FADES (declines). A short.

We test the MECHANISM's prediction the scientific way, dose-response FIRST:
  * anomaly metric is COIN-SPECIFIC (this coin's volume vs its own recent sessions,
    and vs BTC's same session) -> already controlled for market beta;
  * bin by the anomaly strength -> mean next-session return. A real exhaustion-fade
    shows a MONOTONE negative dose-response, strongest when the prior session also
    pumped and closed near its high (the FOMO-top signature);
  * then a small pre-specified model (only the anomaly features) vs a within-week
    shuffled null and vs a market-only baseline, split by session pair.

All features come from the PRIOR session P (closed before the current session opens);
the label is the current session's open->close return. Strictly causal, in-sample.
"""

from __future__ import annotations

import argparse
import glob
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

from anomaly_science.annotation.ohlcv import load_ohlcv_parquet, resample_ohlcv_np
from anomaly_science.simulation.numpy_path import causal_atr
from anomaly_science.strategy.session_break.research.events import ATR_WINDOW
from anomaly_science.strategy.session_break.research.reclaim import _block_runs
import anomaly_science.strategy.session_break.research.session_streak as ss
from anomaly_science.strategy.session_break.research.session_streak import btc_session_index, _init_worker, _BTC
from anomaly_science.strategy.session_break.research.market_context import load_market_arrays, market_asof
from anomaly_science.strategy.session_break.research.sessions import BLOCK_BY_SEQ, block_seq_for_ms, utc_day_for_ms

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
VOL_BASE = 12   # sessions of baseline for the volume z-score


def build_symbol(path: Path, tf_min: int, btc_idx=None) -> pd.DataFrame:
    btc_idx = btc_idx if btc_idx is not None else ss._BTC   # worker global set by _init_worker (not the stale import)
    base = load_ohlcv_parquet(path)
    df = base if tf_min == 1 else resample_ohlcv_np(base, tf_min)
    ts = df["timestamp"]; o = df["open"]; h = df["high"]; lo = df["low"]; c = df["close"]
    qv = df["quote_volume"]; tc = df["trade_count"]; taker = df.get("taker_buy_quote_volume")
    n = len(ts)
    if n < 300:
        return pd.DataFrame()
    atr = causal_atr(high=h, low=lo, close=c, window=ATR_WINDOW)
    day = utc_day_for_ms(ts); seq = block_seq_for_ms(ts)
    S = []
    for d, s, st, en in _block_runs(day, seq):
        if en - st < 3:
            continue
        op = float(o[st]); cl = float(c[en - 1]); hi = float(h[st:en].max()); low = float(lo[st:en].min())
        rng = hi - low
        tkr = float(np.nanmean(taker[st:en]) / (qv[st:en].mean() + 1e-9)) if taker is not None else np.nan
        btc = btc_idx.get((int(d), int(s))) if btc_idx else None
        S.append({"seq": int(s), "st": st, "en": en, "st_ts": int(ts[st]), "end_ts": int(ts[en - 1]),
                  "open": op, "close": cl, "high": hi, "low": low, "ret": cl / op - 1.0 if op > 0 else 0.0,
                  "qv": float(qv[st:en].sum()), "tc": float(tc[st:en].sum()),
                  "logqv": float(np.log(qv[st:en].sum() + 1)),
                  "close_pos": (cl - low) / rng if rng > 0 else 0.5,
                  "body_frac": abs(cl - op) / rng if rng > 0 else 0.0,
                  "upper_wick": (hi - max(op, cl)) / rng if rng > 0 else 0.0,
                  "taker": tkr, "atr_pct": atr[st] / op if op > 0 else 0.0,
                  "rel_qv_btc": (float(qv[st:en].sum()) / btc[1]) if (btc and btc[1] > 0) else np.nan,
                  "rel_tc_btc": (float(tc[st:en].sum()) / btc[0]) if (btc and btc[0] > 0) else np.nan})
    if len(S) < VOL_BASE + 5:
        return pd.DataFrame()
    sf = pd.DataFrame(S).sort_values("st_ts").reset_index(drop=True)
    lq = sf.logqv.to_numpy(); rbtc = sf.rel_qv_btc.to_numpy(); rtcb = sf.rel_tc_btc.to_numpy()
    opens = sf.open.to_numpy(); closes = sf.close.to_numpy(); seqs = sf.seq.to_numpy(); nsf = len(sf)
    # SAME-TYPE volume z (this ASIA vs prior ASIAs) -- a cleaner anomaly than vs all sessions
    def _rz(x):
        m = x.shift(1).rolling(VOL_BASE, min_periods=VOL_BASE // 2).mean()
        s = x.shift(1).rolling(VOL_BASE, min_periods=VOL_BASE // 2).std()
        return (x - m) / (s + 1e-9)
    sf["same_vol_z"] = sf.groupby("seq").logqv.transform(_rz)
    # did the prior session make a NEW HIGH vs the recent window (climax to new high)?
    sf["prior_max_hi"] = sf.high.shift(1).rolling(VOL_BASE, min_periods=VOL_BASE // 2).max()
    out = []
    for i in range(VOL_BASE + 1, len(sf)):
        P = sf.iloc[i - 1]      # prior session (the suspected manipulation)
        Sc = sf.iloc[i]         # current session (the trade)
        win = lq[i - 1 - VOL_BASE:i - 1]
        if len(win) < VOL_BASE or win.std() == 0:
            continue
        vol_z = (lq[i - 1] - win.mean()) / (win.std() + 1e-9)
        # coin-specific relative-to-BTC volume z, and relative-to-BTC TRADE-COUNT z
        rwin = rbtc[i - 1 - VOL_BASE:i - 1]
        relbtc_z = (rbtc[i - 1] - np.nanmean(rwin)) / (np.nanstd(rwin) + 1e-9) if np.isfinite(rbtc[i - 1]) else np.nan
        rtwin = rtcb[i - 1 - VOL_BASE:i - 1]
        reltc_z = (rtcb[i - 1] - np.nanmean(rtwin)) / (np.nanstd(rtwin) + 1e-9) if np.isfinite(rtcb[i - 1]) else np.nan
        rel_tc_btc = float(rtcb[i - 1]) if np.isfinite(rtcb[i - 1]) else np.nan   # raw ratio of this coin's trades to BTC
        # multi-horizon exits from the entry (open of the session after the anomaly)
        entry = opens[i]
        ret_1 = closes[i] / entry - 1 if entry > 0 else np.nan                       # exit at next-session close (current)
        ret_2 = closes[i + 1] / entry - 1 if (i + 1 < nsf and entry > 0) else np.nan  # +2 sessions
        ret_3 = closes[i + 2] / entry - 1 if (i + 2 < nsf and entry > 0) else np.nan  # +3 sessions (~US close for ASIA)
        janom = next((jj for jj in range(i + 1, min(nsf, i + 12)) if seqs[jj] == int(P.seq)), None)
        ret_toanom = (opens[janom] / entry - 1) if (janom is not None and entry > 0) else np.nan  # exit at next same-type-as-anomaly open
        # market backdrop DURING the pump session (causal: pump closed before the trade opens)
        mk = market_asof(ss._MKT_TS, ss._MKT_VALS, int(P.end_ts)) if ss._MKT_TS is not None else {}
        mkt_ret = mk.get("mkt_alt_ret_med", np.nan); mkt_breadth = mk.get("mkt_alt_breadth", np.nan)
        idio_ret = float(P.ret - mkt_ret) if np.isfinite(mkt_ret) else np.nan   # coin's move minus the alt market
        new_high = int(P.high > P.prior_max_hi) if np.isfinite(P.prior_max_hi) else 0
        out.append({
            "symbol": path.stem, "start_ts": int(Sc.st_ts),
            "week": pd.Timestamp(Sc.st_ts, unit="ms", tz="UTC").strftime("%G-W%V"),
            "pair": f"{BLOCK_BY_SEQ[int(P.seq)].name}->{BLOCK_BY_SEQ[int(Sc.seq)].name}",
            "cur_seq": int(Sc.seq),
            # prior-session anomaly features (all causal)
            "vol_z": float(vol_z), "same_vol_z": float(P.same_vol_z) if np.isfinite(P.same_vol_z) else np.nan,
            "relbtc_z": float(relbtc_z), "reltc_z": float(reltc_z), "rel_tc_btc": rel_tc_btc,
            "p_ret": float(P.ret), "p_up": int(P.ret > 0), "p_close_pos": float(P.close_pos),
            "p_body_frac": float(P.body_frac), "p_upper_wick": float(P.upper_wick),
            "p_taker": float(P.taker), "p_atr_pct": float(P.atr_pct),
            "cap": float(np.log10(P.qv + 1)),
            # manipulation-isolation: coin pumped while the market was quiet?
            "idio_ret": idio_ret, "mkt_ret_at_pump": float(mkt_ret) if np.isfinite(mkt_ret) else np.nan,
            "mkt_breadth_at_pump": float(mkt_breadth) if np.isfinite(mkt_breadth) else np.nan,
            "pump_new_high": new_high,
            # OUTCOME: multi-horizon forward returns from the entry (open after anomaly)
            "cur_ret": float(Sc.ret), "cur_down": int(Sc.close < Sc.open),
            "ret_1": float(ret_1) if np.isfinite(ret_1) else np.nan,
            "ret_2": float(ret_2) if np.isfinite(ret_2) else np.nan,
            "ret_3": float(ret_3) if np.isfinite(ret_3) else np.nan,
            "ret_toanom": float(ret_toanom) if np.isfinite(ret_toanom) else np.nan,
        })
    return pd.DataFrame(out)


def _one(a):
    try:
        return build_symbol(Path(a[0]), a[1])
    except Exception:
        return pd.DataFrame()


def dose(d, col, label, bins=10):
    d = d[np.isfinite(d[col])]
    q = pd.qcut(d[col], bins, labels=False, duplicates="drop")
    g = d.groupby(q).agg(n=("cur_ret", "size"), mean_bp=("cur_ret", lambda x: x.mean() * 1e4),
                         down=("cur_down", "mean"), lo=(col, "min"), hi=(col, "max"))
    print(f"\n  dose-response: {label} decile -> next-session mean return")
    for i, r in g.iterrows():
        print(f"    d{int(i):>2} [{r.lo:>7.2f}..{r.hi:>7.2f}]  n={int(r.n):>6}  mean={r.mean_bp:>+7.1f}bp  down%={r.down:.3f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", type=int, default=15)
    ap.add_argument("--out", type=Path, default=Path(".output/results/session_break/prev_vol.parquet"))
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    files = sorted(glob.glob(str(CACHE / "*.parquet")))
    btc_idx = btc_session_index(args.tf)
    mkt_path = Path(f".output/results/session_break/market_index_tf{args.tf}.parquet")
    mkt_ts, mkt_vals = load_market_arrays(mkt_path) if mkt_path.exists() else (None, {})
    print(f"tf={args.tf}m symbols={len(files)} btc_sessions={len(btc_idx)} mkt_sessions={0 if mkt_ts is None else len(mkt_ts)}")
    frames = []; done = 0
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_init_worker, initargs=(btc_idx, mkt_ts, mkt_vals)) as ex:
        futs = {ex.submit(_one, (f, args.tf)): f for f in files}
        for fut in as_completed(futs):
            r = fut.result()
            if len(r):
                frames.append(r)
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(files)} events={sum(len(x) for x in frames):,}")
    t = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    t.to_parquet(args.out)
    d = t[t.start_ts < DEV_END].copy()
    print(f"\nwrote {len(t):,} events ({len(d):,} DEV) | base next-down={d.cur_down.mean():.3f} "
          f"mean cur_ret={d.cur_ret.mean()*1e4:+.1f}bp")

    # 1) raw dose-response on the volume anomaly
    dose(d, "vol_z", "volume z-score (this coin vs its recent sessions)")
    dose(d, "relbtc_z", "volume z-score vs BTC same-session")

    # 2) conditioned on the FOMO-top signature: prior session pumped (up) and closed near high
    fomo = d[(d.p_up == 1) & (d.p_close_pos > 0.66)]
    print(f"\n  === FOMO-top subset (prior up + closed top third), n={len(fomo):,} "
          f"base next-down={fomo.cur_down.mean():.3f} mean={fomo.cur_ret.mean()*1e4:+.1f}bp ===")
    dose(fomo, "vol_z", "vol_z | prior pumped & closed high")

    # 3) the full manipulation setup: top-decile vol_z + pump + close-high -> fade?
    thr = d.vol_z.quantile(0.9)
    setup = d[(d.vol_z >= thr) & (d.p_up == 1) & (d.p_close_pos > 0.66)]
    if len(setup) > 200:
        wk = setup.groupby("week").cur_ret.mean()
        print(f"\n  === FULL SETUP (vol_z top-decile + pump + close-high): n={len(setup):,} "
              f"mean cur_ret={setup.cur_ret.mean()*1e4:+.1f}bp down%={setup.cur_down.mean():.3f} "
              f"short_net@8bp={-setup.cur_ret.mean()*1e4-8:+.1f}bp  weeks_short_pos={(wk<0).mean():.2f} ===")

    # 4) by session pair (is it specific to ASIA->EU?)
    print("\n  === FULL-SETUP fade by session pair (mean cur_ret, want negative) ===")
    sp = setup.groupby("pair").agg(n=("cur_ret", "size"), mean_bp=("cur_ret", lambda x: x.mean() * 1e4),
                                   down=("cur_down", "mean")) if len(setup) > 200 else pd.DataFrame()
    for pair, r in sp[sp.n >= 60].sort_values("mean_bp").iterrows():
        print(f"    {pair:<16} n={int(r.n):>5} mean={r.mean_bp:>+7.1f}bp down%={r.down:.3f}")

    # 5) null-adjusted model: do the anomaly features predict next-down above a shuffled null?
    feats = ["vol_z", "relbtc_z", "p_ret", "p_close_pos", "p_body_frac", "p_upper_wick", "p_taker", "p_atr_pct", "cap"]
    dd = d.dropna(subset=["vol_z"]).copy()
    X = dd[feats].to_numpy(float); y = dd.cur_down.to_numpy(int); wk = dd.week.to_numpy()
    med = np.nanmedian(X, 0); ix = np.where(~np.isfinite(X)); X[ix] = np.take(med, ix[1])
    def run(yv):
        o = np.full(len(yv), np.nan)
        for tr, te in GroupKFold(5).split(X, yv, wk):
            m = CatBoostClassifier(depth=4, iterations=300, learning_rate=0.03, l2_leaf_reg=6, verbose=False, random_seed=0)
            m.fit(X[tr], yv[tr]); o[te] = m.predict_proba(X[te])[:, 1]
        return o
    p = run(y); rng = np.random.default_rng(0)
    yn = dd.groupby("week").cur_down.transform(lambda s: rng.permutation(s.values)).to_numpy(int)
    pn = run(yn)
    mk = np.isfinite(p) & np.isfinite(pn)
    print(f"\n  === anomaly-only model: real AUC={roc_auc_score(y[mk], p[mk]):.3f} "
          f"vs shuffled null={roc_auc_score(yn[mk], pn[mk]):.3f} ===")
    imp = pd.Series(CatBoostClassifier(depth=4, iterations=300, learning_rate=0.03, verbose=False, random_seed=0)
                    .fit(X, y).get_feature_importance(), index=feats).sort_values(ascending=False)
    print("  importances:", ", ".join(f"{k}={v:.1f}" for k, v in imp.items()))


if __name__ == "__main__":
    main()
