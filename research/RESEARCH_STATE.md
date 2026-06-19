# Research state

Current phase: anomaly_science rebirth.

Active rule:
- `legacy_quarantine` is reference-only.
- New code must not import legacy modules.
- Active research strategy is the anomaly family documented in `docs/strategies/anomaly_strategy.md`.
- First target is MVP 1: honest research pipeline through calibrated prediction, decision timing, EV, pessimistic simulation, controls, and holdout governance; not live trading.
- Current implemented slice: data source boundary, data quality, point-in-time universe skeleton, broad anomaly events via BaseStrategy `generate_triggers`, online 1m anomaly state, raw future paths through 180m, feature catalog/matrix, descriptive strategy nature atlas, descriptive future-nature outcome labels for Core-supported 15/30/60/120/180m horizons, Core horizon whitelist/validators, StrategyMetadata selected/allowed/default horizon semantics, registry-enforced strategy/horizon compatibility, CLI-level horizon whitelist/strategy-pair validation, self-describing prediction/model horizon artifacts, horizon-consistency protocol audit rows, weekly frozen walk-forward prediction with active-strategy `H_max` purge, calibration artifacts, placebo/control tests, decision timing with EV, simplified pessimistic trade simulation, run-research holdout freeze/governance, and canonical `strategy_*` artifacts with `anomaly_*` compatibility aliases.
- Future paths remain raw outcomes.
- Atlas outcome bins are descriptive discovery bins only, not decision rules, EV, PnL, or trade simulation.
- Outcome labels are descriptive scenario targets for walk-forward prediction calibration, not trading labels.
- Placebo/control rows are negative scientific controls; passing or failing them does not create a trade signal.
- Decision timing and EV are research decision artifacts, not live trade commands.
- Trade simulation is simplified and pessimistic; it is not shadow live or production execution.
- Shadow live and production live are still intentionally absent.
- Methodology/strategy completion is tracked in `research/METHODOLOGY_GAP_LEDGER.md`; do not claim research completeness while that ledger has in-scope `MISSING` rows or unaudited `PARTIAL` rows.
- Horizon ownership is documented, code-side supported horizon constants live in `anomaly_science.contracts.horizons`, and StrategyMetadata now validates selected `horizon_minutes`, semantic `allowed_horizons`, and `default_horizon_minutes`. Registry-level compatibility validation now rejects arbitrary, mismatched, unknown, and specified-but-not-implemented strategy/horizon pairs before prediction, controls, EV, and simulation configs are accepted; low-level CLI target-horizon commands use the same Core whitelist and validate the resolved strategy/horizon pair before file IO. Strategy registry output now separates executable variants in `strategy_registry.csv` from specified-only variants in `strategy_implementation_status.csv`.
- Horizon ownership is explicit in methodology docs: Core supports the fixed research horizon set, Strategy selects semantic variants from that set, and Registry must enforce the selected strategy/horizon pair before train/OOS/controls/EV/simulation.

Current local base commit before the active run-research holdout freeze patch: `db17b76a`, verified with `git rev-parse --short HEAD`.
Last local validation on 2026-06-19:
- `.venv\Scripts\python.exe -m pytest tests\test_research_run.py tests\test_holdout_governance.py tests\test_artifact_schemas.py`
- `.venv\Scripts\python.exe -m compileall src main.py tests zip_project.py`
- `.venv\Scripts\python.exe -m pytest`


Current horizon audit patch generated from uploaded snapshot `project_20260619_111337.zip` plus H2 v2, H3, H4 v2, H5, and H6:
- `methodology: audit horizon consistency`
  - Adds protocol audit rows for Core whitelist, registry compatibility, target label column, active H_max, purge horizon, prediction artifact identity, and model metadata identity.
  - Patch status: PROPOSED until applied and verified locally.

