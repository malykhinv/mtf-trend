"""RR=1 fade race on the MATURE anomaly events (pump_fade_lifecycle_v3). After the
pump, wait for a structure break down, then run a symmetric race: target = entry -
0.5*pump_size, stop = entry + 0.5*pump_size (RR=1), only if the reward (0.5*pump_size)
> 3%. Because target and stop are equal, the edge is pure WIN RATE -- no fat tail. We
label win = target-first and train a model on the mature features to maximise the AUC
of that win, then read win rate by session and in the confident decile.
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
from anomaly_science.strategy.session_break.research.pump_fade_swing import causal_pivots

CACHE = Path(".output/market/binance_vision/um_futures/enriched_1m")
STATES = Path(".output/results/pump_fade_lifecycle_v3/online_states.parquet")
DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
PIVK = 2; KCONF = 12; PEAK_WIN = 8; HORIZON = 48; REWARD_MIN = 0.03; COST = 0.0008

FEATS = ["pump_size", "verticality", "max_candle_share", "event_path_efficiency", "rehigh_count",
         "latest_high_extension", "high_extension_decay_ratio", "ignition_upper_wick_fraction",
         "mean_upper_wick_fraction", "green_candle_fraction", "turnover_top_candle_share",
         "taker_buy_share_event", "taker_imbalance_event", "cvd_imbalance_5m", "cvd_drawdown_from_peak",
         "cvd_failed_to_confirm_high", "atr_mult", "base_broken_before", "frac_prior_faded_48h",
         "last_prior_faded", "n_prior_48h", "price_vs_ema_240", "pre_return_60m", "pre_dump_depth_240m",
         "ignition_hour_utc", "remaining_to_base", "pump_elapsed_min", "pump_ncandles",
         "close_drawdown_from_high", "recent_red_fraction_5m", "bos_bars", "entry_vs_peak", "reward_frac"]


def build_symbol(sym, ev, feat_cols):
    p = CACHE / f"{sym}.parquet"
    if not p.exists():
        return []
    df = resample_ohlcv_np(load_ohlcv_parquet(p), 15)
    ts = df["timestamp"]; o = df["open"]; h = df["high"]; l = df["low"]; c = df["close"]; n = len(ts)
    last_pl, last_ph = causal_pivots(h, l, PIVK)
    out = []
    for r in ev.itertuples():
        ign = int(r.ignition_time_ms); base = float(r.base_level)
        bi = int(np.searchsorted(ts, ign, side="right")) - 1
        if bi < 40 or bi + HORIZON + 4 >= n:
            continue
        peak = float(h[bi:min(bi + PEAK_WIN, n)].max())
        pk_bar = bi + int(np.argmax(h[bi:min(bi + PEAK_WIN, n)]))
        pump = (peak - base) / base if base > 0 else 0.0
        reward = 0.5 * pump
        if reward < REWARD_MIN or pump <= 0:
            continue
        # structure break DOWN after the peak
        ent = None
        for j in range(pk_bar + 1, min(pk_bar + KCONF, n)):
            if np.isfinite(last_pl[j]) and c[j] < last_pl[j]:
                ent = j; break
        if ent is None or ent + HORIZON + 2 >= n:
            continue
        entry = float(c[ent])
        tgt = entry * (1 - reward); stp = entry * (1 + reward)
        we = min(ent + HORIZON, n); res = None
        for j in range(ent + 1, we):
            hit_lo = l[j] <= tgt; hit_hi = h[j] >= stp
            if hit_hi:                                   # adverse first (pessimistic)
                res = 0; break
            if hit_lo:
                res = 1; break
        if res is None:
            continue                                     # unresolved in horizon -> drop
        row = {c_: getattr(r, c_, np.nan) for c_ in feat_cols}
        row.update({"symbol": sym, "entry_ts": int(ts[ent]), "session": r.session, "y": res,
                    "week": pd.Timestamp(int(ts[ent]), unit="ms", tz="UTC").strftime("%G-W%V"),
                    "bos_bars": ent - pk_bar, "entry_vs_peak": (peak - entry) / entry, "reward_frac": reward,
                    "pump_size": pump})
        out.append(row)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path(".output/results/session_break/pump_rr1.parquet"))
    args = ap.parse_args()
    st = pd.read_parquet(STATES)
    st = st.sort_values(["event_id", "snapshot_time_ms"]).drop_duplicates("event_id", keep="first")
    st = st[st.ignition_time_ms < DEV_END]
    feat_cols = [c for c in FEATS if c in st.columns and c not in ("bos_bars", "entry_vs_peak", "reward_frac")]
    rows = []
    syms = st.symbol.unique()
    for i, sym in enumerate(syms):
        rows += build_symbol(sym, st[st.symbol == sym], feat_cols)
        if (i + 1) % 150 == 0:
            print(f"  {i+1}/{len(syms)} trades={len(rows):,}")
    t = pd.DataFrame(rows)
    t.to_parquet(args.out)
    print(f"\nRR=1 races: n={len(t):,}  base win={t.y.mean():.3f}  (win>0.5 => symmetric edge)")
    print("win rate by session:")
    for s, g in t.groupby("session"):
        print(f"  {s:<9} n={len(g):>5} win={g.y.mean():.3f}")

    use = [c for c in FEATS if c in t.columns]
    X = t[use].to_numpy(float); y = t.y.to_numpy(int); wk = t.week.to_numpy()
    med = np.nanmedian(X, 0); ix = np.where(~np.isfinite(X)); X[ix] = np.take(med, ix[1])
    oof = np.full(len(y), np.nan)
    for a, b in GroupKFold(5).split(X, y, wk):
        m = CatBoostClassifier(depth=5, iterations=500, learning_rate=0.03, l2_leaf_reg=6, verbose=False, random_seed=0)
        m.fit(X[a], y[a]); oof[b] = m.predict_proba(X[b])[:, 1]
    mk = np.isfinite(oof)
    print(f"\n=== RR=1 win discrimination: week-CV AUC={roc_auc_score(y[mk], oof[mk]):.3f} (base win {y.mean():.3f}) ===")
    s = t[mk].assign(p=oof[mk])
    for q, lbl in [(0.7, "top30%"), (0.8, "top20%"), (0.9, "top10%")]:
        sel = s[s.p >= s.p.quantile(q)]
        wk2 = sel.groupby("week").y.mean()
        print(f"  {lbl}: n={len(sel):>5} win={sel.y.mean():.3f}  posWk={ (wk2>0.5).mean()*100:.0f}%")
    imp = pd.Series(CatBoostClassifier(depth=5, iterations=500, learning_rate=0.03, verbose=False, random_seed=0)
                    .fit(np.nan_to_num(t[use].to_numpy(float)), y).get_feature_importance(), index=use).sort_values(ascending=False)
    print("top features:", ", ".join(f"{k}={v:.1f}" for k, v in imp.head(12).items()))


if __name__ == "__main__":
    main()
