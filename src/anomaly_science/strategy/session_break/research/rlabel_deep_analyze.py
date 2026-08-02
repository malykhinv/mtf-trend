"""Deep-revert study: among unconfident pokes that returned into the range, what
separates the ones that fall ALL THE WAY to the range LOW (a big, tradeable move)
from the ones that consolidate back ABOVE the high? Directly targets the user's
questions:
  #1 does the coin actually MOVE (sess_ampl_pct, atr_pct)?
  #2 what distinguishes reach-the-bottom from close-above?
  #3 relative volume (poke/pre/ret rvol), and is the prior high a REAL prominent
     high (prior_up_imp, prior_high_prom) or a weak drift high (ema_dist<0 downtrend)?

Reports: deep-revert base rate overall + by session + by amplitude/volume/prominence
tier; CatBoost week-OOF AUC (deep vs above) + null + SHAP portrait; and a PRICE-SPACE
EV table for a target=LOW short by tier (does bottom-targeting on moving coins beat costs?).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, Pool
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
# non-features / outcome / geometry that would leak or isn't causal-predictive
DROP = {"symbol", "tf", "variant", "session", "break_ts", "week", "label", "label_deep",
        "gross_r", "risk_frac", "ref_high", "ref_low", "ref_mid", "poke_high", "entry_ts",
        "entry_px", "poke_ts", "ref_start_ts", "trail_turnover", "reward_low_pct"}


def _cb():
    return CatBoostClassifier(depth=4, iterations=350, learning_rate=0.04, l2_leaf_reg=6.0,
                             loss_function="Logloss", random_seed=0, verbose=False)


def _clean(df, feats):
    X = df[feats].to_numpy(float)
    med = np.nanmedian(X, axis=0); ix = np.where(~np.isfinite(X))
    X[ix] = np.take(med, ix[1])
    lo = np.nanpercentile(X, 1, axis=0); hi = np.nanpercentile(X, 99, axis=0)
    return np.clip(X, lo, hi)


def oof(X, y, wk):
    o = np.full(len(y), np.nan)
    for tr, te in GroupKFold(5).split(X, y, wk):
        if len(np.unique(y[tr])) < 2:
            continue
        m = _cb(); m.fit(X[tr], y[tr]); o[te] = m.predict_proba(X[te])[:, 1]
    return o


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=Path(".output/results/session_break/rlabel_tf60_deep.parquet"))
    ap.add_argument("--cost-bp", type=float, default=8.0, help="round-trip cost incl slippage (price bp)")
    args = ap.parse_args()
    from anomaly_science.strategy.session_break.research.sessions import BLOCK_BY_SEQ
    raw = pd.read_parquet(args.events)
    raw = raw[raw.break_ts < DEV_END].copy()
    # decidable deep race = reached low (1) or consolidated above (0); drop the stuck-mid (-1)
    t = raw[raw.label_deep >= 0].copy()
    t["reward_mid_pct"] = (t.entry_px - t.ref_mid) / t.entry_px
    print(f"events(DEV)={len(raw):,}  deep-decidable={len(t):,}  DEEP(reach low)={int((t.label_deep==1).sum()):,} "
          f"ABOVE={int((t.label_deep==0).sum()):,}  deep_base={t.label_deep.mean():.3f}")
    print("  (context: of ALL returns, reach-low share vs stuck-mid vs above =",
          f"{(raw.label_deep==1).mean():.2f}/{(raw.label_deep==-1).mean():.2f}/{(raw.label_deep==0).mean():.2f})")

    print("\n--- deep-revert rate by SESSION ---")
    for s, g in t.groupby("session"):
        print(f"  {BLOCK_BY_SEQ[int(s)].name:8s} n={len(g):6d} deep={g.label_deep.mean():.3f} "
              f"med_ampl={100*g.sess_ampl_pct.median():.2f}% med_atr={100*g.atr_pct.median():.3f}%")

    # ---- tier tables for the user's factors (#1 move, #3 volume, #3 real-high) ----
    def tier_table(col, edges, lbl, pct=True):
        print(f"\n--- deep rate & target=LOW EV by {lbl} ({col}) ---")
        print(f"{'tier':>12}{'n':>8}{'deep%':>7}{'above%':>7}{'rewLOW':>8}{'risk':>7}{'EV_bp':>7}{'net_bp':>8}{'poswk':>7}")
        for lo, hi, nm in edges:
            s = t[(t[col] >= lo) & (t[col] < hi)]
            if len(s) < 200:
                continue
            deep = (s.label_deep == 1).mean(); above = (s.label_deep == 0).mean()
            rlow = 1e4 * s.reward_low_pct.median(); risk = 1e4 * s.risk_frac.median()
            # price-space EV: reach low -> +rewardLOW; above -> -risk (stop over poke)
            ev = deep * (1e4 * s.reward_low_pct) - above * (1e4 * s.risk_frac)
            net = ev - args.cost_bp
            wk = net.groupby(s.week).mean()
            sc = (100 * lo if pct else lo)
            print(f"{nm:>12}{len(s):>8}{deep:>7.2f}{above:>7.2f}{rlow:>8.0f}{risk:>7.0f}"
                  f"{ev.mean():>+7.0f}{net.mean():>+8.0f}{(wk>0).mean():>7.2f}")

    tier_table("atr_pct", [(0, 0.002, "<0.2%"), (0.002, 0.004, "0.2-0.4%"), (0.004, 0.007, "0.4-0.7%"),
                           (0.007, 0.012, "0.7-1.2%"), (0.012, 1, ">1.2%")], "coin move / bar-ATR%")
    tier_table("sess_ampl_pct", [(0, 0.02, "<2%"), (0.02, 0.04, "2-4%"), (0.04, 0.08, "4-8%"),
                                 (0.08, 0.15, "8-15%"), (0.15, 5, ">15%")], "session amplitude %")
    tier_table("poke_rvol", [(0, 1, "<1x"), (1, 2, "1-2x"), (2, 4, "2-4x"), (4, 8, "4-8x"),
                             (8, 1e9, ">8x")], "poke relative volume", pct=False)
    tier_table("prior_up_imp", [(0, 1, "<1ATR"), (1, 2, "1-2"), (2, 4, "2-4"), (4, 8, "4-8"),
                                (8, 1e9, ">8")], "prior-high up-impulse (real high)", pct=False)
    tier_table("prior_high_prom", [(-1e9, 0.5, "<0.5"), (0.5, 1.5, "0.5-1.5"), (1.5, 3, "1.5-3"),
                                   (3, 6, "3-6"), (6, 1e9, ">6")], "prior-high prominence", pct=False)

    # ---- CatBoost discrimination deep vs above ----
    feats = [c for c in t.columns if c not in DROP and c != "reward_mid_pct"]
    y = (t.label_deep == 1).astype(int).to_numpy(); wk = t.week.to_numpy(); X = _clean(t, feats)
    print(f"\n=== CatBoost DEEP-vs-ABOVE  n={len(t):,} feats={len(feats)} weeks={t.week.nunique()} base={y.mean():.3f} ===")
    o = oof(X, y, wk); m = np.isfinite(o); auc = roc_auc_score(y[m], o[m])
    rng = np.random.default_rng(0); null = []
    for _ in range(8):
        yp = y.copy()
        for w in np.unique(wk):
            mm = wk == w; yp[mm] = rng.permutation(yp[mm])
        op = oof(X, yp, wk); mk = np.isfinite(op)
        if len(np.unique(yp[mk])) > 1:
            null.append(roc_auc_score(yp[mk], op[mk]))
    print(f"  week-CV AUC={auc:.3f}  null95={np.nanpercentile(null,95):.3f}  "
          f"-> {'SIGNAL' if auc>np.nanpercentile(null,95) and auc>0.55 else 'weak'}")
    thr = np.nanpercentile(o[m], 90); top = m & (o >= thr)
    print(f"  top-decile deep rate={y[top].mean():.3f} vs base {y.mean():.3f}")
    full = _cb(); full.fit(X, y)
    shap = np.abs(full.get_feature_importance(Pool(X, y), type="ShapValues")[:, :-1]).mean(0)
    corr = {f: spearmanr(X[:, i], y).correlation for i, f in enumerate(feats)}
    med1 = t[t.label_deep == 1][feats].median(); med0 = t[t.label_deep == 0][feats].median()
    tbl = pd.DataFrame({"shap": pd.Series(shap, index=feats), "spearman_deep": pd.Series(corr),
                        "deep_med": med1, "above_med": med0}).sort_values("shap", ascending=False)
    print(tbl.head(16).to_string(float_format=lambda x: f"{x:+.3f}"))


if __name__ == "__main__":
    main()
