# PRL fine-tier probe — does 5m/15m give more positive weeks? (protocol + result)

**Code:** `prl/research/fine_tier.py` · **Data:** existing enriched_1m, resampled, **IS only**
(2025-06 → 2026-01; the reserved 2026-H1 OOS is NOT touched). **Status:** IS. Negative result.

---

## 1. Question

The daily book tops out at ~58% positive weeks; the 80% target needs far more independent bets.
Does porting the residual-momentum + quality signal to 5m/15m (more bets/week) raise weekly
consistency — using only already-downloaded 1m data (no new downloads)?

## 2. Result (80 liquid symbols @15m, 60 @5m; 31 IS weeks)

```text
15min  fwd ~4h   IC -0.020   +weeks 45%
15min  fwd ~8h   IC -0.028   +weeks 52%   (CAGR +28% but see caveat)
15min  fwd ~24h  IC -0.025   +weeks 52%
5min   fwd ~4h   IC -0.028   +weeks 42%
5min   fwd ~8h   IC -0.036   +weeks 48%
5min   fwd ~24h  IC -0.029   +weeks 52%
```

Two robust facts across both timeframes and all horizons:

1. **The daily signal INVERTS intraday** — rank-IC is consistently **negative** (−0.02 to −0.036).
   Intraday is reversal-dominated: recent residual leaders/high-quality names tend to mean-revert
   over the next hours, the opposite of the daily continuation. So the daily signal does not port.
2. **Positive-weeks did NOT improve** (42–52%, *below* the daily book's 58%). "More bets" only
   smooths the curve when each bet carries positive edge; the ported signal has none intraday, so
   extra frequency just adds noise. The occasional positive CAGR (e.g. +28/+35%) sits on a negative
   IC over 31 weeks of a single (2025 H2 bear) regime — an artifact, not an edge.

## 3. Verdict

**Naively porting the daily residual signal to intraday does not work, and does not move the
weekly-consistency ceiling.** The 80%-positive-weeks target is not reachable this way.

The consistent **negative** intraday IC is itself a genuine finding (short-term reversal), but
converting it into an 80%-weeks strategy is a **separate, substantial research track** — a
dedicated intraday reversal + microstructure signal — not a quick sign-flip, and building it on a
7-month single-regime window carries high overfitting risk. Deferred, not claimed.

## 4. Bottom line for the return/consistency target

- Daily market-neutral book: real, ~15–20% CAGR, ~58–60% positive weeks. Far from hundreds%/80%.
- Leverage lifts return (×5 → ~100% CAGR) but leaves positive-weeks at ~57% — cannot buy consistency.
- Wider/illiquid universe: hurts. Loser cuts (pump/high-vol): marginal. Fine-tier port: fails.
- **Hundreds %/year every year with 80%+ positive weeks is not achievable from this daily signal.**
  It would require a genuinely different, higher-Sharpe engine (dedicated intraday), which is a new
  project. What we DO have is an honest, leak-clean, year-uniform, market-neutral ~Sharpe-1 book.
