# PRL per-leg rolling market/BTC context & EMA (protocol + result)

**Code:** `prl/research/leg_market_context.py` · **Status:** IS. OOS reserved.

---

## Question (user)

Separately for the long and short legs, how do rolling 1/2/3/7/14d returns of the market and of
BTC, and market/BTC position vs EMA, affect the leg's outcome?

## Result (55 non-overlapping rebalances; corr of causal entry context vs the leg's next-15d raw return)

```text
LONG leg  (* = sign-stable across years)      SHORT leg
  btc_ret_3d  +0.12                              mkt_ret_14d +0.12 *
  mkt_ret_14d -0.09 *                            mkt_ret_7d  +0.11
  mkt_ema200  -0.08 *                            mkt_ema200  +0.06 *
  mkt_ret_7d  -0.06                              mkt_ema20   +0.05 *
```

Interpretable, partly year-stable signs = a weak **market mean-reversion** tilt: after the market
has been up (7-14d) or above its 200-EMA, the next 15d slightly favors the SHORT leg and slightly
hurts the LONG leg (the market tends to revert). BUT the magnitudes are tiny — max |corr| ≈ 0.12 on
55 observations (t ≈ 0.9, not significant). Rolling market/BTC context and EMA position do **not**
provide a strong predictive leg-conditioning signal.

## Reconciliation

- **Contemporaneously** each leg is strongly market-driven (long = long-beta, short = short-beta;
  per-leg gaps ±1.2..1.6 in the week-context study) — this is the real, strong structural fact.
- **Predictively** (trailing rolling context -> next-period leg return) the relationship is weak /
  within noise. Recent market trend / EMA position does not forecast the leg's next period.

So the actionable lever is the **structural beta hedge** (remove the contemporaneous market
exposure — see prl_week_context_beta_v1.md), not entry-time market-trend timing of the legs. Digging
separately per leg confirms the beta finding and closes market-trend timing as a lever.
