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

## Next

The only genuine lead is the **short leg**: EMA-position + ATH/ATL context add +0.023 causal AUC.
Retrain the ranker with these context features in the pool and measure the REAL book (rank-IC,
per-year, weekly%, cost, shuffle) — because AUC gains have repeatedly failed to convert to P&L
(win-rate≠profitability). If it does not lift the book, context is descriptive only.
