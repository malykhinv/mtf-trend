# PRL-PEER — correlation baskets / peer-residual signal (protocol + result)

**Code:** `prl/research/peer.py` · **Spec:** §8, §9, H5. **Status:** IS. OOS reserved. Negative.

---

## What was built (the planned §8 idea)

PIT empirical correlation baskets: trailing-120d daily residual-return correlations -> distance
`sqrt(0.5*(1-rho))` -> agglomerative average-linkage clustering (K fixed, unsupervised, monthly
refit, membership frozen between refits) -> leave-one-out peer factor -> peer-residual = eps -
peer_LOO (relative strength WITHIN the correlation basket) -> peer-residual momentum. H5: does it
add incremental rank-IC over market-only residual momentum?

## Result (vs future market-residual label)

```text
                     IC        by-year
market-only (base)  -0.0008    2023 -0.035  2024 -0.017  2025 +0.042
peer-residual       -0.0077    2023 -0.054  2024 -0.024  2025 +0.034
base+peer combo     -0.0031
```

Robust across K = 8 / 12 / 20 (peer-residual IC ≈ −0.008 every time, clusters: median size 3.5-10,
singleton rate 1-14%). **Peer-residualization does NOT add value — it is slightly worse than the
already-null market-residual momentum.**

## Why

Momentum — market-residual OR peer-residual — is null in this data (PRL-COARSE-000). Residualizing a
null signal against its correlation basket cannot manufacture predictability; it only adds noise.
The peer machinery is sound and PIT-clean, but H5 (peer info adds value) is not supported for the
momentum signal. Correlation baskets remain useful for cluster-neutral construction / effective-bet
counting (§45-§46), not as a new alpha source here.
