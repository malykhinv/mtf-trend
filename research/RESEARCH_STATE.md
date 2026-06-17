# Research state

Current phase: anomaly_science rebirth.

Active rule:
- `legacy_quarantine` is reference-only.
- New code must not import legacy modules.
- First target is MVP 1: dataset/state/future-path/audit, not trading.
- Current implemented slice after applying Patch 8: data source boundary, data quality, point-in-time universe skeleton, broad anomaly events, online 1m anomaly state, raw future paths, descriptive anomaly nature atlas, and descriptive future-nature outcome labels.
- Future paths remain raw outcomes.
- Atlas outcome bins are descriptive discovery bins only, not decision rules, EV, PnL, or trade simulation.
- Outcome labels are descriptive scenario targets for later walk-forward prediction calibration, not trading labels.
- Prediction models, calibrated probabilities, placebo/control tests, decision timing, EV, trade simulation, shadow live, and production live are still intentionally absent.

Current commit: UNKNOWN until committed and verified with `git rev-parse HEAD`.
Last verified base snapshot for Patch 8 generation: Patch 7 applied to `project_20260617_104034.zip`; GitHub head not checked in this environment.
