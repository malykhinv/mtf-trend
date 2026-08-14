# Combined regime-complementary book: PRL + breakout-trend (protocol + result)

**Code:** `prl/research/combined_book.py` · **Status:** IS. OOS reserved. **Best uniformity result.**

---

## Idea

PRL (market-neutral, net-short-beta) profits in the 2025 bear/dispersion; a breakout-long trend
sleeve (Donchian 20/10, breadth-scaled) profits in 2023/24 risk-on -- exactly where PRL is weak.
If they are negatively correlated, the sum is far more uniform than either alone.

## Result (daily-marked, full IS)

```text
sleeve                 CAGR  Sharpe +wk  +mo  maxDD  by-year
PRL market-neutral     +21%  1.51   58%  68%  -10%   2023 -1%  2024 +17%  2025 +62%
PRL beta-hedged        +22%  1.90   59%  75%  -11%   2023 +8%  2024 +22%  2025 +48%
Breakout-long trend     +8%  0.41   41%  39%  -35%   2023 +37% 2024 +14%  2025 -18%

corr(PRL, Breakout) = -0.43     corr(PRL_bh, Breakout) = -0.03

combined:
PRL+BO 50/50           +16%  1.27   50%  71%  -14%   2023 +17% 2024 +18%  2025 +17%
PRL+BO vol-parity      +18%  1.80   57%  82%   -7%   2023 +11% 2024 +19%  2025 +32%
PRLbh+BO vol-parity    +19%  1.66   55%  71%  -10%   2023 +16% 2024 +21%  2025 +26%
```

## Read

The sleeves are genuinely regime-complementary (**corr −0.43**). Combining:
- **Evens the years dramatically** — PRL alone is 2025-dominated (−1/+17/+62); the 50/50 book is
  +17/+18/+17 (near-identical every year), vol-parity +11/+19/+32.
- **Raises Sharpe** (1.51 → 1.80 vol-parity) via the negative correlation, and **cuts drawdown**
  (−10% → −7%), **lifts +months** (68% → 82%).

This is the first result that materially improves year/month uniformity AND risk-adjusted return --
the regime complementarity is real and mechanistic (long-beta trend sleeve offsets the short-beta
market-neutral sleeve).

**Caveats:** still IS (OOS reserved); WEEKLY positive-share stays ~50-57% (the weekly ceiling is
unbroken -- uniformity gain is at the year/month scale); the breakout sleeve is weak standalone
(Sharpe 0.41) and valuable only as a diversifier.

## Rolling window (no calendar statics) — combined_v2.py / combined_v3.py

The v1 PRL sleeve rebalanced every 15 CALENDAR days (single phase, lumpy: ~6.6 name-trades/day but
all batched on 68 rebalance days). Making PRL fully ROLLING (overlapping daily tranches, rebalance
1/H of the book daily) and sweeping the window H:

```text
rolling-PRL sleeve:  H=5 Sh1.43 +wk 63%  |  H=10 +wk 60%  |  H=15 +wk 59%  |  H=20 Sh1.45 +wk 57%  |  H=30 +wk 56%
combined (roll-PRL H + BO, vol-parity):  H=5 Sh1.73 +wk56 +mo75  |  H=15 Sh1.73 +wk54  |  H=20 Sh1.75 +wk51 +uniform yrs +10/+23/+23
```

**Rolling window matters for WEEKLY consistency**: short H=5 lifts the PRL sleeve to **63% positive
weeks** (breaks the 58% single-phase ceiling), long H raises Sharpe/uniformity. But the breakout
sleeve is only 41% weekly (directional trend), so it TRADES weekly for year-uniformity+Sharpe --
the combined weekly is ~51-56%. To get BOTH high weekly and uniform years, the second sleeve must
be uncorrelated AND high-weekly (the breakout gives uniformity, not weekly).

## Runner-SIZED + regime-scaled sleeve — regime_runner_book.py (2026-08-14)

Applying the wide-grid conclusion (direction unpredictable, runner-size real & regime-driven): we
STOP filtering breakout direction and instead SIZE each confirmed-hold breakout by its walk-forward
runner-score (OOF AUC 0.596), and scale the sleeve's GROSS exposure by a causal breadth-rank regime
factor. Hold-to-time HH=48h, no stops (never cut the fat-tail runners). 8,812 confirmed-hold events,
runner-rate 0.159. Standalone (long-beta) and vol-parity with the daily PRL sleeve:

