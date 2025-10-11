# Binance Streaming Architecture

## Rate limiting profiles

The streaming layer uses [`config/stream_limits.py`](../config/stream_limits.py) to describe
command budgets for the three Binance stream types. Each profile defines:

* `max_streams_per_connection` – the maximum number of symbols that share a single
  websocket connection.
* `steady` – a long living window (`CommandWindow`) with a sustainable command budget.
* `burst` – a shorter window that allows quick recovery bursts.
* `minimum_command_reserve` – the amount of capacity that should remain unused so that
  emergency commands (e.g. resubscribe after disconnects) can be issued without waiting
  for the steady window to replenish.

Default values are tuned as follows:

| Stream | Max streams | Steady window | Burst window | Reserve |
| ------ | ----------- | ------------- | ------------ | ------- |
| Depth  | 180         | 5 commands / 1.0s | 2 commands / 0.25s | 1 |
| Trades | 220         | 5 commands / 1.0s | 3 commands / 0.5s  | 1 |
| Book   | 240         | 5 commands / 1.0s | 3 commands / 0.5s  | 1 |

Operations may override these defaults by editing `DEFAULT_BINANCE_STREAM_PROFILES` or by
passing a custom mapping when instantiating `BinanceStreamManager`. Adjust `steady` and
`burst` to match exchange changes; increase `minimum_command_reserve` if manual command
bursts are expected during maintenance windows.

## Worker planning

`BinanceStreamManager` precomputes the number of websocket workers per stream type at
startup using the configured capacities. By default we provision 1.5× the stream
capacity of each profile to create at least two warm connections per stream type. This
provides spare headroom for symbol churn without incurring connection setup latency.

When a registration request would push the weighted load above the current capacity, the
manager automatically expands the worker pool before subscribing the symbol. Operations
can fine tune the preallocation by providing `expected_stream_weights` when constructing
`BinanceStreamManager`.

## Symbol weighting

Symbols are assigned to workers based on liquidity/priority hints sourced from
`TradingProfile`. The mapping lives in `BinanceExchangeData` and can be adjusted to
reflect the actual portfolio mix:

* TOP profile symbols reserve the most capacity (depth weight 3.0, trades 2.5, book 2.0).
* LISTING symbols emphasise trade streams (depth 2.5, trades 3.0).
* ALT profile keeps a near-uniform load (1.0 across streams).
* AUTO acts as the fallback (depth/trades 1.5, book 1.0).

Weights feed into a per-stream total; once the weighted sum reaches the available
capacity the manager prepares an additional websocket before issuing subscribe commands.
This guarantees that high-priority symbols do not contend with low-liquidity ones.

## Operational guidance

1. Update `config/stream_limits.py` whenever Binance changes websocket or command limits.
   Reflect the new values in both steady and burst windows, and review the reserve to
   maintain enough emergency capacity.
2. Revisit the per-profile weights in `BinanceExchangeData` after onboarding new market
   segments to keep worker utilisation balanced.
3. If capacity alerts are raised, adjust `expected_stream_weights` passed to
   `BinanceStreamManager` (or override `DEFAULT_BINANCE_STREAM_PROFILES`) so that more
   workers are precreated during service bootstrap.
4. All changes should be validated in staging by monitoring reconnect storms and
   ensuring command throttling logs stay within the expected windows.
5. Pipelines expose backlog metrics and chronic error hooks via
   `StreamPipeline`. Depth, trades, and book-ticker feeds now have per-profile buffer
   sizing, thresholds, and fallback behaviour defined under `streams/`. Review the
   thresholds when updating `TradingProfile` allocations to ensure the SLA balance
   between TOP and thinly traded symbols remains appropriate.
6. The stream manager reacts to chronic error callbacks by migrating the impacted
   symbol to a reserve socket without disrupting other workers. For incidents focused on
   a single market, prefer tuning the pipeline thresholds before widening global
   restart timers.
7. Downstream strategies consume degradation signals surfaced by the runtime. Expect
   automated position throttling (reduced leverage or temporary suspensions) whenever a
   stream enters a degraded state; coordinate with risk to adjust policy parameters.
