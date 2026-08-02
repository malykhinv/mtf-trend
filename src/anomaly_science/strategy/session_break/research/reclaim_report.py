"""Does the failed-break reclaim short have a NET edge, and do the quality
features (real distribution high, wick, seller aggression) select a profitable
subset? Honest: GROSS R, breakeven cost, and NET at a realistic round-trip cost.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
QUAL = ["prior_down_after", "prior_up_imp", "prior_high_pos", "prior_high_wick",
        "prior_vol_at_high", "poke_depth_atr", "poke_wick", "bars_above",
        "reclaim_taker", "reclaim_voldrop", "oi_change", "pre_rvol", "pre_rtrades", "pre_atr"]


def net(t, c):
    return (t.gross_r - c / t.risk_frac).clip(-10, 10)


def line(t, c, label):
    nr = net(t, c); wk = t.assign(x=nr).groupby("week")["x"].sum()
    cstar = t.gross_r.mean() / (1.0 / t.risk_frac).mean()
    return (f"{label:22s} n={len(t):6d} gross={t.gross_r.clip(-10,10).mean():+.3f} "
            f"net={nr.mean():+.3f} med={nr.median():+.3f} win={(nr>0).mean():.2f} "
            f"pos_wk={(wk>0).mean():.2f} be_cost={cstar*1e4:.1f}bps risk={100*t.risk_frac.median():.2f}%")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", type=Path, required=True)
    ap.add_argument("--cost", type=float, default=0.0006, help="round-trip cost fraction (6bps)")
    args = ap.parse_args()
    t = pd.read_parquet(args.trades)
    t = t[(t.break_ts < DEV_END) & (t.risk_frac > 0)]
    print(f"{args.trades.name}: {len(t):,} DEV trades   cost={args.cost*1e4:.0f}bps")
    print("\n" + line(t, args.cost, "ALL"))

    # quality single-feature deciles: does top/bottom decile of each feature lift NET?
    print("\n=== NET-lift by quality feature (top vs bottom tercile) ===")
    rows = []
    for f in QUAL:
        if f not in t or t[f].notna().sum() < 200:
            continue
        x = t[f]; lo, hi = x.quantile([1/3, 2/3])
        top = t[x >= hi]; bot = t[x <= lo]
        rows.append({"feature": f, "top_net": net(top, args.cost).mean(),
                     "bot_net": net(bot, args.cost).mean(),
                     "top_gross": top.gross_r.clip(-10,10).mean(),
                     "spread": net(top, args.cost).mean() - net(bot, args.cost).mean()})
    r = pd.DataFrame(rows).sort_values("top_net", ascending=False)
    print(r.to_string(index=False, float_format=lambda x: f"{x:+.3f}"))

    # combined quality ranker (week-grouped OOF GBM on quality features): does the
    # model's top-quintile of predicted-good setups have a NET edge?
    try:
        from sklearn.ensemble import HistGradientBoostingRegressor
        from sklearn.model_selection import GroupKFold
        feats = [f for f in QUAL if f in t and t[f].notna().sum() > len(t) * 0.5]
        X = t[feats].to_numpy(float)
        col_med = np.nanmedian(X, axis=0); ix = np.where(~np.isfinite(X))
        X[ix] = np.take(col_med, ix[1])
        y = t.gross_r.clip(-5, 5).to_numpy(float); wk = t.week.to_numpy()
        oof = np.full(len(y), np.nan)
        gkf = GroupKFold(n_splits=5)
        for tr, te in gkf.split(X, y, wk):
            m = HistGradientBoostingRegressor(max_depth=3, max_iter=200, learning_rate=0.05,
                                              min_samples_leaf=200, l2_regularization=1.0)
            m.fit(X[tr], y[tr]); oof[te] = m.predict(X[te])
        t = t.assign(_score=oof)
        print("\n=== combined quality ranker (OOF GBM), top-quintile NET ===")
        for qq, lbl in [(0.8, "top 20%"), (0.9, "top 10%")]:
            thr = np.nanpercentile(oof, qq * 100)
            print(line(t[t._score >= thr], args.cost, lbl))
    except Exception as e:
        print("ranker skipped:", e)

    # the user's core filter: REAL distribution high (big down_after) + rejection wick + seller aggression
    print("\n=== the hypothesis stack (real-high + wick + seller aggression), by tier ===")
    # refined: REAL distribution high (down_after high) + pre-poke volatility high
    # + seller aggression (low taker). poke_wick dropped -- it was inverse (a huge
    # wick = stop-hunt more likely to re-run).
    q = t[(t.prior_down_after >= t.prior_down_after.median())
          & (t.pre_atr >= t.pre_atr.median())
          & (t.reclaim_taker <= t.reclaim_taker.median())]
    tier = pd.cut(t.trail_turnover, [-1, 30e6, 150e6, 1e15], labels=["low", "mid", "high"])
    q_tier = pd.cut(q.trail_turnover, [-1, 30e6, 150e6, 1e15], labels=["low", "mid", "high"])
    print(line(q, args.cost, "quality-stack ALL"))
    for tr in ["low", "mid", "high"]:
        sub = q[q_tier == tr]
        if len(sub) >= 150:
            print(line(sub, args.cost, f"  quality {tr}"))


if __name__ == "__main__":
    main()
