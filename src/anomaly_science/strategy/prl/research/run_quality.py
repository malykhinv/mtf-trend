"""PRL-QUALITY-000 orchestrator (§14, §15, H2).

Two questions on the IS multi-regime daily panel (OOS reserved, as in COARSE-000):
  1. Does any residual path-quality feature carry standalone rank-IC vs the
     frozen-beta future residual return?
  2. Within momentum leaders, do high-quality (persistent, smooth) names beat
     low-quality (one-shot burst) names on MEDIAN future residual? (H2)

Run: python -m anomaly_science.strategy.prl.research.run_quality
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research import ic as icmod
from anomaly_science.strategy.prl.research import quality as ql
from anomaly_science.strategy.prl.research.policy import PRIMARY, PRLCoarsePolicy
from anomaly_science.strategy.prl.research.regime import regime_label
from anomaly_science.strategy.prl.research.run_coarse import _warmup

OUT = Path(".output/results/prl_coarse")


def run(p: PRLCoarsePolicy, q_lb: int = ql.QUALITY_LB, smoke: int = 0) -> None:
    oos = pd.Timestamp(p.oos_start, tz="UTC")
    print(f"loading IS daily panel (< {oos.date()}, OOS reserved) ...")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    if smoke:
        keep = sorted(panel["symbol"].unique())[:smoke]
        panel = panel[panel["symbol"].isin(keep)]
    qv = pn.pivot(panel, "quote_volume")
    umask = pn.build_universe_mask(panel, qv, p.universe_n, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, umask, p)
    eps, mom, label = m["eps"], m["score"], m["label"]
    umask = m["umask"]
    warmup = _warmup(p)
    eval_dates = eps.index[warmup:]

    feats = ql.quality_features(eps, umask, p, q_lb)
    comp = ql.quality_composite(feats, umask)

    print(f"\n=== PRL-QUALITY-000  base={p.config_id()}  q_lb={q_lb} ===")
    print(f"  panel: {panel['symbol'].nunique()} symbols  fwd={p.fwd_horizon}d\n")
    reg = regime_label(m["close"], p)
    print("[standalone quality-feature IC vs future residual]")
    rows = []
    ic_store = {}
    for name in ("path_eff", "frac_pos", "burst", "recent_shr", "composite"):
        feat = comp if name == "composite" else feats[name]
        ic = icmod.daily_ic(feat, label, p, eval_dates)
        ic_store[name] = ic
        s = icmod.summarize_ic(ic)
        boot = icmod.block_bootstrap_mean(ic, p.boot_block, p.boot_n, p.seed)
        plac = icmod.placebo_ic(feat, label, p, eval_dates)
        print(f"  {name:11s} IC={s['ic_mean']:+.4f} (t={s['t_stat']:+.2f})  "
              f"CI[{boot['boot_q05']:+.4f},{boot['boot_q95']:+.4f}]  "
              f"placebo={plac['placebo_ic_mean']:+.4f}  wk>0={s['week_pos_share']:.2f}")
        rows.append({"feature": name, **s, **boot, **plac})

    # regime robustness (§67): a real idiosyncratic-persistence edge must keep its
    # sign across regimes; a bull-only edge is likely a beta-residualization leak.
    print("\n[regime IC of key quality features]  (sign must be stable)")
    for name in ("frac_pos", "burst", "composite"):
        ic = ic_store[name]
        cells = []
        for rname in ("bull", "bear", "sideways"):
            sub = ic[reg.reindex(ic.index) == rname].dropna()
            if len(sub):
                cells.append(f"{rname}:{sub.mean():+.4f}(n{len(sub)})")
        print(f"  {name:11s} " + "  ".join(cells))

    print("\n[H2 conditional double-sort: within momentum leaders, hi-qual vs lo-qual]")
    ds = ql.conditional_double_sort(mom, comp, label, p, warmup)
    print(f"  n={ds['n_reb']}  hi_med={ds['hi_med']*100:+.3f}%  lo_med={ds['lo_med']*100:+.3f}%  "
          f"MEDIAN spread={ds['hiqual_minus_loqual_median']*100:+.3f}% (t={ds['t']:+.2f})  "
          f"net={ds['net']*100:+.3f}%  pos-reb={ds['pos_reb_share']:.2f}")
    h2 = ds["net"] is not None and ds["net"] > 0 and (ds["t"] or 0) > 1.64 and (ds["pos_reb_share"] or 0) > 0.5
    print(f"\n  H2 gate (hi-qual > lo-qual, robust+significant): {'PASS' if h2 else 'fail'}")

    OUT.mkdir(parents=True, exist_ok=True)
    tag = f"{p.config_id()}_q{q_lb}"
    pd.DataFrame(rows).to_parquet(OUT / f"quality_feat_ic_{tag}.parquet")
    pd.DataFrame([{**ds, "h2_gate": h2, "q_lb": q_lb}]).to_parquet(OUT / f"quality_h2_{tag}.parquet")
    print(f"\nwrote {OUT}/quality_h2_{tag}.parquet")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", type=int, default=0)
    ap.add_argument("--q-lb", type=int, default=ql.QUALITY_LB)
    args = ap.parse_args()
    run(PRIMARY, q_lb=args.q_lb, smoke=args.smoke)


if __name__ == "__main__":
    main()
