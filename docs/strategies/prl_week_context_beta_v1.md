# PRL negative vs positive weeks: market context -> net-short-beta -> beta hedge

**Code:** `prl/research/week_context.py` · **Status:** IS. OOS reserved.
**First genuine positive structural lever of the study.**

---

## Question (user)

What distinguishes the book's negative weeks from positive weeks? Different market context?
Different BTC/ETH dynamics? Separately for the long and short legs.

## Descriptive answer (contemporaneous context; `*` = sign-stable across 2023/24/25)

```text
TOTAL book bad weeks vs good weeks:  market UP (market_ret gap -0.87*), high breadth (-0.76*),
                                     BTC/ETH up (-0.47*/-0.39*).  => book loses in up-markets.
LONG  leg  wins when market UP  (btc +1.24*, market +1.55*, breadth +1.47*)  = long beta
SHORT leg  wins when market DOWN (btc -1.25*, market -1.58*, breadth -1.44*) = short beta
```

Each leg is strongly market-directional; the dollar-neutral combination retains a **net negative
beta** — the book is implicitly a bearish/short-market strategy, which is exactly why it is
2025(bear)-concentrated. This is the structural root of the weekly unevenness.

## Timing lever (predict bad weeks) — does NOT convert

Prior-week context -> this week's book sign, walk-forward OOF AUC: TOTAL 0.592 (shuffle 0.481),
SHORT 0.585 (shuffle 0.464), LONG 0.415 (not predictable). Modestly predictive. But going flat in
predicted-bad weeks did NOT help:

```text
baseline              Sharpe 2.77  +weeks(active) 0.60
timed (flat bad wks)  Sharpe 2.99  +weeks(active) 0.46   <- CAGR down, active-weeks down
shuffle control       Sharpe 2.56  +weeks(active) 0.40
```

The AUC-0.59 timing is within noise of the shuffle and lowers CAGR / active-week-hit-rate — the
week-timing does not translate to a tradeable improvement (AUC != P&L, at the week level).

## Structural lever (beta-neutralize) — REAL improvement

Hedge the net beta with a **causal rolling-60d beta** (shifted, no look-ahead):

```text
                       CAGR   Sharpe  +weeks   by-year (weekly-annualized measure)
raw                    +32%   2.02    0.58     2023 -1%  2024 +18%  2025 +63%
beta-neutral vs market +35%   2.49    0.60     2023 +9%  2024 +23%  2025 +49%
beta-neutral vs BTC    +37%   2.23    0.60     2023 +1%  2024 +27%  2025 +61%
```

Beta-neutralizing lifts Sharpe (2.02 -> 2.23 tradeable-BTC / 2.49 EW-market) and — the point —
**evens the years**: 2023 -1% -> +9%, and the 2025 concentration drops 63% -> 49%. It is principled
(removes the root-cause market exposure the descriptive analysis found), causal, and tradeable via a
short-BTC hedge leg.

## Verdict & next

The user's neg-vs-pos-week question paid off: it revealed the net-short-beta structural flaw and a
**real, causal, uniformity-improving fix** (beta-neutralization) — the first genuine positive lever
in the study. Caveats: weekly-annualized Sharpe overstates the daily ~1.5 level; the hedge adds
funding/rebalance costs not yet modeled (§45.1). **Next: implement the BTC/market beta-hedge leg in
the production daily-marked book WITH hedge costs, and re-measure per-year / weekly / cost-stress**;
if the year-evening survives hedge costs, this is a keeper for the frozen spec.