```text
breakout sleeve standalone     Sharpe +wk  maxDD  by-year
equal/flat                      0.73   57%  -86%  +181/+20/-25
runner/flat                     0.81   58%  -90%  +242/+26/-18
equal/regime                    0.64   52%  -81%   +89/+33/-12
runner/regime                   0.72   53%  -87%  +115/+39/ -3
SHUF-runner/flat (control)      0.64    -     -    +145/ -4/-32   <- runner-sizing beats shuffle
equal/SHUF-regime (control)    -0.09   49%  -94%   -15/-57/-58   <- regime beats shuffle (badly)

combined vol-parity (PRL + variant)   Sharpe +wk +mo  maxDD  by-year
PRL alone                              1.51  58% 68%  -10%   -1/+17/+62
PRL + equal/flat                       1.79  61% 79%  -10%   +9/+23/+73
PRL + runner/flat                      1.86  62% 75%  -11%  +11/+24/+78   <- best Sharpe
PRL + equal/regime                     1.71  64% 79%  -11%   +6/+23/+71   <- best weekly
PRL + runner/regime                    1.77  64% 75%  -12%   +7/+23/+76   <- best weekly + uniform
PRL + SHUF-runner (control)            1.70  61%  -     -     +8/+21/+72
PRL + SHUF-regime  (control)           1.19  59% 64%   -     +5/ +7/+52
corr(PRL, breakout variants) = -0.19..-0.21
```

**Both levers are REAL (shuffle-validated) and monetizable:**
- **Runner-sizing** (size by predicted runner) beats its shuffle: standalone Sh 0.81 vs 0.64, CAGR
  +34% vs +12%; combined 1.86 vs 1.70. First time the runner-predictor converts to portfolio value.
- **Regime scaling** (breadth-rank gross) is critical: shuffling it collapses the sleeve
  (+20%->-35%, Sh 0.64->-0.09) and the combo (1.71->1.19). It lifts combined WEEKLY to **64%** (from
  57%) and evens the years, at a small Sharpe cost vs flat.
- **New best combined book:** runner/flat = Sharpe **1.86** (max), runner/regime = **64% weekly** +
  uniform years (+7/+23/+76). Beats the v1 combined (Sh 1.80, +wk 57%).

**Caveats:** (1) weekly is now 64% -- real progress but still short of the 80% goal; the breakout
sleeve itself is only ~53-58% weekly (directional), which caps the combined weekly. Breaking 80%
needs a HIGH-WEEKLY uncorrelated THIRD sleeve. (2) the standalone breakout sleeve has maxDD -86..-90%
(concentrated long-beta) -- safe ONLY inside vol-parity where PRL dominates the risk weight (combo
maxDD stays -10..-12%); never trade the sleeve alone. Still IS; OOS reserved.

## Third-sleeve hunt (NULL) + year-uniformity frontier (third_sleeve.py, uniform_book.py, 2026-08-14)

**Third sleeve NULL.** Funding-carry (short high-funding / long low-funding, dollar-neutral, marked on
price-funding) is REAL (shuffle collapses +2%->-5%, Sh 0.2->-0.77) but tiny (+2%/yr) and +0.28 corr
with PRL -> adding it HURTS the combo (Sh 1.80->1.33). XS mean-reversion LOSES every lookback
(-13..-19%, Sh -1.2..-1.4: momentum, not reversion, works cross-sectionally here). No high-weekly
uncorrelated third sleeve exists on free data.

**Year-uniformity reframing (user: no 10x year jumps; +150-200% EVERY year = the goal).** Selection
becomes MAXIMIN (maximize the worst year), not Sharpe. Runner-sizing lifted Sharpe but RE-concentrated
2025 -> worse on this criterion. Maximin picks **PRL+BO 50/50: +17/+18/+17 (ratio 1.1x), Sh 1.27,
maxDD -14%** -- the uniformity champion. Leverage LADDER on it (uniformity preserved, all years scale
together):

```text
PRL+BO 50/50   L=1x +18%/yr (+17/+18/+17) maxDD -14%   L=2x +36% (+37/+36/+35) -27%
               L=3x +55% (+59/+53/+53)     maxDD -38%   L=4x +73% (+83/+66/+71) -49%   L=6x +107% -65%
PRL+BO vol-par L=6x ~+177% but +78/+124/+329 (2025-driven, ratio 4.2x) maxDD -40%   <- high but NOT uniform
```

**BINDING CONSTRAINT (leverage arithmetic).** return/maxDD ~ Calmar; leverage scales BOTH -> Calmar
invariant (~1.3 uniform 50/50, ~2.6 vol-parity). So **+150%/yr implies maxDD -58..-115%** -- levering
50/50 to +150% worst-year needs ~12x (maxDD -92%, vol-drag turns 2024 negative = uniformity destroyed).
"+150-200% EVERY year at survivable drawdown" needs Calmar ~4+, which free-data edges (Sharpe topped
~1.8) do NOT provide; leverage cannot manufacture it. **Honest reachable point: 50/50 at ~3x =
+55%/yr uniformly (all years +53-59%) at maxDD -38%.** To push the frontier, raise UNLEVERED Calmar.

