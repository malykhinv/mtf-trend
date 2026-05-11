# Anomaly Patch Log

Compact active patch log for the anomaly-first source tree. Retired strategy history was removed from active research memory in P129 to avoid stale contracts controlling current work.

| ID | Title | Status | Files | Type | Purpose | Validation |
| --- | --- | --- | --- | --- | --- | --- |
| P123 | Decouple anomaly runtime helpers | PROPOSED | `research_tools/anomaly_config.py`, `research_tools/charting.py`, anomaly live/backtest modules | cleanup | Move timeframe validation and chart helpers into anomaly-owned/common modules. | `python -m compileall research_tools/anomaly_config.py research_tools/charting.py research_tools/anomaly_micro_live.py research_tools/anomaly_strategy_backtest.py` |
| P124 | Isolate retired strategy CLI imports | PROPOSED | `cli/commands.py`, `cli/parser.py`, `config/*`, `strategy/factory.py` | cleanup | Stop startup/config/parser paths from importing retired strategy modules. | `python -m compileall cli/commands.py cli/parser.py config strategy main.py` |
| P125 | Prune unused launcher and dead constants | PROPOSED | `launcher.py`, `constants.py`, docs | cleanup | Remove unused wrapper and dead sizing constants. | `python -m compileall constants.py cli config research_tools main.py` |
| P126 | Remove stale historical pytest marker | PROPOSED | `pyproject.toml`, `research/*` | cleanup | Remove a test marker for a test suite absent from the ZIP source. | `git grep historical_marker_name` equivalent returns empty. |
| P127 | Neutralize old sizing names | PROPOSED | `constants.py`, `config/*`, runtime modules | cleanup | Rename old sizing defaults to neutral position defaults. | `git grep old_sizing_prefix` equivalent returns empty. |
| P128 | Remove retired strategy active path | PROPOSED | `strategy/`, `cli/`, `config/`, `README.md`, `research/*` | deletion | Remove retired strategy source, diagnostics, CLI, config and factory path. | `python -m compileall cli config constants.py research_tools vectorbt_runner strategy main.py` |
| P129 | Purge retired strategy history from active memory | PROPOSED | `README.md`, `research/*.md` | cleanup | Remove stale historical strategy references from current project docs/logs. | `git grep -i retired_strategy_token -- .` returns empty for tracked files. |
| P130 | Strict live fills and executable market entry | PROPOSED | `data/exchanges/*`, `research_tools/anomaly_micro_live.py`, `research_tools/anomaly_strategy_backtest.py`, `cli/*`, `research/*` | bugfix | Stop executing stale live signals; require exchange fill fields; separate signal price from actual fill; compute live PnL/BE/TP from actual fill; make market backtest enter on execution candle. | `python -m compileall data/exchanges research_tools cli constants.py main.py` |
| P131 | Live blocked-order Telegram alerts | PROPOSED | `research_tools/anomaly_micro_live.py`, `research_tools/anomaly_strategy_backtest.py`, `research/*` | bugfix | Send Telegram event alerts when a selected live signal is blocked as stale/non-executable; make entry-price drift guard absolute in live and market backtest. | `python -m compileall data/exchanges research_tools cli constants.py main.py`; synthetic stale/drift smoke. |

## P129 — Purge retired strategy history from active memory

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

After removing the retired source path, the active research memory still contained extensive historical entries for the previous strategy. Those entries were no longer executable and could keep steering new work toward stale assumptions.

### Change

- Rewrite `README.md` as anomaly-only project documentation.
- Rewrite `research/RESEARCH_STATE.md` around current anomaly runtime paths and risks.
- Rewrite `research/STRATEGY_SPEC.md` as the current anomaly strategy contract.
- Reset `research/PATCH_LOG.md` to a compact active patch log for the current source tree.
- Reset `research/EXPERIMENT_LOG.md` to anomaly-only experiment tracking.

### Validation

```bash
git grep -n -i -E 'retired_strategy_token|old_strategy_token|old_sizing_token' -- .
python -m compileall cli config constants.py research_tools vectorbt_runner strategy main.py
```

### Risk

Medium for project memory: detailed historical notes are intentionally removed from the active repo. Source behavior is unchanged.

## P130 — Strict live fills and executable market entry

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

NVDA live audit showed a signal price/time and execution price/time mismatch: a stale backfilled signal could be selected several minutes after its decision candle, while ledger, PnL and chart still used the signal close as entry. That makes live PnL and edge evidence invalid.

### Change