Current CLI horizon validation patch generated from uploaded snapshot `project_20260619_111337.zip` plus H2 v2, H3, and H4 v2:
- `methodology: align CLI horizon validation`
  - Makes low-level prediction/control/EV/simulation CLI commands accept only the Core-supported horizon whitelist.
  - Adds optional `--strategy-name` and registry validation before downstream file IO.
  - Patch status: PROPOSED until applied and verified locally.

Current registry horizon compatibility patch generated from uploaded snapshot `project_20260619_111337.zip` plus H2 v2 and H3:
- `methodology: enforce registry horizon compatibility`
  - Adds `validate_strategy_horizon(strategy_name, horizon_minutes)` in the strategy registry.
  - Enforces executable strategy/horizon pairs for prediction, controls, EV, and simulation configs.
  - Patch status: PROPOSED until applied and verified locally.


Current horizon metadata patch generated from uploaded snapshot `project_20260619_111337.zip` plus H2 v2:
- `methodology: add StrategyMetadata horizon semantics`
  - Adds selected/allowed/default horizon fields to StrategyMetadata.
  - Validates StrategyMetadata horizons against the Core-supported research horizon whitelist.
  - Exports allowed/default horizons in strategy registry and run-config artifacts.
  - Patch status: PROPOSED until applied and verified locally.


Current documentation-only patch generated from uploaded snapshot `project_20260619_103805.zip`:
- `methodology: document horizon ownership contract`
  - Clarifies that Core-supported horizons are fixed at 15/30/60/120/180 for the current contract.
  - Clarifies that Strategy Spec selects explicit semantic variants only; arbitrary suffixes such as h11/h32 are invalid without a Core contract/schema patch.
  - Clarifies that Registry is the enforcement layer.
  - Patch status: PROPOSED until applied and verified locally.


Pending local patches from uploaded snapshot `project_20260618_115220.zip`:
- `perf: remove Binance Vision cache per-block IO amplification`
  - Fixes Binance Vision cache per-block ledger/disk-scan IO amplification.
  - Excludes top-level `tmp/` generated artifacts from project zip creation.
- `perf: prune Binance Vision cache work by requested date range`
  - Skips symbols without kline archives in the requested date range.
  - Writes `metadata/skipped_symbols.csv`.
  - Adds `metadata/symbol_completion.csv` and deletes completed per-symbol parts after final parquet compaction.
- `perf: exclude Binance delivery contracts from perpetual cache`
  - Unconditionally skips `*_YYMMDD` delivery/fixed-date contracts.
  - Records skipped delivery contracts with `reason=delivery_contract_excluded`.
  - Adds no new CLI flag; the cache command remains compact.
- `perf: make Binance Vision cache startup visible`
  - Adds immediate startup stage logs and preflight progress for archive range filtering.
  - Bakes optimized network defaults into the compact cache command: timeout 45s, connect timeout 8s, retries 2.
- `perf: replace per-symbol Binance Vision preflight with run-level klines index`
  - Replaces per-symbol archive range preflight with one run-level monthly klines index.
  - Avoids 873× symbol-index S3 listing during startup.
  - Keeps the cache command unchanged.
- Patch status: PROPOSED until applied and verified locally.
Current GitHub branch check on 2026-06-18:
- Branch: `codex/pno-anomaly-continuation-lab`.
- Head: `e1a4e19a14a6a735a650aaec50f0f41f65da57eb`.
- Combined status: no status checks returned.
- Uploaded snapshot `project_20260618_124949.zip` contains newer local Binance Vision cache startup changes than that GitHub head; this patch is generated against the uploaded snapshot, not directly against GitHub head.

New pending patch from uploaded snapshot `project_20260618_124949.zip`:
- `perf: replace Binance Vision root archive scan with scoped preflight`
  - Replaces the silent recursive S3 root scan with scoped per-symbol/month kline preflight.
  - Adds visible archive-index progress before symbol block processing starts.
  - Preserves current-month daily-only symbols via bounded daily HEAD probes only when monthly overlap is absent.
  - Keeps optional metrics/liquidation archive probing behavior unchanged.
  - Patch status: PROPOSED until applied and verified locally.
