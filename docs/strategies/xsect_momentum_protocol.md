# Cross-Sectional Momentum — Preregistration Protocol

**Status:** preregistered hypothesis, frozen before any in-sample fit. This document
is the contract. It fixes the estimand, the one primary specification, the controls,
the inference machinery, and the acceptance gates *before* a single backtest number is
looked at. Any deviation is logged in §11 with a date and a reason.

**One-line hypothesis.** Among sufficiently liquid perpetual-futures symbols, a symbol's
trailing return *rank* carries information about its forward short-horizon return,
cross-sectionally, after market beta is removed — and enough of that information survives
turnover and costs to run a small long-biased portfolio without a single period or symbol
carrying the result.

We try to *falsify* this. The default expectation, from the literature we cite and from
this project's own track record on free data, is **weak-to-negative after costs**. The job
of this protocol is to make a false positive hard to manufacture.

---

## 0. Honest constraints (read first — these cap ambition)

These are hard facts about the data, established before design:

1. **Span.** 1m enriched data runs **2025-06-03 → 2026-06-16** (~12.5 months), Binance
   **USD-M futures** (not spot). There is no more local history — the 1m files themselves
   start on 2025-06-03.
2. **IS / OOS split.** The prebuilt daily panel `daily_local_is_v1` covers
   **2025-06-03 → 2025-12-31** (~212 days, 610 symbols). The tail **2026-01-01 → 2026-06-16**
   (~5.5 months) exists in 1m form and is held **frozen** as the untouched OOS. It is not
   read until the logic and every parameter are frozen (§9, §22-style order).
