# Top-Liquid Regime Directional Basket — Protocol v1

**Frozen:** 2026-08-05, before computing any strategy result.

## Hypothesis

When broad crypto breadth, BTC, and ETH agree directionally across several
completed horizons and sessions, a basket of point-in-time large/liquid coins
continues in that direction long enough to be captured with a wide causal
structural trail. Individual large coins may have different conditional beta,
so eligibility is estimated and reported per symbol before basket construction.

This is a separate strategy. It does not reuse or modify cross-sectional
quality ranks.

## Universe contract

Two universes must never be conflated:

1. `large_liquid_proxy` — available locally and admissible for the first audit:
   top `(10, 20, 30)` symbols by trailing 30-day median Binance futures quote
   volume, minimum age 180 days, computed point-in-time. Stablecoins and
   synthetic duplicates are excluded. BTC and ETH define context and are
   reported separately from the alt basket.
2. `historical_market_cap` — a later replication requiring point-in-time market
   capitalisation snapshots. A present-day top-cap list is forbidden because
   it introduces survivor bias.

No conclusion from the liquidity proxy may be described as a market-cap result.

## Causal snapshots

Signals are evaluated on completed 1h bars. Entry is the next hourly open.

Directional components:

- BTC return over completed 6h, 12h, and 24h;
- ETH return over completed 6h, 12h, and 24h;
- median eligible-universe return over the same horizons;
- return of the completed current session-to-date, when at least three hours
  have completed;
- return of the immediately previous complete UTC session;
- share of eligible symbols below/above causal EMA `(24h, 72h, 168h)`;
- share of eligible symbols with negative/positive `(6h, 12h, 24h)` returns.

### Path geometry: cleanliness and verticality

For BTC, ETH, the eligible-market median, and every candidate coin, compute on
completed `(6h, 12h, 24h)` paths:

- `path_efficiency = abs(net_log_return) / sum(abs(hourly_log_returns))`;
- `directional_purity` = share of hourly returns agreeing with the proposed
  long/short direction;
- `verticality` = directional net log return divided by prior 30-day hourly
  volatility scaled by square-root time;
- `aligned_candle_share` = share of hourly candle bodies agreeing with direction;
- `counter_move_share = 1 - directional_purity`;
- `terminal_acceleration` = directional last-3h return divided by the absolute
  full-path return;
- `close_location` inside the completed bar and adverse-wick share;
- persistence of market breadth across the path, not only its terminal value.

Market geometry is the median of BTC, ETH, and eligible-market geometry. The
minimum component is also retained so one vertical asset cannot hide a weak
market.

Registered descriptive states, reported rather than assumed profitable:

```yaml
clean_trend:
  path_efficiency: [0.55, 0.70, 0.85]
  directional_purity: [0.60, 0.70, 0.80]
smooth_trend:
  minimum_verticality: 0.75
  maximum_verticality: [1.5, 2.0, 2.5]
vertical_shock:
  minimum_verticality: [1.5, 2.0, 2.5, 3.0]
possible_exhaustion:
  minimum_verticality: 3.0
  minimum_terminal_acceleration: [0.50, 0.65, 0.80]
```

Verticality is never automatically a continuation or reversal signal. Its
interaction with cleanliness, volume/activity, session timing, breadth change,
and coin beta is an explicit prediction question.

Sessions use the existing versioned Core calendar without strategy-local
redefinition: Asia `00-08`, EU `08-13`, overlap `13-16`, US `16-21`, and late
`21-24` UTC.

## Registered direction rules

Every condition has a mirrored long and short form.

1. `strict_all`
   - BTC, ETH, and median market return agree on 6h, 12h, and 24h;
   - at least 60% of eligible symbols agree on all three horizons;
   - at least 60% are on the corresponding side of EMA72.

2. `score_4_of_5`
   - directional votes: BTC, ETH, median market, horizon breadth, EMA breadth;
   - at least four of five votes agree.

3. `score_3_of_5`
   - at least three of five votes agree; diagnostic coverage neighbour.

4. `session_confirmed`
   - `score_4_of_5`;
   - current session and previous complete session agree.

5. `transition`
   - `score_4_of_5` became true in the last three completed hours and was false
   before that, preventing repeated late entries into the same regime.

6. `clean_transition`
   - `transition`;
   - 12h market path efficiency and directional purity are both at least 0.70.

7. `smooth_transition`
   - `clean_transition`;
   - 12h verticality lies from 0.75 through 2.0.

