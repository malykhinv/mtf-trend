# C09 - XX00 Asia 1m Launch Core 2026-04-04

Status: `Historical candidate / invalidated by honest online backtest`

Update on `2026-04-05`:

- the fully online watchlist backtest removed the post-hoc `5m` universe;
- under that cleaner setup, this candidate did **not** survive as a validated Asia edge;
- keep this note as historical research context only;
- current source of truth is [C11 - XX00 Online Watchlist Honest Backtest 2026-04-05](./C11_xx00_online_watchlist_honest_backtest_2026_04_05.md).

This note fixes the current best-known Asia XX:00 long candidate after the bias check, session-specific research, and exit-layer review.

## Entry Rule

- candidate_id: `C09_xx00_asia_1m_launch_core`
- session: `asia`
- rule_id: `long_launch_r010_c65_v04_p0_rr20`
- primary cohort: `asia__long_union`
- confirmation cohort: `asia__long_hot_range`
- decision timing: evaluate only after the `HH:00` minute closes
- execution timing: enter on the open of `HH:01`
- conditions:
  - minute-0 return >= `+1.0%`
  - minute-0 close position >= `0.65`
  - minute-0 volume ratio >= `4.0x`
  - `prev240` break is not required
- stop: below minute-0 low

## Best-Known Exit

The original entry scan used the `rr20` family, but dedicated exit research changed the best-known exit:

- preferred exit: `fixed_rr30`
- meaning: hold for a full `3R` target
- why: it beats the staged templates and the simpler `2R` exit on the strongest Asia XX:00 layer

## Why It Stays The Asia Core

- The minute-1 launch rule stayed rank `1` in the earlier Asia bias check and kept getting selected in walk-forward checks.
- The latest session-separated Asia read still keeps it as the best-known early long:
  - `asia__long_union`, entry-layer stats: `94` cases, `22` trades, `77.3%` win rate, `+4.22%` mean trade
  - `asia__long_union`, preferred exit `fixed_rr30`: `22` trades, `72.7%` win rate, `+5.28%` mean trade, about `+251.7%` annualized at `3%` risk, `3.05%` max drawdown
  - `asia__long_hot_range`, preferred exit `fixed_rr30`: `11` trades, `90.9%` win rate, `+7.42%` mean trade, about `+175.1%` annualized at `3%` risk, `0.70%` max drawdown
- It still matches the actual trading story we want: an already-hot coin prints a strong launch minute at `XX:00`, and we join only after that minute is closed.

## Limits

- This is still not a full live-ready bot rule by itself.
- The entry and exit simulation are no-lookahead, but the current research universe still starts from a post-hoc `5m` XX:00 event set.
- A real deployment still needs an online watchlist builder that knows only what was available before `HH:01`.
- Trade count is still below the portfolio target by itself, around `20-24` trades per year.

## Practical Use

- Keep this as the current Asia XX:00 long core.
- Use `Fixed 3R` as the default exit until a fully online scan proves otherwise.
- Do not enter inside the `HH:00` candle.
- Do not treat Europe or America transfer results as equally validated just because the same entry rule looks good there.
