# Post Pump Absorption

`post_pump_absorption` is the new research strategy focused on frequent entries after a pump.

Core thesis:

- detect a fresh upward impulse;
- wait for a post-pump range to form;
- watch the lower part of that range;
- trigger long only when buy-side aggression appears and price responds;
- use local stops behind the broken local structure or micro-base, not behind the whole range;
- use `range_mid` and `range_high` as the default profit targets.

Two entry families are implemented:

- `LSB` (`local structure break`): buy aggression near the lower range zone plus a break of the recent local bearish structure.
- `MBB` (`micro base breakout`): buy aggression near the lower range zone plus a breakout of a tight micro-base formed at the bottom.

## Logical Stages

The strategy now exposes an explicit logical stage path instead of one flat block of checks:

1. `stage_1_pump`
2. `stage_2_range`
3. `stage_3_lower_zone`
4. `stage_4_aggression`
5. `stage_5_setup`
6. `stage_6_trade`

This stage order is stored in diagnostics, and generated trades export the full `stage_path` in metadata.

Implementation details of the current version:

- the detected pump is refined to the local peak before the post-pump range scan starts;
- the range is locked on candles strictly before the trigger candle, so breakout candles do not move `range_high` or the targets at decision time;
- `LSB` is swing-based: it breaks the latest confirmed lower high and places the stop behind the local structural low, not behind the whole lookback slice;
- repeated entries inside the same post-pump regime are allowed after the previous trade is closed;
- usable taker-flow data is effectively required; if the frame has no usable buy-side aggression data, the strategy emits `missing_taker_data` diagnostics instead of failing silently;
- each generated trade carries strategy metadata (`setup_type`, range position, aggression strength, stop width, `MFE/MAE`, target hits) for later filtering and diagnostics.

Important first-version constraints:

- `OI` is optional and is not required for the signal path.
- `OI` is implemented as an optional enhancer, not as a mandatory gate.
- when `entry_tf` is `1m` or `3m`, the strategy may use `levels_tf=5m` only as an auxiliary `OI` source.
- when `entry_tf` is `5m`, the same `5m` frame can supply `OI`.
- supportive `5m OI` can slightly relax the aggression thresholds; absent `OI` must not disable the signal path.
- sweep/reclaim is optional and is not required for entry.
- the strategy currently runs only on `1m`, `3m`, `5m`.
- HTF-box logic is intentionally absent; `levels_tf` is either `entry_tf` or auxiliary `5m` for `OI`.
- key windows are time-normalized internally and then converted to bars for the current timeframe.
- no portfolio ranking is used yet; the first iteration runs per symbol.
- the strategy is designed to maximize candidate frequency first, then tighten filters in later iterations.
- each trade now exports time-boundary markers (`entry_on_1m_boundary`, `entry_on_5m_boundary`, `entry_on_30m_boundary`, `entry_on_60m_boundary`) for report slicing.

## Parameter Grid

The strategy no longer runs a single baseline combination per profile.

Each PPA profile now expands into a research grid of named variants around the same thesis, for example:

- `baseline`
- `early_absorption`
- `clean_break`
- `continuation_push`
- `mean_revert_pop`
- `strict_support`
- `late_confirmation`
- `wide_stop_runner`

Each variant moves only a small set of parameters:

- flow strictness (`taker_ratio_threshold`, `taker_volume_mult`)
- lower-zone and entry position tolerance
- structure confirmation speed (`structure_break_minutes`, `micro_base_minutes`, `entry_break_buffer_atr`)
- local stop regime (`stop_buffer_atr`, stop-width limits)

Results export `ppa_grid_variant_id`, so research reports can be compared by named setup family instead of one anonymous baseline row.

## Research Runner

The preferred entry point for analysis is the dedicated research command:

```bash
python main.py run-ppa-research --ppa-profile balanced
```

What it does:

- runs `post_pump_absorption` on `1m`, `3m`, `5m` by default;
- keeps every timeframe in its own results subtree;
- exports per-symbol diagnostics for the best combination of each timeframe;
- builds consolidated research artifacts in one root directory:
  - `summary_by_timeframe.csv/json`
  - `combined_results.csv`
  - `all_best_trades.csv`
  - `symbol_summary.csv`
  - `diagnostics_by_symbol.csv`
  - `diagnostics_summary_by_timeframe.csv`
  - `research_report.md`
  - `charts/*.png`

Useful flags:

```bash
python main.py run-ppa-research --top-n 80 --ppa-profile strict
python main.py run-ppa-research --symbols BTC/USDT ETH/USDT SOL/USDT
python main.py run-ppa-research --timeframes 1m 5m --output-dir ./.output/results/ppa_manual_run
```
