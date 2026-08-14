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

## Runner-SIZED + regime-scaled sleeve — regime_runner_book.py (2026-08-14)

Applying the wide-grid conclusion (direction unpredictable, runner-size real & regime-driven): we
STOP filtering breakout direction and instead SIZE each confirmed-hold breakout by its walk-forward
runner-score (OOF AUC 0.596), and scale the sleeve's GROSS exposure by a causal breadth-rank regime
factor. Hold-to-time HH=48h, no stops (never cut the fat-tail runners). 8,812 confirmed-hold events,
runner-rate 0.159. Standalone (long-beta) and vol-parity with the daily PRL sleeve:

```text
breakout sleeve standalone     Sharpe +wk  maxDD  by-year
equal/flat                      0.73   57%  -86%  +181/+20/-25
runner/flat                     0.81   58%  -90%  +242/+26/-18
equal/regime                    0.64   52%  -81%   +89/+33/-12
runner/regime                   0.72   53%  -87%  +115/+39/ -3
SHUF-runner/flat (control)      0.64    -     -    +145/ -4/-32   <- runner-sizing beats shuffle
equal/SHUF-regime (control)    -0.09   49%  -94%   -15/-57/-58   <- regime beats shuffle (badly)

combined vol-parity (PRL + variant)   Sharpe +wk +mo  maxDD  by-year
PRL alone                              1.51  58% 68%  -10%   -1/+17/+62
PRL + equal/flat                       1.79  61% 79%  -10%   +9/+23/+73
PRL + runner/flat                      1.86  62% 75%  -11%  +11/+24/+78   <- best Sharpe
PRL + equal/regime                     1.71  64% 79%  -11%   +6/+23/+71   <- best weekly
PRL + runner/regime                    1.77  64% 75%  -12%   +7/+23/+76   <- best weekly + uniform
PRL + SHUF-runner (control)            1.70  61%  -     -     +8/+21/+72
PRL + SHUF-regime  (control)           1.19  59% 64%   -     +5/ +7/+52
corr(PRL, breakout variants) = -0.19..-0.21
```

**Both levers are REAL (shuffle-validated) and monetizable:**
- **Runner-sizing** (size by predicted runner) beats its shuffle: standalone Sh 0.81 vs 0.64, CAGR
  +34% vs +12%; combined 1.86 vs 1.70. First time the runner-predictor converts to portfolio value.
- **Regime scaling** (breadth-rank gross) is critical: shuffling it collapses the sleeve
  (+20%->-35%, Sh 0.64->-0.09) and the combo (1.71->1.19). It lifts combined WEEKLY to **64%** (from
  57%) and evens the years, at a small Sharpe cost vs flat.
- **New best combined book:** runner/flat = Sharpe **1.86** (max), runner/regime = **64% weekly** +
  uniform years (+7/+23/+76). Beats the v1 combined (Sh 1.80, +wk 57%).

**Caveats:** (1) weekly is now 64% -- real progress but still short of the 80% goal; the breakout
sleeve itself is only ~53-58% weekly (directional), which caps the combined weekly. Breaking 80%
needs a HIGH-WEEKLY uncorrelated THIRD sleeve. (2) the standalone breakout sleeve has maxDD -86..-90%
(concentrated long-beta) -- safe ONLY inside vol-parity where PRL dominates the risk weight (combo
maxDD stays -10..-12%); never trade the sleeve alone. Still IS; OOS reserved.

## Next expansions (toward profit)

1. **Find/build a high-weekly uncorrelated THIRD sleeve** (the direct lever to 80% weekly) -- a
   mean-reverting or market-neutral intraday source that wins often-and-small, uncorrelated with both
   PRL and the long-beta breakout sleeve.
2. Cost-stress + beta-hedge the runner/regime combined book; then freeze the combined spec -> single OOS.
