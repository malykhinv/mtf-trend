# Validation And Bias Notes

This file records the main places where the current anomaly research can look better than a fully live strategy would.

## 1. No Obvious Arithmetic Bug Was Found In The New Europe/America Long Universe

The main technical issue that was found and fixed was not a positive skew in trade execution itself. It was a missing-feature bug:

- `body_atr`
- `pre_base_range_vs_trigger`

Without these features, all Europe and America long trade models were incorrectly filtered out.

After fixing that, the Europe/America long universe produced real triggered trades and could be analyzed.

## 2. The Biggest Current Source Of Positive Bias

The strongest current source of optimistic bias is methodological:

- category labels use post-anomaly information

Examples:

- next-bar confirmation quality
- 15m buyer rematch
- 30m buyer or seller rematch

These are valid for research and classification, but they are not automatically valid as live decision-time filters.

## 3. The Second Biggest Source Of Positive Bias

Portfolio selection on combined old plus current data is not untouched OOS.

If we:

- define categories on both periods together
- score category plus model pairs on both periods together
- build the final portfolio on both periods together

then the final result is still in-sample for portfolio selection, even if it spans more than one market regime.

## 4. Historical Universe Caveat

The older cache is still not a perfect full historical Binance futures universe.

That means:

- old-period validation is much better than before
- but still not a perfect exchange-wide historical replay

## 5. What Counts As A Fairer Test

For category research:

- freeze category plus model pairs on `old`
- test on untouched `current`

and also:

- freeze on `current`
- test on untouched `old`

For live strategy research:

- only use inputs known at the moment of entry
- avoid any category feature that requires future bars

## 6. Current Practical Interpretation

Use the natural categories as:

- a map of where edge tends to live
- a way to compare broad classes of pumps

Do not yet treat them as:

- a final live entry engine
- proof that the exact same edge is fully tradable in real time

## 7. Current Goal

The goal is to close the gap between:

- descriptive edge: good categories and good trade families
- tradable edge: online entry, stop and exit rules that only use known information

The next fair step is:

- untouched `train -> test` validation for category plus model portfolios
- then online-only execution validation on the surviving candidates
