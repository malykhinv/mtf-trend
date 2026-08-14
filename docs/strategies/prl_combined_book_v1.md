# Combined regime-complementary book: PRL + breakout-trend (protocol + result)

**Code:** `prl/research/combined_book.py` · **Status:** IS. OOS reserved. **Best uniformity result.**

---

## Idea

PRL (market-neutral, net-short-beta) profits in the 2025 bear/dispersion; a breakout-long trend
sleeve (Donchian 20/10, breadth-scaled) profits in 2023/24 risk-on -- exactly where PRL is weak.
If they are negatively correlated, the sum is far more uniform than either alone.

## Result (daily-marked, full IS)

```text
sleeve                 CAGR  Sharpe +wk  +mo  maxDD  by-year
PRL market-neutral     +21%  1.51   58%  68%  -10%   2023 -1%  2024 +17%  2025 +62%
PRL beta-hedged        +22%  1.90   59%  75%  -11%   2023 +8%  2024 +22%  2025 +48%
Breakout-long trend     +8%  0.41   41%  39%  -35%   2023 +37% 2024 +14%  2025 -18%

corr(PRL, Breakout) = -0.43     corr(PRL_bh, Breakout) = -0.03

combined:
PRL+BO 50/50           +16%  1.27   50%  71%  -14%   2023 +17% 2024 +18%  2025 +17%
PRL+BO vol-parity      +18%  1.80   57%  82%   -7%   2023 +11% 2024 +19%  2025 +32%
PRLbh+BO vol-parity    +19%  1.66   55%  71%  -10%   2023 +16% 2024 +21%  2025 +26%
```

## Read

The sleeves are genuinely regime-complementary (**corr −0.43**). Combining:
- **Evens the years dramatically** — PRL alone is 2025-dominated (−1/+17/+62); the 50/50 book is
  +17/+18/+17 (near-identical every year), vol-parity +11/+19/+32.
- **Raises Sharpe** (1.51 → 1.80 vol-parity) via the negative correlation, and **cuts drawdown**
  (−10% → −7%), **lifts +months** (68% → 82%).

This is the first result that materially improves year/month uniformity AND risk-adjusted return --
the regime complementarity is real and mechanistic (long-beta trend sleeve offsets the short-beta
market-neutral sleeve).

**Caveats:** still IS (OOS reserved); WEEKLY positive-share stays ~50-57% (the weekly ceiling is
unbroken -- uniformity gain is at the year/month scale); the breakout sleeve is weak standalone
(Sharpe 0.41) and valuable only as a diversifier.

## Rolling window (no calendar statics) — combined_v2.py / combined_v3.py

The v1 PRL sleeve rebalanced every 15 CALENDAR days (single phase, lumpy: ~6.6 name-trades/day but
all batched on 68 rebalance days). Making PRL fully ROLLING (overlapping daily tranches, rebalance
1/H of the book daily) and sweeping the window H:

```text
rolling-PRL sleeve:  H=5 Sh1.43 +wk 63%  |  H=10 +wk 60%  |  H=15 +wk 59%  |  H=20 Sh1.45 +wk 57%  |  H=30 +wk 56%
combined (roll-PRL H + BO, vol-parity):  H=5 Sh1.73 +wk56 +mo75  |  H=15 Sh1.73 +wk54  |  H=20 Sh1.75 +wk51 +uniform yrs +10/+23/+23
```

**Rolling window matters for WEEKLY consistency**: short H=5 lifts the PRL sleeve to **63% positive
weeks** (breaks the 58% single-phase ceiling), long H raises Sharpe/uniformity. But the breakout
sleeve is only 41% weekly (directional trend), so it TRADES weekly for year-uniformity+Sharpe --
the combined weekly is ~51-56%. To get BOTH high weekly and uniform years, the second sleeve must
be uncorrelated AND high-weekly (the breakout gives uniformity, not weekly).

## Next expansions (toward profit)

1. Regime-adaptive rolling window (different H by circumstance).
2. Size the breakout sleeve by the **runner-predictor** (AUC 0.62); find a high-weekly uncorrelated
   third sleeve to lift combined weekly.
3. Optimize sleeve weights / beta-hedge / cost-stress; then freeze the combined spec -> single OOS.