8. `vertical_transition`
   - `transition`;
   - 12h verticality is at least 2.0 and efficiency at least 0.55.

9. `shock_transition`
   - `transition`;
   - 12h verticality at least 2.5 and purity at least 0.70.

10. `exhaustion_filtered_transition`
    - `transition`;
    - excludes snapshots with verticality at least 3.0 and terminal acceleration
      at least 0.65. This is a registered neighbour, not an assumed best rule.

Threshold neighbourhoods are breadth `(55%, 60%, 65%, 70%)` and EMA length
`(24h, 72h, 168h)`. No single threshold may be selected without neighbouring
support.

Repeated hourly truth values are one regime episode, not independent events.
For each rule/direction the audit retains the first signal, then requires either
six consecutive false hours or a mirrored-direction signal before a new event
is eligible.

## Coin-level audit before trading

For every symbol, direction rule, entry session, year, and horizon
`(6h, 12h, 24h, 48h, 72h, 120h)` report:

- event count and coverage;
- mean/median forward return in sign-adjusted direction;
- win share;
- MFE, MAE, and giveback from MFE;
- bootstrap confidence interval by whole event day;
- beta and residual return versus BTC and the equal-weight market;
- liquidity, spread proxy, and listing-age strata.

Required robustness:

- each accepted coin is positive in 2023, 2024, and 2025;
- leave-one-coin-out basket remains positive for every included coin;
- no coin supplies more than 20% of basket profit;
- no present-day symbol membership filter is allowed.

Coins may be classified as:

- symmetric trend follower;
- long-only responder;
- short-only responder;
- contrarian responder;
- regime-dependent/unstable;
- insufficient support.

## Position management

Entry variants:

- full basket at next-hour open;
- 50% starter plus 50% after a causal continuation swing;
- decreasing tranches `50/30/20` on structural continuation/retest.

Exit variants:

1. wide confirmed swing trail with neighbour scales `(12, 24, 48)` hours;
2. partial close `(25%, 50%, 75%)` on regime disagreement, remainder on swing;
3. full exit when the mirrored directional score reaches four of five;
4. maximum holding neighbourhood `(24h, 48h, 72h, 120h)`;
5. no fixed-percent or ATR-multiple physical TP/SL.

ATR may determine whether a swing is prominent enough to be visible, but every
physical exit price remains the exact confirmed market swing.

## Portfolio variants

- equal weight;
- inverse-volatility weight with per-coin cap;
- beta-balanced versus BTC;
- top `(5, 10, 20)` eligible responders by frozen prior evidence;
- BTC/ETH included versus context-only;
- long and short results always reported separately.

## Controls and gates

Controls:

- BTC-only and ETH-only;
- equal-weight universe buy-and-hold over identical event windows;
- direction sign shuffled within month;
- entry delayed by 6h;
- session labels circularly shifted;
- leave-one-year-out threshold stability.

An executable candidate requires:

- positive result separately in all three IS years;
- at least two adjacent breadth/EMA/trail cells positive;
- bootstrap Q05 Sharpe above zero;
- maximum drawdown at most 15%;
- worst day no worse than -8%;
- positive result after removing the best 1% of trades and best coin;
- no more than 25% of profit from the best month;
- beats BTC-only, ETH-only, and shuffled-direction controls;
- exact hourly costs, funding, gap fills, and structural confirmation timing.

Only after the protocol passes on the large-liquid proxy may historical
market-cap data be added as a separately versioned replication.

## Post-v1 registered geometry plateau audit

The first v1 descriptive read showed that `verticality >= 2` is not a valid
monotone continuation assumption and that joint efficiency/purity >= 0.70 is
too sparse. Before inspecting any new combinations, the following v2
neighbourhood is registered on the unchanged `transition` event set and
unchanged future paths:

- verticality bands: `[0.75, 1.50)`, `[0.75, 2.00)`, `[1.00, 2.00)`,
  `[1.25, 2.00)`;
- minimum directional purity: `0.65`, `0.70`, `0.75`;
- minimum path efficiency: `0.40`, `0.55`, `0.70`;
- every verticality band crossed with every purity minimum;
- baseline `transition` retained as the control.

The audit is diagnostic and contains no physical exit levels. A geometry
candidate is interesting only if the same sign appears in all three years,
after dropping the best 1% of event returns, and across at least two adjacent
verticality/purity cells with adequate event support. Long and short are never
pooled.
