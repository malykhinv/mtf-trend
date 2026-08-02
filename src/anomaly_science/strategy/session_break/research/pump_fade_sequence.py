"""Sequencing / ruin stress for the swing-BOS pump-fade short. The edge is carried by
the best few % of trades -- so what if they land LAST? We take the risk-normalised
trade series and measure: the real equity path (max DD, longest time underwater), an
ADVERSE ordering (all winners pushed to the end = worst case), and a random-shuffle
DD distribution, at 1/2/5% risk per trade. Answers: do we sit in a long drawdown, and
can the strategy itself liquidate us?
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.session_break.research.pump_fade_risknorm import build, FADE_PAIRS

DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
FLOOR = 0.04


def path_stats(R, risk):
    eq = 1.0; peak = 1.0; maxdd = 0.0; underwater = 0; longest = 0
    for r in R:
        eq *= (1 + risk * r)
        if eq >= peak:
            peak = eq; underwater = 0
        else:
            underwater += 1; longest = max(longest, underwater)
            maxdd = max(maxdd, (peak - eq) / peak)
    return eq - 1, maxdd, longest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=Path(".output/results/session_break/prev_vol.parquet"))
    args = ap.parse_args()
    t = pd.read_parquet(args.events)
    d = t[t.start_ts < DEV_END]
    fade = d[d.pair.isin(FADE_PAIRS)]
    base = fade[(fade.vol_z >= fade.vol_z.quantile(0.9)) & (fade.p_up == 1)]
    champ = base[(base.p_close_pos > 0.6) & (base.pump_new_high == 1) & (base.idio_ret > base.idio_ret.median())]
    tr = build(champ).sort_values("exit_ts").reset_index(drop=True)
    R = (tr.net_pct.to_numpy() / np.maximum(tr.sd.to_numpy(), FLOOR))
    n = len(R); ntrades_per_day = n / tr.assign(day=pd.to_datetime(tr.exit_ts, unit="ms").dt.date).day.nunique()
    print(f"trades={n}  ~{ntrades_per_day:.1f}/day  mean={R.mean():.3f}R  win={ (R>0).mean()*100:.1f}%\n")

    print(f"{'risk':>6}{'real final':>12}{'real maxDD':>12}{'realUWtrades':>14}{'ADVERSE maxDD':>15}{'ADVERSE final':>14}")
    Radv = np.sort(R)                       # worst first, all winners at the very end
    for risk in (0.01, 0.02, 0.05):
        rf, rdd, ruw = path_stats(R, risk)
        af, add, auw = path_stats(Radv, risk)
        print(f"{risk*100:>5.0f}%{rf*100:>+11.0f}%{-rdd*100:>11.0f}%{ruw:>13}{-add*100:>14.0f}%{af*100:>+13.0f}%")

    # random-shuffle DD distribution at 1%
    rng = np.random.default_rng(0); dds = []
    for _ in range(2000):
        _, dd, _ = path_stats(rng.permutation(R), 0.01)
        dds.append(dd)
    dds = np.array(dds)
    print(f"\nrandom-order maxDD @1% risk: median={-np.median(dds)*100:.0f}%  "
          f"95th={-np.percentile(dds,95)*100:.0f}%  worst={-dds.max()*100:.0f}%")

    # longest underwater in CALENDAR terms (real order)
    eq = 1.0; peak = 1.0; peak_ts = int(tr.exit_ts.iloc[0]); longest_days = 0
    for i, r in enumerate(R):
        eq *= (1 + 0.01 * r)
        if eq >= peak:
            peak = eq; peak_ts = int(tr.exit_ts.iloc[i])
        else:
            days = (int(tr.exit_ts.iloc[i]) - peak_ts) / 86400000
            longest_days = max(longest_days, days)
    print(f"longest underwater stretch (real order, 1% risk): {longest_days:.0f} days")
    print("\nNote: fixed-fractional 1% risk with capped -1R losses cannot mathematically hit zero;")
    print("the question is drawdown depth + underwater time, shown above.")


if __name__ == "__main__":
    main()
