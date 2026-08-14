"""Bidirectional / regime-adaptive quality strategy on IS (user 2026-08-04).

Tradeable expressions of the IS-stable quality/anti-pump score, plus the controls
that separate a REAL cross-sectional edge from a mere net-short beta bet in a bear:

  * market_neutral      long top-score / short bottom-score, dollar-neutral
  * regime_adaptive     risk-on -> long top ; risk-off -> short bottom ; neutral -> cash
  * short_only          short bottom-score (the pump/high-vol names)
  * quality_long        long top-score only
CONTROLS
  * shuffled_score      same book, score permuted within date  -> must die (~0)
  * blind_short         short RANDOM k (net short)             -> isolates beta vs score
  * mn_shuffled         market-neutral on shuffled score       -> must die (~0)

Never touches OOS. Run:
  python -m anomaly_science.strategy.xsect_momentum.research.run_strategy
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import backtest as bt
from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research import score as sc
from anomaly_science.strategy.xsect_momentum.research.gates import block_bootstrap_sharpe, evaluate_gates
from anomaly_science.strategy.xsect_momentum.research.policy import PRIMARY, XSectMomentumPolicy

OUT = Path(".output/results/xsect_momentum")


def _shuffle_within_date(mat: pd.DataFrame, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    out = mat.copy()
    arr = out.to_numpy()
    for i in range(arr.shape[0]):
        row = arr[i]
        idx = np.where(np.isfinite(row))[0]
        if len(idx) > 1:
            row[idx] = row[rng.permutation(idx)]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebalance", type=int, default=7)
    ap.add_argument("--cost-mult", type=float, default=1.0)
    ap.add_argument("--regime-band", type=float, default=0.0)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    panel = pn.load_panel(is_only=True)
    close = pn.pivot(panel, "close"); open_ = pn.pivot(panel, "open"); qv = pn.pivot(panel, "quote_volume")
    p0 = replace(PRIMARY, rebalance_days=args.rebalance, cost_multiplier=args.cost_mult, regime_band=args.regime_band)
    umask = pn.build_universe_mask(panel, qv, p0.universe_n, p0.liquidity_lb, p0.min_age_days)
    qscore = sc.quality_score(panel, p0, umask)
    qscore_sh = _shuffle_within_date(qscore)
    rand_score = pd.DataFrame(np.random.default_rng(3).random(qscore.shape), index=qscore.index, columns=qscore.columns).where(umask)

    print(f"panel {panel['symbol'].nunique()} sym x {close.shape[0]}d  reb={p0.rebalance_days} cost={p0.cost_multiplier}x band={p0.regime_band}")

    variants = {
        "market_neutral":  (bt.sel_market_neutral,  replace(p0, direction_mode="market_neutral"),  qscore),
        "regime_adaptive": (bt.sel_regime_adaptive, replace(p0, direction_mode="regime_adaptive"), qscore),
        "short_only":      (bt.sel_short_only,      replace(p0, direction_mode="short_only"),      qscore),
        "quality_long":    (bt.sel_quality_long,    replace(p0, direction_mode="long_only"),       qscore),
        # controls
        "mn_shuffled":     (bt.sel_market_neutral,  replace(p0, direction_mode="market_neutral"),  qscore_sh),
        "short_shuffled":  (bt.sel_short_only,      replace(p0, direction_mode="short_only"),      qscore_sh),
        "blind_short_rand":(bt.sel_short_only,      replace(p0, direction_mode="short_only"),      rand_score),
    }

    rows = []
    daily_parts = []
    trade_parts = []
    for name, (sel, p, scores) in variants.items():
        res = bt.run_backtest(panel, close, open_, qv, umask, scores, sel, p, bidirectional=True, seed=0)
        gr = evaluate_gates(res)
        med, q05 = block_bootstrap_sharpe(res.daily_ret, block=p.rebalance_days, n=1500)
        v = gr.values
        rows.append({"variant": name, **v, "boot_med_sharpe": med, "boot_q05_sharpe": q05,
                     "gates": "".join("P" if gr.passes[g] else "." for g in sorted(gr.passes)), "all_pass": gr.all_pass})
        daily_parts.append(pd.DataFrame({
            "variant": name,
            "date": res.daily_ret.index,
            "daily_return": res.daily_ret.to_numpy(),
            "equity": res.equity.reindex(res.daily_ret.index).to_numpy(),
        }))
        trade_parts.append(res.trades.assign(variant=name))
        print(f"\n[{name}]  final=${v['final_equity']:.0f}  ret={v['total_return']*100:+.1f}%  sharpe={v['sharpe']:.2f}  "
              f"bootMed={med:.2f} Q05={q05:.2f}  trades={v['n_trades']}")
        print(f"   G1rem={v['g1_remaining_after_top40_drop']:+.0f}  G2posDay={v['g2_positive_day_share']:.2f}  "
              f"G6win={v['g6_win_rate']:.2f}  G7DD={v['g7_max_drawdown']*100:.1f}%  "
              f"bestWk={v['g4_best_week_share']}  gates(G1..G7)={rows[-1]['gates']} ALL={'PASS' if gr.all_pass else 'fail'}")

    df = pd.DataFrame(rows)
    tag = f"reb{p0.rebalance_days}_cost{p0.cost_multiplier:g}_band{p0.regime_band:g}"
    summary_path = OUT / f"strategy_corrected_{tag}.parquet"
    daily_path = OUT / f"strategy_corrected_{tag}_daily.parquet"
    trades_path = OUT / f"strategy_corrected_{tag}_trades.parquet"
    df.to_parquet(summary_path)
    pd.concat(daily_parts, ignore_index=True).to_parquet(daily_path)
    pd.concat(trade_parts, ignore_index=True).to_parquet(trades_path)
    print(f"\nwrote {summary_path}")
    print(f"wrote {daily_path}")
    print(f"wrote {trades_path}")


if __name__ == "__main__":
    main()
