# Short Fade Cross-Source Sleeves

Date: 2026-06-11
Status: research frontier / no promotable new sleeve found.
Commit: UNKNOWN.

This file records the attempt to add a genuinely different short-fade source:
large-runner local-high fades from
`.output/results/large_runner_discovery_365d`.

The intent was to avoid merely loosening the existing failed-pump structural
frontier. The base portfolio was kept fixed:

```text
failed-pump balanced frontier:
113 OOS trades, avg +0.176R, median +0.250R, cost10 avg +0.153R
```

## Method

Script:

```text
research_tools/short_cross_source_sleeve_search.py
```

Command:

```text
python research_tools/short_cross_source_sleeve_search.py \
  --large-dir .output/results/large_runner_discovery_365d \
  --failed-dir .output/results/failed_pump_short_research_365d \
  --split-date 2025-12-03
```

Rules:

```text
1. Select large-runner candidates on rows before 2025-12-03.
2. Verify on rows from 2025-12-03.
3. Reject descriptors not known by entry time:
   close15 rules require delay_min >= 15,
   close10 rules require delay_min >= 10.
4. Remove same-symbol 60m collisions with the failed-pump base portfolio.
5. Add only marginal sleeves with positive OOS median and positive 10bps cost
   proxy.
```

Generated:

```text
.output/results/large_runner_discovery_365d/short_cross_source_sleeve_candidates.csv
.output/results/large_runner_discovery_365d/short_cross_source_sleeve_oos_candidates.csv
.output/results/large_runner_discovery_365d/short_cross_source_sleeves.csv
.output/results/large_runner_discovery_365d/short_cross_source_portfolio_oos_trades.csv
.output/results/large_runner_discovery_365d/short_cross_source_sleeve_report.md
```

## Result

```text
IS candidate rows: 2941
IS pass rows: 5
OOS marginal candidate rows: 5
OOS pass rows: 0
selected large-runner sleeves: 0
combined OOS trades: 113
combined avg: +0.176R
combined cost10 avg: +0.153R
```

The only IS-passing source shape was essentially:

```text
early_return>=6% & close15<=4%
delay 15
recent10 stop
mostly europe_only or risk_1_3 filters
```

It did not survive OOS. The best OOS marginal row had many trades but negative
median:

```text
early_return>=6% & close15<=4%
delay 15
recent10
half_base_low
risk_1_3

OOS marginal:
165 trades
avg +0.094R
median -0.151R
WR 43.0%
cost10 avg +0.036R
top-trade independence 4.2%
top-symbol independence 5.3%
```

This is not robust enough to add.

## Why Fader Rate Did Not Become Win Rate

The large-runner fader classifier is good at saying that price eventually
reaches the base/lower area, but that is not the same as an executable short
win.

Second-half OOS local-high replay timing:

```text
already_faded_before_entry: 26224 trades, avg -0.277R, median -0.489R, WR 27.3%, fader_rate 99.1%
no_low_break:                3792 trades, avg -0.591R, median -1.042R, WR 16.4%, fader_rate 0.0%
fade_0_5_after_entry:        1284 trades, avg +0.822R, median +0.255R, WR 70.2%
fade_5_10_after_entry:        472 trades, avg +0.969R, median +0.210R, WR 53.4%
fade_10_20_after_entry:       868 trades, avg +0.182R, median -0.403R, WR 37.6%
fade_late_20plus:             696 trades, avg -0.114R, median -0.391R, WR 38.9%
```

Trader translation:

```text
The edge is not "this pump is a fader".
The edge would be "this pump has not already spent the fade, and the next leg
down starts after our entry before stop pressure returns".
```

Current entry-known proxies do not isolate that timing robustly.

## Verdict

Large-runner local-high fading is a real separate market phenomenon, but the
current executable version is not a promotable sleeve. It adds frequency only
by accepting low median, low top-removal, and high already-faded-before-entry
risk.

Do not add this source to the production candidate portfolio yet.

## Next Research Direction

The useful next test is not another filter on the same local-high replay. It is
a new execution design for the large-runner fader classifier:

```text
Fader classifier at 15m ->
wait for first fresh post-classifier lower-high / failed retest ->
require base not touched before entry ->
enter only if the breakdown leg starts after entry ->
trail structurally.
```

This needs a true path replay. The current aggregate replay cannot prove it
without using evaluation-only fade timing.
