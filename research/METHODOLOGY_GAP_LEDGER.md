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
| Strategy registry truthfulness for specified-only variants | IMPLEMENTED | Registry exposes executable broad anomaly variants only, rejects specified-only variants explicitly, writes `strategy_implementation_status.csv` as a generated truth table for implemented vs specified-only variants, and registry-level `validate_strategy_horizon()` rejects unknown, arbitrary, mismatched, and specified-but-not-implemented strategy/horizon pairs. | Keep status artifact and registry tests as permanent gates. |
| Horizon ownership contract | IMPLEMENTED | Core methodology states the supported research horizon set and separates Core mechanics from strategy variant selection and registry enforcement. Code has a single Core whitelist/validator for supported research horizons. StrategyMetadata stores selected `horizon_minutes`, semantic `allowed_horizons`, and `default_horizon_minutes`, validates them against the Core whitelist, registry-level `validate_strategy_horizon()` enforces executable strategy/horizon compatibility before prediction, controls, EV, and simulation configs are accepted, and low-level CLI commands now use the same Core whitelist plus registry validation before file IO. Strategy docs explicitly reject unknown/arbitrary horizon suffixes such as h11/h32. | Keep horizon compatibility tests as permanent regression gate. |
| Data source boundary / normalized market data | PARTIAL | Binance Vision cache, normalized CSV boundary, and run-level data snapshot hashing exist. Full all-symbol 380d resource envelope is not proven here. | Add resource smoke docs after full local 380d validation. |
| Data quality gates | IMPLEMENTED | Shared `DataQualityMask` excludes maskable bad 1m candle rows, technical-noise rows, and post-gap warm-up rows before `strategy.generate_triggers`; dataset/schema/source failures remain blocking FAIL. Event-stage audit writes mask exclusion counts and tests cover row-local bad candle exclusion before trigger. | Keep expanding downstream forensic checks as new stages add quality-sensitive inputs. |
| Point-in-time universe | IMPLEMENTED | Universe is now conservative and data-inferred: `symbol_universe_by_day.csv` includes first/last seen timestamps, source status, listing/delisting confidence, explicit missing days between first and last observations, and `eligible_for_cross_section`. Cross-section features use only eligible rows, so missing/delisted-like data conditions do not silently enter market ranks. External exchange listing metadata remains optional; unknown delisting is represented as unknown confidence rather than a false fact. | Keep universe and cross-section eligibility tests as permanent regression gates. |
| Market context engine | PARTIAL | BTC/ETH/systemic context features exist. Coverage and tests are still MVP-level. | Add catalog/audit coverage by context feature family. |
| Feature registry / feature schema | PARTIAL | Feature catalog exists. It is not yet a hard gate for every model feature. | Enforce catalog membership before prediction. |
| Generic online state builder | IMPLEMENTED | Online state is causal and written as canonical `strategy_state_1m.csv` with `anomaly_state_1m.csv` as compatibility alias. | Keep temporal/no-leakage tests as permanent gate. |
| Generic future path builder | IMPLEMENTED | Future path uses post-snapshot candles, pessimistic double-barrier, and Core constants for 5/15/30/60/120/180m raw outcome windows with tests. | Keep horizon/schema tests as permanent gate. |
| Generic label builder | IMPLEMENTED | ATR labels use the Core `SUPPORTED_RESEARCH_HORIZONS` whitelist for 15/30/60/120/180 with strict schema and tests. | Keep label horizon tests as permanent gate. |
| Atlas / discovery layer | PARTIAL | Descriptive atlas exists for MVP 30m. Multi-horizon and deeper strategy slices are incomplete. | Expand after horizon registry and strategy-neutral artifacts. |
| Weekly walk-forward prediction | IMPLEMENTED | Weekly CatBoost + Isotonic exists. Prediction and controls compute purge from active strategy `H_max`; prediction/model artifacts now store `strategy_name`, strategy version/contract, `target_horizon_minutes`, `target_label_column`, and `active_h_max_minutes` so downstream stages do not infer horizon identity. Prediction protocol audit now verifies Core whitelist, registry strategy/horizon compatibility, target label column, active H_max, purge horizon, OOS prediction artifact identity, and model metadata horizon identity. Tests cover multi-strategy H_max, artifact horizon identity, and horizon audit pass/fail rows. | Keep as permanent regression gate. |
| Calibration artifacts | PARTIAL | Raw/calibrated probabilities and metrics exist. Regime/symbol/session calibration breakdowns need expansion. | Add calibration breakdown ledger and audit gates. |
| Sample weighting | MISSING | Methodology allows as-of sample weights, but no explicit policy is implemented. | Add `sample_weight_policy_v1` after audit/horizon fixes. |
| Decision timing | PARTIAL | Decision timing / EV artifact exists. EV reference model must be aligned with simulation. | Align execution reference and store execution model fields. |
| Expected utility | PARTIAL | EV calculations exist and write canonical `strategy_decision_timing.csv` / `strategy_ev_metrics.csv`. Simulation alignment is still incomplete. | Align execution reference and store execution model fields. |
| Pessimistic trade simulation | PARTIAL | Slippage, fees, next open, stop-first collision, no same-symbol parallel positions, and canonical `strategy_trade_simulation.csv` artifacts exist. Stronger independent audit is still needed. | Strengthen audit simulation assumptions. |
| Controls / placebo | PARTIAL | Placebo and baseline controls exist. Full anomaly-specific ablation set needs verification/completion. | Complete listed anomaly controls and feature ablations. |
| Protocol audit | PARTIAL | Protocol audit exists, prediction-stage horizon consistency is audited, and `run-research` now writes an independent artifact-driven forensic audit after simulation and exits non-zero on any forensic `FAIL`. Holdout mode is enforced before downstream stages, and forensic audit checks root run-manifest completeness. Remaining gap: the forensic layer is still an initial proof set, not yet the full final research-ready audit of data-quality mask, universe, feature catalog, controls, EV/simulation alignment, and rejection funnel. | Keep expanding forensic checks as later methodology gaps become implemented. |
| Reproducibility ledger | IMPLEMENTED | `run-research` writes a root-level `strategy_run_config.csv` / `anomaly_run_config.csv` with strategy identity, target horizon, active H_max, holdout/protocol freeze metadata, forensic audit status, data snapshot hash, config hash, dependency versions, methodology ledger status, and a root `artifact_manifest.json` covering run artifacts. Forensic audit checks root manifest completeness. | Keep manifest completeness as a permanent forensic gate. |
| Final holdout governance | IMPLEMENTED | `run-research` now has explicit `is` and `frozen_holdout` modes. Default `is` mode filters downstream input to exclude the final locked holdout. `frozen_holdout` requires an explicit `protocol_freeze_id` and records an approved holdout access row before downstream stages can read the full period. | Keep run-research governance and holdout-lock tests as permanent gates. |
| Live/shadow/production | OUT_OF_SCOPE | Intentionally absent. | Do not implement in this phase. |

