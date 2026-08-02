# Session-break of the previous-session high — discovery report

**Question.** Segment time into trading sessions and their overlaps. When price in
the current session crosses the *previous* session's high, what — measured at the
break — separates the cases that then run far up from the cases that don't develop
a long move? Emphasis on **relative** volume/trade elevation, and on treating
**cap tiers** (low / mid / high) as mechanically distinct.

**Status.** DEV-only discovery portrait (2025-06-03 → 2026-01-01). No execution
claim. OOS (≥ 2026-01-01) untouched. Code: `strategy/session_break/research/`.

---

## Design

**Sessions (UTC, tiling — non-overlapping so blocks form a sequence):**

| block | hours | role |
|-------|-------|------|
| ASIA | 00–08 | thin books; where low-caps get manufactured |
| EU | 08–13 | liquidity ramps |
| OVERLAP | 13–16 | EU–US overlap: deepest, most volatile |
| US | 16–21 | US flow / macro |
| LATE | 21–24 | post-US dead zone |

**Two "previous session" variants, kept strictly separate (never pooled):**
- **sequential** — the immediately preceding block (US breaks OVERLAP's high; ASIA
  breaks the prior day's LATE high). The classic prior-session-range breakout.
- **same_type** — the same block one day earlier (today's US vs yesterday's US).

**Event** = the first bar of the current block whose high exceeds the reference
high, **required to open below the reference** (a genuine intra-session cross, not
a level price already sat above). All features causal (bars ≤ break).

**Cap tier** from trailing-24h USDT turnover — absolute cuts, because the mechanics
(book depth, cost-to-move) are absolute: low < 30M, mid 30–150M, high ≥ 150M.

**Outcome / runner label.** Forward MFE from the break, ATR-normalised, labelled
into top vs bottom tercile **within each (variant × tier × session) stratum** — so
"far" means far *relative to what is normal for that tier and session*. Two outcome
definitions, and the difference between them is the whole story (below):
- rest-of-current-session MFE (the literal spec), and
- **fixed 120-min forward MFE** (the confound-free version).

**Controls.** Every multivariate AUC is week-grouped cross-validated and compared
to a within-week label-shuffled null; univariate AUCs reported per feature.

Event counts (DEV): sequential 246,700 · same_type 73,736. (The open-below-reference
gate removes ~70% of naive same_type "breaks" — price that had simply trended above
a stale level. Skipping this gate produces a spurious AUC 0.96 driven entirely by
`poke_atr` — a textbook non-event leak.)

---

## The load-bearing methodological finding

**Measuring the move to *session end* is a trap.** An early break has more session
minutes left, so its running max is mechanically larger. Under the rest-of-session
target, `bars_to_break` looks like the dominant predictor (AUC ≈ 0.60) and the
volume features look weak-to-inverted. This is an artifact of window length, not a
signal.

**Under a fixed 120-min forward horizon the artifact disappears:** `bars_to_break`
collapses to AUC ≈ 0.50–0.51, and the relative-participation features surface as the
top discriminators. Everything below uses the fixed horizon.

---

## Result — a modest, real, participation-driven edge

Multivariate week-CV AUC (fixed 120-min), full feature set vs microstructure-only
(rvol, rtrades, ats_ratio, taker, OI, liq — i.e. dropping poke/timing/runup):

| variant | tier | full AUC | micro-only AUC | shuffled null (95%) |
|---------|------|----------|----------------|---------------------|
| sequential | high | 0.551 | 0.529 | 0.491 |
| sequential | mid | 0.556 | 0.533 | 0.497 |
| sequential | low | 0.569 | 0.542 | 0.502 |
| same_type | high | 0.555 | 0.542 | 0.514 |
| same_type | mid | 0.571 | 0.549 | 0.497 |
| same_type | low | 0.588 | 0.560 | 0.501 |

- **A genuine edge exists but is weak** (AUC ~0.55–0.59), the same modest ceiling as
  the earlier runner-ness work. Discrimination ≠ profit (see caveats).
- **It is carried by relative participation.** `rtrades` (break-bar trade count vs
  trailing-24h baseline) is the single most consistent feature (univariate AUC
  0.54–0.56), just ahead of `rvol`. Poke/runup add only ~0.02–0.03 on top of micro.
- **Direction matches the mechanics:** runners have *higher* relative volume and
  *more* trades at the break. Every stratum, sign-stable.

Weak-to-null features at this trigger (unlike at pump ignition): **average trade
size** (`ats_ratio`, whale-vs-retail) only ~0.52; **taker-buy imbalance** ~0.49–0.51;
**OI change** ~0.50–0.51; **short-liquidation fuel** ~0.50 (and largely absent from
the free cache). The whale/retail split that mattered at ignition does **not**
separate runners here.

---

## Cap-tier reading (as requested, qualitatively)

The tiers behave as the mechanics predict, and the *same observable inverts in
weight* across them:

- **Low-cap (thin books).** The participation edge is **strongest** here
  (AUC 0.54–0.59). On a thin book price is cheap to move, so a *broad* participation
  surge (many trades, not just volume) is the meaningful separator between an organic
  pile-in that continues and a one-actor poke that exhausts. `rtrades` beating `rvol`
  is exactly this: breadth of participation > raw size.
- **High-cap (deep books).** Weakest edge (AUC ~0.53–0.55). Price is expensive to
  move, breaks of a widely-watched level are efficient/crowded, and much of any move
  is BTC-beta rather than the break itself — so a local participation surge carries
  less independent information.
- **Mid-cap.** In between, as expected.

**Session/overlap.** Median forward extension is **highest in ASIA** and **lowest in
OVERLAP and LATE** (e.g. low-cap ext_pct: ASIA 1.29% vs OVERLAP 0.89%). The EU–US
overlap is *not* where breaks run furthest at the median — it is the most crowded,
efficient window, so prior-session-high breaks there get faded more readily. Thin
ASIA, breaking the dead-zone LATE high, extends most. (This is a median statement;
tails are fatter on low-caps.)

---

## Caveats / what this is not

- **DEV, in-sample, no OOS validation yet.** AUC ~0.55 is a weak ranker.
- **Discrimination ≠ tradeable edge.** Prior work (runner-ness AUC 0.62) had a real
  ranker whose profit was a once-per-dataset lottery. Nothing here contradicts that;
  no PnL was simulated.
- No stop/exit path simulation was run — this is discovery only.

---

## IS squeeze (2026-07-29, still DEV-only)

Four upgrades, all on IS: (a) richer participation features, (b) level/trend
context, (c) a GBM with interactions, (d) alternative outcome shapes — plus the
one test that mattered most.

**Enriched features that helped.** `vol_accel` (last-5m volume vs prior-15m — the
*steepness* of the surge into the break) became the single best univariate feature
(~0.55–0.57), ahead of `rtrades`/`rvol`. `ema_dist_atr` (extension above the causal
1-day EMA — trend alignment) and `climax` (break-bar volume vs block max) also add.
`taker_buy_share`, `oi_change`, `liq_fuel`, `ref_touches`, `green_run` stay weak.

**GBM (HistGradientBoosting, week-grouped CV) top-decile forward-MFE lift** — the
practical number, "if you traded only the model's top 10% of breaks, how much
bigger is the 120-min move than a blind break in that stratum":

| variant | tier | GBM AUC | top-decile MFE lift |
|---------|------|---------|---------------------|
| sequential | high / mid / low | 0.564 / 0.569 / 0.576 | ×1.41 / ×1.92 / ×1.36 |
| same_type | high / mid / low | 0.539 / 0.569 / 0.599 | ×1.53 / ×1.42 / ×1.58 |

GBM adds only ~+0.01 AUC over logistic — the signal is mostly additive, not deeply
interactive — but the top-decile lift is a tangible ×1.4–1.9.

**The test that mattered — is the lift a steady edge or a one-week lottery?**
(`week_concentration.py`.) Scoring every DEV break out-of-fold and checking the
top-decile across 31 weeks:

| variant / tier | weeks top-decile beats the weekly median | max single-week share of total lift |
|---|---|---|
| sequential high / mid / low | 90% / 97% / 97% | 8% / 7% / 7% |
| same_type high / mid / low | 90% / 97% / 100% | 7% / 8% / 6% |

**This breaks the one-week-lottery failure mode** that killed the earlier
runner-ness edge: the lift is spread evenly across time (no week worth > 8%), and
the top decile out-performs in almost every week. On IS this is a genuinely
time-stable ranker.

**But the outcome-shape check flags an execution catch.** Against a path target
`fp_2_15` (did price reach +2 ATR *before* falling −1.5 ATR within 120 min) the
features **invert**: high-participation / violent breaks are *more* likely to tag
the downside barrier first. So the biggest-MFE breaks are also the most whippy — the
upside is real but a tight stop gets hit first. Practical read: capturing this edge
needs a wide/structural stop, not a tight one — the execution phase must test that.

⚠️ **Do not be fooled by the fp AUC.** The GBM on `fp_2_15` scores AUC **0.71–0.73**
(vs ~0.55–0.60 for MFE) — but this is *not* a bigger edge. The barrier is in
*trailing* ATR units that lag the surge, so the target is dominated by near-term
realised volatility, which clusters and is trivially predictable from the break's own
volatility. The model is mostly predicting "how volatile is this break," not "will
this trade profit." The trustworthy result is the MFE ranker; `fp_2_15` only
corroborates the whipsaw direction. A clean path target needs fixed-R barriers, not
lagging-ATR ones — a fix for the execution phase.

**Net IS position.** A participation-driven ranker (`vol_accel` + `rtrades`/`rvol`
+ trend context) that is discrimination-modest (AUC ~0.55–0.60) but **time-stable**
(top decile ≈ ×1.4–1.9 forward MFE, positive in ~90–100% of weeks), strongest on
mid/low-cap and in the same_type variant. The catch is whipsaw: MFE ≠ capturable
P&L until an exit is proven.

---

## IS execution test (2026-07-29) — MFE does NOT convert to R

Top-decile ranker picks (out-of-fold scores, `score_events.py`) traded through the
frozen core `simulate_long_path` (`execution.py`), reusing the structural ZigZag
swing trail. Grid = 4 entries × 4 initial stops; costs ≈ 30 bps round trip;
stop/trail unit is the 30-min range (the per-minute ATR is microscopic and makes
every stop a whipsaw — a real scaling trap). Look-ahead audited: next-open fills,
decision bars use only past bars, forward window sliced to the horizon, OOF scores.

**Entry timings tested** (the user's question — where to enter):
`break` (at the break), `confirm15` (first 15-min close above the level),
`retest` (pullback to the level), `prebreak` (front-run: enter below the level on
the activity surge in the run-up). **Stops:** `atr_narrow` 0.5×, `atr_wide` 1.5×,
`struct30`/`struct60` (structural swing low).

**Result — no cell is both positive-mean and week-stable** (362k top-decile trades):

| finding | detail |
|---------|--------|
| best cell | `prebreak × struct60` mean **+0.047 R**, but positive in only **29% of weeks** — lottery, not steady |
| every clean entry | `break`/`confirm15`/`retest` × any stop: mean **−0.15 to −0.39 R** |
| wider stops win | `atr_narrow` worst everywhere → confirms the whipsaw (tight stop self-defeating) |
| best entry | `prebreak` least-bad (better fill *below* the level) — but it is the *optimistic* entry (conditioned on a break following), so ≈breakeven is an upper bound |
| win rate | 25–34%; median trade −0.35 to −0.5 R |

**Why.** The forward MFE is real and the ranker is time-stable, but in volatility
terms the MFE is only ~comparable to the stop you must give the trade to survive
the whipsaw → ~1:1 reward:risk at ~30% win → negative after costs. Discrimination
≠ P&L, again. **The session-break long is not tradeable as-is on IS.** OOS never
touched — and there is no reason to spend it until IS execution is positive.

---

## Mean-reversion FADE to the session mean (2026-07-29) — real but not tradeable

The long fizzles because ~70% of breaks come back; so trade the *fade* — short the
pop back to the current session's VWAP. Files: `events.py` (session-VWAP + reversion
outcome), `fade_backtest.py` (bracket short: target = session VWAP, stop above the
breakout high; entries break / ext1 / ext2 / stall; stop buffers 0.3 / 1.0 / 2.0 ×
the 30-min range), `fade_report.py`. Look-ahead audited; DEV-only; ~30 bps RT.
Same per-minute-ATR scaling trap avoided (stops in 30-min-range units).

**What unites reverters vs flyers (causal, online — the question asked).** Reverter
ranker week-CV AUC **0.71** (much > the long's 0.55, though partly mechanical via
`stretch_vwap_atr`, which defines the target distance):
- **reverters** = a concentrated volume **climax/spike** at the break (`block_vol_share`
  AUC 0.66, `climax` 0.60), **less stretched** above VWAP, **early** in the session —
  a poke that exhausts.
- **flyers** = **sustained** volume (spread across the block, low `block_vol_share`),
  more stretched, trend-aligned — genuine demand that keeps going.

**But it does not convert to profit** (2.17M DEV fade trades):

| view | result |
|------|--------|
| best blind cell (wide stop, stall/break) | win **61–62%**, **median +0.06–0.07 R**, but **mean −0.07 to −0.08 R** |
| over-extended (stretch 4–6) | win 64%, **median +0.17 R**, mean −0.11 (flyer tail) |
| top reverter-decile | *worse* — ranker picks **low-stretch** (tiny reward), win falls, mean −0.08 to −0.5 |
| stretch≥4 × reverter-score quartile | win rises 55%→69% across quartiles, **mean pinned at −0.07** (ranker moves win-rate, not expectancy) |
| best cell anywhere (stretch≥6, score≥0.7, wide stop) | mean **−0.025 R at 71% win**, median +0.12 — breakeven-negative, only 32–35% weeks positive |

**Why.** Reward (`stretch`) and revert-probability are **anti-correlated**: the
over-extended pops pay big if they revert but are exactly the ones more likely to
keep flying. Expectancy is pinned just below zero across every selection; the ranker
raises win-rate to ~70% but cannot lift the mean, and ~30 bps costs on the small
VWAP-target reward are the final nail. **Reversion is real (~65–70% hit) but not a
tradeable edge as constructed.**

## Fade re-examination (2026-07-29) — a caught bug, then a quantified verdict

Pushed to find *where* the fade is profitable rather than stop at "no", we rebuilt
the fade as a proper short engine (`fade_exec_v2.py`) with N*ATR stops, structural
trail, partial exits, a session/tier/stretch grid, and a horizon sweep. An apparent
**+0.29 R, 70% win, 100%-positive-weeks** regime showed up — and on adversarial
checking it was a **bug, not an edge**: the engine simulates shorts by negating
prices and reusing the long-only frozen core, which applies slippage in the LONG
direction; under negation that becomes *favourable* to the short. On ~1.4%-of-price
stops, ~10 bps/side of wrong-signed slippage ≈ +0.34 R of pure artifact. (Isolation:
intrabar==close, horizon 120==720, bracket==trail all gave the same +0.29 — none of
those was the driver; zeroing costs collapsed the gap to ~0.)

**Corrected, sign-clean result** (GROSS R from the core with a −1 stop floor, real
round-trip cost charged in R-space via each trade's risk fraction):

| stop | gross mean R | win | median R (gross) | breakeven round-trip cost | net @ maker 4 bps | net @ taker 30 bps |
|------|-------------|-----|------------------|---------------------------|-------------------|--------------------|
| 1·ATR | **+0.015** | 67% | +0.245 | **~1.1 bps** | −0.026 | −0.263 |
| 2·ATR | +0.004 | 73% | +0.159 | ~0.5 bps | −0.018 | −0.145 |

The **typical fade reverts profitably** (median +0.16–0.25 R gross, 67–73% win), but
the **mean gross edge is ~+0.015 R and the breakeven cost is ~1 bp round-trip** — an
edge 10–30× too small to survive any realistic execution (even maker ≈ 4 bps is
negative; taker ≈ 30 bps is −0.26 R). The flyer tail eats the mean; costs finish it.

**So the honest answer to "under what circumstances is the fade profitable": only at
sub-1-bp round-trip cost — unattainable in practice.** The reversion tendency is real
and the levers were exhausted (exit family, N·ATR stop width, holding horizon,
session, tier, stretch, ranker selection); none lifts the gross edge above ~+0.02 R.

## Failed-break RECLAIM short + TF sweep (2026-07-29)

Refined setup (`reclaim.py`, `reclaim_report.py`): current session pokes above the
prior session high, FAILS, and closes back inside the prior range (n_confirm closes,
seller aggression, falling volume) → short at the reclaim, stop above the poke high,
structural trail, book half at the prior-range MID. Sign-clean short engine. Swept
candle TF (15/30/60m), sequential variant, n_confirm=1. The headline question — *was
the prior high a REAL distribution high (big up-impulse then real sell-off) or a drift
high that means nothing* — is a first-class feature.

**The prior-high-quality thesis is validated, but only at higher TF:**

| TF | ALL gross R | real-dist-high top⅓ gross | breakeven cost | pos-weeks (real-high mid-cap) |
|----|------------|---------------------------|----------------|-------------------------------|
| 15m | +0.027 | −0.014 (inverts — noise) | <0 | 32% |
| 30m | +0.014 | +0.012 (weak) | ~2 bps | 32% |
| **60m** | −0.003 | **+0.030** | **~5 bps** | **58%** |

At 60m the real-distribution-high filter lifts gross from −0.003 to **+0.030**,
breakeven ~5 bps, positive in 52–58% of weeks — **5× more cost-robust than the naive
fade (~1 bp)**, a real gain from the refined setup. Still under realistic taker cost
(~20–30 bps): net @ 6 bps ≈ −0.006 (breakeven). Univariate quality features that help
(60m): `prior_up_imp`/`prior_down_after` (real high), `pre_atr` (pre-poke volatility),
pre-poke trades/volume. **Counter to intuition, a big poke wick is WORSE** (a violent
wick = stop-hunt likelier to re-run) — drop it. The combined **OOF GBM ranker FAILS**
(top-decile net −0.08, overfits) — the single monotone real-high filter beats a fitted
model. Not yet run: same_type variant (Ev1↔Ev2, range-held gate), n_confirm 2/3, sub-5bps
cost model. Verdict: best tradeable-looking cell needs **<5 bps round-trip** — maker-only.

## MID-vs-ABOVE discrimination on unconfident-poke events (2026-07-29) — STRONGEST result

Reframed from trade-R to a labelled-event classification (`reclaim_label.py`,
`rlabel_discriminate.py`): every unconfident poke above the prior-session high that
returns into the range is an event; label = reached the range MID (revert) vs
consolidated back ABOVE the high (real break), first-passage over the current+next
session. 29 causal market-logical features, CatBoost week-grouped OOF.

Event counts (DEV, sequential): **60m 369,605 (76% MID) · 30m 398,220 (66%) ·
15m 223,944 (56%)** — higher TF, more reverts.

**60m, n=199k, MID base 75.2%:**
- **week-CV AUC = 0.738** (shuffled null 0.504) — by far the strongest discrimination
  in the project (everything prior was ~0.55–0.62).
- **top-decile MID rate = 98.6%** vs 75.2% base; **positive in 100% of 31 weeks**.
- **Mechanical-leak check PASSES:** dropping `prior_range_atr` (partly defines the
  mid distance), `poke_depth_atr`, `bars_above` only drops AUC to **0.708**;
  top-decile still 97% MID, 100% weeks. The signal is real microstructure, not the
  target's geometry.

**Clean (non-mechanical) discriminators:**
- **→ MID (revert):** `ret_body` (bearish return candle = seller aggression on the
  reclaim — top feature both runs), `ret_rvol` (real volume on the return), a *weak*
  prior high (low `prior_up_imp`), price NOT extended above the daily EMA.
- **→ ABOVE (real break):** `prior_up_imp` (prior high built by a big impulse =
  genuine level, breaks again), `ema_dist` (trend extension), `pre_ret` (momentum
  into the poke), deep/long/fast poke.

This validates the user's whole thesis: the **quality of the prior high** (impulse
distribution vs drift) plus **seller aggression on the return** genuinely separate
reverters from real breakouts. Caveat: DEV in-sample; base rate is 75% MID so the
edge is the lift above base (top-decile 97–99% MID). The natural next step is an
execution test on the top-decile-selected events (target = range mid, stop above the
poke high — a ~32:1 direction ratio in the top decile) + OOS.

**Look-ahead / optimistic-bias audit (`reclaim_audit.py`) — PASSED.** Same
features/model under three CV schemes plus a null:
- week-GroupKFold **0.734** · forward-temporal (train early weeks / test late)
  **0.720** · symbol-GroupKFold (unseen coins) **0.751** · label-shuffled null
  **0.505**.
- 0% of stored events unresolved; only 0.9% have an outcome horizon crossing into
  OOS (negligible boundary effect).
- Features are causal by construction (every feature uses only bars ≤ the return
  bar; the outcome uses only bars after it), and the edge does not lean on the
  geometry features (AUC 0.708 without them).
Conclusion: the discrimination survives temporal extrapolation and unseen-symbol
generalisation with a clean null — no material look-ahead or optimism. It is a
genuine, robust DEV-IS regularity (still to be converted to P&L and scored OOS).

## RR>=3 execution — a CONDITIONAL positive edge (2026-07-29, `reclaim_rr.py`)

User insight: filter the "too late" entries by requiring reward:risk >= 3 (reward =
entry->range mid, risk = entry->stop, stop behind the prior-session HIGH). Causal,
live-executable (RR computed at the entry open). First-passage bracket outcome
(low<=mid = +RR win; high>=stop = -1). 149k events, 60m.

| filter (stop behind high) | n | MID | win@RR | gross R | net @0bps | pos-weeks @0bps | net @2bps | net @6bps | risk |
|---------------------------|---|-----|--------|---------|-----------|-----------------|-----------|-----------|------|
| any RR | 149k | 0.67 | 0.43 | −0.03 | | | | −0.18 | 0.61% |
| **RR>=3** | 37k | 0.48 | 0.18 | **+0.082** | **+0.061** | **61%** | −0.030 | −0.20 | 0.27% |
| RR>=4 | 25k | 0.45 | 0.15 | +0.108 | +0.077 | 58% | −0.025 | −0.22 | 0.24% |
| RR>=5 | 17k | 0.43 | 0.14 | +0.125 | +0.079 | 61% | −0.032 | −0.24 | 0.22% |

**First genuinely positive, reasonably week-stable P&L in the project — but CONDITIONAL
on near-maker cost (<~1-2 bps round trip).** RR>=3 flips gross positive (removing the
low-reward "already near mid" entries works). It is a low-winrate (18%), high-RR (3-5R)
trade → ~60% positive weeks, high variance. Three catches: (1) the stop behind the high
is tiny (~0.27% of price) so it is extremely cost-sensitive — breakeven ~1-2 bps, deeply
negative at taker (20-30 bps); (2) MID drops to 48% (the tight stop is re-poked before
reversion); (3) the OOF MID score does NOT transfer to the RR-filtered subset (its 98%
precision was on easy low-RR already-near-mid events). Execution trap: the confirmed-
reclaim entry is naturally a TAKER (momentum) entry; capturing the maker edge needs
patient limit fills at risk of missed entries. So: a real edge only under cheap/maker
execution, not a free lunch. Next: wider/structural stop (less cost-fragile), realistic
maker-entry model, OOS.

## Win-vs-loss portrait — the trade outcome is stop-path NOISE (`reclaim_winloss.py`)

Target = the actual trade R sign (win = gross_r>0), not the MID label. Result:
**win-vs-loss week-CV AUC = 0.532 (null 0.506) — near-null.** Every feature's median
is nearly identical between winners and losers (all |Spearman| <= 0.07; weak leanings:
winners have marginally bigger prior_range, deeper/faster poke, more EMA extension,
bigger prior up-impulse — near noise, partly mechanical).

The decisive contrast: *where price ends up* (mid vs consolidate-above) is predictable
(**AUC 0.73**), but *whether a given trade wins* is **not** (**0.53**). The gap is
**stop-path noise** — a tight stop above the poke gets tagged on the way to the mid even
when the structural resolution is MID. Implication: feature-based SELECTION for win/loss
is a dead lever (unlike the MID label, which has real signal); the value is in EXIT
MECHANICS — a stop that survives the poke re-test to capture the real 0.73 structural
edge (this is why the wider structural stop had the best cost-headroom). The path forward
is "a stop the path can't shake out" + cheap execution, NOT "pick better trades".

## Net verdict on session-break

Rich, real structure — a time-stable participation ranker (long) and a strong
reverter/flyer discriminator (fade) — but **neither direction is profitably
tradeable on free 1m data after costs.** Consistent with the project's standing
pattern. OOS never touched.

## If continued (low expected value, in priority order)

1. Fade with **lower-cost assumptions** (maker fills / partial-reversion target) — the
   best cell is only ~−0.025 R, so a cheaper cost model is the one thing that could
   flip it; test honestly, don't assume fills.
2. **Independent pre-break detector** (long side) — the only near-breakeven long entry,
   currently break-conditioned/optimistic; build the activity-surge trigger standalone.
3. Only if any IS cell turns genuinely positive AND week-stable: freeze and score OOS.
