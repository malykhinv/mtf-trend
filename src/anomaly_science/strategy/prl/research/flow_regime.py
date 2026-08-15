"""Market-internal FLOW + DIVERGENCE features -> can CatBoost nowcast the regime EARLIER?

Free-data flow proxies over the full 2020-2025 (OI/long-short only exist 2025+): breadth, BTC<->alt
rotation, aggregate turnover trend, taker aggressor imbalance, dispersion, and DIVERGENCES (price up
but breadth/flow/volume NOT confirming = money leaving into a rising tape = regime about to turn).
Target = forward 20d BTC regime (bull/side/bear). Walk-forward CatBoost: does it beat the trailing
r60 detector on TIMELINESS (call turns earlier) and is the signal real (shuffle) with divergences
carrying weight (importance)? IS only, OOS reserved. Run: python -m anomaly_science.strategy.prl.research.flow_regime
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.xsect_momentum.research.catboost_rank import time_folds
from anomaly_science.strategy.prl.research.policy import PRIMARY


def main():
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    close = pn.pivot(panel, "close"); qv = pn.pivot(panel, "quote_volume")
    tbq = pn.pivot(panel, "taker_buy_quote_volume")
    close.index = pd.to_datetime(close.index).tz_localize(None)
    for d in (qv, tbq):
        d.index = close.index
    ret = close.pct_change(fill_method=None)
    btc = close["BTCUSDT"]
    um = qv.rolling(30, min_periods=10).mean() > qv.rolling(30, min_periods=10).mean().median(axis=1).median() * 0.0  # loose
    alt = ret.drop(columns=["BTCUSDT"], errors="ignore").mean(axis=1)

    F = pd.DataFrame(index=close.index)
    # trend / momentum
    F["btc_r20"] = btc / btc.shift(20) - 1
    F["btc_r60"] = btc / btc.shift(60) - 1
    F["btc_vs_ma50"] = btc / btc.rolling(50, min_periods=25).mean() - 1
    # breadth (participation)
    above20 = close >= close.rolling(20, min_periods=10).max().shift(1)
    F["breadth"] = above20.mean(axis=1)
    F["breadth_chg"] = F["breadth"] - F["breadth"].rolling(10).mean()
    # rotation BTC<->alts
    F["rotation"] = (btc / btc.shift(20) - 1) - (1 + alt).rolling(20).apply(np.prod, raw=True) + 1
    F["alt_lead"] = (1 + alt).rolling(10).apply(np.prod, raw=True) - 1 - (btc / btc.shift(10) - 1)
    # flow: aggregate turnover trend + taker aggressor imbalance
    tot = qv.sum(axis=1)
    F["turn_trend"] = tot.rolling(20).mean() / tot.rolling(60).mean() - 1
    taker_imb = (2 * tbq.sum(axis=1) - qv.sum(axis=1)) / qv.sum(axis=1)
    F["taker_imb"] = taker_imb
    F["taker_imb_trend"] = taker_imb.rolling(10).mean() - taker_imb.rolling(30).mean()
    # dispersion / vol
    F["dispersion"] = ret.std(axis=1)
    F["btc_vol"] = btc.pct_change().rolling(20).std()
    # DIVERGENCES (price up but internals not confirming)
    F["div_breadth"] = np.sign(F["btc_r20"]) * (F["breadth"] - 0.5)          # aligned>0, diverging<0
    F["div_turn"] = np.sign(F["btc_r20"]) * F["turn_trend"]
    F["div_taker"] = np.sign(F["btc_r20"]) * F["taker_imb"]
    F["div_breadthfall_pricerise"] = ((F["btc_r20"] > 0) & (F["breadth_chg"] < 0)).astype(float)
    F = F.shift(1)                                                            # CAUSAL: all known day-before

    # target: forward 20d BTC regime
    fwd = btc.shift(-20) / btc - 1
    tgt = pd.Series(1, index=btc.index)   # side=1
    tgt[fwd > 0.10] = 2                    # bull
    tgt[fwd < -0.10] = 0                   # bear
    D = F.join(tgt.rename("y")).dropna()
    feats = [c for c in F.columns]
    print(f"days={len(D)}  regime balance bull/side/bear: {(D.y==2).mean():.2f}/{(D.y==1).mean():.2f}/{(D.y==0).mean():.2f}")

    from catboost import CatBoostClassifier
    from sklearn.metrics import accuracy_score
    dn = D.index.values
    oofp = pd.Series(np.nan, index=D.index); imps = []
    for tr, te in time_folds(dn, 6, embargo=20):
        tri = D.index[np.isin(dn, tr)]; tei = D.index[np.isin(dn, te)]
        if len(tri) < 200 or D.loc[tri, "y"].nunique() < 2:
            continue
        cb = CatBoostClassifier(iterations=300, depth=4, learning_rate=0.03, l2_leaf_reg=8, random_seed=0, verbose=False)
        cb.fit(D.loc[tri, feats], D.loc[tri, "y"]); oofp.loc[tei] = cb.predict(D.loc[tei, feats]).ravel()
        imps.append(cb.get_feature_importance())
    mm = oofp.notna()
    acc = accuracy_score(D.loc[mm, "y"], oofp[mm])
    # baselines
    trail = pd.Series(1, index=D.index); trail[D["btc_r60"] > 0.12] = 2; trail[D["btc_r60"] < -0.12] = 0
    base_acc = accuracy_score(D.loc[mm, "y"], trail[mm])
    maj = D["y"].value_counts(normalize=True).max()
    rng = np.random.default_rng(0); ysh = pd.Series(rng.permutation(D["y"].values), index=D.index)
    sh_acc = accuracy_score(ysh[mm], oofp[mm])
    print(f"\nforward-regime nowcast accuracy:")
    print(f"  CatBoost(flow+divergence) {acc:.3f}   trailing-r60 {base_acc:.3f}   majority {maj:.3f}   shuffle {sh_acc:.3f}")
    imp = pd.Series(np.mean(imps, axis=0), index=feats).sort_values(ascending=False)
    print("  top features: " + ", ".join(f"{k}={v:.1f}" for k, v in imp.head(10).items()))

    # timeliness: does CatBoost flip to bear/bull earlier than trailing at the big turns?
    print("\n=== timeliness at major BTC turns (days CatBoost leads/lags trailing; + = earlier) ===")
    cb_lab = oofp.reindex(D.index)
    for name, dt in [("2021-11 top", "2021-11-10"), ("2022-05 LUNA", "2022-05-09"),
                     ("2022-11 FTX", "2022-11-08"), ("2023-01 recov", "2023-01-13")]:
        d0 = pd.Timestamp(dt)
        want = 0 if "top" in name or "LUNA" in name or "FTX" in name else 2
        cbf = cb_lab[(cb_lab.index >= d0 - pd.Timedelta(days=30)) & (cb_lab == want)]
        trf = trail[(trail.index >= d0 - pd.Timedelta(days=30)) & (trail == want)]
        cbt = cbf.index[0] if len(cbf) else None; trt = trf.index[0] if len(trf) else None
        lead = (trt - cbt).days if (cbt and trt) else "na"
        print(f"  {name:14s} CatBoost->{ ('%s'%cbt.date()) if cbt is not None else 'na':>10}  trailing->{('%s'%trt.date()) if trt is not None else 'na':>10}  lead={lead}")


if __name__ == "__main__":
    main()
