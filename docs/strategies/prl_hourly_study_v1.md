# PRL on 1h data (full period) — reversal is real but untradeable (protocol + result)

**Code:** `prl/research/hourly_study.py` · klines_1h, full IS 2023-01..2026-01, top-150 liquid.
**Status:** IS. OOS reserved.

---

## Why 1h

We had only studied daily (coarse) and the short 1m window. klines_1h covers the FULL multi-regime
period with ~24x more observations -> more power and, for a book, ~24x more bets (the direct lever
on the weekly-positive ceiling).

## Result — residual momentum reverses strongly, at every intraday horizon

Rolling-horizon residual-momentum rank-IC vs the frozen-beta future residual (negative = reversal),
and a rank-weighted fade book (score = −momentum, hold = H):

```text
H     IC        t      wk>0 | fade book x1        x2
3h   -0.0177  -18.4   0.22  | CAGR -84% Sh -10.1  -97%
6h   -0.0216  -21.9   0.26  | CAGR -61% Sh  -5.2  -83%
12h  -0.0285  -28.2   0.30  | CAGR -43% Sh  -3.0  -62%
16h  -0.0309  -30.5   0.27  | CAGR -28% Sh  -1.8  -47%
24h  -0.0351  -34.6   0.22  | CAGR -22% Sh  -1.3  -36%
(full 1d/3d/7d momentum, fwd 24h: IC -0.050, t -48.7, all three years negative)
quality composite (fwd 24h): IC +0.002 (t 2.4) -- essentially null at 1h
```

## Verdict — statistically huge, economically empty

The residual reversal is **real, year-stable and overwhelmingly significant** (IC up to −0.05,
t −48). But it is **completely untradeable**: fading it LOSES at every horizon and cost, and
catastrophically at short horizons (3h: −84%, Sharpe −10). The signature is textbook microstructure:
close-to-close reversal is largely bid-ask bounce, reverted by the next bar's open where the book
enters -- so a next-bar fade captures the wrong side, and costs finish it. Longer horizons (16-24h)
lose less but still lose. Quality/flow, which worked on daily, is null at 1h.

Conclusion: 1h does not add a tradeable edge. The strong 1h reversal is a microstructure artifact,
not alpha; the daily quality/flow book remains the real signal. (Confirms and generalizes the 5m/15m
fade result — now on the full multi-regime period with decisive significance.)

## Exhaustive run (hourly_full.py) — momentum / quality / ATR / EMA / BTC / baskets / funding

Everything we studied on daily, on 1h. Rank-IC vs the future residual (negative = reversal):

```text
1. MOMENTUM      mom_6h -0.018 -> mom_72h -0.042 -> mom_168h -0.042  (t up to -41, all years neg)
2. QUALITY       frac_pos +0.001 (null); burst -0.030 (t-39); path_eff -0.026; composite +0.002
3. ATR/EMA/ATH   ecandle/ATH/ATL weak; EMA-position -0.034..-0.045 (t-40) = price above EMA reverts
4. BTC/market    corr(book, market) -0.29, corr(book, btc) -0.18  = same net-short-beta tilt as daily
5. BASKETS       peer-residual convergence -0.022..-0.031 (t-30) = within-basket divergence reverts
6. FUNDING(1h)   unreliable at 1h (38-symbol coverage, sparse 8h->1h alignment); daily is the valid read
```

**Unifying result: every 1h section measures the SAME phenomenon — a strong, year-stable
mean-reversion** (recent movers, EMA-stretch, within-basket divergence, bursty/smooth paths all
revert). Statistically overwhelming (t up to -41) but untradeable (microstructure; the fade book
loses at every horizon and cost). Continuation/quality, which worked on daily, is null at 1h. The
book carries the same net-short-beta tilt as daily.

**Funding (daily, full period, valid read):** modest NEGATIVE rank-IC vs future residual
(fund_cum_30d -0.029, t-3.7 = crowded-long reversal), orthogonal to the momentum signal (corr -0.02)
but NOT year-stable (fails in 2024 bull). A minor candidate feature, not a game-changer.

**Overall: 1h adds no new tradeable edge.** The daily quality/flow book (Sharpe ~1.5) + beta-hedge
remains the only real signal; 1h is dominated by untradeable microstructure reversal.
