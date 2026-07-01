# Pump-fade same-symbol perp-crowding probability protocol

Status: frozen before full archive acquisition and outcome evaluation.

The hypothesis is that the pumped contract's own premium-index trajectory and
already published funding history contain crowding/temporary dislocation
information beyond price, activity, OI, positioning ratios, and CVD already in
the baseline. These observables do not identify trader intent or liquidation.

The family `pump_fade_perp_crowding_v1` contains exactly the 16 coordinates in
`research/pump_fade_perp_crowding_probability.json`: current premium; fixed
5m/15m/60m premium differences; 15m/60m means; 60m standard deviation and max;
240m z-score with at least 120 closed observations; ignition-relative premium
change; current and previous funding-derived change; three-observation funding
mean; minutes since the last available funding record; premium-minus-funding;
and the joint positive-premium/positive-funding flag.

Premium klines become available only after their close. Funding records receive
a conservative +1m publication lag. Every join is same-symbol backward as-of;
premium maximum age is 2m and funding maximum age is 12h. Missing history stays
NaN and `perp_crowding_complete=false`.

Evaluation is paired complete-case weekly walk-forward at T0 and new-high
ordinals 1-2, with recurrence-chain isolation and identical rows/freezes in both
arms. Family-wise alpha is 0.05/3. Incremental minima are AUC +0.01, log-loss
improvement +0.002, and Brier improvement +0.001, together with all absolute
probability and `p>=0.70` reliability gates. No post-hoc window, subset, symbol,
or state search is allowed on this development OOS period.
