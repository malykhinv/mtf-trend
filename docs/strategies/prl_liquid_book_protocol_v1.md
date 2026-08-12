# PRL liquid book — confident year-uniform executable edge (protocol + result)

**Strategy:** `prl_daily_v1` (coarse) · **Spec:** §45, §47, §65.2, §86
**Code:** `prl/research/liquid_book.py` · **Run:** `python -m anomaly_science.strategy.prl.research.liquid_book`
**Status:** IS. **OOS reserved.**

---

## 1. Goal

The rank-weighted book was year-uniform on top-100 (Sharpe ~1.5) but capacity-inflated: on the
executable top-50 liquid subset it fell to Sharpe ~0.5 with 2024 fragile (negative at 2× cost).
Lift the **top-50** book to a confident, year-uniform, cost-robust edge — no retraining, only
portfolio-level levers (§45/§47/§86): signal smoothing, weight hysteresis, rebalance step.

## 2. Result — top-50 lever sweep

The turnover levers work exactly as hypothesized. Winner (broad robust region, not one cell):

```text
top-50, step=15d, hysteresis=0.5, turnover 0.60:
  cost x1   2023 +1.5% (Sh0.6)   2024 +14.3% (Sh0.9)   2025 +21.3% (Sh0.8)   ALL Sharpe 0.79
  cost x2   2023 +1.1%           2024 +13.2%           2025 +20.4%           ALL Sharpe 0.74
  cost x3   2023 +0.7%           2024 +12.2%           2025 +19.5%           ALL Sharpe 0.69
```

vs the baseline top-50 5d book (Sharpe 0.54, 2024 ~0 and negative at 2× cost). Longer holding +
weight hysteresis roughly halves turnover (1.0 → 0.6) and **turns 2024 from the fragile year into
the strongest per-year Sharpe** — because 2024 was a whipsaw momentum-bull where high-frequency
chasing bled; holding the persistent part and smoothing the book captures it.

Robustness: the whole **step=15 family** is all-years-positive (not a single lucky cell), and the
winner is positive every year at **1×, 2× and 3×** costs (turnover 0.60 makes it cost-insensitive).
Per-year Sharpe is now uniform (0.6 / 0.9 / 0.8).

## 3. Verdict

**Goal substantially met on the executable universe.** A market-neutral top-50 book with
Sharpe ~0.75 net, positive in every year to 3× cost, low turnover, and — unlike every earlier
construction — no fragile year. This is the first PRL result that is simultaneously real,
year-uniform, cost-robust, and tradeable at size.

**Honest caveats:** (a) 2023 is thin (+1.5%, fewer OOF eval days) though still positive and
cost-robust; (b) the step=15 / hyst=0.5 config was selected as best-of-18 on IS — a researcher
DOF (mitigated by the broad robust region, but it must be **frozen before OOS** and logged as a
trial); (c) Sharpe ~0.75 is decent, not spectacular; (d) still IS — **OOS reserved**.

## 4. Next

1. **Freeze** the full transparent spec: liquid top-50 universe, OOF-linear (or a transparent
   additive twin) score, rank-weighted dollar-neutral, step=15, hysteresis=0.5, cost model.
2. Consolidate a low-DOF **transparent additive score** reproducing the linear blend, re-verify
   the liquid book on it.
3. Optional signal extension: PRL-PEER-000.
4. Then the single **OOS 2026-H1** validation, then locked holdout. OOS remains unread.
