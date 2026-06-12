# Short Robust Plateau Engine

Date: 2026-06-12
Status: implemented / one-command 365d preset with strict meta-validation and
controlled self-improvement queue.
Commit: UNKNOWN.

This file documents the neutral short-fade plateau mechanism.

## Purpose

The engine separates two jobs:

```text
Machine A: Event Builder
Build an immutable compact event/outcome store from replay artifacts.

Machine B: Plateau Researcher
Search entry-known rule plateaus over the event store with rolling WFA,
top-removal, cost stress, session/symbol breadth and marginal portfolio checks.
```

It must not privilege the manually found 365d rules. Those rules are preserved
as prior research in:

```text
research/archive/2026-06-11_short_fade_manual_research/
research/HYPOTHESIS_REGISTRY.md
```

## Implementation

Script:

```text
research_tools/short_robust_plateau_engine.py
```

Commands:

```text
python research_tools/short_robust_plateau_engine.py run-365d

python research_tools/short_robust_plateau_engine.py archive

python research_tools/short_robust_plateau_engine.py build-event-store \
  --failed-dir .output/results/failed_pump_short_research_365d \
  --large-dir .output/results/large_runner_discovery_365d \
  --output-dir .output/research_cache/short_robust_plateau_engine

python research_tools/short_robust_plateau_engine.py scan-plateaus \
  --output-dir .output/research_cache/short_robust_plateau_engine

python research_tools/short_robust_plateau_engine.py scan-plateaus \
  --output-dir .output/research_cache/short_robust_plateau_engine_365d \
  --candidate-queue-file .output/research_cache/short_robust_plateau_engine_365d/self_improvement_queue.csv \
  --guided-candidates-limit 400

python research_tools/short_robust_plateau_engine.py smoke --max-rows-per-source 8000
```

The recommended 365d command is:

```text
python research_tools/short_robust_plateau_engine.py run-365d
```

Default 365d preset:

```text
output_dir=.output/research_cache/short_robust_plateau_engine_365d
candidate_profile=balanced_365d
max_candidates=5000
max_natures_per_source=8
is_days=90
oos_days=30
step_days=30
final_holdout_days=30
min_is_trades=25
min_oos_trades=8
top_removal_pct=0.35
min_calendar_positive_day_rate=>0.60
guided_candidates_limit=400 when run-365d sees an existing self_improvement_queue.csv
```

The `balanced_365d` candidate profile is intentionally not a first-N nested
grid. It combines:

```text
execution-grid coverage: source x session x stop x management x risk
feature-layer coverage: source x nature x trigger x session/stop/risk layers
```

This keeps runtime reasonable while still checking sessions, local stop models,
management variants, risk buckets, nature families and trigger families.

## Event Store Contract

Outputs:

```text
events.parquet
event_outcomes.parquet
metadata.json
data_quality_report.md
source_session_nature_summary.csv
```

Every outcome row carries:

```text
event_id
source
nature_id
trigger_id
symbol
entry_timestamp_ms
known_at_ms
feature_available_at_ms
session_bucket
entry_model_id
stop_model
management_id
initial_risk_pct
net_r
cost10_r
data_quality_status
```

The key guard is:

```text
feature_available_at_ms <= entry_timestamp_ms
```

Large-runner descriptor guards:

```text
close15 descriptors require delay_min >= 15
close10 descriptors require delay_min >= 10
```

Current replay-backed sources:

```text
failed_pump_structural
  Original structural failed-pump replay source.

failed_pump_075_path
  Structural failed-pump path replay with 0.75R / BE / trail policies and
  local stop variants including last_lower_high.

large_runner_local_high
  Large-runner local-high structural-exit replay source.

large_runner_failed_continuation
  Long anomaly failed-continuation source: close10/close15 stalls after the
  initial long anomaly. Stop is exported only as artifact initial_stop and is
  labeled failure_window_initial_stop until raw replay exports the exact stop
  construction.
```

## Plateau Research Outputs

```text
wfa_results.csv
plateau_candidates.csv
plateau_clusters.csv
candidate_axis_summary.csv
meta_validation.csv
final_holdout_validation.csv
portfolio_meta_validation.csv
model_gate_diagnostics.csv
improvement_plan.csv
portfolio_candidates.csv
portfolio_oos_trades.csv
rejected_reasons.csv
lookahead_audit.csv
source_session_nature_summary.csv
final_report.md
hypothesis_grammar.csv
hypothesis_ledger.csv
plateau_neighborhoods.csv
diversity_scores.csv
self_improvement_queue.csv
self_improvement_report.md
diversified_portfolio_candidates.csv
diversified_portfolio_oos_trades.csv
diversified_portfolio_meta_validation.csv
compute_budget_plan.csv
theoretical_model_gap_analysis.csv
```

The engine evaluates:

