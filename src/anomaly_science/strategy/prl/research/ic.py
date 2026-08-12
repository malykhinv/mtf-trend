"""Primary statistical objects for PRL-COARSE-000 (§32): cross-sectional rank IC,
dependence-aware CI, decile spread (gross + net), placebo and power/MDE.

All inputs are date x symbol matrices already restricted to the point-in-time
universe (NaN elsewhere). Nothing here reads price directly — only the score and
the frozen-beta residual label — so leak-safety lives entirely in `factors.py`.
"""

from __future__ import annotations

from statistics import NormalDist

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.policy import PRLCoarsePolicy


def _spearman(s: np.ndarray, l: np.ndarray) -> float:
    sr = pd.Series(s).rank().to_numpy()
    lr = pd.Series(l).rank().to_numpy()
    if sr.std() == 0 or lr.std() == 0:
        return np.nan
    return float(np.corrcoef(sr, lr)[0, 1])


def daily_ic(score: pd.DataFrame, label: pd.DataFrame, p: PRLCoarsePolicy,
             dates: pd.Index | None = None) -> pd.Series:
    """Per-date cross-sectional Spearman IC(score, future residual). NaN if the
    cross-section has < min_xs jointly finite names."""
    idx = dates if dates is not None else score.index
    out = {}
    for d in idx:
        s = score.loc[d].to_numpy(dtype=float)
        l = label.loc[d].to_numpy(dtype=float)
        m = np.isfinite(s) & np.isfinite(l)
        if m.sum() >= p.min_xs:
            out[d] = _spearman(s[m], l[m])
    return pd.Series(out, dtype=float).sort_index()


def summarize_ic(ic: pd.Series) -> dict:
    ic = ic.dropna()
    n = int(len(ic))
    mean = float(ic.mean()) if n else np.nan
    std = float(ic.std(ddof=1)) if n > 1 else np.nan
    tstat = float(mean / (std / np.sqrt(n))) if n > 1 and std > 0 else np.nan
    wk = ic.resample("W").mean().dropna() if n else ic
    return {
        "n_days": n,
        "ic_mean": mean,
        "ic_median": float(ic.median()) if n else np.nan,
        "ic_std": std,
        "icir": float(mean / std) if n > 1 and std > 0 else np.nan,
        "t_stat": tstat,
        "ic_pos_share": float((ic > 0).mean()) if n else np.nan,
        "week_pos_share": float((wk > 0).mean()) if len(wk) else np.nan,
        "n_weeks": int(len(wk)),
    }


def block_bootstrap_mean(ic: pd.Series, block: int, n_boot: int, seed: int) -> dict:
    """Moving-block bootstrap CI for the mean daily IC (§32.2). Handles the serial
    dependence that naive IID errors would ignore."""
    x = ic.dropna().to_numpy()
    n = len(x)
    if n < block + 1:
        return {"boot_mean": np.nan, "boot_q05": np.nan, "boot_q95": np.nan}
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block))
    starts_pool = np.arange(0, n - block + 1)
    means = np.empty(n_boot)
    for b in range(n_boot):
        starts = rng.choice(starts_pool, size=n_blocks, replace=True)
        sample = np.concatenate([x[s:s + block] for s in starts])[:n]
        means[b] = sample.mean()
    return {
        "boot_mean": float(means.mean()),
        "boot_q05": float(np.percentile(means, 5)),
        "boot_q95": float(np.percentile(means, 95)),
    }


def _reb_dates(index: pd.Index, warmup: int, step: int) -> list:
    return list(index[warmup::step])