## Calmar overlay -- the frontier lift (calmar_overlay.py, 2026-08-14) -- SESSION PEAK

A causal VOL-TARGET overlay (scale_t = c/trailing_vol_{t-1}, avg exposure = 1 so it is pure TIMING,
not de-risking) roughly DOUBLES the book's Calmar, and it is shuffle-validated (permuting the scale
timing returns Calmar to base):

```text
50/50 book       base Calmar 1.12 (maxDD -14%)  ->  vol-target 1.98 (-9%)  vt+dd 1.95  |  SHUFFLED 1.14
vol-parity book  base Calmar 2.49 (maxDD  -7%)  ->  vol-target 3.26 (Sh 2.26!)          |  SHUFFLED 2.33

frontier levered to maxDD -38%:   50/50 base 3.0x +48%/yr (+59/+53/+53)
                                  50/50 vt+dd 5.5x +99%/yr, UNIFORM (+114/+120/+109, ratio 1.1x)
the +150%-EVERY-year dream:       50/50 base   -> 20x = RUIN (-119% DD, blows up)
                                  50/50 vt+dd  -> 7.7x = +181/+168/+152 (ratio 1.2x, UNIFORM) maxDD -50% worstMo -39%
                                  vol-parity vt+dd 7.4x +152/+218/+720 (non-uniform, 2025 blows out)
```

**The vol-target overlay is the lever that moves the target.** It raises unlevered Calmar (the binding
constraint) ~2x, and UNIQUELY on the uniform 50/50 book it keeps uniformity under leverage. That brings
the user's "+150-200% EVERY year" goal into reach ON IS: **50/50 + vol-target + dd-control, ~7.7x ->
all three years +152..+181% (uniform), maxDD -50%, worst month -39%.** More conservative: 5.5x ->
+109..+120%/yr uniform at -38% DD.

**Honest caveats (do NOT skip before OOS):** (1) IS ONLY, 3 years -- "every year +150%" on n=3 is
suggestive, not proven; the 7.7x is tuned so the worst IS year hits +150% = in-sample leverage fit.
(2) -50% maxDD / -39% worst month at 7.7x is brutal; liquidation risk at that leverage on crypto is
real. (3) leverage COSTS (funding/borrow on the levered notional, overlay scaling turnover) are NOT
yet modeled and will eat returns at 7-8x. (4) the overlay's Calmar gain must survive OOS.

## Robustness + leverage cost stress (calmar_robust.py, 2026-08-14)

**Overlay is robust, NOT a single-param artifact.** vol-target Calmar across a 16-config grid (lookback
10/20/40/60 x cap 2/3/5, dd-thresh/cut variants): min 1.17, median 1.66, max 2.22 -- ALL > base 1.12.
lb=20 is the sweet spot (1.95); lb=60 barely helps (1.17).

**Leverage cost is the SWING factor (corrected model: costs applied once, not re-levered).**
```text
canonical vol-target+dd overlay, net = L*ov - (L-1)*borrow_daily - |dscale|*L*2bps
L=5.5x (-38% DD):  borrow 0 -> +102% worst-yr (+113/+115/+102)   1bps/day -> +71% (+103/+82/+71)
                   2bps/day -> +45% worst      5bps/day -> -11% (costs eat it)
L=7.8x (-50% DD):  borrow 0 -> +142% worst (+182/+160/+142)      1bps/day -> +89% (+162/+103/+89)
                   2bps/day -> +47%            5bps/day -> -30%
```
Perps have no explicit borrow (leverage is embedded; margin = 1/L of notional); the real leverage cost
is FUNDING on the positions, and a dollar-neutral book's NET funding ~ 0 +/- small -> realistic drag
~0-1bps/day, where the levered overlay holds at **+74-100%/yr uniform** (-38..-50% DD). But the result
is SENSITIVE: 5bps/day kills it -> at high leverage FUNDING-NEUTRALITY is a first-order requirement.
This turns the earlier tiny funding-carry into a leverage COST control: tilt the book funding-POSITIVE
(short high-funding) so leverage cost becomes a credit.

## Next expansions (toward profit)

1. **Funding-aware leverage**: tilt the combined book funding-neutral/positive so the leverage cost is
   ~0 or a credit -- directly hardens the sensitivity that threatens the levered book.
2. Cross-sectional breadth (top-300 universe -> more independent bets -> higher base Calmar).
3. Freeze the combined spec (50/50 + vol-target overlay, funding-aware, chosen leverage) -> the single
   reserved OOS 2026-H1 test -- the real verdict. (Caveat that leverage is tuned in-sample.)
