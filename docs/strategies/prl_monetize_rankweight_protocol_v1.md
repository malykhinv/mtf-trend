# PRL monetization — rank-weight + neutralization + capacity (protocol + result)

**Strategy:** `prl_daily_v1` (coarse) · **Spec:** §32.3, §44, §45, §47, §65.2
**Code:** `prl/research/monetize.py` · **Tests:** `tests/test_prl_monetize.py` (2)
**Run:** `python -m anomaly_science.strategy.prl.research.monetize` · **Status:** IS. **OOS reserved.**

---

## 1. Problem

PRL-ML-000: rank-IC positive every year (2023 +0.045, 2024 +0.060, 2025 +0.131) but the naive
decile long-short monetized only in 2025 (2023 −1%, 2024 −26%, 2025 +151%). Goal: a market-neutral
book with positive $ in **each** year, net of realistic cost, on an executable universe.

## 2. What we changed

- **Rank-weighted, dollar-neutral construction** (whole cross-section, `w = rank − mean(rank)`,
  gross 1) instead of decile tails — monetizes the IC faithfully rather than the fat-tail extremes.
- **Cross-sectional neutralization** of the score vs volatility / beta / size (residualize + re-rank).
- **Leg autopsy** long vs short per year; realized open-to-open book net of turnover (Σ|Δw|·bps).

## 3. Results (IS, non-overlap 5d)

**Leg autopsy** — the long leg is the fragile side; the short leg is robust every year:
```text
LONG   2023 -8%   2024 -27%  2025 +58%   (leaders underperform in the 2024 momentum bull)
SHORT  2023 +7%   2024 +1%   2025 +93%   (fading laggards/low-quality works every year)
```

**Construction, paper factor return on the residual label (gross):**
```text
decile LS (raw)      2023 -1%   2024 -26%  2025 +151%   ALL Sharpe 0.84   <- the old problem
rank-weighted (raw)  2023 +5%   2024 +4%   2025 +44%    ALL Sharpe 1.34   <- positive EVERY year
rank-w neutral vol   2023 +4%   2024 +5%   2025 +43%    ALL Sharpe 1.37
```
Rank-weighting alone fixes the year-uniformity; vol-neutralization confirms it is not merely a
low-vol bet (survives, even improves 2024 on the paper factor).

**Realized tradeable book (raw open-to-open, net of turnover), true dollar-neutral:**
```text
top-100 raw  x1  2023 +2.0%  2024 +6.2%  2025 +54%   ALL Sharpe 1.47   positive every year
top-100 raw  x2  2023 +0.6%  2024 +1.8%  2025 +51%   ALL Sharpe 1.26
top-100 raw  x3  2023 -0.8%  2024 -2.5%  2025 +48%   ALL Sharpe 1.05
```
(For the tradeable raw-return book the RAW score beats the vol-neutralized one — some real edge
lives in vol space; neutralizing helps the paper residual factor but hurts the tradeable book.)

**Capacity / liquidity check (§65.2) — the sobering part:** on the executable top-50 liquid subset
the edge weakens sharply:
```text
top-50 raw   x1  2023 +4.0%  2024 +1.0%  2025 +29%   ALL Sharpe 0.54
top-50 raw   x2  2023 +2.6%  2024 -3.2%  2025 +25%   ALL Sharpe 0.40
```
Much of the Sharpe-1.47 result lived in liquidity ranks 50-100 (harder/costlier to size than a flat
bps model assumes). On the safely-tradeable universe the market-neutral edge is modest (Sharpe ~0.5)
and 2024 turns negative at 2× cost.

## 4. Verdict

**Monetization construction problem SOLVED (rank-weight, dollar-neutral) — year-uniformity achieved
on top-100. Signal confirmed genuinely market-neutral (survives true dollar-neutrality) and not a
pure low-vol bet. BUT the executable (top-50) edge is modest (Sharpe ~0.5, 2024 fragile) — the
strong Sharpe is capacity-inflated by less-liquid names.** Real, honest, market-neutral IS edge;
economically thin where it can actually be traded. Still IS; OOS reserved.

## 5. Next (toward a *confident* liquid-universe edge)

1. Lift the **liquid-universe** book: better exits (thesis-decay, vol-target), turnover/hysteresis,
   horizon ensemble (5/10/20d), regime/vol-bucket conditioning — target positive 2024 net on top-50.
2. Freeze a **transparent additive score** (low DOF) once the liquid book is year- and cost-robust.
3. PRL-PEER-000 as a separate signal source.
4. Only then: single OOS 2026-H1, then locked holdout. OOS remains unread.
