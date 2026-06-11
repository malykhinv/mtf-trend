# Short Robust Plateau Engine

Date: 2026-06-11
Status: implemented / one-command 365d preset passed.
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

## Plateau Research Outputs

```text
wfa_results.csv
plateau_candidates.csv
plateau_clusters.csv
candidate_axis_summary.csv
portfolio_candidates.csv
portfolio_oos_trades.csv
rejected_reasons.csv
lookahead_audit.csv
final_report.md
```

The engine evaluates:

```text
rolling IS/OOS windows
cost10 expectancy
median R
positive-day rate
top-trade independence
top-symbol independence
symbol/day breadth
efficiency ratio
plateau clusters
marginal portfolio contribution
same-symbol/time collision removal
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
candidate_universe_rows=5000
candidate_rows=2587
promoted=78
plateau_clusters=64
strict_plateau_pass_clusters=10
report=.output/research_cache/short_robust_plateau_engine_365d/final_report.md
```

Lookahead audit:

```text
feature_available_at_or_before_entry: 0 violations / 179299 rows
evaluation_only_columns_not_in_trigger_rules: 0 violations / 7 rules
large_close15_requires_delay15: 0 violations / 61372 rows
large_close10_requires_delay10: 0 violations / 61372 rows
```

Best robust-looking promoted sleeves in the generated report are mostly
structural failed-pump rows. The large-runner source produced some promoted
rows, but the top one has only 8 OOS trades and fails marginal portfolio
acceptance after collision removal, so it is watchlist material rather than
strategy proof.

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

## Smoke Result

Command:

```text
python research_tools/short_robust_plateau_engine.py smoke --max-rows-per-source 8000
```

Output:

```text
event_store=.output/research_cache/short_robust_plateau_engine_smoke
candidate_rows=265
promoted=21
report=.output/research_cache/short_robust_plateau_engine_smoke/final_report.md
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