```text
rolling IS/OOS windows
development-only candidate generation
final holdout validation
cost10 expectancy
median R
positive-day rate
top-removal stress, default removes the top 35% winning trades
Monte Carlo shuffle/bootstrap stress
top-trade independence
top-symbol independence
symbol/day breadth
efficiency ratio
plateau score-degradation clusters
strict/theoretical model gates
marginal portfolio contribution
same-symbol/time collision removal
hypothesis ledger with stable candidate/family hashes
entry-known hypothesis grammar inventory
novelty/diversity scoring across events, symbols, days and axes
failure-driven guided candidate queue
multi-axis plateau neighborhoods
diversified multi-objective portfolio construction
theoretical model gap analysis
compute budget plan
```

Current strict gates:

```text
calendar_positive_day_rate must be strictly > 60%
top_removal_pct=35%, meaning the remaining 65% of trades must stay positive
final holdout uses the same calendar and top-removal gates
```

## 365d One-Command Result

Command:

```text
python research_tools/short_robust_plateau_engine.py run-365d
```

Output:

```text
event_store=.output/research_cache/short_robust_plateau_engine_365d
event_outcome_rows=179299
events=3283
wfa_windows=8
candidate_universe_rows=5400
base_candidate_universe_rows=5000
guided_candidate_rows=400
development_rows=168391
final_holdout=2026-05-03 -> 2026-06-02 exclusive
candidate_rows=2913
promoted=172
plateau_neighborhoods=138
plateau_neighborhood_pass=112
plateau_neighborhood_strong_pass=64
strict_model_pass=0
theoretical_accept_pass=0
final_holdout_basic_pass=0
report=.output/research_cache/short_robust_plateau_engine_365d/final_report.md
hypothesis_grammar_rows=67
hypothesis_ledger_rows=5400
diversity_score_rows=172
self_improvement_queue_rows=400
self_improvement_queue_new_guided=400
self_improvement_report=.output/research_cache/short_robust_plateau_engine_365d/self_improvement_report.md
theoretical_model_gap_analysis=.output/research_cache/short_robust_plateau_engine_365d/theoretical_model_gap_analysis.csv
```

Lookahead audit:

```text
feature_available_at_or_before_entry: 0 violations / 179299 rows
evaluation_only_columns_not_in_trigger_rules: 0 violations / 7 rules
large_close15_requires_delay15: 0 violations / 61372 rows
large_close10_requires_delay10: 0 violations / 61372 rows
```

Read:

```text
The latest guided rolling scan finds 172 promoted rows after adding 400
queue-generated candidates, mostly structural failed-pump rows. The stricter
theoretical gate still rejects all of them as final strategy candidates: no
sleeve passes top-removal, calendar consistency, plateau robustness and final
holdout together.
```

Latest guided model-gate bottlenecks with top-removal at 35% and calendar gate >60%:

```text
candidate_oos neighborhood_pass: 122 / 172
candidate_oos neighborhood_strong_pass: 74 / 172
candidate_oos top_removal_pass: 15 / 172
candidate_oos calendar_positive_gt_0p60: 0 / 172
candidate_oos median_trades_per_day_ge_3: 0 / 172
final_holdout final_pass_basic: 0 / 172
```

The strongest portfolio-accepted marginal rows in the final report are:

```text
failed_pump_structural|ALL|not_asia_overlap|last_lower_high|full025|risk_5_12|failed_break_le_15
42 marginal trades, avg +0.123R, median +0.227R, cost10 avg +0.106R,
top-trade independence 67.65%.

failed_pump_structural|ALL|not_asia_overlap|last_lower_high|full025|risk_5_12|trigger_all
60 marginal trades, avg +0.032R, median +0.224R, cost10 avg +0.014R,
top-trade independence 27.27%.

failed_pump_structural|ALL|asia_only|recent5|full025|risk_3_8|trigger_all
72 marginal trades, avg +0.068R, median +0.213R, cost10 avg +0.042R,
top-trade independence 40.74%.
```

Portfolio-level read:

```text
selected marginal OOS trades: 130
symbols: 90
avg: +0.109R
median: +0.226R
WR: 67.7%
cost10 avg: +0.090R
MC pass: true
top-35 removal pass: false
calendar_positive_day_rate: 24.2%
calendar_median_trades_per_day: 0
```

Diversified portfolio-level read:

```text
selected marginal OOS trades: 305
symbols: 171
avg: +0.135R
median: +0.221R
WR: 67.9%
cost10 avg: +0.113R
cost10 sum: +34.53R
MC pass: false
top-35 removal pass: false
calendar_positive_day_rate: 40.4%
calendar_median_trades_per_day: 1
```

## Scientist Source Expansion Result

Command:

```text
.\.venv\Scripts\python.exe research_tools\short_robust_plateau_engine.py run-365d --output-dir .output\research_cache\short_robust_plateau_engine_scientist_365d --progress-every 500
```

Output:

```text
runtime: about 15.6 minutes
event_outcome_rows=217910
events=5147
candidate_rows=3030
promoted=168
strict_model_pass=0
theoretical_accept_pass=0
final_holdout_basic_pass=0
```

Source rows:

```text
failed_pump_structural=117927
failed_pump_075_path=31264
large_runner_local_high=61372
large_runner_failed_continuation=7347
```

Lookahead audit:

