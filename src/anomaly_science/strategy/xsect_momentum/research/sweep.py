"""Plateau sweep on IS for the beta-neutral quality book (user 2026-08-04).

Lesson from run_strategy: any net-directional book in the all-bear IS is dominated
by short beta (random shorts beat the real score). So the sweep judges ONLY the
dollar-neutral market-neutral book, and only credits a cell where the REAL score
beats its own SHUFFLED-score twin. We look for a *plateau* — a contiguous region
where neighbours all clear the bar — not a single peak.

Per cell we record: real Sharpe, three independently shuffled controls, delta,
bootstrap Q05, and the frozen risk gates. Fixed-percent stops are deliberately
excluded: they are not causally simulatable from daily bars and project policy
requires physical stops to use point-in-time structural anchors. Never touches OOS.

Run: python -m anomaly_science.strategy.xsect_momentum.research.sweep
"""

from __future__ import annotations

import itertools
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import backtest as bt
from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research import score as sc
from anomaly_science.strategy.xsect_momentum.research.gates import block_bootstrap_sharpe, evaluate_gates, max_drawdown
from anomaly_science.strategy.xsect_momentum.research.policy import PRIMARY
from anomaly_science.strategy.xsect_momentum.research.run_strategy import _shuffle_within_date

OUT = Path(".output/results/xsect_momentum")

GRID = {
    "k": [5, 10, 15, 20],
    "rebalance_days": [3, 7, 14],
    "max_weight": [0.10, 0.20],
    "universe_n": [30, 50, 75],
}

