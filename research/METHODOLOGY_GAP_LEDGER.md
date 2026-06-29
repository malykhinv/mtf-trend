# Methodology gap ledger

Status source for the gap between the documented research methodology, the anomaly strategy spec, and the current implementation.

This file is intentionally part of the repository. It prevents the project from silently treating a documented rule as implemented when it is only specified, partially implemented, or out of scope for the current research-only phase.

## Scope boundary

In scope for "100% without live":

```text
strategy-independent Core methodology
anomaly strategy research variants
offline data quality / universe / feature / label / prediction / control / EV / simulation artifacts
holdout governance
protocol audit
reproducibility ledger
unit and smoke tests
```

Out of scope for this ledger phase:

```text
shadow live
production live
exchange order execution
portfolio risk engine
position sizing for real capital
scheduler / daemon runtime
latency-sensitive market-data stream
```

Rule:

```text
Do not mark live, shadow, execution, or portfolio features as missing research gaps.
They are intentionally absent until offline research evidence passes audit.
```

## Status values

| Status | Meaning |
| :--- | :--- |
| `IMPLEMENTED` | Code, artifact contract, docs, and tests exist for the current research scope. |
| `PARTIAL` | Some code exists, but contract coverage, audit depth, strategy coverage, horizon coverage, or tests are incomplete. |
| `MISSING` | Methodology/spec requires it, but the implementation is absent or only a placeholder. |
| `OUT_OF_SCOPE` | Deliberately excluded from the current offline research scope. |
| `UNKNOWN` | Cannot be claimed from the current snapshot; requires direct verification before planning a patch. |

Rule:

```text
A status may move to IMPLEMENTED only when the implementation and tests are both present.
A passing smoke run alone is not enough if the methodology invariant is not independently audited.
```

## 100% research-ready definition

The offline research system is considered complete when all in-scope rows in this ledger are `IMPLEMENTED` and the final validation gate passes:

```text
python -m compileall main.py src tests zip_project.py
python -m pytest tests
python main.py run-research broad_anomaly_v1_h30 --days <small fixture/cache smoke window>
```

Additionally:

```text
no strategy-specific if/else inside Core Engine
no legacy_quarantine imports from new code
no silent fallback for unknown data schemas
H_max purge is enforced from active strategy horizons
Horizon ownership contract is explicit: Core supports, Strategy selects via variants, Registry enforces
final holdout cannot be read before protocol freeze
protocol audit independently validates temporal and artifact invariants
canonical strategy_* artifacts exist, with anomaly_* only as aliases
```

## Core methodology implementation matrix

