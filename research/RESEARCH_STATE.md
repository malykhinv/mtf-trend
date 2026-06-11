## 2026-06-11 - P552 IS-max / OOS verification

Current commit: UNKNOWN. Status: analysis only / OOS consumed for tested
shortlists.

The first 180 days were used to rank and freeze the best entry-known feature
shortlists. The second 180 days were then evaluated once. Post-entry features
were not allowed as hindsight filters; when tested, they were treated as
executable management checks that exit at the selected minute if continuation
is missing.

Generated:

```text
research_tools/short_is_oos_max_verifier.py
.output/results/failed_pump_short_research_365d/short_is_oos_max_verification_candidates.csv
.output/results/failed_pump_short_research_365d/short_is_oos_max_verification_portfolios.csv
.output/results/failed_pump_short_research_365d/short_is_oos_max_verification_report.md
.output/results/failed_pump_short_research_365d/short_is_oos_guard_expansion_sanity.csv
.output/results/failed_pump_short_research_365d/short_is_oos_guard_expansion_sanity_report.md
```

Anchored period:

```text
IS:  2025-06-06 -> 2025-12-03
OOS: 2025-12-03 -> 2026-06-02
selected frozen candidates: 160
OOS passing candidates: 38
OOS passing rank portfolios: 3
```

Best OOS core after guard expansion:

```text
A_plus_fast
session_filter=not_asia_overlap
stop_model=recent5
base_policy=tp075_full
feature=risk_3_8pct
guard plateau: m5_mfe025 / m10_mfe025 / m10_mfe050 / m15_mfe050
```

Best independence row:

```text
A_plus_fast + not_asia_overlap + recent5 + tp075_full + m5_mfe025 + risk_3_8pct
IS:  33 trades, avg +0.237R, median +0.710R
OOS: 34 trades, avg +0.268R, median +0.477R, WR 61.8%
OOS positive-day rate 62.1%
OOS top-trade independence 61.9%
OOS top-symbol independence 65.0%
OOS avg after 10bps extra cost proxy +0.226R
```

Score-leading variants:

```text
m10_mfe050: OOS 34 trades, avg +0.264R, median +0.491R, WR 70.6%,
top-trade 54.2%, top-symbol 59.1%.

m15_mfe050: OOS 34 trades, avg +0.256R, median +0.710R, WR 67.6%,
top-trade 52.2%, top-symbol 54.5%.

m10_mfe025: OOS 34 trades, avg +0.252R, median +0.713R, WR 67.6%,
top-trade 52.2%, top-symbol 54.5%.
```

Portfolio read:

```text
entry_top3:  OOS 89 trades, avg +0.041R, median +0.218R, WR 55.1%.
entry_top5:  OOS 136 trades, avg +0.045R, median +0.118R, WR 53.7%.
entry_top10: OOS 140 trades, avg +0.040R, median +0.090R, WR 52.9%.
```

These portfolios pass loose OOS positivity but fail cost stress:

```text
entry_top3 cost10bps avg -0.015R
entry_top5 cost10bps avg -0.012R
entry_top10 cost10bps avg -0.016R
```

Interpretation:

The strongest verified edge is narrow but real-looking: a low-frequency
`A_plus_fast` fade sleeve, outside Asia overlap, with risk between 3-8%, recent
local-high stop, no profit exit before 0.75R, and an MFE continuation guard.
It finally meets the user's 50%+ top-removal target on the second 180d half.

The attempt to expand frequency by combining many IS-ranked candidates degrades
quickly and is not robust after realistic extra cost. The target of 3-15 trades
per day is not supported by this sleeve alone.

Next:

Do not tune this OOS further. Convert the best core into one explicit strategy
spec and run only stress/live-forward checks: exact exchange-like entry delay,
extra slippage/fees, monthly non-overlap, and a new unseen period when data is
available.

Follow-up:

The frozen core and lookahead audit were written to:

```text
research/SHORT_FADE_CORE_EDGE.md
.output/results/failed_pump_short_research_365d/short_core_lookahead_audit.csv
```

Lookahead audit summary for the exact frozen core:

```text
core rows: 67
IS rows: 33
OOS rows: 34
future_label_available_at_entry true: 0
short_confirm_closed_before_entry false: 0
entry before confirm close: 0
non-60s confirm candles: 0
selection_eligible false: 0
entry_minus_confirm_close_ms: 0 for all rows
```

## 2026-06-11 - P554 diversified short-fade sleeves

Current commit: UNKNOWN. Status: analysis only / OOS consumed for tested
diversified sleeves.

The search was extended beyond the frozen core to find genuinely different
fader natures. The key check was marginal OOS performance after removing signal
ids already traded by the core.

Generated:

```text
research/SHORT_FADE_DIVERSIFIED_SLEEVES.md
.output/results/failed_pump_short_research_365d/short_other_nature_marginal_screen.csv
.output/results/failed_pump_short_research_365d/short_diversified_sleeve_report.md
.output/results/failed_pump_short_research_365d/short_diversified_portfolio_summary.csv
.output/results/failed_pump_short_research_365d/short_diversified_portfolio_sleeves.csv
.output/results/failed_pump_short_research_365d/short_diversified_portfolio_oos_trades.csv
```

OOS portfolio:

```text
A_plus_fast + not_asia_overlap + recent5 + tp075_full + m5_mfe025 + risk_3_8pct
deep04 + non_us + last_lower_high + tp075_full + no_guard + taker_45_55
deep08 + all + last_lower_high + tp1_full + no_guard + break_5_20m
```

Metrics:

```text
109 OOS trades
80 symbols
76 active days
avg +0.171R
median +0.261R
WR 60.6%
positive-day rate 63.2%
top-trade independence 30.3%
top-symbol independence 33.3%
cost10 avg +0.124R
trades/day across OOS calendar 0.60
```

Interpretation:

Diversification improves frequency by roughly 3.2x versus the core alone but
reduces average R and top-removal independence. This is a useful portfolio
candidate, not yet the desired 3-15 trades/day solution.

## 2026-06-11 - P555 short-fade frontier optimizer

Current commit: UNKNOWN. Status: analysis only / research frontier, not fresh
OOS proof.

Added a portfolio frontier optimizer over existing candidate screens:

```text
research_tools/short_diversified_frontier_optimizer.py
```

Generated:

```text
.output/results/failed_pump_short_research_365d/short_frontier_candidates.csv
.output/results/failed_pump_short_research_365d/short_frontier_portfolios.csv
.output/results/failed_pump_short_research_365d/short_frontier_sleeves.csv
.output/results/failed_pump_short_research_365d/short_frontier_oos_trades.csv
.output/results/failed_pump_short_research_365d/short_frontier_report.md
```

Best balanced OOS frontier:

```text
113 trades
82 symbols
80 active days
avg +0.176R
median +0.250R
WR 59.3%
positive-day rate 61.3%
top-trade independence 31.3%
top-symbol independence 37.3%
cost10 avg +0.130R
trades/day 0.62
```

Sleeves:

```text
A_plus_fast + not_asia_overlap + recent5 + tp075_full + m5_mfe025 + risk_3_8pct
deep08 + all + last_lower_high + tp1_full + no_guard + break_5_20m
deep04 + not_asia_overlap + last_lower_high + tp1_full + no_guard + taker_45_55
```

Frequency frontier:

```text
133 trades
avg +0.153R
median +0.212R
cost10 avg +0.107R
top-trade independence 27.3%
top-symbol independence 29.1%
trades/day 0.73
```

Interpretation:

Balanced frontier is the current best tradeoff. Frequency can be pushed to
~0.73 trades/day, but robustness and independence degrade. The next search
should find new independent sleeves rather than keep adding marginally related
deep04/deep08 variants.

## 2026-06-11 - P556 cross-source large-runner sleeve search

Current commit: UNKNOWN. Status: analysis only / no promotable new sleeve.

Added a cross-source sleeve search:

```text
research_tools/short_cross_source_sleeve_search.py
research/SHORT_FADE_CROSS_SOURCE_SLEEVES.md
```

Generated:

```text
.output/results/large_runner_discovery_365d/short_cross_source_sleeve_candidates.csv
.output/results/large_runner_discovery_365d/short_cross_source_sleeve_oos_candidates.csv
.output/results/large_runner_discovery_365d/short_cross_source_sleeves.csv
.output/results/large_runner_discovery_365d/short_cross_source_portfolio_oos_trades.csv
.output/results/large_runner_discovery_365d/short_cross_source_sleeve_report.md
```

Result:

```text
IS candidate rows: 2941
IS pass rows: 5
OOS marginal candidate rows: 5
OOS pass rows: 0
selected large-runner sleeves: 0
```

Main finding:

The large-runner fader classifier is a real separate phenomenon, but the
current local-high executable replay does not convert it into a robust sleeve.
In OOS, many fader-labeled rows had already faded before entry and still lost:

```text
already_faded_before_entry: 26224 trades, avg -0.277R, median -0.489R,
WR 27.3%, fader_rate 99.1%.
```

Next:

Do not add large-runner local-high sleeves to the portfolio yet. The useful
next test is a true path replay for a fresh post-classifier failed retest /
lower-high entry after 15m, requiring that the base was not already touched
before entry.

## 2026-06-11 - P557 session timing deep dive

Current commit: UNKNOWN. Status: analysis only / no session-only sleeve.

Added:

```text
research_tools/short_session_timing_deep_dive.py
research/SHORT_FADE_SESSION_TIMING_DEEP_DIVE.md
```

Generated:

```text
.output/results/large_runner_discovery_365d/short_session_timing_by_session.csv
.output/results/large_runner_discovery_365d/short_session_close15_bands.csv
.output/results/large_runner_discovery_365d/short_session_entry_known_oos_slices.csv
.output/results/large_runner_discovery_365d/short_failed_frontier_by_session.csv
.output/results/large_runner_discovery_365d/short_session_timing_deep_dive.md
```

Output:

```text
session_timing_rows=64
setup_band_rows=15
entry_known_slice_rows=276
promotion_candidates=0
failed_session_rows=15
```

Main finding:

Session matters mostly through timing, not as a standalone alpha filter. For
the 15m fader classifier, most OOS trades were already faded before entry:

```text
asia_europe_overlap: 82.3% already faded, +0.178R weighted avg
europe_only:         89.7% already faded, -0.095R weighted avg
europe_us_overlap:   83.9% already faded, -0.232R weighted avg
us_only:             85.9% already faded, -0.308R weighted avg
off_session:         94.4% already faded, -0.343R weighted avg
asia_only:           87.5% already faded, -0.505R weighted avg
```

Interpretation:

Weak `close15` is useful for classifying fader context, but it usually fires
after the base touch. A session-aware strategy should use it as context/veto,
then wait for a fresh failed retest / lower high if the base was not already
touched.

## 2026-06-11 - P558 neutral robust plateau engine

Current commit: UNKNOWN. Status: implemented MVP / smoke passed.

Added:

```text
research_tools/short_robust_plateau_engine.py
research/SHORT_ROBUST_PLATEAU_ENGINE.md
research/HYPOTHESIS_REGISTRY.md
research/archive/2026-06-11_short_fade_manual_research/
```

Purpose:

```text
Machine A: build immutable event/outcome store from replay artifacts.
Machine B: scan entry-known rule plateaus with rolling WFA, cost stress,
top-removal, session/symbol breadth and marginal portfolio construction.
```

Smoke command:

```text
python research_tools/short_robust_plateau_engine.py smoke --max-rows-per-source 8000
```

Smoke output:

```text
event_store=.output/research_cache/short_robust_plateau_engine_smoke
candidate_rows=265
promoted=21
report=.output/research_cache/short_robust_plateau_engine_smoke/final_report.md
```

Smoke artifacts:

```text
.output/research_cache/short_robust_plateau_engine_smoke/events.parquet
.output/research_cache/short_robust_plateau_engine_smoke/event_outcomes.parquet
.output/research_cache/short_robust_plateau_engine_smoke/wfa_results.csv
.output/research_cache/short_robust_plateau_engine_smoke/plateau_candidates.csv
.output/research_cache/short_robust_plateau_engine_smoke/plateau_clusters.csv
.output/research_cache/short_robust_plateau_engine_smoke/portfolio_candidates.csv
.output/research_cache/short_robust_plateau_engine_smoke/portfolio_oos_trades.csv
.output/research_cache/short_robust_plateau_engine_smoke/rejected_reasons.csv
.output/research_cache/short_robust_plateau_engine_smoke/lookahead_audit.csv
.output/research_cache/short_robust_plateau_engine_smoke/final_report.md
```

Lookahead audit passed:

```text
feature_available_at_or_before_entry: 0 violations / 14825 rows
evaluation_only_columns_not_in_trigger_rules: 0 violations / 7 rules
large_close15_requires_delay15: 0 violations / 6861 rows
large_close10_requires_delay10: 0 violations / 6861 rows
```

Limit:

The engine is complete for neutral plateau search over existing replay
artifacts. It still needs a new event source for the fresh lower-high /
failed-retest path replay before that hypothesis can be evaluated.

## 2026-06-11 - P551 first-180d feature combo search

Current commit: UNKNOWN. Status: analysis only / IS candidates, OOS untouched.

The next search was constrained to the first 180 days only. The goal was not to
prove an edge, but to build a large feature table and find candidate
regularities without looking at the second 180d half.

Generated:

```text
research_tools/short_is_feature_combo_search.py
.output/results/failed_pump_short_research_365d/short_is_feature_combo_screen.csv
.output/results/failed_pump_short_research_365d/short_is_feature_combo_feature_leaders.csv
.output/results/failed_pump_short_research_365d/short_is_feature_combo_report.md
```

Run:

```text
IS: 2025-06-06 -> 2025-12-03
candidate rows: 74208
passes: 37491
entry/base passes: 8427
post-entry-management passes: 29064
```

Important boundary:

```text
entry_known features may be used for entry selection.
post_entry_management features are only known after entry and may only be used
for hold/exit/derisk decisions.
```

Entry-known themes worth freezing for OOS:

```text
- A_plus_fast / A_fast_deep08 / deep04 remain the useful structure families.
- `confirm_close_low_third`, negative confirm candle, and real quote/trade
  expansion help when combined with structural break quality.
- `risk_3_8pct` appears in good A_plus_fast rows.
- `taker_45_55` looks better than extreme taker in several rows, suggesting
  buyer exhaustion/neutralization rather than pure seller aggression.
- `pump_high_early` and `pump_high_late` both appear, likely representing
  different pump/fade natures rather than one universal timing rule.
```

Strong IS-only entry examples:

```text
deep04 + non_us + recent10 + tp075_full + pump_high_late:
29 trades, avg +0.408R, median +0.699R, WR 82.8%.

A_fast_deep08 + all/not_asia_overlap + recent10 + tp075_full + pump_high_early:
34 trades, avg +0.308R, median +0.698R, WR 73.5%.

A_plus_fast + not_asia_overlap + last_lower_high + tp1_full/tp1_be075 +
confirm_close_low_third:
32 trades, avg +0.343R, median +0.730R, WR 75.0%.

A_plus_fast + not_asia_overlap + existing_stop + tp075_full + risk_3_8pct:
36 trades, avg +0.280R, median +0.712R, WR 75.0%.
```

Post-entry management read:

The strongest systematic separator is quick downside acceptance after entry:

```text
m5/m10/m15 close_r > 0
m5/m10 MFE >= 0.25R or 0.50R
red share >= 60%
MAE controlled in the first 10-15m
```

This supports a trader-readable rule:

```text
Enter only after failed retest/lower high.
Do not take profit before 0.75R.
After 5-10 minutes, keep the short only if price accepts below entry and has
already produced some MFE; otherwise cut or derisk as "static/no continuation".
```

Risk:

These are IS-only discoveries. The second 180d half must now be used only once
with frozen definitions. Do not tune on the OOS half.

Next:

Freeze 10-20 entry-known candidates plus 2-3 post-entry management guards, then
run a single anchored OOS evaluation on the second 180d half with added
slippage/fees and top-trade/top-symbol removal.

## 2026-06-11 - P550 anchored 180d IS / 180d OOS split

Current commit: UNKNOWN. Status: analysis only.

The user correctly flagged that finding a plateau on the full 365d sample and
then running WFA inside the same sample is not true OOS. A stricter anchored
split was added:

```text
IS:  first 180 days only for candidate selection
OOS: second half evaluated once with frozen candidate definitions
```

Generated:

```text
research_tools/short_anchored_is_oos_split.py
.output/results/failed_pump_short_research_365d/short_anchored_is_candidates.csv
.output/results/failed_pump_short_research_365d/short_anchored_is_oos_result.csv
.output/results/failed_pump_short_research_365d/short_anchored_is_oos_report.md
```

Run:

```text
IS:  2025-06-06 -> 2025-12-03
OOS: 2025-12-03 -> 2026-06-02
IS rows: 5295
OOS rows: 6429
IS candidate rows: 3744
selected IS candidates: 40
OOS passes: 20
```

The OOS survivors mostly confirm the P549 plateau, not a completely different
shape:

```text
family_group: mostly A_plus_fast
base_policy: mostly tp075_full
guard: mostly m10_mfe050, with some m10_mfe025 / m15_mfe050
stop_model: last_lower_high, existing_stop, recent5
session: all or not_asia_overlap
```

Best OOS rows:

```text
A_oneprint + not_asia_overlap + existing_stop + tp075_full + m5_mfe025:
IS 26 trades, avg +0.178R, median +0.008R
OOS 32 trades, avg +0.195R, median +0.041R, WR 56.3%
OOS top-trade independence 50.0%, top-symbol independence 52.9%

A_plus_fast + not_asia_overlap + last_lower_high + tp075_full + m10_mfe050:
IS 38 trades, avg +0.183R, median +0.436R
OOS 48 trades, avg +0.149R, median +0.136R, WR 62.5%
OOS top-trade independence 33.3%, top-symbol independence 35.7%

A_plus_fast + all + last_lower_high + tp075_full + m10_mfe050:
IS 41 trades, avg +0.137R, median +0.170R
OOS 52 trades, avg +0.140R, median +0.136R, WR 61.5%
OOS top-trade independence 34.4%, top-symbol independence 34.5%
```

Interpretation:

Anchored OOS reduces the risk of same-window self-deception and still leaves a
real candidate family. The result is not a proof of live edge, but it is much
more credible than the full-period plateau alone. The strongest frozen rule
shape remains:

```text
A_plus_fast
short after failed retest / lower high
SL around structural/retest high
no profit exit before 0.75R
10m no-continuation guard: require MFE >= 0.50R, otherwise exit/derisk
```

Remaining limitation: frequency is still low. OOS survivors have roughly
`32-52` trades over the second 180d half, not `3-15/day`. This is a potential
core sleeve, not the full portfolio.

Next:

Freeze the best 2-3 anchored OOS survivors and run stress tests only on OOS:
extra fees/slippage, random entry delay, non-overlapping monthly blocks, and
top-trade/top-symbol removal on OOS rows only.

## 2026-06-11 - P549 short WFA plateau engine

Current commit: UNKNOWN. Status: analysis only.

Added a bounded WFA/plateau engine around the structural failed-retest short
idea. This is the first step away from single full-period "pretty rows" toward
engineering filtration:

```text
failed retest / lower high -> short
SL above structural/retest high variant
no profit exit before 0.75R
optional no-continuation guard after 3/5/10/15m
train window -> next OOS window -> rolling shift
```

Generated:

```text
research_tools/short_wfa_plateau_engine.py
.output/results/failed_pump_short_research_365d/short_wfa_plateau_candidates.csv
.output/results/failed_pump_short_research_365d/short_wfa_plateau_heatmap.csv
.output/results/failed_pump_short_research_365d/short_wfa_plateau_matrix.csv
.output/results/failed_pump_short_research_365d/short_wfa_plateau_report.md
```

Full-period plateau screen:

```text
base rows: 11724
candidates: 3744
full passes: 331
plateau passes: 73
```

The plateau is not random across all parameters. It clusters around:

```text
family: A_plus_fast = A_post_oneprint_break1p5_retest0p8 + fast_deep_break_taker_above
base policy: tp075_full
guard: 10m MFE >= 0.25R or 0.50R
stop variants: existing_stop, last_lower_high, recent5
session: all or not_asia_overlap
```

Representative full-period plateau rows:

```text
A_plus_fast + not_asia_overlap + last_lower_high + tp075_full + m10_mfe050:
86 trades, 75 symbols, 73 days
avg +0.164R, median +0.164R, WR 60.5%
positive-day rate 58.9%
top-trade independence 38.5%, top-symbol independence 41.3%

A_plus_fast + all + existing_stop + tp075_full + m10_mfe050:
93 trades, 77 symbols, 79 days
avg +0.144R, median +0.084R, WR 58.1%
positive-day rate 55.7%
top-trade independence 35.2%, top-symbol independence 35.6%
```

Walk-forward matrix:

```text
WFA rows: 67
WFA passes: 18
```

The durable WFA rows mostly require longer train windows:

```text
60d train -> 7/14d OOS: only 2 pass rows, weak frequency.
90d train -> 7d OOS: 1 pass row.
120d train -> 14/30d OOS: 15 pass rows, best stability.
```

Best WFA shapes:

```text
A_plus_fast + all + existing_stop + tp075_full + m10_mfe050
120d train -> 30d OOS:
93 selected windows, 83.9% positive OOS windows,
median OOS window +1.77R, avg 8.37 trades / 30d window.

A_plus_fast + not_asia_overlap + last_lower_high + tp075_full + m10_mfe050
120d train -> 30d OOS:
39 selected windows, 89.7% positive OOS windows,
median OOS window +0.57R, avg 7.77 trades / 30d window.

A_plus_fast + not_asia_overlap + existing_stop + tp075_full + m10_mfe025
120d train -> 30d OOS:
35 selected windows, 85.7% positive OOS windows,
median OOS window +1.92R, avg 8.31 trades / 30d window.
```

Interpretation:

This is a real plateau candidate, not a single best point. The robust core is
not "fast_deep only"; it is `A_plus_fast` with a 10-minute no-continuation
guard and profit target not before 0.75R. However, it is still a low-frequency
core: the best 30d OOS windows average roughly `8` trades per 30 calendar days,
not `3-15` trades per day. Current evidence supports core-quality research, not
live high-frequency scale.

Next:

Convert the best WFA shape into a one-trade-per-signal replay with exact
execution order, then run a non-overlapping OOS/Monte Carlo stress layer:
random entry delay, extra slippage, fee expansion, and top-trade/symbol removal
on OOS-only trades.

## 2026-06-11 - P548 structural 0.75R path replay and 1m win/loss anatomy

Current commit: UNKNOWN. Status: analysis only.

The structural short replay was extended with a true 1m path postprocess that
does not take profit before `0.75R`. Early exits are allowed only as defensive
no-continuation exits, not as early profit taking.

Generated:

```text
research_tools/short_structural_075_path_replay.py
.output/results/failed_pump_short_research_365d/short_structural_075_path_replay_trades.csv
.output/results/failed_pump_short_research_365d/short_structural_075_path_replay_summary.csv
.output/results/failed_pump_short_research_365d/short_structural_075_path_micro_win_loss.csv
.output/results/failed_pump_short_research_365d/short_structural_075_path_replay.md
```

Scope:

```text
families replayed:
- A_post_oneprint_break1p5_retest0p8
- fast_deep_break_taker_above
- deep_break_retest0p8
- deep_break_retest0p4

base unique signal/family/stop rows: 3908
replayed rows: 31264
closed rows: 31264
```

Broad result:

```text
All four families together are not a robust portfolio with 0.75R+ exits.
tp075_full: 3908 rows, avg -0.013R, median +0.071R, WR 53.2%.
tp1_full: 3908 rows, avg -0.008R, median -0.004R, WR 49.7%.
Broad deep_break_retest0p4 is a frequency source but negative in aggregate.
```

The useful result is concentrated in `fast_deep_break_taker_above` and, more
modestly, structural `A`.

Fast-deep:

```text
family aggregate, all stop models:
tp075_full: 124 rows, avg +0.216R, median +0.488R, WR 69.4%
tp1_be075: 124 rows, avg +0.282R, median +0.249R, WR 65.3%
tp1_full: 124 rows, avg +0.251R, median +0.249R, WR 66.1%

best row:
fast_deep + recent5 + tp1_be075
31 trades, 31 symbols, 28 days
avg +0.302R, median +0.439R, WR 67.7%
top-trade independence 47.6%, top-symbol independence 47.6%
```

Structural A:

```text
family aggregate, all stop models:
tp075_full: 252 rows, avg +0.075R, median +0.223R, WR 58.3%
tp1_full: 252 rows, avg +0.107R, median +0.131R, WR 54.8%
tp075_full_time15_no_mfe05: 252 rows, avg +0.098R, median +0.024R, WR 52.0%

best defensive row:
A + existing_stop + tp075_full_time10_no_mfe025
64 trades, avg +0.126R, median +0.043R, WR 53.1%
top-trade independence 35.3%, top-symbol independence 41.4%
```

1m win/loss anatomy:

```text
Winners are not defined by OI or a subtle taker ratio here. They are defined by
immediate post-entry price acceptance:

- more red 1m closes in the first 3-5 minutes;
- meaningful MFE by minute 3-5;
- close remains below entry by minute 5;
- losers often print early adverse excursion toward the retest high/stop.
```

Simple management diagnostics on `tp075_full`:

```text
fast_deep:
all rows: 124, avg +0.216R, median +0.488R, WR 69.4%
m5_mfe>=0.50R: 42 rows, avg +0.675R, median +0.725R, WR 100%
m5_mfe<0.25R: 66 rows, avg -0.094R, median -0.0067R

A:
all rows: 252, avg +0.075R, median +0.223R, WR 58.3%
m5_mfe>=0.50R: 89 rows, avg +0.590R, median +0.724R, WR 95.5%
m5_mfe<0.25R: 114 rows, avg -0.255R, median -0.273R

deep_break_retest0p8:
all rows: 1032, avg -0.0049R, median +0.096R
m5_mfe>=0.50R: 226 rows, avg +0.504R, median +0.718R, WR 89.8%
m5_mfe<0.25R: 598 rows, avg -0.210R, median -0.159R
```

Interpretation:

Holding for `0.75R` is plausible only when the first 3-5 minutes after entry
confirm downside acceptance. The strategy should not blindly hold every failed
retest to `0.75R`. A professional rule would be:

```text
enter on failed retest / lower high;
SL above retest high;
do not take profit before 0.75R;
but if after 5m MFE is <0.25R or the 5m close is not below entry, treat it as a
static/failed-continuation short and cut/derisk.
```

This is still research-only until converted into a single live-like replay rule
with one trade per signal and rolling/OOS selection.

## 2026-06-11 - P547 short portfolio frequency search

Current commit: UNKNOWN. Status: analysis only.

The short-side candidates from P545/P546 were combined into deduplicated
portfolios to test the user's desired frequency target: roughly `3-15` trades
per active day, preferably with more if the edge survives. This used existing
365d artifacts only; no new multi-hour 365d rerun was launched.

Generated:

```text
research_tools/short_portfolio_combination_search.py
.output/results/large_runner_discovery_365d/short_portfolio_candidate_library.csv
.output/results/large_runner_discovery_365d/short_portfolio_combination_search.csv
.output/results/large_runner_discovery_365d/short_portfolio_top_trades.csv
.output/results/large_runner_discovery_365d/short_portfolio_combination_search.md
```

Screen output:

```text
candidate rows: 3397
usable candidate rows: 618
portfolios evaluated: 794
frequency target passes: 0
strict portfolio passes: 0
```

Main finding: with current candidate generation, simply combining the best
known short pockets does not produce a robust `3-15/day` portfolio. The
high-quality structural candidates remain around `1.1-1.2` trades per active
day. The higher-frequency positive portfolios reach only about `1.8-2.0` per
active day, and quality/top-independence weakens sharply.

Best quality portfolios:

```text
pair/triple structural A + fast_deep variants:
56 trades, 54 symbols, 50 active days, 6 sessions
1.12 trades/active day
avg +0.103R, median +0.450R, WR 62.5%
top-trade independence 37.1%, top-symbol independence 38.2%
max drawdown -1.82R

larger structural pair:
93 trades, 77 symbols, 79 active days, 6 sessions
1.18 trades/active day
avg +0.096R, median +0.451R, WR 61.3%
top-trade independence 33.3%, top-symbol independence 34.1%
max drawdown -2.49R
```

Best frequency with positive average and median:

```text
large_runner_local_high_top_3:
168 trades, 81 symbols, 86 active days
1.95 trades/active day
avg +0.170R, median +0.040R, WR 53.6%
top-trade independence 11.1%, top-symbol independence 14.3%
max drawdown -9.35R

deep_break_retest0p4 family:
419 trades, 219 symbols, 230 active days
1.82 trades/active day
avg +0.005R, median +0.205R, WR 63.0%
top-trade independence about 1.9%, top-symbol independence about 1.6%
max drawdown -3.71R
```

Interpretation: the 3-15/day objective is not reachable by portfolio blending
alone without admitting diluted or top-dependent trades. To raise frequency
honestly, the next work must expand signal generation and management, especially
a real 1m path replay for structural `A` / `fast_deep` no-continuation exits,
rather than lowering quality thresholds.

Action: keep three research tiers only:

```text
Core: structural A / fast_deep, low frequency but best balance.
Tactical: deep_break_retest0p8, moderate frequency, weaker independence.
Scout: deep_break_retest0p4/local-high fader, frequency source only after
       strict time/no-continuation replay proves it can stop bleeding.
```

Do not promote the scout tier live from current evidence.

## 2026-06-11 - P542 fader/runner/static session readout

Current commit: UNKNOWN. Status: analysis only.

The fader/runner/static outcome study was extended with the project session
model (`asia_only`, `asia_europe_overlap`, `europe_only`,
`europe_us_overlap`, `us_only`, `off_session`). Sessions materially change the
base mix:

```text
europe_only: fast fader 24.6%, runner 27.4%, upper static 33.5%
off_session: fast fader 23.4%, runner 32.8%, upper static 29.3%
europe_us_overlap: fast fader 22.8%, runner 30.7%, upper static 32.0%
asia_only: fast fader 21.8%, runner 28.6%, upper static 35.0%
asia_europe_overlap: fast fader 20.4%, runner 32.3%, upper static 32.5%
us_only: fast fader 20.2%, runner 38.3%, upper static 28.2%
```

Weak post-seed acceptance remains the strongest simple discriminator across
sessions. With `close15 <= ~3.4%`, fast-fader rates were:

```text
off_session 68.2%
europe_only 66.5%
us_only 63.6%
asia_only 63.0%
europe_us_overlap 62.5%
asia_europe_overlap 59.6%
```

By contrast, `close15 >12%` is mostly runner in every active session, especially
`us_only` (`88.5%` runner). This supports a session-aware interpretation:
session shifts the prior, but weak/strong 15m acceptance dominates the local
outcome split.

Failed-pump structural management also showed session effects. The best
session-specific rows were mostly `asia_only` deep-break/retest variants, but
they still did not pass the `50%+` winner top-removal target. Best example:

```text
asia_only + deep_break_retest0p8 + last_lower_high + full0.75R
53 trades, avg +0.119R, median +0.019R, WR 52.8%,
top-removal-to-negative 32.1% of winners
```

Action: future selectors must include session as a first-class axis. Do not use
a global short rule without checking session-specific base rates, but also do
not promote session alone; it is a prior/context layer.

## 2026-06-11 - P541 structural management deep scan

Current commit: UNKNOWN. Status: analysis only.

The failed-pump structural short branch was replayed from `failed_pump_short`
signals on 1m cache without a new 365d discovery run. This tested whether
management, not just filters, can improve robustness: existing stop, last
lower-high stop, recent5/recent10 local stop, full TP at `0.25/0.5/0.75/1R`,
half partial at the same R targets, and structural pivot-high trailing.

No category passed the `50%+` winner top-removal target. Broad structural
families remain negative-median or too dependent on a few winners. The best
morphology clues:

```text
fast_deep_break_taker_above + recent10 stop + full0.5R:
31 trades, 31 symbols, 28 days
avg +0.117R, median +0.102R, WR 61.3%, positive-day share 57.1%
top-removal-to-negative 42.1% of winners

A_post_oneprint_break1p5_retest0p8 + existing stop + full0.75R:
64 trades, 53 symbols, 59 days
avg +0.119R, median +0.032R, WR 56.3%, positive-day share 55.9%
top-removal-to-negative 30.6% of winners

A_post_oneprint_break1p5_retest0p8 + last_lower_high stop + full0.5R:
64 trades, 53 symbols, 59 days
avg +0.082R, median +0.451R, WR 60.9%, positive-day share 59.3%
top-removal-to-negative 28.1% of winners
```

Interpretation: the closest thing to the desired shape is not the broad
failed-pump short; it is a very specific structural morphology: fast deep break
or one-print deep break plus failed retest, then quick fixed profit. But sample
is either small or top-removal independence is still below target. This remains
research-only.

Action: stop broad threshold expansion. Future work should either collect more
instances of this morphology or build a fixed-family rolling selector over
these exact families. Required promotion bar remains `50%+` winner
top-removal-to-negative, positive median, enough active days/symbols, and no
OI-tail-only dependence.

## 2026-06-11 - P540 fade timing vs anomaly duration

Current commit: UNKNOWN. Status: analysis only.

Fade timing was analyzed without a new 365d discovery run. The readout uses
existing `future60_low_break_offset_min` labels and the local-high short replay
to compare fade timing against the long-anomaly confirmation duration
(`10m/15m` entry delays).

Setup-level timing:

```text
fader rows with low break before high10: 3151
low break <=10m after seed close: 1086 rows, 34.5% of faders
low break <=15m after seed close: 1481 rows, 47.0% of faders
low break <=20m after seed close: 1785 rows, 56.6% of faders
median fader low-break offset: 17m
```

This explains a major trap: if the short waits for a 15m failure, many fades
have already broken down before entry. The short is then late; the remaining
path often has poor continuation.

Short replay by evaluation-only timing confirms the shape:

```text
fade 0-5m after short entry:
avg +0.835R, median +0.347R, WR 73.7%, no-down-continuation 9.2%

fade 5-10m after short entry:
avg +1.237R, median +0.699R, WR 64.0%, no-down-continuation 19.6%

fade 10-20m after short entry:
avg +0.228R, median -0.533R, WR 37.4%, no-down-continuation 36.8%

already faded before entry:
avg -0.218R, median -0.520R, WR 27.7%, no-down-continuation 42.2%

no low break:
avg -0.595R, median -1.044R, WR 17.5%, no-down-continuation 46.3%
```

This timing label is future-only and cannot be used directly. Entry-known
proxies for quick post-entry fade were weak: the best simple filters only raised
quick-fade probability to roughly `20-24%`, not enough to create a standalone
edge.

Action: avoid the “long failed, short immediately” trap unless there is fresh
post-entry structural breakdown still ahead. A viable short needs an entry that
is not after the main fade has already happened: either earlier breakdown
confirmation with tight local high, or a failed retest that proves continuation
risk remains. Add a no-continuation guard to future short replay metrics.

## 2026-06-11 - P539 long WR 20-30 inversion readout

Current commit: UNKNOWN. Status: analysis only.

The 365d large-runner long trade grid was analyzed for cohorts where long
trades had `20-30%` winrate. No new 365d discovery run was launched. The scan
used existing dimensions: arm, exit policy, decision offset, policy id,
setup-selection, nature status, and price/flow buckets. It then intersected
those low-WR long cohorts with the existing local-high/partial/trailing short
replay.

Result: low long WR is a strong fader/veto signal, but not yet a robust short
edge. The scan found `178` long low-WR cohorts and `5576` short intersections.
None passed the required short shape: positive average, positive median,
positive active-day share, enough trades/symbols/days, and `50%+` winner
top-removal independence.

Examples of low-WR long anatomy:

```text
E10_confirmed_runner: 13,915 long rows, WR 21.4%, avg net -0.18%,
runner10 32.8%, fader 66.9%.

early_trade_ratio >20: 34,140 long rows, WR 21.4%, avg net -0.26%,
runner10 22.7%, fader 77.2%.

last2_trade 45-55: 14,495 long rows, WR 21.4%, avg net -0.34%,
runner10 24.7%, fader 75.2%.
```

Best short intersections still fail robustness:

```text
trade_ratio>20 + trade_top1<=35:
S3_last2_close15_le2 + recent10 + trail_only
62 short trades, avg +0.324R, median -0.132R,
top-removal-to-negative 14.8% of winners.

quote_ratio>20 + taker 50-55:
S2_last2_close10_le2 + recent10 + half_base_low
69 short trades, avg +0.054R, median +0.014R,
top-removal-to-negative 5.4% of winners.
```

Action: treat long WR 20-30 cohorts as short context/veto and as a source of
anatomy, not as direct short rules. A short rule must still pass the same
execution and top-removal independence standard.

## 2026-06-11 - P538 local-high stop and structural half-exit replay

Current commit: UNKNOWN. Status: analysis only.

The large-runner short-failure replay was extended without rerunning the 365d
discovery. Existing setup artifacts and 1m cache were reused to test a more
trader-like short model:

```text
entry: after 10m/15m failure close, next 1m open
initial SL: recent/local high (`recent3`, `recent5`, `recent10`,
            `last_pivot_or_recent5`), not full pump high
partial: close 50% at pump middle, pump open/base, or pump low/base
remaining position: structural trailing stop using confirmed local pivot highs
costs: fees plus adverse entry/exit slippage
```

Result: local-high stops improve the prior naive failure replay, but strict
robust edge is still not proven. The best broad policy was
`S4_big_early_close15_le4 + recent10 SL + half_base_open`: `788` closed trades,
`+0.002R` average, `-0.165R` median, `38.3%` winrate, and negative
drop-top-3-day/symbol results. This is not tradable.

Top-dependence should be measured by top-removal-to-negative, not fixed
drop-top-3. Sort trades by net R descending and remove the best trades until
remaining total R is `<=0`; use the removed share of winning trades as the main
normalized metric, with `50%+` as the target. By this stricter metric the
filtered pockets are still weak: the best candidates go negative after removing
only `4-5` best trades, or `8-11%` of winners.

The filtered search found small positive-median pockets, but none passed the
strict robust/diversified threshold with `100+` trades, positive median,
positive active-day share, and `50%+` winner top-removal independence. Best
current pockets:

```text
S2 close10<=2 + recent10 SL + half_base_low
risk 3-8%, m1_trade_top1_share>=45%
86 trades, 72 days, 73 symbols
avg +0.051R, median +0.043R, WR 55.8%, positive-day share 56.9%
drop-top-3-days +0.61R, drop-top-3-symbols +1.08R
top-removal-to-negative: 4 trades, 4.7% of all trades, 8.3% of winners

S3 last2 close15<=2 + recent10 SL + half_base_low
risk 2-6%, early_return>=6%
90 trades, 72 days, 78 symbols
avg +0.119R, median +0.003R, WR 51.1%, positive-day share 51.4%
drop-top-3-days +3.03R, drop-top-3-symbols +2.80R
top-removal-to-negative: 5 trades, 5.6% of all trades, 10.9% of winners
```

Rolling check on the S3 pocket shows diversification but not full stability:
one chronological fold was slightly negative (`-0.143R` over `22` trades) and
the worst 30d window was `-2.25R`. Treat these as candidate categories for
stress testing, not as live rules.

Action: do not call these an edge. Continue from the positive-median pockets
only as morphology clues, and require `50%+` winner top-removal independence in
the next fixed-family selector. The next useful test is replaying the structural
failed-retest Candidate A with the same local-high/partial/trailing model.

## 2026-06-11 - P537 executable short-failure replay

Current commit: UNKNOWN. Status: analysis only.

The large-runner short-fade pattern was replayed as an executable proxy trade,
not just as a future label. Entry is only after the failure window is closed:
next 1m open after 10m/15m confirmation, adverse entry/exit slippage, fees, a
short stop at the known pre-entry high, and stop-first handling when TP/SL touch
inside the same 1m candle.

Result: the visible failure predicts downward path very well, but the naive
short implementation is not profitable in R. Best checked variant was
`S3_last2_close15_le2 + tp1_full` with `405` closed trades, `-0.012R` average,
`+0.024R` median, `51.4%` winrate, and `-10.88R` after dropping the top three
days. Core `S1_close15_fail` was worse: `218` closed trades, `-0.122R` average
with TP0.75 and `-0.151R` with TP1. Many S1 rows were skipped because the known
stop distance exceeded the 10% max-risk guard (`335/553` rows).

Trader interpretation: this is not "wait 15 minutes and short every pump".
The market first shows a real pump attempt, then fails acceptance above the seed
close. That failure is informative, but by the time it is confirmed the stop is
often too far and the remaining move is not enough after fees/slippage. The
edge, if it exists, must come from a tighter structural entry after failure
(for example low break + failed retest), not from the generic 15m failure close.

Action: do not promote `close15<=0` as a short rule. Use it as a context/veto
or as a parent regime. The next short test should replay the narrower structural
Candidate A style entry: concentrated pump, decisive low break, failed retest
far below the prior high, with risk measured from actual fill.

## 2026-06-11 - P536 short-fade rolling robustness

Current commit: UNKNOWN. Status: analysis only.

The large-runner short-fade candidates were checked for rolling outcome
robustness before executable short replay. This is not PnL evidence; it tests
whether fixed known-after-failure categories consistently lead to `short_fade4`
outcomes.

Core result: `close15<=0` is robust across 30/60/90d train -> next-day OOS:

```text
S1 close15<=0:
553 labeled rows, 240 active days, 236 symbols
short_fade4 97.47%, runner10 2.53%
drop-top-3-days short_fade4 97.42%
drop-top-3-symbols short_fade4 97.49%
30d OOS: 505 rows, short_fade4 97.82%, runner10 2.18%
60d OOS: 527 rows, short_fade4 97.72%, runner10 2.28%
90d OOS: 511 rows, short_fade4 97.65%, runner10 2.35%
```

Flow-gated S1 variants are purer but mostly overlap the same regime. A broader
greedy basket covers `1695` rows across `334` days and `369` symbols with
`92.63%` short_fade4, but purity drops after leaving the S1 close15-failure
core.

Conclusion: the short-fade outcome is rolling-stable enough to justify an
executable short replay. Do not claim profitability yet.

## 2026-06-11 - P535 large-runner short-fade failure study

Current commit: UNKNOWN. Status: analysis only.

The 365d large-runner long attempt at
`.output/results/large_runner_discovery_365d` was analyzed as negative evidence
for long continuation and as a source of short-fade patterns. The long failure
is mostly nature classification: portfolio fader-label trades contributed
`-55.48` net return (`5,880` trades, avg `-0.9435%`), while runner10-label
trades contributed `+43.88` (`1,932` trades, avg `+2.2712%`).

The setup-level short-fade study used only rows with `future_label_status=ok`
(`11,367` rows out of `573,720` cluster-selected setups). Base rates in this
labeled slice were `31.71%` runner10, `68.29%` fader, `56.96%` short_fade2 and
`42.15%` short_fade4. OI is again late-tail only: `2,059/11,367` labeled rows
had `oi_status=ok`, all in `2026-04..2026-06`.

Strongest visible short-fade condition:

```text
close_ret_15m <= 0

553 rows, 240 active days, 236 symbols
runner10 2.53%, fader 97.47%, short_fade4 97.47%
median future60 min path -12.35%
```

This is not a seed-time signal. It is a visible failure condition known only
after the 15m close. Candidate short replays should enter at the next 1m open
after the failure candle, with structural-high/seed-high stop and actual-fill
PnL. The promising families are `close15<=0` plus real flow/chase evidence:
`m1_sustain_strict`, `trade_ratio>=10`, `quote_ratio>=5`, `taker45_55`, or
concentrated/last2 chase variants. Do not train on OI-tail.

## 2026-06-11 - P534 structural short candidate deep dive

Current commit: UNKNOWN. Status: analysis only.

Deeper forensic search kept the failed-pump short thesis alive, but only as a
narrow structural pattern. The broad rule "short pumps after failure" remains
negative; the useful hypothesis is closer to:

```text
one-print-like pump -> deep structural low break -> failed retest far below
prior structural high -> fixed partial-take short exit
```

Best current fixed-exit candidate:

```text
distribution=one_print_like_top1_ge60
structural_break_depth_pct >= 1.5%
failed_retest_distance_pct >= 0.8%
exit: short_tp0p75r_close50_trail

64 trades, 59 active days, 53 symbols
avg +0.146R, median +0.0097R, winrate 51.6%
positive active-day share 54.2%
drop-top-1-day +7.84R, drop-top-3-days +5.19R
OI-tail: 15 trades, avg +0.174R, median +0.0039R
```

The TP1 version is similar (`64` trades, avg `+0.156R`, median `+0.0016R`,
drop-top-3-days `+5.43R`), but OI-tail median is slightly negative. Two smaller
pockets also appeared: off-session deep break with neutral taker share
(`37` trades, median `+0.0229R`) and fast deep break with taker above norm
(`31` trades, median `+0.0355R`), but both need more sample and the fast-break
candidate weakens in the OI-covered tail.

Do not promote this to live. Next validation should deduplicate alternate exits
at signal level and run the exact anatomy rules through a rolling selector with
explicit low-sample handling. The rule must not be trained on the OI tail.

## 2026-06-11 - P533 failed-pump short rolling readout

Current commit: UNKNOWN. Status: analysis only.

The existing 365d artifacts were analyzed without a new rerun. Full trade-grid
performance is strongly negative (`1,715,460` rows, avg `-0.207R`, median
`-0.276R`). The more realistic `structural_low_break` subset is less bad but
still negative (`277,458` rows, avg `-0.094R`, median `-0.148R`, winrate
`27.3%`). The audit-only no-low-break fade rows are worse (`-0.229R` avg).

The strict daily walk-forward selector found only one selectable rule across
`351` OOS days, and that rule produced only `2` trades on `2026-04-11` for
`-0.425R`. Rolling health therefore did not identify a stable tradable failed
pump short rule. OI remains a late-tail validation layer only: non-missing OI is
available from `2026-04-07` to `2026-06-01` (`56` dates), and all OI buckets are
negative in the full grid.

Action: do not promote failed-pump short rules. At most, run a narrow forensic
readout on OI-blind structural candidates, requiring positive median, positive
active-day share, and drop-top-day robustness. If that fails, keep failed-pump
short evidence only as a diagnostic/avoidance layer for late pump entries.

## 2026-06-11 - P532 rolling-pattern plan with late OI coverage

Current commit: UNKNOWN. Status: analysis only.

The 365d failed-pump-short rolling artifacts under
`.output/results/failed_pump_short_research_365d` should not be read as an OI
validated yearly edge. The run config uses `closed_5m_oi_asof_confirm_close`,
but trade-grid OI buckets show most rows as `missing`; non-missing OI appears
only near the end of the cache. The strict OOS selector produced only `2` trades
on `2026-04-11` (`-0.425R` total), so profitability is not established.

Next research should treat OI as a late holdout/confirmation layer, not as a
year-long training feature. First learn rolling regularities from price,
quote/trade flow, taker share, distribution, session, timing and post-pump
structure across the full period; then test whether the same families improve
or fail in the OI-covered tail. Do not select rules on the OI tail and then
claim 365d robustness.

## 2026-06-08 - P531 level-attack recall layer

Current commit: UNKNOWN. Status: PROPOSED.

P530 should not be used as final 365d evidence yet because it only wrote
level-attack candidates from arm matches. P531 moves the level-attack readout
upstream to cluster-selected 5m setups, so the next run can answer the recall
question: how many future top-growth pumps had an approach-to-level + stronger
flow setup before the old strategy matched anything?

The patch intentionally remains fast: H1 levels are still computed from closed
hourly candles and cached by `(symbol, seed_hour)`. No full-market per-minute
level scanner is added.

## 2026-06-08 - P530 level-attack research path proposed

Current commit: UNKNOWN. Status: PROPOSED.

The next discovery run should test a distinct pump nature: old tested H1 levels as liquidity shelves. The hypothesis is that price approaches a level after a prior rejection/pullback, has already recovered roughly 70%+ of that pullback, and current quote/trade flow is stronger than previous attempts before or during the first crossing. This is not a resistance veto; it is a possible pre-breakout fuel signal.

P530 adds fast closed-H1 level-attack observability to `run-large-runner-discovery` while keeping the existing entry/exit contract unchanged. It also keeps P529 post-entry/session/stability fields so rolling research can compare advance entries versus crossing entries and filter by session/post-entry validation.

After applying, run a short smoke first, then evaluate:

```text
1. advance_before_level vs seed_high_crossing vs seed_close_crossing entries;
2. progress_to_level_from_pullback >= 0.70;
3. current quote/trades vs prior level attack spikes;
4. H1 cascade counts within 1R/2R/3R;
5. session effects and post-entry 1m/3m validation.
```

## 2026-06-07 - P528 category robustness readout

Current commit: 4416b2a9. Status: analysis only.

P528 deepened the 45d P526 runner/fader readout with trade-level robustness
metrics: representative trade count, winrate, positive PnL days, top-symbol
dependency, and drop-top-3 performance. The unit for seed execution is a
deduplicated E5 representative row per `(symbol, entry_timestamp_ms)`, preferring
strict ignition over mass ignition when both exist.

Best early seed expansion found so far:

```text
qtop<=0.45, oiE>=0.5%, oiP>=0, preRet<=6%, taker<=0.62,
active_1m_count>=4, current_quote_vs_prior_24h<=1.2
```

This is a wider version of the prior distributed/OI-supported seed thesis. It
produced `51` E5 representative trades across `42` symbols, `31.37%` winrate,
`+2.82%` average net, `14/27` positive trade days, `31.37%` runner10 labels and
`15.69%` runner20 labels. Top dependency is still present but materially better
than the narrower seed families: top-1 symbol contributed `27.79%` of total net,
top-3 contributed `78.46%`, and after dropping the top three symbols the sample
remained positive at `+2.56%` average net over `43` trades.

Prior seed families remain useful as nature evidence, but not enough as live
rules alone:

```text
Seed A setup labels: 61 setups, 57.38% runner10, 29.51% runner20.
Seed A E5 trades: 19 trades, 31.58% winrate, +3.13% avg net, 5/12 positive days.
Seed B setup labels: 74 setups, 55.41% runner10, 25.68% runner20.
Seed B E5 trades: 26 trades, 15.38% winrate, +0.67% avg net, 3/17 positive days.
```

The 15m confirmation family remains the strongest quality label but is not a
free entry rule. At E5 proxy timing it had `127` trades, `37.80%` winrate,
`+1.64%` average net, `22/39` positive days, and low top dependency
(`19.20%` top-1, `44.34%` top-3). But realistic delayed entries weaken sharply:
E10 had `90` trades, `22.22%` winrate, `+0.37%` average net, `11/39` positive
days and heavy top dependency; E15 had `20` trades and slightly negative average
net. Therefore 15m continuation is useful for quality/management, but pure
waiting-to-15m likely loses too much RR.

Veto evidence strengthened:

```text
Short-cover rebound: 416 setups, only 19.47% runner10 and 2/45 positive
runner-label days. E5 trade PnL is slightly positive only through tail/top
dependence and should not be treated as robust runner edge.
Dump + OI-down + concentrated flow: 19 setups, 5.26% runner10, 42.11% low-break,
0/15 positive runner-label days, and negative E5/E10 trade replay.
```

Interpretation: the best current direction is not "more thresholds", but a
research-only early category for distributed OI-supported active continuation,
plus explicit vetoes for short-cover/rebound and OI-down concentrated dump
patterns. Before live promotion, rerun or extend the backtest with this category
as a named research family and audit actual execution freshness/RR, top-growth
misses, and out-of-sample days.

## 2026-06-07 - P526 anomaly-centric large-runner labels

Current commit: e0ba949a. Status: APPLIED and pushed.

`run-large-runner-discovery` no longer treats hourly top-growth as the primary
runner/fader truth. The research label is now attached to the exact sliding
anomaly seed: from the seed close, a runner must reach +10%/+20%/+30% within the
next 60m before any later 1m candle breaks the anomaly low. If the 60m path is
complete and +10% does not happen before that low break, it is a fader. Same
1m-candle target/low-break ambiguity is counted conservatively as low-break
first.

This fixes the user's concern about hour-bucket hindsight. The future label is
still written only as an evaluation field (`future_label_available_at_entry =
False`) after the known-at-decision setup is built. Hourly top-growth files
remain audit-only for missed-runner orientation.

Validation smoke on `.output/results/large_runner_discovery_1d`: `raw=3972`,
`setups=2412`, `matches=79`, `trades=310`, `avg_net=-1.0806%`,
`win_rate=10.00%`. Among enriched setup rows with available future labels,
roughly 17% were `runner_high10_next60` under the new target-before-low-break
contract.

The 45d P526 run at `.output/results/large_runner_discovery_45d_p526` completed
in 3772.953s over 594 symbols (`2026-04-17T15:50:00Z` to
`2026-06-01T15:50:00Z`): `raw=126987`, `setups=77079`, `matches=2812`,
`trade_grid_rows=14060`, `closed=9945`, `skipped=4115`, `avg_net=+0.0112%`,
`win_rate=22.41%`. The label split on complete enriched setups is useful:
`1497` complete labels, `441` +10 runners (29.46%) and `1056` faders. Runner
hit timing after seed close: p25 5m, median 11m, p75 24m, p90 42m.

Interpretation: the new dataset is structurally valid for runner/fader feature
research, but not yet a finished live edge. Closed grid rows labeled
`runner_high10_next60` averaged about `+2.70%` net, while faders averaged about
`-0.91%`, so the label captures the intended outcome. However, the all-portfolio
sample is slightly negative (`1101` rows, avg `-0.00365%`, sum `-0.04018`) and
top dependency is still high. Early evidence points toward distributed 1m flow
(low top-1 concentration), positive early OI change, and selected nature groups
such as `A_cool_stress_absorption`, `B_liquid_distributed_moderate_taker`,
`D_24h_flow_record_absorption`, and `v4_quality_cool` as candidates for deeper
validation. Do not promote this to live rules without a stricter category/top
dependency and missed-runner audit.

## 2026-06-07 - P527 runner/fader early-feature audit

Current commit: dbacbc19. Status: analysis only.

P527 compared the 441 anomaly-centric +10 runners against 1056 faders from
`.output/results/large_runner_discovery_45d_p526` using only live-available
setup features. The strongest known-at-seed truths are:

- One-print/concentrated flow is bad. `m1_quote_top1_share <= 0.34` improved
  runner10 rate from 29.46% to 38.51%; `m1_quote_top1_share > 0.55` was a weak
  veto bucket around 21%.
- OI matters. Seed OI up is better than OI down; `oi_change_early_pct >= 0.5%`
  gave 40.18% runner10, and `>= 1%` gave 40.97%. Price rising while OI falls is
  short-cover/rebound risk, not organic pump awakening: `early_return > 4%` with
  OI down was only 19.47% runner10 in setup labels and negative in trade-grid
  replay.
- Do not ban every pre-pump drawdown. Plain pre60 drawdown was not the enemy:
  `pre60_min_path < -4%` had 33.95% runner10. The bad case is drawdown plus OI
  down plus concentrated flow; `pre60_min_path < -4%`, OI down and
  `m1_quote_top1_share > 0.5` was only 5.26% runner10 and strongly negative in
  trade replay. Drawdown plus OI up and distributed flow was much better:
  44.68% runner10.
- Simple quote/trade ratios are not monotonic edge. Bigger volume/trade ratio
  alone does not mean a better runner. The useful distinction is whether the
  activity is distributed and OI-supported.
- Waiting for 15m confirmation is much stronger but later: `close_ret_15m` had
  the best univariate AUC (0.843), and `close_ret_15m >= 6%` plus distributed
  flow and OI up produced about 56.4% runner10 over 289 setups. This is not
  necessarily the best entry, because 59.64% of true runners already hit +10%
  within 15m of seed close.

Promising seed-time candidate families to validate next, not yet live rules:

1. Distributed OI-supported seed: `m1_quote_top1_share <= 0.34`,
   `oi_change_early_pct >= 0.5%`, `oi_change_pre60_pct >= 0.5%`,
   `pre60_return_pct <= 6%`, and `current_vs_prior_max_trade_24h <= 1`. It had
   61 setups, 57.38% runner10, 29.51% runner20, and remained above 53% after
   dropping the top three symbols.
2. Broad seed core: `m1_quote_top1_share <= 0.34`,
   `oi_change_early_pct >= 1%`, `m1_elevated_both_count_3x >= 5`, and
   `current_vs_prior_max_quote_24h <= 1`. It had 74 setups, 55.41% runner10,
   25.68% runner20, 49 symbols, and top-symbol share under 7%.
3. Later confirmation family: `close_ret_15m >= 6%`,
   `m1_quote_top1_share <= 0.40`, and OI up. It had 289 setups, 56.40%
   runner10 and stayed 56.18% after dropping top three symbols, but it is a
   later entry and must be tested against missed fast runners and RR collapse.

Next: add these as research-only candidate families in the large-runner module
or a sibling pure rule module, then rerun/report 45d with separate seed-entry
and 10m/15m-confirm entry buckets. Do not merge them into live until the
portfolio model, top dependency, and entry freshness/RR are audited.

## 2026-06-04 - P504 proposed unified targeted LTF accelerator

Current commit: UNKNOWN. Status: PROPOSED.

Next acceleration should be a single module around targeted aggTrades data loading, not more strategy filters in runner discovery. The module should accept requested `(symbol, start_ms, end_ms, target_timeframes, phase)` windows and return the same fetch/materialize artifacts plus trusted target-LTF cache coverage. It must not call `PumpDecisionCore`, choose categories, inspect future labels, inspect exits, use PnL, or decide trade validity.

The honest 10x path is:

1. merge all profile/phase windows by symbol before network;
2. fetch each missing aggTrade interval once from bulk/prebuilt source or REST fallback;
3. materialize all needed target timeframes from the same trades in one pass (`15s` and `30s` together);
4. write the same direct target-LTF cache/version/coverage sidecars;
5. expose artifacts that prove whether data came from trusted cache, bulk source, REST fallback, or unavailable source.

This preserves live/backtest parity because the decision core still reads the same normalized closed LTF candles from cache and emits the same snapshot/verdict contract. The accelerator changes only transport and cache materialization.

Risk checklist before implementation: bulk/raw aggTrades ordering and duplicate ids must be normalized exactly once; interval boundaries must be inclusive/exclusive-compatible with current REST path; empty intervals require source-confirmed coverage; target candle aggregation must keep the same timestamp/open/high/low/close/quote/trade/taker semantics as live/backtest adapters; raw or target cache version must change if aggregation semantics change; delisted/new symbols must remain explicit unavailable data, not silent zeros; parity must be proven by old-vs-new `snapshot_hash`/`signal_verdict` equality on overlapping non-missing 2d windows.

## 2026-06-04 - P503 2d runner discovery audit and partial planner pruning

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

The 2d run at `.output/results/htf_ltf_runner_discovery_2d` is structurally honest enough to analyze bottlenecks: all four TF profile folders and combined artifacts exist; honesty reports mark future-label separation, entry availability, LTF continuity, OI availability, costs and staged data access as ok/research-only as appropriate. The result is still not a profitability sample and should not be read for edge.

Runtime was `7.21h` for 2d, implying about `108h` for 30d if linear. P502 fixed the old Binance 418/429 failure mode: only `5m_15s` had 7 fetch errors, and they were DNS `getaddrinfo` errors, not throttling. The remaining bottleneck is data volume: `5m_15s` fetched about `19.3M` aggTrade rows, with `18.4M` in pre-entry seed fetch alone. `5m_15s` pre-entry consumed about `736h` of fetched LTF windows.

P503 adds safe planner pruning but does not claim 10x. Signal-entry planning now rejects terminal seed-stage failures with fast exact-seed checks: return, internal LTF sustained-flow shape, and closed-context quote/trade ratios. Pre-entry planning can narrow full 2xHTF pair fetches into exact seed windows when 1m bounds are complete; it falls back to the old full-pair superset when 1m coverage is incomplete. A 50-symbol `5m_15s` signal-entry dry sample reduced planned confirm windows from the old broad path to 232 passing windows, dominated by safe rejects `seed_htf_return_below_min`, `seed_ltf_flow_not_sustained`, `seed_htf_trade_ratio_below_min`, and `seed_htf_quote_ratio_below_min`. A 50-symbol pre-entry merge sample reduced merged window time only about 17%, so it is not the main answer.

Conclusion: filters alone in the current REST aggTrades path will not make 30d practical. The next real 10x step is changing the data source/path: bulk daily aggTrades or a shared prebuilt aggTrades cache that materializes both 15s and 30s targets, plus an artifact-light long-run mode. Do not launch 30d full-universe/full-artifact on REST pagination expecting it to finish quickly.

## 2026-06-03 - P502 targeted aggTrades rate-limit repair

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

The P500 artifact audit showed structurally complete but data-incomplete 1d runner discovery artifacts: targeted fetch errors were dominated by Binance `HTTP 418` and `HTTP 429`. That made a 30d run unsafe because it would mostly test exchange throttling/ban behavior, not Pump Awakening edge.

P502 adds a process-wide Binance aggTrades request limiter and retry/backoff policy shared by the direct target-LTF fetch and the legacy 1s aggTrades backfill. Targeted fetch workers default from 4 to 2 so the planner can remain parallel while network requests are globally paced. This changes data-loading reliability, not PumpDecisionCore thresholds, snapshots, execution model, or strategy verdicts.

Next: rerun a 1d runner discovery smoke before 30d. Acceptance is near-zero `targeted_ltf_fetch.csv` errors, or explicit remaining unavailable-data rows. If `HTTP 418/429` remains common, reduce fetch workers to 1 or use a prebuilt aggTrades source; do not launch 30d from a throttled/partial data state.

## 2026-06-03 - P500 runner discovery cache reuse and staged-path acceleration

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

The user's 1d run was not slow because the strategy core needed more threshold tuning. Profiling showed several infrastructure bottlenecks: the live process spent CPU in legacy `_collect_symbol_candidates`/snapshot hashing for seeds that `signal_entry_plan` had already rejected; targeted cache subtraction ignored `30s/delta/*.parquet`; empty aggTrades windows were never marked covered; and `_resolve_end_timestamp_ms` loaded full HTF cache frames just to find the last timestamp.

P500 fixes the path without changing decision thresholds. Final targeted decisions now use only exact `signal_entry planned` seed timestamps, not all `pre_entry` pair starts. Targeted planning/fetch can run in bounded parallel workers. Direct aggTrades target-LTF writes maintain a compact coverage sidecar, and empty aggTrades windows write verified coverage index rows so they are not fetched forever. End timestamp resolution now reads timestamp columns only.

Observed diagnostics: before the coverage fix, 3831 pre-entry windows looked fully missing (`638.5h` of aggTrades) even after hours of running. After building coverage indexes for existing delta files, the same plan showed 3491 covered windows and about `4.93h` missing. A 12-window fetch smoke completed in ~2.5s and wrote empty-window coverage index rows.

Next: rerun only `5m_30s`/1d first, not all four profiles. Acceptance is that pre-entry LTF does not restart from hundreds of missing hours and `targeted_ltf_fetch.csv` shows cache-covered windows plus bounded fetch errors instead of an endless black hole.

Follow-up artifact audit of `.output/results/htf_ltf_runner_discovery_1d`: all four profile artifact sets and combined CSVs are present, and failures are visible. However, the run is not data-complete. `5m_30s` has 283 pre-entry fetch errors, `3m_30s` has 238 fetch errors, and the 15s profiles have mass fetch errors (`HTTP 418`/`HTTP 429`). Do not launch 30d from this state; first fix/throttle/resume targeted fetch so 1d completes with near-zero fetch errors or explicit unavailable-data accounting.

## 2026-06-03 - P499 signal-entry planner no longer runs full seed-stage core

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

The fresh 1d run exposed a new bottleneck: `5m_30s targeted signal-entry plan` reached only 277/594 symbols with ETA about 7h18m. This was not network fetch. The planner was running the full seed-stage shared core for every exact rolling seed candidate, including 24h context hashing/prior-spike/median work, just to decide whether to fetch a short confirm/next-open LTF window.

P499 changes signal-entry planning to a conservative data-loading superset. It keeps only the context-free mandatory seed-return gate before confirm fetch. Seeds with return below the shared core minimum are terminally impossible and remain visible as `not_planned_seed_stage_terminal_reject`; all other exact seeds get a short confirm/next-open fetch window and are decided later by the normal shared core. This may fetch more short LTF windows than P494, but it avoids hours of CPU prefiltering and cannot drop a valid signal.

Benchmarks on cached 1d `5m_30s`: JCT signal-entry plan fell from about 29.6s before the fix path to 1.5-6.6s during intermediate patches and then full 50-symbol benchmark completed in 54.4s for signal planning. Full universe should now be minutes-scale rather than a 7h planning black hole.

Next: rerun `run-htf-ltf-runner-discovery --days 1`. Acceptance: signal-entry planning progresses steadily; planned confirm windows increase versus P494, but decision ledger must show real shared-core reasons after processing.

## 2026-06-02 - P498 closed cheap baseline context for rolling discovery

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

Pre-start audit found two blockers before retrying a long `5m_30s` backtest. First, progress output used a unicode arrow in `targeted aggTrades→30s`, which can crash Windows cp1252 consoles before artifacts are written. Second, P497 did not fully solve rolling seed context: a rolling 5m seed can start on a 30s offset, so exact seed-aligned 24h context would require downloading 24h of 30s candles per exact seed. That is not an acceptable acceleration path.

P498 changes the long pre-seed context contract to closed cheap baseline context: adapters may provide HTF-width context aggregated from fully closed 1m/HTF candles ending no more than 60s before the rolling seed open. Exact aggTrade-derived 30s/15s remains required for the rolling seed and post-seed confirmation morphology. The JCT 1d smoke after P498 produced real rejects only (`seed_htf_return_below_min`, `ltf_confirm_return_below_min`, `seed_ltf_flow_not_sustained`, etc.) and zero `contract_seed_aligned_context_not_ready`.

Next: run a fresh 1d full-universe `5m_30s` smoke before any 30d run. Acceptance: dependency reasons should be dominated by genuine missing 1m/seed/confirm cache or network failures, not context-contract misses; signal-entry fetch windows should remain short confirm/next-open windows, not 24h 30s context downloads.

## 2026-06-02 - P497 HTF warmup for 1d targeted runner discovery

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

Superseded by P498 for rolling offset seeds. P497's HTF warmup is still useful for cheap context availability, but the old seed-aligned interpretation is not enough for `5m_30s`/`3m_15s` rolling offsets without forcing 24h LTF downloads.

The 1d `5m_30s` zero-run diagnosis showed an old pre-P493 artifact, but review found a real remaining bug in current code: processing and targeted planners loaded HTF only from the requested scan `start_ms`, while the shared core requires 24h seed-aligned pre-seed context. On a 1d smoke, this can still make most early candidates `contract_seed_aligned_context_not_ready`.

P497 separates scan range from context range. Candidate discovery still scans only the requested period, but HTF context/baseline frames are loaded with the contract warmup before `start_ms`. A validation sample from the old 1d candidates moved from context dependency to real seed-stage rejects for candidates whose cache has enough 24h history; symbols without sufficient old cache still correctly remain data dependencies.

Next: rerun fresh 1d `5m_30s` smoke. Acceptance: `contract_seed_aligned_context_not_ready` should collapse except for symbols whose HTF cache genuinely lacks the 24h warmup.

## 2026-06-02 - P496 pandas FutureWarning cleanup in targeted cache subtraction

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

P495 introduced noisy repeated pandas `FutureWarning` lines from `fillna(False).astype(bool)` on object-typed `aggtrade_coverage_verified` metadata during targeted cache subtraction. P496 replaces that path with explicit truthy-mask normalization, so cache coverage checks stay strict without warning spam.

No strategy, fetch selection, snapshot, execution, or threshold logic changed.

## 2026-06-02 - P495 trusted target-LTF cache interval subtraction

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

Review result: the proposed "post-entry replay only after executable guard" was already true in current code. `_build_first_ltf_signal(...)` returns `signal_rows` only after selected snapshots pass the backtest execution guard, and post-entry replay is planned from `signal_rows`.

P495 implements the missing safe acceleration instead: direct aggTrade targeted fetch now subtracts already trusted target-LTF cache buckets before network fetch. A bucket is trusted only when the target cache has the direct aggTrades source/version metadata and `aggtrade_coverage_verified=True`. Missing, stale, untrusted, or partial buckets remain fetch-required.

Next: rerun a short smoke or restart a failed long run. Acceptance: `htf_ltf_runner_targeted_ltf_fetch.csv` should expose `cache_subtraction_model=trusted_target_ltf_bucket_interval_subtraction`, `requested_window_ms`, and smaller `fetched_window_ms` on partially cached windows.

## 2026-06-02 - P494 shared-core seed-stage signal-entry prefilter

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

P494 adds the next safe acceleration layer for targeted subminute runner discovery. After exact seed LTF has been fetched, the signal-entry planner now calls a seed-only shared-core evaluator before fetching post-seed confirm/next-open LTF. It skips confirm fetch only when the shared core already returns a terminal `rolling_htf_seed` reject from data known at seed close.

Honesty boundary: this is still a data-loading guard, not a trading filter. It does not use confirm candles, future runner labels, post-entry highs/lows, exits, PnL, or selected-trade survival. If seed/context data is missing or degraded, the planner keeps the window and fetches exact LTF instead of guessing.

Next: rerun a short `5m_30s` smoke and inspect `htf_ltf_runner_targeted_ltf_plan.csv`. Acceptance: `not_planned_seed_stage_terminal_reject` rows are visible, `signal_entry` fetch volume drops, and remaining exact decision rows show normal shared-core selected/rejected/dependency verdicts.

## 2026-06-02 - P493 HTF pre-seed context for targeted discovery

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

The failed long run did not prove "no entries". It reached exact decision evaluation, but every one of the 180,682 `5m_30s` decision rows was `data_dependency_not_ready` with `contract_seed_aligned_context_not_ready`. The final `EmptyDataError` did not erase selected trades; there were no selected trades because the shared core never received the required pre-seed context.

Root cause: after P491, targeted subminute fetch correctly avoided long LTF replay, but `_build_seed_first_backtest_snapshot` still tried to reconstruct the full 24h seed-aligned HTF context from targeted subminute LTF. Those targeted windows contain seed/confirm data, not 24h of 30s/15s history, so the shared core rejected all candidates as missing context.

P493 changes the backtest adapter to build `pre_seed_context_candles` from the cheap closed HTF cache. Exact subminute LTF is still used for seed internal flow and LTF confirmation. This matches the contract: pre-seed context is HTF-width closed candles ending at seed open.

Next: rerun a short `5m_30s` smoke. Acceptance: `data_dependency_not_ready:contract_seed_aligned_context_not_ready` should collapse, and the ledger should show real seed/confirm/category reject reasons or selected signals.

## 2026-06-02 - P492 zero-trade runner discovery artifact guard

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

The 7d/30d runner discovery run exposed a post-processing bug after `5m_30s` finished with `signals=0` and `trades=0`: `htf_ltf_runner_trades_raw.csv` was a BOM-only empty file, and CLI aggregation crashed with `pandas.errors.EmptyDataError: No columns to parse from file`. This is not edge evidence and not a strategy reject issue; zero trades must be a valid analyzable outcome.

P492 makes CLI aggregation treat empty CSV artifacts as empty frames and makes new zero-row signal/trade artifacts write stable headers. This preserves the profile index and lets later profiles continue even when one profile has no selected trades.

Next: rerun the command from the failed profile/output root if possible, or run a short 1d smoke with a deliberately no-trade profile. Acceptance: no `EmptyDataError`, `htf_ltf_runner_discovery_index.csv` is written, and zero-trade profiles remain visible in summaries.

## 2026-06-01 - P491 backtest acceleration implementation

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

P491 implements the non-biased acceleration path for subminute HTF/LTF runner discovery. The data path is now staged: cheap HTF plus optional 1m impossibility bounds plan seed LTF; exact rolling seeds get only the short confirm/next-open signal-entry window; long future label/exit replay LTF is fetched only after `PumpDecisionCore` returns selected signals.

The new `research_tools.runner_coarse_prefilter` module rejects only proven-impossible coarse 1m windows. If 1m coverage or bounds are incomplete, the planner keeps the window and fetches exact LTF. It does not use future labels, top-growth membership, exits, PnL, post-entry highs/lows, or result survival as pre-entry filters.

Also fixed a latent exact-snapshot bug: `_pre_seed_context_candles_from_ltf` checked `window.status.ok` even though strict LTF window status is a string. It now correctly requires `window.status == "ok"`.

Validation: `tests/test_runner_discovery_acceleration.py` and `tests/test_pump_decision_contract.py` passed; compileall for `data/exchanges research_tools cli constants.py main.py` passed; `research_tools.decision_contract_guard` passed. Legacy `tests/test_htf_ltf_runner_discovery.py` still has stale expectations from pre-shared-core signatures and older TP assumptions and is not proof against this patch.

Next: run a 1d/3d targeted discovery smoke and compare selected `snapshot_hash`/`signal_verdict` against the previous path. Acceptance is unchanged selected signal verdicts with materially lower post-entry LTF fetch volume.

## 2026-06-01 - P490 TP1 full-close dust rounding guard

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

The LITE run exposed a live-safety bug, not a strategy bug: a profitable full TP1 close on a tiny position could fail because fetched exchange amount was dust-rounded below Binance's minimum amount precision, then emergency close flattened the position but live remained halted by `position_integrity_error`.

P490 changes full TP1 close sizing to use the larger of exchange amount and locally protected remaining amount. This should let LITE-like `0.009999999`/`0.01` cases close normally and keep supervisor/execution ready after final close verification.

Next: restart live after P489/P490 and verify the next tiny full-TP1 close emits `position_tp1_full_close_verified` or a normal final close, without `tp1_full_reduce_only_close_failed` and without `execution_not_ready`.

## 2026-06-01 - live2 20260601_125722 position audit state

Current commit: UNKNOWN. Status: analysis only.

The run opened three positions: NFP, LITE, PLTR. NFP is a truthful stop-loss lifecycle but poor strategy evidence: it entered after +5.17% pregrowth and +4.22% seed with near-cap risk, then stopped out. LITE is a truthful profitable lifecycle, but exposed an execution/supervisor bug: after a full reduce-only close at profit, a below-min TP1 close attempt raised `position_integrity_error` and halted new entries. PLTR is a truthful protected entry but invalid signal nature: pre-seed was a -2.07% mostly-red dump/rebound, addressed by P489 for future runs.

Next: fix below-min/precision handling for full TP1 or close-all supervision before using this live process for new entries. After that, restart with P489 and check that PLTR-like snapshots reject as `pre_seed_dump_rebound_pattern`.

## 2026-06-01 - P489 PLTR pre-seed dump/rebound guard

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

Run `.output/results/live2_anomaly_runs/20260601_125722` opened PLTR from an `A_resonance_prior_spike` selected snapshot. The entry was technically fresh and protected, but the signal nature was wrong for Pump Awakening: pre-seed pregrowth was about -2.07% with zero positive HTF pregrowth steps. This is not dormant market -> upward flow expansion; it is selloff/noise -> rebound.

P489 makes this a shared-core seed-stage reject: `pre_seed_dump_rebound_pattern`. New ledgers expose pregrowth downside/path/range fields so future runs can show whether a symbol was rejected because the activity came from a dump/rebound prelude.

Next: restart live2 and verify PLTR-like setups show `pre_seed_dump_rebound_pattern` in `live2_decision_ledger.csv`. Separately fix the LITE execution halt from the same run (`tp1_full_reduce_only_close_failed` on below-min precision amount); do not mix that with signal-nature work.

## 2026-06-01 - P488 Chinese-symbol live trade audit

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

Run `.output/results/live2_anomaly_runs/20260601_102722` produced one complete live lifecycle on `龙虾/USDT:USDT`. The signal was a `3m_15s` `C_balanced_flow_acceptance` setup with sustained seed flow and fresh entry guard. Actual entry fill, initial stop, TP1 partial close, stop resize, structural trail and final flat close were all visible.

The lifecycle was good evidence that execution management works, but it exposed an audit/PnL recovery bug: final `position_final_close_verified` did not add the final stop child-fill realized profit from user-data. Binance reported the triggered stop as a reduce-only child market order with sanitized/different identifiers (`l2sr____...`, order `182715994`) rather than the original conditional stop id. User-data showed +0.062356 USDT on the final fill, while the final position event retained only TP1 realized PnL.

P488 fixes the recovery path with a strict child-fill fallback. Approximate trade result from user-data fills: gross +0.184184 USDT, fees 0.0121019 USDT, net about +0.1720821 USDT.

Next: after restart, verify the next stop-triggered final close reports `realized_pnl_status=recovered_from_user_data_order_trade_update` even when Binance emits a child market order id. Do not use pre-P488 final close PnL totals as complete if `matching_stop_fill_not_found_in_recent_user_data_events` appears.

## 2026-06-01 - P487 terminal seed reject / live uptime

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

Run `.output/results/live2_anomaly_runs/20260601_093452` after P486 shows the main all-symbol deadline flood is mostly gone, but trading uptime is still low. In the sampled session, runtime-gate time was roughly 567s allowed vs 476s blocked. The largest block was private user-data / entry-stream reconnect time, but the avoidable code-side block was latency watchdog holds caused by rare 5-9s hot-path cycles on a few actionable symbols such as JCT/ALPINE.

Root cause: `seed_ltf_flow_not_sustained` is a seed-stage reject, but the confirm-sequence evaluator kept checking later confirm windows until max-confirm. Live also kept the pending seed alive unless max-confirm was reached. That made a seed whose nature was already invalid consume repeated hot-path budget.

P487 makes seed-stage rejects terminal in both the shared core and live adapter. Later confirm candles cannot repair an invalid seed nature. This should reduce repeated evaluation of APR-like one-print/fade seeds and improve trading uptime without changing thresholds.

Next: restart live2 and inspect `decision_loop_max_elapsed_ms`, `decision_loop_overrun_count`, `total_deadline_missed`, `seed_ltf_flow_not_sustained` rows, and runtime-gate seconds. If uptime is still low, separate external stream/DNS downtime from remaining hot-path latency before touching strategy filters.

## 2026-06-01 - P486 internal seed flow-shape guard

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

Live APR showed a real weakness in the contract, not just a parameter issue. The bot opened APR after a `3m_15s` C-category signal with fill around `0.18792`, but the chart/volume shape looked like a one-minute impulse followed by fade. The selected features were borderline: `htf_trade_ratio` about 5.74 near the minimum, `dormancy_to_anomaly_trade_ratio` about 2.87, and initial risk about 2.65%. Entry guard checked freshness/price/RR, but the shared core did not make internal seed LTF flow stability mandatory.

P486 rejects aggregate-valid seeds whose LTF sub-candles look like single-print or faded-tail flow. The expected morphology is quiet baseline followed by distributed/held activity (`_____pPPPP`), not one isolated bar. The core now rejects these as `seed_ltf_flow_not_sustained`, and live ledgers expose the internal flow-shape fields.

Next: restart live2 and verify APR-like one-print/fade setups are rejected by `seed_ltf_flow_not_sustained`. Then run a small backtest/live parity sample before changing any C/A/S thresholds.

## 2026-06-01 - P485 live2 seed return pre-context gate

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

Run `.output/results/live2_anomaly_runs/20260601_080623` improved after P484: decision rows dropped to about 152 and the latest gate state was ready, but trading uptime was still only about 70%. Remaining degradation came from rare minute-boundary hot-path spikes: hundreds of candles closed together, the deadline engine spent 4-8s in seed discovery before checking symbols, and selected core signals could become stale before execution.

P485 keeps the seed-first contract but reorders live seed discovery. Before building seed-aligned 24h context or baseline ratios, live now checks the context-free shared seed prerequisite: rolling seed return must be at least `ROLLING_SEED_MIN_HTF_RETURN_PCT`. Seeds failing this cannot pass the core, so skipping context work for them is not a strategy change.

Next: restart live2 after P485 and inspect `total_seed_return_gate_rejected`, `decision_loop_max_elapsed_ms`, `decision_loop_overrun_count`, and stale selected entry-guard rejects. If large spikes remain, the next fix should be cached/incremental rolling context, not threshold tuning.

## 2026-06-01 - P484 live2 seed-possible scheduler gate

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

Run `.output/results/live2_anomaly_runs/20260601_071606` showed P483 was insufficient. Trading uptime was about half the session and the run accumulated about 12k `closed_bucket_was_not_evaluated_before_deadline` rows. Data streams and artifacts were healthy; the bottleneck was that liquid symbols passed the absolute quote/trade actionability gate even when no pending/new rolling seed existed, so they still flooded the deadline queue.

P484 adds a seed-possible scheduler gate before deadline sorting. For non-position symbols without a pending seed on the current decision timeframe, the live signal adapter now performs only current-tick seed discovery. If no pending/new seed exists, the bucket is marked `market_quiet_non_actionable` with `no_pending_or_new_rolling_seed_candidate` and is not allowed to consume deadline budget. This preserves the seed-first contract: without a rolling seed, the shared core cannot produce a trade.

Next: restart live2 after P484 and inspect whether `closed_bucket_was_not_evaluated_before_deadline`, `decision_loop_overrun_count`, and `decision_latency_degraded` collapse. If misses remain, profile the cost of seed discovery itself or true pending-seed core evaluation; do not change thresholds first.

## 2026-06-01 - P483 live2 quiet-drain deadline fix

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

Run `.output/results/live2_anomaly_runs/20260601_064408` improved after P482: market streams were ready, data dependencies were no longer the dominant issue, and shared-core signal rejects became visible. It is still not edge evidence. At audit time live2 still had zero selected signals and thousands of deadline misses, dominated by all-symbol dirty buckets competing for the same 15s/30s decision deadlines.

P483 changes the scheduler hot path only: before deadline sorting, quiet buckets that do not cross quote/trade/return actionability are marked `market_quiet_non_actionable` and removed from deadline competition. Open positions and symbols with pending rolling seeds are preserved. This should reduce routine `closed_bucket_was_not_evaluated_before_deadline` rows without changing shared-core thresholds or execution behavior.

Next: restart live2 after P483 and compare `total_deadline_missed`, `decision_loop_overrun_count`, and the `deadline_decision` mix against run `20260601_064408`. Do not tune thresholds until the runtime gate stays ready long enough to exercise real signal/entry guards.

## 2026-06-01 - P482 live2 deadline/context root cause

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

Run `.output/results/live2_anomaly_runs/20260601_055306` showed live2 was not honestly testing edge: entries were blocked by `decision_latency_degraded`. At audit time there were about 79k decisions, 57k deadline misses, zero selected signals, and core dependencies dominated by `pre_seed_context:live_seed_aligned_context_not_ready`. Market data streams were healthy enough; the bug was live orchestration load and context plumbing.

P482 fixes two root causes: the deadline engine now applies its existing cheap `actionable_reason` gate before creating warning decision records, and the live signal adapter no longer stores every rolling seed when pre-seed context is unavailable or the seed cannot pass the shared core's basic seed gate. Minute-aligned live seeds can use startup/maintenance 1m candles to build 3m/5m seed-aligned context; non-minute-aligned seeds still require true LTF history.

Next: restart live2 and inspect whether `total_deadline_missed`, `decision_latency_degraded`, and `live_seed_aligned_context_not_ready` collapse. Do not judge strategy quality from the pre-P482 run.

## 2026-05-31 - P481 contract unit coverage

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

Added focused unit coverage for the current shared-core contract and the fragile data-availability guards: snapshot hash inputs, exact rejected confirm snapshots, deterministic context slicing, 15s profile registration, safe HTF confirm upper-bound behavior, 5m OI availability timing, and independent live 15s/30s decision-bucket state.

Validation: `.venv\Scripts\python.exe -m pytest tests\test_pump_decision_contract.py -q` passed with 8 tests; `decision_contract_guard` and compileall also passed. Existing legacy `tests/test_htf_ltf_runner_discovery.py` / `tests/test_live2_market_watch.py` still contain stale assertions/imports from older strategy paths and should be migrated separately rather than used as proof against the shared-core path.

Next: migrate or retire legacy tests that still call removed live-only helpers or old discovery signatures, then run a small real runner discovery smoke.

## 2026-05-31 - P480 cheap confirm upper-bound planner gate

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

Runner discovery targeted LTF backfill now has an additional safe pre-entry planner gate: after seed/category upper bounds pass, it checks the next cheap HTF candle as a deliberately loose upper bound for the post-seed confirm horizon. It rejects before expensive LTF/1s backfill only when even the optimistic HTF-bound cannot satisfy shared-core minimum confirm return, quote pace, or trade pace. If the next HTF candle is missing or non-adjacent, the gate does not reject. New planner counters: `rejected_impossible_confirm_return`, `rejected_impossible_confirm_quote_pace`, `rejected_impossible_confirm_trade_pace`; planned rows expose `confirm_bound_*` fields.

Honesty: this is a data-loading impossibility gate, not a runner-quality filter. It does not use future labels, exits, PnL, realized trade outcome, or post-entry replay. It may keep too many windows, but should not drop a window that could have produced a valid core confirm snapshot.

Next: rerun a small runner discovery and inspect `htf_ltf_runner_targeted_ltf_plan.csv` before trusting speedup. Acceptance: nonzero confirm-bound rejects reduce planned LTF windows while exact selected snapshots still pass shared-core decision and data-dependency artifacts remain visible.

## 2026-05-31 - P479 live multi-timeframe decision streams

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

Live2 no longer treats 15s vs 30s as an operator-selected mode. It now runs independent deadline engines for both `15000` and `30000` ms decision streams by default. Per-symbol processed buckets and rolling seed discovery watermarks are stored per timeframe, so a 15s bucket cannot consume or block a 30s bucket for the same symbol. Both streams still share the same execution/portfolio layer, so selected signals compete honestly for actual capacity, existing-position, cooldown, fill and stop checks.

Next: run a short live smoke and inspect `decision_status.engines` split by timeframe, `live2_decision_ledger.csv` `tf_set` mix, deadline misses, and duplicate same-symbol portfolio blocks before any signal-quality conclusion.

## 2026-05-31 - P478 add 15s rolling profiles

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

The shared rolling seed-first contract now supports `5m_15s` and `3m_15s` in addition to `5m_30s` and `3m_30s`; core version is `p478_add_15s_profiles`. The 15s profiles keep the same wall-clock confirmation requirement as 30s profiles: min confirm is 60s (`4x15s`), max confirm is 4m for 5m seed (`16x15s`) and 3m for 3m seed (`12x15s`). Live2 signal adapter is generalized to use the current decision candle timeframe. Backtest runner discovery now includes separate `5m_15s` and `3m_15s` profiles.

Honesty: 15s profiles are parity/research candidates, not a proven edge. They increase live decision load and should be validated with a small controlled parity smoke before any threshold tuning. Category C/A matching is shared across supported profiles; the strict S category remains effectively the original `5m_30s`-specific shape in the matcher.

Next: run a small `--decision-timeframe-ms 15000` live dry smoke and a matching short `run-htf-ltf-runner-discovery` window, then compare ledgers by `snapshot_hash` and inspect deadline misses before judging signal quality.

## 2026-05-31 - P477 snapshot hash integrity and live cooldown parity

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

The shared decision contract now treats data dependencies and candle `source_status` as core-affecting snapshot inputs; core version is `p477_snapshot_hash_integrity`. This fixes the P476 parity bug where identical `snapshot_hash` values could produce different `signal_verdict` when one adapter supplied a non-ok dependency and another did not. Rejected live no-category paths now return the final exact confirm-window verdict instead of an aggregate reject snapshot without `ltf_confirm`, improving live/backtest ledger joins for rejected signals. Core selected features now expose rolling HTF/LTF timeframe milliseconds so live cooldown can match the selected rolling profile.

Accepted limitation: live and backtest may still use different data-acquisition mechanisms. Backtest can use cheap HTF planning before expensive LTF/1s backfill, and live can repair context through REST. Those belong to the data-availability layer; parity claims apply once both adapters have built a normalized decision snapshot.

Next: run a controlled small parity smoke with overlapping data and compare `live2_decision_ledger.csv` vs `htf_ltf_runner_decision_ledger.csv` by `snapshot_hash`. Any `same_snapshot_different_signal_verdict` row is now a contract bug; `live_only_snapshot` / `backtest_only_snapshot` rows should first be explained by data-availability/planner coverage.

## 2026-05-31 - P476 deterministic seed-aligned context contract

Current commit: UNKNOWN. Status: P476 PROPOSED. P465-P472/P474/P475 expected applied locally / UNKNOWN commit.

The shared rolling seed-first contract now requires a deterministic seed-aligned pre-seed context: non-overlapping HTF windows ending exactly at `seed_open_ms`, with fixed per-TF length covering the 24h prior-spike horizon and baseline/dormancy/pregrowth windows. Adapters may retain more history, but the core hashes and derives features only from the exact contract slice. This fixes the P475 risk where live/backtest could pass different amounts of history and get different snapshot hashes for the same seed/confirm.

Next: run a small discovery smoke and a live2 dry smoke to confirm `contract_seed_aligned_context_not_ready` appears when context is genuinely missing, then confirm live/backtest parity join on a controlled overlapping data window.

## 2026-05-31 - P474 remove legacy duplicate decision paths

Current commit: UNKNOWN. Status: P474 PROPOSED. P465-P472 expected applied locally / UNKNOWN commit. P473 scope-hygiene patch intentionally skipped by operator request.

The rolling seed-first contract cleanup now removes the remaining post-hoc backtest category rematch helper. HTF/LTF discovery must use the category selected by `PumpDecisionCore.evaluate_first_ltf_confirm_after_seed` and must not recompute C/A/S from trade rows after execution simulation. A source-level guard `python -m research_tools.decision_contract_guard` fails if legacy live/backtest-only decision helpers, baseline-free live prefilters, confirm-backward paths, or adapter-side `match_rolling_categories` calls are reintroduced.

Next: run compile + guard, then run a small HTF/LTF discovery smoke and a short live2 dry/real smoke to verify `decision_ledger` and `live2_decision_ledger` are populated from the same snapshot-hash contract.

## 2026-05-31 - P470 live2 shared-core-only signal adapter

Current commit: UNKNOWN. Status: P470 PROPOSED. P465-P469 expected applied locally / UNKNOWN commit.

Live2 signal selection is now intended to have no separate executable decision implementation. The live adapter discovers rolling HTF seeds, builds `DecisionSnapshot`, calls `PumpDecisionCore.evaluate_first_ltf_confirm_after_seed`, and returns the core verdict to entry guard/execution. Legacy confirm-backward evaluation, baseline-free live prefilter, and duplicate live C/A/S helper paths are removed from the signal adapter.

Next: P471 should separate core `signal_verdict` from portfolio/execution allocation verdicts in artifacts so max-position/risk/cooldown blocks never distort signal-quality statistics. Then add the explicit live/backtest parity ledger and snapshot-hash comparison.

## 2026-05-31 - P469 live2 rolling seed state machine

Current commit: UNKNOWN. Status: P469 PROPOSED. P465-P468 expected applied locally / UNKNOWN commit.

Live2 is migrated at the scheduler/orchestration layer from “current confirm candle looks backward for HTF” to “store rolling HTF seed first, then evaluate the first post-seed LTF confirm through the shared decision core.” Execution and portfolio allocation remain outside the core. Live/backtest parity is still incomplete until P470 removes the remaining legacy live decision helpers and P471 separates signal verdicts from portfolio verdicts in artifacts.

Next: P470 should make `Live2SignalEngine` a thin adapter around `PumpDecisionCore` and remove the old confirm-backward `_rolling_runner_category_setup` / `_evaluate_rolling_profile` path from executable code.

## 2026-05-31 - P468 backtest adapter to shared seed-first core

Current commit: UNKNOWN. Status: P468 PROPOSED. P465-P467 expected applied locally / UNKNOWN commit.

HTF/LTF discovery now acts as a backtest adapter for the shared rolling decision core on the selected-signal path: it loads/cache-normalizes HTF/LTF candles, builds `DecisionSnapshot`, calls `evaluate_first_ltf_confirm_after_seed`, and writes a decision ledger plus rejected/data-dependency artifacts. Backtest execution remains a separate next-open-plus-slippage simulation layer. Live is not migrated yet, so live/backtest parity is still incomplete until live uses the same seed-first state machine and core call.

Next: P469 should convert live2 from confirm-backward scanning to a rolling seed state machine; P470 should make live2 signal evaluation a thin adapter around the same core.

## 2026-05-31 - P467 seed-first rolling decision core

Current commit: UNKNOWN. Status: P467 PROPOSED. P465/P466 expected applied locally / UNKNOWN commit.

P467 adds the first executable shared decision core for the rolling contract. The core evaluates a source-neutral `DecisionSnapshot` as rolling HTF seed -> first post-seed LTF confirmation -> selected/rejected/data_dependency_not_ready. It computes seed, pre-seed context, LTF confirmation, prior-spike, category and signal-risk fields without pandas and without knowing whether the data came from live sockets or backtest REST/aggTrades. Live and backtest are not migrated yet.

Next: P468 should convert HTF/LTF discovery into a snapshot builder that calls `evaluate_first_ltf_confirm_after_seed`, and should write a decision ledger/rejected exact windows from core verdicts instead of maintaining a separate decision path.

## 2026-05-31 - P466 shared rolling C/A/S category matcher

Current commit: UNKNOWN. Status: P466 PROPOSED. P465 expected applied locally / UNKNOWN commit.

P466 moves the frozen C/A/S rolling category matcher into `research_tools/pump_decision_core.py` and makes both live2 and HTF/LTF discovery call that shared pure function. It intentionally preserves every threshold, supported TF set, category id, and category priority from the duplicated implementations. This is an architecture/parity patch only; it still does not introduce the seed-first evaluator or change execution/portfolio behavior.

Next: P467 should add the actual seed-first evaluator around the shared matcher, with deterministic snapshot input and typed selected/rejected/data-dependency verdicts.

## 2026-05-31 - P465 rolling seed-first parity contract

Current commit: UNKNOWN. Status: P465 PROPOSED.

The next parity work is architectural, not threshold tuning. Live and backtest must both be rolling-based: build a rolling HTF seed first, find the first LTF confirmation after that seed, then pass an equivalent source-neutral snapshot into one shared decision core. P465 adds only the typed contract boundary for that core. It does not change trading behavior.

Next: P466 should move C/A/S category rule matching into `research_tools/pump_decision_core.py` without changing thresholds or profile coverage. After that, backtest should become a snapshot builder before live is migrated to a seed-first state machine.

## 2026-05-30 - P462 live2 all-symbol baseline-free deadline gate

Current commit: UNKNOWN. Status: P462 PROPOSED. P449 was applied by the user locally / UNKNOWN commit.

Goal: reduce live2 deadline load without top-K and without narrowing the universe. Every dirty symbol is still observed. The new gate only rejects candidates that cannot pass the same rolling C/A/S contract by exact baseline-free prerequisites: contiguous 30s confirmation, LTF runner shape, HTF seed availability/return, no confirmation undercut, and structural risk cap. Anything that might still pass baseline/dormancy/category checks continues into the full signal engine.

This is a scheduler/hot-path patch, not a strategy-threshold change. It should turn many `closed_bucket_was_not_evaluated_before_deadline` rows into explicit `rejected_signal_contract` rows with `feature_mode=rolling_baseline_free_prefilter`.

Next: run 60-90 minutes live2 and compare `deadline_missed_count`, `budget_exhausted_count`, and `signal_engine.total_baseline_free_prefilter_rejected`. If deadline misses remain high, the next clean step is incremental rolling context/state caching, not top-K.

## 2026-05-30 - Live2 audit state after Ctrl+C artifact review

Current commit: UNKNOWN. Uploaded workspace patch status: P449 PROPOSED.

Latest live artifacts were collected after forced Ctrl+C, so partial top-growth/user-data counters are not proof of live failure. The real unresolved runtime problem from the run is deadline load: hundreds of 30s actionable buckets can close together, while the deadline engine evaluates symbols sequentially and only a handful finish before the 1.5s decision deadline. No-trade execution lifecycle was not exercised in that run.

P449 is audit/safety hygiene only: remove dead signal entry-drift duplicate, add entry guard live-price source to artifacts/state, and make stop visibility distinguish `visible`, `absent`, and `api_error`. It does not solve the deadline load.

Next clean scheduler direction: keep the “all symbols observed” contract, but replace top-K with deterministic impossibility gates and incremental per-symbol rolling features so every symbol is cheap to classify and only mathematically possible C/A/S candidates reach expensive context/category checks.

## 2026-05-30 - P448 live2 flat-stop recovery crash fix

Current commit: UNKNOWN.

Status: P448 PROPOSED after the 2026-05-29 live run crashed at 22:54:34 in `Live2PositionSupervisor._supervise_position` with `NameError: name 'refreshed_amount' is not defined`. The crash occurred in the safe recovery path where Binance showed the position flat and the protected stop gone. The intended contract remains: treat that as a verified final close only after the stop is gone, recover realized PnL from user-data when available, remove the protected position, and continue live.

Next: apply P448, restart live2, and verify that a normal stop-trigger/flat-stop-gone transition writes `position_final_close_verified` instead of crashing. Then address the remaining health issues: decision backlog/latency, open-position status sync in grid, and compacting routine deadline event payloads.

## 2026-05-29 - P447 live2 rolling 1m context maintenance

Current commit: UNKNOWN.

Status: P447 PROPOSED after P446. Live2 now has a separate bounded background official-1m-kline maintenance layer for rolling context. It refreshes only closed 1m candles, prioritizes active/actionable symbols, exposes status in JSON/grid artifacts, and keeps WS aggTrade as the only live-flow source for 30s decisions.

Next: run a 60-90 minute live smoke. Acceptance: maintenance status is `running`, `total_errors` stays near zero, `last_loaded_candles` increments after minute boundaries, `rolling_1m_maintenance_source` appears in `live2_symbol_state.csv`, `rolling_1m_history_not_ready` falls materially versus the prior run, and decision latency / WS reconnects do not degrade.

## 2026-05-29 - P446 live2 actionable data-readiness hygiene

Current commit: UNKNOWN.

Status: P446 PROPOSED after the artifact backpressure fix exposed that most live2 decisions were routine weak buckets or misclassified flow freshness. The intended live contract is now explicit: quiet buckets stay `market_quiet_non_actionable` in the grid and do not run the signal engine; stale flow in a closed bucket is a strategy/freshness reject (`flow_freshness_reject`), not `data_not_ready`; rolling 1m context gaps carry typed repair metadata into the existing official Binance 1m kline repair path.

Next: run a 60-90 minute live smoke. Acceptance: `real_trade_bucket_for_backtest_parity` disappears, `total_data_not_ready` drops sharply, `rolling_context_repair_status_counts` is non-empty only for threshold-actionable candidates, and remaining `data_dependency_not_ready` reasons separate true rolling context gaps from confirm/HTF aggTrade gap tolerance.

## 2026-05-29 - P445 rolling fetch cost guard

Current commit: UNKNOWN.

Status: P445 PROPOSED after the first P444 run projected multi-day targeted 1s fetch time. The cause is not execution simulation but pre-entry data loading: P444 made the 2xHTF pair gate broad enough that many pairs were sent to aggTrades. P445 keeps the rolling model honest while reducing impossible work: first, reuse already trusted target LTF materialized cache before fetching 1s; second, the pair planner may reject a pair only when pair upper bounds and already-closed calendar context prove that no frozen C/A/S family could ever match inside it. This is still data-loading only and does not use future labels, PnL, exits, or LTF pace.

Next: rerun 45d. Inspect `htf_ltf_runner_targeted_ltf_plan.csv` for `rejected_impossible_runner_category_family`, and `htf_ltf_runner_targeted_ltf_fetch.csv` for `target_ltf_exists_covered_requested_windows`. If ETA remains extreme, stop and examine planned pair counts before loosening/tightening anything.

## 2026-05-29 - P444 rolling pair safe-superset widening

Current commit: UNKNOWN.

Status: P444 PROPOSED after P443 lookahead review. P443 made signal-time logic clean, but the two-HTF pair fetch gate still used strict legacy absolute/range thresholds. That can miss pairs where a live rolling window could pass the official seed (`htf_quote_ratio`, `htf_trade_ratio`, `htf_return`) but the calendar pair did not look large enough under the old targeted-download heuristic. P444 changes the pair gate into a true data-loading safe-superset: fetch the pair unless upper bounds prove the exact rolling seed is mathematically impossible. The gate is explicitly not a trading signal and does not use C/A/S or outcome fields.

Next: rerun 45d rolling discovery and audit `htf_ltf_runner_targeted_ltf_plan.csv` for planned pair counts, rejection reasons, and nonzero exact rolling seeds. Expect more LTF fetches; this is the cost of reducing coverage self-deception.

## 2026-05-29 - P443 rolling baseline contamination guard

Current commit: UNKNOWN.

Status: P443 PROPOSED after P439-P442 review. The rolling HTF path compiled and used next-LTF-open entry, but calendar context for a rolling window could include a calendar HTF candle whose start was before the rolling window while its close was inside the rolling window. That is not post-decision lookahead, but it contaminates baseline/dormancy/pregrowth with anomaly-window data. P443 changes context selection to use only calendar HTF candles fully closed before the rolling window start.

Next: rerun 45d rolling discovery and use combined artifacts only. Check `rolling_baseline_model=calendar_htf_candles_fully_closed_before_rolling_window_start` and verify no selected signal has `entry_timestamp_ms < decision_available_timestamp_ms`.

## 2026-05-29 - P442 combined rolling portfolio guard

Current commit: UNKNOWN.

Status: P442 PROPOSED after rechecking P441. P441 fixed signal selection, but profile execution still let unvalidated TF sets (`5m_1m`, `1m_15s`) run through discovery and applied risk-cap/cooldown inside each profile instead of at the combined strategy level. P442 restricts C/A/S to `3m_30s`/`5m_30s`, runs only those profiles by default, and writes combined portfolio artifacts at the root run directory.

Next: apply P442, rerun 45d rolling discovery, and treat root `htf_ltf_runner_combined_*` files as the strategy-level truth; profile-level files remain diagnostics.

## 2026-05-29 - P441 category trigger audit fix

Current commit: UNKNOWN.

Status: P441 PROPOSED after rechecking P439/P440. Rolling seed baseline is fixed, but first-entry selection still used the old generic LTF-confirm first and only assigned C/A/S categories after trade simulation. P441 moves fixed C/A/S priority into the signal loop: entry is now the first category-qualified closed-LTF signal after a rolling HTF seed, not a later post-trade filter.

Next: rerun 45d rolling discovery and verify `htf_ltf_runner_signals.csv` contains `signal_model=first_category_qualified_ltf_signal_after_rolling_htf_seed` and non-empty `runner_candidate_category`.

## 2026-05-29 - P440 rolling seed baseline fix proposed

Current commit: UNKNOWN.

Status: P440 PROPOSED as a correction after P439. P439 compiled but built exact rolling seed baselines from the short two-HTF-candle LTF slice, so the second-stage exact rolling scan could produce zero/near-zero candidates even when a valid rolling seed existed. P440 keeps the cheap two-HTF safe-superset download, but computes exact rolling current candles from that LTF slice while computing baseline/dormancy/pregrowth/prior-spike context from already-available calendar HTF history before the rolling window. Final discovery also restricts rolling candidates to the safe-superset pairs so full post-entry LTF windows cannot create extra unplanned signals.

Next: apply P440 after P439, run `python -m compileall -q data/exchanges research_tools cli constants.py main.py`, then run the 45d discovery and inspect `htf_ltf_runner_targeted_ltf_plan.csv`: post-entry exact rolling seed rows should be nonzero when pre-entry pairs contain real rolling seeds.

## 2026-05-29 - P439 rolling HTF discovery contract proposed

Current commit: UNKNOWN.

Status: P439 PROPOSED against uploaded `source.zip`. Runner discovery is changed from calendar-HTF candidate discovery to rolling-HTF candidate discovery. Calendar HTF candles are not a trading model anymore; they are only a cheap two-candle safe-superset used to decide whether LTF data may contain a rolling seed. The cheap stage fetches only those two HTF candles. Full confirm/label/exit LTF is fetched only after an exact rolling LTF seed is found inside the pair.

Portfolio contract: selected trades are now fixed-priority C -> A -> S, with one open trade per symbol, symbol cooldown equal to one rolling HTF window, risk_per_trade=2% and max_total_open_risk=8%. Blocked signals are written to `htf_ltf_runner_portfolio_events.csv` instead of disappearing.

Next: run `.\.venv\Scripts\python.exe main.py run-htf-ltf-runner-discovery --days 45` and judge the new rolling artifacts as a new backtest model, not as directly comparable PnL against the old calendar run.

## 2026-05-28 - P438 runner/fader OOS v1 hypothesis proposed

Current commit: UNKNOWN.

Status: P438 PROPOSED against uploaded workspace. The 7d four-profile readout did not prove a stable edge: `5m_1m` was negative every day and all profiles were sensitive to top trades. The actionable pre-entry split was not "more volume"; it was real HTF trade-count awakening without LTF blow-off. In-sample selected/live-filtered hypothesis: `htf_trade_ratio >= 12` and `ltf_trade_pace_ratio <= 6`; strict tier additionally requires `htf_quote_ratio <= 48`. On the provided run this lifted runner share and made the non-top remainder positive, but it is still mined in-sample and must be treated as an OOS hypothesis, not live logic.

Patch: add research-only OOS v1 artifacts and rule-score rows for the next 45d discovery run. The strict artifact writes filtered live-filtered trades, daily summary, profitability summary, and top-dependency summary. It uses only known-at-entry HTF/LTF ratios and explicitly marks `uses_future_label_as_entry_filter=False`.

Next: run `.\.venv\Scripts\python.exe main.py run-htf-ltf-runner-discovery --days 45`; accept the hypothesis only if closed trades are sufficient, median net > 0, sum net > 0, positive-day share > 55%, top20 dependency is not dominant, and the result survives with `5m_1m` either disabled or materially improved.

## 2026-05-28 - P437 targeted LTF post-entry speed patch proposed

Current commit: UNKNOWN.

Status: P437 PROPOSED against uploaded workspace. The 7d 5m/30s discovery run showed post-entry targeted LTF fetch did redundant work: many 60m post-entry windows differed by only one/few LTF candles but were not merged because the shared targeted aggTrade merge cap was 10 minutes. P437 keeps pre-entry merging capped, but allows post-entry targeted LTF backfill to merge by gap only (`max_merged_span_ms=None`). It also applies the strict pre-entry HTF seed timestamp filter before expensive post-entry candidate feature/label construction, rather than building broad candidates and discarding non-seeds afterwards.

Honesty: this changes only fetch/materialization planning and CPU pruning. It does not change signal filters, entry price, stop/trailing simulation, future labels, fees/slippage, or strict wall-clock LTF replay.

Next: apply P437, run `python -m compileall -q data/exchanges research_tools cli constants.py main.py`, then rerun the 7d discovery and compare `post_entry` rows in `htf_ltf_runner_targeted_ltf_fetch.csv`: `raw_targeted_windows` should stay the same while `merged_targeted_windows` should fall materially.

## 2026-05-28 - P436 post-entry fetch root-cause fix proposed

Current commit: UNKNOWN.

Status: P436 PROPOSED against P435 workspace. The interrupted 7d run finished `5m_1m` but stalled during `5m_30s` targeted post-entry fetch; the artifact ZIP contains no `5m_30s` CSVs, while the completed `5m_1m` artifacts show the broad HTF gate still creates thousands of candidates/entry windows. Code review found the actual cause: P434 tightened the pre-entry HTF seed gate, but `_build_targeted_ltf_post_entry_backfill_plan()` ignored that seed set and rebuilt post-entry plans from the broad `_collect_symbol_candidates()` anomaly gate. Therefore the expensive 60m post-entry fetch was still planned for every broad executable LTF window.

Patch: carry pre-entry strict seed timestamps into post-entry planning, filter broad candidates to those seed timestamps, and audit how many broad candidates were skipped because they were not strict seeds. This is not an arbitrary cap and does not use future labels/PnL/post-entry prices.

Next: apply P436 after P435, rerun `./.venv/Scripts/python.exe main.py run-htf-ltf-runner-discovery --days 7`, and check the `post_entry` summary in `htf_ltf_runner_targeted_ltf_plan.csv` before moving to 45d.

## 2026-05-28 - P435 finite LTF decay feature fix proposed

Current commit: UNKNOWN.

Status: P435 PROPOSED against P434 workspace. The active run showed repeated `RuntimeWarning: All-NaN slice encountered` from LTF adjacent quote/trade decay features. The underlying cause is not a market/data decision: some valid closed-LTF windows have adjacent ratios that are all missing because previous buckets have zero/missing flow, and the feature code called `np.nanmin`/`np.nanmax` directly. P435 changes these feature summaries to explicit finite-only min/max/median helpers, returning `NaN`/`False` when no finite value exists.

Next: apply P435 after P434, rerun the 7d discovery, and judge runtime from targeted plan/fetch artifacts rather than terminal warning spam.

## 2026-05-28 - P434 stricter HTF seed gate proposed

Current commit: UNKNOWN.

Status: P434 PROPOSED against P432b workspace. The long 7d run showed the remaining cost problem is not full-universe loading but too many HTF seeds being allowed into targeted post-entry 1s fetch. Instead of arbitrary per-symbol caps, P434 tightens the definition of `anomaly worthy of LTF`: closed-HTF quote/trade expansion, price return/range, dormancy-to-anomaly jump, prior dormancy compression, and absolute quote/trade liquidity are all required before targeted LTF backfill is planned. This is an HTF-only cost/quality gate, not a post-factum selection rule.

Next: apply P434 after P432b, skip P433 unless intentionally testing capped budget mode, run `./.venv/Scripts/python.exe main.py run-htf-ltf-runner-discovery --days 7`, and check the pre-entry `htf_ltf_runner_targeted_ltf_plan.csv` summary before judging edge. If zero/too few events pass, loosen one HTF seed parameter at a time; do not disable strict LTF replay or use future labels for seed selection.

## 2026-05-28 - P432b two-stage targeted LTF backfill proposed

Current commit: UNKNOWN.

Status: P432b PROPOSED against workspace where P431 is already applied. The previous P432 patch was too broad for the user's local state. This incremental patch keeps P431 profiles/progress intact and changes only runner discovery's targeted subminute loading model: pre-entry first, then post-entry/runner-horizon only for signals selected by known-at-entry LTF confirmation and entry guards. This should reduce 1s aggTrade fetch volume without adding lookahead or backdated decisions.

Next: apply P432b after P431, run `./.venv/Scripts/python.exe main.py run-htf-ltf-runner-discovery --days 45`, then inspect `htf_ltf_runner_targeted_ltf_plan.csv` phases. `post_entry` rows must have `selection_model=known_at_entry_ltf_confirmation_and_entry_guards_only` and no future-label based selection.

## 2026-05-28 - P431 profile set, per-profile seed gates, and 1% progress proposed

Current commit: UNKNOWN.

Status: P431 PROPOSED against uploaded/P430 workspace. Runner discovery profile set is now `5m_1m`, `5m_30s`, `3m_30s`, and `1m_15s`. The old `5m_15s` profile is removed because the next research question is cross-HTF discovery, not three LTF variants under the same 5m HTF. Targeted subminute backfill remains seed-window-only; 1m LTF uses normal OHLCV cache, while 30s/15s profiles fetch true aggTrade/1s only around strong profile-specific HTF seed events. Seed gates are profile defaults unless explicitly overridden by CLI args, so 1m/15s no longer inherits 5m-derived thresholds. Progress lines now update in place at whole-percent increments to avoid terminal spam.

Next: first update normal OHLCV cache for upper TFs with `./.venv/Scripts/python.exe main.py update-cache --days 45 --timeframes 1m 3m 5m`, then run `./.venv/Scripts/python.exe main.py run-htf-ltf-runner-discovery --days 45`. Judge subminute profiles only after checking targeted plan/fetch/materialize coverage and strict LTF path statuses.

## 2026-05-28 - P430 targeted LTF backfill for runner discovery proposed

Current commit: UNKNOWN.

Status: P430 PROPOSED against uploaded workspace / GitHub branch content not head-verified. The P429 strict replay run proved `5m_1m` is usable but `5m_15s` and `5m_30s` were almost empty because the command stayed cache-only and did not build subminute data around suspected entries. P430 makes runner discovery plan targeted true aggTrade 1s windows only for stricter HTF anomaly seeds, materialize the requested subminute LTF from those windows, and then run the same strict wall-clock replay. The default targeted seed gate is intentionally tighter than the generic anomaly gate: quote ratio >= 12, trade ratio >= 12, HTF return >= 2.27%, HTF range >= 3.0%, max 20 events per symbol.

Next: run `./.venv/Scripts/python.exe main.py run-htf-ltf-runner-discovery --days 45` and inspect `htf_ltf_runner_targeted_ltf_plan.csv`, `htf_ltf_runner_targeted_ltf_fetch.csv`, and `htf_ltf_runner_targeted_ltf_materialize.csv` before judging 15s/30s edge. If too few events are planned, lower only one seed threshold and rerun; do not disable strict LTF replay.

## 2026-05-27 - P429 strict LTF wall-clock honesty proposed

Current commit: UNKNOWN.

Status: P429 PROPOSED against uploaded workspace / GitHub branch content not head-verified. The 5m/15s and 5m/30s artifacts were not clean edge evidence: post-entry replay used the next N available LTF rows, so sparse subminute cache could turn a one-hour hold into multi-day or multi-week exposure. P429 changes runner discovery to use configured wall-clock LTF paths and explicit gap/incomplete statuses for entry windows, future labels and post-entry replay. Flow logic now requires real `quote_volume` and `number_of_trades`; no close*volume proxy is used for strategy evidence.

Next: apply P429, materialize/backfill continuous 15s/30s cache for the tested period, rerun `.\.venv\Scripts\python.exe main.py run-htf-ltf-runner-discovery --days 45`, then judge only artifacts where post-entry and future LTF path statuses are `ok` or where stop exited before any gap.

## 2026-05-27 - 5m/30s win-loss feature readout

Current commit: UNKNOWN.

Status: ANALYSIS on existing `.output/results/htf_ltf_runner_discovery_30d/5m_30s` artifacts. Winners are not simply "more sustained volume". Compared with losers, winning selected trades have higher LTF quote/trade pace, stronger HTF trade/quote expansion, and less adverse MAE. On entry-window trades, winners more often have real dormancy, higher HTF-internal top1 quote/trade concentration, stronger HTF-internal trade acceleration, and lower smooth pregrowth. This supports a sharper hypothesis: the tradable long is compressed dormancy -> abrupt flow/price acceptance, not slow smooth accumulation.

Best in-sample balance candidate remains `ltf_confirm_return_pct >= 0.5236%` plus `dormancy_range_pct_median <= 0.4566%`. Add-on filters that look useful but need out-of-sample validation: `pregrowth_oi_change_pct <= 0.3947%` gave 69 trades / 66 symbols, WR 62.3%, median +2.43%, sum +469.7%, 16/21 positive days, top20/sum 0.807; `htf_ltf_trade_top1_share >= 0.38433` gave 35 trades, WR 77.1%, median +4.17%, top20/sum 0.601 but lower sample; `ltf_quote_pace_ratio >= 17.1` gave 67 trades, WR 67.2%, median +2.81%, top20/sum 0.782 but only 17/25 positive days.

Next: do not hardcode these thresholds into live. Run the new P428 profile set on a held-out period and promote only if the same family improves median trade and positive-day share without top20/sum drifting above ~1.0.

## 2026-05-27 - P428 5m runner discovery profile set

Current commit: UNKNOWN.

Status: P428 APPLIED locally / UNKNOWN commit. The next fixed discovery run replaces `1m_5s` with two 5m-HTF profiles: `5m_1m` and `5m_15s`, keeping `5m_30s`. This matches the current readout that `1m_5s` had more runner labels but weak trade quality, negative median, and high top dependency. Standard artifacts now include daily summaries for selected trades and entry-window trades so strategy stability can be judged by day, not only by aggregate PnL.

Next: rerun `.\\.venv\\Scripts\\python.exe main.py run-htf-ltf-runner-discovery --days 30` after P428 and compare `5m_30s`, `5m_1m`, and `5m_15s` on median trade, WR, positive-day share, top20 dependency, and real flow coverage.

## 2026-05-27 - P427 30d discovery honesty readout

Current commit: UNKNOWN.

Status: P427 APPLIED locally / UNKNOWN commit. The 30d HTF/LTF discovery artifacts show no backdated entry execution: signal and entry-window rows mark future labels unavailable at entry, and all checked entries have `entry_timestamp_ms >= decision_available_timestamp_ms`. The main caveat found during analysis was artifact/category contamination, not trade backdating: `setup_nature` used the future low-break label for `anomaly_low_broken_after_wakeup`. P427 removes that future-derived branch so future runs keep live nature and outcome labels separate.

Research readout: `5m_30s` selected trades are the only currently promising area, with 83 closed live-filtered trades, WR 54.2%, avg +2.99%, median +0.35%, sum +248%, but top 20% winners exceed total net because losers/lower winners offset them. A mined but interpretable 5m fixed-window rule, `ltf_confirm_return_pct >= 0.5236%` plus `dormancy_range_pct_median <= 0.4566%`, verifies on raw same-symbol filtering at 80 trades / 75 symbols, WR 62.5%, avg +6.25%, median +1.46%, sum +500%, 18/25 positive days. Treat it as a next hypothesis, not an edge claim, because it was selected after seeing this 30d result.

`1m_5s` is not strategy-ready: it has more runner labels and more trades, but selected trades have WR 44.0%, median -0.18%, and strong top dependency. Sustained-flow labels lift runner frequency on candidates but do not translate into robust PnL. Next: rerun after P427, then validate the 5m confirm+tight-dormancy hypothesis on a different period before any live promotion.

## 2026-05-27 - P426 fixed-window runner entry research artifacts

Current commit: UNKNOWN.

Status: P426 APPLIED locally / UNKNOWN commit. The next fixed-profile `run-htf-ltf-runner-discovery --days N` run now writes honest early-entry research artifacts instead of forcing one current entry path. For every HTF anomaly candidate it evaluates bounded closed-LTF windows: `5m_30s` uses 2/4/6/8 closed 30s candles; `1m_5s` uses 6/12/18/24 closed 5s candles. Each window decides only after the last closed LTF candle and enters at the next LTF open with adverse slippage. Future +10% runner labels remain artifact-only and are explicitly marked as unavailable/unused at entry.

Research focus: separate runner starts from fader spikes by comparing early quote-volume/trade-count sustain versus candle-to-candle decay under 50%, current spike size versus prior 24h spike median/maximum, non-negative price acceptance, structural low survival, and OI change available by decision time. Entry-window rule scores are calculated both raw and with same-symbol-only overlap filtering per rule; there is still no cap on simultaneous positions across different symbols.

Next: run `.\\.venv\\Scripts\\python.exe main.py run-htf-ltf-runner-discovery --days 30`, then inspect `htf_ltf_runner_entry_window_rule_scores_live_filtered.csv`, `htf_ltf_runner_entry_window_trades_live_filtered.csv`, `htf_ltf_runner_entry_windows.csv`, `htf_ltf_runner_research_shortlist.csv`, funnel and data-quality artifacts for both profiles. Do not promote live logic until the same rule shows positive average/median net return, acceptable trade count, limited top20 dependency, real flow coverage, and stability across both TF sets or a held-out period.

## 2026-05-27 - P425 runner discovery speed and 5m/30s decay readout

Current commit: UNKNOWN.

Status: P425 APPLIED locally / UNKNOWN commit. The completed `.output/results/htf_ltf_runner_discovery_7d/5m_30s` run took 8571s for 581 symbols and wrote a 596MB candidate CSV because it computed future labels and LTF features for 935824 scanned HTF rows, not only the 2484 HTF anomaly rows. P425 changes the standard discovery path to gate first and write only HTF anomaly candidates while preserving scanned/rejected counts in funnel/run_config.

Research artifacts written under `.output/results/htf_ltf_runner_discovery_7d/5m_30s/runner_decay_research/`. Readout: among strict HTF anomaly rows, only 15/2484 became clean +10% runners within 1h. Current selected trades were profitable on this 7d slice (41 live-filtered closed, WR 56.1%, avg +1.15%, median +0.35%) but did not truly capture runners: only 1/41 had a +10% runner label. Post-close 30s volume-sustain rules show lift but are severely undercovered: only 240/2484 anomalies had post-LTF status ok, and only 72 had 4 closed 30s candles available. Treat volume-sustain as a promising hypothesis, not proof.

Working hypothesis: first-spike HTF flow alone is mostly noise. A better runner candidate waits for early LTF volume/trade sustain after the suspicious HTF candle: no immediate quote-volume decay under 50% candle-to-candle, second-half flow/trade acceleration not collapsing, price acceptance non-negative, and current spike at least comparable to prior 24h spike median. OI was not discriminative in this artifact because runner examples mostly had flat 5m OI change; do not use flat OI as confirmation.

Next: rerun the fixed-profile discovery after P425, then repeat decay research on refreshed `5m_30s` and `1m_5s` artifacts with full cache coverage before changing live logic.

## 2026-05-27 - P424 speed-patch regression cleanup

Current commit: UNKNOWN.

Status: PROPOSED against local P423 workspace. User reported that runtime may have worsened after the first speed patch and asked to inspect the recent patches for dumb regressions.

Finding: P419 replaced a trusted subminute-cache prefilter with a file-presence-only prefilter. That is honest but can be slower on dirty/partial cache because invalid symbols reach the expensive main symbol pass. P420 also made 4 symbol workers the default, which can be slower on Windows/Parquet IO-bound runs due to disk contention and thread overhead. P422 wrote speed-diagnostic artifacts through the same instrumented writer, adding noisy self-referential artifact-write timing.

Patch: restore the early trusted entry-cache prefilter, but make it metadata-only and cached by `(entry_cache_timeframe, entry_timeframe)`. Keep main-pass flow validation unchanged. Default symbol workers back to 1 and leave parallelism opt-in through `--backtest-symbol-workers`. Write speed-diagnostics CSVs without recording their own write cost into the already-built diagnostics frames.

Next: run the same command with default workers first. If `anomaly_speed_summary.csv` shows CPU-bound collection/simulation rather than parquet/read/artifact write, rerun only then with `--backtest-symbol-workers 2` or `4` and compare counts plus runtime.

## 2026-05-27 - P423 responsive Ctrl+C for long backtests

Current commit: UNKNOWN.

Status: PROPOSED against local P422 workspace. The operator reported that anomaly backtest Ctrl+C can look silent and may not exit even after repeated interrupts. The root causes are that `_run_with_logging` only logged KeyboardInterrupt, while ThreadPoolExecutor context-manager shutdown can wait for running pandas/parquet worker threads during interruption.

Patch: install a responsive SIGINT handler in `main.py`: first Ctrl+C prints a visible stop message and raises KeyboardInterrupt; repeated Ctrl+C forces exit code 130 with `os._exit`. The anomaly-lab and HTF/LTF runner thread-pool paths now cancel queued futures and avoid context-manager shutdown waits on KeyboardInterrupt. No candidate logic, execution model, cache validation, fees/slippage, TP/SL, portfolio filtering, or artifacts semantics are changed.

Next: apply P423 and start the same long run. Press Ctrl+C once: it should print a stop message. If parquet/thread cleanup still stalls, press Ctrl+C again: the process should exit immediately with code 130. Treat artifacts from forced-exit runs as partial/incomplete.

## 2026-05-27 - P422 anomaly-lab speed diagnostics

Current commit: UNKNOWN.

Status: PROPOSED against local P421 workspace. User applied P421 but reported runtime still did not improve enough, so the next safe step is instrumentation instead of another blind optimization.

Patch: anomaly-lab now writes detailed speed artifacts for pair runs and root-level multi-timeframe precollection. The diagnostics identify slow stages, slow symbols, parquet-read cost, flow-validation cost, slice/aggregation cost, collector cost, trade-resolution cost, latency-cache cost, and per-CSV artifact write cost. No candidate thresholds, execution timing, fill/stop math, fees, slippage, data-quality gates, or portfolio filters are changed.

Next: rerun the same command and inspect `anomaly_lab_precollection_speed_summary.csv` at the root plus each pair folder's `anomaly_speed_summary.csv` and `anomaly_slowest_symbols.csv`. The next speed patch should target the largest `seconds_sum` bucket, not the guessed bottleneck.

## 2026-05-27 - P421 cached targeted-flow and prepump fast path

Current commit: UNKNOWN.

Status: PROPOSED against local P420 workspace. P420 symbol workers did not improve observed wall time, which suggests the run is dominated by serial targeted-flow cache/materialization and/or artifact context work rather than independent candidate/trade simulation.

Patch: targeted 1s backfill and subminute materialization now use narrow parquet metadata checks to skip already-covered windows without loading full 1s/target frames or rewriting target parquet. Normal `run-anomaly-lab` also defaults the offline runner/fader pre-pump context study to disabled through `--write-prepump-context false`; core candidate/signal/trade/funnel/honesty/timing artifacts remain enabled.

Next: rerun the same command once. Inspect root/pair `targeted_flow_fetch.csv`, `targeted_flow_materialize.csv`, and `anomaly_timing_summary.csv`. Expected fast-path statuses are `exists_covered_requested_window` and `exists_covered_requested_intervals`; if timing is still flat, the remaining bottleneck is likely coarse candidate scan or trade simulation, not cache rewrite.

## 2026-05-27 - P420 symbol-parallel backtest execution

Current commit: UNKNOWN.

Status: P420 PROPOSED against uploaded/P419 workspace. User reported P419 applied locally, but GitHub head was not confirmed as containing it.

Finding: after P419 removed duplicate reads and local O(n²) trailing scans, the next safe bottleneck is serial symbol processing. Candidate collection, independent trade-path resolution, and runner discovery are symbol-independent until the existing portfolio filter step.

Patch boundary: no change to candidate thresholds, future-label separation, entry availability, actual/proxy entry price model, stop/TP math, fees, slippage, skip reasons, or final portfolio filtering. The patch only runs independent symbol work concurrently and merges results in deterministic order before the existing sequential portfolio filter.

Validation: compileall passed for data/exchanges research_tools cli constants.py main.py. Next: run the same small symbol list twice, once with `--backtest-symbol-workers 1` and once with default 4; candidate/signal/trade counts and skip-reason summaries should match while wall-clock drops on multi-symbol cache runs.

## 2026-05-27 - P419 safe backtest hot-path speedup

Current commit: UNKNOWN.

Status: P419 PROPOSED against uploaded workspace. GitHub branch content was reachable, but the exact branch head commit hash was not available from the local zip.

Finding: the backtest had several non-strategy bottlenecks: duplicate subminute parquet scans before the real collection pass, duplicate context enrichment after shared multi-TF precollection, repeated availability-column rebuilding during slicing, and O(n²)-style structural trailing loops that rescanned prior candles for every simulated candle.

Patch boundary: no change to signal thresholds, future labels, entry availability, execution price model, TP/SL rules, fees, slippage, or portfolio overlap rules. The optimization removes redundant reads/recomputations and keeps the same quality checks in the execution path.

Sandbox validation: compileall passed for data/exchanges research_tools cli constants.py main.py, and synthetic old/new smoke comparisons matched for both structural-trailing paths changed by P419. Next: apply P419 locally, then run a small fixed-symbol/fixed-period before/after comparison. Counts in candidates/signals/trades and skip-reason summaries should match; runtime should drop most on full-cache multi-TF and runner-discovery runs.

## 2026-05-27 - P418 compact discovery progress

Current commit: UNKNOWN.

Status: P418 APPLIED locally / UNKNOWN commit. HTF/LTF runner discovery stdout is now compact: each profile updates one progress line with count/percent/current symbol/ETA, then prints one final summary line. Per-symbol details stay in artifacts instead of flooding the terminal.

Next: run the 30d command normally; expected terminal output should be a few profile progress/summary lines, not one line per symbol.

## 2026-05-27 - P417 runner discovery overlap model

Current commit: UNKNOWN.

Status: P417 APPLIED locally / UNKNOWN commit. The HTF/LTF runner discovery live-filter no longer caps total simultaneous positions across different symbols. It only rejects same-symbol overlap while a simulated position on that symbol is still open. This matches the intended research question: do not suppress independent runners on different coins; only prevent stacking repeated entries on the same coin during one active position.

Next: rerun the fixed-profile 30d discovery and compare raw vs live-filtered artifacts. Expect live-filtered counts to be much closer to raw than under any old cap-1 portfolio model.

## 2026-05-27 - P416 fixed TF-set discovery command

Current commit: UNKNOWN.

Status: P416 APPLIED locally / UNKNOWN commit. `run-htf-ltf-runner-discovery` is now a fixed-profile command: the operator passes only `--days`, and the code runs both agreed TF sets, `5m/30s` and `1m/5s`, into separate artifact folders. This removes shell-level tuning/optionality from the research run and makes the tested TF contract explicit.

Next: run `python main.py run-htf-ltf-runner-discovery --days 30`, then compare profile outputs through the root `htf_ltf_runner_discovery_index.csv` plus each profile's shortlist and data-quality artifacts.

## 2026-05-27 - P415 HTF/LTF runner discovery scoring completed

Current commit: UNKNOWN.

Status: P415 APPLIED locally / UNKNOWN commit. The P414 discovery scaffold is now completed with rule-score artifacts for candidate nature and live-filtered trade outcomes. It records HTF-internal LTF distribution/acceleration features, candidate label lift for dormancy/smooth/OI/sustained-flow rule sets, trade winrate by net PnL sign, average/median/sum net return, MFE/MAE, clean-runner label share, and top20 positive-PnL dependency.

Important boundary: `runner_10pct_next_hour` and anomaly-low-break labels remain future labels for research only. They are not used to select entries. Entry replay still waits for closed LTF confirmation, enters at the next LTF open with adverse slippage, uses a structural stop plus structural trailing, and has no TP.

Operational note: use 5m HTF / 1m LTF for broad 30d cache-only discovery when seconds cache is unavailable or too slow. The tool reads Parquet cache only and does not backfill/download exchange candles during the run. Use 1m/5s only after subminute cache coverage is already materialized.

Next: run 30d 5m/1m discovery, inspect `htf_ltf_runner_research_shortlist.csv`, then rerun any promising rule on a different period or with existing 5s cache before changing live2 entry logic.

## 2026-05-27 - P414 live2 stop-recovery crash fix and runner discovery backtest

Current commit: UNKNOWN.

Status: P414 APPLIED locally / UNKNOWN commit. The latest live2 run `.output/results/live2_anomaly_runs/20260526_175528` stopped because `_recover_stop_close_from_user_data()` called missing helper `_same_symbol` while processing a CATI stop fill after TP1 partial and structural trailing. The fix adds explicit symbol matching via Binance market id normalization.

Latest live readout: no trade from this run became a true +10% runner within the next hour by Binance 1m replay. CATI was the only managed partial runner: TP1 hit, stop trailed twice, then private stop fill arrived. UAI was the strongest continuation after entry at about +6.3% in 1h but still below the +10% runner label. Most entries were first-spike/noise or too fragile: early OI-down/exhaustion, seller pressure, high stall, or structural low/stop break before any large continuation.

New research tool: `run-htf-ltf-runner-discovery` / `research_tools.htf_ltf_runner_discovery` scans HTF anomalies, labels next-hour +10% runners and anomaly-low breaks separately from entry, waits for closed LTF confirmation, enters only at the next LTF open, then simulates structural SL plus structural trailing with no TP. It writes candidate/signal/trade/funnel/top-dependency/data-quality/honesty artifacts.

Next: run the discovery on a wider explicit universe with real 5s flow and OI coverage, then select candidate filters by clean +10% labels and live-filtered expectancy, not by raw PnL alone.

## 2026-05-26 - P413 live2 multi-position and stop PnL fix

Current commit: UNKNOWN.

Status: P413 APPLIED locally / UNKNOWN commit. Live2 now treats `execution_max_open_positions=0` as unlimited and uses it by default, while still rejecting duplicate live2 positions on the same symbol. The operator grid displays realized PnL plus final/early/SL/BE/TP counters from the position supervisor. Stop-trigger final closes recover realized PnL from private user-data order events when possible; if no stop fill event is available, Telegram shows `PNL: n/a`, not `+0 USDT`.

Safety note: this removes the one-position portfolio cap. Actual exposure is now controlled by order notional, signal frequency, exchange margin, duplicate-symbol protection, and verified stop lifecycle. If a hard cap is needed again, start live2 with positive `--execution-max-open-positions`.

Validation: focused live2 tests passed (34 passed); compileall passed for data/exchanges research_tools cli constants.py main.py.

Next: after restart, verify `live2_events.csv` contains user-data stop fills around any `position_final_close_verified` stop event and that Telegram/grid PnL agrees with those fills.

## 2026-05-26 - P412 runner shape gate applied to rolling runner categories

Current commit: UNKNOWN.

Status: P412 APPLIED locally / UNKNOWN commit. Live2 and the backtest category profile now require `runner_oi_confirmed`, `runner_flow`, and `runner_balanced` to show synchronous rolling-shape confirmation: quote volume, real trade count, and range expansion must be present across the full 12 closed 5s setup, the second 30s must accelerate versus the first 30s, the second-half return must be non-negative, and top1 quote-volume share must stay below the single-print cap.

Why: the 20260526_120454 live audit showed that good candidates more often look like dormancy followed by coordinated range/volume/trades acceleration, not just one large quote-volume candle. This patch intentionally reduces trade count and should be judged by reject distribution plus missed-runner audit, not by one live session.

Validation: focused live2 tests passed (32 passed); compileall passed for data/exchanges research_tools cli constants.py main.py.

Next: run a rolling 1m/5s profile backtest with category profiles and compare filtered rejects against later top movers. If too many delayed runners are missed, the next change should be an explicit delayed-acceptance candidate state, not loosening quote-volume alone.

## 2026-05-26 - P411 current OI endpoint is wired into live2 poller

Current commit: UNKNOWN.

Status: P411 APPLIED locally / UNKNOWN commit. Live2 OI polling now fetches Binance current OI via `/fapi/v1/openInterest` on every symbol poll and stores it as separate `current_oi_*` state/artifact fields. The existing 5m OI history remains the baseline for 3x5m context and is not treated as the current point.

Validation: focused live2 tests passed (30 passed); compileall passed for available project paths.

Risk/limitation: current OI is REST-polled, so it is fresher than 5m candles but not tick-level. Interpret exact OI timing through `current_oi_last_seen_ms` and `current_oi_timestamp_ms`.

## 2026-05-26 - live2 run 20260526_120454 trade readout

Current commit: UNKNOWN.

Analyzed `.output/results/live2_anomaly_runs/20260526_120454` for HIGH, VVV, FF, OPG, NAORIS, AZTEC, BLUAI, IN. Executed/closed positions were BLUAI, AZTEC, NAORIS, OPG, FF, VVV twice, HIGH. IN had deadline/reject rows but no artifact-confirmed live2 position in this run.

Key result: several names were not dead immediately after early exit. Binance 1m replay after live entries shows post-entry/post-exit upside potential in AZTEC, OPG, VVV, BLUAI, and FF. NAORIS looks like noise. HIGH was closer to TP-like movement than a clean runner and is ambiguous.

Hypothesis update: the stronger delayed/runners tend to show a long quiet baseline followed by simultaneous acceleration in quote volume, trade count, and range. In this sample, OPG and FF strongly match this shape; VVV second entry partly matches; BLUAI has flow/range expansion but weaker cleanliness; AZTEC ran later despite only moderate pre-entry acceleration. This supports testing stricter entry confirmation and/or delayed confirmation, but not as proof yet.

Next: derive a small replay rule from this run and prior artifacts: require all three dimensions to accelerate together versus a quiet baseline, then enter only after acceptance rather than first spike. Avoid tightening only quote-volume because that would keep some noisy names and miss the nature distinction.

## 2026-05-26 - P410 ticker current-OI signature boundary

Current commit: UNKNOWN.

P410 proposed after the first post-P409 live2 start failed during startup ticker snapshot with `SymbolState.update_ticker() got an unexpected keyword argument 'current_fetched_at_ms'`. Root cause: the store-level update_ticker boundary accepted current-OI fields but the per-symbol SymbolState.update_ticker method did not. The fix completes the same typed boundary and persists current-OI first-ok/pump-start baseline when ticker startup/current-OI path provides it.

Next validation: apply P410, run compileall, then restart live2 and confirm startup ticker snapshot passes and the next status reaches warmup/market-data stages.

## 2026-05-26 - P409 current-OI baseline separation

Current commit: UNKNOWN.

P409 proposed after P408 revealed that `entry_current_oi` answers only "did OI fall after our fill?", not "did OI fall from the pump awakening?" The live2 contract now keeps three separate current-OI baselines: first valid active/radar snapshot (`pump_start_current_oi_*`), selected signal snapshot (`signal_current_oi_*`), and post-fill protected-entry snapshot (`entry_current_oi_*`). Early-exit artifacts expose deltas from all three. Default OI-down threshold is 0.3% to avoid treating tiny endpoint noise as thesis failure.

Next validation: run one small live2 forward session and inspect `position_early_exit_full_close_verified` / protected-position payloads for `pump_start_current_oi_*`, `signal_current_oi_*`, `entry_current_oi_*`, and the three post-baseline change fields. Confirm the pump-start timestamp is not later than selected-signal timestamp for normal radar-covered entries; if it is later/missing, treat pump-start OI conclusion as unavailable for that trade.

# Anomaly Research State

## 2026-05-26 - P413/P414 live2 multi-position and truthful stop PnL

Current commit: UNKNOWN.

Status: PROPOSED against GitHub head / uploaded workspace.

The live2 execution cap now treats `execution_max_open_positions=0` as unlimited, keeps the existing same-symbol protected-position reject, and exposes `max_open_positions_unlimited` to diagnostics. The CLI also has `--execution-max-open-positions`, defaulting to unlimited.

The supervisor now counts all final close paths, early exits, stop/breakeven buckets and realized PnL deltas for the grid. Stop-trigger final closes recover realized PnL from private user-data events when available; Telegram uses top-level action PnL and prints `PNL: n/a` for unrecovered stop closes instead of showing fake zero from the closed position snapshot.

Next: restart live2 and verify in `live_events.csv` that simultaneous different-symbol protected positions can coexist, while duplicate entries on the same symbol are still rejected; then inspect one TP/early/SL close in the grid and Telegram.

## 2026-05-26 - P409 live2 current-OI entry baseline completed

Current commit: 7611fbb9d43368dbc82b6ae3c5fb4dd7c883406e on GitHub branch `codex/pno-anomaly-continuation-lab`; patch status: PROPOSED against uploaded workspace.

Finding: the previous P408 current-OI work was incomplete. The OI poller passed `current_*` fields into `SymbolStateStore.update_open_interest`, but the store method did not accept them, so the poller could fail with `TypeError` instead of refreshing current OI. The execution engine also persisted signal-time current OI into the protected position, while the supervisor's OI-down exit compared 5m historical OI against the entry 5m snapshot, not current OI against an actual entry snapshot.

Patch: P409 fixes the state-store boundary, fetches a current-OI snapshot after entry fill and verified initial stop, stores that snapshot in `Live2ProtectedPosition`, and makes the early-exit OI-down branch compare later current-OI snapshots against the protected entry current-OI snapshot. The old 5m OI fields remain artifact context only for this branch.

Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py` passed. Focused smoke verified protected-position entry current OI capture and state-store current OI update. Literal AGENTS compile command with `launcher.py` cannot be used because `launcher.py` is absent in this workspace.

Residual risk: current OI is a polled Binance USD-M endpoint, not tick-level OI. The snapshot is taken after verified stop protection to avoid delaying stop placement, so it is an entry-protection-time baseline, not a pre-stop blocking guard.

## 2026-05-26 - live2 TP1 partial runner, OI monitor, source-flow velocity

Current commit: UNKNOWN.

Status: P408 APPLIED locally / UNKNOWN commit. Live2 now defaults to 12 USDT notional and TP1 closes 50% of the exchange position instead of flattening it. The remaining position stays protected only after a replacement stop for the remaining exchange amount is submitted, verified visible, and the old stop is cancelled/verified gone.

Position context: protected positions now persist the selected category, selected source-flow velocity (`selected_source_flow_*`) and the entry-time 5m OI snapshot. The supervisor compares current OI against that entry/anomaly snapshot and includes both in early-exit artifacts. Source-flow speed is artifacted for runner and post-HTF acceptance categories.

Management: early exit still uses only closed post-fill 5s candles. It can now also exit after MFE when OI falls from the entry 5m snapshot and buyer flow is exhausted, and it can structurally trail the stop on the post-TP1 remainder using recent closed post-fill 5s lows.

Risk/limitation: OI is still Binance 5m open-interest history, not per-second Coinglass/tick OI. It is useful as a coarse context/exit factor, not proof of liquidation mechanics. Forward live artifacts must be analyzed before treating partial TP/trailing as expectancy-positive.

Validation: focused live2 tests passed (29 passed); compileall passed for available project paths.

## 2026-05-26 - live2 runner categories moved to rolling setup

Current commit: UNKNOWN.

Status: P407 APPLIED locally / UNKNOWN commit. Answer to the open question: no, the old live2 runner categories were not rolling. They used `_live_backtest_like_setup` with `floor(decision_open_ms, 1m)` and therefore depended on calendar-minute boundaries.

Patch: `_live_backtest_like_setup` now uses the trailing contiguous 12 closed 5s candles ending at the decision candle as a rolling 60s setup. This applies to `runner_oi_confirmed`, `runner_flow`, and `runner_balanced`. `post_htf_acceptance_long` was already rolling after P405 and remains separate.

Artifact contract: runner near-misses/deadline rows now expose `live_setup_alignment=rolling_60s_5s_step`, `live_setup_calendar_aligned=false`, `live_setup_setup_open_ms`, and `live_setup_setup_close_ms`.

Risk/limitation: this improves live behavior but breaks direct comparability with old calendar-minute forming runner backtests. Any edge claim for runner categories now needs a rolling replay/backtest or forward live analysis under the new artifact markers.

Validation: focused live2 tests passed (28 passed); compileall passed for available project paths.

## 2026-05-26 - live2 run readout and early-exit patch

Current commit: UNKNOWN.

Status: ANALYZED + P406 APPLIED locally / UNKNOWN commit. The run `.output/results/live2_anomaly_runs/post_htf_rolling_20260526_090002` produced two artifact-confirmed live2 positions: BAS and CGPT. SKYAI appears in rejects/dependency events, but this run does not prove a live2-managed SKYAI trade.

Important interpretation: BAS and CGPT were selected by legacy/live runner categories, not by `post_htf_acceptance_long`. Therefore they should not be used as proof that the new rolling post-HTF category enters too late. They do show the broader live2 failure mode: CGPT entered after a strong 5s push with weak HTF anomaly acceptance, then sat for about 97 minutes and closed near flat before fees.

Runtime/data risk: this run had severe artifact and scheduling pressure (`live2_events.csv` about 4.6 GB, `live2_near_misses.csv` about 5.1 GB, many deadline/backlog events). Some "late entry" symptoms may be runtime lag/artifact pressure, not only strategy timing.

Patch response: P406 adds a conservative post-entry early-exit supervisor based on closed post-fill 5s candles, actual fill, verified flat position, and verified old-stop cancellation. It watches for buyer-flow exhaustion, seller pressure, and OI-up/no-progress patterns. It does not change entry timing or category selection.

Next: run a forward live smoke with P406 and inspect only `position_early_exit_full_close_verified` versus TP1/final closes. Separately, reduce artifact spam/deadline pressure before optimizing rolling length. If earlier pump capture is still desired, test rolling30/45 in shadow/backtest first, not as an immediate live default.

## 2026-05-26 - live2 rolling post-HTF acceptance update

Current commit: UNKNOWN.

Status: P405 APPLIED locally / UNKNOWN commit. Follow-up to P404: post-HTF acceptance no longer depends on calendar 1m boundaries. HTF is now a rolling 60s window built from the 12 closed 5s candles immediately before the 6 closed 5s confirmation window.

Startup data policy: do not load 75m of universal 5s/aggTrades. live2 loads a lightweight 75m HTF baseline from raw Binance 1m klines, preserving real quote volume and number_of_trades; the existing aggTrade warmup remains short/default 15m. This gives immediate HTF baseline without pretending we have full rolling 5s history for the whole market.

New guard: `post_htf_acceptance_long` rejects long entries when OI context is ok, OI change over 3x5m is positive, and 15m price context is negative (`oi_up_price_down_blocked`). This targets the screenshot pattern: price under pressure while OI climbs.

Risk/limitation: rolling HTF uses real 5s flow; baseline is real-flow raw 1m kline baseline, not rolling 5s baseline. Artifacts expose `post_htf_acceptance_htf_alignment=rolling_60s_5s_step` and `post_htf_acceptance_htf_calendar_aligned=false`.

Validation: compileall passed for available paths; focused live2 tests passed (25 passed).

## 2026-05-26 - live2 post-HTF acceptance long enabled

Current commit: UNKNOWN.

Status: P404 APPLIED locally / UNKNOWN commit. The researched long category `post_htf_acceptance_long` is now part of the default live2 category contract and can trade through the existing live2 execution pipeline when runtime readiness and entry guards allow new entries.

Contract: closed 1m HTF anomaly first; no decision inside that HTF candle; exactly 6 closed 5s candles after HTF close; ltf6 return >= 0.5%; structural risk from signal close to closed HTF anomaly low-buffered stop in [1.5%, 5.0%]; ltf6 last3 quote share <= 50%; ltf6 top1 quote share <= 75%; prior_spike_count_24h <= 3. TP1 is 1.5R from the live signal price and structural stop.

Artifact separation: selected events carry `category_id=post_htf_acceptance_long`, `post_htf_acceptance_artifact_mode=post_htf_acceptance_long`, and detailed `post_htf_acceptance_*` fields in `live2_events.csv` data_json. Non-selected post-actionable cases expose the same key fields in `live2_near_misses.csv`.

Validation: compileall passed for available project paths; focused live2 tests passed (23 passed). `launcher.py` is absent in this workspace.

Residual risk: this is live forward validation, not edge proof. The live prior context is 24h while the discovery label used legacy 72h naming; artifacts include `post_htf_acceptance_prior_context_parity_note`. First run should be analyzed by category and artifact marker before trusting expectancy.

## 2026-05-25 - post-HTF fader/short research state

Current commit: UNKNOWN.

Status: UPDATED ANALYSIS. The first signal-row inversion test was methodologically dirty and not sufficient. A cleaner bare-HTF anomaly research pass collapsed the run to 167 unique closed HTF anomalies and ignored long categories as classes. Result: 81/167 reached >=2% post-close short MFE; 49/167 were clean with adverse_up_before_short_low <=1.5%.

Finding: the short/fader effect is concentrated in prior crowding/fade plus LTF short-pressure confirmation. Best small probes are prior_fast_fade>=3 or prior_spike>=10 combined with failed_new_high/taker_fade_red, using RR2.0-2.5. Best cap-1 variants showed about +20% over 9-12 closed trades, but top-dependency is high because the sample is small.

Exit check: focused partial exits did not help the best probes. Full RR2.5 beat 50% partial at 1R and 1.5R, with or without BE, because the few clean winners need room.

Conclusion: do not enable live shorts yet. This is now a plausible research candidate, not proof. Next validation must rerun the bare HTF fader contract on a larger period/universe and compare against forming long/live-priority results.

Planned implementation: write a separate honest bare-HTF short/fader backtest path with closed HTF anomaly detection, post-close LTF feature windows within 60 minutes, trigger-based next-LTF-open short simulation, structural stop, RR2.0-2.5 variants, cap-1 live filter, skip reasons, top-dependency and distribution artifacts. Long categories may appear only as diagnostics, not as short entry classes.

Implementation update: P398 is APPLIED locally / UNKNOWN commit. `--pair-collection-mode bare_htf_short_fader` now writes the separate short/fader artifact set and keeps long categories out of short entry selection. Smoke validation passed on INJ/BEAT plumbing, but edge is still unproven and requires a larger run.

Discovery update: P399 is APPLIED locally / UNKNOWN commit. The short/fader mode is now wide discovery by default: prior crowding/fade is artifact annotation, not a required signal gate, and RR exit grid is off unless explicitly enabled. Next step is a larger discovery run, then category extraction by decay/fader nature from artifacts and cached candles.

Expanded discovery update: P400 is APPLIED locally / UNKNOWN commit. The short/fader mode now includes seven default post-close trigger types, compact post-close LTF path slices, and heuristic decay-category artifacts. These categories are research triage labels only; final categories still need to be derived from a larger run.

Analysis tooling update: P401 is APPLIED locally / UNKNOWN commit. Added `research_tools.short_fader_category_analysis` to score category/rule candidates from an existing wide discovery run. It is in-sample artifact analysis only; promising rules need strict replay after selection.

## 2026-05-25 - P395 post-HTF forward LTF confirmation mode

Current commit: UNKNOWN.

Status: APPLIED locally / UNKNOWN commit. Added `post_htf_close_ltf_forward_confirmation`: closed HTF N must show setup interest, then LTF confirmation is evaluated only after HTF N close, inside the following HTF window. Entry remains tied to the forward LTF decision and cannot occur inside N.

Expected impact: tests the conservative "HTF noticed first, LTF confirms after" hypothesis without mixing it with forming/live2 early-entry logic. It should reduce stale/retro-fill optimism versus using LTF inside N as if it were an entry trigger.

Validation: compileall passed; focused collector tests for P394/P395 passed.

Next validation: run one small explicit pair first, then inspect candidates for `feature_contract=post_htf_close_ltf_forward_confirmation_v1`, `candidate_collection_policy=all_ltf_forward_confirmations_after_post_htf_close`, and non-empty forward confirmation fields before interpreting PnL.

## 2026-05-25 - P394 post-HTF LTF left-context parity

Current commit: UNKNOWN.

Status: APPLIED locally / UNKNOWN commit. Follow-up to P393: `post_htf_close_ltf_confirmation` now requires and uses LTF candles to the left of the anomalous HTF candle, without moving the decision/fill back into the past. The decision remains available at HTF close, simulated market entry remains no earlier than that close, and left LTF context is used for the prior-whipsaw filter in this mode.

Expected impact: closer live/backtest parity for post-HTF-close continuation tests, because live would already have the left LTF tape by the time the HTF close decision is made. Results may become stricter and targeted backfill heavier because full left LTF context is now required.

Validation: compileall passed and the focused new post-HTF left-context test passed. Full anomaly continuation test file still exposes two forming-mode baseline failures in current HEAD: tests expect entry-derived baseline, while the code uses setup-timeframe baseline.

Next validation: resolve the forming-mode baseline contract honestly, then run a small `5m/1m --pair-collection-mode post_htf_close_ltf_confirmation` slice and inspect `targeted_flow_plan.csv` window_before_ms plus candidate left-context columns.

## 2026-05-24 - P393 post-HTF-close LTF confirmation mode

Current commit: UNKNOWN.

Status: PROPOSED against the uploaded ZIP / GitHub commit still UNKNOWN. Adds `--pair-collection-mode post_htf_close_ltf_confirmation` for pair runs. The mode selects a closed HTF setup candle, uses the complete LTF segment inside that candle only after HTF close as confirmation, and forces simulated entry to occur no earlier than the HTF close through the existing next-entry-candle execution model.

Expected impact: provides a cheap honest alternative to early forming LTF backtests for `5m/1m` and similar pairs without requiring universal second-level data. It intentionally does not claim the old early `5s/15s/30s` edge.

Validation: compileall passed for `data/exchanges data/fetchers research_tools cli constants.py main.py`. Synthetic smoke confirmed post-close decision availability equals the no-earlier-than-entry timestamp and the simulated entry is after the HTF close.

Next test: run a 5m/1m post-close backtest with full 1m cache and `--targeted-flow-backfill false`, then compare live-filtered results against 1m-only and 5m-only runs.

## 2026-05-23 - P392 wide simulation plus live-like portfolio filter

Current commit: UNKNOWN.

Status: APPLIED locally / UNKNOWN commit. Anomaly-lab keeps raw `max_open_positions=1000` simulation for category discovery, then writes a separate final live-like cap-1 portfolio filter. New artifacts include `anomaly_trades_live_filtered.csv`, live-filtered profitability summaries/by-category/by-family/by-symbol, skip reasons, edge health, and `anomaly_live_portfolio_filter_summary.csv`.

Expected impact: raw artifacts keep all category material; live-parity conclusions use only the live-filtered artifacts. This patch does not change signal thresholds, categories, TP/SL, fill model, or live2 execution.

Next validation: compileall, focused anomaly/live2 tests, then a reused-candidate 1m/5s replay to read live-filtered live-priority category metrics before deciding whether any category thresholds should change.

Validation update: compileall and focused tests passed. Reused-candidate 1m/5s replay wrote core live-filter artifacts at `.output/results/anomaly_lab/live_parity_1m_5s_p392`: raw 1120 closed -> live-filtered 45 closed, live_priority 17 closed / +20.37% sum_net, discovery 28 closed / -3.94%. The replay timed out after core artifacts were written, before late chart/status completion; render_charts=false, so the core category readout is usable.

## 2026-05-23 - latest anomaly_lab readout state

Current commit: UNKNOWN.

Status: ANALYZED. Latest `.output/results/anomaly_lab` multi-TF artifacts are research-valid but not live2 PnL proof. All three TF sets have 28/28 honesty-report nodes with 0 failures, real aggTrade-derived subminute flow labels, and no observed as-of timestamp violations. The remaining hard limitations are cache-snapshot universe survivorship risk and `max_open_positions=1000`.

Finding: 1m/5s is the strongest surface. Non-Asia keeps almost all 1m/5s profit with fewer trades and much lower worst-day damage. Live-priority categories are cleaner than discovery. Discovery remains a dirty search bucket: useful for finding category candidates, not for live default trading.

Parity note: live2 is closest to the 1m/5s contract only. Current live2 execution uses max_open_positions=1, while the analyzed backtest run uses 1000, and 1m/15s plus 5m/30s are research TF sets rather than proven live2 paths. Current code/run also conflict with the older P385 state text that anomaly-lab default is max_open_positions=1; constants.py/run_config.csv show 1000.

Next: run a cheap 1m/5s live-like replay with `max_open_positions=1` and explicit/as-of symbol universe where possible, then compare all vs non-Asia vs live_priority_non_asia before changing thresholds.

## 2026-05-22 - P385 backtest honesty hardening

Current commit: UNKNOWN.

Status: APPLIED locally. The backtest is now stricter on the remaining non-lookahead optimism gaps: default anomaly-lab execution is live-like `max_open_positions=1`, adverse entry/exit slippage is applied by default, and portfolio overlap is checked at the actual simulated entry timestamp rather than at decision time.

New artifacts: every anomaly-lab run writes `anomaly_backtest_honesty_report.csv` with 28 checklist nodes covering CLI/config, universe scope, OHLCV/flow provenance, candidate collectors, baseline/flow/future-label boundaries, prior/OI/derivatives context, signal builder, entry/guards/stop/TP/exit, overlap/portfolio, fees/slippage, grid bias, precollect/backfill, and summaries.

Validation: 26 anomaly continuation tests passed. Existing-code compile passed with `python -m compileall -q data\exchanges research_tools cli constants.py main.py`; `launcher.py` is absent in this workspace, so the literal AGENTS command including launcher.py cannot be used here. Compact INJ/BEAT 2-day closed-1m run completed at `.output/results/anomaly_lab/lookahead_honesty_p385_closed_1m`: 23 candidates, 5 signals, 5 closed, 0 skipped, avg_net 0.8786%, sum_net 4.3930%, win_rate 80.00%. All 28 honesty-report nodes were `ok` with 0 failures.

Residual risk: this is still candle-level execution, not order-book/tick replay. Slippage is a conservative fixed proxy. Tiny INJ/BEAT validation is a correctness smoke, not edge evidence.

## 2026-05-22 - P384 backtest lookahead audit closure

Current commit: UNKNOWN.

Status: APPLIED locally. P374-P383 were reviewed against the current execution path. The main availability guards are real: reused candidates are config/window scoped, historical cache-snapshot universe scans are blocked without explicit opt-in, OHLCV/aggTrade/OI/derivatives rows now have as-of semantics, and future outcome labels are artifact-only.

New fixes: market-entry simulation now includes the entry candle and rejects delayed fills after TP1 was already reached; forming HTF setup availability is the LTF decision close, with full HTF close recorded separately; chart/prepump context uses only rows available at decision/anchor; entry-grid signals are rebuilt after derivatives enrichment; closed-TF candidates now carry the flow-hold/prior-context fields required by category replay.

Validation: 23 anomaly continuation tests passed. Compact INJ/BEAT 2-day closed-1m anomaly-lab run completed at `.output/results/anomaly_lab/lookahead_audit_p384_closed_1m`: 23 candidates, 5 signals, 5 closed trades. Artifact checks confirmed decision/setup availability ordering, OI/mark/premium/funding/long-short as-of <= decision availability where present, and all trades have `post_entry_simulation_includes_entry_candle=True`.

Residual risk: this compact run was not an edge test and used closed 1m, not the live 1m/5s path because local INJ/BEAT 1s/5s caches are old pre-P378 versions. Funding context still shows explicit `missing_available_timestamp` for old INJ funding cache rows; those rows are refused rather than consumed.

## 2026-05-22 - P379 closed candidate collector audit state

Current commit: UNKNOWN.

P379 proposed after p.6 lookahead audit. The closed setup candidate collector no longer requires a complete future label horizon for a candidate row to exist. Candidate construction now only depends on data available through the decision candle. Future labels remain artifact-only and are marked `unlabeled_insufficient_future` when the right-edge future window is incomplete.

Next validation: apply P379 after P378, run compileall, then run a small closed-TF backtest near an explicit `--end-timestamp-ms`. Check that candidates near the right edge appear with `future_label_status=insufficient_future_window` instead of being silently dropped.

## 2026-05-22 - P383 derivatives context availability guard

Current commit: UNKNOWN.

P383 proposed after p.13 audit. Critical finding: derivatives context rows were selected with period timestamp semantics. Premium/mark klines could be saved while still forming, and long-short/taker rows had no explicit `available_timestamp_ms`; downstream enrichment used a lag approximation over `timestamp` instead of a cache-level known-time contract.

Change: derivatives context caches now store `available_timestamp_ms`. Premium/mark rows derive it from raw Binance close time + 1ms and are dropped until available. Ratio rows get conservative period-lag availability, funding uses fundingTime. Backtest context enrichment now rejects legacy context caches without availability metadata and selects rows using `available_timestamp_ms <= decision_available_timestamp_ms`.

Next validation: rebuild derivatives context caches for a small explicit symbol set, run a small red-flag/profile backtest, and confirm market_context_status has no `missing_available_timestamp` rows and boundary decisions select the previous available context row.

## 2026-05-22 - P382 OI context availability guard

Current commit: UNKNOWN.

P382 proposed after p.12 audit. Critical finding: OI enrichment used the last cached 5m OI row with `timestamp <= decision_timestamp_ms`, but cached OI rows had no explicit publication/availability timestamp. If the exchange/cache timestamp is a period timestamp rather than known-time, OI-confirmed categories can consume a value from the still-unavailable current 5m OI period.

Change: OI context is now selected by availability timestamp, defaulting to `timestamp + 5m`, and compared against `decision_available_timestamp_ms` when present. Artifacts now expose `oi_available_timestamp_ms`, `oi_asof_timestamp_ms`, and available cache coverage.

Next validation: run a small backtest with `runner_oi_confirmed` categories and confirm OI rows near 5m boundaries shift back to the last available completed OI period instead of the current period timestamp.

## 2026-05-22 - P381 flow-ratio source trust gate

Current commit: UNKNOWN.

P381 proposed after p.9 audit. Critical finding: after P378, the code could still consume old subminute/aggTrade-derived caches by merely labeling them `missing_version`/`unknown_version`, and pair/forming candidate provenance still labeled setup/levels flow as generic cached OHLCV even when the forming HTF candle was built from entry/LTF flow.

Change: subminute entry flow now requires trusted P378 cache versions. Materialization from 1s refuses old/missing-version 1s caches. Signal building refuses candidates with missing/unknown/proxy flow source labels, and pair/forming candidates now label setup/levels flow from the actual entry-flow source. The trusted direct 1s aggTrade version is `p378_latency_aggtrades_full_buckets_v1`, matching P378.

Next validation: regenerate affected 1s and materialized subminute caches, then run a small pair-mode backtest and confirm no `untrusted_*_entry_flow_cache` rows remain in candidate artifacts.

## 2026-05-22 - P380 pair/forming collector audit state

Current commit: UNKNOWN.

P380 proposed after p.7 lookahead audit. The HTF/LTF pair/forming candidate collector no longer requires a complete future label horizon for a candidate row to exist. Candidate construction now only depends on closed entry candles through decision_ts and historical baseline rows. Future outcome fields remain artifact-only and are marked `unlabeled_insufficient_future` when the right-edge future window is incomplete.

Next validation: apply P380 after P379, run compileall, then run a small pair-mode backtest near an explicit `--end-timestamp-ms`. Check that right-edge candidates appear with `future_label_status=insufficient_future_window` instead of being silently dropped.

## 2026-05-22 - P378 aggTrade/event cache full-bucket guard

```text
Current patch status: P378 PROPOSED / UNKNOWN commit.
Question: audit p.5 aggTrade/event-derived cache for critical lookahead/data leakage only and patch if needed.
Finding: aggTrade REST/event windows can start or end at arbitrary milliseconds. The old aggregation accepted buckets by `bucket_timestamp <= end_timestamp_ms`, so a final bucket could contain only the early part of a second/5s window while being saved as a closed OHLCV/flow candle. Targeted event backfills are especially exposed because their edges are not guaranteed to align with 1s/5s boundaries.
Change: aggTrade-to-OHLCV aggregation now requires full source-window coverage for every emitted bucket and records candle availability plus aggTrade coverage metadata. 1s->subminute materialization uses that metadata and drops derived buckets whose complete interval is not covered. Aggregation version strings were bumped for both direct 1s backfill and materialized subminute caches.
Residual risk: existing caches generated before P378 may already contain partial edge buckets and are not cleaned automatically by this patch. Regenerate affected 1s cache and derived subminute caches before making parity/edge claims from aggTrade data. This does not audit OI/derivatives publication lag.
Validation: python -m compileall -q data/exchanges research_tools cli constants.py main.py, plus direct checks that a full 1s bucket is kept and an edge-partial 1s bucket is dropped.
```

## 2026-05-22 - P377 OHLCV fetch normalization availability guard

```text
Current patch status: P377 PROPOSED / UNKNOWN commit.
Question: audit p.4 Binance OHLCV normalization for critical lookahead/data leakage only and patch if needed.
Finding: Binance klines and generic CCXT OHLCV fetches can include a still-forming candle when the fetch end is current time, or include a candle whose open time is <= historical end_timestamp_ms even though its final high/low/close/volume/quote_volume/number_of_trades/taker_buy values were not available at that as-of cutoff. Saving that row poisons the cache; later runs may treat partial flow as final.
Change: Binance normalization now drops rows using raw kline close_time + 1 > min(end_timestamp_ms, fetch_time_ms). The final fetch frame also enforces timestamp + timeframe_ms <= availability cutoff. M10 aggregation from M5 data applies the same cutoff to avoid partial target buckets.
Residual risk: this does not solve historical exchange publication lag beyond candle close, and does not audit OI/derivatives context. Existing caches that already contain partial rows need refetch/overwrite for affected newest candles if they were produced before P377.
Validation: python -m compileall -q data/exchanges research_tools cli constants.py main.py, plus direct Binance kline normalizer check that drops a row not closed by the cutoff. Uploaded zip does not contain launcher.py.
```

## 2026-05-22 - P376 OHLCV cache availability guard

```text
Current patch status: P376 PROPOSED / UNKNOWN commit.
Question: audit p.3 OHLCV cache loader for critical lookahead only and patch if needed.
Finding: cached OHLCV rows use exchange candle open time in `timestamp`. The row's high/low/close/volume/quote_volume/number_of_trades are only fully known after candle close. The previous backtest window slicing used `timestamp <= end_timestamp_ms`, so an explicit as-of cutoff inside a candle could include data from a candle that was not closed yet.
Change: attach explicit availability columns to ParquetStorage OHLCV loads; slice closed and pair candidate input windows by `available_timestamp_ms <= end_timestamp_ms`; propagate setup/decision availability timestamps into candidates. Lower-TF aggregation also carries availability timestamps and excludes partial target buckets at the end boundary.
Residual risk: this is not a full exchange publication-lag model and does not fix OI/derivatives context availability; those are later audit nodes. Historical all-cache runs with no explicit cutoff remain final-data backtests, now with clearer timestamp semantics.
Validation: python -m compileall -q data/exchanges research_tools cli constants.py main.py. Uploaded zip does not contain launcher.py.
```

## 2026-05-22 - P375 universe/symbol-scope guard

```text
Current patch status: P375 PROPOSED / UNKNOWN commit.
Question: audit p.2 universe/symbol selection for critical backtest lookahead/survivorship only and patch if needed.
Finding: the critical p.2 issue is not per-candle future data; it is universe contamination. A historical run with --end-timestamp-ms and no explicit --symbols scanned the current local cache snapshot, which is not an as-of historical listing universe. Reused candidates also had no symbol-scope contract, so a subset-universe candidate CSV could be silently treated as the current requested universe.
Change: run-anomaly-lab now blocks historical cache-snapshot universe unless explicitly overridden with --allow-cache-snapshot-universe true. Backtest outputs record universe_symbol_scope, normalized requested symbols, survivorship_bias_risk and historical_listing_snapshot_available=false in run_config.csv plus anomaly_universe_contract.csv. Reused candidates must now prove the same universe scope/symbol set. Explicit symbol filtering is normalized across closed/pair collectors and coverage artifacts.
Residual risk: this does not create a true historical listing universe. For a clean all-market historical test, provide an explicit as-of universe/symbol snapshot or accept the override as cache-snapshot biased.
Validation: python -m compileall -q data/exchanges research_tools cli constants.py main.py. Uploaded zip does not contain launcher.py.
```

## 2026-05-22 - P374 reused candidate lookahead guard

```text
Current patch status: P374 PROPOSED / UNKNOWN commit.
Question: audit p.1 CLI/config for critical backtest lookahead only and fix what is critical.
Finding: the critical p.1 issue is --reuse-candidates-dir. It could load anomaly_candidates.csv from a previous run without verifying the candidate collection contract, symbol scope, timeframe pair or end timestamp. That lets a run that claims one window/config consume candidates collected under another, including candidates from after the requested end timestamp.
Change: reused candidates now require sibling run_config.csv, exact match of critical collection config, required audit columns, valid decision timestamps, and row-level filtering to current timeframe/contract/window/symbols.
Residual risk: this does not address non-critical p.1 issues such as in-sample grid selection or execution realism; those belong to later nodes. Reuse artifacts from older runs without run_config.csv must be regenerated.
Validation: python -m compileall data/exchanges research_tools cli constants.py main.py. Uploaded zip does not contain launcher.py.
```

## 2026-05-22 - P373 live2 trade parity and targeted event cache

```text
Current patch status: P373 APPLIED locally / UNKNOWN commit.
Question: compare live2 trades INJ (run 20260521_164122) and BEAT (run 20260521_194030) with same-period category-only backtests, avoid a full 1s cache, and fix any live2/backtest choke or display bugs.

Finding: the old comparison was not trustworthy. BEAT was rejected live at 2026-05-21T22:11:30Z only by mark_basis_below_category_min, then selected later at 2026-05-21T22:11:47Z after mark WS basis improved. Historical backtest cannot reproduce that tick-level mark basis from closed mark klines without lookahead or stale under-entry, so mark basis was an unfair trading gate. INJ was also missed/shifted by mixing live aggTrade-derived entry counts with raw kline setup trade-count baselines in the backtest.

Change: use targeted aggTrade windows around interesting live events, not a full-second cache; fix bounded aggTrade pagination; add availability-aware derivatives context with 1m mark as-of timestamps; remove mark basis from live-priority category blockers while keeping diagnostics; compute prior spike/fade context from closed cached 5m candles like live2; compute setup quote/trade baselines from the same 5s aggTrade cache in 1m/5s pair mode. Live2 grid active/trading rows and stop-trigger settle handling were fixed at the same time.

Result after targeted backfill/materialized 5s and fixed4 backtests: runner_oi_confirmed sees both INJ and BEAT. INJ appears at decision 2026-05-21T17:36:30Z / entry 17:36:35, while live entered later at 17:36:42.752Z fill 5.211. BEAT appears at decision 2026-05-21T22:11:30Z / entry 22:11:35, while old live entered later at 22:11:47.668Z fill 0.8475. The BEAT delay was mostly the old mark-basis category gate, not proof that the market was untradeable.

Residual risk: fixed4 is an event-window parity audit, not an edge proof. Backtest still uses next-bar proxy execution and cannot claim exact live fill prices. The next validation is a new live2 run on P373: selected/rejected artifacts should show no mark-basis trading rejects, Active should be current/session-seen, Trading should show run-level orders/positions, and a normal stop fill should not leave runtime gates disabled.
```

## 2026-05-21 - P372 live2 stage state store hotfix

```text
Current patch status: P372 PROPOSED / UNKNOWN commit. GitHub branch head checked before patch: 2e9f38269a32546247fe22bff794cd3d8b4b5f4f.
Incident: after applying P371 locally, `run-anomaly-live2` crashed during startup with `AttributeError: 'SymbolStateStore' object has no attribute 'stage_symbol_counts'` in `AnomalyLive2Runner._market_data_status()`.
Cause: runner/status-grid path referenced the new stage counter, but the state-store boundary was missing from the runtime code. Because `SymbolState` uses `slots=True`, the stage timestamp fields must also exist explicitly; otherwise the next decision path could fail when deadline code assigns `stage0_passed_ms` etc.
Change: add `LIVE2_STAGE_LABELS`, explicit `stage0_passed_ms`..`stage5_passed_ms` fields, export them into symbol-state artifacts, and implement `SymbolStateStore.stage_symbol_counts()`. Keep `actionable_symbol_counts` backward-compatible but source it from stage0 threshold crossings.
Trading impact: none. Diagnostics/status only.
Validation: compileall plus direct `SymbolStateStore.stage_symbol_counts()` smoke.
```

## 2026-05-21 - P371 live2 stage-aware active grid

```text
Current patch status: P371 PROPOSED / UNKNOWN commit. GitHub branch head checked before patch: f12455f264f63432af8b64abed9f433532a0390b.
Question: `Активные 564/579` is misleading because it counts ordinary real-trade buckets, not symbols that passed meaningful strategy stages.
Finding: the previous active metric used `actionable_since_ms`, and P370 intentionally let every real 5s trade bucket reach the signal engine for backtest parity. That made operator UI interpret passive liquidity as active opportunity.
Change: live2 now keeps TTL stage flags: stage0 threshold-crossed bucket, stage1 signal selected, stage2 entry guard checked, stage3 guard accepted, stage4 execution attempted, stage5 position opened/protected. The grid renders stage0/1/2 and stage3/4/5 counts. Backward-compatible `actionable_symbol_counts` now reports stage0 counts, not parity-only buckets.
Trading impact: none. The decision path still evaluates parity buckets; only diagnostics and status-grid semantics change.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; status-grid smoke asserts `stage0/1/2 1/0/0`.
```

## 2026-05-21 - P370 deeper live2/backtest parity repair

```text
Current patch status: P370 APPLIED locally / UNKNOWN commit.
Question: make live2 match backtest as closely as possible excluding network/CPU latency, and check for logic/math/substituted-value errors.
Finding: P369 aligned entry execution, but live2 still used several lookalike fields differently from backtest. The live whipsaw cap used 24h prior context, while backtest uses the 60 setup-candle baseline. Live effort-per-return divided by the final 5s return, while backtest divides by the whole forming setup return. Live taker/flow-hold was trailing stream-native, while backtest uses the confirmation segment. Live also had a pre-signal single-bucket actionability gate that could skip a cumulative backtest candidate before the signal engine saw it. Finally, live still allowed a 5s-scaled baseline fallback before 60 closed 1m baseline candles existed.
Change: live2 now computes whipsaw, quote/trade effort per return, taker share/delta, and flow_hold from the same forming 1m/5s confirmation segment and 60x1m baseline used by backtest. Live quote/trade setup ratio constants are aligned to the current backtest CLI defaults, 5.0/5.0. The 5s-scaled trading baseline fallback was removed; live returns data_dependency_not_ready until the real 1m baseline exists. Any real 5s trade bucket reaches the signal engine for parity, even if the old actionability display thresholds are not crossed. Computed-feature missing values now reject like backtest masks instead of being reported as external data dependencies.
Residual parity risk: live candle rings are built from real aggTrades and do not synthesize zero-trade candles. If the historical cache contains exchange kline zero-volume candles, baseline medians can still differ. This is honest and visible rather than hidden by fallback.
Validation: `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py tests\test_anomaly_continuation_lab.py`; `python -m compileall data\exchanges research_tools cli constants.py main.py`; `git diff --check`.
```

## 2026-05-21 - P369 live2/backtest execution parity audit

```text
Current patch status: P369 APPLIED locally / UNKNOWN commit.
Question: check live2/backtest parity carefully.
Finding: after P367/P368, candidate shape was much closer, but execution parity still had two live-overfilters and one risk-model mismatch. Live2 entry guard accepted only 2000ms signal age and RR>=0.95, while the backtest market model enters on the next 5s entry candle and uses min_market_rr_to_signal_tp1=0.70. Live2 also built signal stop/TP1 from decision_box_low and 1R, while backtest uses initial_stop=max(box_low - 0.05*box_range, decision EMA20) and signal TP1=rounded(entry + 0.75*(entry - box_low)).
Change: live2 signal construction now uses the backtest stop/TP1 model for selected signals and entry-guard RR checks. Live2 entry guard defaults are now max_signal_age_ms=5000 and min_rr_to_tp1=0.70. The backtest latency stress default/grid now uses 0ms and 5000ms to match the live entry freshness window. Near-miss artifacts expose live_setup_decision_ema20, stop buffer, and tp1_r.
Residual parity risk: live2's runner_flow flow_hold remains stream-native rather than bit-identical to the backtest confirmation-segment calculation. It is not currently the known live-overfilter, but it should be checked in the next live near-miss funnel.
Validation: `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py`; compileall on touched files.
```

## 2026-05-21 - P368 remove live1 monolith and tighten parity audit

```text
Current patch status: P368 APPLIED locally / UNKNOWN commit.
Question: can the old 19k-line live1 monolith be removed, and are there other live2/backtest filtering mismatches?
Change: removed research_tools/anomaly_micro_live.py and the run-anomaly-live CLI command. The still-used aggTrade cache aggregation helpers moved to research_tools/anomaly_aggtrade_cache.py, and standalone run-anomaly-top-growth now uses live2 top-growth plumbing. The live1-only lifecycle test file was removed with the live1 runner.
Parity finding: another live2-overfilter remained after P367. Live2 rejected any final 5s decision candle that was not green, but backtest does not require the last confirmation candle to be green; it requires setup-level price_retention, verticality and hold_count across the forming setup. Live2 now removes the single-5s upward gate and applies the backtest setup-level confirmation filters instead, with near-miss live_setup_* diagnostics.
Residual parity risk: runner_flow flow_hold is not bit-identical; live uses trailing no-lookahead 5s hold while backtest's field name is next_n but is computed on the confirmation segment available at decision. This is not currently an overfilter relative to backtest, but future run artifacts should compare runner_flow rejects separately.
Validation: `python -m compileall data\exchanges research_tools cli constants.py main.py`; `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py tests\test_anomaly_continuation_lab.py`; `python main.py run-anomaly-top-growth --help`; CLI help no longer lists run-anomaly-live.
```

## 2026-05-21 - P367 live2/backtest candidate parity repair

```text
Current patch status: P367 APPLIED locally / UNKNOWN commit.
Question: why did post-P365 live2 run 20260521_124514 still have selected_count=0?
Finding: live2 still choked before entry guard. The run had 123388 decisions, selected_count=0, execution/orders=0, and rejects dominated by stream_candle_is_not_upward_price_confirmation=67516 and prior_whipsaw caps about 50k per live-priority category. Top-growth showed real hourly movers (BSB +14.0% and +17.5%, EDEN +11.9%, FIDA +10.6%), so "dead market" is false. Root cause: live2 applied shared category caps to a single 5s actionable bucket, while backtest builds a forming 1m setup from 5s entry candles after the default 4 confirmation candles. This made prior_whipsaw/range/risk materially stricter in live than in backtest.
Change: live2 signal features now build a backtest-like 1m/5s forming setup before category evaluation. Category gates use cumulative setup quote/trade pace, forming setup range/risk, and prior-whipsaw divided by the forming setup range. Near-miss artifacts now expose live_setup_* parity diagnostics and feature initial risk. The grid `Активные` count now uses current TTL / unique active symbols since the current session metric start.
Risk: medium. This aligns live2 with the existing backtest candidate contract, but it means default live2 category evaluation waits for the same 4x5s confirmation horizon as the backtest before shared categories can accept. If the operator target is truly pump-start-to-order <5s, the backtest contract itself must be changed and revalidated; live2 should not fake faster parity.
Validation: `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py`; `python -m compileall research_tools\anomaly_live2 cli\commands.py cli\parser.py constants.py main.py`; `python -m compileall data\exchanges research_tools cli constants.py main.py`.
```

## 2026-05-21 - post-P365 live2 choke audit

```text
Current patch status: no additional trading-logic patch after P365.
Question: are current live2 zero-selected symptoms still caused by dumb blockers?
Finding: current code no longer shows the confirmed unit/scale blockers fixed by P365. The old run 20260521_110133 cannot validate the new funnel because it predates P365. Remaining gates are strict but strategy-level: upward price confirmation, mark premium, OI delta, prior fake-pump caps, range expansion, and no-lookahead flow hold. Changing them without a post-P365 near-miss/top-growth comparison would be threshold loosening, not root-cause repair.
Next validation: restart live2 on current head and inspect live2_near_misses.csv, decision_funnel, selected_count, entry_guard counts, and top_growth mismatch. If selected_count remains zero, the next patch should be category/threshold research-driven, not a live hotfix.
```

## 2026-05-21 - P365 live2 signal feature contract parity

```text
Current patch status: P365 APPLIED locally / UNKNOWN commit.
Question: are the zero selected signals in run 20260521_110133 healthy strictness or bugs that choke everything?
Finding: not fully healthy. The run had real activity, but live2 compared a 5s baseline quote value directly to min_baseline_quote_daily_proxy=300000 and used a single 5s candle range as the prior-whipsaw denominator. Those units do not match the backtest/category contract and can make all categories unreachable. Initial risk also used the current 5s low instead of a decision-box low, making min_initial_risk_pct likely unreachable if earlier filters passed.
Change: live2 now computes baseline_quote_daily_proxy from the 5s baseline pace, and uses a recent closed-live decision box for prior-whipsaw range and initial stop/risk. Thresholds and execution safety were not loosened.
Validation: `python -m compileall research_tools/anomaly_live2 cli/commands.py cli/parser.py constants.py main.py`; `python -m compileall data/exchanges research_tools cli constants.py main.py`; `.venv\Scripts\python.exe -m pytest -q tests/test_live2_market_watch.py`.
Next validation: restart live2 and require near-miss/decision artifacts to show whether remaining blockers are now real OI/mark/category rejections, entry_guard drift/RR/stale rejections, or actual exchange execution.
```

## 2026-05-21 - P364 live2 session-scoped operator grid

```text
Current patch status: P364 APPLIED locally / UNKNOWN commit.
Question: make grid-log numbers session-scoped where appropriate, show four session growth tops, and replace dot spacer rows.
Change: the live2 grid now uses session-scoped decision/execution/runtime counters derived by subtracting a baseline at the session metric window boundary. First session after process start counts from live start; later session rollovers reset the baseline. Session top tracker limit is four, and grid separators are full-width underscore lines.
Trading impact: none. This changes operator display semantics only; cumulative forensic counters remain in events/diagnostics.
Validation: `python -m compileall research_tools/anomaly_live2 cli/commands.py cli/parser.py constants.py main.py`; `python -m compileall data/exchanges research_tools cli constants.py main.py`; `.venv\Scripts\python.exe -m pytest -q tests/test_live2_market_watch.py`.
```

## 2026-05-21 - P363 live2 active-symbol grid truth

```text
Current patch status: P363 APPLIED locally / UNKNOWN commit.
Question: why did run 20260521_110133 show 0 active symbols after 50+ minutes?
Finding: it was a metric bug, not a market fact. Live2 processed about 67m of market-runtime, 41053 decisions, 40981 signal evaluations, and 0 selected signals. However, the grid counted only state.status=actionable. The deadline engine writes actionable_since_ms for every actionable bucket but immediately sets status back to watching after the verdict, so current status actionable stays zero by design.
Change: the grid now renders `Активные current/seen` using actionable_since_ms within the radar TTL and total symbols ever actionable in the run. This makes the operator view reflect active recent anomaly symbols instead of a transient internal enum.
Trading impact: none. It does not loosen filters; selected_count remains the count of category-accepted entry signals, and entry_guard/execution are unchanged.
Validation: `python -m compileall research_tools/anomaly_live2 cli/commands.py cli/parser.py constants.py main.py`; `python -m compileall data/exchanges research_tools cli constants.py main.py`; `.venv\Scripts\python.exe -m pytest -q tests/test_live2_market_watch.py`.
```

## 2026-05-21 - P362 live2 near-miss artifacts

```text
Current patch status: P362 APPLIED locally / UNKNOWN commit.
Question: why did current live2 show zero active/selected symbols despite running for much longer on the terminal?
Finding: in run 20260521_103438, persisted diagnostics showed about 11m of market-runtime after startup/context warmup, not full process wall-clock. The run had 9129 deadline decisions, selected_count=0, entry_guard total_checked=0, and execution total_execute_calls=0, so the choke point was signal contract before entry/execution.
Change: live2 writes live2_near_misses.csv for post-actionable non-selected decisions. This makes "market was quiet vs filters cut too hard" directly inspectable by symbol, stage, blocker reasons, prior whipsaw/spike/fade context, OI/mark status, flow metrics, and full event JSON.
Trading impact: none. The patch adds audit only; no category threshold, runtime gate, entry guard, exchange order, fill, stop, or position lifecycle behavior changed.
Validation: `python -m compileall research_tools/anomaly_live2 cli/commands.py cli/parser.py constants.py main.py`; `python -m compileall data/exchanges research_tools cli constants.py main.py`; `.venv\Scripts\python.exe -m pytest -q tests/test_live2_market_watch.py`.
Next validation: restart live2 on this commit and require live2_near_misses.csv to fill while live2_events.csv still records every deadline_decision; if artifact_writer_status rejected_count/error_count rises, new entries must remain disabled rather than silently losing audit.
```

## 2026-05-21 - P361 live2 operator grid semantics

```text
Current patch status: P361 APPLIED locally / UNKNOWN commit.
Question: live2 terminal grid did not match requested semantics; `Активные 0/578` mixed current actionable entry candidates with universe size.
Change: live2 status grid now uses four-column `◆` sections with dot separator rows and renders `Аномалии` as total actionable decisions while `Активные` is current actionable / total selected entry signals. Context, latency, market, session top, and trading rows are kept compact and operator-facing only.
Validation: `python -m compileall research_tools/anomaly_live2/status_grid.py`; `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py`.
```

## 2026-05-21 - P360 live2 entry attempt timing artifacts

```text
Current patch status: P360 APPLIED locally / UNKNOWN commit.
Question: can live2 prove where time is spent for each attempted entry, and are recent live runs blocked by dumb entry blockers?
Change: live2 deadline_decision events now include `entry_attempt_timing` for actionable buckets, with signal evaluation, entry guard, runtime gate, and execution call timestamps/durations. Execution results now include internal `execution_timing`: pre-position fetch, market order submit/fill, post-position fetch, stop submit, stop visibility verification, and emergency close timing when applicable.
Run check: latest usable full run 20260521_044228 had 0 selected signals, 0 entry guard checks, 0 execution calls, 0 orders, and 0 integrity errors. It was not blocked by order/execution plumbing; rejects were signal-contract filters dominated by non-upward price confirmation and prior whipsaw/spike/fast-fade category caps.
Risk: artifact-only hot-path additions use in-memory timestamps and JSON event fields; no entry thresholds, fallback data, fill model, or stop logic changed.
```

## 2026-05-21 - P359 anomaly-lab latency grid

```text
Current patch status: P359 APPLIED locally / UNKNOWN commit.
Question: how to run 40d anomaly-lab without latency simulation, and what latency grid should be used when enabled?
Change: normal `run-anomaly-lab` still does not run hidden 1s latency stress unless `--latency true` is passed. When latency stress is enabled, default grid is now `0ms, 2000ms`. The 2000ms value is aligned with live2 `entry_guard_max_signal_age_ms`, so it models the largest signal age live2 should still execute.
Validation: `python -m compileall research_tools/anomaly_strategy_backtest.py cli/commands.py cli/parser.py`.
```

## 2026-05-20 - P358 live2 top-growth audit off hot path

```text
Current patch status: P358 APPLIED locally / UNKNOWN commit.
Question: is anything left before launching after P357?
Change: top-growth audit no longer calls exchange REST from the main heartbeat/decision loop. The runner starts a single daemon worker for bounded audit chunks and drains completion events from the main loop.
Trading impact: none. This closes the main remaining concern from P357: audit visibility should not become a latency bottleneck for live entries.
Validation: `python -m compileall research_tools/anomaly_live2 cli/commands.py cli/parser.py`; `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py`.
Next validation: start live2 and watch `decision_loop_overrun_count`, `total_deadline_missed`, `total_deadline_expired_backlog`, `entry_stream_ready`, and `top_growth_audit.status` for the first hour.
```

## 2026-05-20 - P357 live2 closed-hour top-growth audit

```text
Current patch status: P357 APPLIED locally / UNKNOWN commit.
Question: can live2 prove what hourly pumps happened during the run even when it made no trades?
Change: live2 now owns a bounded top-growth audit task. It writes closed 1h exchange-candle `top_growth/` artifacts inside the run root: index, capped top rows, and full per-symbol status rows. The task is not a signal source and does not use ticker/session snapshots as a fallback.
Trading impact: none directly. This improves missed-pump visibility and data-quality audit only. It may add low-rate REST load on heartbeat; default is one symbol per heartbeat to avoid moving the latency bottleneck into the decision loop.
Validation: `python -m compileall research_tools/anomaly_live2 cli/commands.py cli/parser.py`; `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py`.
Next validation: run live2 across one UTC hour close and require `top_growth/top_growth_index.csv`, `top_growth_*.csv`, and `top_growth_status_*.csv` to appear; heartbeat diagnostics should show `top_growth_audit.status=processing/completed` without decision latency degradation.
```

## 2026-05-20 - P356 live2 entry-stream gate and backlog/context diagnostics

```text
Current patch status: P356 APPLIED locally / UNKNOWN commit.
Question: can live2 stop losing entries to global WS flaps, reconnect backlog, and over-strict prior-context id-gap invalidation without sweeping failures under the rug?
Change: runtime market-data readiness now requires selected universe + aggTrade readiness + live decision watermark, not global ticker+aggTrade+mark all at once. Ticker/mark global readiness remains in artifacts; mark is now checked stale-aware per symbol inside the signal dependency contract. Deadline processing is fresh-first and old reconnect/backlog buckets become explicit `deadline_expired_backlog` instead of competing with still-enterable buckets. Prior-context live 5m roll-forward tolerates aggTrade-id gaps as diagnostic, with `total_ws_5m_gap_above_tolerance_tolerated` preserving the evidence.
Trading impact: fewer false global no-entry windows and fewer stale backlog decisions on the hot path. No entry can pass with stale required per-symbol mark/OI/prior context; stale context remains `data_dependency_not_ready`. No fill/stop/TP logic changed.
Validation: `python -m compileall research_tools/anomaly_live2 cli/commands.py cli/parser.py`; `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py`.
Next validation: restart live2 and require `entry_stream_ready=true` during ticker/mark global flaps when aggTrade is fresh, `total_deadline_missed` near zero for fresh buckets, old reconnect bursts visible as `total_deadline_expired_backlog`, `total_ws_5m_gap_rejected=0`, and above-tolerance gap counts visible rather than silently absent.
```

## 2026-05-20 - P355 live2 session trading percent

```text
Current patch status: P355 APPLIED locally / UNKNOWN commit.
Change: live2 now tracks runtime-gate allowed/blocked seconds per current crypto session metric window and renders the operator header as `Торговля N%`. The window resets on the same metric_start_ms used by session top-growth, so the percentage describes the current session, not whole process uptime.
Validation: `.venv\Scripts\python.exe -m compileall -q research_tools\anomaly_live2\runner.py research_tools\anomaly_live2\status_grid.py`; `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py`.
```

## 2026-05-20 - P354 live2 rolling prior-context maintenance

```text
Current patch status: P354 APPLIED locally / UNKNOWN commit.
Question: can live2 keep Ctx stale near zero cheaply from existing WebSockets without masking data holes?
Change: prior 24h context remains REST-bootstrapped from closed 5m OHLCV at startup, then rolls forward from live aggTrade-derived closed 5m candles. Full-window runtime REST repoll is no longer the normal freshness mechanism for symbols with an ok rolling buffer. Minor intra-5m aggTrade id gaps are tolerated up to max(5 ids, 10% observed trades) and recorded; larger gaps mark `ws_gap_exceeds_tolerance` and block context until repair. Symbol-state artifacts include last live 5m context open/close, appended/tolerated/rejected counts, missing id count, and tolerance.
Trading impact: stricter and cheaper context freshness. No signal thresholds, entry guards, fills, stops, TP, or position lifecycle changed. Stale/missing/rejected prior context still becomes `prior_24h_context_not_ready`; the patch changes maintenance, not acceptance.
Validation: `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py`; `.venv\Scripts\python.exe -m compileall -q data\exchanges research_tools cli constants.py main.py`.
Next validation: restart live2 and require `prior_context.maintenance_mode=startup_rest_bootstrap_plus_live_ws_5m_rolling_append`, rising `total_ws_5m_candles_appended`, low/zero `total_ws_5m_gap_rejected`, and no sustained `Ctx stale` for hot symbols after startup.
```

## 2026-05-20 - P353 live2 prior-context launch wiring and artifact durability

```text
Current patch status: P353 APPLIED locally / UNKNOWN commit.
Question: post-P352 live2 run 20260520_122522 showed deadline misses largely fixed, but prior 24h context remained materially stale/not_seen and some decisions stayed blocked by prior_24h_context_not_ready. Root cause: CLI parser/command defaults still injected the old P337/P351 values (15m stale, 5m cooldown, 4 symbols/cycle), overriding the widened AnomalyLive2Config defaults. A separate audit durability issue was observed: full-rewrite artifacts such as live2_symbol_state.csv could be momentarily truncated while the async writer rewrote them in place.
Change: CLI parser defaults and command fallbacks now match runtime config (20m stale, 10m cooldown, 10 symbols/cycle). The prior-context startup event now labels the real selected-universe active-priority poll scope. Status, diagnostics summary, and symbol-state full rewrites are atomic temp-file replacements with fsync before replace. Writer failures still surface through artifact_writer_status and can disable new entries; no fallback data is introduced.
Trading impact: stricter visibility and better context freshness after restart. Signals still reject/block on stale or missing prior context; this patch makes the intended poller budget actually reach launched live2 and prevents audit snapshots from disappearing during rewrite. No category thresholds, execution guards, fill, stop, TP, or position lifecycle changed.
Validation: `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py`; `.venv\Scripts\python.exe -m compileall -q data/exchanges research_tools cli constants.py main.py`.
Next validation: restart live2 on this commit, then require prior_context stale/not_seen counts to trend down after one 20m window, `total_data_dependency_not_ready` from prior_24h_context_not_ready to stop accumulating at the old rate, and artifact_writer_status error/rejected counts to remain zero.
```

## 2026-05-20 - P352 live2 market-watch stability patch

```text
Current patch status: P352 APPLIED locally / UNKNOWN commit.
Question: current live2 run 20260520_112804 is stable at WS/execution level but still has market-watch holes: many `deadline_missed`, prior-context stale counts across the selected universe, and signal-side stale context could still be treated as ok.
Change: live2 closes ended real-trade candles by wall clock inside the decision loop, without synthetic candles or REST/backfill. This removes the dependency on a later trade to make the previous 5s bucket visible to the deadline engine. Prior-context runtime refresh now covers the whole selected universe with active/actionable symbols prioritized and oldest-first fairness, defaulting to 10 symbols/10s, 600s cooldown, and 20m stale. Signal features now use effective stale-aware OI/prior-context statuses while preserving raw status fields in artifacts.
Trading impact: stricter and more timely. Potential entries are less likely to be lost as deadline_missed after a burst goes quiet; stale derivative/prior context can no longer pass category gates as ok. No thresholds, fill, stop, TP, or execution safety are loosened.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; `.venv\Scripts\python.exe -m pytest -q tests\test_live2_market_watch.py`.
Next validation: restart live2 on this commit only when ready, then require `total_deadline_missed` share and prior_context stale count to trend down while artifact writer backpressure and prior_context total_errors stay near zero.
```

## 2026-05-20 - P351 live2 event-driven deadline and aggTrade/OI diagnostics

```text
Current patch status: P351 PROPOSED / UNKNOWN commit.
Question: live2 run 20260520_103312 had stable WS/execution infrastructure but frequent decision deadline misses and useless `ok_with_gaps` aggTrade diagnostics; OI startup prewarm existed but runtime OI refresh fairness let alphabetically early symbols recycle before the tail of the universe.
Change: deadline evaluation is now event-driven by aggTrade dirtiness instead of scanning every selected symbol every 50-100ms, and the live decision watermark is cached once per cycle. Fast runtime gates no longer build full per-symbol count summaries every loop; full counts stay on heartbeat/status writes. aggTrade status now separates `ok_active`, `ok_idle_no_trades`, `gap_missing_expected_bucket`, and `stale`. Runtime OI polling rotates by oldest poll time inside priority buckets, so selected-universe refresh cannot starve later symbols. Defaults align OI stale with 5m OI cadence and full-universe refresh, with explicit startup OI prewarm retained before live entries.
Trading impact: latency/audit bugfix only. No signal threshold, fill, stop, TP, or fallback logic is loosened. Missing data still blocks via dependency/status paths.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; `launcher.py` is absent in this zip, so the broader hygiene command with launcher cannot be run literally.
Next validation: rerun live2 and require deadline_missed share to collapse, `live_aggtrade_status_counts` to split across ok_active/ok_idle/gap/stale instead of all ok_with_gaps, and OI stale counts to trend down after one refresh cycle.
```

## 2026-05-20 - P349 live2 aggTrade shard stale_ms wiring

```text
Current patch status: P349 PROPOSED / UNKNOWN commit.
Question: fresh live2 startup run 20260520_090142 failed after aggTrade WS startup with all four shards reporting `AttributeError: _Live2AggTradeWsShard object has no attribute stale_ms`.
Change: Live2AggTradeWsSource now passes its configured `aggtrade_stale_ms` into every `_Live2AggTradeWsShard`, and the shard validates/stores it before using it for receive timeouts and stale watchdog checks.
Trading impact: bugfix only. This does not loosen readiness: all aggTrade shards still need fresh live WS payload before live2 can enter the main loop.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; rerun live2 startup and require the previous AttributeError to disappear.
Next validation: inspect the next `aggtrade_ws_startup_failed` event if startup still fails; it should now expose the real Binance/network/payload blocker instead of the local AttributeError.
```

## 2026-05-20 - P342 live2 hard startup/execution safety

```text
Current patch status: P342 PROPOSED / UNKNOWN commit.
Question: after P331-P341, live2 still had a few ambiguous runtime states: it could keep running with failed execution preflight, missing private user-data stream, or too-small auto-universe; Binance listenKey keepalive/close calls sent an unnecessary listenKey parameter despite the USD-M endpoints documenting no request parameters; and a protected position that became exchange-flat outside the TP1 path could be removed from the local registry without proving the old stop was gone.
Change: live2 now treats execution preflight failure, user-data stream startup failure, and auto-universe below minimum as startup failures. The startup event explicitly labels live2 as real-orders-only/no dry-run. Binance USD-M listenKey keepalive/close calls now call their documented no-parameter endpoints. When the exchange position is flat, the supervisor verifies the protected stop is already gone or cancels/verifies it gone before removing the protected position.
Trading impact: stricter and less ambiguous. Live2 either has the mandatory execution/user-data/universe prerequisites or stops. It will not silently keep a flat local state while a reduce-only conditional stop remains visible.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic flat-position orphan-stop smoke.
Next validation: run live2 smoke and require either clean startup with user-data ready + universe >= floor, or immediate explicit startup failure event/reason.
```

## 2026-05-20 - P341 live2 full-TP1 position contract

```text
Current patch status: P341 PROPOSED / UNKNOWN commit.
Question: live2 supervisor still implemented the older TP1 partial-close + BE-stop runner lifecycle, while the current live2 strategy contract needs TP1 to close the whole position and avoid runner remainder complexity.
Change: live2 TP1 close fraction is now exactly 1.0; config validation rejects partial fractions; protected positions default to 100% TP1; supervisor submits a reduce-only full-position TP1 close, requires exchange position flat afterwards, cancels/verifies gone the old initial stop, removes the protected registry row, and emits `position_tp1_full_close_verified`. If the full close does not flatten the exchange position or the old stop cannot be cancelled, live2 emits a strict position integrity error. No BE stop is created after TP1.
Trading impact: simpler and stricter live2 exit lifecycle. TP1 is now terminal for the position. Runner/BE-stop behavior is deliberately removed from live2 default, not kept as optional fallback.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic full-TP1 close smoke.
Next validation: live2 smoke after P331-P341; verify `position_tp1_full_close_verified`, `total_tp1_closes`, `total_final_closes`, old stop cancellation artifacts, and no `position_tp1_filled_be_stop_verified` on the new path.
```

## 2026-05-20 - P339 live2 flow-hold/taker confirmation contract

```text
Current patch status: P339 PROPOSED / UNKNOWN commit.
Question: current live2 categories include flow-hold / next-taker-buy confirmation fields, but P338 correctly exposed them as not ready. Live2 needs a non-lookahead contract for those fields before `runner_flow` can be evaluated honestly.
Change: signal evaluation now computes flow-hold from trailing closed live WS 5s candles at or before the decision candle. It exposes `flow_hold_status`, reason, count, window, quote/trade/taker-buy totals, taker share mean/last/delta, and maps legacy `min_next_taker_buy_quote_share` to `live_confirmed_taker_buy_quote_share`. No future buckets, hot-path IO, REST fallback, or zero substitution are used.
Trading impact: `runner_flow` can now become computable when live aggTrade candles provide enough closed pre-entry flow. Missing baseline/live candles still produce `data_dependency_not_ready`; weak confirmed flow remains a real strategy reject. Mark/OI/24h prior dependencies are unchanged.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic closed-live-flow signal smoke.
Next validation: live2 smoke after P331-P339; inspect `deadline_decision.signal_features.flow_hold_*`, `live_confirmed_taker_buy_quote_share`, and ensure no category uses future candles or startup REST candles as live flow hold.
```

## 2026-05-20 - P338 live2 dependency-aware signal gate

```text
Current patch status: P338 PROPOSED / UNKNOWN commit.
Question: current live2 category evaluation mixed missing data dependencies with strategy rejects and also risked accepting categories while some contract fields were unavailable.
Change: signal evaluation now classifies missing required mark/OI/24h prior/baseline/derived category inputs as `data_dependency_not_ready`, keeps real threshold failures as `rejected_signal_contract`, writes dependency/reject reason arrays into deadline events, adds dependency counters to deadline status, diagnostics summary, symbol state, and the terminal grid. There is no fallback or zero substitution.
Trading impact: stricter and more honest. Live2 can now show that a signal was not decidable because data dependencies were not ready, instead of counting it as a strategy reject. Current categories that require not-yet-final flow-hold/taker-delta handling can remain blocked by dependency status until that data contract is completed.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic missing-dependency signal smoke.
Next validation: live2 smoke after P331-P338; inspect `deadline_decision.verdict=data_dependency_not_ready`, `signal_dependency_reasons`, `live2_status.json.decision_status.total_data_dependency_not_ready`, and `live2_diagnostics_summary.json.decision_funnel.signal_dependency_funnel`.
```

## 2026-05-20 - P337 live2 24h prior context poller

```text
Current patch status: P337 PROPOSED / UNKNOWN commit.
Question: provide the prior fake-pump/spike/whipsaw context required by current live2 categories without using 72h or hot-path fallback.
Change: live2 now starts an active/radar-only prior-context poller that fetches closed 5m OHLCV over an exact 24h window, computes prior spike count, prior fast-fade count, and prior whipsaw legs, stores source/status/coverage fields per SymbolState, exposes context counts in status/grid/diagnostics, and evaluates legacy category `*_72h` fields from explicitly labelled 24h live context. Missing/empty/invalid context remains `prior_24h_context_not_ready`; no zero fallback is introduced.
Trading impact: category acceptance is stricter and more honest for prior-context-required categories. Market-data stream readiness, order placement, fills, stops, TP/BE and execution guards are unchanged. This patch also fixes the P336 OI poller status snapshot copy so OI diagnostics can be read without constructing from its `ready` view field.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic 24h prior-context snapshot/poller smoke.
Next validation: live2 smoke with small explicit universe; require `prior_context_poller_starting`, `prior_context_status_counts`, grid `24h ctx`, and category rejects to move from missing prior context to real `prior_24h_context_not_ready` / count-threshold rejects / accepted when context is ready.
```


## 2026-05-20 - P334 live2 planned WS rotation lifecycle

```text
Current patch status: P334 PROPOSED / UNKNOWN commit.
Question: prevent Binance USD-M market WebSocket sessions from relying on server-side 24h disconnects and make WS lifecycle/audit data explicit.
Change: live2 ticker and aggTrade WS sources now accept `ws_connection_max_age_seconds` (default 84600s = 23h30m), track per-connection start/age/max-age, close/error details, and planned rotation counters. When the configured age is reached, the source closes the WS intentionally and reconnects immediately without consuming exponential backoff. AggTrade shard readiness still requires fresh applied payload. No signal/category/order/fill/stop/TP logic changed.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic short-lifetime rotation smoke.
Next validation: run live2 smoke long enough to see normal payloads, then a short test with `--ws-connection-max-age-seconds 2` and confirm planned rotations increment without reconnect storm or new-entry false readiness.
```
# Anomaly Research State

## 2026-05-20 - P335 live2 markPrice WS context

```text
Current patch status: P335 PROPOSED / UNKNOWN commit.
Question: current default live2 categories need mark-vs-decision basis, while live2 had no markPrice data source.
Change: live2 now runs a routed Binance USD-M `!markPrice@arr@1s` WS source for the selected universe, stores mark/index/funding fields in SymbolState, gates market-data readiness on fresh markPrice payloads, writes mark diagnostics/status counts, and evaluates `mark_close_vs_decision_close_basis` from real mark price in the signal adapter. No mark fallback is introduced.
Trading impact: no order/fill/stop/TP behavior changed. Market-data readiness is stricter: ticker + live aggTrade + markPrice are required. OI and prior 24h context are still missing by design and remain next patches.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic mark-price payload smoke.
Next validation: run a short live2 smoke and confirm `mark_price_ws.ready=true`, `mark_price_status_counts.ok > 0`, and `stream_coverage_ready` only when ticker, aggTrade, and markPrice are all fresh.
```

## 2026-05-20 - P333 live2 transition-only market-data diagnostics

```text
Current patch status: P333 PROPOSED / UNKNOWN commit.
Question: stop live2 from spamming hundreds of thousands of market-data coverage events while preserving post-mortem truth.
Change: live2 now emits `market_data_coverage_transition` only when the semantic market-data state changes. The dedup key excludes rolling clean/degraded windows and reconnect counters, which remain in status/summary artifacts instead of event spam. A new `live2_diagnostics_summary.json` exposes event-type counts, market-data transition counts, runtime-gate transition counts, allowed/blocked gate seconds, reconnect summary, decision funnel, and execution funnel.
Trading impact: none. No strategy thresholds, categories, WS ingestion, order placement, fills, stops, TP/BE, or runtime gates are loosened. This is audit volume control and diagnostic truthfulness only. Prior-context planning remains 24h.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic artifact-writer diagnostics smoke.
Next validation: short live2 smoke after P331-P333; require `live2_events.csv` to contain transitions/heartbeats/decisions rather than per-cycle `market_data_coverage_update` spam, while `live2_diagnostics_summary.json` contains reconnect counters and gate durations.
```

## 2026-05-20 - P332 live2 warmup/live watermark separation

```text
Current patch status: P332 PROPOSED / UNKNOWN commit.
Question: prevent startup REST warmup from masquerading as live flow or creating stale live decisions.
Change: live2 now stores startup REST aggTrade and live WS aggTrade counters separately, labels candle source composition, exposes live/startup counts in status artifacts, and gives the deadline engine a live aggTrade watermark. Closed buckets before the first valid live WS payload are consumed as pre-live buckets without `deadline_decision` events, so warmup history cannot become fake `deadline_missed`.
Trading impact: no strategy thresholds, order placement, fills, stops, or category logic changed. This is market-data truthfulness and decision-gating only.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic warmup/live watermark smoke.
Next validation: short live2 smoke after P331+P332; require `candle_coverage_counts.startup_warmup_only` during REST warmup, transition to `live_ready` only after WS payloads, and no startup burst of `deadline_missed`.
```

## 2026-05-19 - P324 live2 Telegram operator safety messages

```text
Status: PROPOSED / UNKNOWN commit.
Live2 now has its own Telegram operator notifier. The command requires the same TELEGRAM_* env contract as live1, sends startup/status degradation messages to the events channel, sends verified entry / TP1-BE / final-close messages to positions, and sends strict critical alerts for position/order integrity errors. Symbol names are rendered as Coinglass links. Telegram remains notification-only; live2_events.csv/live2_status.json remain source of truth.
Next: smoke run live2 with tiny universe and verify telegram_message_sent/telegram_*_failed events plus actual rendered messages before adding restart/open-order reconciliation.
```

## 2026-05-19 - P320 live2 fast decision loop gates

```text
Status: PROPOSED / UNKNOWN commit.
Live2 deadline decisions now run on a dedicated fast loop (`decision_loop_interval_seconds`, default 0.1s) instead of waiting for the 5s heartbeat. Heartbeat/status/symbol-state writes remain periodic through the async artifact writer. Runtime gates now expose stream coverage readiness, decision-latency degradation/recovery, artifact-writer readiness, exchange boundary readiness, and the exact no-new-entries reason.
Next: implement verified real order lifecycle only after live2 proves low deadline misses and stable runtime gates under a short smoke run.
```

## 2026-05-19 - P319 live2 bounded async artifact writer

```text
Status: PROPOSED / UNKNOWN commit.
Live2 audit writes now use a bounded background writer queue. Deadline/signal cycles enqueue audit jobs instead of doing blocking CSV/JSON disk writes on the hot path. Writer queue health, rejected enqueue count, and IO errors are exposed through live2_status/heartbeat data; artifact_writer_ready becomes false if the queue fills or the writer errors, which keeps new entries disabled rather than trading without safe audit.
Next: runtime hardening for reconnect/coverage/latency degradation gates before enabling real order placement.
```

Compact project memory. Detailed rules live in Project Instructions.

## 2026-05-20 - P336 live2 active/radar OI poller

```text
Current patch status: P336 PROPOSED / UNKNOWN commit.
Question: provide real open-interest context for live2 categories without polling the whole universe or substituting missing OI with zero.
Change: live2 now starts an active/radar-only 5m open-interest poller after universe/WS startup. It polls only watching/actionable/in-position or recently live-flow symbols, computes real 3x5m OI change from exchange OI history, stores OI fields/source/status per SymbolState, exposes status counts/grid/diagnostics, and lets `runner_oi_confirmed` evaluate `min_oi_change_pct_3x5m` from those fields. Missing/empty/invalid OI remains explicit `oi_context_not_ready` or source status; no silent fallback is introduced.
Trading impact: no thresholds, prior-24h context, entry guards, order placement, fills, stops, TP/BE, or runtime market-data readiness are loosened. OI is a category dependency, not a global stream-coverage gate in this patch.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic open-interest snapshot/poller smoke.
Next validation: live2 smoke with small explicit universe; require `open_interest_poller_starting`, OI status counts in heartbeat/status/grid, and OI-required category rejects to move from unavailable-generation reason to real `oi_context_not_ready` / `oi_change_3x5m_below_category_min` / accepted when data is ready. Prior-context planning remains 24h.
```

## 2026-05-20 - P331 live2 aggTrade market routed WS readiness

```text
Status: PROPOSED / UNKNOWN commit.
Live2 aggTrade combined streams now use the Binance USD-M Futures routed `/market/stream?streams=` endpoint. Shards are not ready on TCP connect alone: readiness requires a fresh applied aggTrade payload. Reconnect backoff is reset only after the first valid payload of the current connection, and endpoint URL length / close / exception / pre-first-payload failure diagnostics are exposed in live2_status and the terminal grid. This changes only live2 market-data infrastructure, not strategy filters or order logic. Prior-context planning should use 24h, not 72h.
Next: apply P331, run a 2-3 minute live2 smoke without changing strategy parameters, and require aggTrade rows_applied > 0, shards_connected == shards_total, no pre-first-payload reconnect storm, and new_entries_allowed staying false until real payload readiness.
```

---

## 1. Current state

```text
Branch: codex/ideal-like from uploaded ZIP
Commit: UNKNOWN
Local patch stack: P130-P176 present in uploaded ZIP / UNKNOWN commit; P177/P178/P179/P180 applied locally by user / UNKNOWN commit; P181/P184/P185 present in uploaded ZIP / UNKNOWN commit; P186 proposed; P189/P190/P192/P205/P206/P207/P208/P209 applied/proposed status UNKNOWN from prior memory; P213-P217 applied locally in uploaded ZIP / UNKNOWN commit; P218 proposed; P219/P220/P221 applied locally / UNKNOWN commit; P222 proposed; P223/P224/P225/P226/P227/P228/P229 applied locally by user / UNKNOWN commit; P230 proposed
Last active patch: P336 proposed live2 active-symbol OI poller
Updated: 2026-05-20
```

## 2026-05-19 - P315 live2 deadline verdict engine

```text
Current patch status: P315 PROPOSED / UNKNOWN commit.
Question: add the next live2 layer after ticker-selected aggTrade universe, prioritizing speed/reliability and proving candidates cannot disappear into a queue.
Change: add `Live2DeadlineEngine`, which reads already-built in-memory 5s candle rings, treats threshold-crossing closed buckets as diagnostic actionable buckets, and gives each processed actionable bucket an explicit verdict before/after the configured deadline. Generation 0 still has no real SignalEngine, so successful on-time actionable buckets end as `rejected_signal_engine_todo`; degraded buckets become `data_not_ready`; late buckets become `deadline_missed`. Decision counters, verdict reasons, and latency are written to `live2_status.json`, `live2_events.csv`, and `live2_symbol_state.csv`.
Trading impact: no real orders, signal thresholds/categories, execution guards, fills, stops, TP, BE, or live1 behavior are changed. New entries remain disabled because SignalEngine/ExecutionEngine are still TODO.
Validation: P311->P314 were applied first, then P315 applied on top; `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic deadline-cycle smoke confirmed one actionable 5s bucket gets a bounded-time `rejected_signal_engine_todo` verdict and state counters update.
Next validation: run `.venv\Scripts\python.exe main.py run-anomaly-live2` for 60-120 seconds and check `deadline_decision` events, `decision_status.last_cycle`, no candidate-drop fields, and no hot REST/backfill fields.
```

## 2026-05-19 - P314 live2 ticker-selected startup universe

```text
Current patch status: P314 PROPOSED / UNKNOWN commit.
Question: remove the manual `--symbols` dependency from live2 market-data startup without adding REST discovery or hot-path fallback.
Change: `run-anomaly-live2` now starts all-ticker WS first, selects a startup universe from already-received ticker state when explicit symbols are absent, marks `universe_selected/rank/reason` in `live2_symbol_state.csv`, and then starts aggTrade shards for the selected symbols. Default auto universe is top USDT futures by 24h quote volume/trade count, capped at 240 symbols with a 300k USDT minimum 24h quote-volume. Explicit symbols still bypass liquidity pruning.
Trading impact: no signal thresholds, categories, order placement, fills, stops, TP, BE, or PnL logic changed. New entries remain disabled because SignalEngine/ExecutionEngine are still TODO. This patch only improves live2 market-data startup speed/reliability and removes manual-symbol-only aggTrade coverage.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic selector smoke confirmed top-liquidity selection and symbol-state universe marking.
Next validation: apply P314 after P313 and run `.venv\Scripts\python.exe main.py run-anomaly-live2`; check `universe_selected`, aggTrade shard count, `live2_status.json.market_data_status.universe`, and no hot REST fields.
```

## 2026-05-19 - P310 prior fake-pump quarantine

```text
Current patch status: P310 APPLIED locally / UNKNOWN commit.
Question: prevent symbols that already exceeded the 24h prior fake-pump / fast-fade threshold from entering warm/radar hot lanes and creating latency before category rejection.
Change: after startup symbol-context snapshots are ready, live pre-populates a scheduler quarantine for symbols with prior_fast_fade_count_24h > 2. Before each ticker-radar promotion cycle the quarantine is refreshed from current snapshots and checked before warm/radar enqueue. Quarantine expiry is not a fixed TTL: it is computed from the excess fast-fade timestamps, releasing when enough events age out of the 24h lookback (`oldest_excess_fast_fade_ts + 24h + 1ms`). Quarantined symbols remain in ticker/top-growth universe and are visible through artifacts.
Trading impact: no signal thresholds, category math, executable-entry guards, order placement, fills, stops, TP, BE, or PnL logic changed. This can reduce hot-lane opportunities on symbols with repeated fake fades; it is intentional scheduler quarantine, not permanent universe removal.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`.
Next validation: next live smoke should show `prior_fake_pump_quarantine_started/refresh`, `candidate_quarantined_prior_fake_pumps`, lower warm/radar queue pressure, and no disappearance from top-growth/session ticker visibility.
```

## 2026-05-19 - P309 strict warm cap and class latency

```text
Current patch status: P309 APPLIED locally / UNKNOWN commit.
Question: before the next live run, strengthen the post-startup delivery path without flags/fallbacks and make the <=5s latency claim directly auditable.
Change: warm backlog pressure cap is reduced from 12 to 8 and the pressure keep floor from 8 to 6; warm waiting selection is score-first after immediate-danger priority. Live now writes per-class candidate latency fields for active, immediate_danger, ticker_radar, and warm_watch into symbol_batch_selected/live_cycle_summary. signal_symbol_scan_summary now includes scan_origin, origin score, immediate-danger flag, first_seen/promoted timestamps, first_seen_to_scan_ms, and promote_to_scan_ms.
Trading impact: no signal thresholds, categories, executable-entry guards, order placement, fill, stop, TP, BE, or PnL logic changed. This only tightens scheduler backlog and improves latency auditability.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`.
Next validation: next live smoke should show warm_watch_symbol_count lower, class latency fields populated, and immediate_danger/ticker_radar first_seen/promote-to-scan distributions directly measurable from signal_symbol_scan_summary.
```

## 2026-05-19 - P308 hot-waiting priority prefetch

```text
Current patch status: P308 APPLIED locally / UNKNOWN commit.
Question: post-startup live latency review of 20260519_112206 showed active and immediate-danger scans are fast, but hot waiting candidates often lack prepared subminute aggTrade coverage and fall back to REST during precise scan.
Change: after the critical scan/order path, live may prefetch due subminute aggTrade gap debt for up to 2 top waiting radar/warm symbols that are immediate-danger flow or score >= 8.0, even when the queue is not idle or latency SLA is already breached. Signal/order/position work still blocks this optional prefetch. Prefetch now ignores tiny open-tail gaps <=250ms instead of making a REST call for them.
Trading impact: no signal thresholds, categories, executable-entry guards, order placement, fill, stop, TP, BE, or PnL logic changed. This is scheduler/data-readiness only.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`.
Next validation: short live smoke; require `hot_waiting_priority_prefetch_cycle` events under queue pressure, lower `aggtrade_rest_gap_prefetch` inside hot scans, `tail_gap_ignored` for tiny open-tail gaps, and first_seen/promote->precise scan p95 closer to <=5s.
```

## 2026-05-19 - P307 live heartbeat quality marks

```text
Current patch status: P307 PROPOSED / UNKNOWN commit.
Question: make the live operator grid show whether rapidly changing connection/latency/pulse/queue values are good, warning, or bad without changing live logic.
Change: heartbeat values now include compact quality marks: `✓` good, `!` warning, `×` bad, `?` unavailable/unknown. Marks are added to stability, pulse, data source, guard status, rolling network windows, latency windows, queue, q5p95, and cycle p95.
Trading impact: none. This is display-only; no scheduler, candidate, scan, guard, order, fill, stop, Telegram, or artifact semantics are changed.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`.
Next validation: live smoke; confirm the heartbeat remains compact and marks match operator thresholds during WS/gapREST changes.
```

## 2026-05-19 - P304 immediate danger-flow precise lane

```text
Current patch status: P304 PROPOSED / UNKNOWN commit.
Question: cold symbols with extreme ticker/flow spikes wait in warm/radar queues, sometimes requiring a second observation or getting deferred/dropped under latency pressure.
Change: extreme real-flow ticker candidates now bypass ordinary warm-watch observation/defer/drop policy: first observation promotes them to radar with `danger_flow_immediate_promoted`, up to two such symbols get reserved precise slots beyond the normal adaptive radar cap, and queue pressure refuses to drop them before first precise scan. The precise scan uses `precise_immediate_danger_flow` scan_mode for audit.
Trading impact: no category thresholds, entry guard, drift/RR/stale guard, order placement, fill, stop, TP, or PnL logic changes. This only changes how fast an already-detected extreme-flow candidate reaches the existing precise signal path.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`.
Next validation: short live smoke; require `danger_flow_immediate_promoted` -> `symbol_batch_selected` scan mode `precise_immediate_danger_flow` -> `signal_symbol_scan_summary` latency under 1-2s for fresh extreme spikes, and no pressure drop before first scan.
```

## 2026-05-19 - P301 live hot-path idle-only optional work

```text
Current patch status: P301 PROPOSED / UNKNOWN commit.
Question: reduce live latency between suspicious anomaly detection and actionable precise decision without changing trading filters or order semantics.
Change: critical live cycle now selects/scans/opens first. Bounded hot-waiting aggTrade prefetch is no longer executed inside batch selection; it runs only after critical scan/open and only when the loop is idle enough. Periodic orphan-order reconcile is deferred whenever positions/opening symbols/active symbols/radar/warm candidates or latency SLA pressure are present. Precise cold coverage is hard-gated whenever active/radar/warm candidates exist. Prior fast-fade/prior-spike counting now uses exact bisect over sorted snapshot timestamps instead of repeatedly scanning the whole timestamp list.
Trading impact: no category thresholds, drift/RR/stale guards, fill handling, stop/TP placement, or TP/SL math changed. This is scheduler/optional-work latency hygiene only.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py` passed. The broader project standard command with `launcher.py` cannot run in this uploaded ZIP because `launcher.py` is absent.
Next validation: short live smoke; require `batch_select_seconds p95 < 0.25s`, `order_reconcile_status=deferred_hot_path` when queues are non-empty, `hot_waiting_prefetch_policy` not blocking due hot scans, and `inactive_cold_coverage_gate_reason=hot_candidate_queue_not_empty` during active radar/warm pressure.
```

## 2026-05-18 - P300 live potential-anomaly latency grid

```text
Current patch status: P300 PROPOSED / UNKNOWN commit.
Question: make the live operator grid show how late potential anomaly candidates are being processed.
Change: live heartbeat `Контроль` now has a second row with `Задержка p95`, `max`, and `Очередь`, using existing latency SLA due-scan p95/max samples and the current radar+warm queue count. `live_cycle_summary` also writes explicit `potential_anomaly_latency_p95_seconds`, `potential_anomaly_latency_max_seconds`, `potential_anomaly_latency_samples`, and `potential_anomaly_queue_count` aliases so the grid is auditable in CSV.
Trading impact: none; scheduler, candidate TTL/drop policy, drift/RR/stale guards, order path, fills, stops, and Telegram behavior are unchanged.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; inline heartbeat smoke confirmed the new latency row renders.
Next validation: run a short live smoke and compare terminal `Задержка p95/max/Очередь` with `live_cycle_summary.potential_anomaly_latency_*` and existing `latency_sla_*` fields.
```

## 2026-05-18 - P299 rejected market-entry audit fields

```text
Current patch status: P299 APPLIED locally / UNKNOWN commit.
Question: make backtest artifacts preserve the actual delayed market-entry price/timestamp and computed drift/RR for execution-guard rejections.
Change: `_resolve_signal_entry` now returns a small reject-audit payload for delayed market-entry guards and `simulate_long_signal` writes it into skipped rows. The patch does not change signal selection, entry thresholds, fills, stops, PnL, or latency behavior.
New skipped-row fields: rejected_market_entry_timestamp_ms, rejected_market_entry_timestamp_utc, rejected_market_entry_price, market_entry_drift_pct, market_entry_abs_drift_pct, max_market_entry_drift_pct, market_entry_rr_after_latency, min_market_rr_to_signal_tp1, signal_tp1_price, actual_market_risk_at_signal_stop.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`; synthetic `_resolve_signal_entry` market_entry_price_drift smoke confirmed rejected price/timestamp/drift audit fields.
Next validation: rerun anomaly lab with latency=true and drift 0.004; compare saved skipped rows for drift bands 0.003-0.004 before treating the looser guard as beneficial.
```

## 2026-05-18 - P298 executable entry drift guard 0.4%

```text
Current patch status: P298 PROPOSED / UNKNOWN commit.
Question: raise the live executable entry drift guard from 0.3% to 0.4%.
Change: introduce DEFAULT_EXECUTABLE_ENTRY_PRICE_DRIFT_PCT = 0.004 and use it for live max_entry_price_drift_pct plus market/latency backtest max_market_entry_drift_pct. This keeps the execution model comparable instead of loosening live only.
Queue/TTL note: active symbols expire by active_symbol_ttl_ms=60s, ticker radar watches by ticker_radar_watch_ttl_ms=120s, warm-watch entries by warm_watch_ttl_ms=10m, and pressure control can drop low-ranked/stale radar/warm candidates with explicit candidate_dropped_latency_pressure or candidate_expired_backlog_stale artifacts.
Validation: compileall passed for data/exchanges, research_tools, cli, constants.py, main.py; parser smoke shows live/lab drift defaults = 0.004. Direct cli.commands import was not used as validation because this sandbox lacks pyarrow.
Next validation: short live smoke; compare reject_entry_price_drift count and discrete_signal_snapshot_entry_missed outcomes against previous 0.003 baseline.
```

## 2026-05-18 - P297 noticed-symbol decision speed patch

```text
Current patch status: P297 APPLIED locally / UNKNOWN commit.
Question: implement the decision-speed plan from live run 20260518_090759 for coins noticed by the bot, without changing drift guard semantics.
Implemented fixed code policy with no new CLI flags: latency SLA threshold lowered from 15s to 12s; breached/pressure adaptive radar slots raised to 3; candidate pressure now keeps a smaller score-ranked queue (top keep 8, radar cap 18, warm cap 12); top-score warm symbols (score >= 8.0) can promote to the radar hot lane even while optional work is SLA-gated, with latency_sla_hot_lane_override artifacts.
Added bounded hot waiting prefetch: after batch selection, up to 3 waiting radar/warm symbols prefetch due subminute entry gap debt and emit hot_waiting_prefetch_cycle plus aggtrade_rest_gap_prefetch with hot_waiting_* reason/scan_mode. This does not fabricate coverage; pending or over-budget gaps remain explicit.
Active/opening symbols still have first priority and are never dropped by precise budget. Prescan stale decisions now emit reject_stale_decision_latency, separate from execution-guard reject_stale_signal.
Validation: compileall passed for research_tools/anomaly_micro_live.py. Broader tests pending in this turn.
Next validation: short live smoke; inspect symbol_batch_selected hot_waiting_prefetch_count, latency_sla_status, candidate_queue_* totals, radar->precise latency, reject_stale_decision_latency, and no hidden fallback in aggtrade_rest_gap_prefetch coverage_pending/backfilled rows.
```

## 2026-05-18 - P294 live startup catch-up and latency backtest grid

```text
Current patch status: P294 APPLIED locally / UNKNOWN commit.
Question: implement the safe speedups and add a backtest mode that can model live execution delay using 1s cache.
Live cache speedups:
1) startup/reprepare symbol-context backfill remains mandatory, but after the initial pass it now performs bounded catch-up passes to a target no fresher than max(15m, effective snapshot freshness). This closes the gap that can accumulate while the initial all-symbol pass is running, without skipping any symbols/TFs and without an infinite loop.
2) all live OHLCV cache flushes now use delta parquet writes, not only symbol_context_* flushes. ParquetStorage reads already merge base+delta with timestamp dedupe, so this avoids repeated full-file rewrites in normal live cycles.
Backtest latency:
`run-anomaly-lab --latency true` keeps the existing market entry candle model, then internally runs a 10s 1s-cache execution delay plus the hidden 0/7/10/15s latency grid. The 10s grid point reuses the primary latency run instead of simulating twice. The grid writes `anomaly_latency_grid_summary.csv` so latency sensitivity is visible before changing live guards.
If 1s cache is missing for a latency symbol/window, the backtest now backfills the needed 1s window from Binance futures aggTrades during simulation and persists it as delta parquet. Missing/failed backfill still remains an execution skip; no synthetic fills are fabricated.
The run now writes `anomaly_timing_summary.csv` and prints final market metrics plus per-stage timings.
Validation: compileall passed for live/backtest/CLI; run-anomaly-lab --help shows only `--latency`; synthetic latency smoke entered at next-bar-open + 10s on 1s data.
Next validation: run a targeted live-window anomaly-lab with `--latency true` and compare `anomaly_latency_grid_summary.csv` closed_trades/avg_net_return/skip_reason:market_entry_price_drift across hidden delay values.
```

## 2026-05-18 - P293 live context cache delta writes

```text
Current patch status: P293 APPLIED locally / UNKNOWN commit.
Question: why does live startup data collection take tens of minutes, and can it be fixed without skipping any required context/readiness work?
Latest live artifacts show startup context backfill is the bottleneck: 533 symbols x 2 context TF = 1066 OHLCV windows, then 1599 snapshots. Runs 20260518_051811/070057/074930 spent about 30-31 minutes before ticker radar/live cycles. Ticker radar itself was sub-second after context readiness.
The safe bottleneck is cache writing, not the context requirement: live tail updates were using ParquetStorage.save_incremental(), which reads the whole symbol/TF parquet, merges, sorts, validates, and rewrites the full file even when only a small tail was fetched. This repeats for 1000+ symbol/TF files.
P293 adds a ParquetStorage delta layer. load/load_window transparently merge base data.parquet plus delta/*.parquet with timestamp dedupe, while live symbol_context_* cache flushes write small delta parquet files instead of rewriting base parquet. Offline canonical fetchers still use full save_incremental().
Startup context readiness remains mandatory and unchanged. The patch changes cache storage mechanics and adds startup phase timings to symbol_context_startup_backfill_completed.
Expected next validation: restart live and compare symbol_context_startup_backfill_completed elapsed_seconds/fetch_phase_seconds/flush_seconds/snapshot_seconds/cache_write_storage_mode=delta against prior ~1800s baseline.
```

## 2026-05-18 - P292 live heartbeat/session-top label cleanup

```text
Current patch status: P292 APPLIED locally / UNKNOWN commit.
Operator-only cleanup after P290/P291: the heartbeat label is back to "Стабильность" while the value remains session-scoped, and the visible session-top label no longer prints "с начала сессии".
The session metric window logic is unchanged; only the operator/artifact label string is hidden to keep the live log compact.
Validation: compileall passed for research_tools/anomaly_micro_live.py; inline heartbeat smoke confirmed "Стабильность" is present and "WS сессия"/"с начала сессии" are absent.
Next validation: restart live and confirm the heartbeat shows "Стабильность" and session-top rows do not append "с начала сессии".
```

## 2026-05-18 - P291 live session metric NameError fix

```text
Current patch status: P291 APPLIED locally / UNKNOWN commit.
User live command failed during startup ticker radar validation at .output/results/live_anomaly_runs/20260518_070057 with NameError: _HOUR_MS is not defined.
Root cause: P290 used _HOUR_MS in anomaly_micro_live.py, while this module defines HOUR_MS. The failure happened before live cycles started, inside session top artifact snapshot.
Fix: replace _HOUR_MS with HOUR_MS in session top elapsed-hour and WS session sample pruning calculations.
Operator output cleanup: runner.shutdown now finishes the live status line, and run-anomaly-live calls shutdown before LiveStartupError/generic exception propagation, so errors should print on a fresh line instead of appending to a status message.
Validation: compileall passed for research_tools/anomaly_micro_live.py and cli/commands.py; anomaly continuation lab tests passed 13/13; live order lifecycle unittest passed 5/5; direct LiveSessionTopTracker snapshot smoke passed.
Next validation: restart run-anomaly-live and confirm startup passes ticker radar validation and any future exception appears on a separate terminal line.
```

## 2026-05-18 - P290 live data-readiness retry and session metrics

```text
Current patch status: P290 APPLIED locally / UNKNOWN commit.
Artifact reviewed: .output/results/live_anomaly_runs/20260518_051811.
The latest live run had 0 category_selected/position_opened, but ws_aggtrade_frame_read was fully covered: 2872 reads, connection_status=connected, missing_range_count=0, backfilled_rows=0, ws_rows=result_rows=529476. The 99.9% stability plus "Кеш REST" was mostly a label/window issue: REST there meant OHLCV cache fill, not degraded aggTrade flow.
Real skip risk found: empty setup/entry OHLCV returned normal no_signal and could mark the LTF decision closed before the cache filled. P290 makes this a retryable dependency with explicit setup_empty/entry_empty/retry_policy fields, so it does not consume the candle until data is available or the signal goes stale.
Stability is now session-scoped instead of cumulative process-since-start. live_cycle_summary keeps ws_health_pct/ws_health_observed_seconds/ws_health_healthy_seconds for the current session metric window and adds cumulative fields separately.
Session top growth no longer uses rolling 6h. It uses the same session metric baseline: Asia+Europe from Asia start, Europe from Asia+Europe start, Europe+America from Europe start, America from Europe+America start, America+Asia from America start.
Operator label cleanup: heartbeat stability value is session-scoped; OHLCV cache statuses are "OHLCV REST"/"OHLCV gap" to avoid confusing REST cache fills with flow degradation.
Validation: compileall passed for research_tools/anomaly_micro_live.py. Broader tests pending in this turn.
Next validation: next live run should show ws_health_scope=session_metric_window and no normal no_signal closeout for signal_scan_empty_ohlcv rows.
```

## 2026-05-18 - P289 live run 20260517_200159 and context label/parity cleanup

```text
Current patch status: P289 APPLIED locally / UNKNOWN commit.
Artifact reviewed: .output/results/live_anomaly_runs/20260517_200159.
The run was operationally alive from 2026-05-17 20:02Z to 2026-05-18 04:38Z. No live_internal_error/data-integrity/order error event was found; startup preflight and position cleanup were OK; live_positions.csv stayed empty because no order path was reached.
Funnel: 10425 signal_symbol_scan_summary rows, 23082 evaluated TFs, signal_count=0, category_selected=0, order_attempt=0, position_opened=0. Delayed replay processed 758 all-category-rejected cases and strict_replay_would_enter=false for all 758.
Main pre-signal rejects: reject_weak_start_flow=13043, reject_setup_too_early=8567, reject_insufficient_real_entry_buckets=66, reject_entry_below_initial_stop=170, reject_invalid_tp1_pump_leg_bottom_risk=23.
Main category bottleneck after candidates reached categories: 2274 category_rejected rows, dominated by reject_mark_basis_below_min=2062 across runner_oi_confirmed/runner_flow/runner_balanced. No category_selected happened.
Data quality improved versus the prior live run: startup context readiness was 100% after backfill and after safe live reprepare, ws_aggtrade_frame_read was active, and top-growth visibility could attribute missed movers to concrete precise/category rejects.
Residual issue: 269 retryable dependency blocks still expired on reject_prior_fast_fade_filter_unavailable, despite 320 symbol_context_snapshot_tail_refreshed events. Treat this as context freshness/contract observability to monitor, not proof of an executable missed trade.
Logical cleanup: live prior context already uses SYMBOL_CONTEXT_PRIOR_LOOKBACK_HOURS=24. P289 removes hardcoded 72ч labels/history_days from live startup/reprepare logs/artifacts and makes backtest legacy *_72h category fields use the same 24h effective window for live/backtest parity. Field names remain legacy-compatible.
Validation: compileall passed for live/backtest/CLI/tests; anomaly continuation lab tests passed 13/13; live order lifecycle unittest passed 5/5 after updating full-TP1 expectations.
Next validation: the next short live should show context 24h in terminal/Telegram/status artifacts and symbol_context_startup_backfill_* prior_context_lookback_hours=24.
```

## 2026-05-18 - targeted backtest correction for live run 20260517_200159

```text
Correction to prior wording: delayed replay showing strict_replay_would_enter=false does not mean an offline backtest over the period will find nothing.
Targeted anomaly-lab over 37 live-relevant symbols for 2d ending 2026-05-18 04:38Z found 82 signals total and 60 signals inside/post-startup live window.
Inside/post-startup live window: 60 trades rows, 16 closed non-overlap trades, all discovery family, 0 live_priority trades. Closed discovery results were weak: summed trade returns -11.6%, avg -0.73%, WR 43.8%, TP1 hit 50%.
Strict parity report for the live window: live_priority_pass=false for all 841 rows, category_parity_class was discovery_only=60 and not_in_pre_context_universe=781; strict_live_replay_enter=false for all rows. The 60 discovery-only rows had context_parity_status=oi_context_stale_asof, so they are not clean live-priority evidence.
Live saw many of the same timestamps but rejected them by the live category contract, mostly reject_mark_basis_below_min or retryable prior-fast-fade dependency timeout. This explains why live did not open although offline discovery backtest had hindsight trades.
Conclusion: do not say "backtest shows nothing" for this period. Say "targeted backtest shows only weak discovery/hindsight trades, not current live-priority executable entries."
Next validation if zero live trades persist: run a wider full-universe live-window lab or targeted strict live-priority ablation to test whether mark-basis/context dependency gates are too restrictive.
```

## 2026-05-17 - P288 live exit rule update

```text
Current patch status: P288 APPLIED locally / UNKNOWN commit.
TP1 is now a full-position target at entry + 0.75 * (entry - pump_leg_bottom). In backtest, pump_leg_bottom is decision_box_low. In live, pump_leg_bottom is the selected entry segment low.
Initial SL remains max(pump_leg_bottom - stop_buffer_range_fraction * impulse_range, EMA20). This means TP1 risk basis is intentionally different from SL risk basis.
Live actual-fill path recomputes TP1 from the actual exchange fill price and signal pump_leg_bottom, places the TP1 limit for 100% of the filled amount, and records tp1_target_basis/tp1_basis_risk/tp1_r/tp1_fraction in artifacts.
Backtest defaults are tp1_r=0.75, tp1_fraction=1.0, min_market_rr_to_signal_tp1=0.70. Live constants are LIVE_TP1_R=0.75 and LIVE_TP1_FRACTION=1.0.
Validation: compileall passed for anomaly backtest/live and CLI; defaults smoke confirmed backtest/live TP1 basis/r/fraction.
Next validation: run a short live with tiny notional and require category_selected/position_opened artifacts to show tp1_target_basis=pump_leg_bottom, tp1_r=0.75, tp1_fraction=1.0, and tp1_order_amount equal to filled position amount.
```

## 2026-05-17 - P287 exit portfolio replay tool

```text
Current patch status: P287 APPLIED locally / UNKNOWN commit.
Added research_tools/anomaly_exit_portfolio_replay.py, a standalone artifact-level no-overlap portfolio replay for saved anomaly_lab trades.
The tool reads anomaly_trades.csv and anomaly_context_parity_report.csv from each TF run, filters family/context parity, enforces max concurrent positions and same-symbol overlap policy, and writes summary/trades/reviewed/daily/top-tail CSVs.
Supported exit models include current, full_tp1, and generic tp1_<pct>_rest_<R>r with optional _be fallback.
Validation run on latest anomaly_lab with live_priority, context_parity=ok, max_positions=1, models current,tp1_25_rest_1p5r,tp1_50_rest_1p5r,full_tp1 wrote .output/results/anomaly_lab/portfolio_exit_replay.
Initial strict no-overlap readout: 105 accepted trades; current sum +187.8%, avg +1.79%; full_tp1 sum +190.7%, avg +1.82%, lower top15 share 60.3% versus current 71.3%, but slightly worse max daily drawdown.
Next validation: compare max_positions=1/2/3 and review skipped/reviewed rows before promoting an exit rule.
```

## 2026-05-17 - P286 missed-pump/category parity diagnostics

```text
Current patch status: P286 APPLIED locally / UNKNOWN commit.
Top-growth missed-pump visibility now records first/last precise-scan reject event, first/last reject reason, and top precise reject reason counts before falling back to generic precise_scanned_no_actionable_signal_or_untracked_reject.
This should make live artifacts say whether a missed pump was rejected by weak start flow, setup too early, dependency timeout, empty/fetch data issue, or another precise-stage reject before category/execution.
Backtest context parity report now includes category_parity_class, live_priority_pass, discovery_only, live_priority_reject_reason, strict_live_replay_enter, and strict_live_replay_enter_reason.
This is diagnostics/parity only; it does not loosen category thresholds or change live order execution.
Next validation: rerun a short live/top-growth audit and one anomaly-lab export, then group missed_pump_visibility.csv by not_scanned_reason/top_precise_reject_reasons and anomaly_context_parity_report.csv by category_parity_class/live_priority_reject_reason.
```

## 2026-05-17 - P285 active context suffix refresh

```text
Current patch status: P285 APPLIED locally / UNKNOWN commit.
For active/open/retryable symbols, a stale prior-fake-pump context tail no longer immediately blocks category evaluation. Live now performs a targeted levels-TF OHLCV suffix refresh through the cache-backed fetch path, recomputes that symbol's context snapshot, and re-checks the category once.
The prior fake-pump lookback used by live symbol_context_snapshot / _live_prior_fast_fade_72h is reduced from 72h to 24h. Schema names still contain 72h for compatibility, but live emits prior_context_lookback_hours=24 in the result payload.
The shared live category contract is bumped to shared_pump_category_contract_v1_live_overlay_v8 and allows max_prior_fast_fade_count_72h=2 for runner_oi_confirmed, runner_flow, runner_reclaim, runner_balanced. This means up to 2 prior fast-fade/fake-pump events in the 24h lookback are accepted.
30d broad candidate check on latest .output/results/anomaly_lab: old prior_fast_fade_72h>1 affected 23.2% of 1m/5s candidates, 15.3% of 1m/15s, 20.1% of 5m/30s. New 24h>2 would affect 16.2%, 9.2%, 12.4%. The new policy releases about 30.3%, 40.0%, 38.4% of old fast-fade rejects respectively.
Next validation: run a short live and require symbol_context_snapshot_tail_refreshed events for active/retryable stale-tail cases, fewer candidate_expired_dependency_timeout rows, and no increase in exchange/order errors.
```

## 2026-05-17 - live run 20260517_122732 artifact review

```text
Current artifact review: .output/results/live_anomaly_runs/20260517_122732/1.zip.
Current patch status: P284 APPLIED locally / UNKNOWN commit.
Live process health looks operationally OK: no live_internal_error/live_data_integrity_error, account preflight OK, startup position cleanup found zero positions, live_positions.csv is empty because no order attempt happened.
The bot did not open trades because no signal reached category_selected: 3465 signal_symbol_scan_summary rows, 7517 evaluated TFs, signal_count=0, category_selected=0, order_attempt_total=0.
Main pre-signal rejects: reject_weak_start_flow=4251 and reject_setup_too_early=2822. These are mostly early/forming setup checks and weak real trade-count expansion versus the strict min_quote/min_trade pace=4 contract.
Real data path is mostly healthy: ws_aggtrade_frame_read covered=7516/7517, cache reads filled/hit dominate, and top-growth audit saw EDEN before/during the pump with radar/warm/precise scan active.
Real issue: symbol_context_snapshot rolling updates are starved by latency SLA. Startup backfill took 2347s and was partial; after live start only 118 partial_budget updates happened while 759 snapshot cycles were skipped. This produced 344 retryable category dependency blocks and 338 dependency timeouts from symbol_context_snapshot_tail_stale.
Do not loosen category thresholds first. Fix/validate context freshness throughput and delayed replay visibility first, then decide whether the strict start-flow contract is rejecting real EDEN-like continuation too early.
P284 lets retryable/active/open symbols get a tiny priority context snapshot refresh even when optional snapshot work is gated by latency SLA. It does not loosen anomaly category thresholds and does not make ticker/warm radar tradable by itself.
Next validation: rerun live with delayed replay enabled, then inspect symbol_context_snapshot_updated latency_sla_priority_override, category_selected, signal_scan_retryable_dependency_blocked, candidate_expired_dependency_timeout, and EDEN/top-growth visibility before touching trading filters.
```

## 2026-05-17 - targeted backtest check for live window

```text
Targeted cache refill: EDEN/Q/PTB OHLCV 1m/5m and derivatives context updated cleanly to 2026-05-17 15:36Z. Historical aggTrade REST backfill over a 24h window failed for EDEN and Q with Binance 400 Internal error: 1, while PTB succeeded. Existing/materialized subminute cache was then overwritten for 5s/15s/30s.
Live-like anomaly-lab over EDEN/Q/PTB for 2026-05-16 15:36Z..2026-05-17 15:36Z found discovery-fallback trades only on PTB, not EDEN/Q.
Discovery fallback results after subminute overwrite: 1m/5s produced 26 signals / 4 closed non-overlap trades, avg net -0.68%, TP1 hit 0%; 1m/15s produced 2 closed trades, avg net -0.68%, TP1 hit 50%; 5m/30s produced 0 trades.
Live-priority category checks on 1m/15s produced 0 trades for runner_oi_confirmed, runner_flow, runner_balanced. runner_oi_confirmed/runner_balanced rejected the single post-filter candidate by mark_basis_below_min; runner_flow had no post-filter signal.
Conclusion: a backtest can show hindsight discovery trades in this window, but current live category contract would not honestly open them. This supports fixing data/context freshness and category diagnostics before loosening live categories.
```

## 2026-05-17 - P277 applied locally

```text
Current patch status: P277 APPLIED locally / UNKNOWN commit.
Backtest TP1 is no longer counted on a mere candle-high touch. `simulate_long_signal()` now labels `tp1_fill_model=conservative_limit_proxy`, requires trade-through (`high > tp1_price`) for TP1 fill, and records exact touches as `touched_not_filled_conservative`.
Same-candle TP1/SL conflict remains conservative: stop is processed first and such cases are labeled `ambiguous_intrabar_stop_first`.
The run crash `candidates missing required columns: ['mark_close_vs_decision_close_basis']` was not caused by P276; it was a pre-context universe bug. `_strip_derivative_context_requirements()` now also clears `red_flag_profile` after category overrides are already applied, so `build_anomaly_signals()` does not reintroduce mark/OI requirements before context enrichment.
Expect TP1 hit-rate, winrate, and expectancy to drop versus old candle-high backtests. Treat that as improved honesty, not strategy deterioration by itself.
Next validation: rerun the failed anomaly-lab command and inspect `anomaly_trades.csv` for `tp1_fill_model`, `tp1_fill_status`, and lower/changed TP1 hit distribution.
```

## 2026-05-17 - anomaly_lab review state

```text
Current artifact review: .output/results/anomaly_lab, pairs 1m/5s + 1m/15s + 5m/30s.
TP1 conservative fields are present in trades, so this artifact is after P277 or equivalent.
Data quality is usable: trade_count_proxy_used=false, real number_of_trades and quote_volume sources are present. Context parity is still not perfectly strict; live-priority rows include some requested_context_missing_or_bad and oi_context_stale_asof, so final live-edge claims should use context_parity_status=ok.
Live-priority categories are meaningfully better than discovery: n=490, WR 72.9%, avg +1.30%, median +0.84%, positive-day share 91.7%. Discovery remains broad/noisy: n=1946, WR 47.1%, avg +0.17%, median -0.10%, worst day -43%.
runner_oi_confirmed and runner_flow are strongest. runner_balanced is stable but more top-tail dependent. runner_reclaim is not live-ready.
Best decision-time hardening candidate is positive/high mark_close_vs_decision_close_basis, then minimum non-tiny initial risk, stronger start_range_pct_ratio_to_baseline, lower trades/quote per abs return, lower prior_spike_count_72h / prior whipsaw.
Session effect matters: Asia/EU are cleaner; US is weaker and has worse day risk; late session is sparse/tail-dependent.
Next best step: strict parity ablation on current artifacts with context_parity_status=ok and category/session/TF split before changing live category thresholds.
```

## 2026-05-17 - P279 live-first category state

```text
Current patch status: P279 APPLIED locally / UNKNOWN commit.
Default live categories are now runner_oi_confirmed, runner_flow, runner_balanced. runner_reclaim is still supported but no longer default because current artifact evidence was weak and session-sensitive.
Category contract id: shared_pump_category_contract_v1_live_overlay_v6.
New shared contract fields now enforced in both backtest and live: min_start_range_pct_ratio_to_baseline, min_initial_risk_pct, category max_initial_risk_pct, max_prior_up_down_whipsaw_to_impulse_range, max_prior_spike_count_72h.
The hardening targets decision-time runner/fader separators: positive mark basis, meaningful range expansion, non-tiny initial risk, lower prior whipsaw/spike history, and lower poor trade-effort-per-return.
Expected effect: fewer live trades, higher median/avg quality if the 30d anomaly_lab relationship survives live execution. Treat this as a live-statistics collection policy, not proof of hundreds of percent monthly account returns.
Next validation: run real live at position_notional_usdt=12 and inspect category_rejected distributions, selected categories, context dependency timeouts, actual exchange fills, TP1 limit fills, and closed-trade PnL.
```

## 2026-05-17 - P280 startup context freshness state

```text
Current patch status: P280 APPLIED locally / UNKNOWN commit.
Slow 72h startup context backfill can leave the first live cycles with a trailing context gap. This does not stale the actual signal OHLCV or exchange order path, but it can stale prior_spike/prior_fast_fade category evidence.
P280 makes that gap explicit: if decision_timestamp_ms is more than symbol_context_snapshot_fresh_ms after the snapshot effective_cache_end_timestamp_ms, live reports symbol_context_snapshot_tail_stale and blocks the category as a retryable dependency.
This is the cheap/safe fix before live collection: do not run a second full 72h startup pass; let priority rolling context refresh catch up active/radar/retryable symbols.
Next validation: after live startup, inspect signal_scan_retryable_dependency_blocked for symbol_context_snapshot_tail_stale, then confirm symbol_context_snapshot_updated priority_reason_counts includes retryable_dependency_blocked and later selected signals have fresh prior context.
```

## 2026-05-17 - P281 baseline liquidity state

```text
Current patch status: P281 APPLIED locally / UNKNOWN commit.
Current anomaly_lab review supports an absolute-liquidity floor: live-priority returns improve with higher baseline trade-count / pre_1h trade-count / pre_1h quote-volume, while extremely thin pre-volume buckets are weak.
The bot should not rely on relative start_quote_ratio/start_trade_ratio alone because one print on a low-volume symbol can create a fake x100 flow ratio.
Category contract id: shared_pump_category_contract_v1_live_overlay_v7.
Runner categories now require min_baseline_quote_daily_proxy=300k USDT/day proxy, computed from baseline_quote_volume_median and setup timeframe in both live and backtest.
Do not raise the floor to 1m yet: 300k-1m baseline daily proxy still showed positive live-priority expectancy and useful frequency.
Next validation: in live_events.csv, monitor reject_low_baseline_quote_daily_proxy counts and compare selected trades' baseline_quote_daily_proxy distribution against closed PnL.
```

## 2026-05-17 - P282 live liquidity universe state

```text
Current patch status: P282 APPLIED locally / UNKNOWN commit.
Live can now reject low-liquidity symbols before the expensive 72h context and scan loop when the universe comes from the exchange default symbol list.
The default startup/refresh gate is Binance 24h ticker quoteVolume >= 300k USDT, refreshed every 12h. Explicit --symbols bypass this universe gate so targeted tests are not silently pruned.
If the ticker request fails or the filter would empty the live universe, live keeps the previous universe and writes live_symbol_universe_liquidity_filter with refresh_failed_keep_previous or empty_keep_previous.
This is a scan/context cost and data-quality guard, not a substitute for the P281 category-level min_baseline_quote_daily_proxy filter.
Next validation: start live with position_notional_usdt=12 and inspect live_symbol_universe_liquidity_filter output_count/removed_symbols before judging signal frequency.
```

## 2026-05-17 - P283 live terminal grid state

```text
Current patch status: P283 APPLIED locally / UNKNOWN commit.
The live status grid must be a single pinned terminal block: ordinary logs/warnings clear the current grid, print the message, then repaint the latest grid as the last output.
P283 routes Python warnings through the status logger during the live loop, clears multi-line status blocks with ANSI clear-from-top-to-bottom, and removes the pandas synthetic_ohlcv_bucket FutureWarning source.
Next validation: run live in PowerShell and confirm live_symbol_universe/status updates do not leave duplicate Соединение/Рынок blocks and warning lines appear above the repainted grid.
```

## 2026-05-17 - P276 applied locally

```text
Current patch status: P276 APPLIED locally / UNKNOWN commit.
Live safety audit found one real TP1-limit transition risk: after a verified entry and initial stop, failure to create/verify the exchange-side TP1 limit closed exposure reduce-only but did not cancel the already-created initial stop.
P276 cancels that initial stop after successful reduce-only cleanup and records either position_initial_stop_cancelled_after_tp1_failure or position_initial_stop_cancel_after_tp1_failure_failed.
Lifecycle tests now match the current live contract: verified stop + verified TP1 limit on open, TP1 fill reconciliation from exchange order fill, BE stop replacement, and stop/TP1 cleanup.
Live/backtest parity remains not exact for exits: backtest still models TP1 by candle high, while live requires an exchange-side reduce-only limit fill. Treat backtest TP1 as optimistic until a parity report compares live order-fill outcomes against candle outcomes.
Next validation: run the real minimal live-order smoke after P274/P276 and require final ordinary/algo open orders = 0; then run a short strategy live smoke and inspect position_tp1_limit_order_verified, tp1_limit_exit_filled, position_tp1_limit_order_cancelled, and the new TP1-failure cleanup event absence/presence.
```

The project direction is anomaly-first: anomaly nature/category research, anomaly continuation backtests, and strict REST-only micro-live validation.

---

## 2. Active system thesis

```text
abnormal activity -> nature/category check -> controlled continuation -> executable entry -> managed exit
```

The edge is not assumed. The current task is to identify which anomaly categories are tradable, which are exhausted/fake/thin/late, and which should be rejected before expensive backtests.

---

## 3. Reliability priority

```text
data availability > leakage safety > execution realism > edge stability > parameter optimization
```

No strong conclusion about anomaly nature without real trade-count and quote-volume evidence available at decision time.

---

## 4. Current code status

```text
Active CLI:
fetch-data
update-cache
run-anomaly-lab
run-anomaly-live
run-anomaly-top-growth
run-hourly-levels
check-quality
clear-cache
```

Runtime strategy path:

```text
research_tools/anomaly_config.py
research_tools/anomaly_strategy_backtest.py
research_tools/anomaly_micro_live.py
research_tools/charting.py
research_tools/hourly_levels.py
```

---

## 5. Open risks

1. Anomaly live/backtest logic still needs robustness evaluation across months and regimes.
2. Micro-live execution has real slippage, spread, partial fills and operational failure modes.
3. OI/derivatives context availability can limit category classification.
4. The 2026-05-11 NVDA micro-live position is audit-invalid for edge/PnL: stale signal execution mixed signal close with later live order timing.
5. P156 fixes the Linux `main.py` startup blocker by importing `ctypes.windll` only on Windows.
6. Historical local artifacts may contain stale compiled files; they are ignored by git and should be deleted locally.
7. Closed-hour top-growth snapshots are now populated by default inside live through P242 incremental closed-1h exchange-candle audit. The live loop processes a bounded number of symbols per cycle and writes `top_growth_index.csv`, per-hour top/status files, and `missed_pump_visibility*.csv` from the same run's `live_events.csv`. P237/P238 session-top heartbeat remains ticker-snapshot operator UI only and is not used as a closed-hour audit fallback.
8. Active symbols whose latest closed levels candle was already scanned are now kept visible as `active_waiting_*` in batch artifacts and should not consume OHLCV scan slots until a new closed candle exists.
9. Ticker radar is scheduling-only: it can add bounded extra watch scans, but cannot remove symbols from round-robin, cannot open trades, and cannot replace closed-kline flow evidence.
10. Operator commands now live in root `COMMANDS.md`; the shared command baseline is 30 days via `DEFAULT_COMMANDS_BASE_DAYS`.
11. P167 changes live scheduling/performance, not entry filters: selected hot symbols are scanned across all due TF sets in one pass, and production-fast live can set `--inactive-scan-slots-per-cycle 0` to rely on active state plus ticker radar instead of heavy cold round-robin aggTrade work.
12. P168 changes live data access: live now reads parquet first, fetches only missing ranges, writes fetched rows with provenance, and emits cache gap events instead of treating incomplete cache as a silent no-signal condition.
13. P169 fixes live cache candle boundaries: subminute missing ranges fetch through the final candle end, parquet first-load reads only the requested window, and decision frames exclude accidental non-closed cached candles.
14. P170 buffers live cache writes so hot-loop decisions are not blocked by parquet rewrites; buffered/flushed/failed rows are explicit live artifacts.
15. P177 fixes the live OHLCV cache concat path that emitted pandas `FutureWarning` when empty cache placeholders were concatenated with fetched candles; it does not change signal or trading logic.
16. P178 proposes per-cycle raw aggTrades range reuse for S30/S15/S5 live scans and status logging as `batch/full` cycle time with local closed-trade PnL only.
17. P179 proposes deferring inactive subminute `aggTrades` scans until ticker-radar or active state, with startup refusal instead of fallback when ticker radar is unavailable.
18. P180 fixes live startup/first-cycle status artifacts after P178/P179: local closed-trade PnL is `0.0` until the first finalized closed trade, while non-finite local PnL counters still raise integrity errors.
19. P181 changes live operator status/artifact numbering only: it displays `цикл batch/full-cycle` so batch ticks are not confused with completed universe passes.
20. P185 proposes strict WS live health: subminute live refuses blind ticker-radar startup, and WS aggTrade precise scans no longer perform unbounded REST backfill by default.
21. P186 proposes event-driven WS scheduler semantics: subminute+ticker-radar live defaults to zero implicit inactive scan slots, and the operator heartbeat reports ticker/aggTrade health instead of ambiguous batch/full-cycle timing.
22. P188 proposes WS live missed-entry hardening: bounded initial aggTrade backfill is default for radar-promoted symbols, all-missing ticker radar becomes network degradation, max-position rejects remain retryable within the same decision candle, and monitor internal errors are integrity errors.
23. P189 proposes live aggTrade REST gap prefetch planning: precise active/radar symbols coalesce due S30/S15/S5 entry WS coverage gaps per symbol before signal evaluation, populate the WS buffer once, and expose prefetch/backfill/pending counts in `live_cycle_summary`. No new flags are introduced; oversized gaps remain explicit coverage-pending, not stale-entry fallback.
24. P190 proposes idempotent live order placement with deterministic client order ids and pre-stop exposure cleanup.
25. P192 proposes live account-mode preflight plus close-only startup exchange-position cleanup: unsupported hedge mode blocks startup, and any pre-existing live-universe exchange position is reduce-only closed and verified flat before the live loop.
26. P205 proposes live/backtest category parity cleanup: backtest category attribution now follows live TF priority with discovery fallback for artifacts, live does not add discovery as tradable category, and live prior-fast-fade 72h context uses levels-timeframe historical OHLCV instead of unavailable subminute entry cache.
27. P219 fixes a real live crash from run `20260514_201044`: `_live_client_order_id()` used `re` without importing it, so the first real GWEI entry attempt stopped before order submission. Fatal internal/data-integrity errors now use synchronous Telegram delivery and emit `telegram_sync_send_failed` if Telegram itself fails.
28. P220 fixes a live safety gap found during the last-15-commit review: an exception from entry order/fill resolution before `LivePosition` creation now tracks the symbol and immediately attempts reduce-only cleanup of any real unprotected exposure.
29. P221 fixes a second post-entry runtime blocker found in the live health review: `append_position()` no longer references undefined scan-mode/guard locals and the live ledger schema now includes those diagnostics.
30. P223/P224 add an opt-in idle-only delayed replay auditor: live captures category_selected/category_rejected/execution-rejected anomaly decisions into delayed_replay artifacts, processes them only when idle, recomputes frozen-decision signals from cache-only OHLCV, and can send TG when replay finds an ignored entry.
31. P225 adds a frozen LiveSignal snapshot fallback for execution-rejected runner signals when exact cache-only recomputation is blocked by intentionally disabled mark/OI exchange-context fetch.
32. P226 proposes evidence labeling for delayed replay: strict candle recompute and frozen live-signal snapshot are separated in result columns, mismatch labels, and Telegram wording so snapshot fallback is not presented as strict backtest-like proof.
33. P227-P229 harden delayed replay final-decision capture and immutable decision snapshots; P230 proposes removing the separate Telegram switch so important replay alerts follow `--delayed-replay-enabled true`.

---

## 6. Next best step

After the 2026-05-16 stop-visibility live run, do not tune entry filters first. Run the P263 fake-exchange lifecycle tests, then add narrower CCXT/Binance stop payload and order-not-found tests before changing live stop verification code.

```bash
python -m unittest tests.test_live_order_lifecycle -v
python -m compileall -q data/exchanges research_tools cli constants.py main.py tests/test_live_order_lifecycle.py
```

Expected readout: all four lifecycle tests pass. They must prove: successful entry writes `position_stop_order_verified` + `position_opened`; invisible initial stop raises `LiveOrderPositionIntegrityError` and writes `unprotected_entry_reduce_only_exit_filled`; monitor stop exit writes `stop_exit_filled` + `position_closed`; TP1 path writes `tp1_partial_exit_filled`, replaces stop to BE, cancels old stop, then closes on verified stop fill.

Latest next step after P167:

```bash
python main.py run-anomaly-live --confirm-real-orders --max-cycles 60 --symbol-batch-size 20 --inactive-scan-slots-per-cycle 0 --ticker-radar-watch-batch-size 10 --scan-hot-timeframes-per-symbol true --live-ohlcv-cache-enabled true --live-ohlcv-cache-write-enabled true --live-ohlcv-cache-flush-interval-seconds 10 --live-ohlcv-cache-max-buffer-rows 5000
```

Read `live_events.csv` first: `ticker_radar_snapshot`, `ticker_radar_promoted`, `symbol_batch_selected`, `signal_symbol_scan_summary`, `live_ohlcv_cache_read`, `live_ohlcv_cache_gap`, `live_ohlcv_cache_buffered`, and `live_ohlcv_cache_flush_summary` must prove that the loop is active/radar driven, cache-backed, fast, closed-candle aligned, and not silently skipping diagnostics.


---

## 7. Current audit note

P130 compiled and addressed the main live-execution bug, but second review found one remaining realism issue: entry drift must be absolute, not only positive. P131 fixes that and adds Telegram event notifications for stale/non-executable selected signals.

---

## 8. Current audit note — P132

P132 is proposed after P130/P131. Entry selection is no longer the only audit risk: position protection and exit accounting must also be strict. Live now treats unknown stop/fill/monitor data as explicit artifact events and integrity errors instead of temporary noise. Backtest artifacts now export skip-reason distribution so reduced trade count is explainable.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# fake-exchange smoke: stop verification failure, TP1 verified fill, repeated empty monitor OHLCV
```

---

## 9. Current audit note — P133

P133 is Telegram wording only. It does not change live trading decisions, fills, stop verification, PnL calculation, or backtest logic. Telegram becomes concise; `live_events.csv` remains the detailed source of truth for blocked-entry and integrity details.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# Telegram smoke: blocked-entry, integrity, open, close and stop-update messages render with clickable symbol links.
```

---

## 10. Current audit note — P134

P134 changes only live scheduling, not signal filters, fill verification, stop logic or PnL. The fixed `inactive_batch_with_active=7` behavior was wrong for live execution: active now means open/opening positions plus symbols with recent high-stage pump/signal state. Each cycle scans `active + (symbol_batch_size - active_count)` inactive symbols, live artifacts record why a symbol became active or left the active set, and selected signals blocked only by max-open-position capacity are not consumed until they expire or can be retried.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# smoke: mark two active symbols with symbol_batch_size=20; next batch must contain both plus 18 inactive symbols.
```


---

## 11. Current audit note — P137

P137 is diagnostics only. It repairs the partially applied hourly-level scanner: the module is restored and the CLI command is wired. It adds `run-hourly-levels` for offline/manual review of 1h overhead levels. Touches are valid only when followed by a meaningful bounce; wick-only marks and clear downtrend pseudo-levels are rejected by default.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py -h
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05
```


---

## 12. Current audit note — P138

P138 is diagnostics-only. It prevents hourly-level chart generation from spamming matplotlib missing-glyph warnings for symbols containing non-ASCII characters. CSV outputs still keep original symbols; only chart titles and file stems are converted to ASCII-safe text when needed.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05
```


---

## 13. Current audit note — P139

P139 is diagnostics-only. It adds visible progress/ETA logging to `run-hourly-levels` so long all-cache chart runs are operator-observable instead of appearing stuck after the start line. It does not change level detection rules, live trading, backtest entries, exits, stops or PnL.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05 --progress-every-symbols 5 --progress-min-seconds 5
```


---

## 14. Current audit note — P140

P140 is diagnostics-only. It makes `run-hourly-levels` closer to human chart review: repeated candles near the same price no longer count as independent touches unless price first resets away from the level, chart levels start at the first valid touch, close duplicate levels are capped, and pierced/spiked-through resistance is rejected by default.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05 --touch-tolerance-pct 0.006 --reject-pierced-levels true --max-level-pierce-pct 0.015 --max-levels-per-symbol 4
```


---

## 15. Current audit note — P141

P141 is diagnostics-only. It speeds up `run-hourly-levels` by reducing parquet payloads, trimming source candles before 1h aggregation, and replacing pandas row/copy loops in level validation with numpy scans. Use `--fast-source-trim false` only when an exact full-history aggregation comparison is needed.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05 --fast-source-trim true
```


---

## 16. Current audit note — P142

P142 is diagnostics/chart-output only. It removes the `tight_layout()` solver from hourly-level charts and uses explicit subplot margins because the chart intentionally draws price tags outside the right edge of the price panel. This removes the warning and avoids a small per-chart layout cost.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 45 --min-touches 3 --min-bounce-pct 0.05 --fast-source-trim true
```


---

## 17. Current audit note — P143

P143 is live-performance/audit cleanup. It does not relax stale-signal safety, does not change signal thresholds, and does not add dynamic universe pruning. Live scan now deduplicates OHLCV fetches by `levels_timeframe`, records stale backfilled decisions before expensive signal build as `reject_stale_signal` with `stage=prescan`, and moves top-growth collection to standalone `run-anomaly-top-growth`.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --confirm-real-orders --max-cycles 30 --symbol-batch-size 20
python main.py run-anomaly-top-growth --top-growth-min-return-pct 0.10 --top-growth-limit 5
```


---

## 18. Current audit note — P144

P144 keeps the live universe fixed and does not add dynamic pruning. It reduces repeated REST work by remembering the last closed candle scanned per symbol and levels timeframe. Active symbols remain operator-visible every cycle, but unchanged active symbols move to `active_waiting_symbols` and free their slot for inactive rotation. This should improve cold-universe traversal without weakening stale-signal guards or hiding stale/empty-data artifacts.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-lab --days 30 --symbol-batch-size 20
```


---

## Current audit note — P146

P146 is a safety cleanup for the P145 ticker-radar scheduler. It does not change strategy thresholds or execution guards. It avoids oversized ticker queries, prevents radar-waiting symbols from wasting inactive slots, and clears ticker-radar watch state once a symbol becomes a real active symbol.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-lab --days 30
```

Watch `ticker_radar_watch_cleared`, `ticker_radar_waiting_count`, `inactive_count`, `effective_scan_count`, cycle time and `ticker_radar_failed`.

---

## Current audit note — P147

P147 is operator UX/default wiring only. It adds root `COMMANDS.md` as the single copy-paste command file and centralizes the shared 30-day command baseline in `constants.py`.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py fetch-data --help
python main.py run-anomaly-lab --help
python main.py run-anomaly-live --help
python main.py run-hourly-levels --help
```

Known caveat:

```text
P156 removes the Linux `ctypes.windll` startup blocker; keep `python main.py --help` in verification.
```

Operational rule:

```text
Copy commands from COMMANDS.md; change the 30-day baseline in constants.py instead of expanding command lines.
```
---

## Current audit note — P148

P148 is live console UX only. Routine heartbeat status now updates one terminal line in-place for the default interactive console runner, while real event/error/position/Telegram failure logs first terminate that status line and remain permanent sequential logs. The old `live: цикл ...s` label is superseded by P201's prefix-free heartbeat.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

Known caveat:

```text
External/non-interactive loggers keep the old sparse heartbeat cadence to avoid writing every cycle to file-like logs.
```


---

## Current audit note — P149

P149 is diagnostics-only. `run-hourly-levels` now counts only high-based 1h touches: the candle high must be near the level, the candle body must remain below the touch band, counted touches must be separated by at least 6h, and pierced levels are rejected strictly by default. This should reduce false overhead levels drawn through candle bodies or already wicked-through highs.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 30 --min-touches 3 --min-touch-spacing-hours 6 --reject-pierced-levels true --max-level-pierce-pct 0.0
```

Known effect:

```text
Level count should drop versus P148; compare charts before treating the reduced output as a strategy signal.
```

---

## Current audit note — P150

P150 is diagnostics-only. `run-hourly-levels` now rejects a pivot/high source candle when any close in the previous 12h is above that candle high. This prevents building overhead levels from candles whose price was already accepted/reclaimed shortly before the supposed resistance touch.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-hourly-levels --source-timeframe 5m --days 30 --level-source-close-lookback-hours 12
```

Known effect:

```text
Level count can drop again versus P149; rejected cases should be those with a source high below a close from the previous 12h.
```


---

## Current audit note — P151

P151 is artifact-only. Trade charts now use three panels: top trade-window candles with execution annotations, middle independent 1h context for the last 7 fully closed days before entry with unlabeled strict hourly levels, and bottom normalized quote-volume/trade-count line curves. Trading/backtest execution logic is unchanged.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-lab --days 30
```

Known effect:

```text
The lower panel compares normalized shapes only: orange quote volume and green number_of_trades are each scaled to their own chart-window maximum.
```

---

## Current audit note — P152

P152 is live operator UX and chart-artifact only. After a verified entry fill and stop placement, live now attempts to render the canonical trade chart for the opening Telegram message. P163 supersedes the older three-panel layout: current charts are `LTF -> HTF -> volume/trades -> 1h`, the bottom 1h context spans 4 days through the candle that contains the main chart end, and chart levels are searched only inside that visible 4-day context window. Open charts replace risk/reward shaded rectangles with TP1/SL horizontal lines. Text-only Telegram remains a fallback.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

Known effect:

```text
A real opened position now performs one additional H1 context fetch and Telegram photo upload; failures are recorded as chart_render_failed, chart_context_fetch_failed, or telegram_open_chart_failed and should not block position monitoring. Any levels drawn in the 1h context panel must have been discovered from the same visible 4-day context, not older hidden history.
```

---

## Current audit note — P154

P154 is Telegram operator UI only. Symbol-specific messages now use a deterministic animal emoji derived from compact symbol text, so open/stop/close/blocked/error messages for the same symbol share one stable emoji across process restarts. Service messages use fixed `🚧`/`⚠️` icons, timeframe/context lines are monospace, and error payloads are monospace.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

Known effect:

```text
Telegram text appearance changes only; live signal, order, stop, chart, and artifact semantics are unchanged.
```

---

## Current audit note — P155

P155 is live audit-only. Network degradation/recovery, async Telegram failures, open/close Telegram photo fallbacks, and stop-cooldown signal rejects are now explicit `live_events.csv` events instead of being visible only through console output or implicit fallback behavior.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
```

Known effect:

```text
No signal/order/risk behavior changes. live_events.csv should become more verbose around Telegram delivery and temporary API/network degradation.
```

---

## Current audit note — P156

P156 is live safety/audit cleanup. Linux CLI startup no longer imports `ctypes.windll` at module import time, so `python main.py -h` can run on Linux. Live monitor no longer converts externally closed or stop-closed positions with unresolved exit fill into normal `position_closed` rows with proxy PnL. Such cases now end as `position_exit_unresolved`, write ledger `status=exit_unresolved`, keep realized PnL blank, and notify events as an integrity/audit problem.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py -h
python main.py run-anomaly-live --help
# fake-exchange smoke: exchange amount zero before monitor decision -> position_exit_unresolved, no realized PnL
# fake-exchange smoke: stop triggered but fetch_order_fill fails -> position_exit_unresolved, no realized PnL, stop cooldown recorded
```

Known effect:

```text
Closed-position counts still advance because exchange exposure is gone, but edge/PnL analysis must use only ledger rows with status=closed and verified exit fill.
```


---

## Current audit note — P158

P158 is proposed on top of P156; P157 was not applied. It changes live/backtest signal architecture from fake timeframe labels to a two-stage model:

```text
forming HTF setup from closed LTF buckets -> LTF entry permission -> live exchange guards
```

Live no longer waits for the full HTF candle to close. For `5m/30s`, the default `confirmation_candles=4` means the earliest setup decision is after 4 closed 30s buckets, about 2 minutes. HTF/forming-HTF flow remains the source of setup quality; LTF is used for entry freshness, activation hold and execution path quality, not for re-proving the whole pump thesis.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-lab --setup-timeframe 5m --entry-timeframe 30s --days 30
python main.py run-anomaly-live --help
```

Known effect:

```text
Backtest artifacts with feature_contract=htf_setup_ltf_entry_v1 are not comparable to old closed_setup_tf_v1 results. Pair-aware backtest needs cached entry timeframe data; missing 30s/15s/5s cache is a data availability problem, not a strategy result.
```

---

## Current audit note — P159

P159 supersedes the standalone P158 patch when applied as the combined HTF/LTF live-health patch on top of P156. Live keeps the intended two-stage model:

```text
forming HTF setup from closed LTF buckets -> LTF entry permission -> live exchange guards -> actual fill / verified stop
```

Fixes added after P158:

```text
forming HTF quote/trade checks use pace-normalized ratios plus a raw-progress floor
TP1 = nearest higher round market number above the old 1R TP1
transient setup/entry fetch failures no longer consume the scan slot
sub-minute pair-aware backtest requires explicit historical entry-TF cache
```

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
python main.py run-anomaly-lab --setup-timeframe 5m --entry-timeframe 30s --days 30
```

Known effect:

```text
TP1 hit-rate and RR guard behavior can change because TP1 is no longer exactly 1R. Treat P159 results as a new feature_contract-era run; do not compare PnL directly to old closed_setup_tf_v1 or raw-P158 runs.
```

---

## Current audit note — P160

P160 is cleanup-only on top of P159. It removes retired closed-HTF live signal builders and unused backtest helpers so the maintained path is unambiguous:

```text
forming HTF setup from closed LTF buckets -> LTF entry permission -> live exchange guards
```

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
python main.py run-anomaly-lab --help
```

Known effect:

```text
No trading behavior should change. If a future patch needs closed-HTF-only logic, it should be reintroduced explicitly with matching live/backtest artifacts instead of editing dead legacy methods.
```

---

## Current audit note — P161

P161 fixes a backtest runtime blocker in the market-entry execution path. `_resolve_signal_entry()` already had a module-local safe divide helper named `_safe_divide_value`, but the drift/RR guard used the stale `_safe_divide` name and crashed before skip reasons or trades could be written.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-lab --setup-timeframe 5m --entry-timeframe 30s --days 30
# launcher.py is absent in the uploaded ZIP
```

Known effect:

```text
No trading semantics should change. The previous run stopped before evaluating market-entry drift/RR guards; after P161 it should either simulate trades or emit normal skip reasons such as market_entry_price_drift / market_entry_rr_collapsed.
```

---

## Current audit note — P162

P162 fixes live terminal PnL accounting. Normal `position_closed` rows now calculate the terminal PnL leg from the verified remaining amount, not from original entry size when `remaining_amount == 0`. TP1 monitoring also treats a disappeared material residual position after a partial TP1 fill as unresolved exit evidence, not as a normal close.

Next verification:

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
python main.py run-anomaly-live --help
python main.py run-anomaly-lab --help
# synthetic: remaining_amount=0 terminal close does not add position.amount PnL
# synthetic: partial TP1 fill + exchange amount zero -> position_exit_unresolved, blank realized PnL
```

Known effect:

```text
Live PnL can decrease versus old artifacts because previously double-counted or proxy-counted terminal size is no longer included. Treat older live ledger rows around TP1/full-close residuals as audit-suspect until revalidated.
```

---

## Latest anomaly-lab readout

```text
Artifact: .output/results/anomaly_lab
Config: 1m/1m, feature_contract=closed_setup_tf_v1, 7-day window
Observed entries: 2026-05-04 13:48 UTC -> 2026-05-11 08:32 UTC
```

Verdict:

```text
1095 closed trades across 476 symbols, but aggregate edge is negative:
avg net return = -0.0760%
sum net return = -83.20%
win rate = 45.57%
positive days = 2 of 8
```

This run rejects any claim of stable edge for the tested `closed_setup_tf_v1` setup. Signal frequency is high, but expectancy, median trade, and day-level consistency are weak.

Current best next research step:

```text
P163 makes quote-volume / trade-count provenance explicit in fresh anomaly-lab exports.
Next compare the same window against the pair-aware HTF/LTF contract before tuning filters.
```

Same-window P163 comparison result:

```text
1m/1m closed_setup_tf_v1:
closed trades = 1105
avg net return = -0.0568%
median net return = -0.1794%
sum net return = -62.73%
positive days = 1 of 8

5m/1m htf_setup_ltf_entry_v1:
closed trades = 423
avg net return = -0.0521%
median net return = -0.2407%
sum net return = -22.05%
positive days = 4 of 8
```

Interpretation:

```text
HTF/LTF reduces trade count and aggregate damage, and improves day-level distribution, but it still does not produce positive expectancy. Do not claim edge. The next useful research step is not parameter tuning; first inspect why TP1/trail winners fail to overcome stop-loss mass and whether anomaly category separation can reject the losing wake-up types.
```

Correction:

```text
5m/1m was only a cheap proxy and is not an intended trading TF set. Do not use it as the trading-grid result.
```

Latest intended TF-set result:

```text
Data source: cached 1s OHLCV aggregated in memory to 30s/15s/5s entry frames.
Provenance labels: cached_1s_aggregated_to_<tf>.number_of_trades / quote_volume.

5m/30s:
closed trades = 17
avg net return = +0.1281%
sum net return = +2.18%
win rate = 52.94%
TP1 hit rate = 47.06%

1m/15s:
closed trades = 44
avg net return = +0.5832%
sum net return = +25.66%
win rate = 70.45%
TP1 hit rate = 65.91%

1m/5s:
closed trades = 37
avg net return = +0.0793%
sum net return = +2.94%
win rate = 54.05%
TP1 hit rate = 51.35%

Combined:
closed trades = 98
avg net return = +0.3140%
sum net return = +30.78%
win rate = 61.22%
TP1 hit rate = 57.14%
```

Reliability note:

```text
This is promising but not yet "big and confident plus": only 98 trades, only 3 active UTC days, and the best TF set is 1m/15s with 44 trades. Treat patterns as candidate grid hypotheses, not proven production filters.
```

Most useful observed pattern:

```text
Winners are concentrated in clean/high-retention/large-ticket setups, especially when mark is positive versus decision close. Fast-fade labels and mark-discount/flat + oi_down_price_up + clean/high-retention/large-ticket setups are weak.
```

Leakage/bottleneck status:

```text
P164 audit found no direct use of future_* / outcome / exit-result fields in executable anomaly signal filters.
Those fields are still exported for diagnostics and must stay forbidden for grid filters.
The run command used --days 7, but the 98 closed true-TF trades occurred only on 2026-05-04..2026-05-06 UTC.
That density is high enough to start cutting with red-flag hypotheses, but the filters are not proven until tested on a longer 1s-backed window.
Main runtime bottleneck is repeated full-symbol 1s parquet reads + in-memory aggregation/scanning for each TF set; materialized 5s/15s/30s caches are the highest-value speed patch.
```

P165 status:

```text
Implemented materialized 1s-derived 5s/15s/30s caches, entry-cache coverage artifacts, searchsorted LTF slicing, and explicit red-flag profiles.
The "7 day" true-TF sample is confirmed to be only 3 active executable-data days because subminute/1s coverage ends on 2026-05-06T11:40Z while the requested end was 2026-05-11T08:36Z.
Same-window cautious red flags improved combined true-TF result to 36 closed trades, avg +0.9198%, sum +33.11%, WR 83.33%, TP1 77.78%.
This is a candidate filter set, not a proven grid. Next step: fetch/repair 1s coverage through the requested end and rerun base vs cautious with unchanged thresholds.
```

Dormancy note:

```text
Current anomaly "sleep" is only a local 60 setup-candle baseline, not a 24h/72h dormancy rule.
Quick same-artifact check did not support rejecting all symbols with any recent spike; 1-4 prior same-symbol candidates in 72h were not bad.
High serial density looked weaker: prior_72h_candidates >= 5 had avg -0.0214% and WR 46.15% on 26 trades.
Next diagnostic patch should add prior_spike_count_24h/72h, prior_fast_fade_count_24h/72h and time_since_prior_spike before using this as a filter.
```

P166 14d runner-study status:

```text
Added aggTrades -> 1s backfill command, render_charts switch, and recent-spike diagnostics.
Binance futures kline endpoint cannot fetch 1s; any 1s backfill must use aggTrades.
Full 14d all-symbol 1m/5s did not complete within 1 hour, so current 5s evidence is active-symbol subset only.
Best full-universe result is 1m/15s cautious: 62 closed, avg +1.4642%, sum +90.78%, WR 80.65%, TP1 77.42%, runner 75.81%.
Runner separators in the full base sample: positive mark basis, strong mark context momentum, low quote/trade effort per return, and mid-range hold ratio.
Next step: do targeted aggTrades backfill for candidate-bearing symbols/windows, then rerun 1m/5s full without changing thresholds.
```

P171 command-contract status:

```text
`python main.py run-anomaly-lab --days N` now means the full working anomaly TF set: 5m/30s, 1m/15s, 1m/5s.
The old 1m/1m mode is no longer implicit; it only runs when explicitly requested with `--timeframe 1m` or an equivalent single-pair override.
Multi-run artifacts are separated by pair under the output directory and indexed by anomaly_lab_timeframe_runs.csv.
Next readout should compare all three per-pair summaries from the same end timestamp; do not mix old root-level 1m/1m artifacts with new per-pair outputs.
```

2026-05-13 indexed anomaly-lab readout:

```text
Analyzed .output/results/anomaly_lab indexed subdirs only: 5m_30s, 1m_15s, 1m_5s.
Closed trades: 98 across only 3 active UTC days, combined avg +0.3140%, median +0.2478%, sum +30.78%, WR 61.22%, TP1 57.14%.
Best set remains 1m/15s: 44 closed, avg +0.5832%, sum +25.66%, WR 70.45%, TP1 65.91%.
Reliability is not live-ready: entry cache coverage has symbols_covering_end=0 and entry caches end around 2026-05-11 08:35Z while requested end is 2026-05-13; no complete 7-day executable window.
Combined result becomes negative without top 10 winners, so current edge is candidate evidence, not proven stability.
Most promising live-available filter family: positive mark basis/context momentum + flow_hold>=1 + cap extreme effort/quote/trade ratios + reject serial/fast-fade recent context.
Critical live-prep item: add a paper/live shadow run that logs selected TF arbitration, cache coverage, would-enter/would-skip reasons, and exchange fillability before real order launch.
```

P172 backtest orchestration status:

```text
Default multi-TF anomaly lab no longer walks symbols separately for 5m/30s, 1m/15s and 1m/5s.
It now collects candidates in one symbol-major pass, reading each symbol/timeframe frame once and evaluating all eligible TF pairs before moving to the next symbol.
Per-pair artifacts remain separated under the same indexed output directories.
Next validation on a real run: compare candidate/signal/trade counts against the prior indexed run at a fixed end timestamp; any difference must be explained before interpreting PnL changes.
```

P173 runner-category replay status:

```text
Current .output/results/anomaly_lab is not a complete 3-TF run: 5m_30s and 1m_15s are complete, 1m_5s has no candidate/trade artifacts because the command was interrupted.
The completed run is 30 requested days with 26 active trade days, but executable subminute coverage is partial and symbols_covering_end=0, so it is not full-universe production evidence.
Baseline completed pairs: 567 closed, avg +0.5950%, median +0.3274%, sum +337.38%, WR 56.79%, TP1 54.14%, top10 dependency 42.26%.
Runner-balanced replay from existing candidates: 124 closed, avg +1.7739%, median +1.3985%, sum +219.97%, WR 80.65%, TP1 75.81%, active days 25, top10 dependency 46.56%.
Runner-balanced rules are in-sample category hypothesis: mark basis >= 10bp, quote/trade effort caps, start taker-buy delta cap, prior fast-fade 72h cap.
Next proof step: rerun runner_balanced on fixed-end complete 1s/subminute coverage including 1m/5s, then compare out-of-sample or later-window replay before live.
```

P174 OI-confirmed live-category status:

```text
OI is not optional noise. In the latest completed-pair artifacts, moderate positive OI confirmation improves the runner category, while extreme OI > 5% is too sparse and bad in-sample.
Runner-balanced + OI 3x5m > 0.3% + mark basis >= 30bp replay:
5m/30s: 15 closed, avg +2.574%, median +1.717%, sum +38.61%, WR 100.00%, TP1 100.00%
1m/15s: 21 closed, avg +3.014%, median +2.710%, sum +63.29%, WR 80.95%, TP1 76.19%
Combined: 36 closed, avg +2.831%, median +1.885%, sum +101.90%, WR 88.89%, TP1 86.11%, top10 dependency 75.58%
Live default category is now runner_oi_confirmed. Missing/stale mark or OI context is a reject, not neutral.
Residual risk: the category is selective, in-sample, missing 1m/5s, and live cannot yet fully match backtest prior_fast_fade_count_72h at startup.
```

P175 live-lag diagnostic status:

```text
Live now records first executable entry timestamp and lag from that timestamp to order submit/fill.
entered_late_vs_first_executable means fill lag >= 1 entry LTF candle.
Live also records scheduler scan gap: previous closed LTF candle seen for that symbol/TF, first unscanned decision timestamp, and skipped LTF candle count before the current detected signal.
If a selected signal has a scan gap, live starts a background missed_entry_replay_probe over the already loaded candle window and writes the first skipped LTF candle where the same live category filter would already have selected a signal.
Reject events for stale/drift/RR collapse include the same lag fields, so shadow-live can separate missed timing from bad signal quality.
The scan-gap counters add no exchange calls. The replay probe is non-blocking; for OI/mark-confirmed categories it may fetch OI/mark context in the background and must be monitored separately from order latency.
```

P176 live honesty/budget status:

```text
missed_entry_replay_probe now probes skipped LTF decisions from the earliest missed candle. If the skipped window is larger than signal_scan_backfill_candles, the event is marked probe_truncated and reports unprobed_newer_decision_count.
No-signal status in a truncated probe is no_prior_signal_found_in_probed_prefix, not a claim about the whole skipped interval.
Live exposes --signal-scan-backfill-candles so context/API budget can be reduced for scarce-limit sessions.
Residual limit risk remains: runner_oi_confirmed replay checks can request OI/mark context in the background. This is diagnostic-only but should be run with a small cap during real test live.
```

P177 live latency status:

```text
Current live evidence shows full-universe cold pass around 10-12.5 minutes, but inactive symbols are mostly deferred and cheap; expensive time is concentrated in precise_ticker_radar/precise_active scans that fetch subminute aggTrade tails.
Adding another per-symbol "cheap" 1m/5m wake-up is not clean yet because it can become a second hidden OHLCV scan path. The clean first step is attribution plus explicit precise-scan budgeting.
live_cycle_summary now records ticker_radar_seconds, batch_select_seconds, signal_scan_seconds, open_signal_seconds, order_reconcile_seconds, and cache_flush_seconds.
Live exposes max_precise_scan_symbols_per_cycle. Active symbols are not dropped; only ticker-radar watch symbols beyond the remaining budget wait and are logged.
```

P178 live universe status:

```text
Default live universe now excludes a conservative static high-cap major list without calling external market-cap sources.
This is not a hidden edge filter: live writes live_symbol_universe_filter with excluded symbols and counts.
Explicit --symbols are not filtered, so manual tests remain exact.
Risk: static high-cap exclusion introduces selection bias and should not be back-justified as data-derived cap ranking.
```

20260513_164045 live timing readout:

```text
Run had 69 cycle summaries, 563-symbol universe, and predates the high-cap exclusion patch.
No trades opened and no category_selected events; this is a latency/bottleneck sample only.
Latency split: signal_scan_seconds avg 10.161s (~62.5% of batch time), cache_flush_seconds avg 4.977s (~30.6%), ticker_radar_seconds avg 0.454s, order_reconcile occasional ~6.5s spikes.
Precise ticker-radar scans cost p50 ~2.0s, p95 ~5.7s, max 9.75s per symbol; inactive deferred scans are near-zero.
Next speed work should target subminute aggTrade tail fetch count and cache flush overhead before adding another OHLCV wake-up path.
```

P179 live speed patch status:

```text
Implemented bounded cache flush and partial aggTrade raw cache reuse.
Default live cache flush changed from 10s/5k rows to 30s/50k rows, with at most 20 symbol/timeframe shards flushed per non-forced cycle.
Forced shutdown/error/max-cycle flush still drains all pending shards.
AggTrade cycle cache now subtracts already covered raw intervals and fetches only missing time ranges, instead of requiring one cached range to cover the whole request.
Flush diagnostics now separate failed_symbol_timeframes from deferred_symbol_timeframes.
Next live timing comparison should check cache_flush_seconds, deferred_symbol_timeframes, aggtrade_network_calls, aggtrade_cache_hits, and live_ohlcv_cache_gap.
```

P180 interrupt handling status:

```text
Ctrl+C is now handled at the command wrapper and top-level main boundary.
Expected behavior: no traceback, exit code 130, short message that the command was stopped by the user.
Live's internal KeyboardInterrupt cleanup remains the preferred path when the interrupt lands inside the live loop.
```

P181 reactive rollout status:

```text
Reactive migration plan: keep anomaly decision logic synchronous; make ingestion/scheduling event-driven through narrow data-source interfaces.
Step 1 completed: ticker radar now reads through LiveTickerSnapshotSource. Default source is RestLiveTickerSnapshotSource, so behavior remains REST-backed.
ticker_radar_snapshot and ticker_radar_failed now include source id. This is the first provenance hook for future WS ticker shadow/primary mode.
Next step should be a LiveScheduler/scan-reason seam or WS ticker shadow source, not WS aggTrade trading data yet.
```

P182 reactive rollout status:

```text
Step 2 completed: current live batch selection is now represented as LiveSymbolBatchSelection.
Behavior remains rest_round_robin_scheduler with the same active/radar/inactive composition.
symbol_batch_selected now includes scheduler_source and scan_reason_by_symbol, preparing for a future event-driven scheduler without changing anomaly evaluation.
No symbols should be silently dropped by future scheduler modes; queued/waiting reasons must stay explicit.
```

P183 reactive rollout status:

```text
Step 3 completed: live ticker radar now defaults to Binance WS !ticker@arr through BinanceWsAllTickerSnapshotSource.
No REST fallback is used while live_ws_ticker_enabled=true. If WS is not ready/stale/broken, ticker_radar_failed is emitted with source=binance_ws_all_ticker and radar promotions pause.
REST ticker source remains available only through explicit --live-ws-ticker-enabled false.
This changes scheduling/wake-up transport only; anomaly signal logic, subminute candles, OI/mark, and order path remain unchanged.
Next live validation must inspect ticker_radar_failed, ticker_radar_snapshot source, radar promotion counts, and whether WS not-ready/stale causes unacceptable blind periods.
```

P184 reactive rollout status:

```text
Step 4 implemented: subminute precise scan now has a primary Binance WS aggTrade buffer for active/ticker-radar watch symbols.
REST aggTrade is no longer the primary subminute path while live_ws_aggtrade_enabled=true; it is used only as explicit missing-range backfill with ws_aggtrade_frame_read diagnostics.
WS aggregate trade id gaps are treated as data holes and must be backfilled before a requested interval is considered covered.
Local validation passed compile/smoke, but the current environment still cannot resolve fstream.binance.com, so real WS freshness/throughput is UNKNOWN until a live run on the trading host produces connected ws_aggtrade events.
Next validation should compare aggtrade_network_calls and ws_aggtrade_frame_read.status over a 10-30 minute live sample with real DNS/connectivity.
```

P187 operator heartbeat status:

```text
P187 is PROPOSED against P186 current code.
It restores a compact human live status line while keeping P186 detailed WebSocket diagnostics in artifacts.
Expected console style is superseded by P201: `5.0s · 99.4% · аномалии 104 · активно 2/6 · позиции 1/2 · PNL 4.60% · ticker ok · flow ok`.
Routine WS counters stay out of the operator heartbeat; only short WS issue suffixes are appended when attention is required.
No trading logic, signal filters, order path, fill/stop handling, or PnL accounting changes.
```

P189 ticker-radar degraded source status:

```text
Status: PROPOSED, commit UNKNOWN.
The Windows live start showed WS DNS failure for fstream.binance.com before the first cycle.
Patch P189 keeps WS ticker as primary but allows explicit REST ticker-radar degradation when the primary WS ticker source fails.
The degradation is visible through ticker_radar_primary_source_failed, ticker_radar_source_degraded, ticker_radar_snapshot.source_status=degraded_rest_fallback, and live_cycle_summary.ticker_radar_status.
No OHLCV/aggTrade full-scan fallback is restored; if both WS and REST ticker sources fail, or if all ticker snapshots are missing, live refuses/pauses instead of hiding the problem.
Next validation: run live again and inspect live_events.csv for ticker_radar_source_degraded plus nonzero ticker_radar_startup_ready.ok_count.
```

### 2026-05-13 - P190 proposed: default WS aggTrade backfill budget widened

Status: PROPOSED. Commit: UNKNOWN.

Reason: after P189, degraded REST ticker discovery can start live when WS ticker DNS is broken, but the 300000 ms aggTrade backfill cap can still miss the last fresh 5m/30s setup scan when WS aggTrade is unavailable/cold. P190 raises the default cap to 360000 ms and exposes degraded ticker status in the console line.

Validation target: inspect `live_events.csv` for `ticker_radar_source_degraded`, `ticker_radar_snapshot.source_status=degraded_rest_fallback`, `ws_aggtrade_frame_read.backfill_max_ms=360000`, low `signal_entry_ws_aggtrade_pending` count, and acceptable `signal_scan_seconds` / `aggtrade_network_calls`.


### 2026-05-13 - P191 proposed: distinguish aggTrade flow connecting vs REST degraded vs pending

Status: PROPOSED. Commit: UNKNOWN.

Reason: the operator line `WS: flow подключается` can persist for minutes when Binance aggTrade WS is down, but current logic may still evaluate entries through explicit bounded REST aggTrade backfill. The previous UI state is too ambiguous for live safety.

Validation target: after applying P191, inspect console and `live_events.csv`: `WS: flow REST` means degraded REST backfill is covering requested flow windows; `WS: flow pending` means entries can be missed; `live_cycle_summary.ws_aggtrade_effective_source` must match the console state.


### 2026-05-13 - P192 proposed: fix aiohttp WS DNS resolver path and expose DNS in operator line

Status: PROPOSED. Commit: UNKNOWN.

Reason: live artifacts showed REST ticker discovery working but both aiohttp WebSockets failing with `ClientConnectorDNSError: Could not contact DNS servers`. The next clean fix is to force aiohttp WS connections through `ThreadedResolver` / OS `getaddrinfo`, then surface the remaining DNS cause directly in the inline live status.

Validation target: after applying P192, run live and check whether `WS: ticker REST/dns` disappears. If it remains, run OS-level DNS checks for `fstream.binance.com`; code is then reporting a real local DNS/network problem rather than masking it.


## 2026-05-13 — P193 proposed: Binance futures WS market route

Current commit: UNKNOWN.

A post-P192 live smoke no longer shows DNS failures, but ticker WS remains `connected` without messages and discovery falls back to REST. The likely current root cause is stale unrouted Binance futures WebSocket URLs for market streams. Proposed P193 routes ticker and aggTrade streams through `/market`, matching Binance's migrated USD-M futures WebSocket structure.

Next check: run a short live smoke and verify ticker discovery uses `binance_ws_all_ticker`, aggTrade effective source is mostly `ws`, and REST degraded events disappear except during real transport failures.


## 2026-05-13 — P194 proposed: WS ticker startup seed and anomaly heartbeat counter

Current commit: UNKNOWN.

A post-P193 live smoke showed ticker discovery on the primary WS route, but the all-ticker cache warmed from roughly 100/525 to 524/525 over the first seconds. Proposed P194 seeds the WS ticker cache once from REST at startup with explicit `rest_startup_seed.*` labels, avoiding the initial blind spot without hiding the source. The inline heartbeat should stop using audit row count as `события` and instead show cumulative detected ticker-radar anomaly promotions.

Next check: run a short live smoke and verify `ticker_radar_startup_seeded`, low/no initial `ticker_radar_missing_fields` spam, `аномалии` in the console, and transition from `primary_seeded_rest` to primary WS after real stream updates.

## 2026-05-14 - live health 20260513_202255

Status: analyzed.

```text
Ticker discovery health is good: startup REST seed is explicit, then primary WS snapshots are ok for 525/525 symbols with no ticker failures.
Trading funnel is alive but produced no trades: 1,626 ticker promotions, 14,281 due/evaluated timeframe rows, 0 fetch failures, 612 category rejects, 0 category_selected.
Main no-trade reason is strict runner_oi_confirmed filtering, especially mark basis below 0.3% (561/612 category rejects). OI rejected only 3 cases.
aggTrade WS health is the weak point: source stayed connected, but reads were partial/stale/not_subscribed rather than covered, causing 14,278 explicit REST gap-backfill reads and 18,357 aggTrade REST network calls.
This is honest data handling, not hidden fallback, but it means the WS migration has not yet delivered the expected speed/coverage improvement.
Next patch should improve WS coverage accounting around no-trade edge intervals, id-gap holes, subscription warm ranges, and the small not_subscribed race.
```

## 2026-05-14 - P195 live health patch

Status: PROPOSED.

```text
Implemented the first fix for 20260513_202255 health issues.
aggTrade WS coverage now follows active subscription coverage instead of first/last trade edges, so no-trade edge intervals should stop triggering REST backfill.
True aggregate trade id gaps remain strict holes and still require explicit backfill.
Backfill ranges, including empty REST responses, extend coverage so the same historical pre-subscription window is not repeatedly fetched.
Command-level KeyboardInterrupt now calls runner.shutdown(reason=command_keyboard_interrupt) before the common wrapper prints the human stop message.
Next validation: run 10-30 minutes live and compare ws_aggtrade_frame_read.status covered/partial/stale/not_subscribed, aggtrade_network_calls, ws_aggtrade_backfill_reads, signal_scan_seconds, live_ohlcv_cache_gap, signal_scan_empty_ohlcv.
```

## 2026-05-14 - Backtest/live parity risk from uploaded ZIP

Status: ANALYZED. Commit: UNKNOWN.

```text
Code parity is not yet proven. Live and backtest duplicate the forming-setup signal path.
Main suspected correctness issue is backtest-side: first raw candidate inside an HTF setup consumes that setup/cooldown before later OI/mark/category/entry filters run, while live can still accept a later LTF decision in the same setup.
runner_oi_confirmed needs strict as-of freshness parity for OI and mark basis; live rejects stale context, backtest must not accept stale context under the same production profile.
Next step should be a parity harness/report over the exact latest live window after 2d cache warmup, not a generic PnL comparison.
```

## 2026-05-14 - P196 applied locally: stricter backtest/live parity

Status: PROPOSED. Commit: UNKNOWN.

```text
Backtest forming HTF from LTF collection now keeps all valid LTF decision candidates inside a setup instead of consuming the setup on the first raw pump-flow candidate.
runner_balanced now rejects stale derivatives context when mark basis is part of the profile; runner_oi_confirmed now forces oi_status=ok.
Live category_selected diagnostics now include selected OI-change and mark-basis value/status/age so live/backtest parity can compare accepted rows, not only rejects.
Expected impact: more candidate rows, potentially more late-in-setup signals, and stricter rejection of stale OI/mark context. This should make the backtest less cosmetically optimized and closer to live.
Next validation: run a 2d cache-backed parity sample after the current live run and compare live window decision/reject/select rows before treating PnL as meaningful.
```

## 2026-05-14 - P197 applied locally: broad category statistics

Status: PROPOSED. Commit: UNKNOWN.

```text
Current confirmed live-candidate category remains runner_oi_confirmed only.
Backtest now supports broad discovery while tagging each signal/trade by the strongest matching profile: runner_oi_confirmed, runner_flow, runner_reclaim, runner_balanced, or discovery.
The new anomaly_profitability_by_category.csv artifact is intended to find candidate categories without suppressing broad trades.
Next validation should inspect category counts and expectancy after the 2d parity run; do not promote a category to live until it survives wider-period robustness checks.
```

## 2026-05-14 - P198 applied locally: backtest candidate speed prescreen

Status: PROPOSED. Commit: UNKNOWN.

```text
The 30d broad run became slow because P196 correctly evaluates all LTF decision candles inside each forming setup, especially 1m/5s.
P198 adds rolling baseline medians and a cheap cumulative quote/trade flow prescreen before expensive row construction. It uses the same thresholds as the row builder and should only skip rows that would have returned None.
This is a speed optimization, not a fallback or strategy change. Remaining larger speedups should come from vectorized event detection and/or parallel symbol collection after parity is validated.
```

## 2026-05-14 - P199 applied locally: live category expansion

Status: PROPOSED. Commit: UNKNOWN.

```text
Live now defaults to runner_oi_confirmed, runner_flow, runner_reclaim, and runner_balanced; discovery remains research-only and is not traded.
Category priority is TF-specific based on the 30d broad category table.
Artifacts include category_contract=live_category_overlay_v1_no_prior_fast_fade to make the current known gap explicit: live does not yet enforce the backtest 72h prior_fast_fade exclusion.
Next validation: short shadow/live run and inspect category_selected / position_opened category ids, per-TF selected category mix, and whether expanded categories increase low-quality entries.
```

## 2026-05-14 - P200 proposed: live prior_fast_fade parity filter

Status: PROPOSED. Commit: UNKNOWN.

```text
Live now applies max_prior_fast_fade_count_72h for runner_oi_confirmed, runner_flow, runner_reclaim, and runner_balanced instead of merely documenting the gap.
The filter is computed from local cached candidate history: the 72h lookback start and internal candles must be covered, but the trailing cache lag before the live decision is ignored and exposed as ignored_tail_ms / effective_cache_end_timestamp_ms.
Unavailable filter data rejects the category with reject_prior_fast_fade_filter_unavailable; positive prior fast-fade history rejects with reject_prior_fast_fade_72h.
Next validation: short shadow/live run and inspect category_rejected/category_selected payloads for prior_fast_fade_count_72h, ignored_tail_ms, effective_cache_end_timestamp_ms, and whether serial fast-fade symbols disappear without making all categories unavailable.
```

## 2026-05-14 - P201 proposed: live WS healthy percentage

Status: PROPOSED. Commit: UNKNOWN.

```text
Live heartbeat now includes cumulative `соединение N%`, measured by wall-clock time between health samples.
A cycle is healthy only when ticker radar is primary WS and flow WS, if it has targets, is connected/subscribed without coverage pending or REST backfill.
Network-connectivity waits are sampled as unhealthy time.
Normal ticker-radar `not_due` cycles now inherit the last attempted ticker-radar health state instead of being counted as unhealthy.
`ticker_radar_status=ok` from `binance_ws_all_ticker` now maps to primary WS health; REST `ok` does not.
Expected startup bootstrap is counted as healthy for the first 180 seconds: `ticker seed` and bounded startup `flow gap REST` do not force the connection percentage to begin at 0%.
Non-bootstrap REST fallback, flow pending, subscription mismatch, and network errors still count as unhealthy connection time.
AggTrade subscription mismatch counts as unhealthy only when subscribed targets are fewer than requested targets; extra still-active old subscriptions are overhead, not a lost connection.
Heartbeat format is now `{seconds}s · {connection_pct}% · аномалии N · активно current/seen · позиции open/closed · PNL X% · {connection_status}`, with no `live` prefix.
Heartbeat metric columns are compact width 9 and right-aligned; connection status is always non-empty and left-flowing without fixed padding.
Connection status is split into healthy labels (`ticker ok`, `ticker ok · flow ok`) and degraded labels (`ticker seed`, `ticker REST`, `ticker нет`, `flow pending`, `flow REST`, `flow подписка`, `flow gap REST`).
Heartbeat single-line rendering now uses ANSI clear-line instead of padding-only carriage return, and the line is highlighted when at least one position is open.
The same health fields are written to live_cycle_summary for artifact validation.
This is diagnostics/operator UX only; trading logic is unchanged.
Next validation: short live smoke and verify heartbeat stays on one line, changes color with an open position, and live_events.csv has ws_healthy/ws_health_reason/ws_health_pct changing when ticker seed, REST fallback, flow pending, or flow REST backfill occurs.
```

## 2026-05-14 - P202 proposed: reduce live cache flush stalls

Status: PROPOSED. Commit: UNKNOWN.

```text
Longest recent live run analyzed: .output/results/live_anomaly_runs/20260514_104416.
It ran 500 cycles, 3317 observed health seconds, 0 positions.
Main safe bottleneck was synchronous parquet cache flush in the hot loop: cache_flush_seconds summed ~904s, p95 ~13.2s, max ~31.9s.
P202 lowers default non-forced live_ohlcv_cache_flush_max_symbol_timeframes from 20 to 4.
This is infrastructure/runtime only: signal selection, categories, entry/exit, stops, and forced shutdown persistence are unchanged.
Next validation: run 10-15 minutes live and compare cache_flush_seconds p90/p95, remaining_rows, pending_rows_before_flush, live_ohlcv_cache_flush_summary count, and memory pressure.
```

## 2026-05-14 - P203 proposed: live subscription/position safety hardening

Status: PROPOSED. Commit: UNKNOWN.

```text
Live code review found two correctness risks.
First, aggTrade WS subscriptions were considered active immediately after sending SUBSCRIBE, before Binance ACK; this could overstate WS coverage for newly promoted symbols or after subscription errors.
Second, position amount parsing could return zero when CCXT normalized contracts were absent/zero but Binance info.positionAmt was present.
P203 tracks pending WS subscribe/unsubscribe request ids and only marks symbols subscribed after ACK. It also uses info.positionAmt when normalized contracts are zero.
Trading thresholds, category selection, entry/exit formulas, and cache policy are unchanged.
Next validation: compile, then short live smoke and inspect ws_aggtrade_subscription_target, not_subscribed/covered transition, ws_aggtrade_frame_read partial/backfill rows, and any position amount readouts during a controlled position/order smoke.
```

## 2026-05-14 - P204 proposed: live aggTrade REST gap backfill optimization

Status: PROPOSED. Commit: UNKNOWN.

```text
Live REST aggTrade backfill now has a shared process-memory raw range cache and coalescing planner.
Both WS uncovered gaps and non-WS subminute raw fetches reuse the same 20m TTL cache.
Nearby missing ranges are coalesced and padded by 60s within the requested scan window, so one REST request can serve multiple TF consumers and later cycles.
This is data-access/runtime only: missing coverage remains explicit, max WS backfill budget still gates whether a signal is evaluated, and no strategy thresholds or execution rules changed.
Next validation: run 10-15 minutes live and compare aggtrade_network_calls, aggtrade_process_cache_hits, aggtrade_coalesced_missing_ranges, aggtrade_rest_fetched_ms, ws_aggtrade_backfill_reads, signal_scan_seconds, and signal_entry_ws_aggtrade_pending_count.
```


## 2026-05-14 - Live execution reliability state after P190 proposal

Status: PROPOSED. Commit: UNKNOWN.

```text
P190 targets the main remaining execution reliability gap: ambiguous state-changing create_order retries and pre-stop exposure cleanup.
Normal successful order placement adds no extra REST calls. Reconciliation only runs after an ambiguous transport/order-mutation failure. Validation/exchange errors remain hard failures, not network fallbacks. The next reliability gaps to reach 10/10 are startup recovery for existing exchange positions/local ledger, explicit account-mode preflight, and optional exchange-native TP protection if TP1 miss risk becomes material.
```

---

## 2026-05-14 - P206 shutdown reconcile scope

Status: PROPOSED. Commit: UNKNOWN.

```text
Ctrl+C shutdown should no longer look frozen before cleanup starts. The first interrupt records live_shutdown_started and logs the bounded forced-reconcile scope. Forced orphan-order reconciliation on shutdown/max-cycles is scoped to symbols with exchange order activity during the current live run, plus currently open/opening active symbols, instead of fetching open orders for every symbol in the live universe. This changes shutdown/runtime behavior only; signal selection, category logic, order entry, fills, stops, and exits are unchanged.
Next validation: run live, stop with one Ctrl+C, and verify live_events.csv contains live_shutdown_started and orphan_order_reconcile_started with symbols_to_check equal to the number of run-trade symbols, not the whole universe.
```

## 2026-05-14 - P207 proposed DANGER: retryable dependencies and cold coverage

Status: PROPOSED. Commit: UNKNOWN.

```text
Why 72h exists: max_prior_fast_fade_count_72h=0 is a red-flag exclusion inherited by the confirmed runner categories. It tries to avoid symbols that recently produced a fast fade after an apparent pump setup. It requires enough historical context to prove absence of those prior fast fades; after P205 this context is levels-timeframe history, not 72h of 5s/15s/30s executable tape.

P207 changes live mechanics in three places. First, taker-buy share parity now matches backtest by averaging only valid taker/share rows with quote_volume > 0 while preserving explicit invalid-data rejects when there are no valid rows. Second, temporary category data dependencies such as prior-fast-fade coverage, mark context unavailable/stale, or OI context unavailable/stale no longer consume the decision candle; live retries the same decision until it becomes stale or receives a final reject/selected category. Third, DANGER default cold coverage adds 5 precise inactive symbols per cycle for subminute live, clearly labeled in artifacts and capped by max_precise_scan_symbols_per_cycle if configured. Set inactive_scan_slots_per_cycle=0 to disable cold coverage.

Next validation: run a short dry/shadow live and inspect signal_scan_retryable_dependency_blocked counts, repeated decision timestamps until stale/final, valid_taker_share_rows/total_taker_share_rows in taker rejects, symbol_batch_selected scan modes, and API pressure metrics before any long real-order run.
```


## 2026-05-14 - P208 proposed: health-gated cold coverage

Status: PROPOSED. Commit: UNKNOWN.

```text
P208 narrows the P207 DANGER cold coverage behavior. Cold coverage now runs only when the cumulative WS health percentage is strictly above 95% and the runner has no active symbols, no opening positions, and no open positions. When health is <=95% or active/position state exists, cold coverage is gated off and artifacts record inactive_cold_coverage_gate_reason plus health/threshold fields. The old heartbeat label "DANGER обход ~Ns" meant estimated full-universe cold coverage cycle time; P208 replaces it with an explicit cold full-cycle label only when cold coverage is actually running, otherwise cold off <reason>.

Next validation: short live smoke and check symbol_batch_selected/live_cycle_summary for inactive_cold_coverage_gate_reason=ws_health_below_95pct during startup, then DANGER cold coverage only after health >95% and idle state.
```


## 2026-05-14 - P209 DANGER adaptive cold coverage controller

Status: PROPOSED. Commit: UNKNOWN.

```text
Cold coverage is now an adaptive idle/audit scanner rather than a fixed slot count. It is hard-off for open/opening positions and active-due symbols, soft-reduced by active-waiting symbols, and scored by WS health, scheduler heartbeat EWMA, and REST/cache pressure. This should improve missed-symbol/parity diagnostics during healthy idle periods without competing with active execution, but it must be monitored by cold score, selected slots, scheduler_cycle_seconds, aggtrade_network_calls, REST fetched span, and pending coverage gaps.
```

## 2026-05-14 - P210 DANGER local guard / flow radar / micro-cache metrics

Status: PROPOSED. Commit: UNKNOWN.

```text
P210 keeps discovery improvements explicit and measurable. Live entry now uses local open/opening symbol memory as the pre-entry duplicate guard instead of fetching exchange position amount before every signal; startup cleanup and post-fill exchange position verification remain the safety boundary. The all-ticker WS stream already contains trade-count and quote-volume deltas, so DANGER cheap flow radar can promote early flow-only watch symbols without REST. WS aggTrade rolling buffers are widened for active/radar/watch/current cold symbols only, not for the full universe. Cold coverage usefulness is measured by cold scanned/evaluated/retryable/signal/order counters in live_cycle_summary. Next validation: 24h live, then compare cold_before_radar_count manually from ticker_radar_promoted source, cold_signal_count/order attempts, scheduler latency, and post-fill mismatch events.
```

## P211 proposed state
- Added proposed offline DANGER experiment for runner/fader pre-pump separability.
- Current commit: UNKNOWN.
- Next check: run on 30d backtest artifacts and inspect whether pre-anomaly HTF features separate runner/fader without symbol/month leakage.

## P212 proposed state
- P211 standalone runner/fader prepump study is now proposed as a default backtest artifact integration, not only a manual script.
- Backtest artifacts should include `runner_fader_prepump_context.csv`, feature separation, label/status summaries, run config, and `runner_fader_prepump_run_status.csv`.
- Current commit: UNKNOWN.
- Next validation: run 30d backtest and inspect runner/fader feature separation before considering any live filter.

## P214 applied locally state
- Live now maintains `symbol_context_snapshot.csv` as a cache-only rolling context table.
- Prior-fast-fade category checks read the prepared snapshot instead of fetching/computing 72h context inside precise scan.
- Missing/stale snapshots remain explicit retryable category dependencies; there is no synchronous context fallback during precise scan.
- Current commit: UNKNOWN.
- Next validation: run a short dry live and inspect `symbol_context_snapshot.csv`, `symbol_context_snapshot_updated`, `symbol_context_snapshot_*` fields in `live_cycle_summary`, and `reject_prior_fast_fade_filter_unavailable` reasons.


## P215 applied locally state
- Top-growth snapshots now have a mandatory missed-pump visibility artifact shape.
- Standalone top-growth can join against a live run with `--visibility-events-csv path/to/live_events.csv` and writes radar/warm/precise/category/execution visibility columns.
- Missing or invalid live event input remains explicit in `visibility_source_status` and `not_scanned_reason`; no live visibility is fabricated.
- Current commit: UNKNOWN.
- Next validation: run top-growth for a completed hour with a real live_events.csv and inspect whether top movers show the first missing stage clearly.


## P216 applied locally state
- Live now has a latency SLA controller for optional scan expansion.
- Active and already-promoted radar precise scans remain protected; optional cold coverage is cut to zero when active/radar due-scan p95 breaches the configured SLA.
- Warm-watch candidates that reached the precise threshold are deferred with explicit `warm_watch_precise_deferred_latency_sla` events while SLA is breached, not silently dropped.
- Current commit: UNKNOWN.
- Next validation: run short dry live and inspect `latency_sla_status`, `latency_sla_due_scan_p95_seconds`, cold gate reason, and `warm_watch_deferred_count` before widening cold/warm budgets.

## P217 applied locally state
- Live can optionally use a validated P212 `runner_fader_prepump_feature_separation.csv` as a warm-watch priority scorer only.
- The scorer is disabled by default and requires an explicit profile CSV; missing or unstable profiles fail startup instead of silently disabling.
- `symbol_context_snapshot.csv` contract is now v2 and can include cache-only spot/flow prepump features for 30m/1h/2h/6h windows.
- The score is bounded/additive and only affects warm/radar priority ordering; it does not reject categories, entries, or execution.
- Current commit: UNKNOWN.
- Next validation: run a 30d backtest with P212 artifacts, enable scoring in a dry live with the feature separation CSV, then compare warm_watch_precise_promoted symbols against later top_growth without using the score as an entry filter.


## P218 proposed state
- Live context snapshot maintenance is now optional work after the critical scan path, not before batch selection/precise scan.
- Snapshot updates are SLA-gated, wall-clock budgeted, and cursor advancement is tied to processed symbols only.
- Snapshot freshness uses an effective runtime window derived from universe refresh throughput, with configured freshness as a minimum.
- Warm-watch aggTrade micro-cache subscriptions are capped by score/recency; active, opening, current batch, and promoted radar targets remain uncapped by this warm cap.
- Current commit: UNKNOWN.
- Next validation: short dry live and inspect `symbol_context_snapshot_skipped`, `symbol_context_snapshot_updated`, `ws_aggtrade_subscription_target`, and `live_cycle_summary` for budget/SLA/cap fields before changing scan budgets.

## P231 proposed state
- Live heartbeat duplication was traced to inline status rendering: long heartbeat strings can wrap in PowerShell/narrow terminals, while the logger cleared only the current physical row.
- The status logger now tracks rendered row count and clears all rows occupied by the previous heartbeat before drawing the next one.
- This is console-output-only; live trading, delayed replay, Telegram, artifacts, and scan logic are unchanged.
- Current commit: UNKNOWN.
- Next validation: run a short live/dry live in the same PowerShell terminal and confirm the heartbeat line is replaced instead of concatenated.


## P224 proposed state
- Delayed replay now performs cache-only frozen-decision recomputation instead of only artifact outcome auditing.
- It captures category selected/rejected decisions plus execution rejects and reports whether the frozen decision would select/enter under the signal builder.
- It still cannot see symbols/windows that live never scanned; those remain a separate scheduler/top-growth visibility problem.
- Replay does not fetch missing mark/OI context; unavailable exchange context is an explicit replay reject to avoid stealing live resources.
- Current commit: UNKNOWN.
- Next validation: run short dry live with `--delayed-replay-enabled true --delayed-replay-delay-seconds 60 --delayed-replay-min-idle-seconds 10`, then inspect `delayed_replay_results.csv`, `live_events.csv`, and Telegram events for any `*_replay_would_enter` mismatch.


## P226 proposed state
- Delayed replay decision recompute still uses only cached windows ending at decision_timestamp_ms; outcome windows start after decision and are labeled as post-decision.
- Frozen live-signal snapshot fallback is not equivalent to strict backtest-like recompute. P226 separates evidence with strict_recompute_signal and frozen_signal_snapshot_used.
- Telegram keeps the alert but uses different wording for snapshot fallback, with a caveat that strict candle recompute was blocked by disabled mark/OI fetch.
- Current commit: UNKNOWN.
- Next validation: short dry live with delayed replay enabled; inspect result rows where recompute_source=frozen_live_signal_snapshot and verify no *_replay_would_enter mismatch is emitted for those rows.

## P227 proposed state
- Delayed replay now queues final live decisions instead of every intermediate category-profile reject.
- A no-signal anomaly is represented by one `all_categories_rejected` case after the category loop completes, so prior profile rejects inside a later selected signal cannot create false “live ignored entry” alerts.
- Execution rejects now include duplicate/opening symbol, stop cooldown, not-yet-closed signal, and stale signal in the delayed replay queue.
- Strict cache-only recompute and frozen live-signal snapshot evidence are separated in result columns and operator alert kind.
- Current commit: UNKNOWN.
- Next validation: short dry live with delayed replay enabled; inspect that `delayed_replay_queue.jsonl` has no raw `category_rejected` source cases and that TG alerts distinguish `strict_replay_ignored_entry` from `frozen_signal_snapshot_only`.


## P228 proposed state
- Delayed replay result CSV now preserves `recompute_source`, so strict cache-only recompute rows and frozen live-signal snapshot rows can be separated without reading live_events.json details.
- Current commit: UNKNOWN.
- Next validation: short dry live with delayed replay enabled; inspect `delayed_replay_results.csv` for both `recompute_status` and `recompute_source`.

## P229 proposed state
- Delayed replay now prefers immutable live decision snapshots over later cache reconstruction when the snapshot is available.
- Snapshot replay captures baseline/setup/entry rows through `decision_timestamp_ms` plus frozen mark/OI/prior-fast-fade context, so replay can recompute without REST fetch and without depending on cache that became fuller after the live decision.
- Cache-only delayed replay remains a fallback only for older/no-snapshot cases.
- Current commit: UNKNOWN.
- Next validation: short dry live with delayed replay enabled; inspect `delayed_replay_queue.jsonl` for `decision_snapshot_json`, then verify `delayed_replay_results.csv` rows show `recompute_source=immutable_live_decision_snapshot_recompute` and `decision_snapshot_status=ok`.


## P226 proposed state
- Delayed replay decision recompute still uses only cached windows ending at decision_timestamp_ms; outcome windows start after decision and are labeled as post-decision.
- Frozen live-signal snapshot fallback is not equivalent to strict backtest-like recompute. P226 separates evidence with strict_recompute_signal and frozen_signal_snapshot_used.
- Telegram keeps the alert but uses different wording for snapshot fallback, with a caveat that strict candle recompute was blocked by disabled mark/OI fetch.
- Current commit: UNKNOWN.
- Next validation: short dry live with delayed replay enabled; inspect result rows where recompute_source=frozen_live_signal_snapshot and verify no *_replay_would_enter mismatch is emitted for those rows.


## P233 proposed state
- Live heartbeat is now a fixed-width five-line operator block with rows `LIVE`, `FEED`, `PUMP`, `RPLY`, and `RISK`.
- Delayed replay backlog is shown as `RPLY pnd <n>` or `RPLY pnd off` without changing replay logic.
- Inline status clearing counts explicit newline rows and wrapped terminal rows to avoid duplicated-looking PowerShell output.
- Current commit: UNKNOWN.
- Next validation: run a short live/dry live in PowerShell and confirm the block redraws cleanly with one blank line before it.


## P234 proposed state
- Live heartbeat is now a Russian grouped operator block: `Соединение`, `Рынок`, `Торговля`, `Контроль`.
- The block shows runtime, stability, ticker/flow state, anomaly events, active symbols, positions, orders, delayed replay backlog and coverage/guard state with fixed-width cells.
- `Replay` is shown as `Повтор`; cold coverage is shown as `Покрытие`.
- Current commit: UNKNOWN.
- Next validation: run a short live/dry live in PowerShell and confirm the multiline block redraws cleanly and does not wrap/duplicate.


## P235 proposed state
- Russian live heartbeat grouping is refined: `Рынок` shows runtime, anomaly events and active-symbol load; `Торговля` shows PNL, positions and orders.
- This is console-output-only; delayed replay, Telegram, artifacts, scan and order logic are unchanged.
- Current commit: UNKNOWN.
- Next validation: run a short live/dry live in PowerShell and confirm the block redraws cleanly with the new grouping.

## P236 proposed state
- Live retry warnings from the exchange client are routed through the live status logger, so they terminate the inline heartbeat before printing and appear as highlighted alert lines instead of being glued to the status block.
- `ExchangeConnectivityError` handling now prints the network/API failure as a highlighted alert with a blank line before it, records the first degraded reason, and keeps retrying the Telegram degraded alert every 60 seconds while the outage persists.
- On recovery, live writes `network_recovered` with the original reason and sends a Telegram recovery note, so a DNS-wide outage that also blocks Telegram still leaves a later operator notification path.
- Current commit: UNKNOWN.
- Next validation: run a short live smoke with blocked DNS/API and confirm console separation, `network_degraded_telegram_alert_enqueued`, `telegram_async_send_failed` if Telegram is unreachable, and Telegram delivery/recovery notification once connectivity returns.

## 2026-05-15 — P245 proposed

P245 addresses the live backlog loop directly: radar/warm-watch candidates are no longer allowed to accumulate indefinitely and keep latency SLA breached. When the queue is under pressure, live keeps the strongest candidates, expires stale backlog, and drops weak tail with explicit `candidate_dropped_latency_pressure` / `candidate_expired_backlog_stale` events.

No execution safety was loosened: stale, drift, RR, actual-risk, position, fill and stop guards are unchanged. No new CLI flags were added; queue limits are code-reviewed policy constants.

Current commit: UNKNOWN.
Next validation: run a short live smoke and inspect `candidate_queue_*` fields in `live_cycle_summary` / `symbol_batch_selected`, plus `candidate_dropped_latency_pressure`, `candidate_expired_backlog_stale`, `warm_watch_precise_deferred_latency_sla`, and `latency_sla_status` counts.

## 2026-05-15 — P246 proposed

P246 adds an internal adaptive precise-scan budget on top of P245. During latency/backlog/runtime pressure, active/opening symbols keep priority and radar precise scans are capped to the strongest fresh candidates instead of scanning a wider stale queue.

No execution guards are loosened. No new CLI flags are introduced. Current commit: UNKNOWN.

Next validation: run a short live smoke and compare `adaptive_precise_budget_status`, `adaptive_precise_budget_radar_slots`, `latency_sla_status`, `warm_watch_precise_deferred_latency_sla`, and `candidate_queue_*` versus the previous 5h run.

## 2026-05-15 — P239 proposed

Live prior-fast-fade context must not depend only on a post-scan optional snapshot. P239 proposes default startup backfill + startup snapshot computation for 72h+baseline levels-timeframe context, with explicit tiny-gap tolerance (`min_coverage_ratio=0.995`, `max_gap_candles=2`) and no subminute context backfill. Commit: UNKNOWN.
---

## 2026-05-15 — P240 proposed

After P239, unavailable prior-fast-fade context should be rare, but it must not be treated as a market/category rejection when it still happens. P240 keeps such decisions retryable: no `category_rejected` artifact and no delayed-replay final-reject case are emitted for retryable category dependencies; `signal_scan_retryable_dependency_blocked` carries the blocked categories and context reason.

Next check: run a short live smoke and verify that `reject_prior_fast_fade_filter_unavailable` no longer dominates `category_rejected`; remaining unavailable context appears as retryable dependency until stale/expiry.


## 2026-05-15 — P241 proposed

Rolling symbol-context snapshots now prioritize symbols that can unblock near-term decisions: open/active symbols first, then retryable dependency requests, then ticker-radar/warm-watch symbols, then normal universe round-robin. This keeps live context maintenance cache-only but stops spending the tiny post-scan budget mostly on random inactive symbols while a hot symbol is waiting for prior-fast-fade context.

Current commit: UNKNOWN.
Next validation: after P239-P241, run a short live smoke and inspect `symbol_context_snapshot_updated.priority_reason_counts`, `signal_scan_retryable_dependency_blocked`, and whether repeated prior-fast-fade unavailable cases for the same hot symbols resolve before stale/expiry.


## 2026-05-15 — P242 proposed

Live closed-hour top-growth/missed-pump visibility is no longer only a standalone post-run command. P242 proposes a default-on incremental live audit that scans closed 1h exchange candles in bounded per-cycle chunks and writes top/status/visibility artifacts into the current run directory using the same `live_events.csv` as visibility evidence.

Current commit: UNKNOWN.
Next validation: run live across at least one UTC hour close; verify `top_growth_index.csv` gets a row, `top_growth_status_*.csv` contains all live-universe symbols, and `missed_pump_visibility*.csv` is populated when top movers cross the threshold.

## 2026-05-15 — P243 proposed

P242 needed one correction before live smoke: closed-hour top-growth should not add new CLI flags or run during latency pressure. P243 makes the audit always-on, fixed-policy and latency-gated: no `--live-top-growth-*` options, no ticker fallback, no processing while optional scans are blocked by SLA.

Current commit: UNKNOWN.
Next validation: run live across an hour close and verify `live_cycle_summary.live_top_growth_status=skipped_latency_sla` during pressure, then `processing/completed` only when optional work is allowed.

## 2026-05-15 — P244 proposed

P244 loosens only discovery/market-shape gates and data-dependency handling, not execution safety. Live defaults become less likely to reject borderline early pump candidates: start flow 4x/4x, retention 65%, verticality 0.20, prior whipsaw cap 0.75, runner prior-fast-fade cap 1, taker-buy delta cap 0.35, and slightly lower runner mark/OI thresholds. `max_entry_price_drift_pct` remains 0.003.

Unavailable taker-buy, mark-basis, and OI context are no longer final category rejects; they are retryable dependencies until context appears or the decision becomes stale. P239 startup-context switches are removed from CLI/config plumbing so startup context backfill is default-on policy rather than an operator toggle.

Current commit: UNKNOWN.
Next validation: run a short live smoke and inspect that category_rejected is dominated by real market reasons, while `signal_scan_retryable_dependency_blocked` carries data-context issues without consuming decisions.

## 2026-05-16 — P252 proposed

P252 fixes a runtime `NameError: selection is not defined` in `live_cycle_summary`: P245/P246 wrote candidate queue/adaptive precise fields using the local `selection` variable after the batch-selection scope had ended. The patch stores those values on runner state when the batch is selected and uses the state fields in cycle summary. It also adds current local `HH:MM:SS` to the inline `контекст 72ч` startup backfill status line.

Current commit: UNKNOWN.
Next validation: restart live and verify startup status updates as `контекст 72ч · HH:MM:SS · кеш N/total · SYMBOL · ETA ...`, then confirm the first `live_cycle_summary` writes without NameError.

## 2026-05-16 — P253 proposed startup visibility note

Observed live startup can sit silently after `контекст 72ч · кеш 535/535` because the main heartbeat starts only after startup cache flush, symbol-context snapshot computation/write, ticker radar seed/validation, and entry into the first live loop. P253 proposes status-only instrumentation for those startup stages. Current commit: UNKNOWN.

## 2026-05-16 — P256 proposed startup visibility note

The startup `контекст 72ч · запись кеша` stage was observed to look stalled after the 72h fetch loop completed. P256 keeps the blocking healthy-start policy, but reports forced cache flush progress per symbol/timeframe with ETA and adds ETA to startup snapshot computation/readiness status. Current commit: UNKNOWN. Next validation: restart live and confirm `запись кеша N/total · SYMBOL TF · ETA ...` updates during the formerly silent flush stage.


## 2026-05-16 — P257 proposed state

- 72h context preparation keeps the healthy-start policy but flushes OHLCV cache writes in bounded chunks during backfill instead of accumulating one large final write.
- Live context reprepare is state-based and safe-gated: it can run only with zero active symbols, zero open positions, zero opening symbols, and zero tracked order-reconcile symbols. Unsafe cases are deferred with explicit artifacts.
- If real-orders context remains below readiness thresholds after a safe reprepare, live stops safely rather than continuing with degraded context.
- Current commit: UNKNOWN.
- Next validation: restart live and verify startup `запись кеша` appears in small chunks during backfill; during a long run, inspect `live_context_reprepare_deferred/started/completed` events only when context readiness degrades.


## 2026-05-15 — P247 proposed

P247 adds dependency retry cooldown on top of P245/P246. Retryable data-dependency blocks no longer re-enter precise scan every cycle while waiting for context/taker/mark/OI evidence. Cooldown symbols are prioritized by the context snapshot refresher, due-scan latency ignores intentional cooldown waits, and stale timeout emits `candidate_expired_dependency_timeout` instead of silently looping.

No execution guards are loosened. No new CLI flags are introduced. Current commit: UNKNOWN.

Next validation: run a short live smoke and inspect `signal_scan_dependency_retry_scheduled`, `candidate_expired_dependency_timeout`, `dependency_retry_cooldown_*`, `signal_scan_retryable_dependency_blocked`, `latency_sla_status`, and `warm_watch_precise_deferred_latency_sla`.


## 2026-05-15 — P248 proposed

P248 makes live initial-risk rejects explicit: `reject_entry_below_initial_stop` now records whether the computed stop came from EMA20, the structural low-based stop, or a tie, plus the stop-above-entry distance. It does not change stop calculation or allow any fallback stop substitution.

Current commit: UNKNOWN.
Next validation: run a live smoke and inspect `reject_entry_below_initial_stop` rows for `stop_source`, `risk_side`, `previous_stop`, `decision_ema20`, and `stop_above_entry_pct`.

## 2026-05-15 — P249 proposed

Review after P245-P248 found one artifact-accounting issue in P247: active dependency retry cooldowns were skipped before the local scan-summary cooldown counter, so a cooldown wait could appear as generic `skipped_not_due_count`. P249 corrects the summary classification so cooldown pressure is measurable as `dependency_retry_cooldown_skipped_count`.

Current commit: UNKNOWN.
Next validation: in the next live smoke, check that repeated dependency waits increase `dependency_retry_cooldown_skipped_count` / `dependency_retry_cooldown_skipped_cycle` rather than hiding under `skipped_not_due_count`.

## 2026-05-15 — P250 proposed

P250 changes only the operator display for default-on 72h startup context backfill. Instead of logging `контекст 72ч · кеш 1/535 · ok ... · ошибки ...` and then staying visually quiet until every 50 symbols, live now refreshes the status line for every symbol with the current symbol and ETA. Fetch failures still go to `live_events.csv`; the status line no longer shows misleading ok/error counters.

Current commit: UNKNOWN.
Next validation: start live with a cold/partial cache and verify the single startup line updates on every symbol as `контекст 72ч · кеш N/total · SYMBOL · ETA ...`.


## 2026-05-16 — P258 proposed state

- Mandatory 72h context preparation/reprepare now has Telegram start and finish notifications in addition to terminal/artifact events.
- Notifications are events-channel only and do not affect readiness decisions, cache writes, trading filters, or live execution safety.
- Current commit: UNKNOWN.
- Next validation: restart live and confirm Telegram shows `Контекст 72ч: подготовка включена` before the 72h context phase and `Контекст 72ч готов` or `Контекст 72ч не готов` after the readiness verdict.

## 2026-05-16 — P259 proposed

P259 adjusts only the operator heartbeat display. The connection block now shows stability, pulse, and data health; runtime moved back to the market block and is counted from live-loop start after mandatory 72h context preparation, not from process startup. Session top movers are rendered to 0.1%. The live status logger now clears the previous multi-line heartbeat before warnings/ordinary log messages, so an error does not leave a stale grid above the alert.

Current commit: UNKNOWN.
Next validation: run live until the first warning/retry message and verify the old heartbeat is cleared before the alert, then the next heartbeat renders once.

## 2026-05-16 — P261 proposed

P261 tightens live stop verification after the SKYAI stop visibility halt. Stops are still not trusted from the create response: verification checks open orders by order id/clientOrderId, then uses the typed exchange clientOrderId lookup before declaring the stop confirmed. Stop integrity failures now carry the affected symbol into terminal log, `live_data_integrity_error` artifact row, and synchronous Telegram halt notification.

Current GitHub head checked before patch: c0b5dfd970dabc5383cd6f493a688f41e29ba517.
Current patch status: APPLIED in GitHub head c0b5dfd970dabc5383cd6f493a688f41e29ba517.
Next validation: run a synthetic stop-verification smoke for delayed open-orders visibility, clientOrderId-only lookup confirmation, and unresolved stop halt.

## 2026-05-16 — P262 proposed

P262 adds an explicit dangerous diagnostic flag `--danger-continue-after-order-position-errors`. Default live behavior remains strict. With the flag enabled, order/position integrity failures are written to `live_order_position_integrity_error`, reported synchronously to Telegram, live cache is flushed when inside the loop, and startup/live execution continues instead of returning code 3 where possible.

Current GitHub head checked before patch: c0b5dfd970dabc5383cd6f493a688f41e29ba517.
Current patch status: PROPOSED / not applied.
Next validation: run one strict synthetic stop/order failure and one danger-mode synthetic failure to verify strict halt vs Telegram+continue behavior.

---

## 2026-05-16 — Proposed parity state after P264

```text
Current patch status: P264 APPLIED locally / UNKNOWN commit.
Backtest PnL before P264 is not live-category parity-valid because runner category thresholds were duplicated and different from live.
After P264, runner category thresholds/priority must come from research_tools/anomaly_category_contract.py in both live and backtest.
Discovery remains allowed only as an explicit backtest fallback and must be separable through pump_category_family=discovery vs live_priority.
Delayed replay must preserve the original live setup_source from immutable snapshots to avoid changing forming-HTF pace ratios during replay.
```

## 2026-05-16 — Proposed parity state after P266

```text
Current patch status: P266 APPLIED locally / UNKNOWN commit.
P266 keeps discovery as the backtest fallback but makes skipped rows carry the same category metadata as closed rows.
Backtest derivatives context fetching is widened before final category checks so runner candidates are not excluded from mark/OI enrichment simply because mark/OI was not loaded yet.
`anomaly_context_parity_report.csv` becomes the first artifact to inspect when a live-selected timestamp is missing from backtest, has `outside_pre_context_signal_universe`, or loses category metadata on an execution guard skip.
```

## 2026-05-16 — P265 proposed

P265 adds live observability for discrete signal misses. If the signal snapshot itself was executable, but the current live executable price fails the execution guard (`TP1 already reached`, price drift, invalid risk, wide risk, or collapsed RR), live still rejects the order but records `discrete_signal_snapshot_entry_missed` and sends a specific Telegram message.

Current patch status: APPLIED locally / UNKNOWN commit.
Next validation: run a short live smoke and count `discrete_signal_snapshot_entry_missed` versus normal execution rejects. If this dominates, evaluate lower-latency partial-candle/event-driven research separately; do not open by stale snapshot price.

## 2026-05-16 — P267 proposed

```text
Current patch status: P267 APPLIED locally / UNKNOWN commit.
P267 is a data-quality/parity patch only. It does not change discovery selection, runner thresholds, stop-order handling, or PnL logic.
Delayed replay snapshots must preserve `synthetic_ohlcv_bucket` so replay does not turn synthetic no-trade buckets into real flow evidence.
Live/replay setup confirmation must require enough real entry buckets; synthetic filled buckets may maintain time/price continuity but must not satisfy confirmation count.
Backtest context parity now reports OI cache/load/asof freshness separately from mark-price context so SYS-like disagreements can be traced to cache coverage/staleness instead of a generic context failure.
P263 lifecycle tests are referenced in research memory but `tests/` is absent from the current uploaded ZIP; treat P263 validation as not present in this ZIP unless tests are supplied separately.
```

## 2026-05-16 — P268 proposed

```text
Current patch status: P268 APPLIED locally / UNKNOWN commit.
P268 adds a real Binance USD-M order lifecycle smoke command. It is not a strategy signal and does not weaken live execution safety.
The command requires `--confirm-real-order-smoke`, refuses non-flat symbols or pre-existing open orders, opens one minimal market long, verifies the reduce-only STOP_MARKET via open orders/clientOrderId lookup, then cancels the stop and closes reduce-only by default.
Binance `-2013 Order does not exist` is now classified as `ExchangeOrderNotFound`, not as connectivity retry exhaustion, so stop visibility diagnostics can distinguish “order absent” from network/API failure.
Artifacts: `live_order_smoke_events.csv` and `live_order_smoke_summary.json` under the selected output directory.
```
## 2026-05-16 — P269 proposed

```text
Current patch status: P269 APPLIED locally / UNKNOWN commit.
The real Binance smoke showed a UI-visible protective stop that ordinary `fetch_open_orders`/`fetch_order`/`cancel_order` could not see or cancel.
Treat Binance protective stops as conditional/algo orders: create through `fapiPrivatePostAlgoOrder`, verify through `fapiPrivateGetOpenAlgoOrders`, and cancel through `fapiPrivateDeleteAlgoOrder`. Ordinary order lookup remains only a legacy secondary path.
After P269, the next real-order smoke must prove: `stop_verified.source` is `open_algo_orders_*` or `algo_client_order_id_lookup`, `stop_cancelled` succeeds, and final snapshot has both `smoke_open_orders_seen=0` and `smoke_algo_open_orders_seen=0`.
```

## 2026-05-16 — P270 proposed

```text
Current patch status: P270 APPLIED locally / UNKNOWN commit.
P270 extends the existing real-order smoke with an optional management lifecycle instead of adding strategy behavior: initial algo stop -> closer replacement algo stop -> old stop cancel verification -> reduce-only close while replacement stop remains active -> replacement stop cancel -> final ordinary/algo order sweep.
The old quick smoke remains unchanged unless `--replacement-stop-distance-pct` is supplied.
Next validation: run the management smoke at minimal notional and inspect `live_order_smoke_events.csv` for `stop_replacement_verified`, `old_still_open=false`, `new_still_open=true` before close, and final `smoke_algo_open_orders_seen=0`.
```
## 2026-05-16 — P271 proposed

```text
Current patch status: P271 APPLIED locally / UNKNOWN commit.
The first P270 management smoke failed before replacement because stop verification compared the raw requested floating stop price to Binance's normalized algo trigger price strictly enough to reject one valid price-precision truncation: 0.035643999999999995 -> 0.03564.
P271 keeps strict stop verification but compares trigger price with exchange-normalized precision tolerance instead of treating one displayed price unit as integrity failure.
Next validation: rerun the same management smoke and require `stop_verified`, `stop_replacement_verified`, old stop removed, replacement stop present until close, then final flat/no ordinary or algo orders.
```

## 2026-05-16 — P272 proposed

```text
Current patch status: P272 PROPOSED, commit UNKNOWN.
P271 management smoke passed the real Binance lifecycle, but the smoke summary artifact reused `_stop_order_id` after replacement and therefore reported the replacement id as `stop_order_id`.
P272 is artifact-only: `stop_order_id` remains the initial protective stop id, `replacement_stop_order_id` remains the replacement id, and `active_stop_order_id` records the last/current managed stop. No order placement, verification, cancellation, or strategy behavior changes.
Next validation: rerun compile/unit/help checks, then on the next management smoke confirm the summary ids match the event stream.
```

## 2026-05-17 — P274 proposed

```text
Current patch status: P274 PROPOSED, commit UNKNOWN.
Position management now treats TP1 as an exchange-side reduce-only limit order, not a candle-high-triggered market close.
The live monitor uses the signal entry timeframe for structural trailing and treats the pre-first-closed-LTF-candle interval as waiting, not data integrity failure.
Next validation must be a minimal real-order lifecycle smoke that verifies: stop order visible, TP1 limit order visible, TP1 cancel/cleanup works when position exits before TP1, and no ordinary/algo orphan orders remain.
```

## 2026-05-17 — P275 proposed

```text
Current patch status: P275 PROPOSED, commit UNKNOWN.
The live terminal heartbeat should behave as one pinned multi-section status grid. When any ordinary log or alert appears, the runner clears the grid, prints the message, and immediately repaints the latest grid so the operator view always ends with the current heartbeat. This is display-only and does not affect live execution, artifacts, orders, or Telegram.
Next validation: run a short live session in an interactive PowerShell terminal and confirm one self-updating grid containing Соединение/Рынок/Торговля/Контроль stays at the bottom after startup logs and any warning/error lines.
```

## 2026-05-19 — P303 proposed

```text
Current patch status: P303 PROPOSED, commit UNKNOWN.
The 20260519_064747 live run closed BAS with exchange position amount zero but wrote exit_unresolved because the monitor finalized unresolved before trying known TP1/stop order fill recovery. P303 keeps the strict no-synthetic-PnL rule but attempts exchange-fill recovery first.
Operator heartbeat Orders previously displayed orphan cancel delta, not active protection order count; P303 changes it to a cheap local protective-order count.
Next validation: run a short real-order live/smoke and require entry_order_submit_started -> entry_fill_verified -> stop/TP verified, and if position becomes flat externally, either position_external_exit_fill_recovered -> position_closed or a detailed position_external_exit_fill_recovery_failed -> position_exit_unresolved.
```

## 2026-05-19 — P305 proposed

```text
Current patch status: P305 PROPOSED, commit UNKNOWN.
P305 targets the post-P304 bottleneck seen in 20260519_084702: repeated scans of the same weak-flow/mark-basis rejects and optional top-growth/context/cache work running while hot candidates exist.
It adds a hard hot-idle policy for optional work and a symbol-level reject cooldown after repeated weak-flow or mark-basis rejects. Immediate danger-flow precise scans bypass this cooldown so a fresh extreme spike is not suppressed.
Next validation: run live after P304+P305 and require live_cycle_summary to show top_growth/context skipped_hot_path while queue>0, cache_flush skipped_hot_path except emergency, reject_cooldown_started/skipped metrics for repeated weak rejects, and no drop in immediate_danger_flow scan coverage.
```

## 2026-05-19 — P306 proposed

```text
Current patch status: P306 PROPOSED, commit UNKNOWN.
Short live screens can show latency jumping from ~1s to ~19s because the heartbeat previously displayed a single current SLA sample/max without rolling context. P306 adds rolling 1m/5m/15m/run quality windows to the heartbeat and artifacts, so a bad spike can be separated from sustained network/queue degradation.
Next validation: run live for 20-30 minutes and inspect live_quality_window_summary plus live_cycle_summary quality_* fields. A good run should have 5m/15m WS health stable >95-98%, 5m latency p95 near target, and isolated max spikes visible without making the whole run look broken.
```

## 2026-05-19 live OI guard follow-up

- Current head: UNKNOWN (ZIP snapshot, no git metadata).
- BAS live entry showed runner_flow can accept taker-buy/price spikes while OI context is weak/negative.
- Next patch status: P302 mandatory live current-OI short-cover guard proposed, no runtime flags.

## 2026-05-19 - P311 live2 v0 runtime skeleton

```text
Current patch status: P311 PROPOSED / UNKNOWN commit.
Question: start a separate `run-anomaly-live2` runtime next to the existing live without copying the old monolith or adding shadow/dry-run semantics.
Change: add `research_tools/anomaly_live2/` with typed config/contracts/state/artifact writer/runner, register CLI command `run-anomaly-live2`, and create isolated artifacts under `.output/results/live2_anomaly_runs/<run_id>/`. Generation 0 keeps market-data, signal, exchange boundary, position supervisor, and execution as explicit `todo_not_implemented` readiness gates; `new_entries_allowed=false`.
Trading impact: no real orders, signal thresholds, category math, guard logic, existing live1 scheduler, fills, stops, TP, BE, or PnL are changed. This is the first live2 runtime shell only; the command name is real-live oriented, but order placement is intentionally not implemented yet.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`.
Next validation: run `python main.py run-anomaly-live2`, stop with Ctrl+C, and confirm `live2_events.csv`, `live2_status.json`, and `live2_symbol_state.csv` are created with TODO readiness gates and no order attempts.
```


## 2026-05-19 - P312 live2 all-ticker WS state ingestion

```text
Current patch status: P312 PROPOSED / UNKNOWN commit.
Question: add the next live2 layer after the runtime skeleton, following the deadline-driven/no-queue plan.
Change: `run-anomaly-live2` now starts Binance futures `!ticker@arr` WS ingestion and mutates one `SymbolState` per configured symbol, or per Binance market id when no explicit symbols are passed. State records now expose ticker market id, first/last seen timestamps, update count, last price, 24h quote volume, 24h trade count, 24h price-change pct, source, status, and reason. Artifacts include ticker status counts and market-data status.
Trading impact: no real orders, signal decisions, category thresholds, execution guards, fills, stops, TP, BE, or live1 behavior are changed. `market_data_ready_for_entries=false` remains mandatory because aggTrade/candle coverage is still TODO; new entries remain forbidden.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`.
Next validation: run `python main.py run-anomaly-live2 --symbols BTC/USDT:USDT ETH/USDT:USDT` for 30-60 seconds and confirm `ticker_ws_startup_status`, `live2_heartbeat.market_data_status.ticker_ws.ready`, and `live2_symbol_state.csv` ticker fields update without any candidate queue/drop fields.
```


## 2026-05-19 - P313 live2 aggTrade WS candle rings

```text
Current patch status: P313 PROPOSED / UNKNOWN commit.
Question: add the next live2 layer after all-ticker state ingestion, prioritizing speed and reliability.
Change: `run-anomaly-live2` now starts Binance futures combined aggTrade WS shards for explicit `--symbols`, normalizes each trade, and mutates the same per-symbol `SymbolState` with real trade flow plus in-memory 5s/15s/30s/1m candle rings. Missing buckets are counted as gaps; they are not synthetic-filled. Artifacts expose aggTrade status counts, candle coverage counts, shard status, and candle summaries in `live2_symbol_state.csv`/`live2_status.json`.
Trading impact: no real orders, signal decisions, category thresholds, execution guards, fills, stops, TP, BE, or live1 behavior are changed. `market_data_ready_for_entries=false` remains because signal/execution are still TODO; no entry is allowed.
Important limitation: P313 does not invent a live2 universe manager. Until that exists, aggTrade coverage requires explicit `--symbols`; no-symbol mode remains ticker-only and writes an explicit reason.
Validation: `python -m compileall -q data/exchanges research_tools cli constants.py main.py`.
Next validation: run `python main.py run-anomaly-live2 --symbols BTC/USDT:USDT ETH/USDT:USDT` for 60-120 seconds and confirm aggTrade shard readiness, nonzero aggtrade_update_count, 5s/15s closed candle counts, and zero hot REST/backfill fields.
```

## 2026-05-19 — P316 proposed

```text
Current patch status: P316 PROPOSED, commit UNKNOWN.
Live2 now has a stream-only SignalEngine adapter connected to the deadline engine. It uses in-memory aggTrade candles and the shared pump category contract subset available from live stream features, with no network/disk IO in evaluation. Categories requiring derivative/OI/mark context are rejected explicitly as unavailable in generation 0.
New entries remain forbidden: execution, executable-entry guards, exchange position precheck, actual fill, verified stop, and position supervisor are still TODO.
Next validation: run `run-anomaly-live2` after P311-P316 and inspect `deadline_decision` events. On strong buckets, verdicts should be `selected` only when stream baseline/category checks pass; otherwise `rejected_signal_contract`, `data_not_ready`, or `deadline_missed` must explain the reason.
```

## 2026-05-19 — P317 proposed

```text
Current patch status: P317 PROPOSED, commit UNKNOWN.
Live2 now has an executable-entry guard layer connected after stream signal selection. It rejects selected signals when the signal is stale, current stream price drift is too high, TP1 is already touched before execution, RR to TP1 collapsed, or live stream price/risk levels are unavailable. The guard is stream-only: no REST/cache/file IO and no order placement.
New entries remain forbidden: execution, exchange position precheck, actual fill, verified stop, and position supervisor are still TODO. `new_entries_allowed=false` remains mandatory.
Next validation: run `run-anomaly-live2` after P311-P317 and inspect `deadline_decision` events. If any stream signal becomes selected, entry guard fields must show either `accepted` or a concrete `rejected_entry_guard` reason; no late/drifted/TP-touched signal may proceed toward execution.
```

## 2026-05-19 — P318 proposed

```text
Current patch status: P318 PROPOSED, commit UNKNOWN.
Live2 now has a strict execution boundary connected after entry-guard acceptance. Startup performs Binance/account preflight through the typed exchange client; accepted signals perform pre-entry `fetch_symbol_position_amount` before any future order placement can exist. Existing exchange positions are rejected explicitly; flat symbols end at `rejected_execution_order_placement_not_implemented` until verified fill and verified stop are implemented.
New entries remain forbidden: no market order, no actual fill, no stop order, no TP/BE position supervisor. `new_entries_allowed=false` remains mandatory.
Next validation: run `run-anomaly-live2` after P311-P318 and inspect `execution_preflight`, `deadline_decision.execution_*`, and `live2_status.json.execution_status`. There must be no order attempts; accepted guard decisions must never proceed without exchange position precheck.
```

## 2026-05-19 — P321 proposed

```text
Current patch status: P321 PROPOSED, commit UNKNOWN.
Live2 now has the first verified real-order entry lifecycle. A selected stream signal with accepted entry guard may proceed only if runtime gates allow entries, exchange preflight is ready, symbol is flat on the exchange, and live2 protected-position capacity is available. Execution uses deterministic client ids, verifies actual market-order fill, checks exchange position delta, places a reduce-only initial stop, verifies the stop through the Binance stop/algo-order boundary, and records a protected local position. If a fill occurs but stop/position integrity fails, live2 attempts emergency reduce-only close and disables further entries.
Still not complete: TP1 partial close, BE stop move, stop replacement verification, final close reconciliation, Telegram operator messages, and full position supervisor. Do not treat P321 as production-ready for unattended trading until P322 position supervision is implemented and smoke-tested.
Next validation: apply P311-P321, run compileall, then run `run-anomaly-live2 --symbols <one liquid symbol>` only with minimal account exposure and inspect `deadline_decision.execution_*`, `live2_status.json.execution_status.protected_positions`, and exchange UI for actual fill plus visible initial stop. Stop visibility failure must produce `position_integrity_error` and emergency close attempt.
```

## 2026-05-19 — P322 proposed

```text
Current patch status: P322 PROPOSED, commit UNKNOWN.
Live2 now has a verified PositionSupervisor for positions created by P321. It manages only registered protected positions with actual entry fill and visible current stop. It can perform TP1 reduce-only partial close from exchange fill, move the remaining stop to breakeven after verifying the new stop, cancel the old stop, and remove the position only after exchange position amount is flat.
Still not complete: dedicated stop-trigger fill lookup, stale open-order reconciliation across restart, Telegram operator messages, and broader runtime/coverage hardening. Do not increase notional/universe until P322 has been smoke-tested against real exchange behavior.
Next validation: run `run-anomaly-live2 --symbols <one liquid symbol>` with minimal notional, inspect `position_tp1_filled_be_stop_verified`, `position_final_close_verified`, and `position_integrity_error` events, and compare them with exchange UI/open conditional orders.
```

## 2026-05-19 — P323 proposed

```text
Current patch status: P323 PROPOSED, commit UNKNOWN.
Live2 runtime coverage is hardened around WS health and decision-loop pressure. Ticker and aggTrade sources now expose connect/reconnect/disconnect counters and thread liveness. The runner emits `market_data_coverage_update` when source/gate status changes, keeps market-data readiness false immediately on stale/disconnected coverage, and only re-enables the market-data gate after configured clean recovery windows. Runtime gate status now includes market-data clean/degraded windows and decision-loop overrun counters.
Trading impact: no signal threshold, order sizing, fill, stop, TP1/BE, or live1 behavior is changed. New entries become stricter under WS reconnect/stale coverage and recover only after clean windows.
Still not complete: Telegram critical/operator messages, restart/open-order reconciliation, exact stop-trigger fill reconstruction, and full stress/load validation.
Next validation: run `run-anomaly-live2 --symbols <one liquid symbol>` for 2-5 minutes, interrupt/reconnect network if possible, and inspect `market_data_coverage_update`, `runtime_gate_update`, `live2_status.json.market_data_status.ws_health`, and `runtime_gate_status.decision_loop_overrun_count`. Entries must remain disabled during stale/reconnect periods and recover only after clean windows.
```

## 2026-05-19 - Live2 audit after P324

Current commit: UNKNOWN.

P325 proposed after patch-stack audit. Main finding: P311-P324 compile, but run-anomaly-live2 had startup/import and safety-contract issues that should be fixed before real smoke.

## 2026-05-19 - P326 live2 grid-log state

Current commit: UNKNOWN.

P326 proposed after P325. Live2 gets a v1-style terminal grid log so the operator can see connection/market/trading/control health without opening JSON/CSV. This is UI-only; artifacts remain source of truth and trading behavior is unchanged.

## 2026-05-19 - P327 live2 warmup/backoff state

Current commit: UNKNOWN.

P327 proposed after P326. Live2 now has startup-only aggTrade REST warm-up into bounded in-memory candle rings and exponential reconnect backoff/watchdog restarts for ticker/aggTrade WebSockets. This does not reintroduce hot REST fallback: signal decisions still use only already-hydrated stream state and must reject/degrade when coverage is stale.

## 2026-05-19 - P328 live2 startup/operator visibility state

Current commit: UNKNOWN.

P328 proposed after P327. Live2 no longer stays silent during startup: the terminal shows stage progress for preflight, ticker, universe selection, warm-up, and aggTrade WS before the first heartbeat grid. The heartbeat grid is now rendered through a v1-style repaintable console logger instead of printing a new block every heartbeat. Runtime gate flips remain in artifacts/grid, but Telegram no longer sends “new entries enabled/disabled” messages. Default auto-universe is broadened to max 600 symbols with no 24h quote/trade-count minimum, because the previous 300k quote-volume filter could shrink live2 to roughly 100-150 symbols while v1 covered 500+.


## 2026-05-19 - P329 live2 universe floor state

Current commit: UNKNOWN.

P329 proposed after P328. Live2 default auto-universe now uses `universe_min_quote_volume_24h=30_000` instead of `0`. This filters dead/dust symbols while preserving broad 500+ style coverage. Trading logic, real-order lifecycle, stop/TP handling, Telegram behavior, and runtime gates are unchanged.

## 2026-05-19 - P330 live2 startup universe snapshot state

Current commit: UNKNOWN.

P330 proposed after P329. Live2 auto-universe selection no longer depends on the first partial `!ticker@arr` WebSocket payload. At startup it hydrates ticker/liquidity fields once through the exchange startup ticker snapshot boundary, then selects the 30k+ quote-volume universe from that broad snapshot plus any live WS updates. The REST snapshot remains startup-only and is not available to signal/decision hot path. A minimum auto-universe guard is added so live2 does not silently proceed with a tiny auto universe such as 90 symbols.

## 2026-05-19 — P316 proposed

```text
Current patch status: P316 PROPOSED, commit UNKNOWN.
Live2 now has a stream-only SignalEngine adapter connected to the deadline engine. It uses in-memory aggTrade candles and the shared pump category contract subset available from live stream features, with no network/disk IO in evaluation. Categories requiring derivative/OI/mark context are rejected explicitly as unavailable in generation 0.
New entries remain forbidden: execution, executable-entry guards, exchange position precheck, actual fill, verified stop, and position supervisor are still TODO.
Next validation: run `run-anomaly-live2` after P311-P316 and inspect `deadline_decision` events. On strong buckets, verdicts should be `selected` only when stream baseline/category checks pass; otherwise `rejected_signal_contract`, `data_not_ready`, or `deadline_missed` must explain the reason.
```

## 2026-05-20 - P340 live2 private user-data stream state

Current commit: UNKNOWN.

P340 proposed after P339. Live2 now starts a Binance USD-M private user-data stream through a typed exchange boundary: create listenKey, connect to `/private/ws/<listenKey>`, keep listenKey alive, rotate before 24h, and emit user-data execution events into artifacts. `new_entries_allowed` now requires `user_data_stream_ready=true` in addition to market data, decision latency, artifact writer, exchange preflight, position supervisor, and execution gates. Raw listenKey is not written to artifacts.

Next validation: apply P331-P340, run compileall, then run a short live2 smoke with minimal account exposure. `live2_status.json.execution_status.user_data_stream.ready` must become true before any entry can be allowed; user stream disconnect/keepalive failure must flip entries off with `user_data_stream_not_ready`.

## 2026-05-20 - P343 live2 hard market-data startup state

Current commit: UNKNOWN.

P343 proposed after P342. Live2 now treats missing mandatory ticker, aggTrade, or markPrice WS readiness at startup as a hard startup failure. This removes the ambiguous long-running blocked mode for dead market data while preserving runtime gates for later disconnects/recovery after a successful startup.

Next validation: apply P331-P343, run compileall, then run a short live2 smoke. Startup must fail quickly with `ticker_ws_startup_failed`, `aggtrade_ws_startup_failed`, or `mark_price_ws_startup_failed` if a mandatory stream has no valid payload within its startup wait. If startup succeeds, later runtime disconnects should still be handled by normal gates/transitions.

## 2026-05-20 - P344 live2 reconnect backoff state

Current commit: UNKNOWN.

P344 proposed after P343. Additional audit found that markPrice WS and private user-data stream used `Live2ReconnectBackoff.next_delay()`, but the shared helper exposes `next_delay_seconds()`. Compileall cannot catch this because the path is runtime-only after reconnect/error. P344 fixes both call sites so markPrice/private WS threads can recover instead of dying on the first reconnect.

Next validation: apply P331-P344, run compileall, then run a short live2 smoke and verify reconnect/error paths do not produce `AttributeError: 'Live2ReconnectBackoff' object has no attribute 'next_delay'`.

## 2026-05-20 - P345 live2 startup context prewarm state

Current commit: UNKNOWN.

P345 proposed after P344. Live2 is still not a true all-seeing runtime if OI and 24h prior context are lazy-loaded only after a symbol becomes active/radar, because the first 5s impulse can be classified as `data_dependency_not_ready` before context arrives. P345 adds startup prewarm for OI and strict 24h prior context across the selected universe before runtime entries begin. Runtime context refresh remains active/radar-only. No fallback or hot-path REST is added.

Next validation: apply P331-P345, run compileall, then run a short live2 smoke. Startup should show OI/24h prewarm progress and `live2_status.json.market_data.startup_context_prewarm`. If startup is too slow, tune source-side request pacing deliberately; do not revert to lazy-only context for real-order mode.

## 2026-05-20 - P346 live2 missing prior-context module state

Current commit: UNKNOWN.

## 2026-05-26 - short/fader 30d research state

Current commit: UNKNOWN.

The 30d `bare_htf_short_discovery_30d_1m_5s` run does not prove a tradable broad short edge. The full live-filtered strategy is strongly negative: 191 closed, sum_net -120.34%, median -1.15%, winrate 29.8%. Data honesty is acceptable except for cache-snapshot universe survivorship/listing bias; flow uses real number_of_trades/quote_volume, not proxy.

Main research finding: early post-HTF LTF pressure matters, but many attractive `ltf12_*` categories are not available at the original early fill and become negative if we honestly wait 12 candles. The only plausible executable candidate from this run is a 6-candle delayed rule: after the closed 1m HTF anomaly and any short-pressure trigger, wait until 6x5s context is available, require `ltf6_red_share >= 66%` and delayed risk <1.5%, then enter short with RR2.5. It produced 23 trades / 19 symbols / 13 days, +11.86% sum, 52.2% winrate, PF 1.89, top5 positive-profit share 60.7%, first/second half both positive. This is still too small for live and needs strict replay on another period/universe.

Follow-up one-print/downtrend audit: a concentrated single 5s print inside the HTF anomaly plus prior downtrend is not a standalone edge. One-print and downtrend filters alone were negative. The only useful one-print refinement was pairing it with immediate LTF taker weakness after HTF close (`one_score>=0.45 & ltf6_taker<48`), but delayed6 replay is weaker than the red/risk candidate: 32 trades, +12.3% sum, WR 50%, median ~0, PF 1.42. We cannot honestly infer liquidation/short-cover from aggTrades alone; that needs liquidation feed or low-latency OI delta.

Next best step: implement or script an explicit delayed-entry replay mode for this candidate rule, then run 60d/time-split validation. Do not enable live shorts from this result.

## 2026-05-25 - P403 short/fader cost-control state

Current commit: UNKNOWN.

P403 tightens the default `bare_htf_short_fader` anomaly definition for targeted 1s planning. Before P403, short/fader discovery used flow-only coarse anomalies (`quote/trades >= 5x baseline`) and could plan an excessive number of 1s windows over 30d full-market runs. After P403, the default short/fader prefilter requires stronger closed HTF evidence: quote ratio >=10, trade ratio >=8, and HTF close-open return >=1.5%. Validation smoke reduced 23 coarse candidates to 2 targeted 1s windows and passed honesty checks.

Next validation: run the 30d discovery with defaults first. If targeted 1s ETA is still too high, raise `--short-fader-prefilter-min-htf-return` to 0.02 or 0.03.

## 2026-05-25 - P402 short/fader artifact honesty state

Current commit: UNKNOWN.

P402 fixes a discovered artifact-contract bug in `bare_htf_short_fader`: trigger rows inherited `decision_available_timestamp_ms` from the closed HTF anomaly even though the actual trigger decision is only available after the triggering LTF candle closes. The simulated fill path already entered on the next LTF open, so this is primarily an audit/honesty bug rather than a known PnL timing bug. Validation smoke passed the honesty availability/baseline checks with 0 failures.

Next validation: run the larger 30d/60d discovery and analyze categories out-of-sample or by time split before treating any category as an edge.

P346 proposed after a failed live2 startup showed `ModuleNotFoundError: No module named 'research_tools.anomaly_live2.market_data.prior_context'`. The earlier P345 prewarm patch referenced `Live2PriorContextPoller`, but the module file was missing from the applied patch stack. P346 adds the missing module so live2 can import and proceed to startup preflight.

Next validation: apply P346, run compileall, then run `run-anomaly-live2` again. If import succeeds, continue debugging from the next concrete startup/runtime error.


## 2026-05-20 - P347 live2 user-data startup event writer state

Current commit: UNKNOWN.

P347 proposed after a failed live2 startup showed `AttributeError: 'AnomalyLive2Runner' object has no attribute '_write_user_data_stream_starting_event'`. The private user-data stream startup path called an audit helper that was missing from the applied patch stack. P347 adds that helper so startup can proceed to listenKey creation and private WS readiness checks.

Next validation: apply P347, run compileall, then run `run-anomaly-live2` again. If startup passes this point, continue debugging from the next concrete startup/runtime error.


## 2026-05-20 - P348 live2 aggTrade startup blocker diagnostics state

Current commit: UNKNOWN.

P348 proposed after live2 reached strict market-data startup and failed with `live2 aggTrade WS startup failed: partial_or_connecting`. That error is too generic for post-mortem. The patch keeps aggTrade fail-fast strict, increases the default startup wait from 10s to 60s for multi-shard startup, and adds `reason` plus `readiness_blockers` to aggTrade status so the next failure identifies the exact shard-level blocker.

Next validation: apply P348, run compileall, then run `run-anomaly-live2` again. If it still fails, inspect `aggtrade_ws_startup_failed.data.aggtrade_ws.readiness_blockers` in `live2_events.csv` / status instead of guessing.

## 2026-05-20 - P351 live2 selected-universe OI refresh state

Current commit: UNKNOWN.

P351 proposed after a successful live2 market-data startup showed `OI ! 72/578` and `OI stale 503` after ~17 minutes. Root cause: P345 prewarmed OI for the selected universe, but runtime refresh remained active/radar-only, so most prewarmed passive symbols expired after the 180s stale window. P351 makes runtime OI refresh the selected universe continuously, active-first, and aligns OI freshness to 5m historical OI cadence plus the full-universe refresh rate.

Next validation: apply P351, run compileall, then run live2 long enough for one full OI refresh cycle. Expected status after several minutes: OI ready should climb toward selected universe size, OI stale should fall sharply, OI err should remain low. If OI err rises or Binance rate-limit symptoms appear, reduce `oi_max_symbols_per_cycle` deliberately rather than reverting to lazy-only OI.

## 2026-05-20 - P350 live2 operator status state

Current commit: UNKNOWN.

P350 proposed after a live2 smoke reached running market-data state. It fixes operator-facing status semantics only: the grid `Время` now starts at market monitoring instead of startup/prewarm, `Позиции` shows open/session-total so a fresh run is 0/0 instead of 0/max-capacity, v1-style session top movers are shown between Market and Trading, and the ambiguous `Дедлайн` label is renamed to `Опоздало`.

Next validation: apply P350, run compileall, then restart live2 and confirm the grid shows market-monitoring runtime, fresh-run positions 0/0, session top movers, and no trading-path behavior changes.


## 2026-05-22 - P387 aggTrade cache trust-version state

Current commit: UNKNOWN.

Latest 1m/5s, 1m/15s and 5m/30s artifact set produced zero final signals because every pair/forming candidate failed with `untrusted_materialized_entry_flow_cache` from old `p165_1s_ohlcv_to_subminute_v1` or missing 1s-derived cache metadata. A code mismatch also meant freshly backfilled 1s aggTrade caches would be written with `p378_aggtrades_to_1s_full_buckets_v1` while the strategy expected `p378_latency_aggtrades_full_buckets_v1`. P387 aligns the trusted version and makes the aggTrade backfill skip existing chunks only when the existing 1s cache already has trusted P378 metadata.

Next validation: apply P387, run compileall, regenerate 1s aggTrade cache and materialized 5s/15s/30s caches, then rerun anomaly lab and confirm anomaly_candidates contain non-error rows and anomaly_signals is non-empty before interpreting PnL.

## 2026-05-22 - P388 targeted flow backfill state

Current commit: UNKNOWN.

P388 proposed after the latest 1m/5s artifact set showed the backtest was fully choked by missing/untrusted subminute flow caches. The intended historical path is restored: run-anomaly-lab performs a coarse setup-timeframe scan, backfills trusted 1s aggTrade windows only around coarse anomaly windows, materializes required subminute entry caches, and then runs pair/forming collection. This avoids full 1s backfill for the entire 40-day universe while keeping P381/P387 trust gates strict.

Next validation: apply P388 after P386v2 and P387, run compileall, rerun a small 1m/5s slice, and inspect `targeted_flow_backfill.csv`, `targeted_flow_materialize.csv`, and `anomaly_candidates.csv`. Expected: no mass `untrusted_materialized_entry_flow_cache`; candidate rows should be non-error unless Binance fetch failed or no coarse anomaly windows exist.

## 2026-05-22 - P389 targeted flow backfill planner state

Current commit: UNKNOWN.

P389 proposed after P388 started `targeted 1s flow` with an effectively unbounded ETA. Root cause: the planner used every coarse anomaly candidate as a fetch window and padded each with setup-baseline history, so long runs collapsed toward a full 1s rebuild. P389 changes planning to coarse pre-context signals only, removes OI/derivatives enrichment from the coarse planning scan, and makes pair/forming historical baseline come from setup-timeframe OHLCV instead of subminute cache.

Next validation: apply P389, run compileall, then rerun a small 1m/5s slice. Inspect `targeted_flow_backfill.csv`: the `__coarse_scan__` row should show `window_selection=coarse_signal_prefilter`, `coarse_signals` much smaller than `coarse_candidates`, and `window_before_ms` near one setup candle instead of baseline history.


## 2026-05-22 - P390 targeted flow backfill planner state

Current commit: UNKNOWN.

P390 proposed after P389 still produced multi-thousand-hour `targeted 1s flow` ETAs. Root cause: the planner was still fetching too many and too-wide windows before enough cheap gates were applied, and materialization still operated at symbol-cache scope. P390 narrows the fetch plan to coarse/pre-context signal rows, applies a closed setup-candle upper-bound prune for subminute flow, fetches setup-candle plus immediate-entry-tail windows only, merges nearby windows by symbol, materializes only requested intervals, and filters pair collection to trusted subminute caches.

Next validation: apply P390, run compileall, then rerun a small 1m/5s slice. Inspect `targeted_flow_backfill.csv`: `status=window_plan` should show `merged_targeted_windows` and `merged_targeted_window_ms` far below the raw candidate-window totals; `targeted_flow_materialize.csv` should show `written_interval`, not full-symbol rebuild behavior.

## 2026-05-22 - P391 targeted-flow pipeline integrity state

Current commit: UNKNOWN.

P391 proposed after the latest post-P390 run still produced zero valid candidates and contradictory stale-looking edge artifacts. The issue is pipeline integrity: the backtest must prove that targeted aggTrade windows were planned, fetched, materialized into trusted subminute flow, and covered before final candidate collection and edge reporting. P391 adds explicit plan/fetch/materialize/coverage/funnel/verdict artifacts and disables edge/grid summaries when the run is data-invalid.

Next validation: apply P391, run compileall, then rerun a small 1m/5s slice. Inspect `targeted_flow_plan.csv`, `targeted_flow_fetch.csv`, `targeted_flow_materialize.csv`, `targeted_flow_coverage.csv`, `anomaly_funnel.csv`, and `anomaly_run_verdict.csv`. Only interpret PnL if `valid_backtest=true`.

## 2026-05-26 - Long continuation from bare HTF anomaly, structural-stop status

Current commit: UNKNOWN.

The fixed-percent long SL exploration is invalid for strategy conclusions. Pump Awakening edge claims must use graphically/structurally justified stops only.

Latest structural-only artifact set: `.output/results/bare_htf_short_discovery_30d_1m_5s/long_structural_discovery_analysis/`.

Status:
- Long continuation after closed 1m HTF anomaly is more promising than the short-fader hypothesis on the same artifacts.
- Broad local/recent post-close structural stops are negative; they only become positive under momentum filters and still have poor median trade quality.
- Broad HTF-anomaly-low structural stop is positive but dirty: best broad row wait4/RR2.0 has 622 trades, 210 symbols, 31 days, WR 42.4%, avg +0.266%, median -0.369%, sum +165.2%, PF 1.24.
- Best current candidate category: wait4/RR2.0, SL under HTF anomaly low, `ltf4_ret>0 & prior_spikes<=3`: 358 trades, 180 symbols, 31 days, WR 44.7%, avg +0.507%, median -0.180%, sum +181.5%, PF 1.53, top5 positive share 25.0%.

Interpretation:
- This is not a clean final edge yet because the median trade remains negative and the broad edge comes from winners/time exits, not from most trades being good.
- The structural idea worth testing next is continuation after anomaly reclaim/hold with HTF-low invalidation, not short fading and not fixed-percent risk.
- Live/backtest parity is not established until live2/backtest both implement the same post-HTF entry timing, structural HTF-low stop, stale/drift/RR guards, and actual-fill risk accounting.

Next best test:
Implement a minimal honest long-discovery mode that trades only the candidate structural contract, writes reject funnel and structural-risk fields, then run a small parity backtest before any live use.

## 2026-05-26 - Long quality candidate update

Current commit: UNKNOWN.

Quality-over-frequency search found a stronger structural long candidate than the broad wait4/RR2 row:

- Entry timing: wait 6 closed 5s candles after closed 1m HTF anomaly.
- Rule: `ltf6_ret>0.5% & risk 1.5-5%`.
- Stop: structural SL under the closed HTF anomaly low, no fixed-percent SL.
- Exit tested: RR1.5.
- Metrics: 215 trades, 132 symbols, 30 days, WR 53.5%, avg +0.785%, median +0.391%, sum +168.8%, PF 1.78, top5 positive share 19.9%, max-symbol positive share 5.0%, first half +68.9%, second half +99.9%.

This improves the broad structural baseline at the cost of fewer trades: lower top dependence, higher winrate, positive median, and slightly higher total sum. The cleaner RR1.0 variant has WR 56.7%, median +0.598%, top5 share 17.1%, but lower total sum (+117.2%).

Current status: prefer the wait6/RR1.5 quality candidate for the next honest implementation test. It is still research-only until validated out-of-sample and implemented with live/backtest parity.

## 2026-05-26 - Long candidate nature read

Current commit: UNKNOWN.

The current wait6 long rule should not be understood as "buy because 30s return is >0.5%". That is only a cheap proxy.

Working nature hypothesis:
- HTF candle shows real awakening flow.
- After HTF close, the market does not immediately reject the move.
- The first 30s of 5s candles show post-anomaly acceptance/continuation while structural invalidation remains under HTF low.
- Best cases are not single-print blowoffs: distributed early flow and moderate taker share are healthier than one 5s print or extreme buyer chase.
- Clean prior history helps; repeated prior spikes/fades make the same continuation less trustworthy.

Useful evidence from nature audit:
- Base wait6/RR1.5 rule: 215 trades, WR 53.5%, median +0.391%, PF 1.78, top5 share 19.9%.
- Wins and losses have similar median `ltf6_ret`; therefore `ltf6_ret` alone is not the edge explanation.
- `prior_spikes<=3 & last3_quote_share<=50%`: 100 trades, WR 64.0%, median +1.08%, sum +133.0%, PF 3.39, but top share rises to 28.6%.
- `taker<=55%`: 104 trades, WR 61.5%, median +1.18%, PF 2.23, but top share rises to 30.9%.
- Very concentrated one-print confirmation is bad; top one 5s quote share >75% was negative in the candidate set.

Next implementation should express this as a nature contract, not a naked momentum rule: post-HTF acceptance, structural HTF-low invalidation, distributed/non-blowoff 5s confirmation, controlled risk, and prior-history cleanliness.

## 2026-05-28 - HTF/LTF runner noise separation state

Current commit: 736cd41b, dirty worktree observed.

Latest runner discovery artifact: `.output/results/htf_ltf_runner_discovery_7d`.

Readout:
- Do not treat missing subminute future-label windows as noise. Valid-label filtering changes the base: 30s/15s profiles have about 11-12% runner labels among rows with `future_label_status=ok` and `htf_ltf_status=ok`.
- Stronger candidate separators in the valid 30s set are real LTF participation, distributed flow, and prior spike sustain: high `htf_ltf_number_of_trades`, high real `htf_ltf_quote_volume`, low `htf_ltf_quote_top1_share` / `htf_ltf_trade_top1_share`, and low `prior_spike_next_decay50_share`.
- Binary `dormancy_ok` is not currently a positive runner separator; in this 7d set it often selects lower runner-rate rows. This does not invalidate the dormancy thesis, but it means current dormancy thresholds may be too strict or are confounding true sleep with dead liquidity.
- Existing executable selected streams are not robust enough: `5m_30s` and `3m_30s` are only slightly positive with negative median and high top-positive dependency; `5m_1m` and `1m_15s` are negative.

Current conclusion: runner-vs-noise separation is visible at the label/nature layer, but not yet proven as executable edge. The next test should be a predeclared 30s validation on another period with valid-label accounting, real participation floor, distributed-flow cap, prior-spike sustain, moderate anomaly/no-chase guard, and live-filtered structural replay.


## 2026-05-29 - P446 rolling fetch speed correction

Current commit: UNKNOWN.

Status: P446 PROPOSED after P445 did not reduce the `targeted 1s aggTrades` bottleneck. The issue is not that 1s trades are a trading feature; for 30s rolling decisions, 1s is only an intermediate materialization source. P446 keeps true aggTrade flow but writes trusted 30s target-LTF cache directly, avoiding duplicate 1s parquet work and reducing targeted window churn by merging windows up to one hour.

Next: apply P446, restart the 45d rolling discovery, and confirm the progress line says `targeted aggTrades→30s` rather than `targeted 1s aggTrades`.

## 2026-05-29 - P447 live2 rolling C/A/S preparation

Current commit: UNKNOWN.

Status: P447 PROPOSED. Live2 is prepared to trade the same rolling runner categories being tested in discovery: rolling 5m/30s and 3m/30s, first fixed C/A/S category trigger, 30s decision buckets, risk-per-trade sizing, total open-risk cap, and symbol cooldown equal to rolling HTF width. This intentionally removes the legacy 5s/1m live signal path from entry selection rather than keeping it as a fallback.

Next: apply P447 only after P446 is applied, run compileall, then first run live2 in minimal-notional/limited-risk observation and compare live `deadline_decision` artifacts against rolling discovery fields before increasing size.

## 2026-05-29 - P449 live context rolling correction

Current commit: UNKNOWN.

Status: P449 PROPOSED after review of P448. P448 fixed insufficient warmup and prior-spike baseline availability but incorrectly pushed live context back toward wall-clock calendar HTF buckets. P449 restores event-rolling context for live: rolling seed remains 30s-based; context uses the latest fully closed 1m candles before the rolling HTF start and walks backward in HTF-width chunks. Startup baseline lookback is limited to the latest 48h, not full history.

Next: apply P449 over P448, run compileall, then verify live artifacts show rolling context and no `rolling_1m_history_not_ready` after startup.


## 2026-05-29 - P450 live2 rolling warmup completeness guard

Current commit: UNKNOWN.

Status: P450 PROPOSED. Live2 rolling C/A/S has no detected trading-signal lookahead after P449, but startup 1m HTF baseline warmup had a data-quality hole: a single 48h kline request could be truncated by exchange/API limits and still mark the symbol as warmed. P450 makes the warmup paginated and requires recent contiguous 1m history before a symbol is counted as warmed. Zero-volume Binance 1m candles are kept so real dormancy does not become artificial gaps.

P450 also blocks rolling live C/A/S decisions when selected 30s confirmation or rolling HTF candles contain aggTrade-id gaps, so incomplete websocket/rest trade candles become data dependencies instead of tradable signals.


## 2026-05-29 - P451 live2 rolling data-quality tolerance

Current commit: UNKNOWN.

Status: P451 PROPOSED after P450 feedback. Live2 rolling C/A/S should not fail permanently on tiny aggTrade delivery holes, but it also must not trade blindly on large missing-flow gaps. The contract is now explicit tolerance + artifact visibility: small 30s aggTrade-id gaps are marked tolerated, large gaps remain data-dependency blocks. Startup 48h 1m history remains a REST warmup, not waiting in real time. Runtime decisions still require enough currently contiguous 1m history for the rolling context; missing 1m data causes not-ready until sufficient fresh contiguous history exists, not silent fallback.

## 2026-05-29 - P452 live2 rolling 1m emergency repair

Current commit: UNKNOWN.

Status: P452 PROPOSED. Live2 rolling C/A/S now has a cleaner data-completeness model: 30s aggTrade holes use explicit tolerance artifacts, while missing/stale 1m baseline context triggers a bounded REST repair from official 1m klines. The repair is not a trading fallback: if REST cannot restore enough recent contiguous 1m history, signal remains `data_dependency_not_ready`.
## 2026-05-29 - P448 live2 rolling context parity audit

Current commit: UNKNOWN.

Status: P448 PROPOSED after reviewing P447 live2. P447 had no direct future/outcome field usage in C/A/S, but live defaults and context construction were not safe enough: CLI still warmed only 75 minutes of closed 1m baseline, prior-spike detection did not require full 24h plus pre-spike baseline history, and live aggregated 1m history into rolling-aligned chunks instead of the calendar HTF context used by discovery. P448 raises live startup baseline to 30h, requires enough HTF context before C/A/S can select, computes prior-spike baselines from full pre-current history, and calendar-aligns live context candles fully closed before the rolling HTF start.

Next: apply P448, run compileall, then run live2 only after the rolling 45d discovery result is acceptable. In live artifacts, verify `rolling_1m_history_not_ready` disappears after startup and selected signals contain `rolling_runner_tf_set` only in `5m_30s` / `3m_30s`.

## 2026-05-29 - P453 TP1/structural runner parity

Current commit: UNKNOWN.

Status: P453 PROPOSED. Position-management policy is now intended to be: TP1 at 0.75R closes 50%, the remaining 50% is held under verified structural stop/trailing, and early-exit conditions are audit telemetry only. Do not compare runs produced by the old no-TP discovery replay against P453 runs without labeling the exit model.

Prior-spike parity rule by conscience: prior-spike features must be computed only from same-symbol candles fully closed before the current rolling seed starts. They may describe historical resonance/decay, but must never include the current seed, confirmation candles after decision, future runner labels, or post-entry lows/highs.

## 2026-05-29 - live2 audit artifact state after P454

Current commit: UNKNOWN.

Status: P454 PROPOSED. Live2 artifact health issue is diagnosed as audit-policy/backpressure, not websocket connectivity. Product audit contract after P454: raw event/near-miss CSVs are bounded detail streams; durable strategy-review truth is the combination of `live2_deadline_summary.csv`, `live2_near_miss_summary.csv`, `live2_near_miss_examples.csv`, `live2_status.json`, `live2_symbol_state.csv`, and top-growth index/status/top files.

Next validation: restart live2 and require `live2_events.csv` to stay near its configured budget, `artifact_writer_status.dropped_count` to no longer imply loss of selected/entry/execution rows, summary files to cover the full runtime, and interrupted top-growth scans to leave `completion_status=partial` rows with processed/remaining counts.


## 2026-05-30 - P458 live2 NameError sweep

Status: PROPOSED. Current commit: UNKNOWN.

Fixes the remaining live2 undefined global found by a sweep after the flat-stop recovery crash: `Live2PositionSupervisor.run_cycle()` referenced `_dict(...)` when classifying final-close reasons, but this helper was not defined or imported in `position_supervisor.py`. The patch adds a local typed `_dict()` helper and does not change position-management logic, exchange calls, fills, stops, or PnL recovery.

Validation:

```bash
python -m compileall -q data/exchanges research_tools cli constants.py main.py
```

Additional static sweep used for live2: import all `research_tools.anomaly_live2.*` modules and inspect function bytecode for unresolved `LOAD_GLOBAL` / `LOAD_NAME`; after this patch no unresolved live2 globals remain.

## 2026-05-30 - P459 live2 health state

Current commit: UNKNOWN. Status: P459 PROPOSED.

Run `20260529_190712` showed that data-ingestion sockets were healthy, but product health still failed on runtime hygiene: decision latency degraded, routine event payloads exhausted raw budgets, protected positions stayed `watching` in the grid, and top-growth partial status contained only `empty_ohlcv` rows. P459 keeps socket ingestion untouched and moves the remaining cleanup to audit/state/projection boundaries: compact routine decision events, sync protected position state into the grid, defer hot-path REST repair to background 1m maintenance, and fetch top-growth from explicit closed Binance 1h klines.

Next validation after restart: `symbol_status_counts.in_position` must match `execution_status.open_protected_positions`, routine raw event file growth must slow materially, `decision_loop_overrun_count` should stop climbing from REST repair stalls, and top-growth status should contain `ok` / `below_threshold` rows rather than all `empty_ohlcv` for normal Binance symbols.

## 2026-05-30 - P460 live2 data-health state

Current commit: UNKNOWN. Status: P460 PROPOSED.

Run `20260530_065636` proved that sockets, writer, graceful shutdown, and top-growth partial artifacts are materially healthier after P454-P459, but rolling C/A/S remained mostly blocked by `rolling_1m_history_not_ready`. Artifact rows showed `max_closed_candles=3000` and thousands of 1m candles for major symbols, so the earlier ring-capacity diagnosis was incomplete. The actual issue is maintenance currentness: a fresh latest 1m candle does not prove the preceding recent 1m context is contiguous. P460 changes maintenance to require contiguous recent official 1m coverage and to backfill bounded official-klines gaps behind a fresh tail.

Next validation: run live2 after P460 and require `rolling_1m_maintenance_recent_contiguous_count` to climb toward the configured lookback for active symbols, while `rolling_1m_history_not_ready` drops sharply. If dependency rejects remain high, separate true 30s aggTrade gap reasons from residual 1m context lag.


## 2026-05-30 - P461 live2 maintenance crash state

Current commit: UNKNOWN. Status: P461 PROPOSED.

Run `20260530_080733` showed healthy WS ingestion and a working P460 rolling 1m maintenance model, but live crashed from a concurrent-read bug in maintenance status: `_recent_contiguous_1m_count_from_state()` iterated `ring.closed` while another thread mutated the deque. P461 moves the recent 1m context read behind a `SymbolStateStore` lock boundary and throttles non-inline console grid prints so IDE/redirected terminals do not spam repeated `◆ Соединение` blocks. Status reads are now cheap snapshots, not eligibility rescans from the runtime gate heartbeat.

Next validation: restart live2 and require no `RuntimeError: deque mutated during iteration`, `rolling_context_maintenance.total_errors=0`, `decision_loop_overrun_count` not to regress, and the console to show bounded periodic snapshots rather than one full grid per heartbeat when repaint is unavailable.

## 2026-05-30 - P463 live2 runtime/operator hygiene

Current commit: UNKNOWN. Status: P463 PROPOSED on top of P449/P462 from uploaded code state.

P462 removed the observed deadline-miss bottleneck without top-K selection: all symbols remain observed, and baseline-free impossibility rejects replaced most heavy signal evaluations. The next run surfaced runtime/operator issues instead of strategy issues: a Windows lock on `live2_symbol_state.csv` could make `artifact_writer_ready=false`, startup was still slow due sequential warmups, user-data stream readiness mixed transport readiness with payload evidence, and top-growth took too many heartbeats to finish a full closed-hour universe audit.

P463 separates critical append-only audit writes from non-critical snapshot/status writes, overlaps independent startup warmups, makes user-data readiness more explicit, accelerates top-growth completion, and makes console output compact during warmup and grid-only after startup. Trading filters, entry contract, execution, fills, stops, and PnL logic are not changed.

Next validation: run live2 without opening/copying current artifact CSVs if possible, but also deliberately copy/zip the run folder during live once. Accept if a transient `live2_symbol_state.csv` lock increments `artifact_writer_status.noncritical_error_count` without disabling `new_entries_allowed`; reject if any critical event/near-miss writer error occurs. Also compare startup wall-clock against the previous ~29 minutes and inspect user-data status fields separately: `transport_ready`, `payload_seen`, `order_event_seen`.

## 2026-05-30 — P464 proposed

P464 is PROPOSED / UNKNOWN commit. It fixes live2 operator console hygiene after P463: stdout should expose only two repainting surfaces, startup warmup and live grid. Retry warnings, command logger lines, and background thread tracebacks are routed to run artifacts instead of corrupting the terminal UI. No trading logic changed.

## 2026-05-31 - P471 proposed

P471 is PROPOSED / UNKNOWN commit. Live/backtest artifacts now separate `signal_verdict` from `portfolio_verdict`. Max positions, cooldown, same-symbol-open, risk-cap, runtime gates, and existing exchange positions are portfolio/allocation outcomes, not signal-quality rejections. Next parity analysis should compare shared-core `signal_verdict` first, then inspect portfolio/execution differences separately.

## 2026-05-31 - P472 proposed

P472 is PROPOSED / UNKNOWN commit. Shared-core decisions now carry a source-neutral `snapshot_hash` and `snapshot_match_key`. Live2 writes `live2_decision_ledger.csv`; discovery writes matching hash/key fields in `htf_ltf_runner_decision_ledger.csv`; `research_tools.decision_parity_join` joins them exactly. Next parity analysis should first check `same_snapshot_hash + different signal_verdict`; any such row is a decision-core bug. Rows with matching signal verdict but different portfolio/execution verdict belong to allocation/execution replay, not signal quality.

## 2026-05-31 - P475 proposed

P475 is PROPOSED / UNKNOWN commit. It fixes the remaining parity break after P465-P472/P474: live and backtest now build pre-seed context as rolling HTF-width windows stepped by LTF candles from the same normalized LTF substrate. Backtest no longer drops seeds at the adapter `anomaly_gate` before the shared core, exact confirm rejects are ledgered per window, live pending seeds expire deterministically after max-confirm, and `snapshot_hash` excludes adapter `decision_time_ms`.

Next validation: apply P475 on top of P465-P472/P474, run compile/guard/core smoke, then run a small same-period live/backtest replay and inspect `snapshot_hash` joins before changing any thresholds or exits.

## 2026-06-04 - P505 targeted LTF accelerator status

Current commit: UNKNOWN. Status: APPLIED locally.

The next honest 10x acceleration path is now isolated behind `research_tools/targeted_ltf_accelerator.py`. It currently centralizes targeted subminute cache orchestration and materializes 15s/30s sibling target caches from one raw aggTrade interval, but it does not yet add bulk/archive aggTrade sources. Trading logic, shared core inputs, execution model, and thresholds are unchanged.

Main remaining bottleneck risk: cold-cache 30d discovery can still be REST-bound if most requested windows are new. The next improvement should add a bulk/raw aggTrade source into the accelerator source-priority chain, not another strategy filter, and acceptance must be parity-first: same target windows should produce the same `snapshot_hash -> signal_verdict` before any profitability comparison.

## 2026-06-04 - P506 archive-backed backtest acceleration status

Current commit: UNKNOWN. Status: APPLIED locally.

Targeted subminute discovery now has a reusable raw-data source before REST: Binance public USD-M futures daily aggTrades ZIPs cached under `_raw_aggtrade_archive`. The accelerator still materializes only explicitly requested windows and still uses the same target-LTF writer as REST. Full-day archive files are not feature lookahead because rows are filtered to requested timestamp intervals before any candle aggregation.

Parity acceptance is now measurable, not qualitative: `research_tools.decision_parity_join --summary-output ...` reports exact snapshot coverage, same-snapshot signal mismatches, and selected-signal overlap. For the project goal, require `same_snapshot_different_signal_verdict=0` and selected overlap at or above 90% on a same-period live/backtest comparison before using 30d PnL as strategy evidence.

## 2026-06-04 - P507 runner discovery CLI contract

Current commit: UNKNOWN. Status: APPLIED locally.

`run-htf-ltf-runner-discovery` is again a simple fixed-profile command with only `--days` exposed. Targeted LTF backfill, archive/REST source priority, seed planning thresholds, profile list, execution model, and portfolio simulation are internal research-contract values, not CLI knobs. Any future change to them should be a named patch plus research log entry, not an ad-hoc shell flag.

## 2026-06-04 - P508 3d readiness finding

Current commit: UNKNOWN. Status: APPLIED locally.

The 3d run was structurally complete and used the archive accelerator, but targeted fetch artifacts still contained dozens of `UnicodeEncodeError` rows for non-ASCII pseudo-symbol market ids. P508 converts those into explicit `unsupported_binance_market_id` data-source rejects without archive/REST attempts. Before launching 30d, rerun at least a short smoke or inspect a resumed run's `htf_ltf_runner_targeted_ltf_fetch.csv`; fetch `error` rows should no longer be dominated by Unicode encoding failures.

## 2026-06-05 - 30d runner discovery state

Current commit: b0bcafa8. Status: ANALYZED.

Run `.output/results/htf_ltf_runner_discovery_30d` completed all four fixed profiles: `5m_30s`, `3m_30s`, `5m_15s`, `3m_15s`. Coverage is good enough for selected-signal, trade, and per-category readouts: HTF uses real `quote_volume` and `number_of_trades`; LTF uses real `quote_volume`, `number_of_trades`, and `taker_buy_quote_volume` for almost the full universe. Fetch rejects are not internet-loss dominated: they are mainly explicit `unsupported_binance_market_id` rows for three non-ASCII market ids, plus five stock-like symbols with missing LTF source coverage.

The run is not yet enough for a broad candidate-universe nature claim, because `htf_ltf_runner_candidate_rule_scores.csv` is empty (`no_events`) in every profile and candidate rows do not carry future labels. That does not invalidate trade PnL or selected category analysis, but it prevents a clean runner-vs-fader lift study over all seed candidates.

Trading state: all selected combined profiles remain negative after costs (`816` closed, sum `-1.1323`, avg `-0.00139`, median `+0.00606`, winrate `53.6%`). C/A/S categories alone are not the edge. The strongest entry-time hypothesis from this run is `ltf_taker_buy_quote_share >= 0.55` plus no meaningful pre-seed dump (`pregrowth_min_path_return_pct >= -1%`). Per-profile live-filtered results for this fixed hypothesis were positive in all four profiles: `5m_30s` 165 trades sum `+0.6095`, `3m_30s` 158 trades sum `+0.1838`, `5m_15s` 114 trades sum `+0.6052`, `3m_15s` 151 trades sum `+0.3924`. The 15s profiles are cleaner across halves/days; 30s profiles are more first-half dependent.

Next honest step: do not tune thresholds from this same run. Freeze the `buyer + no pre-seed dump` hypothesis, add or run a post-hoc candidate labeler only for already planned/covered candidate windows if broad runner/fader nature is needed, and validate on another period or a same-period live/backtest parity join before promoting any filter to live.

## 2026-06-05 - 30d winning category families

Current commit: be65b688. Status: ANALYZED / HYPOTHESIS GENERATION.

Follow-up mining on `.output/results/htf_ltf_runner_discovery_30d` used only entry-time fields for rule masks; future runner labels, MFE/MAE, exits and PnL were evaluation-only. Generated artifacts are in `.output/results/htf_ltf_runner_discovery_30d/_analysis_coverage/winning_categories_v1/`.

Old C/A/S categories are not validated as standalone edge. They should not be discarded from artifacts, but they should be demoted to context/modifiers. A strengthened `C` subset is good only after adding buyer/confirmation/history filters; raw combined `C` and raw combined `A` remain negative.

The robust nature family is `clean buyer continuation`: taker-buy confirmation, no material pre-seed dump, real confirmation return/trade pace, clean prior-spike history, and no obvious single-print/late-tail chase. Canonical frozen hypothesis candidates:
- Broad base: `buyer55 + no pre-seed dump`, 588 per-profile trades, sum `+1.7910`, WR `60.4%`, top20 positive share `58.8%`.
- Strong cross-TF: `buyer60 + confirm_ret>=0.6% + prior_spikes<=10`, 78 trades, sum `+0.9864`, WR `76.9%`, both halves positive, top20 positive share `44.5%`.
- Strong cross-TF plus no-dump: add `pregrowth_min_path_return_pct>=-1%`, 63 trades, sum `+0.7899`, WR `79.4%`, both halves positive, top20 positive share `42.5%`.
- Distributed variant: add `seed_top1_quote_share<=50%`, 75 trades, sum `+1.0037`, WR `77.3%`.
- Not-late-chase variant: `buyer60 + seed_tail_quote_share<=55% + confirm_trade_pace>=5 + prior_spikes<=10`, 85 trades, sum `+0.9611`, WR `78.8%`.

This is still not a live-ready proof because it was mined on the same 30d dataset. The next validation must freeze one or two rules above and test on another period or same-period live/backtest parity before changing live filters.

## 2026-06-05 - strengthened category and exit-policy memory

Current commit: UNKNOWN. Status: ANALYZED / APPLIED locally.

The categories to remember are no longer raw C/A/S. Raw C/A/S stays an artifact label only. The frozen research family is `clean buyer continuation`: no material pre-seed dump, strong taker-buy confirmation, real confirmation return/trade pace, clean prior-spike history, and no single late-tail/single-print seed dominance.

Stronger high-win candidates from the same 30d run:
- `buyer60 + confirm_ret>=0.8% + prior_spikes<=10 + no pre-seed dump`: 48 trades, sum `+0.6771`, WR `81.3%`, top20 positive share `41.8%`.
- `buyer60 + confirm_ret>=0.6% + prior_spikes<=10 + 5m profiles`: 53 trades, sum `+0.7450`, WR `83.0%`, top20 positive share `44.2%`.
- `buyer60 + confirm_trade_pace>=5 + prior_spikes<=10 + 15s profiles`: 45 trades, sum `+0.5684`, WR `80.0%`, top20 positive share `41.1%`.
- `buyer60 + confirm_trade_pace>=5 + quote_pace in [3,40] + 5m_15s`: 42 trades, sum `+0.5476`, WR `81.0%`, top20 positive share `43.1%`.

Exit-policy replay was rerun through `ParquetStorage`, so base parquet plus `delta/` targeted LTF cache were visible. Input was 118 strong-category rows with forced post-entry materialization; 99 windows were fully continuous and 19 had later LTF gaps, but every replayed trade closed before the problematic gap. Artifacts are in `.output/results/htf_ltf_runner_discovery_30d/_analysis_coverage/exit_policy_v1/`.

Exit readout:
- No-TP / structural-trail-only is not the default path. It can raise summed return on this sample, but it drops WR to about `56-59%` and makes returns heavily top-dependent (`top10` positive share about `44-52%`, `top20` about `68-69%` on the strong union).
- Current `TP1=0.75R close 50% then trail` is profitable on strong categories but still leaves more runner-tail dependence than needed.
- `TP1=0.75R close 75% then trail 25%` is the best near-term compromise for the project goal: it keeps upside, cuts runner-tail dependence versus 50% close, and does not turn the system into scalp-only.
- `full TP at 0.75R` is the clean conservative benchmark. `full TP at 0.5R` maximizes WR and lowers top dependence further, but sacrifices average return and may overfit toward small early pops.

Code hygiene applied locally: discovery TP fill now requires candle trade-through (`high > TP`) instead of ambiguous exact touch, and the honesty report now describes the actual TP1 partial + structural trailing model. This does not change signal/category selection.

## 2026-06-05 - shared clean-buyer trade policy implementation

Current commit: fcf768bc. Status: APPLIED locally / UNKNOWN commit.

P512 moves the mined clean-buyer rules out of ad-hoc analysis masks and into a source-neutral module: `research_tools/pump_trade_policy.py`. Both live2 and runner discovery now call the same policy after `PumpDecisionCore` returns `selected`. Core-selected but policy-rejected rows are no longer hidden as portfolio effects; they are signal-quality rejects with `core_signal_verdict=selected`, `trade_policy_verdict=rejected`, and a visible policy reason.

Trading policy implemented for live/backtest:
- Active policy: `clean_buyer_continuation_v1`.
- Accepted rule family: strong clean buyer continuation, 5m strong buyer confirmation/history, 15s fast tape/history, 5m15 active tape, distributed seed, and not-late tail tape.
- Broad `buyer55 + no dump` is watchlist-only unless it also matches a stronger accepted rule.
- Exit policy: `tp075_close75_structural_trail_v1`, `TP1=0.75R`, close `75%`, structurally trail `25%`.

Live2 now carries policy fields in `Live2SignalDecision`, live decision ledger, protected position state, and execution details. For managed live positions, TP1 is recalculated from actual entry fill and actual initial risk, while signal TP remains audit/entry-guard context. The supervisor uses per-position `tp1_close_fraction`; config is fallback only for legacy/manual positions.

Backtest discovery now writes policy fields in signal/trade rows and decision ledger, and its post-entry replay reads `tp1_r` and `tp1_close_fraction` from the accepted signal policy instead of treating discovery config as the policy source.

Next validation: run a short `run-htf-ltf-runner-discovery --days 1` and inspect `trade_policy_verdict` funnel counts before comparing PnL. Then run live shadow/small-notional and verify live2 decision ledger has zero unexplained policy/execution mismatches.

## 2026-06-06 - exit timing state

Current commit: UNKNOWN. Status: ANALYZED.

30d exit-timing research found no evidence that clean-buyer pump trades should be force-closed quickly when TP is not immediate. For current-policy raw trades, TP is often not instant: median TP delay among hits is `8.5m`, q75 `21.25m`, q90 `32.5m`. On the 115-row materialized strong replay, current `TP0.75R close75 trail` remains the best tested management policy (`+1.2836` sum, `75.7%` WR, PF `3.52`). Full `TP0.75R` is the conservative benchmark, not a clear upgrade.

Fixed no-TP timers, dynamic same-speed timers, and flow/seller redflags all reduced expectancy on this replay. The redflags especially over-exited normal post-entry digestion. Pre-entry pump speed and buyer/pace metrics can be logged as confidence/diagnostics, but they should not move live stops or force exits without out-of-sample proof.

Next honest step: keep live exit policy unchanged, and use the exit-timing artifacts to design a later out-of-sample validation only after category validation. Do not try to rescue weak category buckets with exit hacks; inspect weak `trade_policy_rule_id` buckets separately.

## 2026-06-06 - P514 live2 launch-ready tightening

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

Trade policy was tightened before the next live2 launch. The weak standalone
`five_minute_15s_active_tape` bucket and standalone distributed seed buckets
are now watchlist-only; they remain visible in artifacts but no longer permit
orders unless a stronger accepted rule also matches. On the 30d current-policy
raw readout, removing these primary weak buckets would reduce closed trades from
`365` to `286` while improving old-exit avg from `+0.7446%` to `+0.9599%` and
WR from `68.8%` to `72.0%`. Treat this as in-sample strengthening, not final
edge proof.

Live2 launch defaults were aligned with runtime config: rolling 1m maintenance
lookback `720m`, top-growth cycle `16` symbols / `3s`, and supervisor TP1 close
fraction `0.75`. The live2 market-watch test file now imports cleanly under the
shared rolling seed-first runtime; legacy live-only setup tests are explicitly
skipped instead of blocking collection.

Next live step: start live2 with the simple command and inspect
`live2_decision_ledger.csv` for `trade_policy_verdict`, `trade_policy_rule_id`,
and zero unexpected execution integrity errors before judging PnL.

## 2026-06-06 - live2 audit hygiene after pre-P514 run

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

The pre-P514 live2 run `.output/results/live2_anomaly_runs/20260605_214104`
did not reveal an execution integrity failure: two real entries had verified
fills/stops, one TP1 partial was verified, both final closes were verified, and
`total_integrity_errors=0`.

The run did reveal an artifact durability problem. `live2_events.csv` hit the
128 MiB budget because high-frequency heartbeat events carried full nested
runtime/status blobs; heartbeat rows alone consumed about `120 MB`. Full status
belongs in `live2_status.json` and diagnostics summary, not every event row.

Applied locally: heartbeat event payload is now compact/bounded and keeps only
gate state, counters, per-timeframe decision totals, execution/supervisor
counters, and artifact budget counters. This should preserve long live audit
capacity without changing signal, trade policy, execution, or parity logic.

Next live validation: launch live2 after P514 plus compact heartbeat and verify
that `live2_events.csv` no longer approaches budget, `dropped_count` stays zero,
and decision latency/backlog is assessed without event-writer noise.

## 2026-06-06 - PIEVERSE OI-collapse guard

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

PIEVERSE from `.output/results/live2_anomaly_runs/20260605_214104` exposed a
real strategy-safety gap: the current rolling seed-first live path did not use
OI collapse to block entries. The old OI-divergence rejection was legacy
`post_htf_acceptance_long` code and was not active for the current shared
core/trade-policy path.

Applied locally: live2 entry guard now rejects selected/accepted long signals
before order placement when fresh comparable current OI is down more than
`0.5%` from the first fresh session OI baseline, or when available 5m OI is
down more than `0.5%` over `3x5m`. The reject reasons are explicit:
`current_oi_drop_from_first_ok_before_execution` and
`oi_3x5m_drop_before_execution`.

This is intentionally an execution-layer live safety guard, not a
`PumpDecisionCore` signal change, because current OI is not the same data shape
as seed/confirm candles. Backtest parity still needs a separate honest
historical 5m-OI simulation before OI collapse can be treated as a shared
trade-policy rule.

Next validation: run live2 and inspect `entry_guard_reason` counts. A future
backtest experiment should replay the accepted 30d signals with decision-time
5m OI to estimate how much this guard reduces trades and whether it improves
expectancy without hiding data-quality gaps.

## 2026-06-06 - baseline liquidity correlation state

Current commit: UNKNOWN. Status: ANALYZED.

The 30d runner discovery does not support a simple rule that higher absolute
baseline liquidity is better. On all 816 live-filtered closed trades,
`baseline_quote_volume_median` and `baseline_number_of_trades_median` both had
weak negative correlation with net return and win/loss. The broad raw sample
showed the same pattern, with the highest-liquidity quartile underperforming.

For the current P514 accepted-policy subset replayed from entry-time fields,
the sample was only 48 rows and showed a weak positive quote-volume slope, but
not enough to promote a liquidity threshold. Use baseline liquidity as a
segmentation/audit dimension, not as a live entry filter. The stronger edge
candidate remains clean buyer continuation nature: no dump, buyer flow,
confirmation, clean prior-spike history, and non-late/non-single-print tape.

## 2026-06-06 - FORM trade interpretation

Current commit: UNKNOWN. Status: ANALYZED.

FORM from `.output/results/live2_anomaly_runs/20260605_214104` was not a clean
10%+ runner example. It was a short clean-buyer continuation/pop: the live
entry was fresh and exactly filled at signal price, policy evidence was strong,
TP1 partial and structural trailing close were verified, and realized PnL was
positive. The later chart weakness happened after the position was already
closed by trailing stop.

No immediate code patch is justified from FORM alone. High seed-tail quote
share looked suspicious visually, but the 30d P514 accepted subset does not
show high tail share as a bad standalone splitter. The important research risk
is semantic: current live policy can monetize TP-pop continuations, while the
project's stated target often refers to 10-20-50% runners. Future validation
must separate `TP-pop edge` from `large-runner edge` before changing live
filters.

## 2026-06-06 - live2 20260606_070834 SANTOS / uptime state

Current commit: UNKNOWN. Status: ANALYZED.

The current live2 run `.output/results/live2_anomaly_runs/20260606_070834`
opened one real position: `SANTOS/USDT:USDT`, `5m_15s`, category
`C_balanced_flow_acceptance`, policy rule `strong_high_win_clean_notdump`.
Entry integrity was good: signal age about `152ms`, drift `0`, market fill
`0.6694` versus signal `0.6702`, initial stop verified, and final stop close
verified. The trade was not a reconciliation failure; it was a clean-buyer
continuation loser with about `4.4%` initial risk that stopped before TP1.

Runtime trading availability is still below target. The diagnostics window
showed about `77%` session allowed time (`5452.5s` allowed / `1535.9s`
blocked), mostly from `decision_latency_degraded`. Artifact writing is healthy
after heartbeat compaction and did not drop events. The main blocker is hot-path
deadline load/backlog across the 15s and 30s engines, with user-data stream DNS
reconnects as a secondary gate.

Parity risk found: live selected rows currently record final `latency_ms` after
the whole execution call. For SANTOS that is `3128ms`, although signal
evaluation and entry guard completed within `152ms`; the rest is order, stop,
verification, and current-OI fetch. This is safe for execution but confusing for
backtest/live parity analysis. Future artifacts should separate
pre-execution decision latency from post-execution audit duration.

## 2026-06-06 - large-runner timing and nature state

Current commit: UNKNOWN. Status: ANALYZED.

The 30d 5m/1m cache supports a separate large-runner readout. On first 5m broad
awakening setups, `10%+` hourly high runners were uncommon (`954 / 42076`,
`2.27%`), `20%+` runners were rare (`185 / 42076`, `0.44%`), and `30%+`
runners were tail events (`62 / 42076`, `0.15%`). This means the live strategy
cannot treat every clean pop as a future 10-30% runner.

Timing is still favorable if the early move is real. For `20%+` runners, the
median `10%` hit came about `15m` after the first 5m awakening and median `20%`
hit came about `30m`; the median peak was about `45m`. A 5m entry left median
remaining peak potential near `20.8%`; a 10m confirmation still left about
`19.3%`; a 15m confirmation left about `16.1%`; 20m started to become expensive
at about `13.4%`. For `30%+` runners, even 10-15m confirmation often remained
usable, but 20m materially reduced the remaining edge.

The strongest first-5m discriminator was early price expansion itself:
`first5m_return >= 3%` captured about `56%` of `20%+` runners with about
`7.8%` `20%+` precision. Flow ratios and 1m structure improve cleanliness but
do not replace price confirmation. Useful 1m structure is sustained/late flow:
`3+` elevated 1m candles in both quote and trades, top one-minute concentration
not too dominant, and last two minutes still contributing roughly `30%+` of
first-5m flow. Requiring every minute to be green, requiring taker-buy share to
be very high, requiring OI to rise, or requiring the first spike to exceed the
prior 24h maximum is not supported as a hard filter.

Important risk: hourly `high10/high20` labels include violent wick/fader
events. Clean runner validation should prefer close/structure-aware labels and
should evaluate structural trailing behavior after entry, not just whether a
future high printed. The current live policy may be a TP-pop policy; a
large-runner policy should be separated in research and then shared by
backtest/live only after parity checks.

## 2026-06-06 - large-runner edge deep-dive state

Current commit: UNKNOWN. Status: ANALYZED / PROPOSED.

A second 30d readout tested entry-time candidate rules against remaining peak
potential after 5m/10m/15m entry, clean close-positive runner labels, daily and
symbol concentration, and dead-noise rates. Artifacts are under
`_analysis_coverage/large_runner_nature_v1/edge_deep_dive_v2/`.

The best pure-entry candidates are not yet a proven edge. Strong first-5m
expansion around `5-7%`, especially with strict 1m sustained tape and prior
flow relevance, gives only about `1-10` candidates/day and improves clean
`15%+` potential materially versus the broad base, but still has high dead-noise
rate around `35-42%` and negative median remaining close. This means the next
work should not be another static entry-filter tweak. The edge, if present, is
likely in combining early runner selection with runner-specific structural
management and fast invalidation.

Important feature signal:
- early price expansion and absolute first-5m trade/quote mass dominate;
- 1m sustained/late flow improves quality but cannot replace price expansion;
- high pre60 range/preheat is positively associated with large runners, so a
  rigid sleep-only/no-prior-volatility rule can discard good moves;
- OI non-collapse is useful mainly as a short-covering safety guard, not as a
  standalone alpha booster;
- buyer-share extremes and prior-24h spike dominance are weak as hard filters.

Proposed backtest expansion: add a separate `large_runner_candidate` research
profile with multiple arms for early 5m ignition, 10m confirmation, and rare
15m exceptional continuation. Keep it additive: do not replace the current
TP-pop clean-buyer policy until the 30d structural-exit backtest proves actual
expectancy after fees/slippage, portfolio sequencing, top-trade dependence, and
live/backtest parity.

## 2026-06-06 - large-runner discovery mode state

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

Implemented a separate command, `run-large-runner-discovery --days N`, for
cache-only large-runner research. It is not a flag on the existing runner
discovery and does not change live2, `PumpDecisionCore`, or the TP-pop
clean-buyer policy.

The mode scans 5m candles for broad first-awakening setups, keeps the first
setup per symbol per 60m cluster, applies a cheap 5m-only prefilter, and only
then loads targeted 1m windows for candidate arms and structural exit
simulation. It avoids `1s`, `15s`, and `30s` data, so it should be much cheaper
than the old HTF/LTF discovery path while still preserving true 1m dynamics for
runner/fader research.

Honesty boundary: future labels/top-growth coverage are written only as
evaluation artifacts. Arm matching uses only fields available at the relevant
5m/10m/15m decision time. Empty artifacts are written with headers to avoid the
old `No columns to parse from file` failure mode.

Smoke result: `run-large-runner-discovery --days 1` completed on the current
594-symbol cache in about `240.6s`, producing `3972` raw broad candidates,
`2389` first-cluster setups, `44` arm matches, and `220` trade-grid rows
(`165` closed after execution guards). The sample was negative in 1d, which is
expected and is not an edge conclusion.

## 2026-06-06 - independent runner-edge pack review

Current commit: UNKNOWN. Status: ANALYZED / PROPOSED.

Reviewed `runner_edge_independent_research_pack.zip` against the local
`large_runner_discovery_30d` artifacts. The pack direction is useful but must
remain a hypothesis until validated out-of-sample or on untouched forward/live
artifacts.

The strongest current contour is not "buy every large hourly runner". It is a
narrow E5-only portfolio branch using `E5_ignition_strict` and
`E5_mass_ignition`, excluding `preheat_ignition`, `E10`, and `E15` as trade
entries:

```text
v4_quality_cool =
((stress_seed) OR (liquid_oi_seed))
AND anti_chase
AND pre60_return_pct <= 6%

stress_seed:
pre60_range_pct >= 3.0%
pre60_trades_sum >= 12k
m1_min_path_return <= -0.4%

liquid_oi_seed:
early_return_pct <= 7.5%
pre60_trades_sum >= 40k
m1_quote_top1_share <= 41%
oi_change_early_pct >= 0 OR OI missing

veto:
pre60_return_pct > 6%
early_return_pct > 9%
m1_quote_top1_share > 55%
m1_trade_top1_share > 55%
early_taker_buy_quote_share > 62%
```

Independent local recalculation confirms the pack's main `v4_quality_cool`
numbers when restricted to portfolio-selected E5 strict/mass trades with
`tp075r_close25_be1r_kill10`: `31` trades, `22` symbols, `70.97%` WR,
`+4.59%` average net, `+1.67%` median net, `+142.15%` sum net,
`+40.83%` ex-top5, `+14.36%` ex-top10, and `82.35%` positive active days.

Key interpretation:
- two candidate natures are tradeable research branches:
  `A_cool_stress_absorption` and `B_liquid_distributed_moderate_taker`;
- OI support, 24h flow-record, and extreme range are boosters, not independent
  arms;
- `preheat_ignition` is not part of v4-quality trading;
- `E10/E15` can label confirmed runners, but market continuation entry is often
  late and needs a separate retest/pullback hypothesis;
- "no dump before pump" did not survive this readout; controlled stress plus
  absorption currently looks stronger than clean no-pain dormancy.

Data-quality boundary: the 30d local run has true 5m `quote_volume` and
`number_of_trades` for `594/594` symbols and cached 5m OI for `586/594`.
However, 1m enrichment is targeted (`163` symbols, `532` enrichable setups),
so m1-nature conclusions must be validated with an explicit missed-runner /
prefilter audit.

Missed-runner boundary: current large-runner arms cover only about `14%` of
hourly `10%+` high runners, `28%` of `20%+`, `36%` of `30%+`, and `60%` of
`50%+`. That is acceptable for a high-conviction E5 branch, but it does not yet
solve the chronic missed-runner problem.

## 2026-06-06 - large-runner nature evaluator implementation

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

Implemented the independent pack direction as research-only classification:
`research_tools/large_runner_nature_rules.py` now exposes a pure v4 evaluator
for `v4_quality_cool`, `v4_standard`, two core natures, and three boosters.
`large_runner_discovery.py` writes the evaluator fields into setups, matches,
trade grid, and portfolio trades, then emits summary artifacts for:

```text
large_runner_nature_summary.csv
large_runner_nature_by_week.csv
large_runner_nature_by_symbol.csv
large_runner_nature_top_dependency.csv
large_runner_nature_sensitivity.csv
large_runner_prefilter_missed_top_growth.csv
```

The evaluator is intentionally not wired into live2 or `PumpDecisionCore`. It
is an artifact classifier for the next 45d run. The selected trade scope is
E5-only and limited to `E5_ignition_strict` / `E5_mass_ignition`; preheat and
E10/E15 remain research readouts.

Validation completed:

```text
pytest tests/test_large_runner_discovery.py tests/test_cli_runner_discovery_empty_artifacts.py -q
compileall data/exchanges research_tools cli constants.py main.py launcher.py
```

`launcher.py` is absent in the local checkout, so compileall reports it cannot
list that path while still exiting successfully. Next step is a 45d
`run-large-runner-discovery` and analysis of the selected category artifacts.

## 2026-06-06 - 45d large-runner nature readout

Current commit: UNKNOWN. Status: ANALYZED.

Ran:

```text
python main.py run-large-runner-discovery --days 45
```

Artifacts:

```text
.output/results/large_runner_discovery_45d
```

Period: `2026-04-17T15:50:00Z` to `2026-06-01T15:50:00Z`. Runtime:
`1706.687s`. Data quality remained suitable for 5m flow research:
`594/594` symbols had real `quote_volume` and `number_of_trades`; `586/594`
had cached 5m OI. Targeted 1m enrichment loaded `218` symbols and `875`
enrichable setups; `37657` first-cluster setups were skipped by the 5m
prefilter.

Selected portfolio result for `v4_quality_cool` with
`tp075r_close25_be1r_kill10`:

```text
trades: 53 / 1.18 per day
symbols: 39
win_rate: 56.60%
avg_net: +2.64%
median_net: +0.53%
sum_net: +140.12%
sum ex-top5: +38.81%
sum ex-top10: +6.22%
top5 share: 72.31%
high10 next60: 79.25%
high20 next60: 24.53%
positive active day rate: 65.52%
worst day: -7.15%
```

Verdict: `v4_quality_cool` survived the 45d extension but is materially weaker
than the 30d readout. It remains a promising research candidate, not a proven
live strategy. The result is median-positive and ex-top10 positive, but top5
share is slightly above the target, and weekly stability is not clean.

`v4_standard` should not be promoted: `75` trades, `1.67/day`, `46.67%` WR,
`-0.37%` median, and `-11.42%` worst day. It adds frequency by admitting too
many weak/chase setups.

Sensitivity readout:
- `pre60_return_cap <= 4%` did not materially improve robustness over 6%.
- `m1_quote_top1 <= 35%` and `early_taker <= 55%` improve WR/median but cut
  frequency to about `0.6/day` and increase top-trade dependence.
- Harder filters look like confidence tiers, not a standalone solution.

Missed-runner audit:

```text
all >=10% hourly top-growth rows: 1785
5m_prefilter_failed: 788
broad_5m_gate_not_seen: 717
covered_by_portfolio: 183
arm_matched_but_not_selected_nature: 76
enriched_but_no_arm_match: 18
```

For `30%+` hourly high runners (`133` rows): `48` failed the 5m prefilter,
`32` were not seen by the broad 5m gate, `27` matched an arm but not selected
nature, and only `23` were portfolio-covered. This confirms the chronic missed
runner problem is mostly candidate generation / prefilter coverage, not just
the final category filter.

Next research direction: keep `v4_quality_cool` as the current conservative
benchmark, reject `v4_standard`, and investigate the 45d missed-runner funnel
to add decision-time candidate generators that catch slow/hidden early runners
without using future labels in trade rules.

## 2026-06-06 - Large-runner timing audit patch

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

The first 45d missed-runner funnel was directionally useful but too coarse: it
classified top-growth coverage only inside the first 15 minutes of the labelled
hour. That is not enough to distinguish a genuinely missed early runner from a
runner whose top-hour label began before/after the actual pump start.

P522 adds evaluation-only timing artifacts to `run-large-runner-discovery`:

```text
large_runner_top_growth.csv:
  hour high 5m-candle offset plus first5/first15/first30 high/close/flow stats

large_runner_top_growth_timing_audit.csv:
  candidate/setup/prefilter/enrichment/arm/nature/trade/portfolio visibility
  for first_15m_of_top_hour, full_top_hour, and pre60_to_hour_end windows
```

This is diagnostics only. It does not change candidate rules, nature rules,
execution simulation, live2, or `PumpDecisionCore`.

Next: rerun `python main.py run-large-runner-discovery --days 45` and use the
new timing audit to separate true early misses from late/hour-label artifacts
before adding any new candidate generator.

Follow-up before rerun: a quick timing read over the old 45d artifacts showed
the old first-15m funnel was indeed too blunt. Of `717` rows marked
`broad_5m_gate_not_seen` in the first 15 minutes, `559` had raw 5m candidates
later in the full labelled hour. Therefore these rows are not all true early
misses. The next generator work must focus on first-15m/pre-hour misses and on
cases where full-hour visibility exists but prefilter/nature rejects the setup.

## 2026-06-06 - Large-runner cluster promotion patch

Current commit: UNKNOWN. Status: APPLIED locally / UNKNOWN commit.

The timing audit exposed a concrete research-model flaw: the old
`run-large-runner-discovery` setup selector kept only the first broad 5m
candidate per symbol/60m cluster. On strong `>=30%` hourly runners, many
`5m_prefilter_failed` rows were caused by a weak first broad print, while a
later raw candidate in the same cluster passed the same 5m prefilter.

Patch P524 changes candidate generation additively:

```text
keep first broad 5m awakening per 60m cluster for audit continuity
if it fails 5m prefilter, also keep the first later raw candidate in that
cluster that passes the decision-time 5m prefilter
```

This remains a research expansion, not live trading logic. It does not use
top-growth labels, PnL, MFE/MAE, exits, or portfolio survival for selection.

1d smoke:

```text
raw: 3972
setups: 2412
matches: 79
closed simulated rows: 310
```

The expansion is bounded: the 1d smoke increased matches from the previous
`44` to `79`, not by orders of magnitude. The next required check is a 45d
rerun to see whether promoted setups recover missed large runners without
destroying median/ex-top10/top-dependence.

45d promoted rerun result at commit `62d177be`:

```text
runtime: 3719.766s
raw: 126987
setups: 77079
matches: 2812
trade-grid rows: 14060
closed simulated rows: 9945
```

Coverage improved materially:

```text
>=30% first15 top-growth portfolio-covered: 23 -> 33
>=30% full-hour top-growth portfolio-covered: 27 -> 50
>=30% pre60_to_hour_end portfolio-covered: 36 -> 67
>=30% first15 5m_prefilter_failed: 48 -> 31
>=30% full-hour 5m_prefilter_failed: 55 -> 9
```

But the combined `v4_quality_cool` portfolio weakened as a tradable package:

```text
trades: 124 / 2.76 per day
WR: 40.32%
median: -0.48%
sum: +195.65%
top5 share: 61.38%
top10 share: 97.25%
ex-top10: +5.38%
positive active day rate: 57.5%
worst day: -13.05%
```

The original first-broad `v4_quality_cool` subset stayed identical to the
previous benchmark: `53` trades, `56.6%` WR, `+0.53%` median, `+140.12%` sum,
`+6.22%` ex-top10. The new promoted subset added `71` portfolio trades but was
not robust: `28.17%` WR, `-0.77%` median, `+55.53%` sum, and negative ex-top10.

Conclusion: cluster promotion is useful as missed-runner research coverage, but
it must not be promoted to live trade policy as-is. The best promoted
sub-hypothesis is `m1_last2_trade_share <= ~0.385`, which means the promoted
seed is not tail-chase inside its own 5m candle. It remains too small and
top-dependent for live promotion: about `21` portfolio trades, `61.9%` WR,
`+0.88%` median, but negative ex-top10 in portfolio sequencing.

## 2026-06-11 - P543 session-aware fader classifier

Status: APPLIED locally / UNKNOWN commit.

Using the existing `.output/results/large_runner_discovery_365d` artifacts, a
session-aware outcome table was built without a new multi-hour 365d rerun:

```text
large_runner_session_outcome_research_table.csv
large_runner_session_fixed_hypothesis_outcome_rules.csv
large_runner_session_fader_classifier_short_replay_summary.csv
large_runner_session_fader_classifier_rolling_edge_report.md
```

Main finding: the robust edge found here is a classification edge, not yet a
proven executable short edge.

Best broad fader classifier:

```text
all_sessions: close15<=3.39% & pre60_range>=10%
rows: 889
symbols: 267
days: 279
fader_rate: 77.62%
runner_rate: 5.40%
static_or_other_rate: 12.37%
valid_months: 12
min_month_fader_rate: 68.52%
rolling30_min_fader_rate: 57.14%
rolling30_median_fader_rate: 76.85%
```

Session readout:

```text
non_us same rule: 509 rows, 78.78% fader, 4.52% runner
europe_or_off same rule: 262 rows, 79.77% fader, 2.29% runner
asia_only same rule: 208 rows, 78.85% fader, 7.69% runner
us_only same rule: 245 rows, 76.33% fader, 7.76% runner
```

Static/no-trade zone:

```text
3.39% < close15 <= 8.10%
static_or_other about 58-59% on all sessions
```

Runner/no-short veto:

```text
close15 >= 12%
all_sessions runner_rate: 81.63%
us_only runner_rate: 88.45%
```

The classifier was joined into the existing local-high structural short replay.
Current execution/management did not become robust:

```text
all close15<=3.39 & pre60_range>=10:
avg -0.177R, median -0.627R, WR 28.60%

europe_or_off same classifier:
avg -0.085R, median -0.544R, WR 28.27%

best small positive management slice:
68 trades, avg +0.342R, median -0.211R, WR 39.71%,
only 11.11% of winning trades need removal to go negative
```

Conclusion: we can separate faders from statics/runners in a rolling-stable and
session-aware way, but the current short entry/SL/exit implementation still
fails the desired `50%+` top-winning-trade-removal robustness target.

## 2026-06-11 - P544 table-first session edge workbench

Status: APPLIED locally / UNKNOWN commit.

A reusable postprocess was added:

```text
research_tools/session_edge_workbench.py
```

It builds a table-first workbench from existing 365d artifacts without a new
multi-hour rerun:

```text
large_runner_edge_workbench_setups.csv
large_runner_edge_workbench_feature_catalog.csv
large_runner_edge_workbench_replay_scorecard.csv
large_runner_edge_workbench_readme.md
```

Generated counts:

```text
setup_master_rows: 11,367
feature_catalog_rows: 33
replay_scorecard_rows: 909
strict replay robustness passes: 0
watchlist positive-but-top-dependent rows: 90
```

Core bucket outcome rates:

```text
fader_candidate:
889 rows, 77.6% fast fader, 5.4% runner

static_no_trade:
4614 rows, 47.2% upper static, 15.5% runner, 17.4% fast fader

runner_no_short:
2145 rows, 81.6% runner, 2.8% fast fader
```

The workbench separates:

```text
decision-time features
known-after-10m/15m confirmation fields
tail-only OI context
evaluation-only labels
execution-result metrics
```

Conclusion: use the workbench as the default next research surface. Promotion
requires positive average R, positive median R, enough symbols/days/trades, and
`top_remove_winner_pct_to_negative >= 0.50`; current replay policies still have
zero strict passes.

## 2026-06-11 - P545 balance screen over current short watchlist

Status: APPLIED locally / UNKNOWN commit.

Added and ran:

```text
research_tools/session_edge_balance_screen.py
```

Generated artifacts:

```text
large_runner_edge_workbench_balance_screen.csv
large_runner_edge_workbench_balance_by_session.csv
large_runner_edge_workbench_balance_screen.md
```

The screen ranks the existing positive full-period watchlist by a balance of:

```text
sample size
symbols/days/sessions
avg R and median R
positive day/month rate
rolling 30d/60d fixed-rule stability
top trade independence
top symbol independence
fader/runner contamination
```

Result:

```text
rows screened: 89
strict candidates: 0
balanced watchlist rows: 0
max median_r among screened rows: -0.06395R
max top_trade_independence_pct: 11.11%
max top_symbol_independence_pct: 13.04%
```

Best current balance row:

```text
selector: europe_or_off_close15<=3.39 & pre60_range>=10
management: early_return>=6% & close15<=4%
stop/exit: last_pivot_or_recent5 + half_base_low
trades: 68
symbols: 54
days: 52
sessions: 2
avg: +0.342R
median: -0.211R
WR: 39.71%
positive_day_rate: 42.31%
positive_month_rate: 50.00%
top_trade_independence: 11.11%
top_symbol_independence: 13.04%
roll30_min_sum: -3.20R
fader_rate: 88.24%
runner_rate: 2.94%
```

Conclusion: the best balance is still a research watchlist shape, not an edge.
It identifies the right market nature, but execution still pays too much in
median loss and depends on a small set of winners. The next improvement must be
an executable structural trigger that raises median R and top independence, not
more fader classification.

## 2026-06-11 - P546 short enhancement strategy screen

Status: APPLIED locally / UNKNOWN commit.

Added and ran:

```text
research_tools/short_enhancement_strategy_screen.py
```

Artifacts:

```text
.output/results/large_runner_discovery_365d/short_enhancement_strategy_screen.csv
.output/results/large_runner_discovery_365d/short_enhancement_strategy_screen_local.csv
.output/results/large_runner_discovery_365d/short_enhancement_strategy_screen_structural.csv
.output/results/large_runner_discovery_365d/short_enhancement_strategy_screen.md
.output/results/large_runner_discovery_365d/short_enhancement_strategy_proxy_top_scenarios.csv
```

Scope:

```text
local 15m fader/local-high replay + failed-pump structural management replay
filters: session, fader context, target-valid, no pre-entry base/deep touch,
         risk buckets, fast break, one-print, deep break/retest
management: existing fixed/partial/trail policies
proxy triage: BE/time-stop/no-continuation upper-bound on top scenarios only
```

Realized screen:

```text
combined rows: 3397
strict passes: 0
promising realized watchlist rows: 164
local-high promising rows: 0
```

The best realized candidates are structural failed-pump families, not the
generic 15m fader replay.

Best balance:

```text
family: A_post_oneprint_break1p5_retest0p8
filter: fast_break15
stop: recent10
policy: full05
trades: 49
symbols: 42
days: 45
sessions: 6
avg: +0.0886R
median: +0.4562R
WR: 59.18%
positive_day_rate: 60.00%
top_trade_independence: 34.48%
top_symbol_independence: 36.00%
max_drawdown: -2.09R
```

Best larger sample shape:

```text
family: deep_break_retest0p8
filter: fast_break20
stop: recent5
policy: full025
trades: 147
symbols: 100
days: 118
avg: +0.0223R
median: +0.2090R
WR: 71.43%
positive_day_rate: 66.95%
top_trade_independence: 14.29%
top_symbol_independence: 11.94%
```

Best high-independence small sample:

```text
family: fast_deep_break_taker_above
stop: recent10
policy: full05
trades: 31
symbols: 31
days: 28
avg: +0.1169R
median: +0.1020R
WR: 61.29%
top_trade_independence: 42.11%
top_symbol_independence: 42.11%
```

Proxy triage:

```text
A_post_oneprint_break1p5_retest0p8 + last_lower_high + full05:
realized fast_break15: 53 trades, avg +0.087R, median +0.454R,
top_trade 29.41%, top_symbol 33.33%.

time_stop_no_mfe025_proxy:
avg +0.152R, median +0.454R,
top_trade 50.00%, top_symbol 55.17%.
```

This proxy is not tradable evidence because MFE order is unknown. It says the
next high-value implementation is a real 1m path replay for no-continuation /
time stop on the structural `A` family.

Strategy verdict:

```text
1. Generic 15m fader context: good classifier, weak execution; do not promote.
2. Skip already-faded/target-valid/risk filters: improve some rows but do not
   create a robust local-high edge.
3. Fixed quick TP (full025/full05) beats broad trailing for larger samples.
4. Structural one-print/deep-break/retest is the strongest realized family.
5. BE is not the main lever on structural families; time/no-continuation stop
   is the most promising path-required enhancement.
6. Averaging down has no support from current evidence; only staged entry after
   break + failed retest should be tested, with fixed total risk.
```

## 2026-06-11 - P559 robust plateau engine 365d preset

Status: implemented and verified locally.

Command:

```text
python research_tools/short_robust_plateau_engine.py run-365d
```

The command now performs the full current mechanism in one pass:

```text
archive manual 365d knowledge
build immutable event/outcome cache from current replay artifacts
run balanced rolling IS/OOS plateau scan
write lookahead audit, axis summaries, rejection funnel and portfolio checks
```

Final local output:

```text
.output/research_cache/short_robust_plateau_engine_365d/final_report.md
event_outcome_rows: 179299
events: 3283
WFA: 8 windows, 90d IS / 30d OOS / 30d step / 30d holdout
candidate_universe: 5000 balanced candidates
candidate_rows: 2587
promoted: 78
strict plateau_pass clusters: 10
lookahead violations: 0
```

Read:

```text
The mechanism is now operational and reproducible. It found several promising
failed-pump structural short-fade sleeves, especially around Asia-only and
not-Asia-overlap sessions, local-high stops, full025/full05 management and
risk_3_8/risk_5_12 buckets.
```

Important limitation:

```text
This is still not fresh proof. The 365d artifacts have been heavily inspected.
Use this engine as the repeatable discovery/filtering mechanism and treat the
final judge as future unseen/live-forward data or a newly built period.
```