SHUFFLE_SEEDS = (11, 29, 47)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    corrected_path = OUT / "sweep_market_neutral_corrected_v2.parquet"
    panel = pn.load_panel(is_only=True)
    close = pn.pivot(panel, "close"); open_ = pn.pivot(panel, "open"); qv = pn.pivot(panel, "quote_volume")

    # cache universe masks + scores per universe_n (expensive part)
    cache: dict[int, tuple] = {}
    for un in GRID["universe_n"]:
        um = pn.build_universe_mask(panel, qv, un, PRIMARY.liquidity_lb, PRIMARY.min_age_days)
        qs = sc.quality_score(panel, replace(PRIMARY, universe_n=un), um)
        cache[un] = (um, qs, [_shuffle_within_date(qs, seed) for seed in SHUFFLE_SEEDS])

    key_cols = ["k", "rebalance_days", "max_weight", "universe_n"]
    if corrected_path.exists():
        prior = pd.read_parquet(corrected_path)
        rows = prior.to_dict("records") if set(key_cols).issubset(prior.columns) else []
    else:
        rows = []
    completed = {tuple(row[c] for c in key_cols) for row in rows}
    combos = list(itertools.product(GRID["k"], GRID["rebalance_days"], GRID["max_weight"], GRID["universe_n"]))
    print(f"sweeping {len(combos)} no-stop market-neutral cells (real vs {len(SHUFFLE_SEEDS)} shuffled seeds); "
          f"resuming after {len(completed)} ...", flush=True)
    for k, reb, mw, un in combos:
        key = (k, reb, mw, un)
        if key in completed:
            continue
        um, qs, shuffled_scores = cache[un]
        p = replace(PRIMARY, top_k=k, n_short=k, rebalance_days=reb, max_weight=mw,
                    universe_n=un, direction_mode="market_neutral", stop_loss_pct=0.0)
        real = bt.run_backtest(panel, close, open_, qv, um, qs, bt.sel_market_neutral, p, bidirectional=True)
        if real.meta["bankrupt"]:
            shuffled_sharpes = [np.nan] * len(SHUFFLE_SEEDS)
        else:
            shuffled_sharpes = []
            for qs_sh in shuffled_scores:
                shuf = bt.run_backtest(panel, close, open_, qv, um, qs_sh, bt.sel_market_neutral, p, bidirectional=True)
                shuffled_sharpes.append(evaluate_gates(shuf).values["sharpe"])
        shuf_mean = float(np.mean(shuffled_sharpes)) if np.isfinite(shuffled_sharpes).any() else np.nan
        shuf_max = float(np.max(shuffled_sharpes)) if np.isfinite(shuffled_sharpes).any() else np.nan
        gr = evaluate_gates(real)
        med, q05 = block_bootstrap_sharpe(real.daily_ret, block=reb, n=800)
        v = gr.values
        rows.append({
            "k": k, "rebalance_days": reb, "max_weight": mw, "universe_n": un,
            "config_id": p.config_id(),
            "is_start": panel["date"].min(), "is_end": panel["date"].max(),
            "source_symbols": int(panel["symbol"].nunique()),
            "bankrupt": bool(real.meta["bankrupt"]),
            "forced_exit_count": int(real.meta["forced_exit_count"]),
            "execution_unavailable_count": int(real.meta["execution_unavailable_count"]),
            "real_sharpe": v["sharpe"], "shuf_sharpe_mean": shuf_mean,
            "shuf_sharpe_max": shuf_max,
            **{f"shuf_sharpe_seed_{seed}": value for seed, value in zip(SHUFFLE_SEEDS, shuffled_sharpes)},
            "delta_sharpe": v["sharpe"] - shuf_mean,
            "boot_q05": q05, "ret": v["total_return"], "maxDD": v["g7_max_drawdown"],
            "worst_day": v["g8_worst_day"], "g1_rem": v["g1_remaining_after_top40_drop"], "win": v["g6_win_rate"],
            "best_week": v["g4_best_week_share"], "n_trades": v["n_trades"],
            "mean_turnover": float(real.turnover.mean()),
            **{
                f"ret_{year}": float((1.0 + real.daily_ret[real.daily_ret.index.year == year]).prod() - 1.0)
                for year in (2023, 2024, 2025)
            },
            "G1": gr.passes["G1"], "G4": gr.passes["G4"], "G7": gr.passes["G7"], "G8": gr.passes["G8"],
        })
        pd.DataFrame(rows).to_parquet(corrected_path)
        completed.add(key)
        print(f"  completed {len(completed)}/{len(combos)}: k={k} reb={reb} max_w={mw} universe={un}", flush=True)
    df = pd.DataFrame(rows)
    df["beats_shuffled"] = df["delta_sharpe"] > 0.3
    df["robust"] = df["beats_shuffled"] & (df["boot_q05"] > 0) & df["G1"] & df["G7"] & df["G8"]
    df.to_parquet(corrected_path)

    print(f"\ncells beating shuffled (delta>0.3): {int(df['beats_shuffled'].sum())}/{len(df)}")
    print(f"cells with bootQ05>0:               {int((df['boot_q05']>0).sum())}/{len(df)}")
    print(f"cells passing G1 (top40-drop>0):    {int(df['G1'].sum())}/{len(df)}")
    print(f"cells passing G7 (DD<=15%):         {int(df['G7'].sum())}/{len(df)}")
    print(f"cells passing G8 (worstDay>=-8%):   {int(df['G8'].sum())}/{len(df)}")
    print(f"ROBUST cells (all of the above):    {int(df['robust'].sum())}/{len(df)}")

    print("\n--- top 12 by delta_sharpe (real minus shuffled) ---")
    cols = ["k", "rebalance_days", "max_weight", "universe_n", "real_sharpe", "shuf_sharpe_mean", "shuf_sharpe_max",
            "delta_sharpe", "boot_q05", "ret", "maxDD", "worst_day", "g1_rem", "win", "robust"]
    with pd.option_context("display.width", 200, "display.max_columns", 30):
        print(df.sort_values("delta_sharpe", ascending=False)[cols].head(12).to_string(index=False))

    if df["robust"].any():
        print("\n--- ROBUST region ---")
        print(df[df["robust"]][cols].to_string(index=False))
    else:
        print("\nNo robust cell: real edge over shuffled does not co-occur with positive bootstrap + risk gates.")
    print(f"\nwrote {corrected_path}")


if __name__ == "__main__":
    main()