3. **Consequence for lookbacks.** Long momentum lookbacks of 84–252 days (the source
   document's range) **do not fit** a 7-month IS window. This protocol uses short/medium
   lookbacks only. See §2.3.
4. **Consequence for regime inference.** ~7 months of IS contains only a handful of
   *independent* macro-regime windows. The "no concentration in one month" gate (§6) is
   therefore **low-power** — it can catch gross concentration but cannot certify regime
   robustness. We report it honestly and do **not** dress a 7-month result as multi-cycle
   evidence.
5. **Futures, not spot.** The source document recommends starting on spot. We only have
   futures. Futures is an acceptable research proxy but adds **funding** as a real carry
   cost; funding is treated as a cost layer (§4), not ignored, and never as free alpha.

Because of (3)–(4), the CatBoost step is deliberately **not** the surrogate-plateau engine
from the source document. Scanning 10^5–10^6 candidate points with a surrogate makes the
effective number of trials unbounded and un-correctable — fatal on a 7-month sample. Here
CatBoost has one narrow, honest job (§8): measure whether a large feature pool predicts the
*forward return rank*, under leak-free splits and against blind/shuffled/linear baselines.

---

## 1. Estimand (what single thing we are measuring)

> Within the point-in-time liquid universe, does trailing-return rank predict the
> **beta-neutral** forward return over the next rebalance horizon?

Primary scientific object: the **long-short decile spread** return
`R_spread(t) = mean(top decile forward) − mean(bottom decile forward)`, and its
market-beta-hedged version `R_spread_bn`. This isolates the *ranking* signal from crypto
market beta. It is the cleanest test of the hypothesis and is reported **whether or not it
is tradeable** (shorting alts is costly).

Primary tradeable object: a **long-biased top-K portfolio** started at **\$1000**, marked
to market daily. This is what the acceptance gates in §6 judge.

Two objects, two roles: the spread answers "is the rank informative?"; the portfolio
answers "can we run it?". A yes on the portfolio with a no on the spread is treated as
suspicious (likely levered beta, not momentum) and investigated, not celebrated.

---

## 2. The ONE primary specification (frozen)

Exactly one spec is primary. Everything else is a control (§7) or a preplanned robustness
perturbation (§10). This is the anti-overfit anchor: the primary number is reported first,
alone, before any grid.

### 2.1 Frequency & execution
- Bars: daily, UTC close, from `daily_local_is_v1` (`complete_daily_bar == True` only).
- Signal formed on close of day `t`. **Execution not at that close.**
- Fill: **next daily open** (`t+1` open). One-bar execution delay is the base case.
- `available_time_ms` from the panel gates causal availability; a symbol-day enters the
  universe/signal only if its bar was closed and available before the decision time.

### 2.2 Point-in-time universe
On each rebalance date `t`, from symbols alive and trading on `t`:
- rank by median dollar (quote) volume over the prior `liquidity_lb` days;
- keep the top `universe_n`;
- exclude stablecoins, wrapped/1000x-denominated duplicates of the same economic risk,
  and symbols younger than `min_age_days`;
- drop symbol-days failing quality flags (stale price runs, zero-volume price moves,
  incomplete bar).

### 2.3 Signal (lookbacks fit the 7-month IS)
Log returns. Combined score with a skip gap:

`M_i(t) = alpha * r_i(t-short_lb, t-skip) + (1-alpha) * r_i(t-long_lb, t-skip)`

Frozen base values (chosen to fit the window, **not** tuned on results):

```yaml
short_lb: 14        # days
long_lb: 42         # days  (fits 7-month IS; leaves warmup + test)
skip: 1             # day   (completed data only; damps 1-day reversal)
alpha: 0.5
```

### 2.4 Ranking → portfolio
```yaml
rebalance_days: 7          # weekly; daily equity still marked every day
top_k: 5                   # long-biased top-K
require_positive: true     # no long with negative absolute momentum
enter_rank: 5              # hysteresis: enter if rank <= 5 ...
exit_rank: 8               # ... hold until rank > 8  (churn control)
weighting: capped_inverse_vol
vol_lb: 20
max_weight: 0.30
gross_exposure: 1.0        # no leverage in research
regime_asset: BTC
regime_lb: 50              # fits window
regime_threshold: 0.0      # risk-off -> cash when BTC trend < 0
```

Cash leg is a settlement asset, not risk-free; depeg is out of scope on futures but funding
carry on held positions is in scope (§4).

---

## 3. Portfolio accounting (the \$1000 account)

- Start equity **\$1000**, `gross_exposure = 1.0`, no leverage.
- Daily mark-to-market: even with weekly rebalance, portfolio return is computed **every
  day** from held positions' daily returns, so the day-level gates (§6) and the drawdown
  path are real, not weekly-sampled.
- Costs charged on turnover at execution (§4). Funding charged daily on held notional.
- Outputs per run: daily equity curve, daily return series, per-rebalance trade blotter,
  turnover, per-symbol and per-period PnL attribution, max drawdown (on the daily curve).

---

## 4. Cost model (charged, not assumed away)

Per executed notional:

```
cost_bps = fee_bps + half_spread_bps + base_slippage_bps
         + impact_coef * sqrt(order_notional / ADV)
```
plus daily **funding** on held notional (futures). ADV = trailing median daily quote
volume. Base scenario uses realistic taker-side futures costs; the gates in §6 require
survival at **2× costs** as well. Stress scenarios (mandatory, not tuned): `1×`, `2×`, `3×`
costs; execution delay `+1` / `+2` bars; ADV haircut 50%.

The classic disqualifier — using day `t` close simultaneously for signal, universe, and
fill — is structurally impossible here because fill is `t+1` open via `available_time_ms`.

---

## 5. Inference machinery (how a number becomes evidence)

- **Regime/time-block bootstrap.** CIs on Sharpe / mean return come from **block bootstrap**
  over contiguous week (and month) blocks — never iid resampling of autocorrelated daily
  returns. Reported: median, IQR, and a **lower** interval bound.
- **Blind + shuffled controls are mandatory** (§7). The spec must beat both.
- **Top-period-share checks up front**, not at the end (project lesson: one-week-lottery
  trap). We compute, on the *first* pass: share of total PnL from the single best day / week
  / month, and the leave-one-out family.
- **Multiple testing.** Every configuration ever run is logged (§11). When a grid is used
  (§10), family-wise control is **BY-FDR** across the grid; the primary spec is judged
  alone, before the grid, so it is not FDR-diluted.

---

## 6. Acceptance gates (FROZEN before results; thresholds flexible but preregistered)

A configuration is a **candidate** only if it clears **all** of these on the IS portfolio.
Thresholds are the agreed targets; they are recorded here so they cannot be moved to fit a
result. If a threshold is later relaxed, it is logged in §11 with the reason.

| # | Gate | Threshold | Rationale |
|---|------|-----------|-----------|
| G1 | **Top-trade independence** | Drop the top **40%** of trades by PnL → net result still **> 0** | Kills single-winner / fat-tail dependence |
| G2 | **Positive-day share** | **≥ 70%** of trading days have non-negative portfolio return | Broad, not lumpy, edge |
| G3 | **No day concentration** | Best single **day** ≤ preregistered share of total PnL (target ≤ **15%**) | One-day-lottery guard |
| G4 | **No week concentration** | Best single **week** ≤ target share (≤ **25%**) | One-week-lottery guard (project's #1 failure mode) |
| G5 | **No month concentration** | Best single **month** ≤ target share (≤ **40%**) | *Low power at 7 months — reported, weighted lightly* |
| G6 | **Win rate** | Trade-level win rate **> 50%** | Directional edge, not just tail |
| G7 | **Drawdown** | Max drawdown on \$1000 daily curve ≤ **15%** | Risk budget |
| G8 | **Cost survival** | G1–G7 still hold at **2× costs** and **+1 bar** delay | Not a fills artifact |
| G9 | **Beats controls** | Net Sharpe > blind (equal-weight universe) **and** > shuffled-rank, block-bootstrap non-overlapping | Signal, not beta/luck |

Notes on tension: G2 (≥70% positive *days*) and G6 (>50% *trade* win rate) with G7
(DD ≤ 15%) are jointly demanding for a weekly-rebalanced long-biased alt portfolio. That is
intentional — the bar is high on purpose. All thresholds are explicitly marked **flexible**
by the user; any change is a logged §11 event, applied *before* seeing the affected result,
never after.

---

## 7. Mandatory controls (the spec must beat these)

1. **Blind** — equal-weight the entire liquid universe (no ranking).
2. **Random top-K** — K random symbols from the universe, many seeds.
3. **Shuffled rank** — permute the momentum scores within each date.
4. **BTC buy-and-hold** and **BTC trend filter only** (no alt selection).
5. **Time-series momentum** on the same symbols (per-symbol own-trend), the better-supported
   cousin — if TS ≥ XS, cross-sectional complexity is not justified.
6. **Delayed signal** — execute one extra bar later.

If the primary spec does not beat blind and shuffled after costs, the hypothesis is
rejected at the IS stage and the OOS tail is **not** spent.

---

## 8. CatBoost's narrow, honest role (feature study — NOT plateau surrogate)

CatBoost is used **only** after §6/§7 give the base spec a pulse, and **only** to answer:

> Given a large pool of coin-day features — including **lower-timeframe (1m/15m)
> pre-entry** features (intraday realized variance / semivariance, return skew/kurtosis,
> taker imbalance mean/std, quote-volume HHI, max-minute-volume share, session returns,
> OI change/vol, first/last-hour volume share, etc., all already in `daily_local_is_v1`
> plus fresh 1m-window features computed strictly before the `t+1` fill) — does any of it
> improve the *forward return rank* beyond trailing momentum alone?

Leak-free rules:
- Target = forward coin-day return rank over the trade horizon, availability-gated.
- Splits: **GroupKFold by config/period** and a **contiguous time-block** holdout the model
  never sees; purge/embargo around the lookback and forward windows.
- Baselines the model must beat: **momentum-only**, **linear/logistic** on the same
  features, **shuffled target**. A tree that only beats nothing but itself is noise.
- Diagnostics: global SHAP, dependence, interaction strength. Red flags = importance
  dominated by `period`/date, a single narrow-peak feature, or execution-side features
  (edge = fills artifact).
- **No surrogate over backtest results. No million-point plateau scan.** Explicitly excluded
  as un-correctable multiple testing on a 7-month sample.

The feature pool is intentionally "huge" as requested — the discipline is in the *splits and
baselines*, not in shrinking the pool.

---

## 9. Research order (OOS spent last)

1. Sanity: implement base spec, hand-check a handful of rebalances.
2. Controls (§7) on IS.
3. Primary spec vs gates (§6) on IS — reported alone, first.
4. Up-front concentration / leave-one-out diagnostics (§5).
5. Only if alive: small preplanned grid (§10) with BY-FDR.
6. Only if a *plateau of neighbours* (not a peak) survives: CatBoost feature study (§8).
7. Freeze everything. **Then** open 2026-01→06 OOS once.
8. If OOS agrees: paper/shadow reconciliation before any further claim.

## 10. Preplanned robustness grid (small, logged, FDR-controlled)

Neighbours of the frozen spec only — to test that the result is a *plateau*, not a spike:
`short_lb ∈ {7,14,21}`, `long_lb ∈ {28,42,56}`, `alpha ∈ {0.25,0.5,0.75}`,
`rebalance_days ∈ {3,7,14}`, `top_k ∈ {3,5,8}`, `universe_n ∈ {30,50,75}`,
`weighting ∈ {equal, capped_inverse_vol}`, `regime_lb ∈ {30,50,75}`. Costs `{1×,2×,3×}` are
**scenarios**, never optimized. Every cell logged in §11; BY-FDR across the family.

## 11. Deviation & trial log

Every configuration executed — including failures — is appended to
`.output/results/xsect_momentum/trial_log.parquet` (config hash, gates, block-bootstrap
stats). Any change to §2 spec or §6 thresholds is recorded here with date + reason, and
applied before the affected result is viewed.

---

## 12. Verdict criteria ("worth continuing")

Simultaneously: primary spec clears §6 gates on IS; a neighbourhood plateau (§10) clears
them too (not a lone cell); result is not made by one day/week/symbol (§5); survives 2×
costs and +1 bar (G8); beats blind, shuffled, and TS-momentum (§7); and the frozen OOS tail
does not contradict it. Missing the plateau condition means we found a spike, not a strategy
— reject, per the source document's own §23.7.

Given §0, the realistic prior remains: **low probability of a tradeable edge; moderate
probability of a statistically real but untradeable cross-sectional spread.** This protocol
is designed to tell those two apart honestly and cheaply, and to spend the OOS tail only
once.
