# C12 - XX00 Online Ranking Honest Backtest 2026-04-05

Status: `Red flag / ranked selection did not rescue the XX:00 long edge`

This note extends `C11` by replacing pass/fail watchlists with cross-sectional ranking at decision time.

## What Changed

Starting from the honest online launch universe from `C11`, the new layer does this:

- keep only raw `1m`-derived `HH:00` opportunities with no post-hoc `5m` event universe;
- decide only after the `HH:00` minute closes;
- fill on the `HH:01` open;
- rank simultaneous launches within the same hour using only pre-hour and `m0` features known at entry time;
- search `top-1` and `top-2` per hour on `old` only;
- read `current` as the honest result.

The search grid stayed intentionally restrained:

- `8` launch rules;
- `6` online scopes;
- pairwise and 3-feature rank scores only;
- `33,600` old summary rows in total.

## Honest Result

Ranking did not recover a deployable long edge.

- combos with positive annualized return on both `old` and `current`: `0`
- best old-selected combo in every session used `scope_near_high_union`
- the repeated best score family was `pre_ema200 + pre_range`

### America

- best old-selected combo:
  - rule: `launch_r010_c80_v04_p0_rr30`
  - scope: `scope_near_high_union`
  - selection: `top-1`
  - score: `pre_ema200,pre_range`
- honest current read:
  - `138` trades
  - `32.6%` win rate
  - `-0.22%` mean trade
  - about `-26.5%` annualized at `3%` risk
  - about `42.6%` max drawdown at `5%` risk

### Asia

- best old-selected combo:
  - rule: `launch_r010_c80_v08_p1_rr30`
  - scope: `scope_near_high_union`
  - selection: `top-1`
  - score: `pre_ema200,pre_range`
- honest current read:
  - `86` trades
  - `22.1%` win rate
  - `-0.61%` mean trade
  - about `-37.7%` annualized at `3%` risk
  - about `48.2%` max drawdown at `5%` risk

### Europe

- best old-selected combo:
  - rule: `launch_r010_c80_v04_p0_rr30`
  - scope: `scope_near_high_union`
  - selection: `top-2`
  - score: `pre_ema200,pre_range`
- honest current read:
  - `146` trades
  - `23.3%` win rate
  - `-0.29%` mean trade
  - about `-38.2%` annualized at `3%` risk
  - about `48.3%` max drawdown at `5%` risk

## What Ranking Did Teach Us

On old competitive hours, the winner of the hour was more often associated with:

- America: `m0_vol`, `m0_close`, and `near_prev60`
- Asia: `pre_vol` and `near_prev60`
- Europe: `near_prev60`, `pre_ret60`, `pre_ema200`, and `pre_vol`

So the ranking idea is still descriptively useful, but the descriptive signal did not transfer into a profitable tradable rule.

## Practical Verdict

- Do not deploy `top-1` or `top-2` ranked XX:00 long selection.
- Do not treat `pre_ema200 + pre_range` as a validated online score.
- The honest XX:00 long problem remains unsolved.

## Remaining Caveats

- score families were still searched on `old`, so `current` remains the only honest read;
- slippage beyond next-open fills is still not modeled;
- ranking was done inside the honest launch universe, not yet on a broader all-symbol hourly scan with portfolio competition across different launch-rule families.
