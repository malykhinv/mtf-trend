# Legacy quarantine

This directory contains the pre-rebirth repository snapshot.

Rules:
- New `anomaly_science` code must not import from `legacy_quarantine`.
- Legacy code is reference-only.
- Useful ideas may be reimplemented from scratch under `src/anomaly_science`.
- Do not fix trading logic here.
- Do not build the new methodology on top of this code.
- This directory is temporary and should be deleted after the new system replaces it.

Operational note:
- Runtime trash such as `.env`, logs, caches, `.zip` files and `__pycache__` should stay untracked.
