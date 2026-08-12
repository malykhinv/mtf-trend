"""PRL-COARSE-000 orchestrator (§32.1.1 coarse existence gate).

Measures, on the 2023-2026 multi-regime daily panel and IS window only:
  * primary: mean OOS-style cross-sectional Spearman IC of multi-horizon
    market-residual momentum vs the frozen-beta future residual return (§32);
  * dependence-aware block-bootstrap CI (§32.2);
  * economic co-primary: net-of-cost top-minus-bottom decile spread (§32.1.2);
  * placebo (shuffled score) (§70);
  * pre-registered power / MDE (§32.4);
  * §67 regime breakdown (bull / bear / sideways).

The reserved OOS tail (>= oos_start) is NEVER read: forward labels near the IS
boundary truncate to NaN and drop out (§6.3.2 embargo). This run touches no OOS.

Run:  python -m anomaly_science.strategy.prl.research.run_coarse
      python -m anomaly_science.strategy.prl.research.run_coarse --smoke 120
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research import ic as icmod
from anomaly_science.strategy.prl.research.policy import PRIMARY, PRLCoarsePolicy
from anomaly_science.strategy.prl.research.regime import regime_label

OUT = Path(".output/results/prl_coarse")


def _warmup(p: PRLCoarsePolicy) -> int:
    return max(max(p.mom_lbs) + p.skip + 2, p.beta_lb + 2,
              p.liquidity_lb + 2, p.min_age_days + 2)


def run(p: PRLCoarsePolicy, smoke: int = 0) -> dict:
    oos = pd.Timestamp(p.oos_start, tz="UTC")
    print(f"loading IS daily panel (< {oos.date()}, OOS reserved) ...")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    if smoke:
        keep = sorted(panel["symbol"].unique())[:smoke]
        panel = panel[panel["symbol"].isin(keep)]
    qv = pn.pivot(panel, "quote_volume")
    umask = pn.build_universe_mask(panel, qv, p.universe_n, p.liquidity_lb, p.min_age_days)
    print(f"  panel: {panel['symbol'].nunique()} symbols x {qv.shape[0]} days "
          f"({panel['date'].min().date()} -> {panel['date'].max().date()})  "
          f"median universe/day={int(umask.sum(axis=1).median())}")

    m = fx.build_coarse(panel, umask, p)
    score, label, close = m["score"], m["label"], m["close"]

    warmup = _warmup(p)
    eval_dates = score.index[warmup:]
    ic = icmod.daily_ic(score, label, p, eval_dates)
    summ = icmod.summarize_ic(ic)
    boot = icmod.block_bootstrap_mean(ic, p.boot_block, p.boot_n, p.seed)
    dec = icmod.decile_spread(score, label, p, warmup)
    plac = icmod.placebo_ic(score, label, p, eval_dates)
    reb_dates = score.index[warmup::p.rebalance_days]
    reb_ic = icmod.daily_ic(score, label, p, reb_dates)
    pwr = icmod.power_mde(reb_ic, p)

    # §67 regime breakdown
    reg = regime_label(close, p)
    reg_rows = []
    for name in ("bull", "bear", "sideways"):
        sub = ic[reg.reindex(ic.index) == name].dropna()
        if len(sub):
            reg_rows.append({"regime": name, "n_days": len(sub),
                             "ic_mean": float(sub.mean()),
                             "ic_pos_share": float((sub > 0).mean())})

    # --- report -----------------------------------------------------------
    print(f"\n=== PRL-COARSE-000  {p.config_id()} ===")
    print(f"  factor={p.market_factor}  mom_lbs={p.mom_lbs}  fwd={p.fwd_horizon}d  "
          f"beta_lb={p.beta_lb}(shrink {p.beta_shrink})  universe_n={p.universe_n}")
    print(f"\n[PRIMARY IC]  mean={summ['ic_mean']:+.4f}  median={summ['ic_median']:+.4f}  "
          f"t={summ['t_stat']:.2f}  ICIR={summ['icir']:.3f}")
    print(f"   days={summ['n_days']}  IC>0 days={summ['ic_pos_share']:.2f}  "
          f"weeks={summ['n_weeks']}  IC>0 weeks={summ['week_pos_share']:.2f}")
    print(f"[block-bootstrap mean IC]  {boot['boot_mean']:+.4f}  "
          f"90% CI [{boot['boot_q05']:+.4f}, {boot['boot_q95']:+.4f}]  "
          f"(block={p.boot_block}d, n={p.boot_n})")
    print(f"[placebo shuffled IC]  mean={plac['placebo_ic_mean']:+.4f}  t={plac['placebo_ic_t']:.2f}")
    print(f"[power/MDE]  n_eff={pwr['n_eff']}  reb_ic_std={pwr['reb_ic_std']:.4f}  "
          f"MDE_meanIC={pwr['mde_mean_ic']:+.4f}  (power {pwr['power_target']}, alpha {pwr['power_alpha']})")
    print(f"[econ decile]  n={dec['n_reb']}  MEAN gross={dec['gross_spread_mean']*100:+.3f}%"
          f"(t={dec['spread_t_mean']:.2f})  MEDIAN gross={dec['gross_spread_median']*100:+.3f}%"
          f"(t={dec['spread_t_median']:.2f})  net_median={dec['net_spread_median']*100:+.3f}%/hold  "
          f"med>0 reb={dec['med_pos_reb_share']:.2f}")
    if dec["gross_spread_mean"] > 2 * max(dec["gross_spread_median"], 1e-9):
        print("   !! MEAN >> MEDIAN -> fat-tail illusion: mean spread driven by a few "
              "explosive top-decile names, not a typical-name edge (sec 31/49).")
    print("[regime IC]  " + "  ".join(
        f"{r['regime']}:{r['ic_mean']:+.4f}(n{r['n_days']},pos{r['ic_pos_share']:.2f})"
        for r in reg_rows))

    # verdict hints (not a substitute for freeze + holdout). Econ gate uses the
    # ROBUST median spread, so the fat-tail illusion cannot pass it.
    stat_ok = (boot["boot_q05"] is not None and boot["boot_q05"] > 0
               and summ["ic_mean"] > pwr["mde_mean_ic"])
    # robust median spread must be net-positive AND significant AND positive in a
    # majority of holding periods — a formal-sign-only pass cannot clear this.
    econ_ok = (dec["net_spread_median"] is not None and dec["net_spread_median"] > 0
               and (dec["spread_t_median"] or 0) > 1.64
               and (dec["med_pos_reb_share"] or 0) > 0.5)
    print(f"\n  stat_gate(IC CI>0 & > MDE): {'PASS' if stat_ok else 'fail'}   "
          f"econ_gate(net decile>0): {'PASS' if econ_ok else 'fail'}   "
          f"-> {'candidate' if stat_ok and econ_ok else ('structure-not-tradable' if stat_ok else 'not-confirmed')}")

    OUT.mkdir(parents=True, exist_ok=True)
    tag = p.config_id()
    ic.rename("ic").to_frame().to_parquet(OUT / f"daily_ic_{tag}.parquet")
    dec_row = {k: v for k, v in dec.items() if not isinstance(v, np.ndarray)}
    pd.DataFrame([{**p.as_row(), **summ, **boot, **plac, **pwr, **dec_row,
                   "stat_gate": stat_ok, "econ_gate": econ_ok}]
                 ).to_parquet(OUT / f"summary_{tag}.parquet")
    if reg_rows:
        pd.DataFrame(reg_rows).to_parquet(OUT / f"regime_ic_{tag}.parquet")
    print(f"\nwrote {OUT}/summary_{tag}.parquet")
    return {"summary": summ, "boot": boot, "decile": dec, "placebo": plac, "power": pwr}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", type=int, default=0, help="use only first N symbols")
    ap.add_argument("--horizon", type=int, default=0, help="override fwd_horizon (0=policy)")
    ap.add_argument("--market-factor", default="", help="trimmed_mean|median|mean|mean_loo")
    ap.add_argument("--universe", type=int, default=0, help="override universe_n")
    args = ap.parse_args()

    p = PRIMARY
    changes = {}
    if args.horizon:
        changes["fwd_horizon"] = args.horizon
    if args.market_factor:
        changes["market_factor"] = args.market_factor
    if args.universe:
        changes["universe_n"] = args.universe
    if changes:
        p = replace(p, **changes)
    run(p, smoke=args.smoke)


if __name__ == "__main__":
    main()
