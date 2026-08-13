# PRL context features — ATH/ATL, EMA, candle/ATR, recurrence, activity (protocol + result)

**Code:** `prl/research/context_features.py` · daily book name-trades, 15d, market-relative.
**Status:** IS. OOS reserved.

---

## Question (user)

Do these separate winners from losers: proximity to ATH/ATL; position vs EMAs; size of the last /
entry candle vs ATR; recurrence of similar past activity spikes; trade count & volume; spike
dynamics (fast vs smooth)? Tested with per-year sign stability AND causal incremental OOF AUC.

## Separators (win−lose, std units; `*` sign-stable across 2023/24/25)

```text
                       LONG (win .465)      SHORT (win .688)
ATH/ATL proximity      dist_ath_all +0.14*  dist_ath_all -0.17*  dist_atl_all -0.21
EMA position           weak / mixed         ema20/50/100/200 -0.23..-0.27*  (below all EMAs)
entry/last candle/ATR  last3_range +0.24*   ecandle_range -0.22  last3_range -0.16*
                       ecandle_body +0.17*
recurrence (spikes)    n_volspikes -0.03    n_volspikes -0.04   (weak, unstable)
volume / trades        log_qv +0.26*        log_qv -0.09*
spike dynamics         verticality -0.14    verticality +0.07   (smooth helps longs)
```

Winning **shorts** = below all EMAs, far from ATH / near ATL, quiet (low volume), small recent
candles — a clean, sign-stable "genuine downtrend laggard" portrait. Winning **longs** = higher
volume, bigger recent candles, near ATH, smooth (non-vertical) — but weaker and partly unstable.

## Causal incremental value — OOF AUC (does context add OOS predictive power?)

```text
LONG   base 0.499 -> base+context 0.492   (delta -0.007  => NO OOS value; long leg ~ random)
SHORT  base 0.588 -> base+context 0.610   (delta +0.023  => REAL OOS value)
```

**Decisive:** the pretty long-leg separators do NOT generalize (AUC stays ~0.5 — long-leg outcomes
remain causally unpredictable, as in the loser-filter study). Context adds real predictive power
**only to the short leg** (+0.023 AUC), driven by EMA-position and ATH/ATL distance.

## Answers to the six questions

1. **ATH/ATL proximity** — matters, mainly shorts (far-from-ATH / near-ATL laggards win).
2. **EMA position** — strongest short signal: winning shorts are below all EMAs. Weak for longs.
3. **Entry/last candle vs ATR** — longs: bigger recent candles win; shorts: smaller win. Not
   OOS-predictive for longs.
4. **Recurrence of past spikes** — weak and unstable; not a meaningful separator here.
5. **Volume / trade count** — level effects (longs higher-vol win, shorts lower-vol win) but no
   long-leg OOS value.
6. **Spike dynamics (fast/smooth)** — smoothness helps longs modestly; weak overall.

## Conversion test — retrained ranker with context in the pool (48 features)

Added EMA20/50/100/200, dist_ath_all/atl_all/ath_250, entry/last-candle-vs-ATR to the pool and
retrained. It did NOT help:

```text
ranker linear OOF rank-IC:  0.089 -> 0.085 (slightly worse); catboost 0.074 -> 0.080
real book (rank-weight top-100, 15d, hyst 0.5):
  baseline  CAGR +22%  Sharpe 1.56  +weeks 58%   2023 -1% 2024 +18% 2025 +63%
  +context  CAGR +19%  Sharpe 1.23  +weeks 58%   2023 -4% 2024 +12% 2025 +63%
```

The +0.023 short-leg AUC did NOT convert — adding the context lowered the book (Sharpe 1.56→1.23,
2024 +18→+12%). Same lesson again: **win/lose separation ≠ book P&L.** Per the incremental-value
rule (§27.1) the context features are REJECTED for the pool (kept at 39). They are descriptively
true (winning shorts sit below EMAs, far from ATH) but add no tradeable value; the extra features
just gave the ranker more to overfit.

## Verdict

The user's ATH/ATL and EMA-position hypotheses are real *descriptively* (especially for the short
leg) but do not improve the tradeable book. Recurrence and candle/ATR add nothing. Pool stays at
39 features; daily book unchanged (~Sharpe 1.5, ~20% CAGR, ~62% weeks).
