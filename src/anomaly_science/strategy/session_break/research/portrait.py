"""Runner-vs-fizzle discrimination portrait for session-break events.

Answers the discovery question -- *after price crosses the previous session's
high, what at the break separates the far-runners from the fizzles* -- without
making any execution claim yet. Discipline (from prior burned studies):

  * causal features only (already enforced at build time),
  * cap tier from trailing turnover, because the mechanics are cap-conditional,
  * runner/fizzle defined WITHIN each (variant x tier x session) stratum, so
    "far" means far *relative to what is normal for that tier and session*,
  * every AUC checked against a within-week shuffled null,
  * multivariate AUC via week-grouped cross-validation (week-cluster inference),
  * the two prev-session variants reported SEPARATELY, never pooled.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler

DEFAULT_EVENTS = Path(".output/results/session_break/events.parquet")
DEV_END_MS = pd.Timestamp("2026-01-01T00:00:00Z").value // 10**6

# trailing-24h turnover cuts (USDT). Mechanics are absolute (book depth /
# cost-to-move), so tiers are absolute, not cross-sectional ranks.
TIER_CUTS = {"low": (0, 30e6), "mid": (30e6, 150e6), "high": (150e6, np.inf)}

FEATURES = [
    "rvol",            # break-bar volume vs trailing 24h median  (relative volume)
    "rtrades",         # break-bar trade count vs baseline        (participation breadth)
    "ats_ratio",       # avg trade size vs baseline  (>1 = whale, <1 = retail crowd)
    "block_qv_ratio",  # block volume so far vs baseline-implied   (session engagement)
    "taker_buy_share", # aggressor buy share at the break
    "oi_change",       # OI at break vs block start (new longs vs short-covering)
    "liq_fuel_norm",   # short-liquidation fuel in block, vs baseline volume
    "poke_atr",        # how far above ref the break bar printed
    "runup_atr",       # how far price already travelled to reach ref
    "bars_to_break",   # minutes into the block before the break
    # participation SHAPE
    "vol_accel",       # last-5m volume vs prior-15m (surge steepness)
    "climax",          # break-bar volume vs block max so far (climax vs building)
    "block_vol_share", # break bar's share of block volume (concentration)
    "ats_trend",       # break avg trade size vs block avg (whales arriving)
    "green_run",       # consecutive green closes into the break (momentum)
    # level / trend context
    "ref_touches",     # times the reference block tested the level
    "ema_dist_atr",    # price vs causal 1d EMA (extension / trend)
    "vwap_dist_atr",   # price vs rolling 1d VWAP
    "stretch_vwap_atr",# price vs the CURRENT session VWAP (fade over-extension)
    # extra market-logical metrics
    "brk_body",        # break-candle body fraction (conviction)
    "brk_upwick",      # break-candle upper wick (top rejection)
    "brk_range_atr",   # break-candle range in ATR (violence)
    "ret_15",          # 15-min momentum into the break
    "ret_60",          # 60-min momentum into the break
    "vol_ratio",       # ATR now vs 60 bars ago (volatility expansion)
    "sess_progress",   # fraction into the session block at the break
    "dist_dayhigh_atr",# distance below the rolling daily high (room)
    "rtrades_accel",   # break trade-count vs recent 15-bar mean
    "taker_trend",     # taker-buy share at break vs block mean
    "oi_slope",        # OI change per bar over the block
    "gap_open_atr",    # session open gap from prior block close
]


def add_features(ev: pd.DataFrame) -> pd.DataFrame:
    ev = ev.copy()
    ev["rvol"] = ev["qv_break"] / ev["base_qv"]
    ev["rtrades"] = ev["tc_break"] / ev["base_tc"]
    ev["ats_ratio"] = ev["ats_break"] / ev["ats_base"]
    ev["block_qv_ratio"] = ev["block_qv_to_break"] / (ev["base_qv"] * (ev["bars_to_break"] + 1))
    ev["liq_fuel_norm"] = ev["liq_fuel"] / (ev["base_qv"] + 1.0)
    ev["oi_change"] = ev["oi_change"].fillna(0.0)
    ev["taker_buy_share"] = ev["taker_buy_share"].fillna(0.5)
    for c in FEATURES:
        if c in ev.columns:
            ev[c] = ev[c].astype(float)
    ev["week"] = pd.to_datetime(ev["break_ts"], unit="ms", utc=True).dt.strftime("%G-W%V")
    tier = pd.Series("mid", index=ev.index)
    for name, (lo, hi) in TIER_CUTS.items():
        tier[(ev["trail_turnover"] >= lo) & (ev["trail_turnover"] < hi)] = name
    ev["tier"] = tier
    return ev


def _runner_fizzle(g: pd.DataFrame, target: str) -> pd.DataFrame:
    """Label the runner target within a stratum.

    Continuous targets -> top vs bottom tercile (drop the middle). First-passage
    targets (``fp_*``, already 1/0/-1) -> use as-is, dropping the -1 no-resolve.
    """

    if len(g) < 30:
        return g.iloc[0:0]
    out = g.copy()
    if target.startswith("fp_") or target == "rev_hit":
        out["runner"] = g[target].astype(int)
        return out[out["runner"] >= 0]
    lo, hi = g[target].quantile([1 / 3, 2 / 3])
    out["runner"] = np.where(g[target] >= hi, 1, np.where(g[target] <= lo, 0, -1))
    return out[out["runner"] >= 0]


def _make_model(kind: str):
    if kind == "gbm":
        from sklearn.ensemble import HistGradientBoostingClassifier
        return HistGradientBoostingClassifier(
            max_depth=3, max_iter=200, learning_rate=0.05,
            l2_regularization=1.0, min_samples_leaf=200, random_state=0,
        )
    if kind == "catboost":
        from catboost import CatBoostClassifier
        return CatBoostClassifier(
            depth=4, iterations=350, learning_rate=0.04, l2_leaf_reg=6.0,
            loss_function="Logloss", random_seed=0, verbose=False,
        )
    return LogisticRegression(max_iter=500, C=1.0)


def _clean(x: pd.DataFrame) -> np.ndarray:
    a = x.to_numpy(float)
    a = np.where(np.isfinite(a), a, np.nan)
    col_med = np.nanmedian(a, axis=0)
    idx = np.where(np.isnan(a))
    a[idx] = np.take(col_med, idx[1])
    # winsorise to tame perp-data tails
    lo = np.nanpercentile(a, 1, axis=0)
    hi = np.nanpercentile(a, 99, axis=0)
    return np.clip(a, lo, hi)


def univariate_auc(df: pd.DataFrame) -> pd.DataFrame:
    y = df["runner"].to_numpy(int)
    rows = []
    for f in FEATURES:
        x = _clean(df[[f]])[:, 0]
        try:
            auc = roc_auc_score(y, x)
        except ValueError:
            auc = np.nan
        rows.append({"feature": f, "auc": auc, "runner_med": df.loc[y == 1, f].median(),
                     "fizzle_med": df.loc[y == 0, f].median()})
    r = pd.DataFrame(rows)
    r["lift"] = (r["auc"] - 0.5).abs()
    return r.sort_values("lift", ascending=False)


def multivariate_cv(df: pd.DataFrame, kind: str = "logit", n_shuffle: int = 30,
                    seed: int = 0, max_n: int = 40000):
    """Week-grouped CV AUC vs a within-week shuffled null.

    Returns (real_auc, (null_lo, null_hi), oof_scores) where oof_scores are the
    out-of-fold model probabilities aligned to ``df`` (NaN where a fold could not
    score), for downstream top-decile lift.
    """

    if len(df) > max_n:  # cap huge strata so the shuffled null stays affordable
        df = df.sample(max_n, random_state=seed).reset_index(drop=True)
    y = df["runner"].to_numpy(int)
    weeks = df["week"].to_numpy()
    X = _clean(df[FEATURES])
    n_groups = len(np.unique(weeks))
    if n_groups < 4 or y.sum() < 15 or (1 - y).sum() < 15:
        return np.nan, (np.nan, np.nan), None, df
    k = min(5, n_groups)
    gkf = GroupKFold(n_splits=k)

    def _oof(yv):
        oof = np.full(len(yv), np.nan)
        for tr, te in gkf.split(X, yv, weeks):
            if yv[tr].sum() == 0 or (1 - yv[tr]).sum() == 0:
                continue
            sc = StandardScaler().fit(X[tr])
            clf = _make_model(kind)
            clf.fit(sc.transform(X[tr]), yv[tr])
            oof[te] = clf.predict_proba(sc.transform(X[te]))[:, 1]
        return oof

    def _auc(yv, oof):
        m = np.isfinite(oof)
        if m.sum() < 20 or len(np.unique(yv[m])) < 2:
            return np.nan
        return roc_auc_score(yv[m], oof[m])

    oof_real = _oof(y)
    real = _auc(y, oof_real)
    rng = np.random.default_rng(seed)
    null = []
    for _ in range(n_shuffle):
        yp = y.copy()
        for w in np.unique(weeks):  # shuffle labels WITHIN week
            m = weeks == w
            yp[m] = rng.permutation(yp[m])
        a = _auc(yp, _oof(yp))
        if np.isfinite(a):
            null.append(a)
    band = (np.nanpercentile(null, 5), np.nanpercentile(null, 95)) if null else (np.nan, np.nan)
    return real, band, oof_real, df


def topdecile_lift(df: pd.DataFrame, oof: np.ndarray, outcome: str = "mfe120_atr"):
    """Median forward-MFE of the top-decile-scored breaks vs the stratum median.

    The practically meaningful number: if you traded only the model's top 10%,
    how much bigger is the realised move than a blind break?
    """

    if oof is None or outcome not in df.columns:
        return np.nan, np.nan, np.nan
    m = np.isfinite(oof)
    if m.sum() < 50:
        return np.nan, np.nan, np.nan
    out = df[outcome].to_numpy(float)
    thr = np.nanpercentile(oof[m], 90)
    top = m & (oof >= thr)
    base_med = np.nanmedian(out[m])
    top_med = np.nanmedian(out[top])
    return top_med, base_med, int(top.sum())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    ap.add_argument("--target", default="ext_atr",
                    choices=["ext_atr", "ext_pct", "mfe_atr", "mfe60_atr", "mfe120_atr",
                             "mfe240_atr", "fp_2_15", "fp_3_15", "rev_hit"])
    ap.add_argument("--model", default="logit", choices=["logit", "gbm"])
    ap.add_argument("--lift-outcome", default="mfe120_atr")
    ap.add_argument("--drop", default="", help="comma-separated features to exclude")
    args = ap.parse_args()

    global FEATURES
    if args.drop:
        drop = {d.strip() for d in args.drop.split(",")}
        FEATURES = [f for f in FEATURES if f not in drop]
        print(f"[ablation] using features: {FEATURES}")

    ev = pd.read_parquet(args.events)
    ev = ev[ev["break_ts"] < DEV_END_MS]  # DEV only; OOS frozen
    ev = add_features(ev)
    print(f"DEV events: {len(ev):,}   target={args.target}")
    print("\n=== trailing-turnover / tier mix ===")
    print(ev.groupby(["variant", "tier"]).size().unstack(fill_value=0))

    print("\n=== outcome scale by tier x session (median ext_atr / ext_pct) ===")
    piv = ev.groupby(["tier", "session"]).agg(
        n=("ext_atr", "size"), ext_atr=("ext_atr", "median"), ext_pct=("ext_pct", "median")
    )
    print(piv)

    for variant in ["sequential", "same_type"]:
        sub = ev[ev["variant"] == variant]
        print("\n" + "=" * 78)
        print(f"VARIANT: {variant}   (n={len(sub):,})")
        print("=" * 78)
        for tier in ["high", "mid", "low"]:
            tsub = sub[sub["tier"] == tier]
            if len(tsub) < 200:
                print(f"\n[{tier}] n={len(tsub)} -- too few, skipped")
                continue
            # runner/fizzle within tier x session
            labelled = (
                tsub.groupby("session", group_keys=False)
                .apply(lambda g: _runner_fizzle(g, args.target))
            )
            if len(labelled) < 100:
                print(f"\n[{tier}] labelled n={len(labelled)} -- too few")
                continue
            print(f"\n---- tier={tier}  labelled n={len(labelled):,} "
                  f"(runners={int(labelled['runner'].sum())}) ----")
            uni = univariate_auc(labelled)
            print(uni.head(8).to_string(index=False, float_format=lambda x: f"{x:.3f}"))
            real, band, oof, scored = multivariate_cv(labelled, kind=args.model)
            # a genuine ranker must beat BOTH chance (0.5) and the shuffled null;
            # merely clearing a sub-0.5 null band is not signal.
            has_signal = np.isfinite(real) and np.isfinite(band[1]) and real > band[1] and real > 0.52
            verdict = "SIGNAL" if has_signal else "null"
            print(f"  [{args.model}] week-CV AUC={real:.3f}  shuffled 5-95%=[{band[0]:.3f},{band[1]:.3f}]  -> {verdict}")
            top_med, base_med, n_top = topdecile_lift(scored, oof, args.lift_outcome)
            if np.isfinite(top_med):
                print(f"  top-decile {args.lift_outcome}: {top_med:.2f} vs stratum median {base_med:.2f}"
                      f"  (lift x{top_med / base_med:.2f}, n_top={n_top})")


if __name__ == "__main__":
    main()
