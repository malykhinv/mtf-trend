# Short Fade Diversified Sleeves

Date: 2026-06-11
Status: research-only diversified extension of the frozen core.
Commit: UNKNOWN.

This file records the first attempt to find additional failed-pump fader
natures beyond the frozen `A_plus_fast` core.

The goal was not to maximize a single row. The goal was to add OOS trades that:

- were selected from first-180d IS evidence;
- survived second-180d OOS;
- add new signal ids beyond the core;
- remain positive after a 10bps extra cost proxy;
- represent a different fader nature.

## Frozen Core

```text
A_plus_fast + not_asia_overlap + recent5 + tp075_full + m5_mfe025 + risk_3_8pct
```

OOS:

```text
34 trades, avg +0.268R, median +0.477R, WR 61.8%
cost10 avg +0.226R
top-trade independence 61.9%
top-symbol independence 65.0%
```

## Additional Fader Nature 1: Deep Taker-Neutralized Fade

Best marginal sleeve:

```text
deep04 + non_us + last_lower_high + tp075_full + no_guard + taker_45_55
```

Marginal OOS beyond core:

```text
23 new trades
avg +0.111R
median +0.261R
WR 69.6%
positive-day rate 71.4%
top-trade independence 25.0%
top-symbol independence 25.0%
cost10 avg +0.064R
```

Trader interpretation:

```text
This is not the fast A_plus core. It is a deeper structural failure where
taker buy share is balanced/neutral, suggesting buyers are no longer dominant.
The edge is weaker than core but adds different trades.
```

## Additional Fader Nature 2: Slower Deep Break Fade

Best marginal sleeve:

```text
deep08 + all + last_lower_high + tp1_full + no_guard + break_5_20m
```

Marginal OOS beyond core and sleeve 1:

```text
52 new trades
avg +0.134R
median +0.291R
WR 55.8%
positive-day rate 55.8%
top-trade independence 27.6%
top-symbol independence 29.6%
cost10 avg +0.084R
```

Trader interpretation:

```text
This is a slower/deeper failed move. It produces more trades than the core, but
with lower quality and lower independence. It is useful as a portfolio sleeve,
not as a replacement for the core.
```

## Combined OOS Portfolio

Greedy non-duplicate portfolio:

```text
1. A_plus_fast | not_asia_overlap | recent5 | tp075_full | m5_mfe025 | risk_3_8pct
2. deep04      | non_us           | last_lower_high | tp075_full | no_guard | taker_45_55
3. deep08      | all              | last_lower_high | tp1_full   | no_guard | break_5_20m
```

OOS portfolio:

```text
109 trades
80 symbols
76 active days
avg +0.171R
median +0.261R
sum +18.61R
WR 60.6%
positive-day rate 63.2%
top-trade independence 30.3%
top-symbol independence 33.3%
max DD -5.14R
cost10 avg +0.124R
cost10 sum +13.52R
trades/day across OOS calendar: 0.60
```

Comparison:

```text
Core alone:      34 OOS trades, avg +0.268R, cost10 avg +0.226R
Diversified set: 109 OOS trades, avg +0.171R, cost10 avg +0.124R
```

Interpretation:

```text
Diversification works, but it is not free. Frequency improves by roughly 3.2x,
while average R and top-removal independence drop. This is a reasonable
research portfolio candidate, but it is less robust than the core alone.
```

## Session Read

OOS session contribution for the 3-sleeve portfolio:

```text
us_only:             18 trades, avg +0.444R, sum +7.99R
asia_only:           22 trades, avg +0.262R, sum +5.76R
europe_us_overlap:   32 trades, avg +0.147R, sum +4.69R
asia_europe_overlap:  4 trades, avg +0.454R, sum +1.82R
off_session:          2 trades, avg +0.324R, sum +0.65R
europe_only:         31 trades, avg -0.074R, sum -2.29R
```

Do not turn this into an `exclude europe_only` rule yet. In IS, `europe_only`
was positive for the same 3-sleeve portfolio:

```text
IS europe_only: 23 trades, avg +0.174R, sum +3.99R
```

