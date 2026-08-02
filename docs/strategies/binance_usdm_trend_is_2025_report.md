# Binance USD-M trend: calendar-2025 IS report

Run date: **2026-07-20**. Protocol partition: calendar 2025 IS only; rows from
2026 were neither aggregated nor inspected.

## Data audit

- Source: existing local `enriched_1m` parquet cache; network access disabled.
- Source files checked: 749 USDT contracts; schema failures: 0.
- Contracts with calendar-2025 rows: 610.
- Observed interval: 2025-06-03 through 2025-12-31. The missing first five
  months were not backfilled.
- Aggregated rows: 114,805; complete daily rows: 114,656 (99.87%).
- OI coverage: 92.60% of daily rows and 90.67% of observed minutes.
- Point-in-time universe: 24 distinct contracts appeared in the monthly top-20.

## Research population

- Futures horizons: `(3, 5, 7, 10, 14, 20, 30, 60, 90)`.
- Feature rows: 2,440.
- Causally resolved 20-day labels: 2,040, from 2025-09-01 through 2025-12-11.
- Rows with `trend_signal > 0`: 1,128.
- Feature schema: `usdm_trend_features_v4_breakout_flow_is_2025`.
- Numeric candidate features: 375 across trend/path, volatility/range,
  liquidity/flow, OI/positioning and cross-section/market families.
- Exact pre-breakout events reconstructed from 1m data: 1,015 across 24
  contracts.

Only four monthly blocks remain after warm-up. The preregistered bootstrap
requires six, so FDR q-values are intentionally unresolved and zero features
pass the formal exploratory gate. This is insufficient history, not evidence
that every feature has zero information.

## Baseline EV result

The long-only 20-day forward return was negative for every Donchian
active-count bucket. Mean forward return ranged from -19.3% (`active_count=4`)
to -4.6% (`active_count=9`). Even the strongest signal bucket remained
negative, with only 33.9% positive forward returns. Across dates, active count
had mean daily rank IC of approximately -0.013 against the risk-adjusted
target.

Monthly mean forward returns were also all negative in the resolved sample:

| Month | Mean return | Positive share |
|---|---:|---:|
| 2025-09 | -12.0% | 19.0% |
| 2025-10 | -20.4% | 7.1% |
| 2025-11 | -11.2% | 21.5% |
| 2025-12 through day 11 | -13.2% | 7.7% |

The prerequisite positive-EV gate therefore fails. CatBoost, portfolio
simulation and OOS inspection are not scientifically justified by this run.

## Pre-breakout volume and trade-count hypothesis

For each daily-confirmed entry, the event layer finds the first minute close
above the lowest newly entered Donchian threshold. The breakout minute is
excluded. The preceding 5/15/30/60 minutes are compared with the prior 30 days
at the same UTC clock time, requiring at least 10 complete baselines.

There are 446 labeled trend-positive event rows (39.5% coverage); 402 rows on
33 dates have at least five same-day events for cross-sectional testing.

Result: **0 of 24** combinations of measure, window and anomaly normalization
passed the frozen composite check (positive IC, advantage over shuffled
control, positive Q5-Q1 spread, positive Q5 absolute return, and monotonicity
at least 0.8).

Notable exploratory values:

| Feature | Daily IC | Control IC | Q5-Q1 target | Q5 forward return | Monotonicity |
|---|---:|---:|---:|---:|---:|
| 5m quote-volume log-ratio | +0.103 | +0.136 | +0.070 | -16.4% | 0.8 |
| 15m trade-count percentile | +0.076 | +0.041 | +0.058 | -16.0% | 0.8 |
| 60m trade-count log-ratio | +0.080 | +0.069 | +0.143 | -14.8% | 0.3 |

Thus “more anomalous pre-breakout activity is more interesting” is not
supported as an absolute long-only EV rule in this IS. The 15-minute trade
count percentile is the cleanest relative-ranking hint, but its absolute EV is
strongly negative and the sample has only four post-warm-up months.

## Causal first-crossing audit

The daily-confirmed cohort is valid only for a next-day decision. It would be
lookahead if treated as the population known at the first intraday crossing.
The causal event layer was therefore rebuilt to retain every first crossing,
including later failures. The snapshot is the close of the crossing minute;
all pre-crossing windows end before that minute.

- Causal first crossings: 1,026.
- Later daily-close confirmed: 49.51%.
- Resolved future paths: 1,026 at 1h and 4h, 1,016 at 1d, 993 at 3d and 962 at
  5d.
- Signal density: 8.41 unique crossings per calendar day over the 122-day
  crossing span (9.24 per active day). The four stop timeframes are alternative
  policies for one crossing and are not counted as four entries. The target
  research density is at most 3 entries per calendar day, so a causal quality
  filter is required; no within-day quota is used.

