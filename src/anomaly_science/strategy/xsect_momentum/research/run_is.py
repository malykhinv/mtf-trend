"""In-sample orchestration (§9 steps 2-4): primary spec vs controls vs gates.

Run:  python -m anomaly_science.strategy.xsect_momentum.research.run_is
Reads only IS data (< 2026-01-01). The frozen OOS tail is never touched here.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import backtest as bt
from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.gates import (
    block_bootstrap_sharpe,
    evaluate_gates,
)
from anomaly_science.strategy.xsect_momentum.research.policy import PRIMARY, XSectMomentumPolicy

OUT = Path(".output/results/xsect_momentum")


def _matrices(panel: pd.DataFrame):
    close = pn.pivot(panel, "close")
    open_ = pn.pivot(panel, "open")
    qv = pn.pivot(panel, "quote_volume")
    return close, open_, qv


def decile_spread(close: pd.DataFrame, scores: pd.DataFrame, umask: pd.DataFrame, p: XSectMomentumPolicy) -> dict:
    """Beta-neutral-ish long-short decile spread diagnostic (§1 estimand).

    Not a tradeable book — a gross forward-return spread of top vs bottom decile,
    equally weighted, one rebalance horizon ahead. Answers 'is the rank informative?'
    """
    dates = close.index
    fwd = close.pct_change(p.rebalance_days, fill_method=None).shift(-p.rebalance_days)  # forward horizon return
    warmup = max(p.long_lb + p.skip + 5, p.liquidity_lb + 5)
    top_r, bot_r, mkt_r = [], [], []
    for ri in range(warmup, len(dates) - p.rebalance_days - 1, p.rebalance_days):
        uni = umask.columns[umask.iloc[ri].to_numpy()]
        s = scores.iloc[ri].reindex(uni).dropna()
        f = fwd.iloc[ri].reindex(s.index).dropna()
        s = s.reindex(f.index)
        if len(f) < 10:
            continue
        q = max(1, len(f) // 10)
        order = s.sort_values(ascending=False)
        top = f.reindex(order.index[:q]).mean()
        bot = f.reindex(order.index[-q:]).mean()
        top_r.append(top); bot_r.append(bot); mkt_r.append(f.mean())
    top_r, bot_r, mkt_r = map(np.array, (top_r, bot_r, mkt_r))
    spread = top_r - bot_r
    return {
        "n": int(len(spread)),
        "spread_mean_per_reb": float(np.nanmean(spread)) if len(spread) else np.nan,
        "spread_t": float(np.nanmean(spread) / (np.nanstd(spread) / np.sqrt(len(spread)))) if len(spread) > 2 and np.nanstd(spread) > 0 else np.nan,
        "top_minus_market": float(np.nanmean(top_r - mkt_r)) if len(top_r) else np.nan,
        "long_beat_market_share": float(np.nanmean(top_r > mkt_r)) if len(top_r) else np.nan,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebalance", type=int, default=0, help="override rebalance_days (0=policy)")
    ap.add_argument("--cost-mult", type=float, default=1.0)
    ap.add_argument("--delay", type=int, default=1)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    print("loading IS daily panel ...")
    panel = pn.load_panel(is_only=True)
    close, open_, qv = _matrices(panel)
    print(f"  panel: {panel['symbol'].nunique()} symbols x {close.shape[0]} days "
          f"({close.index.min().date()} -> {close.index.max().date()})")

    p = PRIMARY
    if args.rebalance:
        p = XSectMomentumPolicy(rebalance_days=args.rebalance, cost_multiplier=args.cost_mult, execution_delay_days=args.delay)
    else:
        p = XSectMomentumPolicy(cost_multiplier=args.cost_mult, execution_delay_days=args.delay)

    umask = pn.build_universe_mask(panel, qv, p.universe_n, p.liquidity_lb, p.min_age_days)
    mom = bt.momentum_score(close, p)
    ts = bt.ts_momentum_score(close, p)
    # shuffled-rank control: permute momentum scores within each date
    rng = np.random.default_rng(7)
    shuffled = mom.copy()
    for i in range(len(shuffled)):
        row = shuffled.iloc[i].to_numpy()
        finite = np.isfinite(row)
        idx = np.where(finite)[0]
        perm = rng.permutation(idx)
        row2 = row.copy()
        row2[idx] = row[perm]
        shuffled.iloc[i] = row2

    runs = {
        "PRIMARY_momentum": (bt.sel_momentum, mom, True),
        "blind_equal_universe": (bt.sel_blind_equal, mom, True),
        "shuffled_rank": (bt.sel_momentum, shuffled, True),
        "ts_momentum": (bt.sel_ts_momentum, ts, True),
        "no_regime_momentum": (bt.sel_momentum, mom, False),
    }

    print(f"\n=== rebalance={p.rebalance_days}d  cost_mult={p.cost_multiplier}x  delay={p.execution_delay_days} ===")
    rows = []
    for name, (sel, sc, use_reg) in runs.items():
        res = bt.run_backtest(panel, close, open_, qv, umask, sc, sel, p, use_regime=use_reg, seed=0)
        gr = evaluate_gates(res)
        med_sh, q05_sh = block_bootstrap_sharpe(res.daily_ret, block=p.rebalance_days, n=1500)
        v = gr.values
        rows.append({"run": name, **v, "boot_med_sharpe": med_sh, "boot_q05_sharpe": q05_sh,
                     "gates_pass": "".join("1" if gr.passes[g] else "0" for g in sorted(gr.passes))})
        print(f"\n[{name}]  final=${v['final_equity']:.0f}  ret={v['total_return']*100:+.1f}%  "
              f"sharpe={v['sharpe']:.2f}  bootQ05={q05_sh:.2f}  trades={v['n_trades']}")
        print(f"   G1 top40-drop remain={v['g1_remaining_after_top40_drop']:+.1f}  "
              f"G2 posDay={v['g2_positive_day_share']:.2f}  G6 win={v['g6_win_rate']:.2f}  "
              f"G7 maxDD={v['g7_max_drawdown']*100:.1f}%")
        print(f"   conc: bestDay={v['g3_best_day_share']}  bestWk={v['g4_best_week_share']}  bestMo={v['g5_best_month_share']}")
        print(f"   gates(G1..G7)={''.join('P' if gr.passes[g] else '.' for g in sorted(gr.passes))}  ALL={'PASS' if gr.all_pass else 'fail'}")

    # long-short spread diagnostic (§1)
    sp = decile_spread(close, mom, umask, p)
    print(f"\n[decile_spread diagnostic]  n={sp['n']}  spread/reb={sp['spread_mean_per_reb']*100:+.2f}%  "
          f"t={sp['spread_t']:.2f}  top-mkt={sp['top_minus_market']*100:+.2f}%  longBeatMkt={sp['long_beat_market_share']:.2f}")

    df = pd.DataFrame(rows)
    tag = f"reb{p.rebalance_days}_cost{p.cost_multiplier:g}_delay{p.execution_delay_days}"
    df.to_parquet(OUT / f"is_summary_{tag}.parquet")
    print(f"\nwrote {OUT / f'is_summary_{tag}.parquet'}")


if __name__ == "__main__":
    main()
