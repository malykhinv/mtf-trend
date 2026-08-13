# PRL amplification levers — anatomy + rebalancing/horizon (protocol + result)

**Code:** `prl/research/anatomy.py`, `overlap.py` · **Status:** IS. OOS reserved.

---

## 1. Anatomy of the daily top-100 book (2023-09..2025-12, 838 days)

```text
55 rebalances x ~100 names = 5,498 name-trades, ~6.6/day (but batched every 15d)
CAGR +20.7%  Sharpe 1.51  t-stat 2.75  (t>2 => real, not pure noise, but modest)
+weeks 58% (n=121)   +months 68% (n=28)
```
Concentration is real: best-5-weeks = **39%** of total return, best-3-months = **48%**;
removing the best 15% of weeks -> total <= 0. By year: 2023 −1%, 2024 +17%, **2025 +62%**
(the market-neutral edge shines in the 2025 alt-bear / high-dispersion regime).

Mechanics: this is a scheduled cross-sectional rebalance, not per-name triggered trades.
Every name always carries a weight = rank − mean(rank) (top long, bottom short, dollar-neutral).
"Entry" = the rebalance day; "exit" = the next rebalance changes the weight. **Both legs are
traded — the short leg is the stronger one** (68.8% vs 46.5% long-leg win-rate).

## 2. Selectivity (trim the middle / cut losers) — does NOT help

Keeping only the conviction tails (top/bottom 30/20/10/5%) leaves CAGR flat at ~21% and +weeks at
~55-60%; Sharpe falls as you concentrate. Earlier pump/high-vol loser cuts added only ~+1% weeks.
**There is no separable "loser layer" to cut** — the edge is spread evenly across the rank.

## 3. Rebalancing / horizon levers — EXHAUSTED at a ~62% weekly ceiling

Holding-period grid H∈{5..30}, single-phase vs **overlapping tranches** (rebalance 1/H of the book
daily = continuous rebalance / sliding window), and multi-horizon combinations:

```text
overlapping smooths the Sharpe estimate (removes lucky-phase noise) -> ~Sharpe 1.4, CAGR ~18%
but +weeks stays ~57-63% at every H
multi-horizon (H=5+15+30) -> +weeks 62% (best), Sharpe 1.44
corr(H=5 book, H=30 book) = 0.86
```

The 0.86 correlation is the key: overlapping/horizon tricks only smooth when the sub-books are
**independent**, but all of them trade the same slow signal, so they are ~86% correlated and the
averaging barely reduces weekly variance. **The ~62% positive-weeks ceiling is a property of the
SIGNAL, not the rebalancing schedule** — no scheduling change breaks it.

## 4. Conclusion + remaining lever

Rebalancing, window/cadence, overlapping, multi-horizon, selectivity, loser-cuts: all exhausted.
Daily book ≈ CAGR 18-21%, Sharpe 1.4-1.5, +weeks ~62%, 2025-concentrated. To move the weekly
ceiling you must change the **trade-level outcome distribution**, not average the same signal.

The one untested lever that does this: an **asymmetric exit** — trail each position on **1h swing
extremes** (let winners run, cut losers) instead of a fixed horizon. 1h data covers the full IS,
so it is testable next.