| Area | Status | Current evidence / gap | Required patch direction |
| :--- | :--- | :--- | :--- |
| Legacy quarantine boundary | IMPLEMENTED | New code is under `src/anomaly_science`; tests include no-legacy-import checks. | Keep as permanent gate. |
| BaseStrategy contract | IMPLEMENTED | Strategy metadata and trigger/custom-feature contract exist. | Keep stable unless explicit contract version bump. |
| Strategy registry for broad anomaly | IMPLEMENTED | `broad_anomaly_v1_h15/h30/h60` are registry-backed. | Keep registry honest. |
| Strategy registry truthfulness for specified-only variants | IMPLEMENTED | Registry exposes executable anomaly variants explicitly, writes `strategy_implementation_status.csv` as a generated truth table, and registry-level `validate_strategy_horizon()` rejects unknown, arbitrary, and mismatched strategy/horizon pairs. The current anomaly strategy spec has no remaining specified-only executable gaps. | Keep status artifact and registry tests as permanent gates. |
| Horizon ownership contract | IMPLEMENTED | Core methodology states the supported research horizon set and separates Core mechanics from strategy variant selection and registry enforcement. Code has a single Core whitelist/validator for supported research horizons. StrategyMetadata stores selected `horizon_minutes`, semantic `allowed_horizons`, and `default_horizon_minutes`, validates them against the Core whitelist, registry-level `validate_strategy_horizon()` enforces executable strategy/horizon compatibility before prediction, controls, EV, and simulation configs are accepted, and low-level CLI commands now use the same Core whitelist plus registry validation before file IO. Strategy docs explicitly reject unknown/arbitrary horizon suffixes such as h11/h32. | Keep horizon compatibility tests as permanent regression gate. |
| Data source boundary / normalized market data | IMPLEMENTED | Binance Vision cache, normalized CSV boundary, run-level data snapshot hashing, and export-level `cache_export_coverage.csv` / `cache_export_manifest.json` proof artifacts exist. Full local 380d export proof is recorded in `research/validation/cache_export_380d_validation.json`: 2025-06-03..2026-06-17, 796 exported perpetual symbols, 344,895,197 1m rows, 0 missing UTC days, 0 duplicate 1m rows, 0 unclassified 1m gaps. The only missing 1m rows are 3,570 explicitly classified settlement-transition rows across AIAUSDT/CVXUSDT/LITUSDT/PUMPUSDT/SLPUSDT with `{symbol}SETTLED` sibling evidence. Fixed-date delivery contracts are excluded from perpetual exports by default and recorded in the manifest. `run-research` uses the full available cache period when `--days` is omitted. | Keep `validate-cache-export-proof --expected-days 380 --allow-settlement-transition-gaps` as the permanent full-cache proof gate. |
| Compact all-symbol `run-research` executability | IMPLEMENTED | `run-research <strategy> --days N` creates output automatically, uses the full cache period when `--days` is omitted, and auto-scales default IS holdout for short windows while preserving the final holdout. The short-window rule keeps up to 8 non-holdout research days when available, so weekly WFA can produce OOS proof on compact windows larger than a 2d smoke. The all-symbol 2d command `python main.py run-research broad_anomaly_v1_h30 --days 2` completed with forensic `0` FAIL rows and expected WARN rows from the intentionally tiny window. The single-command pipeline now releases stage-local memory between major stages. Atlas reads strict state/future/feature CSV boundaries in bounded chunks and enforces row alignment per chunk. The 9d all-symbol state/future/feature-matrix/labels proof completed through `strategy_outcome_labels.csv` with `7,308,031` rows and label-stage audit `12` PASS / `0` FAIL rows. Downstream 9d atlas/prediction/EV/simulation proof remains the next compact-run validation target. | Keep compact-run smoke plus full tests as regression gates; use >=9d compact windows when non-empty OOS/EV/simulation proof is required. |
| Data quality gates | IMPLEMENTED | Shared `DataQualityMask` excludes maskable bad 1m candle rows, technical-noise rows, and post-gap warm-up rows before `strategy.generate_triggers`; dataset/schema/source failures remain blocking FAIL. Event-stage audit writes mask exclusion counts and tests cover row-local bad candle exclusion before trigger. | Keep expanding downstream forensic checks as new stages add quality-sensitive inputs. |
| Point-in-time universe | IMPLEMENTED | Universe is now conservative and data-inferred: `symbol_universe_by_day.csv` includes first/last seen timestamps, source status, listing/delisting confidence, explicit missing days between first and last observations, and `eligible_for_cross_section`. Cross-section features use only eligible rows, so missing/delisted-like data conditions do not silently enter market ranks. External exchange listing metadata remains optional; unknown delisting is represented as unknown confidence rather than a false fact. Dataset-date and first/last-seen aggregation is vectorized so all-symbol data audit can execute without per-row universe scans. | Keep universe and cross-section eligibility tests as permanent regression gates. |
| Market context engine | IMPLEMENTED | BTC-relative, cross-sectional market-relative, idiosyncratic momentum, simultaneous anomaly, systemic cluster, and market-shock id features are materialized in `strategy_feature_matrix.csv`, declared in `strategy_feature_catalog.csv`, and independently checked by forensic audit for required field coverage and value ranges. | Keep market-context forensic and feature-matrix tests as permanent gates. |
| Feature registry / feature schema | IMPLEMENTED | Prediction now validates every numeric/bool state and feature-matrix model feature against `build_default_feature_catalog()` before training; undeclared model features raise `PredictionInputError`. Catalog rows cover the current prediction state feature set, and tests cover both accepted catalog-backed features and rejection of an undeclared feature. Feature-matrix CLI writes the canonical matrix artifact through a strict streaming writer, preserves state/feature row order for downstream row-alignment, builds only a compact per-snapshot state summary for cross-section fields, moves cross-section rank cache into a temporary SQLite lookup, streams grouped symbol CSV boundaries so only the current symbol candle series plus BTC context is held while writing feature rows, serializes rows from dataclass slots after one schema validation instead of per-row deep dataclass conversion, and removes temporary lookup files before stage exit. Identical aliases remain hardlink-first for compatibility only. Core feature-matrix hot paths now use explicit as-of OI indexing, exact rolling moment prefix sums, exact cached rolling medians, and indexed liquidation event windows instead of repeated row scans. | Keep catalog membership, grouped-boundary, row-order, and temp-cleanup tests as permanent gates. |
| Generic online state builder | IMPLEMENTED | Online state is causal and written as canonical `strategy_state_1m.csv` with `anomaly_state_1m.csv` as compatibility alias. The CLI reads candles/events through strict CSV boundaries, processes grouped candle input one symbol at a time, streams state rows through the canonical schema, serializes `StrategyState1mRow` directly from slots after one schema validation for multi-million-row outputs, and creates a hardlink-first compatibility alias, avoiding pandas input-frame materialization, full-market candle index materialization, multi-million-row output list materialization, per-row deep dataclass conversion, and duplicate alias bytes when supported by the filesystem. | Keep temporal/no-leakage tests as permanent gate. |
| Generic future path builder | IMPLEMENTED | Future path uses post-snapshot candles, pessimistic double-barrier, and Core constants for 5/15/30/60/120/180m raw outcome windows with tests. The CLI writer streams rows through the strict canonical schema, reads grouped state/candle CSV boundaries one symbol at a time, serializes `FuturePathRow` directly from slots after one schema validation for multi-million-row outputs, creates identical aliases hardlink-first, and treats zero as-of ATR as explicit missing ATR fields rather than fallback or crash. | Keep horizon/schema tests as permanent gate. |
| Generic label builder | IMPLEMENTED | ATR labels use the Core `SUPPORTED_RESEARCH_HORIZONS` whitelist for 15/30/60/120/180 with strict schema and tests. The CLI labels stage streams row-aligned state/future artifacts, parses only labels-owned strict fields, writes canonical labels incrementally, serializes `StrategyOutcomeLabelRow` directly from slots after one schema validation for multi-million-row outputs, and keeps `anomaly_*` as a hardlink-first compatibility alias. | Keep label horizon, row-aligned join, and strict schema tests as permanent gates. |
| Atlas / discovery layer | IMPLEMENTED | Descriptive atlas now writes 15/30/60/120/180m ATR-normalized slices, session/market-context/speed/systemic contexts, and relaxed shelf/sweep/consolidation geometry response surfaces. Atlas remains discovery-only and is not consumed by decision logic. | Keep atlas schema/tests as permanent gate; add only descriptive slices, never trade rules. |
| Weekly walk-forward prediction | IMPLEMENTED | Weekly CatBoost + Isotonic exists. Prediction and controls compute purge from active strategy `H_max`; prediction/model artifacts now store `strategy_name`, strategy version/contract, `target_horizon_minutes`, `target_label_column`, `active_h_max_minutes`, `supervised_anchor_policy_id`, and supervised anchor offsets so downstream stages do not infer horizon or supervised-population identity. The default supervised population is the bounded `registered_state_lattice_v1` offsets `0,5,10,15,30,60`; `t0_only_v1` remains an explicit ablation/baseline. Prediction protocol audit now verifies Core whitelist, registry strategy/horizon compatibility, target label column, active H_max, purge horizon, OOS prediction artifact identity, and model metadata horizon identity. Tests cover multi-strategy H_max, artifact horizon identity, anchor lattice filtering, and horizon audit pass/fail rows. | Keep as permanent regression gate; never tune anchor offsets after OOS review without a new registered policy id. |
| Calibration artifacts | IMPLEMENTED | Raw/calibrated probabilities, aggregate metrics, and calibration breakdowns by session, week, month, symbol, systemic regime, market-shock group, alpha-decay bucket, and trigger-age bucket are written and independently audited. | Keep monitoring minimum row counts in real 380d runs. |
| Sample weighting | IMPLEMENTED | Prediction config now requires explicit `sample_weight_policy`; the default `event_anchor_normalized_v1` uses only `(symbol,event_id)` grouping to keep each event's total split weight bounded when registered state-lattice anchors produce multiple rows. CatBoost receives validated fit-split sample weights, model metadata and training diagnostics record the policy and weight sums, run config records the policy, and prediction protocol audit emits `sample_weight_policy_explicit_and_asof_safe`. Tests reject unknown policies and verify artifact/audit output. | Keep policy-version tests as permanent gate; any non-event/as-of-safe weighting requires a new policy version and train-only proof. |
| Decision timing | IMPLEMENTED | Decision timing writes canonical `strategy_decision_timing.csv` / `anomaly_decision_timing.csv` with explicit `execution_reference_model`, EV entry price basis, and cost model fields. The EV stage run config and protocol audit record `execution_reference_model_aligned_between_ev_and_simulation`, and tests verify artifact roundtrip/run output. | Keep execution-reference schema tests as permanent gate. |
| Expected utility | IMPLEMENTED_PROXY | MVP1 EV is computed before simulation from OOS probabilities, causal structural stop/target anchors, fees, slippage, and the shared execution/cost model contract, but it is explicitly `utility_model_kind=nature_proxy`, `utility_evidence_status=NON_FINAL`, and `utility_evidence_claim_allowed=false`. `strategy_barrier_outcomes.csv` now records labels-only realized target-first / stop-first / timeout outcomes for later utility modeling, but no realized barrier outcome model is trained or allowed to drive decisions yet. ATR remains descriptive context only and cannot set a physical barrier. Simulation validates policy ids, anchors, reference prices, triggers, and cost fields before consuming a decision. | Keep EV/simulation structural-policy alignment tests, barrier-outcome boundary tests, and utility proxy metadata forensic checks as permanent gates; do not claim final EV proof from nature-proxy utility until a calibrated realized barrier outcome model exists. |
| Pessimistic trade simulation | IMPLEMENTED | Slippage, fees, next open, stop-first collision, causally confirmed swing trailing, registered partial closes, and canonical `strategy_trade_simulation.csv` artifacts exist. Each policy/fraction is a separate research variant; metrics and anti-overlap are scoped by variant and never pooled into one fictitious portfolio. | Keep simulation forensic and variant-isolation tests as permanent gates. |
| Controls / placebo | IMPLEMENTED | Control artifacts include random/time/symbol-shuffled placebo labels, global/session/event-time/price-path/volume/BTC baselines, anomaly heuristic baselines, CVD/OI/liquidation ablations, idiosyncratic/systemic subsets, and simulation-level always-no-trade/random-entry controls. Forensic audit now independently verifies the required control names and simulation control metrics from written artifacts. | Keep required-control forensic tests as permanent gate. |
| Protocol audit | IMPLEMENTED | Protocol audit exists, prediction-stage horizon consistency is audited, and `run-research` writes an independent artifact-driven forensic audit after simulation and exits non-zero on any forensic `FAIL`. Holdout mode is enforced before downstream stages. Forensic audit now checks root run-manifest completeness, data-quality mask enforcement, point-in-time universe eligibility, temporal contract, purge/H_max, prediction/model horizon identity, canonical/alias consistency, EV/simulation alignment, pessimistic simulation price/cost assumptions, no-parallel simulation positions, required controls completeness, calibration breakdown completeness, rejection-funnel completeness, full feature-catalog coverage, market-context feature coverage, and stage-audit FAIL rows. Large canonical/alias pairs are checked through hardlink fast-path or streaming comparison, not full pair materialization. | Keep forensic audit as permanent hard gate; add new artifact-level checks whenever new research artifacts become in-scope. |
| Reproducibility ledger | IMPLEMENTED | `run-research` writes a root-level `strategy_run_config.csv` / `anomaly_run_config.csv` with strategy identity, target horizon, active H_max, holdout/protocol freeze metadata, forensic audit status, data snapshot hash, config hash, dependency versions, methodology ledger status, and a root `artifact_manifest.json` covering run artifacts. Forensic audit checks root manifest completeness. | Keep manifest completeness as a permanent forensic gate. |
| Final holdout governance | IMPLEMENTED | `run-research` now has explicit `is` and `frozen_holdout` modes. Default `is` mode filters downstream input to exclude the final locked holdout. `frozen_holdout` requires an explicit `protocol_freeze_id` and records an approved holdout access row before downstream stages can read the full period. | Keep run-research governance and holdout-lock tests as permanent gates. |
| Live/shadow/production | OUT_OF_SCOPE | Intentionally absent. | Do not implement in this phase. |

