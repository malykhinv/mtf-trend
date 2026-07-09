"""IS economics of the triple-tap / CAP strategy.

Portfolio simulation on the realised outcomes (``r_multiple`` from labels), with
transaction costs and fixed-fractional risk. Answers the question the project's
prior edges kept failing: does model-ranked selection make money NET of costs,
and is the equity real or carried by a handful of trades?

Cost in R = cost_rate / dist_stop_pct: a tight structural stop = high leverage =
costs eat more of each R. IS ONLY.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.triple_tap.research import TRADE_KEY

RES = Path(".output/results/triple_tap_v1")
DEPOSIT = 10_000.0
RISK_PCT = 0.01                          # 1% of equity risked per trade
COST_RATE = 0.0012                       # round-trip taker cost on notional (12 bps)


def _merge(labels_path: Path, oof_path: Path | None) -> pd.DataFrame:
    setups = pd.read_parquet(RES / "setups_discovery.parquet")
    setups = setups[setups["valid_entry"]][TRADE_KEY + ["stop"]]
    lab = pd.read_parquet(labels_path)
    df = lab.merge(setups, on=TRADE_KEY, how="inner", validate="one_to_one")
    df = df[df["r_multiple"].notna()].copy()
    df["dist_stop"] = (df["entry_price"] - df["stop"]) / df["entry_price"]
    required_times = {"fill_time_ms", "outcome_time_ms"}
    if not required_times.issubset(df.columns):
        raise ValueError("labels must be rebuilt with exact fill/outcome times")
    df["exit_ms"] = df["outcome_time_ms"].astype("int64")
    if oof_path is not None and oof_path.exists():
        oof = pd.read_parquet(oof_path)
        df = df.merge(oof, on=TRADE_KEY, how="inner", validate="one_to_one")
    return df.sort_values("fill_time_ms").reset_index(drop=True)


def _simulate(trades: pd.DataFrame, deposit=DEPOSIT, risk_pct=RISK_PCT, cost_rate=COST_RATE,
              slip_pct=0.0) -> dict:
    if trades.empty:
        return {}
    equity = deposit
    risk_dollars = risk_pct * deposit          # FIXED-fractional (non-compounding) -> interpretable
    curve = [equity]
    pnls: list[float] = []
    net_r: list[float] = []
    for t in trades.itertuples(index=False):
        # entry slippage on illiquid pumps: worse fill by slip_pct of price -> loses
        # (slip_pct / dist_stop) of an R (tight stop = bigger R hit). Applied on top
        # of the round-trip taker cost.
        cost_r = (cost_rate + slip_pct) / t.dist_stop if t.dist_stop > 0 else 0.0
        r = float(t.r_multiple) - cost_r
        pnl = risk_dollars * r
        equity += pnl
        pnls.append(pnl)
        net_r.append(r)
        curve.append(equity)
    curve = np.array(curve)
    pnls = np.array(pnls)
    net_r = np.array(net_r)
    peak = np.maximum.accumulate(curve)
    dd = (curve - peak) / peak
    wins = net_r > 0
    # streaks
    def _streak(mask):
        best = cur = 0
        for m in mask:
            cur = cur + 1 if m else 0
            best = max(best, cur)
        return best
    gross_win = pnls[pnls > 0].sum()
    gross_loss = -pnls[pnls < 0].sum()
    order = np.argsort(pnls)
    top5_profit = pnls[order[-5:]].sum()
    return dict(
        n=len(pnls), final=equity, ret_pct=(equity / deposit - 1) * 100,
        win_rate=wins.mean() * 100, expectancy_R=net_r.mean(),
        avg_win_R=net_r[wins].mean() if wins.any() else np.nan,
        avg_loss_R=net_r[~wins].mean() if (~wins).any() else np.nan,
        profit_factor=gross_win / gross_loss if gross_loss > 0 else np.inf,
        max_dd_pct=dd.min() * 100,
        max_win_streak=_streak(wins), max_loss_streak=_streak(~wins),
        top_win=pnls.max(), top_loss=pnls.min(),
        pct_profit_top5=top5_profit / pnls.sum() * 100 if pnls.sum() != 0 else np.nan,
        ret_ex_top5=((deposit + pnls.sum() - top5_profit) / deposit - 1) * 100,
    )


def _simulate_concurrent(trades: pd.DataFrame, max_k: int, deposit=DEPOSIT,
                         risk_pct=RISK_PCT, cost_rate=COST_RATE, slip_pct=0.0,
                         one_position_per_symbol: bool = True) -> dict:
    """Realistic portfolio: at most ``max_k`` positions open at once. Setups that
    arrive while ``max_k`` slots are full are SKIPPED (can't be taken) - this both
    caps simultaneous risk and collapses same-pump-day clusters to what you could
    actually hold. FCFS in fill-time order. PnL realised at each position's exit
    (curve/DD ordered by exit time). Risk = fixed fraction of the initial deposit."""
    if trades.empty:
        return {}
    order = ["fill_time_ms"] + (["oof_prob"] if "oof_prob" in trades else []) + ["symbol", "tf"]
    ascending = [True] + ([False] if "oof_prob" in trades else []) + [True, True]
    t = trades.sort_values(order, ascending=ascending)
    equity = float(deposit)
    open_positions: list[dict] = []
    realised: list[tuple[int, float, float]] = []
    n_seen = 0
    for row in t.itertuples(index=False):
        n_seen += 1
        now = int(row.fill_time_ms)
        closed = sorted((p for p in open_positions if p["exit_ms"] <= now), key=lambda p: p["exit_ms"])
        for position in closed:
            equity += position["pnl"]
            realised.append((position["exit_ms"], position["pnl"], position["net_r"]))
        open_positions = [p for p in open_positions if p["exit_ms"] > now]
        if equity <= 0 or len(open_positions) >= max_k:
            continue                                       # no slot -> skip setup
        if one_position_per_symbol and any(p["symbol"] == row.symbol for p in open_positions):
            continue
        cost_r = (cost_rate + slip_pct) / row.dist_stop if row.dist_stop > 0 else 0.0
        r = float(row.r_multiple) - cost_r
        risk_dollars = risk_pct * equity
        pnl = risk_dollars * r
        open_positions.append(dict(exit_ms=int(row.exit_ms), pnl=pnl, net_r=r, symbol=row.symbol))
    for position in sorted(open_positions, key=lambda p: p["exit_ms"]):
        equity += position["pnl"]
        realised.append((position["exit_ms"], position["pnl"], position["net_r"]))
    if not realised:
        return {}
    realised.sort()
    pnls = np.array([p for _, p, _ in realised])
    curve = deposit + np.concatenate([[0.0], np.cumsum(pnls)])
    peak = np.maximum.accumulate(curve)
    dd = (curve - peak) / peak
    net_r = np.array([r for _, _, r in realised])
    wins = net_r > 0
    def _streak(mask):
        best = cur = 0
        for m in mask:
            cur = cur + 1 if m else 0
            best = max(best, cur)
        return best
    gross_win = pnls[pnls > 0].sum()
    gross_loss = -pnls[pnls < 0].sum()
    order = np.argsort(pnls)
    top5 = pnls[order[-5:]].sum()
    return dict(
        n=len(pnls), taken_frac=len(pnls) / n_seen, final=curve[-1],
        ret_pct=(curve[-1] / deposit - 1) * 100, win_rate=wins.mean() * 100,
        expectancy_R=net_r.mean(), avg_win_R=net_r[wins].mean() if wins.any() else np.nan,
        avg_loss_R=net_r[~wins].mean() if (~wins).any() else np.nan,
        profit_factor=gross_win / gross_loss if gross_loss > 0 else np.inf,
        max_dd_pct=dd.min() * 100, max_win_streak=_streak(wins), max_loss_streak=_streak(~wins),
        top_win=pnls.max(), top_loss=pnls.min(),
        pct_profit_top5=top5 / pnls.sum() * 100 if pnls.sum() != 0 else np.nan,
        ret_ex_top5=((deposit + pnls.sum() - top5) / deposit - 1) * 100,
    )


def _fmt(name: str, m: dict) -> str:
    if not m:
        return f"{name:16s} (no trades)"
    return (f"{name:16s} n={m['n']:4d}  ret={m['ret_pct']:+7.1f}%  maxDD={m['max_dd_pct']:6.1f}%  "
            f"win={m['win_rate']:4.1f}%  E[R]={m['expectancy_R']:+.3f}  PF={m['profit_factor']:.2f}  "
            f"streakL={m['max_loss_streak']:2d}  topWin=${m['top_win']:.0f}  topLoss=${m['top_loss']:.0f}  "
            f"top5={m['pct_profit_top5']:.0f}%ofPnL  ex-top5={m['ret_ex_top5']:+.1f}%")


def _fmt_c(name: str, m: dict) -> str:
    if not m:
        return f"{name:22s} (no trades)"
    return (f"{name:22s} took={m['n']:4d}({m['taken_frac']*100:4.0f}%)  ret={m['ret_pct']:+7.1f}%  "
            f"maxDD={m['max_dd_pct']:6.1f}%  win={m['win_rate']:4.1f}%  E[R]={m['expectancy_R']:+.3f}  "
            f"PF={m['profit_factor']:.2f}  streakL={m['max_loss_streak']:2d}  top5={m['pct_profit_top5']:.0f}%")


def run_concurrency(labels_path: Path = RES / "labels.parquet", oof_path: Path | None = RES / "oof.parquet",
                    entry_tag: str = "break", slip_pct: float = 0.0050,
                    ks=(1, 3, 5, 10), oof_q: float = 0.0) -> None:
    """Deployable portfolio: cap simultaneous open positions at K. ``oof_q``>0
    pre-filters to model top-(1-oof_q) before applying the concurrency cap."""
    df = _merge(labels_path, oof_path)
    pre = ""
    if oof_q > 0 and "oof_prob" in df.columns and df["oof_prob"].notna().any():
        d = df[df["oof_prob"].notna()]
        df = d[d["oof_prob"] >= d["oof_prob"].quantile(oof_q)]
        pre = f", model top-{(1-oof_q)*100:.0f}%"
    print(f"\n----- CONCURRENCY-CAPPED ({entry_tag}{pre}, slip {slip_pct*1e4:.0f}bps), "
          f"{len(df)} candidate setups -----", flush=True)
    for k in ks:
        print(_fmt_c(f"max {k} open", _simulate_concurrent(df, k, slip_pct=slip_pct)), flush=True)


def run(labels_path: Path = RES / "labels.parquet", oof_path: Path | None = RES / "oof.parquet",
        entry_tag: str = "break", slip_pct: float = 0.0) -> None:
    df = _merge(labels_path, oof_path)
    print(f"\n===== IS ECONOMICS ({entry_tag}) — deposit ${DEPOSIT:.0f}, risk {RISK_PCT:.0%}/trade, "
          f"cost {COST_RATE*1e4:.0f}bps + slip {slip_pct*1e4:.0f}bps =====", flush=True)
    print(f"universe {len(df)} trades, {df['symbol'].nunique()} symbols, "
          f"median dist_stop {df['dist_stop'].median():.1%}", flush=True)
    print(_fmt("BLIND (all)", _simulate(df, slip_pct=slip_pct)), flush=True)
    if "oof_prob" in df.columns and df["oof_prob"].notna().any():
        d = df[df["oof_prob"].notna()]
        for q, name in [(0.50, "oof top50%"), (0.75, "oof top25%"), (0.90, "oof top10%")]:
            thr = d["oof_prob"].quantile(q)
            print(_fmt(name, _simulate(d[d["oof_prob"] >= thr].sort_values("entry_time_ms"), slip_pct=slip_pct)), flush=True)
    # by setup type
    for st in df["setup_type"].unique():
        print(_fmt(f"type={st[:10]}", _simulate(df[df["setup_type"] == st], slip_pct=slip_pct)), flush=True)
    # ONE-per-wave dedup: collapse setups on the same symbol within 24h to the first
    # (clustered setups on one pump are ~one bet, not many independent ones).
    dd = df.sort_values(["symbol", "entry_time_ms"]).copy()
    dd["gap"] = dd.groupby("symbol")["entry_time_ms"].diff()
    dd = dd[(dd["gap"].isna()) | (dd["gap"] > 24 * 3600_000)]
    print(_fmt("dedup 1/24h", _simulate(dd.sort_values("entry_time_ms"), slip_pct=slip_pct)), flush=True)


OOF_CLOSE = RES / "oof_close.parquet"

if __name__ == "__main__":
    run(RES / "labels.parquet", RES / "oof.parquet", "break, no slip", slip_pct=0.0)
    run(RES / "labels.parquet", RES / "oof.parquet", "break, +50bps slip", slip_pct=0.0050)
    run(RES / "labels.parquet", RES / "oof.parquet", "break, +150bps slip", slip_pct=0.0150)
    if (RES / "labels_close.parquet").exists():
        run(RES / "labels_close.parquet", OOF_CLOSE if OOF_CLOSE.exists() else None,
            "close, +50bps slip", slip_pct=0.0050)
    # --- deployable, concurrency-capped (the honest return) ---
    run_concurrency(RES / "labels_close.parquet", OOF_CLOSE, "close", slip_pct=0.0050, oof_q=0.0)
    run_concurrency(RES / "labels_close.parquet", OOF_CLOSE, "close", slip_pct=0.0050, oof_q=0.75)