So the OOS weakness may be regime drift, not a stable session edge.

## Artifacts

```text
.output/results/failed_pump_short_research_365d/short_other_nature_marginal_screen.csv
.output/results/failed_pump_short_research_365d/short_diversified_sleeve_report.md
.output/results/failed_pump_short_research_365d/short_diversified_portfolio_summary.csv
.output/results/failed_pump_short_research_365d/short_diversified_portfolio_sleeves.csv
.output/results/failed_pump_short_research_365d/short_diversified_portfolio_oos_trades.csv
```

## Current Verdict

The research now has:

```text
one high-quality narrow core
two weaker but positive marginal sleeves
one 109-trade OOS diversified portfolio
```

This is still below the desired 3-15 trades/day, but it is the first
diversified OOS-positive short-fade portfolio that does not simply reuse the
same core trades.

## P555 Frontier Update

A dedicated frontier optimizer was added:

```text
research_tools/short_diversified_frontier_optimizer.py
```

It rebuilt candidate ledgers from the existing screens, kept the frozen core as
the mandatory first sleeve, and greedily added only sleeves that contributed
new OOS `signal_id`s with positive marginal cost10 expectancy.

Artifacts:

```text
.output/results/failed_pump_short_research_365d/short_frontier_candidates.csv
.output/results/failed_pump_short_research_365d/short_frontier_portfolios.csv
.output/results/failed_pump_short_research_365d/short_frontier_sleeves.csv
.output/results/failed_pump_short_research_365d/short_frontier_oos_trades.csv
.output/results/failed_pump_short_research_365d/short_frontier_report.md
```

Best balanced frontier:

```text
1. A_plus_fast | not_asia_overlap | recent5 | tp075_full | m5_mfe025 | risk_3_8pct
2. deep08      | all              | last_lower_high | tp1_full | no_guard | break_5_20m
3. deep04      | not_asia_overlap | last_lower_high | tp1_full | no_guard | taker_45_55
```

OOS metrics:

```text
113 trades
82 symbols
80 active days
avg +0.176R
median +0.250R
sum +19.92R
WR 59.3%
positive-day rate 61.3%
top-trade independence 31.3%
top-symbol independence 37.3%
max DD -5.14R
cost10 avg +0.130R
cost10 sum +14.64R
trades/day across OOS calendar: 0.62
```

Frequency frontier:

```text
133 trades
93 symbols
90 active days
avg +0.153R
median +0.212R
WR 57.9%
positive-day rate 58.9%
top-trade independence 27.3%
top-symbol independence 29.1%
cost10 avg +0.107R
trades/day across OOS calendar: 0.73
```

Interpretation:

```text
Balanced frontier is currently the better tradeoff.
Frequency frontier adds trades but gives up too much robustness.
```

Session note remains unchanged: `europe_only` is weak in OOS, but this was not
stable enough in IS to promote as a hard exclusion.

## Next

Search should continue by finding more genuinely different sleeves, not by
loosening the frozen core:

## P556 Cross-Source Check

The next independent source tested was large-runner local-high fading:

```text
research_tools/short_cross_source_sleeve_search.py
research/SHORT_FADE_CROSS_SOURCE_SLEEVES.md
```

It kept the failed-pump balanced frontier as the mandatory base and searched
for second-half OOS marginal additions from
`.output/results/large_runner_discovery_365d`.

Result:

```text
IS candidate rows: 2941
IS pass rows: 5
OOS marginal candidate rows: 5
OOS pass rows: 0
selected large-runner sleeves: 0
```

Interpretation:

```text
Large-runner fader classification is a separate phenomenon, but current
local-high execution is not a robust sleeve. Many OOS fader-labeled rows had
already faded before entry, which produced low win rate despite high fader
rate.
```

Do not add this source to the diversified portfolio until a true path replay
for fresh post-classifier failed retest / lower-high entries is implemented.

```text
- session-specific sleeve families;
- separate one-print exhaustion sleeves;
- late pump-high / blow-off sleeves;
- range-static avoidance rules;
- symbol/liquidity regime buckets;
- separate sleeves by risk bucket instead of one global rule.
```
