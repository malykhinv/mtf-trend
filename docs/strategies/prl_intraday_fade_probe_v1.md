# PRL intraday reversal fade + breakeven-stop probe (protocol + result)

**Code:** `prl/research/intraday_fade.py` · 1m→15m, **IS only** (2025-06..2026-01, reserved OOS
untouched). **Status:** IS. Negative result. ~7 months, single regime → high overfit risk.

---

## Question (user)

The daily signal inverts intraday (negative 5m/15m IC). (1) How do we find an intraday ENTRY for
reversal? (2) Does moving SL to breakeven under various triggers (ATR, prior-candle break, two
consecutive candles) help?

## Design

- **Entry (event-driven):** market-relative short-term overextension. Per bar, z-score of the
  4-bar residual return vs its trailing vol; z ≥ thr → fade **short** (pump), z ≤ −thr → **long**
  (dump). Enter next bar open. So the "entry point" is the overextension event, not a schedule.
- **Trade:** initial 1-ATR stop against the fade, reversion target at `tgt`×ATR, time stop, cooldown.
- **Breakeven grid:** move stop to entry on {none, ATR move in favor, break of prior bar, two
  favorable bars}.

## Result (50 liquid symbols @15m)

**Breakeven grid** (thr=2, SL 1 ATR, tgt 1.5 ATR, hold 16):
```text
none  n=82,988  win 0.399  mean -0.012%  exp -0.017R  PF 0.98
atr   n=95,268  win 0.104  mean -0.274%  exp -0.317R  PF 0.35
prev  n=98,438  win 0.113  mean -0.219%  exp -0.264R  PF 0.43
two   n=92,428  win 0.256  mean -0.073%  exp -0.087R  PF 0.82
```

**Base fade config grid** (breakeven=none), thr∈{2,3} × tgt∈{0.5,0.75,1.5} × hold∈{4,8,16}:
every cell has **PF ≤ 0.98, negative expectancy**. A tight target (0.5 ATR) buys a high win-rate
(0.63-0.65) but still loses (mean −0.02%, PF 0.93) — many small wins, rare big stop-outs. All
figures are **before** transaction costs.

## Verdict

1. **The intraday reversal fade has no tradeable edge here.** The negative cross-sectional IC
   (−0.02..−0.036) is a real but weak statistical tendency that does not survive as an event-driven
   fade with realistic stops — the same IC≠R gap as daily, and thinner. Costs would sink it further.
2. **SL→breakeven is counterproductive for a mean-reversion fade** (clean answer to the question):
   every breakeven trigger made it worse, because moving the stop to entry cuts the trade flat
   *before* the reversion it is waiting for completes. Breakeven is a trend-trade tool, not a
   reversion tool.

Deliberately **not tuned further**: chasing a positive cell across parameters on 7 months of a
single regime is the overfitting self-deception the project methodology exists to prevent.

## Where this leaves the ambitious target

The daily book (real, ~Sharpe 1.4-1.5, ~20% CAGR, ~62% weeks, 2025-concentrated) is the one honest
asset. Every amplification lever (rebalance/window/overlap/multi-horizon/selectivity/loser-cut/
wide-illiquid/1h-trailing/leverage) and the intraday-reversal alternative are exhausted or empty.
**Hundreds-%/year with 80%+ positive weeks is not supported by this signal on this data.** The
honest next steps are: freeze the daily book and spend the single OOS, or open a genuinely new
research direction (different data/edge) — not further tuning of these.
