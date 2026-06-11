# Short Fade Session Timing Deep Dive

Date: 2026-06-11
Status: exploratory postprocess / no new sleeve promoted.
Commit: UNKNOWN.

This file records a session-aware timing deep dive over existing artifacts.

Script:

```text
research_tools/short_session_timing_deep_dive.py
```

Command:

```text
python research_tools/short_session_timing_deep_dive.py \
  --large-dir .output/results/large_runner_discovery_365d \
  --failed-dir .output/results/failed_pump_short_research_365d \
  --split-date 2025-12-03
```

Generated:

```text
.output/results/large_runner_discovery_365d/short_session_timing_by_session.csv
.output/results/large_runner_discovery_365d/short_session_close15_bands.csv
.output/results/large_runner_discovery_365d/short_session_entry_known_oos_slices.csv
.output/results/large_runner_discovery_365d/short_failed_frontier_by_session.csv
.output/results/large_runner_discovery_365d/short_session_timing_deep_dive.md
```

Important limitation:

```text
future60_low_break_offset_min is used only as an evaluation label.
It is not an entry feature.
```

## Main Finding

Session matters, but mostly through timing:

```text
Fader context is useful.
Late fader entry is bad.
Fresh post-entry fade is good.
Current entry-known proxies do not isolate fresh fade robustly.
```

No entry-known OOS slice passed promotion criteria:

```text
entry_known_slice_rows=276
promotion_candidates=0
```

## Why 15m Fader Context Fails As Entry

For the 15m fader classifier:

```text
close_ret_15m <= 3.39%
pre60_range_pct >= 10%
delay_min >= 15
```

OOS by session:

```text
session                trades  fresh_0_10  already_faded  no_low  weighted_avg
asia_europe_overlap      632      5.1%        82.3%       2.5%     +0.178R
europe_only             2140      1.5%        89.7%       7.3%     -0.095R
europe_us_overlap       1492      2.1%        83.9%      10.7%     -0.232R
us_only                 2296      7.1%        85.9%       6.3%     -0.308R
off_session              924      1.3%        94.4%       1.3%     -0.343R
asia_only               2880      1.7%        87.5%       8.2%     -0.505R
```

Trader translation:

```text
By the time the 15m weak-close classifier fires, the fader move is usually
already spent. The short then enters into chop, rebound, or no-continuation.
```

## Setup-Level Session Read

Weak close15 still separates faders from runners:

```text
close15 <= 0:
europe_only fader 100.0%, median base touch 6m
off_session fader 100.0%, median base touch 5m
us_only fader 98.5%, median base touch 4m
asia_only fader 95.3%, median base touch 4m
```

This is exactly why the entry is hard:

```text
The better the weak-close fader label, the more likely the base was touched
well before a 15m entry.
```

The middle zone remains a no-trade/static zone:

```text
3.39% < close15 <= 5.63%
```

The strong close15 zone remains a no-short / runner-veto zone, especially in
US trading hours.

## Session Interpretation

Europe/off-session:

```text
Weak close15 is a strong fader context, but the move is often already done.
Do not short the 15m close directly. Require:
- base not touched before entry;
- fresh failed retest / lower high after classifier;
- SL above retest high;
- no profit exit before at least 0.75R unless structural invalidation.
```

US session:

```text
Strong close15 is the clearest no-short condition.
Weak close15 can still fade, but squeezes/re-acceleration make direct 15m
shorts poor. Require the cleanest structure and tighter risk.
```

Asia:

```text
Weak close15 fader labels exist, but current local-high execution is
tail-sensitive and mostly already-faded. Require tighter risk and stronger
lower-high confirmation before treating Asia as a tradable sleeve.
```

Asia-Europe overlap:

```text
Some aggregates are positive, but breadth is weak and top dependence is high.
Treat as watchlist only.
```

## Failed-Pump Frontier Session Read

The current balanced failed-pump frontier still looks better than large-runner
local-high fading:

```text
asia_only core: 10 OOS trades, avg +0.534R, median +0.729R
us_only deep08: 10 OOS trades, avg +0.454R, median +0.963R
us_only core: 8 OOS trades, avg +0.431R, median +0.714R
europe_only deep08: 16 OOS trades, avg -0.112R, median -0.307R
europe_only core: 11 OOS trades, avg -0.066R, median -0.141R
```

This reinforces that session effects are strategy-family-specific. A session
that helps one fader nature can hurt another.

## Verdict

No new session-only sleeve should be added.

The next useful test is a true 1m path replay:

```text
15m fader classifier
AND base not already touched
THEN wait for fresh failed retest / lower high
ENTRY on failure/close-down after retest
SL behind retest high
target/management: no early profit exit before 0.75R, structural trail
```

This is the first large-runner short design that matches the market logic
shown by the session/timing evidence.