## Anomaly strategy implementation matrix

| Area | Status | Current evidence / gap | Required patch direction |
| :--- | :--- | :--- | :--- |
| Broad anomaly strategy variants h15/h30/h60 | IMPLEMENTED | Variants are registry-backed and executable. | Keep tests around contract validation. |
| Broad anomaly trigger breadth | IMPLEMENTED | Detector persists causal `trigger_component` / `trigger_components` accounting for one-shot spike, fast burst, grind pump, volume-only anomaly, range expansion, breakout, pump-inside-noise, session activity burst, and market-wide impulse. Unit tests cover each advanced trigger family without adding trade/PnL logic. | Keep trigger-family tests as permanent gate; new families must remain causal audit metadata, not trade setups. |
| Trigger deduplication / anti-pyramiding | IMPLEMENTED | Same-symbol cooldown/dedup policy exists. | Keep as permanent regression test. |
| Anomaly event lifecycle | IMPLEMENTED | Online state lifecycle now materializes causal confirmed structural high/low levels, level timestamps, distances to those levels, and keeps missing levels explicit until confirmation. | Keep no-leakage tests around structural state. |
| Running high/low as-of semantics | IMPLEMENTED | State uses data available only up to `state_time`. | Keep no-leakage tests. |
| Post-anomaly extension strategy | IMPLEMENTED | `post_anomaly_extension_v1_h60/h120/h180` are registry-backed executable strategies. Trigger starts from a causal broad anomaly source event, then emits only the first late extension row when closed 1m price continues in the seed direction by the configured as-of threshold; event_start_time remains the source anomaly start and OI/liquidations are required before trigger generation. | Keep registry, horizon, trigger-frame, and event-artifact tests as permanent gates. |
| Post-pump distribution strategy | IMPLEMENTED | `post_pump_distribution_v1_h60/h120/h180` are registry-backed executable fixed-horizon strategies. Trigger uses causal `daily_return_asof_t > 0.30`, same-minute `trade_count_market_percentile_asof_t > 0.99`, and first trigger per symbol/UTC-day. OI/liquidations are optional enrichment with explicit missingness; the canonical pump-fade close race is a separate horizon-free protocol. | Keep registry, horizon, trigger-frame, missing-data, and event-artifact tests as permanent gates. |
| Pump-fade incremental OI value | PARTIAL | Paired runner compares no-OI and with-OI models on identical `oi_available=true` rows, excludes availability from features, retains blind/shuffled controls in both arms, persists verification predictions, and applies a 2,000-draw paired group bootstrap. The 30-symbol smoke delta was positive but its 95% interval crossed zero; no stable category survived. | Complete the running 796-symbol paired development run. Do not claim OI value unless both control suites pass and the paired interval excludes zero; pristine evidence still requires a frozen untouched holdout. |
| 180m anomaly horizon | IMPLEMENTED | 180m fields are supported in future paths, labels, prediction target dispatch, controls, EV target dispatch, artifact schemas, tests, and executable post-pump/post-extension strategy variants. | Keep horizon compatibility tests as permanent gate. |
| Relaxed geometry features | IMPLEMENTED | Feature matrix now materializes the required continuous shelf/sweep/consolidation geometry fields from data available <= state_time; raw shelf prices are audit-only and ATR/relative coordinates are cataloged as model features. | Keep point-in-time equivalence tests and anti-binary catalog checks. |
| Required anomaly controls | IMPLEMENTED | Anomaly-specific controls include always-follow, always-fade, fade-after-extension, follow-early-squeeze, no-CVD/no-OI/no-liquidation ablations, and idiosyncratic/systemic subsets. Forensic audit verifies the complete required set in `strategy_baseline_comparison.csv`. | Keep required-control forensic tests as permanent gate. |
| Reject reasons | IMPLEMENTED | `strategy_rejection_funnel.csv` / `anomaly_rejection_funnel.csv` now records artifact-driven lineage across data quality, universe, events, state, future paths, labels, prediction, decision, and simulation. Large included stage rows are represented as explicit summary rows with `row_count` so the funnel does not duplicate multi-million-row artifacts. Excluded rows require explicit `reason_code`; forensic audit fails if the funnel is missing, empty, has invalid statuses, or omits a required stage. | Keep funnel completeness, compact included-row summaries, and explicit reason-code tests as permanent gates. |
| Strategy-specific artifact aliases | IMPLEMENTED | Strategy-owned stages write canonical `strategy_*` artifacts first, protocol audit rows name canonical `strategy_*` artifacts as the primary boundary, and `anomaly_*` remains only as compatibility aliases, with schema/writer/audit-wording tests. | Keep canonical-first writer and protocol-audit wording tests as permanent gates. |
| Strategy/Core separation | IMPLEMENTED | Artifact boundaries are strategy-neutral. Core contracts now expose strategy-neutral `StrategyEvent`, `StrategyState1mRow`, `StrategyFeatureMatrixRow`, and `StrategyOutcomeLabelRow` names, with old `Anomaly*` names retained only as compatibility aliases. Core loaders/builders now expose and use `strategy_*` entrypoints while `anomaly_*` functions remain compatibility wrappers for documented alias artifacts. | Keep compatibility alias tests and avoid adding strategy-specific branches inside Core. |

