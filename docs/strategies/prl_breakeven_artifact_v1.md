# PRL breakeven-only exit — a caught backtest ARTIFACT (protocol + result)

**Code:** `prl/research/be_only.py` (includes the shuffle control) · **Status:** IS. **Artifact — not
a real edge.** A worked example of the audit discipline catching a mirage.

---

## What was tested

User question: on the **main daily book**, apply breakeven **without an initial stop** — hold as
usual (never cut early losers), but once a position is clearly in profit (a trigger fires) place a
stop at entry so a winner cannot become a loser. Triggers: ATR move, prior-candle break, two
consecutive favorable candles; on 1h and 1d granularity.

## What it looked like (spectacular)

Per-trade (market-relative) and per-rebalance, the daily-candle triggers looked incredible:

```text
REAL daily-marked dollar-neutral decile book (true market P&L, cost x1):
  exit    CAGR   Sharpe  +weeks  +months  maxDD   by-year
  FIXED   +6%    0.33     50%     48%      -42%    2023 -25% 2024 +39% 2025 +19%
  prev    +82%   2.52     68%     91%      -11%    2023 +52% 2024 +216% 2025 +126%
  two     +139%  3.15     72%     97%      -14%    2023 +63% 2024 +444% 2025 +260%
```

Sharpe 3.15, +139% CAGR, positive every year — exactly the hundreds-%/high-consistency target.
After a whole study where nothing beat ~Sharpe 1.5, a mere exit rule tripling Sharpe is a **red
flag**, not a celebration.

## The audit that killed it — shuffle test

Replace the score with **random per-date ranks** (no predictive information) and rerun:

```text
REAL score:      none Sharpe 0.33  ->  two Sharpe 3.15  (+139%)
SHUFFLED score:  none Sharpe -0.68 ->  two Sharpe 2.50  (+77%, +weeks 67%)
```

**With a completely random signal, breakeven-'two' still yields Sharpe 2.50.** A real edge collapses
to ~0 on shuffled input (as the fixed book does: −0.68). The breakeven mechanism therefore
**manufactures the return from nothing** — it is a backtest artifact.

## Why (mechanism)

The "exit exactly at **entry** whenever the daily high/low touches it" assumes a perfect intrabar
fill at the breakeven level. Combined with the asymmetric payoff (cap losers at 0, let winners run),
this favorable-fill assumption creates positive skew out of any volatile series — the classic
intrabar-stop-fill trap. It has nothing to do with the PRL signal.

## Doing it "properly" — gap-aware, slippage-charged fills

The idealized "exit exactly at entry" was the main culprit, so we redid it properly: gap-aware fill
(if the bar opened through entry, fill at the open, else at entry) plus spread+slippage against us.
Re-running with the shuffle control (top-100 decile book):

```text
fill    signal     exit    Sharpe
ideal   real       two      3.15       ideal   shuffled  two   2.50   <- big artifact
real    real       none     0.83       real    shuffled  none  0.01
real    real       two      1.32       real    shuffled  two   0.55
```

Proper fills **shrink the artifact** (shuffle 2.50 → 0.55) — the user was right that the exact-level
fill was cheating. **But it is not eliminated, and it is not signal-specific:** breakeven adds
~+0.5 Sharpe to the real book (0.83→1.32) *and about the same to a pure-noise book* (0.01→0.55). So
the remaining lift is a **generic stop-overlay** (favorable asymmetry on non-martingale daily
prices + residual daily-bar fill optimism), not PRL alpha. At the most realistic execution tested
(1h intrabar breakeven, `be_only.py --tf`/`stop_be.py`), the benefit is ~nil (+1.1% vs +1.0%).

## Verdict & lesson

**Breakeven is not a real, signal-attributable edge for this strategy.** The more realistically it
is executed, the more the benefit vanishes: ideal daily fill = huge artifact; gap-aware daily fill =
smaller artifact that helps noise as much as signal; 1h intrabar = ~nothing. Daily book conclusion
unchanged: ~Sharpe 1.4-1.5, ~20% CAGR, ~62% positive weeks, market-neutral, 2025-concentrated.

Lesson (worth keeping): (1) any exit/stop rule that looks like it triples Sharpe must be
shuffle-tested before it is believed; (2) even after realistic fills, subtract the shuffle
(no-signal) result — an overlay that helps noise as much as signal is not alpha. The discipline
worked: the mirage was caught on IS, before we trusted it or spent the OOS holdout.
