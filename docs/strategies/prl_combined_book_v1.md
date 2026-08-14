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

## Next expansions (toward profit)

1. Size the breakout sleeve by the **runner-predictor** (AUC 0.62) instead of equal-weight.
2. Optimize sleeve weights / add the beta-hedge to the combined; test cost-stress.
3. Add further complementary sleeves; then freeze the combined spec and spend the single OOS.