On this non-selected cohort, stronger pre-crossing flow anomaly generally
describes exhaustion rather than continuation. For example, 30m quote-volume
robust z-score has approximately -0.186 rank IC with the 1d continuation
score; 60m volume anomaly has approximately -0.204 rank IC with 1h MAE.
These are exploratory estimates because only four calendar blocks survive.

## Structural-stop experiment

Two causal stop definitions were evaluated independently on the same resolved
five-day paths. Neither uses daily candles, a fixed percentage, or an ATR
multiple.

1. Raw v1: a 2-left/2-right confirmed swing low on 5m/15m/30m/1h.
2. Mechanically confirmed v2: the same fractal is only a candidate; it becomes
   admissible after a later completed bar closes above the latest still-unbroken
   prior swing high. The stop is trailed only to a newly confirmed higher low
   and can never be lowered.

Protected anchors widened the median initial risk and lengthened holding time,
but did not cure the stop-out/recovery pathology:

| Stop TF | Raw median risk | Protected median risk | Protected median hold | Protected recovery after stop |
|---|---:|---:|---:|---:|
| 5m | 1.03% | 1.25% | 83 min | 95.0% |
| 15m | 1.61% | 2.11% | 292 min | 91.6% |
| 30m | 2.19% | 3.04% | 618 min | 87.8% |
| 1h | 3.01% | 3.93% | 1,130 min | 83.2% |

All 5m/15m/30m protected policies and 960 of 961 1h policies hit a structural
stop inside five days. Among losing trades, 92.5%, 87.3%, 82.5% and 76.1%
respectively later recovered the entry before the five-day horizon. The
mechanical confirmation removes some micro-fractals but is not sufficient to
identify a structural low that is visually obvious on the chart. It remains a
rejected benchmark, not the definition of visual structure.

### Which stop timeframe is best?

No timeframe is selected from this IS. The paired and calendar-block checks do
not identify a robust winner:

| TF | Win rate | Mean R | Median R | Mean return | Positive months |
|---|---:|---:|---:|---:|---:|
| 5m | 33.1% | -0.055 | -0.238 | +0.02% | 1/4 |
| 15m | 33.5% | -0.033 | -0.225 | -0.08% | 2/4 |
| 30m | 30.6% | -0.040 | -0.254 | -0.23% | 2/4 |
| 1h | 30.3% | +0.139 | -0.285 | -0.36% | 2/4 |

The positive 1h mean R is not robust: one XRPUSDT trade has an initial risk of
0.0249% and contributes +200.4R. The 1h median R and mean return remain
negative, and its paired advantage over the other TFs appears in only half of
the calendar months against 15m and 30m. This tail also shows that a mechanically
confirmed low can still be an economically degenerate, visually minor anchor.

### What separates winners and losers without lookahead?

Only values known by `snapshot_time` are admitted to this comparison. Later
daily-close confirmation has been removed from both the feature table and the
context slices. The strongest remaining effects are short-window activity
anomalies, but none is stable across all four stop timeframes:

- 5m trade-count robust z-score: median absolute SMD 0.171, winner direction
  higher on three of four timeframes.
- 5m trade-count percentile: median absolute SMD 0.168, higher on three of four.
- 15m trade-count robust z-score: median absolute SMD 0.156, higher on three of
  four.
- Crossing time: median absolute SMD 0.117, later for winners on all four.

These are weak IS associations, not a validated filter. Future retention,
failure, continuation and trade result remain labels only; they are never
features of the row being predicted.

## Desk audit surface

The existing interactive Desk has a read-only `Futures IS` view over all 3,847
mechanical-benchmark trades. Each chart uses the trade's stop timeframe and shows the
entry-to-exit interval, the red initial-risk region, the green realized-MFE
opportunity region, breakout level, entry, exit, and every causal stop update.
The green region is not a take-profit target; it is descriptive realized MFE.
The view does not modify research artifacts.

## Scientifically defensible next step

CatBoost can now be used only as an IS proof in two separate weekly
walk-forward tasks. First, an entry-quality model predicts early level
retention/failure and continuation from snapshot-time features; its frozen
absolute probability threshold targets no more than 3 entries per calendar day
on preceding training weeks. Second, after visually structural low candidates
are explicitly labeled, a candidate-ranking model estimates early-stop risk for
each already-known anchor. It may rank structural prices but may not invent a
price or move the stop to a non-structural level.

The next data step is therefore a visual structural-low atlas in Desk: initial
anchor, later higher anchors, and rejection reasons for the mechanical
candidate. All candidates from one event must stay in the same weekly fold.
Only causal features enter either model; future paths and trade outcomes are
labels. OOS remains untouched until the weekly IS proof demonstrates calibrated
early predictability and a positive-EV region at the required frequency.
