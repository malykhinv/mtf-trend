# PRL win/lose full picture + can we cut losers? (protocol + result)

**Code:** `prl/research/loser_filter.py` · daily book name-trades, 15d hold, market-relative.
**Status:** IS. OOS reserved. Rigorous negative on loser-cutting (per-year stability + causal AUC + shuffle).

---

## 1. What separates winners from losers (full picture)

Standardized feature gap win−lose (std units); `*` = sign-stable across 2023/24/25:

```text
LONG  (win-rate 0.465)                     SHORT (win-rate 0.688)
  dvol_20        -0.28                        rsharpe_28   -0.30 *
  burst_28       -0.24 *                      rmom_14      -0.30 *
  rvol_14        -0.24                         rsharpe_14   -0.28 *
  rvol_28        -0.24                         rmom_28      -0.24 *
  avg_trade_size +0.23                         path_eff_28  -0.23 *
  resid_from_low -0.22                         rawmom_28    -0.23 *
  rmom_28        -0.17 *                        frac_pos_28  -0.22 *
  rmom_7         +0.16 *
```

Winning **longs** = calm (low vol), non-bursty, whale-sized trades, not extended, lower prior
momentum — but most separators are year-UNSTABLE (only burst/rmom hold sign). Winning **shorts** =
genuine laggards (low momentum / sharpe / path-efficiency / positive-day fraction) — and **every
short separator is sign-stable across all years** (a robust, real pattern).

## 2. How separable are losers (causal walk-forward win-classifier, OOF AUC)

```text
LONG  leg AUC = 0.441   (BELOW 0.5 -> long losers are NOT causally separable;
                          the in-sample separators are overfit / year-unstable)
SHORT leg AUC = 0.543   (modestly separable -- the 'genuine laggard' pattern generalizes)
```

## 3. Does cutting predicted losers help? (per-rebalance L/S + shuffle control)

Keep only the top-half predicted win-prob per leg:

```text
baseline (all)          CAGR~+93%   +rebs 74%
filtered (top-50% prob) CAGR~+86%   +rebs 74%
filtered on SHUFFLED    CAGR~+84%   +rebs 81%
```

**Cutting predicted losers does NOT help** — the filtered book (+86%) is *worse* than baseline
(+93%) and indistinguishable from the shuffle control (+84%). Even where the classifier has mild
skill (shorts, AUC 0.54), it does not convert to book improvement — the same win-rate≠profitability
gap: we cut small losers but also forgo winners, and reward is not aligned with win-probability.

## 4. Verdict

**There is no separable, cuttable "loser layer."** The full picture is real and interpretable
(winning shorts = true laggards, year-stable; winning longs = calm/whale/non-pumped), but:
- long-leg losers are not even causally predictable (AUC < 0.5),
- a causal loser-filter does not beat baseline or a shuffle control.

The ranker already extracts what is separable; a second-stage loser filter adds only noise. This
confirms the earlier trimming / pump-cut / high-vol-cut findings (all marginal). The daily book
stands as-is; loser-cutting is not a lever. Method note: every cut was validated with per-year
sign-stability AND a shuffle control before being believed.