- Add typed exchange fill boundary: market entry must resolve order id, timestamp, average price and filled amount through exchange order/trade payloads.
- Reject live entries when signal is stale, live price drift is too high, TP1 is already reached, RR to signal TP1 collapsed, or exchange already has a position for the symbol.
- Ledger and chart now separate `signal_entry_price` from actual `entry_price`; TP1/BE/PnL are based on actual fill.
- Stop replacement verifies the new stop is open and does not silently ignore old-stop cancellation state.
- Market backtest no longer enters at decision close; it enters on the configured execution candle and rejects drift/RR-collapsed cases.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
```

### Risk

Medium: live will reject more signals and backtest results for `entry_method=market` will change. This is intended; old behavior was optimistic and non-executable.

## P131 — Live blocked-order Telegram alerts

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

P130 blocks stale and non-executable selected live signals, but the operator would only see those decisions in `live_events.csv` unless watching artifacts. Second review also found the drift guard was one-sided: it rejected only positive drift, while a selected signal could still be bought after price moved too far below the signal entry.

### Change

- Send Telegram event-channel alerts for `reject_stale_signal` and live executability rejects: invalid live price, TP1 already reached, invalid/wide live risk, entry price drift, and RR collapsed.
- Keep artifact events as source of truth; Telegram is only an operator notification queued through the existing dispatcher.
- Change live and market-backtest entry-price drift from one-sided positive drift to absolute drift around the signal entry.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# synthetic smoke: stale signal and absolute drift reject enqueue Telegram event and create no market order
```

### Risk

Low/medium: fewer fresh-but-dislocated live entries pass, and Telegram event channel can receive more messages. Existing Telegram cooldown key is per symbol/event.

## P132 — Live position safety audit artifacts

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

P130/P131 fixed stale/non-executable entry selection, but live position management could still hide operational problems: monitor integrity errors were handled like temporary network failures, TP1 partial exits used unverified fills, initial stops were accepted by id only, repeated empty monitor OHLCV had no hard failure, and market-backtest skip reasons were not exported as first-class artifacts.

### Change

- Verify initial and replacement stop orders through open-order state: id, side, stop type, reduce-only flag, amount and stop price.
- If initial stop verification fails after entry fill, attempt a verified reduce-only emergency exit and write the exact outcome to `live_events.csv`.
- Execute TP1 partial close through verified fill resolution and record actual TP1 fill price/amount/PnL in `live_events.csv`.
- Treat `LiveDataIntegrityError` in position monitor as `position_integrity_error` with Telegram alert and no generic retry masking.
- Record repeated empty monitor OHLCV as `position_monitor_empty_ohlcv`; after `max_monitor_empty_ohlcv_cycles`, escalate to integrity error.
- Export `anomaly_skip_reasons.csv`, include skip reasons in profitability summary, expose execution-guard skips in entry-grid summary, and mark market backtest execution as `next_bar_open_proxy_latency_N`.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
```

### Risk

Medium: live may halt position monitoring when exchange stop/order state cannot be verified. This is intentional; unknown protection state must be visible and handled manually instead of being treated as a transient loop error.

## P133 — Human Telegram live messages

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

Live Telegram messages were technically correct but too verbose and used raw Coinglass URLs. Operator messages should be short, human-readable and use clickable symbols while `live_events.csv` remains the full audit source.

### Change

- Replace startup/error/network blocked-entry/integrity/open/close/stop Telegram text with compact human-style templates.
- Render symbols as Telegram HTML links to Coinglass instead of printing a separate raw URL.
- Keep detailed blocked-entry and integrity metadata in artifacts; Telegram only shows the concise reason and context.
- Display close location as `SL`, `BE`, `TP-` or `TP+`.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
```

### Risk

Low: Telegram wording changes only. Audit detail is intentionally preserved in `live_events.csv`, not repeated in every operator message.

## P134 — Active-symbol live scheduler

Status: PROPOSED
Date: 2026-05-11
Commit: UNKNOWN

### Reason

The live loop treated only open positions as active. When at least one position was open it used a separate hard-coded inactive batch size, so a symbol that had just passed an important pump/signal stage could wait behind the normal round-robin instead of being rescanned on the next few cycles.

### Change

- Replace the old `inactive_batch_with_active = 7` path with one scheduler rule: `active_symbols + (symbol_batch_size - active_count)` inactive symbols.
- Add an explicit active-symbol watchlist for recent pump-flow candidates, selected entry signals, symbols waiting for a free position slot, and currently opening symbols.
- Keep open positions always active; if active symbols exceed the configured batch size, all active symbols are scanned and inactive slots become zero.
- Export scheduler state into `live_events.csv`: `active_symbol_marked`, `active_symbol_cleared`, `active_symbol_expired`, `symbol_batch_selected`, and `signal_decision_consumed`.
- Do not consume a selected signal when it is blocked only by `max_open_positions`; it remains active until the signal expires or a free slot appears.
- Add CLI knobs `--symbol-batch-size` and `--active-symbol-ttl-ms`.

### Validation

```bash
python -m compileall data/exchanges research_tools cli constants.py main.py
# synthetic scheduler smoke: two active symbols with symbol_batch_size=5 => two active + three inactive
```

### Risk

Low/medium: a burst of many active symbols can make a cycle longer because active symbols are not dropped to preserve a fixed batch cap. This is intentional; symbols with near-term action priority must not be starved by inactive round-robin scanning.
