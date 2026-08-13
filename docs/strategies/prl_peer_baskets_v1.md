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

## Doing it "by the book" — the real scientific uses of baskets (peer_rv.py + cluster-neutral)

The momentum residualization was the wrong use (momentum is null). The genuine uses of correlation
baskets were then tested:

**1. Relative-value / convergence (stat-arb)** — recent within-basket divergence -> future
peer-residual. IC is tiny and sign-UNSTABLE across years (lb5/fwd5 −0.007 but 2023/24 converge,
2025 diverges; lb10 flips to +0.011). The cluster-neutral convergence book (short over-diverged /
long under-diverged within each basket) LOSES in every config (CAGR −10..−18%, Sharpe −0.5..−1.0).

**2. Cluster-neutral construction of the WORKING signal** (rank the OOF score within each basket):
```text
plain rank-weight   CAGR +22%  Sharpe 1.56  +weeks 58%
CLUSTER-NEUTRAL     CAGR  +9%  Sharpe 1.01  +weeks 55%   <- worse
```
Forcing within-basket neutrality HURTS -- the working signal's edge is partly CROSS-basket (which
clusters are strong), and neutralizing it throws that information away.

## Verdict

Correlation baskets add nothing here in ANY standard use: not peer-residual momentum, not
relative-value convergence, not cluster-neutral construction. On daily crypto the baskets are noisy
and unstable (everything co-moves in risk-on/off, the §8.7.1 concern), and the real edge is
genuinely cross-sectional ACROSS the whole market, not within baskets. Basket line closed.
