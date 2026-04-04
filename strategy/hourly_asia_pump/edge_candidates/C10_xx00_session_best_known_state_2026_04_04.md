# C10 - XX00 Session Best Known State 2026-04-04

Status: `Core + exploratory + red flags`

Update on `2026-04-05`:

- this note is now historical;
- the fully online watchlist backtest has become the new source of truth;
- see [C11 - XX00 Online Watchlist Honest Backtest 2026-04-05](./C11_xx00_online_watchlist_honest_backtest_2026_04_05.md).

This note freezes the current best-known state of the XX:00 work after the separate Asia, Europe, and America research passes.

## Best-Known Session Map

### Asia long

- status: `best known core`
- rule: `long_launch_r010_c65_v04_p0_rr20`
- entry: decide after the `HH:00` minute closes, enter on `HH:01` open
- stop: below minute-0 low
- preferred exit: `fixed_rr30`
- current read:
  - `22` trades on `asia__long_union`
  - `72.7%` win rate
  - `+5.28%` mean trade
  - about `+251.7%` annualized at `3%` risk
  - `3.05%` max drawdown
- role: keep as the current Asia XX:00 core

### Europe long

- status: `exploratory best-ready overlay`
- rule: `long_launch_r010_c65_v04_p0_rr20`
- entry: same minute-1 launch logic as Asia
- preferred exit: `fixed_rr30`
- smoother alternate exit: `fixed_rr20`
- current read on `europe__long_union`:
  - `53` trades
  - `77.4%` win rate with `fixed_rr30`
  - `+3.78%` mean trade
  - about `+168.5%` annualized at `3%` risk
  - `5.47%` max drawdown
- why not fixed as core yet:
  - it still comes from a research universe built on post-hoc `5m` XX:00 events
  - it has not passed a clean untouched-holdout deployment check
  - earlier session-gate review still marked it as a near-miss rather than a final production edge

### America long

- status: `no committed XX:00 long edge yet`
- best current rule: `long_launch_r010_c80_v04_p1_rr15`
- best current exit for max return: `fixed_rr30`
- current read on `america__long_union`:
  - `17` trades
  - `58.8%` win rate
  - `+3.05%` mean trade
  - about `+71.2%` annualized at `3%` risk
  - `7.69%` max drawdown
- verdict: interesting, but below the bar for a committed long layer

## What We Should Not Treat As Edge

- Do not auto-short a dead `XX:00` pump just because it failed to continue.
- `warm + below EMA200` helps identify weak continuation, but that has not turned into a reliable XX:00 short edge by itself.
- America and Europe transfer scans from the earlier broad `all_loose_xx00` universe were useful for discovery, not for direct deployment claims.

## Live Execution Contract

The real bot should behave like this:

1. Keep rolling features for all symbols during the active session: trend, range, drift, volume medians, and other pre-launch context.
2. Before each full hour, build a watchlist of symbols that already look heated enough for a real launch.
3. Observe the `HH:00` minute while it forms, but do not decide yet.
4. At `HH:01:00`, evaluate the now-closed `HH:00` minute.
5. If the rule passes, enter on the open of `HH:01`.
6. If a follow-up playbook is enabled later, continue watching `HH:01` to `HH:04`.

This is the key anti-lookahead rule: the bot never enters inside the `HH:00` candle based on information that exists only after that candle closes.

## Honest Limitation

The entry and exit simulation already respect decision-time ordering, but the current research still starts from a post-hoc `5m` XX:00 event universe.

So the next technical milestone is clear:
- build the online watchlist builder
- re-run Asia, Europe, and America on that fully online universe
- only then call the result live-ready
