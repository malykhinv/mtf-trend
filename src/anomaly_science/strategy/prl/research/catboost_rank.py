"""PRL residual-rank predictability test (§38, §39) — how much can a nonlinear
ranker extract from the wide causal pool, beyond the simple quality composite,
beyond a linear model, and beyond shuffled/delayed controls?

Metric = OOF rank-IC (mean per-date Spearman of prediction vs the frozen-beta
future residual return), robust to fat tails. Purged/embargoed time-block CV,
group = date. Nothing tradeable is claimed here — this only tests whether extra
predictive information exists on IS. OOS stays reserved.

Run: python -m anomaly_science.strategy.prl.research.catboost_rank
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.xsect_momentum.research.catboost_rank import (
    _per_date_rank, rank_ic, time_folds,
)
from anomaly_science.strategy.prl.research import feature_pool as fp
from anomaly_science.strategy.prl.research.policy import PRIMARY as P

OUT = Path(".output/results/prl_coarse")
N_FOLDS = 5


def _load_pool(p) -> tuple[pd.DataFrame, list[str]]:
    oos = pd.Timestamp(p.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    qv = pn.pivot(panel, "quote_volume")
    umask = pn.build_universe_mask(panel, qv, p.universe_n, p.liquidity_lb, p.min_age_days)
    df, feats = fp.build_feature_pool(panel, umask, p)
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_convert(None)
    return df, feats


def _decile_net(oof: pd.DataFrame, p) -> dict:
    """Robust (median) top-minus-bottom decile net spread on the residual label."""
    d = oof.dropna(subset=["pred", "fwd_ret"]).copy()
    d["r"] = _per_date_rank(d, "pred")
    top = d[d["r"] >= 0.9]["fwd_ret"]; bot = d[d["r"] <= 0.1]["fwd_ret"]
    side = (p.fee_bps + p.half_spread_bps + p.base_slippage_bps) * p.cost_multiplier / 1e4
    med = float(top.median() - bot.median())
    return {"top_med": float(top.median()), "bot_med": float(bot.median()),
            "median_spread": med, "net_median": med - 4 * side}


def _fit_fold(feats, tr, te, seed):
    from catboost import CatBoostRegressor
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler
    kw = dict(loss_function="RMSE", iterations=600, depth=6, learning_rate=0.03,
              l2_leaf_reg=8.0, random_seed=seed, verbose=False)
    cb = CatBoostRegressor(**kw).fit(tr[feats], tr["y_rank"])
    te = te.copy()
    te["catboost"] = cb.predict(te[feats])
    rng = np.random.default_rng(1000 + seed)
    y_sh = pd.Series(rng.permutation(tr["y_rank"].to_numpy()), index=tr.index)
    cb2 = CatBoostRegressor(**kw).fit(tr[feats], y_sh)
    te["shuffled"] = cb2.predict(te[feats])
    med = tr[feats].median()
    sc = StandardScaler().fit(tr[feats].fillna(med))
    lin = Ridge(alpha=10.0).fit(sc.transform(tr[feats].fillna(med)), tr["y_rank"])
    te["linear"] = lin.predict(sc.transform(te[feats].fillna(med)))
    return te, cb.get_feature_importance()


def _run_cv(df, feats, tag):
    folds = time_folds(df["date"].values, N_FOLDS, embargo=P.fwd_horizon + 2)
    oof = {"catboost": [], "shuffled": [], "linear": []}
    imps = []
    for fi, (tr_d, te_d) in enumerate(folds):
        tr = df[df["date"].isin(tr_d)]; te = df[df["date"].isin(te_d)]
        te, imp = _fit_fold(feats, tr, te, fi)
        imps.append(imp)
        for k in oof:
            oof[k].append(te[["date", "symbol", k, "resid_label"]]
                          .rename(columns={k: "pred", "resid_label": "fwd_ret"}))
    print(f"\n[{tag}]  folds={len(folds)}  rows={len(df):,}  feats={len(feats)}")
    res = {}
    for k, parts in oof.items():
        allp = pd.concat(parts, ignore_index=True)
        ic, t, n = rank_ic(allp, "pred")
        res[k] = (ic, t, n)
        print(f"  {k:9s} OOF rank-IC={ic:+.4f}  t={t:+.2f}  (dates={n})")
    cb_all = pd.concat([o.assign() for o in oof["catboost"]], ignore_index=True)
    dec = _decile_net(cb_all, P)
    print(f"  catboost decile (median): top={dec['top_med']*100:+.3f}%  bot={dec['bot_med']*100:+.3f}%  "
          f"spread={dec['median_spread']*100:+.3f}%  net={dec['net_median']*100:+.3f}%")
    imp = pd.Series(np.mean(imps, axis=0), index=feats).sort_values(ascending=False)
    return res, dec, imp, oof


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("building wide residual feature pool (IS only, OOS reserved) ...")
    df, feats = _load_pool(P)

    # univariate leak-sniff: any single causal feature with implausible IC
    print("\n=== univariate feature rank-IC (top 15 by |IC|) ===")
    uni = []
    for c in feats:
        ic, t, n = rank_ic(df[["date", c, "resid_label"]]
                           .rename(columns={c: "pred", "resid_label": "fwd_ret"}), "pred")
        if np.isfinite(ic):
            uni.append((c, ic, t))
    for c, ic, t in sorted(uni, key=lambda x: -abs(x[1]))[:15]:
        print(f"  {c:20s} IC={ic:+.4f}  t={t:+.2f}")

    res, dec, imp, oof = _run_cv(df, feats, "PRIMARY")
    print("\n=== CatBoost feature importance (top 15) ===")
    for name, v in imp.head(15).items():
        print(f"  {name:20s} {v:6.2f}")

    # persist OOF predictions so trader_summary / leakage_audit run on the real
    # in-fold fitted signal (linear = simpler & higher IC, catboost as cross-check).
    lin = pd.concat(oof["linear"], ignore_index=True).rename(
        columns={"pred": "score_linear", "fwd_ret": "resid_label"})
    cb = pd.concat(oof["catboost"], ignore_index=True).rename(
        columns={"pred": "score_catboost"})[["date", "symbol", "score_catboost"]]
    oof_df = lin.merge(cb, on=["date", "symbol"], how="inner")
    oof_df.to_parquet(OUT / "oof_scores_prl.parquet")
    print(f"  wrote {OUT}/oof_scores_prl.parquet  ({len(oof_df):,} rows)")

    # +1-day execution-delay control (§5.1, §87): rebuild the pool with skip+1.
    print("\nbuilding +1d-delay pool ...")
    df_d, feats_d = _load_pool(replace(P, skip=P.skip + 1))
    res_d, dec_d, _, _ = _run_cv(df_d, feats_d, "DELAY+1d")

    rows = [{"variant": "primary", **{f"ic_{k}": res[k][0] for k in res},
             "cb_t": res["catboost"][1], "net_median": dec["net_median"]},
            {"variant": "delay+1d", **{f"ic_{k}": res_d[k][0] for k in res_d},
             "cb_t": res_d["catboost"][1], "net_median": dec_d["net_median"]}]
    pd.DataFrame(rows).to_parquet(OUT / "catboost_rank_prl.parquet")
    imp.rename("importance").to_frame().to_parquet(OUT / "catboost_importance_prl.parquet")
    print(f"\nwrote {OUT}/catboost_rank_prl.parquet")

    cb_ic = res["catboost"][0]; sh_ic = res["shuffled"][0]
    print(f"\n  CatBoost OOF IC={cb_ic:+.4f} vs shuffled={sh_ic:+.4f} vs delay+1d={res_d['catboost'][0]:+.4f}")
    print("  (shuffled must be ~0; a large primary->delay drop = latency-fragile)")


if __name__ == "__main__":
    main()