```text
feature_available_at_or_before_entry: 0 / 217910
evaluation_only_columns_not_in_trigger_rules: 0 / 13
large_close15_requires_delay15: 0 / 61372
large_close10_requires_delay10: 0 / 61372
large_failure_close15_requires_delay15: 0 / 7347
large_failure_close10_requires_delay10: 0 / 7347
path075_source_has_no_delay_trigger_dependency: 0 / 31264
```

Best new source/session/nature read:

```text
large_runner_failed_continuation + europe_us_overlap:
- high_trade_failed_close15_le0: 25 events, cost10_avg +0.198R, WR 72.0%
- high_quote_failed_close15_le0: 36 events, cost10_avg +0.141R, WR 66.7%
- sustained_m1_failed_close15_le0: 27 events, cost10_avg +0.125R, WR 63.0%
- late_buyer_failed_close15_le2: 67 events, cost10_avg +0.121R, WR 66.2%
```

Diversified portfolio:

```text
trades=511
symbols=238
cost10_avg=+0.108R
cost10_sum=+55.28R
WR=63.2%
calendar_positive_day_rate=53.3%
calendar_median_trades_per_day=2
top35 pass=false
MC pass=false
```

Read:

```text
The engine now searches multiple fader natures by source and session, but it
still rejects the result as launch-ready. Calendar >60%, top-35 removal and
final holdout remain the hard blockers.
```

## Controlled Self-Improving Research Loop

The engine now includes the first constrained self-improvement layer. It is not
an unconstrained optimizer. It generates concrete next candidates from
development/WFA failures, diversity scores and entry-known grammar only. Final
holdout fields are ledgered for audit, but they are not used as a generation
target.

```text
1. Hypothesis grammar
   hypothesis_grammar.csv lists allowed event sources, entry-known features,
   sessions, stop/management families, risk buckets and trigger rules.

2. Hypothesis ledger
   hypothesis_ledger.csv assigns stable hypothesis/family hashes, source-data
   fingerprint, generation source, selection scope, rejection reason and final
   holdout audit fields.

3. Diversity / novelty scoring
   diversity_scores.csv penalizes candidates that overlap on events, symbols,
   days or peer sleeves, and marks independent/complementary/redundant sleeves.

4. Failure-driven iteration queue
   self_improvement_queue.csv mutates candidate axes by failure driver:
   calendar/frequency failure -> session and nature diversification;
   top-removal failure -> nature/session/trigger diversification;
   plateau fragility -> adjacent risk buckets;
   MC fragility -> execution and management neighbors.

5. Guided next scan
   A later scan can consume the queue:

   python research_tools/short_robust_plateau_engine.py scan-plateaus \
     --output-dir .output/research_cache/short_robust_plateau_engine_365d \
     --candidate-queue-file .output/research_cache/short_robust_plateau_engine_365d/self_improvement_queue.csv \
     --guided-candidates-limit 400

6. Multi-objective portfolio builder
   diversified_portfolio_candidates.csv selects by marginal expectancy,
   novelty, top-removal, calendar contribution and event/session/source
   overlap penalties. It remains bounded-greedy by design for 16GB RAM / i5.

7. Compute budget controller
   compute_budget_plan.csv documents cheap reject, WFA, stress-test, final
   audit and guided-queue stages. The queue is capped to 400 rows by default.

8. Theory-vs-bot gap analysis
   theoretical_model_gap_analysis.csv lists which theoretical capabilities are
   implemented, partial or still blocked by missing raw event-source replay.
```

365d self-improvement output:

```text
hypothesis_ledger_rows: 5400
diversity_scores_rows: 172
diversity_grade independent_watchlist: 1
diversity_grade complementary_research: 52
diversity_grade redundant: 119
self_improvement_queue_rows: 400
self_improvement_queue new_guided_candidate: 400
plateau_neighborhoods: 138
neighborhood_pass: 112
neighborhood_strong_pass: 64
```

The queue is a research input, not an acceptance shortcut. A proposed candidate
must still survive normal WFA, top-removal, MC, plateau, calendar and final
holdout diagnostics.

The theoretical model still exceeds the bot where new executable event sources
are required: fresh lower-high/failure-retest path replay, minute-path return
driver correlation and truly unseen/live-forward data.

## Smoke Result

Command:

```text
python research_tools/short_robust_plateau_engine.py smoke --max-rows-per-source 8000
```

Output:

```text
event_store=.output/research_cache/short_robust_plateau_engine_meta_smoke2
candidate_rows=286
promoted=6
report=.output/research_cache/short_robust_plateau_engine_meta_smoke2/final_report.md
model_gate_diagnostics.csv created: yes
```

Lookahead audit:

```text
feature_available_at_or_before_entry: 0 violations / 14825 rows
evaluation_only_columns_not_in_trigger_rules: 0 violations / 7 rules
large_close15_requires_delay15: 0 violations / 6861 rows
large_close10_requires_delay10: 0 violations / 6861 rows
```

## Limits

This is a complete neutral plateau mechanism over existing replay artifacts,
not a new raw-candle path replay engine.

It can reject and rank plateau candidates from current artifacts, but it cannot
prove a new lower-high/fresh-retest strategy until that replay is added as a
new event source.

The current 365d artifacts remain development/sandbox data. The final judge
must be a future unseen/live-forward period.
