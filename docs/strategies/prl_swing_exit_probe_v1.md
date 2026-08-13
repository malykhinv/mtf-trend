# PRL 1h swing-trailing exit probe (protocol + result)

**Code:** `prl/research/swing_exit.py` · 1h klines, full IS. **Status:** IS. Negative result.

---

## Question

Does replacing the fixed 15-day hold with a **1h swing-trailing exit** (ratcheting Donchian
stop: long exits on close below the cummax of the rolling-min low; short mirrored) improve the
trade-level outcome — let winners run, cut losers?

## Result (market-relative to BTC over each trade's actual window, top-60, hold 15d)

```text
             FIXED                    TRAIL N=12      TRAIL N=24      TRAIL N=48
LONG   win 0.325  mean -0.90%    win .29 +0.13%   .26 +0.27%     .24 +0.26%
SHORT  win 0.687  mean +3.48%    win .32 -0.33%   .30 -0.23%     .26 -0.02%
per-rebalance mean:  fixed +1.24%   trail -0.10% / +0.02% / +0.12%
```

- Trailing **modestly helps the LONG leg** (cuts big losers: −0.90% → +0.26%) but **destroys the
  strong SHORT leg** (+3.48% → ≈0): a 1h stop gets knocked out by intraday up-pops in the volatile
  laggards before their slow multi-day decline delivers.
- Wider trails (N=24/48) do not rescue shorts. Net per-rebalance is always below fixed.

## Verdict

**A 1h swing-trailing exit is net-negative for this signal.** The edge is a slow ~15-day
cross-sectional drift, not an intraday breakout, so trailing stops just add noise-driven early
exits — fatal to the short leg (our best). The fixed-horizon hold is correct here; the short leg
must be held to term. (Useful asymmetry noted: longs like a loss-cut, shorts do not — but the gain
is marginal in a dollar-neutral book and not worth the added DOF.)

## Amplification levers — full tally (all IS, OOS reserved)

Everything proposed to amplify the **daily** book has now been tested and is exhausted:

```text
rebalance cadence grid 5..30        -> ~62% weekly ceiling
overlapping tranches / continuous   -> smooths Sharpe, weekly ceiling unmoved (corr .86)
multi-horizon combine               -> +weeks 62% best
selectivity / trim middle           -> no gain
pump/high-vol loser cut             -> +1% weeks
wider/illiquid universe             -> hurts
1h swing-trailing exit              -> net-negative (kills shorts)
fine-tier port to 5m/15m            -> signal inverts, weeks worse
leverage                            -> return only, weeks unchanged
```

The daily book is **structurally** ~Sharpe 1.4-1.5, ~18-21% CAGR, ~62% positive weeks,
2025-concentrated. No daily-level lever breaks the weekly ceiling — it is a property of the slow
signal. The only remaining path to the hundreds-%/80%-weeks target is a genuinely different,
higher-frequency **engine** (e.g. the intraday short-term-reversal hinted by the consistently
negative 5m/15m IC) — a new project, not an amplification of this one.
