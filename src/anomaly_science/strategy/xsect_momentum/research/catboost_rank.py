"""Forward-return-rank predictability study (§8) — the last honest card.

Question: does the feature pool (momentum + anti-pump/quality + lower-TF intraday)
predict the *rank* of forward return, beyond raw momentum, beyond a linear model,
and beyond a shuffled target — under leak-free time-block CV?

Metric = rank-IC (mean per-date Spearman of prediction vs forward return). Robust
to the fat-tail outliers that made the raw spread misleading. Nothing tradeable is
claimed here; this only tests whether predictive information exists.

Run:  python -m anomaly_science.strategy.xsect_momentum.research.catboost_rank
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from anomaly_science.strategy.xsect_momentum.research import features as ft
from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.policy import PRIMARY as P

OUT = Path(".output/results/xsect_momentum")
HORIZON = 7
N_FOLDS = 5


def _per_date_rank(df: pd.DataFrame, col: str) -> pd.Series:
    return df.groupby("date")[col].rank(pct=True)


def rank_ic(df: pd.DataFrame, pred_col: str, min_n: int = 10) -> tuple[float, float, int]:
    """Mean per-date Spearman(pred, fwd_ret) — vectorized (Pearson on within-date ranks)."""
    d = df[["date", pred_col, "fwd_ret"]].dropna()
    if d.empty:
        return np.nan, np.nan, 0
    n = d.groupby("date")[pred_col].transform("size")
    d = d[n >= min_n]
    if d.empty:
        return np.nan, np.nan, 0
    g = d.groupby("date")
    pr = g[pred_col].rank(); yr = g["fwd_ret"].rank()
    prc = pr - pr.groupby(d["date"]).transform("mean")
    yrc = yr - yr.groupby(d["date"]).transform("mean")
    num = (prc * yrc).groupby(d["date"]).sum()
    den = np.sqrt((prc * prc).groupby(d["date"]).sum() * (yrc * yrc).groupby(d["date"]).sum())
    ics = (num / den.replace(0, np.nan)).dropna().to_numpy()
    if len(ics) == 0:
        return np.nan, np.nan, 0
    tstat = ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics))) if ics.std() > 0 else np.nan
    return float(ics.mean()), float(tstat), len(ics)


def time_folds(dates: np.ndarray, k: int, embargo: int) -> list[tuple[np.ndarray, np.ndarray]]:
    uniq = np.array(sorted(pd.unique(dates)))
    chunks = np.array_split(uniq, k + 1)  # first chunk is train-only warmup
    folds = []
    for i in range(1, len(chunks)):
        test = chunks[i]
        cutoff = test.min() - np.timedelta64(embargo, "D")
        train = uniq[uniq < cutoff]
        if len(train) < 20:
            continue
        folds.append((train, test))
    return folds


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    from catboost import CatBoostRegressor
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler

    print("building features (IS only) ...")
    panel = pn.load_panel(is_only=True)
    qv = pn.pivot(panel, "quote_volume")
    umask = pn.build_universe_mask(panel, qv, P.universe_n, P.liquidity_lb, P.min_age_days)
    df = ft.build_features(panel, P, HORIZON)

    # keep in-universe coin-days with a valid target
    um = umask.stack().rename("in_universe").reset_index()
    um.columns = ["date", "symbol", "in_universe"]
    df = df.merge(um, on=["date", "symbol"], how="left")
    df = df[df["in_universe"].fillna(False) & df["fwd_ret"].notna()].copy()
    # normalize to tz-naive: .values drops tz and would break fold membership tests
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_convert(None)
    feats = ft.feature_columns(df)
    # drop rows with no momentum at all
    df = df[df["mom_long"].notna()].reset_index(drop=True)
    print(f"  rows={len(df):,}  dates={df['date'].nunique()}  feats={len(feats)}")

    # target = per-date percentile rank of forward return (robust to fat tails)
    df["y_rank"] = _per_date_rank(df, "fwd_ret")

    folds = time_folds(df["date"].values, N_FOLDS, embargo=HORIZON + 2)
    print(f"  folds={len(folds)}")

    oof = {"catboost": [], "linear": [], "mom_long": [], "shuffled": []}
    for fi, (train_dates, test_dates) in enumerate(folds):
        tr = df[df["date"].isin(train_dates)]
        te = df[df["date"].isin(test_dates)].copy()

        X_tr, y_tr = tr[feats], tr["y_rank"]
        X_te = te[feats]

        # CatBoost (native NaN handling)
        cb = CatBoostRegressor(loss_function="RMSE", iterations=600, depth=6,
                               learning_rate=0.03, l2_leaf_reg=8.0, random_seed=fi,
                               verbose=False)
        cb.fit(X_tr, y_tr)
        te["catboost"] = cb.predict(X_te)

        # shuffled-target control (same model, labels globally permuted in-fold ->
        # must break every feature->target link, so IC must collapse to ~0)
        rng = np.random.default_rng(1000 + fi)
        y_sh = pd.Series(rng.permutation(y_tr.to_numpy()), index=y_tr.index)
        cb2 = CatBoostRegressor(loss_function="RMSE", iterations=600, depth=6,
                                learning_rate=0.03, l2_leaf_reg=8.0, random_seed=fi, verbose=False)
        cb2.fit(X_tr, y_sh)
        te["shuffled"] = cb2.predict(X_te)

        # linear baseline (median-fill + standardize)
        med = X_tr.median()
        Xtr_f = X_tr.fillna(med); Xte_f = X_te.fillna(med)
        sc = StandardScaler().fit(Xtr_f)
        lin = Ridge(alpha=10.0).fit(sc.transform(Xtr_f), y_tr)
        te["linear"] = lin.predict(sc.transform(Xte_f))

        # momentum-only baseline = the raw long momentum score
        te["mom_long_pred"] = te["mom_long"]

        for k, col in [("catboost", "catboost"), ("linear", "linear"),
                       ("mom_long", "mom_long_pred"), ("shuffled", "shuffled")]:
            oof[k].append(te[["date", "symbol", col, "fwd_ret"]].rename(columns={col: "pred"}))

    # univariate per-feature rank-IC (leak sniff: any single causal feature with
    # implausibly high IC is a red flag to inspect)
    print("\n=== univariate feature rank-IC (top 12 by |IC|) ===")
    uni = []
    for c in feats:
        ic, t, n = rank_ic(df.rename(columns={c: "pred"})[["date", "pred", "fwd_ret"]], "pred")
        if np.isfinite(ic):
            uni.append((c, ic, t))
    for c, ic, t in sorted(uni, key=lambda x: -abs(x[1]))[:12]:
        print(f"  {c:22s}  IC={ic:+.4f}  t={t:+.2f}")

    print("\n=== rank-IC (mean per-date Spearman of prediction vs forward return, OOF) ===")
    results = {}
    for k, parts in oof.items():
        allp = pd.concat(parts, ignore_index=True)
        ic, t, n = rank_ic(allp, "pred")
        results[k] = (ic, t, n)
        print(f"  {k:10s}  IC={ic:+.4f}  t={t:+.2f}  (dates={n})")

    # decile lift: top-decile forward vs bottom-decile forward, by catboost score, OOF
    cb_all = pd.concat(oof["catboost"], ignore_index=True)
    cb_all["pred_rank"] = _per_date_rank(cb_all, "pred")
    top = cb_all[cb_all["pred_rank"] >= 0.9]["fwd_ret"]
    bot = cb_all[cb_all["pred_rank"] <= 0.1]["fwd_ret"]
    print(f"\n  catboost decile: top median={top.median()*100:+.2f}%  bottom median={bot.median()*100:+.2f}%  "
          f"top>bot dates check via IC above")

    # --- anti-pump validation (user hypothesis) ---
    print("\n=== anti-pump check: within top-momentum decile, do pump-signature coins underperform? ===")
    dd = df.copy()
    dd["mom_rank"] = _per_date_rank(dd, "mom_long")
    tm = dd[dd["mom_rank"] >= 0.9].copy()  # the momentum leaders
    for sig in ["hhi_mean", "verticality", "stretch", "maxminshare_mean", "maxabsret_max"]:
        if sig not in tm.columns:
            continue
        tm["_sig_rank"] = tm.groupby("date")[sig].rank(pct=True)
        hi = tm[tm["_sig_rank"] >= 0.5]["fwd_ret"].median()
        lo = tm[tm["_sig_rank"] < 0.5]["fwd_ret"].median()
        print(f"  leaders split by {sig:18s}: high median fwd={hi*100:+.2f}%   low median fwd={lo*100:+.2f}%   "
              f"({'pump hurts' if hi < lo else 'no/inverse'})")

    pd.DataFrame({k: {'ic': v[0], 't': v[1], 'dates': v[2]} for k, v in results.items()}).T.to_parquet(OUT / "catboost_rank_ic.parquet")
    print(f"\nwrote {OUT / 'catboost_rank_ic.parquet'}")


if __name__ == "__main__":
    main()
