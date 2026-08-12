"""Strict look-ahead / leakage battery for the PRL residual ranker (§56, §71,
lookahead-audit-rule). Gated on an attractive result (the wide-pool IC), it tries
hard to make the signal fail on purpose.

Tests:
  1. POSITIVE control (future-feature injection): add the future label as a feature.
     OOF IC MUST spike toward ~1. If it does not, the CV/labelling harness cannot
     detect leakage and every positive result is untrustworthy.
  2. Embargo sensitivity: OOF IC at embargo in {0, H+2, 2H+4}. A real signal is
     roughly flat; a big drop as embargo grows means overlap leakage was inflating it.
  3. Feature staleness: shift all features +3 extra days. IC should decay gracefully,
     not stay pinned (a pinned IC can indicate a slow-moving leaked target).

Fast config (fewer iterations / folds) — this is a diagnostic, not the headline fit.
Run: python -m anomaly_science.strategy.prl.research.leakage_audit
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research.catboost_rank import rank_ic, time_folds
from anomaly_science.strategy.prl.research.catboost_rank import _load_pool
from anomaly_science.strategy.prl.research.policy import PRIMARY as P

OUT = Path(".output/results/prl_coarse")
FOLDS = 3
ITERS = 300


def _oof_ic(df, feats, embargo, seed=0):
    from catboost import CatBoostRegressor
    parts = []
    for fi, (tr_d, te_d) in enumerate(time_folds(df["date"].values, FOLDS, embargo=embargo)):
        tr = df[df["date"].isin(tr_d)]; te = df[df["date"].isin(te_d)].copy()
        cb = CatBoostRegressor(loss_function="RMSE", iterations=ITERS, depth=5,
                               learning_rate=0.03, l2_leaf_reg=8.0, random_seed=seed, verbose=False)
        cb.fit(tr[feats], tr["y_rank"])
        te["pred"] = cb.predict(te[feats])
        parts.append(te[["date", "pred", "resid_label"]].rename(columns={"resid_label": "fwd_ret"}))
    ic, t, n = rank_ic(pd.concat(parts, ignore_index=True), "pred")
    return ic, t


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("building pool for leakage audit (IS only) ...")
    df, feats = _load_pool(P)
    H = P.fwd_horizon

    print("\n=== 1. POSITIVE control: inject the future label as a feature ===")
    df2 = df.copy()
    df2["_future_leak"] = df2["resid_label"]  # a bug that leaks the outcome
    ic_leak, t_leak = _oof_ic(df2, feats + ["_future_leak"], embargo=H + 2)
    ic_base, t_base = _oof_ic(df, feats, embargo=H + 2)
    print(f"  clean pool        IC={ic_base:+.4f} (t={t_base:+.1f})")
    print(f"  + future-leak feat IC={ic_leak:+.4f} (t={t_leak:+.1f})   "
          f"-> {'DETECTED (harness works)' if ic_leak > ic_base + 0.3 else 'NOT DETECTED (harness broken!)'}")

    print("\n=== 2. Embargo sensitivity (real signal ~ flat) ===")
    rows = []
    for emb in (0, H + 2, 2 * H + 4):
        ic, t = _oof_ic(df, feats, embargo=emb)
        rows.append({"embargo": emb, "ic": ic, "t": t})
        print(f"  embargo={emb:2d}d  IC={ic:+.4f} (t={t:+.1f})")
    span = max(r["ic"] for r in rows) - min(r["ic"] for r in rows)
    print(f"  IC span across embargo = {span:.4f}  "
          f"-> {'OK (flat)' if span < 0.015 else 'WARN: overlap-sensitive'}")

    print("\n=== 3. Feature staleness (+3d shift) ===")
    df3 = df.sort_values(["symbol", "date"]).copy()
    df3[feats] = df3.groupby("symbol")[feats].shift(3)
    df3 = df3.dropna(subset=feats, how="all")
    ic_stale, t_stale = _oof_ic(df3, feats, embargo=H + 2)
    print(f"  +3d stale features IC={ic_stale:+.4f} (t={t_stale:+.1f})  (vs fresh {ic_base:+.4f}; "
          f"graceful decay expected)")

    pd.DataFrame([{"test": "clean", "ic": ic_base}, {"test": "future_leak", "ic": ic_leak},
                  *[{"test": f"embargo_{r['embargo']}", "ic": r["ic"]} for r in rows],
                  {"test": "stale+3d", "ic": ic_stale}]).to_parquet(OUT / "leakage_audit_prl.parquet")
    print(f"\nwrote {OUT}/leakage_audit_prl.parquet")


if __name__ == "__main__":
    main()