## Anomaly strategy implementation matrix

| Area | Status | Current evidence / gap | Required patch direction |
| :--- | :--- | :--- | :--- |
| Broad anomaly strategy variants h15/h30/h60 | IMPLEMENTED | Variants are registry-backed and executable. | Keep tests around contract validation. |
| Broad anomaly trigger breadth | PARTIAL | Detector exists, but component coverage should be explicit for one-shot, burst, grind, volume-only, breakout, session, and market-wide impulse. | Add explicit `trigger_component` accounting and tests. |
| Trigger deduplication / anti-pyramiding | IMPLEMENTED | Same-symbol cooldown/dedup policy exists. | Keep as permanent regression test. |
| Anomaly event lifecycle | PARTIAL | Online state lifecycle exists. Structural state fields are incomplete. | Add structural high/low/break/compression fields. |
| Running high/low as-of semantics | IMPLEMENTED | State uses data available only up to `state_time`. | Keep no-leakage tests. |
| Post-anomaly extension strategy | MISSING | Specified, but not implemented in registry. | Add dedicated strategy class and tests. |
| Post-pump distribution strategy | MISSING | Specified, but not implemented in registry. | Add dedicated strategy class and tests. |
| 180m anomaly horizon | IMPLEMENTED | 180m fields are supported in future paths, labels, prediction target dispatch, controls, EV target dispatch, artifact schemas, and tests. | Keep post-pump/post-extension 180m blocked only by strategy implementation rows. |
| Relaxed geometry features | MISSING | Shelf/sweep/consolidation continuous features are not fully implemented. | Add strategy-specific feature module and catalog rows. |
| Required anomaly controls | PARTIAL | Controls exist, but full anomaly-specific ablation/control list needs completion check. | Add missing baselines/ablations. |
| Reject reasons | PARTIAL | Reject constants exist, but funnel coverage is not complete across every stage. | Add rejection funnel artifact and tests. |
| Strategy-specific artifact aliases | IMPLEMENTED | Strategy-owned stages write canonical `strategy_*` artifacts first and keep `anomaly_*` only as compatibility aliases, with schema/writer tests. | Keep canonical-first writer tests as permanent gate. |
| Strategy/Core separation | PARTIAL | Artifact boundaries are strategy-neutral. Some internal module/function names remain anomaly-specific and should be generalized after registry/status cleanup. | Refactor internal names after higher-priority governance/audit gaps. |

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
11. `strategy: make registry status self-auditing`.
12. `strategy: complete broad anomaly trigger component accounting`.
13. `strategy: add 180m horizon support or remove 180m promises` - implemented.
14. `strategy: implement post-pump distribution variants`.
15. `strategy: implement post-anomaly extension variants`.
16. `features: add structural state and relaxed geometry features`.
17. `methodology: complete controls, rejection funnel, run manifest, and docs sync`.

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
