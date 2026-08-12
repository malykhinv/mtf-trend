# PRL deep dive — universe (liquid/illiquid), win/lose discriminator, pump/dump

**Strategy:** `prl_daily_v1` (coarse) · **Code:** `prl/research/discriminate.py`, `catboost_rank.py --universe`
**Status:** IS. **OOS reserved.** KPIs measured with daily marking (CAGR, %-positive weeks/months).

---

## 1. Liquid vs illiquid — TESTED (not assumed)

Retrained the ranker on `--universe 300` (264k rows, linear OOF IC +0.070 vs +0.089 on top-100 —
illiquid names are noisier, lower IC). Daily-marked rank-weighted book (step=15, hyst=0.5) by
universe size, on the u300 scores:

```text
top-50   CAGR +13%  Sharpe 0.87  +weeks 53%
top-100  CAGR +16%  Sharpe 1.27  +weeks 60%   <- best
top-200  CAGR +11%  Sharpe 1.11  +weeks 52%
top-300  CAGR  +2%  Sharpe 0.28  +weeks 49%   <- deep illiquid nearly kills it (2025 negative)
```

**Verdict: adding illiquid names HURTS.** The sweet spot is ~top-100; the deep-illiquid tail
destroys the edge even at flat costs (real slippage would be worse). Hypothesis closed.

## 2. What separates winning from losing trades

Decile longs / shorts, held 15d, labelled win/lose by market-relative forward return:

```text
LONG  (base win-rate 0.465 — the fragile leg):
  winners are LOW vol (dvol/rvol -0.28..-0.24), NON-bursty (burst -0.24),
  WHALE-sized trades (avg_trade_size +0.23), NOT extended (resid_from_low -0.22),
  LOWER prior momentum (rmom_28 -0.17).
SHORT (base win-rate 0.688 — the robust leg):
  winners are genuine LAGGARDS (rsharpe/rmom -0.30, path_eff -0.23, frac_pos -0.22).
```

Winning longs = calm, whale-backed, non-pumped leaders; losing longs = high-vol, bursty,
already-run-up. Winning shorts = truly deteriorating names.

## 3. Recent pump/dump right before entry

Market-relative forward return by pre-entry 3-day move quintile:

```text
LONG  mean rel:  dump-- +0.34%  dump- +2.56%  flat +0.90%  pump+ +1.74%  pump++ -5.30% (win 0.28!)
SHORT mean rel:  dump-- -2.01%  dump- -1.11%  flat -3.37%  pump+ -4.29%  pump++ -2.72%
```

**Going LONG a name that just pumped hard (top-quintile 3d move) is a strong loser: −5.3%, 28%
win-rate** — it reverses. Buying after a recent DUMP is fine (+2.6%). Shorts are robust to the
recent move (slightly better after a pump — mean reversion).

## 4. Loser cut — marginal

Zeroing pumped / high-vol names on the long side and re-normalizing:

```text
top-100 baseline              CAGR +21%  Sharpe 1.51  +weeks 58%
top-100 cut pump++ longs       CAGR +20%  Sharpe 1.49  +weeks 60%
top-100 cut pump+highvol longs CAGR +22%  Sharpe 1.54  +weeks 59%
```

The per-trade pump-reversal is real, but rank-weighting already gives pumped names small weight,
so cutting them adds only ~+1% positive weeks / +0.03 Sharpe. A free clean-up, not a breakthrough.

## 5. Takeaways

- Optimal executable universe ≈ **top-100**; illiquid does not help.
- The signal's edge = calm/whale/non-pumped **leaders** (long) + genuine **laggards** (short);
  a recent hard pump is the clearest single long-side loser tell.
- **The ~58-60% positive-weeks ceiling is not broken by any daily-level tweak** (universe, loser
  cut). Higher weekly consistency (the 80% target) requires more independent bets — i.e. the
  fine-tier (1m/5m/15m), tested next on the already-covered IS window.
