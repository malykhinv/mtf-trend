"""Volume level + volume-evenness study (user 2026-08-04).

Question: does a coin's average daily volume over 1d/1w/1mo/2mo/3mo — and, more
pointedly, how EVEN vs spiky its daily-volume distribution is — predict forward
return? Hypothesis: even/organic volume = quality; spiky/lumpy volume = pump/
manipulation.

Evenness metrics per trailing window W (all vectorized, causal, shifted by skip):
  * cv        = std/mean of daily volume            (low = even)
  * hhi       = sum(v^2)/sum(v)^2                    (low = even; = 1/effective-days)
  * maxshare  = max daily vol / sum over window      (high = one spiky day)
Level metrics: log mean daily quote volume over W.
Plus vol_trend = mean_7 / mean_90 (rising vs fading participation).

Reports univariate rank-IC (mean per-date Spearman vs forward return) + t, monthly
sign-stability for the standouts, correlation with daily return-vol (is this just
volatility re-discovered?), and the incremental IC of evenness after residualizing
on daily_vol. IS only; never touches OOS.

Run: python -m anomaly_science.strategy.xsect_momentum.research.volume_study
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.catboost_rank import rank_ic
from anomaly_science.strategy.xsect_momentum.research.policy import PRIMARY as P

OUT = Path(".output/results/xsect_momentum")
HORIZON = 7
LEVEL_WINS = [1, 7, 30, 60, 90]
EVEN_WINS = [7, 30, 60, 90]


def _roll_sum(m, w):
    return m.rolling(w, min_periods=max(3, w // 2)).sum()


def build(panel: pd.DataFrame) -> tuple[pd.DataFrame, list[str], list[str]]:
    close = pn.pivot(panel, "close")
    qv = pn.pivot(panel, "quote_volume")
    ret = close.pct_change(fill_method=None)
    sk = P.skip

    feats: dict[str, pd.DataFrame] = {}
    for w in LEVEL_WINS:
        feats[f"vol_mean_{w}"] = np.log(qv.rolling(w, min_periods=max(1, w // 2)).mean() + 1.0).shift(sk)
    for w in EVEN_WINS:
        mean = qv.rolling(w, min_periods=max(3, w // 2)).mean()
        std = qv.rolling(w, min_periods=max(3, w // 2)).std()
        s1 = _roll_sum(qv, w)
        s2 = _roll_sum(qv * qv, w)
        feats[f"vol_cv_{w}"] = (std / mean).shift(sk)
        feats[f"vol_hhi_{w}"] = (s2 / (s1 * s1)).shift(sk)          # low = even
        feats[f"vol_maxshare_{w}"] = (qv.rolling(w, min_periods=max(3, w // 2)).max() / s1).shift(sk)
    feats["vol_trend_7_90"] = (qv.rolling(7, min_periods=4).mean() / (qv.rolling(90, min_periods=30).mean() + 1e-9)).shift(sk)
    # control: daily return volatility (the already-known strong feature)
    feats["daily_vol"] = ret.rolling(P.vol_lb, min_periods=10).std().shift(sk)

    fwd = (close.shift(-(1 + HORIZON)) / close.shift(-1) - 1.0)
    parts = [m.stack(dropna=False).rename(n) for n, m in feats.items()]
    parts.append(fwd.stack(dropna=False).rename("fwd_ret"))
    df = pd.concat(parts, axis=1)
    df.index.set_names(["date", "symbol"], inplace=True)
    df = df.reset_index()
    level_cols = [f"vol_mean_{w}" for w in LEVEL_WINS]
    even_cols = [c for c in feats if c.startswith("vol_cv") or c.startswith("vol_hhi") or c.startswith("vol_maxshare")]
    return df, level_cols, even_cols


def monthly_ic(df, col, sign=1):
    out = []
    for m, g in df.assign(mo=df["date"].dt.to_period("M")).groupby("mo"):
        ics = []
        for _, gd in g.groupby("date"):
            gd = gd[[col, "fwd_ret"]].dropna()
            if len(gd) >= 10 and gd[col].nunique() > 3:
                ic, _ = spearmanr(gd[col] * sign, gd["fwd_ret"]); ics.append(ic)
        out.append((str(m)[2:], np.mean(ics) if ics else np.nan))
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    panel = pn.load_panel(is_only=True)
    qv = pn.pivot(panel, "quote_volume")
    umask = pn.build_universe_mask(panel, qv, P.universe_n, P.liquidity_lb, P.min_age_days)
    df, level_cols, even_cols = build(panel)

    um = umask.stack().rename("u").reset_index(); um.columns = ["date", "symbol", "u"]
    df = df.merge(um, on=["date", "symbol"], how="left")
    df = df[df["u"].fillna(False) & df["fwd_ret"].notna()].copy()
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_convert(None)
    print(f"rows={len(df):,}  dates={df['date'].nunique()}")

    all_cols = level_cols + even_cols + ["vol_trend_7_90", "daily_vol"]
    print("\n=== univariate rank-IC vs forward return (mean per-date Spearman) ===")
    ic_tab = {}
    for c in all_cols:
        ic, t, n = rank_ic(df.rename(columns={c: "pred"})[["date", "pred", "fwd_ret"]], "pred")
        ic_tab[c] = ic
        print(f"  {c:18s}  IC={ic:+.4f}  t={t:+.2f}")

    # correlation of evenness metrics with daily_vol (is this just volatility?)
    print("\n=== corr(feature, daily_vol) — high => just re-discovering volatility ===")
    for c in even_cols + ["vol_trend_7_90"]:
        cc = df[[c, "daily_vol"]].dropna()
        r = cc[c].corr(cc["daily_vol"]) if len(cc) > 50 else np.nan
        print(f"  {c:18s}  corr={r:+.3f}")

    # incremental IC: residualize best evenness metric on daily_vol, re-IC
    best_even = max(even_cols, key=lambda c: abs(ic_tab[c]))
    print(f"\n=== incremental IC of best evenness ({best_even}) after removing daily_vol ===")
    d2 = df[[best_even, "daily_vol", "fwd_ret", "date"]].dropna().copy()
    # cross-sectional residual per date
    def _resid(g):
        x = g["daily_vol"].values; y = g[best_even].values
        if len(g) < 10 or np.std(x) == 0:
            g["resid"] = np.nan; return g
        b = np.polyfit(x, y, 1); g["resid"] = y - (b[0] * x + b[1]); return g
    d2 = d2.groupby("date", group_keys=False).apply(_resid)
    ic, t, n = rank_ic(d2.rename(columns={"resid": "pred"})[["date", "pred", "fwd_ret"]], "pred")
    print(f"  residual {best_even} IC={ic:+.4f}  t={t:+.2f}  (raw was {ic_tab[best_even]:+.4f})")

    # monthly stability for level + best evenness
    print("\n=== monthly IC stability ===")
    for c in ["vol_mean_30", best_even, "daily_vol"]:
        mi = monthly_ic(df, c)
        print(f"  {c:14s} " + " ".join(f"{m}:{v:+.2f}" for m, v in mi))

    # median forward return: even vs spiky (top/bottom half by best evenness, per date)
    print(f"\n=== median forward return split by {best_even} (per-date halves) ===")
    dd = df.dropna(subset=[best_even, "fwd_ret"]).copy()
    dd["r"] = dd.groupby("date")[best_even].rank(pct=True)
    even_half = dd[dd["r"] < 0.5]["fwd_ret"].median()   # low hhi/cv/maxshare = even
    spiky_half = dd[dd["r"] >= 0.5]["fwd_ret"].median()
    print(f"  EVEN volume (low {best_even}): median fwd={even_half*100:+.2f}%   "
          f"SPIKY (high): median fwd={spiky_half*100:+.2f}%")

    pd.Series(ic_tab).to_frame("ic").to_parquet(OUT / "volume_study_ic.parquet")
    print(f"\nwrote {OUT / 'volume_study_ic.parquet'}")


if __name__ == "__main__":
    main()
