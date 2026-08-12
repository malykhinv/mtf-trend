"""Lift the EXECUTABLE (top-50 liquid) rank-weighted book toward a confident, year-
uniform edge (§45, §47, §86). The top-100 book was Sharpe ~1.5 but capacity-inflated;
on top-50 it fell to ~0.5 with 2024 fragile. Levers tried here (no retraining):

  * signal smoothing   — rolling-mean the score before ranking (less noise / turnover);
  * weight hysteresis   — blend new weights with the prior book (less turnover);
  * rebalance step      — 5/10/15d holding (cheap proxy for a longer horizon);
  * vol-neutral toggle  — remove the low-vol tilt on the liquid subset.

Reports per-year net Sharpe + turnover so we can see which config makes 2024 net-positive
on the tradeable universe. Run: python -m anomaly_science.strategy.prl.research.liquid_book
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research import feature_pool as fpm
from anomaly_science.strategy.prl.research import monetize as mz
from anomaly_science.strategy.prl.research.policy import PRIMARY
from anomaly_science.strategy.prl.research.run_coarse import _warmup

OUT = Path(".output/results/prl_coarse")


def book(score, open_, umask, p, step, smooth=1, hyst=0.0, cost_mult=1.0):
    """Realized dollar-neutral rank-weighted book with smoothing + hysteresis + step."""
    idx = score.index
    if smooth > 1:
        score = score.rolling(smooth, min_periods=1).mean()
    side_bps = (p.fee_bps + p.half_spread_bps + p.base_slippage_bps) * p.cost_multiplier * cost_mult
    prev_w = pd.Series(dtype=float)
    out, turns = {}, []
    for ri in range(_warmup(p), len(idx) - step - 1, step):
        ei, xi = ri + 1, 1 + ri + step
        if xi >= len(idx):
            break
        s = score.iloc[ri].where(umask.iloc[ri]).dropna()
        o_in = open_.iloc[ei].reindex(s.index); o_out = open_.iloc[xi].reindex(s.index)
        ok = (o_in > 0) & (o_out > 0) & o_in.notna() & o_out.notna()
        s = s[ok]
        if len(s) < max(p.min_xs, 15):
            continue
        r = s.rank(pct=True); w = r - r.mean(); w = w / w.abs().sum()
        if hyst > 0 and len(prev_w):
            w = (1 - hyst) * w + hyst * prev_w.reindex(w.index).fillna(0.0)
            w = w / w.abs().sum()
        fwd = (o_out[ok] / o_in[ok] - 1.0)
        turn = float((w.subtract(prev_w, fill_value=0.0)).abs().sum())
        turns.append(turn)
        out[idx[ri]] = float((w * fwd).sum()) - turn * side_bps / 1e4
        prev_w = w
    return pd.Series(out).sort_index(), (np.mean(turns) if turns else np.nan)


def _year_flags(ret, step):
    r = ret.copy(); r.index = pd.to_datetime(r.index)
    ys = {yr: g.sum() for yr, g in r.groupby(r.index.year)}
    allsh = r.mean() / r.std() * np.sqrt(252 / step) if r.std() > 0 else np.nan
    return ys, allsh, all(v > 0 for v in ys.values())


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    p50 = replace(PRIMARY, universe_n=50)
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    qv = pn.pivot(panel, "quote_volume")
    umask = pn.build_universe_mask(panel, qv, p50.universe_n, p50.liquidity_lb, p50.min_age_days)
    m = fx.build_coarse(panel, umask, p50)
    close, open_ = m["close"], pn.pivot(panel, "open").reindex_like(m["close"])
    umask = m["umask"]
    oof = pd.read_parquet(OUT / "oof_scores_prl.parquet")
    oof["date"] = pd.to_datetime(oof["date"], utc=True)
    score = oof.pivot(index="date", columns="symbol", values="score_linear").reindex(
        index=close.index, columns=close.columns)

    print("=== top-50 liquid rank-weighted book: lever sweep (cost x1) ===")
    print(f"{'step smooth hyst':22s} {'2023':>7} {'2024':>7} {'2025':>7} {'ALLsh':>6} {'turn':>5}  all>0")
    rows = []
    for step in (5, 10, 15):
        for smooth in (1, 5, 10):
            for hyst in (0.0, 0.5):
                r, turn = book(score, open_, umask, p50, step, smooth, hyst, cost_mult=1.0)
                ys, allsh, allpos = _year_flags(r, step)
                tag = f"s{step} sm{smooth} h{hyst:g}"
                print(f"{tag:22s} {ys.get(2023,0)*100:+6.1f}% {ys.get(2024,0)*100:+6.1f}% "
                      f"{ys.get(2025,0)*100:+6.1f}% {allsh:+6.2f} {turn:5.2f}  {'YES' if allpos else '-'}")
                rows.append({"step": step, "smooth": smooth, "hyst": hyst, "all_sharpe": allsh,
                             "y2023": ys.get(2023, np.nan), "y2024": ys.get(2024, np.nan),
                             "y2025": ys.get(2025, np.nan), "turnover": turn, "all_pos": allpos})
    df = pd.DataFrame(rows)
    df.to_parquet(OUT / "liquid_book_sweep.parquet")
    best = df[df["all_pos"]].sort_values("all_sharpe", ascending=False).head(3)
    print("\nbest all-years-positive configs:")
    print(best.to_string(index=False) if len(best) else "  (none all-positive)")


if __name__ == "__main__":
    main()