## Patch queue implied by this ledger

1. `methodology: add implementation gap ledger` — this patch.
2. `methodology: enforce active-horizon H_max purge` - implemented in this patch.
3. `methodology: canonicalize strategy-neutral artifacts` - implemented in this patch.
4. `methodology: integrate holdout freeze into run-research` - implemented in this patch.
5. `methodology: document horizon ownership contract` - implemented in this patch.
6. `methodology: add Core supported horizon constants` - implemented in this patch.
7. `methodology: enforce registry horizon compatibility` - implemented in this patch.
8. `methodology: add prediction artifact horizon identity` - implemented in this patch.
9. `methodology: add independent forensic protocol audit` - implemented.
10. `methodology: hard-gate run-research on forensic audit` - implemented in this patch.
11. `methodology: enforce feature catalog membership before prediction` - implemented.
12. `methodology: add explicit sample weight policy` - implemented.
13. `methodology: align EV and simulation execution reference` - implemented.
14. `methodology: strengthen simulation forensic audit` - implemented.
15. `methodology: audit required controls completeness` - implemented.
16. `methodology: audit market context feature coverage` - implemented.
17. `strategy: make registry status self-auditing` - implemented.
18. `strategy: complete broad anomaly trigger component accounting` - implemented.
19. `strategy: add 180m horizon support or remove 180m promises` - implemented.
20. `strategy: implement post-pump distribution variants` - implemented.
21. `strategy: implement post-anomaly extension variants` - implemented.
22. `features: add structural state and relaxed geometry features` - implemented.
23. `methodology: complete rejection funnel and docs sync` - implemented.
24. `strategy: complete broad anomaly trigger breadth` - implemented.
25. `methodology: complete final forensic protocol audit coverage` - implemented.
26. `methodology: neutralize core contract names` - implemented.
27. `methodology: validate full 380d cache export proof` - implemented.

Rule:

```text
Patch order may change only if a later patch is blocked by a stricter invariant discovered earlier.
Do not optimize PnL or thresholds while this ledger still has unresolved MISSING/PARTIAL research-methodology rows.
```

## Maintenance rule

Every methodology or strategy patch must update this file when it changes any row status, scope definition, or completion criterion.

Forbidden:

```text
claiming methodology completeness in README, PATCH_LOG, RESEARCH_STATE, or chat while this ledger still has in-scope MISSING rows
marking a row IMPLEMENTED without tests
hiding a MISSING row by moving it to OUT_OF_SCOPE unless it is live/shadow/production or explicitly removed from methodology/spec
```