def decile_spread(score: pd.DataFrame, label: pd.DataFrame, p: PRLCoarsePolicy,
                  warmup: int) -> dict:
    """Non-overlapping top-minus-bottom decile forward-residual spread.

    Reports BOTH a mean-based and a within-decile MEDIAN-based spread. A large gap
    (mean >> median) is the fat-tail illusion (§31, §49): a few explosive names in
    the top decile inflate the mean while the typical name shows no edge. The
    economic gate is taken on the robust median spread, consistent with the
    rank-IC primary (§32.1.2, §54, §65.1).
    """
    dates = _reb_dates(score.index, warmup, p.rebalance_days)
    mean_spreads, med_spreads = [], []
    tops, bots = [], []
    for d in dates:
        s = score.loc[d].dropna()
        l = label.loc[d].reindex(s.index).dropna()
        s = s.reindex(l.index)
        if len(l) < max(p.min_xs, 10):
            continue
        q = max(1, len(l) // 10)
        order = s.sort_values(ascending=False)
        top = l.reindex(order.index[:q]); bot = l.reindex(order.index[-q:])
        mean_spreads.append(top.mean() - bot.mean())
        med_spreads.append(top.median() - bot.median())
        tops.append(top.mean()); bots.append(bot.mean())
    mean_spreads = np.array(mean_spreads, dtype=float)
    med_spreads = np.array(med_spreads, dtype=float)
    n = len(mean_spreads)
    side = (p.fee_bps + p.half_spread_bps + p.base_slippage_bps) * p.cost_multiplier / 1e4
    roundtrip = 4.0 * side  # long open+close + short open+close, per holding period
    gross_mean = float(np.nanmean(mean_spreads)) if n else np.nan
    gross_med = float(np.nanmean(med_spreads)) if n else np.nan
    net_med = float(gross_med - roundtrip) if n else np.nan
    t_mean = (float(np.nanmean(mean_spreads) / (np.nanstd(mean_spreads) / np.sqrt(n)))
              if n > 2 and np.nanstd(mean_spreads) > 0 else np.nan)
    t_med = (float(np.nanmean(med_spreads) / (np.nanstd(med_spreads) / np.sqrt(n)))
             if n > 2 and np.nanstd(med_spreads) > 0 else np.nan)
    return {
        "n_reb": n,
        "gross_spread_mean": gross_mean,
        "gross_spread_median": gross_med,
        "net_spread_median": net_med,          # robust econ gate
        "net_spread_mean": float(gross_mean - roundtrip) if n else np.nan,
        "roundtrip_cost": float(roundtrip),
        "spread_t_mean": t_mean,
        "spread_t_median": t_med,
        "med_pos_reb_share": float(np.nanmean(med_spreads > 0)) if n else np.nan,
        "top_mean": float(np.nanmean(tops)) if n else np.nan,
        "bot_mean": float(np.nanmean(bots)) if n else np.nan,
    }


def placebo_ic(score: pd.DataFrame, label: pd.DataFrame, p: PRLCoarsePolicy,
               dates: pd.Index) -> dict:
    """Shuffle the score within each date -> the IC must collapse to ~0 (§70)."""
    rng = np.random.default_rng(p.seed + 1)
    shuffled = score.copy()
    arr = shuffled.to_numpy()
    for i in range(arr.shape[0]):
        row = arr[i]
        fin = np.where(np.isfinite(row))[0]
        if fin.size > 1:
            row[fin] = row[rng.permutation(fin)]
        arr[i] = row
    shuffled = pd.DataFrame(arr, index=score.index, columns=score.columns)
    ic = daily_ic(shuffled, label, p, dates)
    return {"placebo_ic_mean": float(ic.mean()) if len(ic) else np.nan,
            "placebo_ic_t": summarize_ic(ic)["t_stat"]}


def power_mde(reb_ic: pd.Series, p: PRLCoarsePolicy) -> dict:
    """Pre-registered minimum detectable mean IC at target power (§32.4).

    Uses the NON-overlapping rebalance-date IC series for an honest effective N.
    If MDE exceeds a plausible crypto XS effect, a null is 'underpowered', not
    'no effect'.
    """
    x = reb_ic.dropna().to_numpy()
    n_eff = len(x)
    std = float(np.std(x, ddof=1)) if n_eff > 1 else np.nan
    z_a = NormalDist().inv_cdf(1 - p.power_alpha)
    z_p = NormalDist().inv_cdf(p.power_target)
    mde = float((z_a + z_p) * std / np.sqrt(n_eff)) if n_eff > 1 and std > 0 else np.nan
    return {"n_eff": n_eff, "reb_ic_std": std, "mde_mean_ic": mde,
            "power_alpha": p.power_alpha, "power_target": p.power_target}
