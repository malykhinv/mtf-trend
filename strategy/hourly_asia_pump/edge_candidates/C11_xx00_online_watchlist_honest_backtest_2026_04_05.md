# C11 - XX00 Online Watchlist Honest Backtest 2026-04-05

Status: `Red flag / no validated XX:00 long edge yet`

This note supersedes the earlier post-hoc `5m`-universe confidence around the XX:00 launch work.

## What Changed

The new backtest starts from raw `1m` data only:

- every symbol contributes hourly `HH:00` opportunities directly from `1m`;
- watchlists are built from pre-hour features only;
- thresholds are frozen on `old` hourly data only;
- the launch decision is made only after the `HH:00` minute closes;
- fills happen on the `HH:01` open;
- exit is `Fixed 3R` with conservative stop-first same-bar handling.

This removes the main earlier leak: pre-selecting events from a post-hoc `5m` XX:00 universe.

## Honest Result

No session produced a validated long edge under this cleaner setup.

### Asia

- earlier core idea did not survive the honest online scan;
- best old-selected combo:
  - watchlist: `wl_pressure_hot`
  - rule: `launch_r015_c80_v08_p0_rr30`
- honest current read:
  - `121` trades
  - `27.3%` win rate
  - `-0.44%` mean trade
  - about `-18.8%` annualized at `3%` risk
  - about `43.8%` max drawdown at `5%` risk

### Europe

- there are some current-positive online combos, but they fail old-to-current transfer;
- best old-selected combo:
  - watchlist: `wl_near_high_union`
  - rule: `launch_r010_c80_v04_p0_rr30`
- honest current read:
  - `152` trades
  - `25.7%` win rate
  - `-0.14%` mean trade
  - about `-32.7%` annualized at `3%` risk
  - about `43.3%` max drawdown at `5%` risk

### America

- the only session with clearly positive old-selected return did not hold on current;
- best old-selected combo:
  - watchlist: `wl_near_high_union`
  - rule: `launch_r010_c80_v04_p0_rr30`
- honest current read:
  - `156` trades
  - `32.1%` win rate
  - `-0.22%` mean trade
  - about `-30.8%` annualized at `3%` risk
  - about `45.5%` max drawdown at `5%` risk

## Practical Verdict

- Do not treat `C09` as a validated Asia core anymore.
- Do not deploy the Europe transfer either.
- There is still research value here, but not a production-ready XX:00 long mechanism.

## What This Probably Means

The earlier edge was materially helped by the post-hoc `5m` event universe.

The fully online problem is harder:

- the watchlist still admits too many mediocre `HH:00` launches;
- the real continuation cases may need stronger pre-hour ranking, not just pass/fail filtering;
- session-specific ranking or top-1-per-hour selection may matter more than the current broad watchlist logic.

## Remaining Caveats

- watchlist families are still research-driven, not a pristine untouched idea;
- slippage beyond next-open fills is still not modeled;
- `old` uses a smaller symbol universe than `current`, so transfer remains conservative but not perfectly apples-to-apples.
