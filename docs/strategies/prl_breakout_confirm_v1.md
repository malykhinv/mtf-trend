# Breakout confirm/fail (1d-high, 1h decision) — protocol + result

**Code:** `prl/research/breakout_probe.py` (Stage 1), `breakout_features.py` (Stage 2 exhaustive).
**Data:** klines_1h + daily highs, full IS 2023-01..2026-01, top-150 liquid. **Status:** IS. OOS reserved.

---

## Hypothesis (user)

A coin breaks its trailing 20d high; on 1h we watch whether it CONSOLIDATES above (held) or falls
back UNDER (failed) T=4h later. Thesis: held -> continuation (long), failed -> reversal (short).
Study every accompanying metric; decide long/short; test entries/exits separately.

## Stage 1 — base directional edge (T=4h, forward H=24h, market-relative to BTC)

```text
events 14,464 (held 8,839 / failed 5,625)
BLIND baseline (random entry): mean -0.08%, median -0.36%
HELD  (long):  mean +0.18%  median -0.80%  win 0.428  by-year +0.13/+0.32/+0.02
FAILED(short): mean +0.07%  median -0.51%  win 0.451  by-year +0.38/+0.12/-0.42 (sign flips)
HELD minus FAILED forward gap = +0.12% (hold/fail weakly predicts direction)
volume-inflow split of HELD: high-vsurge mean +0.34% but median -0.83%, win 0.43 (unchanged)
```

**Read:** the same fat-tail lottery as all prior breakout work ([[session-break-discovery]],
[[runner-vs-fizzle-portrait]]). Held breakouts beat blind on the MEAN (+0.18 vs -0.08) and are
positive every year, but the MEDIAN is -0.80% and win-rate 43% -- the typical breakout fails; rare
runners carry the mean. Volume inflow selects BIGGER runners (higher mean, fatter tail) but does NOT
raise the win-rate or median. At 43% win / ~1:1 R this is negative expectancy on a fixed horizon
after costs. The short (failed) side is unstable across years.

## Stage 2 — exhaustive: can the full arsenal predict the runners?

breakout_features.py computes, at each event, the entire metric set (volume/trade/taker inflow,
level age & prior-breakout recurrence, extension, EMA positions, ATR candle, ATH distance, recent
momentum, coin-minus-BTC, market breadth/dispersion/BTC-trend, correlation-basket confirmation, and
funding) and runs a causal walk-forward classifier (OOF AUC for win & runner) + shuffle, separately
for HELD (long) and FAILED (short).

```text
HELD (long):  AUC[win] 0.519 (shuffle 0.505)   AUC[runner] 0.624 (shuffle 0.506)
FAILED(short):AUC[win] 0.521 (shuffle 0.496)   AUC[runner] 0.584 (shuffle 0.509)
top HELD win separators (year-stable*): basket -0.12* (isolated breakout > broad), breadth -0.05*,
  taker_ratio +0.04*  -- winning breakouts are isolated strength in a flat/weak market.
```

**Decisive: RUNNER (big move) is predictable (AUC 0.58-0.62 >> shuffle), DIRECTION (win) is NOT
(0.52 ~ shuffle).** With the full arsenal + correlation baskets we can identify the lottery tickets
with bigger jackpots but not whether they win. This is the fundamental reason breakouts don't
convert to R (matches session-break / runner-vs-fizzle).

## Cap & relative volume (breakout_capvol.py)

```text
HELD by CAP:        low-cap win 0.441 runner 0.162  >  high-cap win 0.417 runner 0.152  (small)
HELD by BAR SURGE:  high-surge runner 0.198 vs low 0.134, but win ~0.43 both (volume -> size, not win)
HELD by 24h RVOL:   low-vol win 0.457 med -0.39% runner 0.121  |  high-vol win 0.413 med -1.40% runner 0.207
```

**Cap and volume move the SIZE/variance axis (runner), not the DIRECTION axis (win).** High relative
volume makes the outcome BIMODAL -- more runners AND more failures (lower win, worse median): a
loud breakout is a bigger lottery ticket, not a more reliable one. Low-vol/low-cap breakouts are
more reliable (higher win, better median) but smaller. No bucket reaches win > 0.46 or a positive
median -> no clean fixed-horizon long edge; the runner-rich buckets only feed a convex execution.

## Post-event dynamics (breakout_postdynamics.py)

Does reading the confirmation-window behavior (retest depth, consolidation tightness, close position,
hold/volume persistence, taker flow, post drift) predict direction where pre-event features could
not? [results appended after the run]
