"""Build an auditable trader-facing summary from frozen hourly run artifacts.

This module does not rerun or tune the strategy.  It derives descriptive
statistics from existing daily equity curves and position ledgers, and uses
the frozen 1h bars only to attribute the registered 2025-10-10 shock day.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_RESULTS_ROOT = Path(".output/results/xsect_momentum")
DEFAULT_HOURLY_ROOT = Path(".output/market/binance_vision/um_futures/klines_1h")
START_EQUITY = 1_000.0
BLACK_SWAN_DAY = pd.Timestamp("2025-10-10", tz="UTC")


@dataclass(frozen=True, slots=True)
class RunSpec:
    run_id: str
    label: str
    status: str


RUNS = (
    RunSpec(
        "real_u100_k20_reb7_short_structural",
        "primary protected u100/k20/reb7",
        "registered primary",
    ),
    RunSpec(
        "real_u75_k20_reb3_short_structural",
        "best protected u75/k20/reb3",
        "exploratory IS winner; not approved",
    ),
    RunSpec(
        "real_u100_k20_reb7_no_stop",
        "primary unprotected u100/k20/reb7",
        "registered baseline",
    ),
)


def _safe_ratio(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if denominator else np.nan


def _session(timestamp: pd.Timestamp) -> str:
    hour = timestamp.hour
    if hour < 8:
        return "Asia 00-08 UTC"
    if hour < 16:
        return "Europe 08-16 UTC"
    return "US 16-24 UTC"


def _max_streak(values: pd.Series, positive: bool) -> int:
    target = values.gt(0) if positive else values.lt(0)
    groups = target.ne(target.shift()).cumsum()
    streaks = target.groupby(groups).sum()
    return int(streaks.max()) if len(streaks) else 0


def _max_drawdown_details(equity: pd.Series, start_equity: float) -> dict[str, object]:
    first_date = equity.index.min() - pd.Timedelta(days=1)
    curve = pd.concat([pd.Series([start_equity], index=[first_date]), equity]).sort_index()
    peaks = curve.cummax()
    drawdown = curve / peaks - 1.0
    trough = drawdown.idxmin()
    peak = curve.loc[:trough].idxmax()
    after = curve.loc[trough:]
    recovered = after[after >= curve.loc[peak]]
    recovery = recovered.index[0] if len(recovered) else pd.NaT

    underwater = drawdown.lt(0)
    groups = underwater.ne(underwater.shift()).cumsum()
    durations = underwater.groupby(groups).sum()
    max_underwater = int(durations.max()) if len(durations) else 0
    ulcer = float(np.sqrt(np.mean(np.square(drawdown.clip(upper=0.0)))))
    return {
        "max_drawdown": float(-drawdown.min()),
        "peak_date": peak,
        "trough_date": trough,
        "recovery_date": recovery,
        "recovered": bool(pd.notna(recovery)),
        "peak_to_trough_days": int((trough - peak).days),
        "max_underwater_days": max_underwater,
        "ending_drawdown": float(-drawdown.iloc[-1]),
        "ulcer_index": ulcer,
    }


def _prepare_trades(frame: pd.DataFrame) -> pd.DataFrame:
    trades = frame.copy()
    for column in ("rebalance_date", "entry_time", "exit_time"):
        trades[column] = pd.to_datetime(trades[column], utc=True)
    trades["side"] = np.where(trades["weight"] > 0.0, "long", "short")
    trades["entry_notional"] = (trades["weight"] * trades["equity_at_entry"]).abs()
    trades["position_net_return"] = trades["pnl_net"] / trades["entry_notional"]
    trades["position_gross_return"] = np.sign(trades["weight"]) * trades["seg_ret"]
    trades["holding_hours"] = (
        trades["exit_time"] - trades["entry_time"]
    ).dt.total_seconds() / 3600.0
    trades["entry_session"] = trades["entry_time"].map(_session)
    trades["exit_session"] = trades["exit_time"].map(_session)
    return trades


def _trade_stats(run: RunSpec, trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for side, group in [("all", trades), *list(trades.groupby("side"))]:
        winners = group.loc[group["pnl_net"] > 0.0]
        losers = group.loc[group["pnl_net"] < 0.0]
        gross_profit = float(winners["pnl_net"].sum())
        gross_loss = float(-losers["pnl_net"].sum())
        rows.append({
            "run_id": run.run_id,
            "label": run.label,
            "status": run.status,
            "side": side,
            "trades": len(group),
            "winners": len(winners),
            "losers": len(losers),
            "breakeven": int((group["pnl_net"] == 0.0).sum()),
            "win_rate": float((group["pnl_net"] > 0.0).mean()),
            "net_pnl": float(group["pnl_net"].sum()),
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "profit_factor": _safe_ratio(gross_profit, gross_loss),
            "avg_trade_pnl": float(group["pnl_net"].mean()),
            "avg_profit": float(winners["pnl_net"].mean()),
            "max_profit": float(group["pnl_net"].max()),
            "avg_loss": float(losers["pnl_net"].mean()),
            "max_loss": float(group["pnl_net"].min()),
            "avg_position_return": float(group["position_net_return"].mean()),
            "median_position_return": float(group["position_net_return"].median()),
            "avg_winner_return": float(winners["position_net_return"].mean()),
            "best_position_return": float(group["position_net_return"].max()),
            "avg_loser_return": float(losers["position_net_return"].mean()),
            "worst_position_return": float(group["position_net_return"].min()),
            "payoff_ratio": _safe_ratio(
                float(winners["position_net_return"].mean()),
                abs(float(losers["position_net_return"].mean())),
            ),
            "holding_hours_mean": float(group["holding_hours"].mean()),
            "holding_hours_median": float(group["holding_hours"].median()),
            "holding_hours_p90": float(group["holding_hours"].quantile(0.90)),
            "holding_hours_max": float(group["holding_hours"].max()),
            "costs": float(group["pnl_cost"].sum()),
            "median_abs_weight": float(group["weight"].abs().median()),
            "max_abs_weight": float(group["weight"].abs().max()),
        })
    return pd.DataFrame(rows)


def _streak_stats(run: RunSpec, daily: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for side, group in [("all", trades), *list(trades.groupby("side"))]:
        ordered = group.sort_values(["exit_time", "entry_time", "symbol"])
        rows.append({
            "run_id": run.run_id,
            "side": side,
            "sequence_unit": "individual_trade_symbol_tiebreak",
            "max_win_streak": _max_streak(ordered["pnl_net"], True),
            "max_loss_streak": _max_streak(ordered["pnl_net"], False),
        })
        cohorts = group.groupby("exit_time", sort=True)["pnl_net"].sum()
        rows.append({
            "run_id": run.run_id,
            "side": side,
            "sequence_unit": "exit_timestamp_cohort",
            "max_win_streak": _max_streak(cohorts, True),
            "max_loss_streak": _max_streak(cohorts, False),
        })
    returns = daily.sort_values("date")["daily_return"]
    rows.append({
        "run_id": run.run_id,
        "side": "portfolio",
        "sequence_unit": "calendar_day",
        "max_win_streak": _max_streak(returns, True),
        "max_loss_streak": _max_streak(returns, False),
    })
    return pd.DataFrame(rows)


def _concentration(run: RunSpec, trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for side, group in [("all", trades), *list(trades.groupby("side"))]:
        positive = float(group.loc[group["pnl_net"] > 0.0, "pnl_net"].sum())
        loss = float(-group.loc[group["pnl_net"] < 0.0, "pnl_net"].sum())
        net = float(group["pnl_net"].sum())
        for fraction in (0.01, 0.05, 0.10, 0.20, 0.40):
            count = max(1, int(np.ceil(len(group) * fraction)))
            best = group.nlargest(count, "pnl_net")["pnl_net"]
            worst = group.nsmallest(count, "pnl_net")["pnl_net"]
            rows.append({
                "run_id": run.run_id,
                "side": side,
                "fraction": fraction,
                "trade_count": count,
                "top_winner_share_of_gross_profit": _safe_ratio(float(best.clip(lower=0.0).sum()), positive),
                "net_after_removing_top_winners": net - float(best.sum()),
                "worst_loser_share_of_gross_loss": _safe_ratio(float(-worst.clip(upper=0.0).sum()), loss),
                "net_after_removing_worst_losers": net - float(worst.sum()),
            })
    return pd.DataFrame(rows)


def _period_series(daily: pd.DataFrame, frequency: str) -> pd.DataFrame:
    indexed = daily.set_index("date").sort_index()
    prior = indexed["equity"].shift(1)
    prior.iloc[0] = START_EQUITY
    base = pd.DataFrame({
        "return": indexed["daily_return"],
        "pnl": indexed["equity"] - prior,
    })
    if frequency == "day":
        out = base.copy()
    else:
        rule = "W-SUN" if frequency == "week" else "ME"
        out = base.resample(rule).agg(
            return_= ("return", lambda value: float((1.0 + value).prod() - 1.0)),
            pnl=("pnl", "sum"),
        ).rename(columns={"return_": "return"})
    out.index.name = "period_end"
    return out.reset_index()


def _period_stats(run: RunSpec, frequency: str, periods: pd.DataFrame) -> dict[str, object]:
    positive = periods.loc[periods["pnl"] > 0.0, "pnl"].sort_values(ascending=False)
    gross_positive = float(positive.sum())
    shares = positive / gross_positive if gross_positive else pd.Series(dtype=float)
    best = periods.loc[periods["return"].idxmax()]
    worst = periods.loc[periods["return"].idxmin()]
    return {
        "run_id": run.run_id,
        "frequency": frequency,
        "periods": len(periods),
        "positive_periods": int((periods["return"] > 0.0).sum()),
        "positive_share": float((periods["return"] > 0.0).mean()),
        "mean_return": float(periods["return"].mean()),
        "median_return": float(periods["return"].median()),
        "best_return": float(best["return"]),
        "best_period": best["period_end"],
        "worst_return": float(worst["return"]),
        "worst_period": worst["period_end"],
        "top1_share_of_positive_pnl": float(shares.head(1).sum()),
        "top3_share_of_positive_pnl": float(shares.head(3).sum()),
        "top5_share_of_positive_pnl": float(shares.head(5).sum()),
        "positive_pnl_hhi": float(np.square(shares).sum()),
    }


def _session_stats(run: RunSpec, trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for dimension in ("entry_session", "exit_session"):
        for (side, session), group in trades.groupby(["side", dimension]):
            rows.append({
                "run_id": run.run_id,
                "dimension": dimension,
                "side": side,
                "session": session,
                "trades": len(group),
                "win_rate": float((group["pnl_net"] > 0.0).mean()),
                "net_pnl": float(group["pnl_net"].sum()),
                "avg_position_return": float(group["position_net_return"].mean()),
                "avg_holding_hours": float(group["holding_hours"].mean()),
            })
    return pd.DataFrame(rows)


def _exit_reason_stats(run: RunSpec, trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for (side, reason), group in trades.groupby(["side", "exit_reason"]):
        rows.append({
            "run_id": run.run_id,
            "side": side,
            "exit_reason": reason,
            "trades": len(group),
            "win_rate": float((group["pnl_net"] > 0.0).mean()),
            "net_pnl": float(group["pnl_net"].sum()),
            "avg_position_return": float(group["position_net_return"].mean()),
            "avg_holding_hours": float(group["holding_hours"].mean()),
        })
    return pd.DataFrame(rows)


def _calendar_and_activity(run: RunSpec, daily: pd.DataFrame, trades: pd.DataFrame) -> dict[str, object]:
    entry_days = set(trades["entry_time"].dt.normalize())
    exit_days = set(trades["exit_time"].dt.normalize())
    active_days: set[pd.Timestamp] = set()
    for row in trades[["entry_time", "exit_time"]].itertuples(index=False):
        active_days.update(pd.date_range(row.entry_time.normalize(), row.exit_time.normalize(), freq="D"))
    return {
        "run_id": run.run_id,
        "market_days": len(daily),
        "nonzero_return_days": int(daily["daily_return"].ne(0.0).sum()),
        "entry_days": len(entry_days),
        "exit_days": len(exit_days),
        "entry_or_exit_days": len(entry_days | exit_days),
        "active_position_days": len(active_days),
    }


def _year_side_stats(run: RunSpec, trades: pd.DataFrame) -> pd.DataFrame:
    work = trades.assign(year=trades["exit_time"].dt.year)
    rows: list[dict[str, object]] = []
    for (year, side), group in work.groupby(["year", "side"]):
        rows.append({
            "run_id": run.run_id,
            "year": year,
            "side": side,
            "trades": len(group),
            "win_rate": float((group["pnl_net"] > 0.0).mean()),
            "net_pnl": float(group["pnl_net"].sum()),
            "avg_position_return": float(group["position_net_return"].mean()),
        })
    return pd.DataFrame(rows)


def _symbol_stats(run: RunSpec, trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for (side, symbol), group in trades.groupby(["side", "symbol"]):
        rows.append({
            "run_id": run.run_id,
            "side": side,
            "symbol": symbol,
            "trades": len(group),
            "win_rate": float((group["pnl_net"] > 0.0).mean()),
            "net_pnl": float(group["pnl_net"].sum()),
            "avg_position_return": float(group["position_net_return"].mean()),
        })
    return pd.DataFrame(rows)


def _risk_stress(run: RunSpec, daily: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for allocation in (0.02, 0.03, 0.04, 0.05):
        nominal_gross = 40.0 * allocation
        scaled = daily["daily_return"] * nominal_gross
        equity = START_EQUITY * (1.0 + scaled).cumprod()
        q05 = float(scaled.quantile(0.05))
        per_trade = allocation * trades["position_net_return"]
        rows.append({
            "run_id": run.run_id,
            "allocation_per_position": allocation,
            "nominal_gross_exposure": nominal_gross,
            "linear_stress_only": True,
            "total_return": float(equity.iloc[-1] / START_EQUITY - 1.0),
            "max_drawdown": _max_drawdown_details(equity.set_axis(daily["date"]), START_EQUITY)["max_drawdown"],
            "worst_day": float(scaled.min()),
            "daily_var_95": q05,
            "daily_cvar_95": float(scaled.loc[scaled <= q05].mean()),
            "avg_trade_deposit_change": float(per_trade.mean()),
            "avg_winner_deposit_change": float(per_trade.loc[per_trade > 0.0].mean()),
            "avg_loser_deposit_change": float(per_trade.loc[per_trade < 0.0].mean()),
            "best_trade_deposit_change": float(per_trade.max()),
            "worst_trade_deposit_change": float(per_trade.min()),
        })
    return pd.DataFrame(rows)


def _structural_risk_budget(run: RunSpec, trades: pd.DataFrame) -> pd.DataFrame:
    short = trades.loc[
        (trades["side"] == "short")
        & trades["anchor_price"].notna()
        & (trades["anchor_price"] > trades["entry_price"])
    ].copy()
    if short.empty:
        return pd.DataFrame()
    short["stop_distance"] = short["anchor_price"] / short["entry_price"] - 1.0
    short["actual_equity_risk"] = short["weight"].abs() * short["stop_distance"]
    rows: list[dict[str, object]] = []
    for budget in (0.02, 0.03, 0.04, 0.05):
        required = budget / short["stop_distance"]
        rows.append({
            "run_id": run.run_id,
            "risk_budget_per_short": budget,
            "eligible_short_trades": len(short),
            "median_stop_distance": float(short["stop_distance"].median()),
            "stop_distance_p90": float(short["stop_distance"].quantile(0.90)),
            "median_actual_equity_risk": float(short["actual_equity_risk"].median()),
            "actual_equity_risk_p90": float(short["actual_equity_risk"].quantile(0.90)),
            "median_required_position_weight": float(required.median()),
            "required_weight_p90": float(required.quantile(0.90)),
            "share_requiring_over_25pct_weight": float(required.gt(0.25).mean()),
            "share_requiring_over_100pct_weight": float(required.gt(1.0).mean()),
        })
    return pd.DataFrame(rows)


def _bar_prices(hourly_root: Path, symbol: str) -> pd.DataFrame:
    path = hourly_root / f"{symbol}.parquet"
    frame = pd.read_parquet(path, columns=["timestamp", "open", "close"])
    index = pd.to_datetime(frame.pop("timestamp"), unit="ms", utc=True)
    return frame.set_axis(index)


def _shock_attribution(
    run: RunSpec,
    trades: pd.DataFrame,
    hourly_root: Path,
    day: pd.Timestamp,
) -> pd.DataFrame:
    end = day + pd.Timedelta(days=1)
    overlap = trades.loc[(trades["entry_time"] < end) & (trades["exit_time"] >= day)].copy()
    bars = {symbol: _bar_prices(hourly_root, symbol) for symbol in overlap["symbol"].unique()}
    rows: list[dict[str, object]] = []
    for trade in overlap.itertuples(index=False):
        symbol_bars = bars[trade.symbol]
        if trade.entry_time >= day:
            start_price = float(trade.entry_price)
        else:
            start_price = float(symbol_bars.loc[day - pd.Timedelta(hours=1), "close"])
        if trade.exit_time < end:
            end_price = float(trade.exit_price)
        else:
            end_price = float(symbol_bars.loc[end - pd.Timedelta(hours=1), "close"])
        signed_notional = float(trade.weight * trade.equity_at_entry)
        gross_pnl = signed_notional * (end_price - start_price) / float(trade.entry_price)
        rows.append({
            "run_id": run.run_id,
            "day": day,
            "symbol": trade.symbol,
            "side": trade.side,
            "entry_time": trade.entry_time,
            "exit_time": trade.exit_time,
            "exit_reason": trade.exit_reason,
            "weight_at_entry": trade.weight,
            "start_price": start_price,
            "end_price": end_price,
            "asset_return_during_overlap": end_price / start_price - 1.0,
            "gross_pnl_attribution": gross_pnl,
        })
    return pd.DataFrame(rows)


def _october_summary(
    run: RunSpec,
    daily: pd.DataFrame,
    attribution: pd.DataFrame,
) -> dict[str, object]:
    indexed = daily.set_index("date").sort_index()
    october = indexed.loc[(indexed.index >= "2025-10-01") & (indexed.index < "2025-11-01")]
    prior_equity = float(indexed.loc[indexed.index < "2025-10-01", "equity"].iloc[-1])
    shock_equity_prior = float(indexed.loc[indexed.index < BLACK_SWAN_DAY, "equity"].iloc[-1])
    shock_pnl = float(indexed.loc[BLACK_SWAN_DAY, "equity"] - shock_equity_prior)
    by_side = attribution.groupby("side")["gross_pnl_attribution"].sum()
    intra_curve = pd.concat([
        pd.Series([prior_equity], index=[pd.Timestamp("2025-09-30", tz="UTC")]),
        october["equity"],
    ])
    return {
        "run_id": run.run_id,
        "october_return": float(october["equity"].iloc[-1] / prior_equity - 1.0),
        "october_pnl": float(october["equity"].iloc[-1] - prior_equity),
        "october_positive_day_share": float((october["daily_return"] > 0.0).mean()),
        "october_max_drawdown": _max_drawdown_details(intra_curve.iloc[1:], prior_equity)["max_drawdown"],
        "october_worst_day": october["daily_return"].idxmin(),
        "october_worst_return": float(october["daily_return"].min()),
        "october_best_day": october["daily_return"].idxmax(),
        "october_best_return": float(october["daily_return"].max()),
        "black_swan_day": BLACK_SWAN_DAY,
        "black_swan_return": float(indexed.loc[BLACK_SWAN_DAY, "daily_return"]),
        "black_swan_exact_pnl": shock_pnl,
        "black_swan_gross_attributed_pnl": float(attribution["gross_pnl_attribution"].sum()),
        "attribution_minus_exact_pnl": float(attribution["gross_pnl_attribution"].sum() - shock_pnl),
        "long_segments": int((attribution["side"] == "long").sum()),
        "short_segments": int((attribution["side"] == "short").sum()),
        "long_gross_pnl": float(by_side.get("long", 0.0)),
        "short_gross_pnl": float(by_side.get("short", 0.0)),
    }


def _write_markdown(
    out_dir: Path,
    overall: pd.DataFrame,
    trades: pd.DataFrame,
    october: pd.DataFrame,
    concentration: pd.DataFrame,
) -> None:
    lines = [
        "# Trader summary v1",
        "",
        "Frozen descriptive report for 2023-02-19 through 2025-12-31. The u75/reb3 run is exploratory IS selection, not an approved strategy.",
        "",
        "## Portfolio overview",
        "",
    ]
    for row in overall.itertuples(index=False):
        lines.append(
            f"- `{row.run_id}`: return {row.total_return:.1%}, final equity {row.final_equity:.2f}, "
            f"max DD {row.max_drawdown:.1%}, worst day {row.worst_day:.1%}, Sharpe {row.sharpe:.2f}."
        )
    lines.extend(["", "## Trades by side", ""])
    for row in trades.itertuples(index=False):
        if row.side == "all":
            continue
        lines.append(
            f"- `{row.run_id}` / {row.side}: {row.trades} trades, win rate {row.win_rate:.1%}, "
            f"net PnL {row.net_pnl:.2f}, PF {row.profit_factor:.2f}, average win {row.avg_winner_return:.2%}, "
            f"average loss {row.avg_loser_return:.2%}, worst {row.worst_position_return:.2%}, "
            f"median hold {row.holding_hours_median:.0f}h."
        )
    lines.extend(["", "## 2025-10-10 shock", ""])
    for row in october.itertuples(index=False):
        lines.append(
            f"- `{row.run_id}`: exact day {row.black_swan_return:.1%}; long gross {row.long_gross_pnl:.2f} "
            f"across {row.long_segments} segments, short gross {row.short_gross_pnl:.2f} across "
            f"{row.short_segments} segments; October {row.october_return:.1%}."
        )
    lines.extend(["", "## Profit concentration", ""])
    selected = concentration.loc[
        (concentration["side"] == "all") & concentration["fraction"].isin([0.01, 0.10, 0.40])
    ]
    for row in selected.itertuples(index=False):
        lines.append(
            f"- `{row.run_id}` top {row.fraction:.0%}: {row.top_winner_share_of_gross_profit:.1%} of gross "
            f"profits; net after removing them {row.net_after_removing_top_winners:.2f}."
        )
    lines.extend([
        "",
        "## Interpretation limits",
        "",
        "- The 2-5% allocation table is a linear exposure stress, not a fresh backtest.",
        "- Risk-to-stop sizing is reported separately and only for shorts with a valid structural anchor.",
        "- Trade PnL is segment PnL. A symbol can reappear after a scheduled rebalance.",
        "- Session labels are UTC: Asia 00-08, Europe 08-16, US 16-24.",
    ])
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_report(results_root: Path, hourly_root: Path, out_dir: Path) -> None:
    runs_root = results_root / "hourly_structural_runs_v2"
    summary = pd.read_parquet(results_root / "hourly_structural_summary_v2.parquet")
    selected = summary.loc[summary["run_id"].isin([run.run_id for run in RUNS])].copy()
    selected["label"] = selected["run_id"].map({run.run_id: run.label for run in RUNS})
    selected["status"] = selected["run_id"].map({run.run_id: run.status for run in RUNS})
    overall = selected[[
        "run_id", "label", "status", "final_equity", "total_return", "sharpe", "n_trades",
        "g6_win_rate", "g7_max_drawdown", "g8_worst_day", "worst_week", "cvar_5",
        "ret_2023", "ret_2024", "ret_2025", "gates", "all_gates_pass",
    ]].rename(columns={
        "g6_win_rate": "win_rate", "g7_max_drawdown": "max_drawdown", "g8_worst_day": "worst_day",
    })

    tables: dict[str, list[pd.DataFrame] | list[dict[str, object]]] = {
        "trade_stats_by_side": [], "streaks": [], "top_concentration": [],
        "period_returns": [], "period_stats": [], "session_stats": [],
        "session_matrix": [], "exit_reason_stats": [], "activity_stats": [],
        "drawdown_stats": [], "year_side_stats": [], "symbol_stats": [],
        "allocation_stress_2_5pct": [], "structural_stop_risk_2_5pct": [],
        "october_2025": [], "october_2025_attribution": [],
    }
    source_hashes: dict[str, str] = {}
    for run in RUNS:
        daily_path = runs_root / f"{run.run_id}_daily.parquet"
        trades_path = runs_root / f"{run.run_id}_trades.parquet"
        daily = pd.read_parquet(daily_path)
        daily["date"] = pd.to_datetime(daily["date"], utc=True)
        trades = _prepare_trades(pd.read_parquet(trades_path))
        source_hashes[str(daily_path)] = hashlib.sha256(daily_path.read_bytes()).hexdigest()
        source_hashes[str(trades_path)] = hashlib.sha256(trades_path.read_bytes()).hexdigest()

        tables["trade_stats_by_side"].append(_trade_stats(run, trades))
        tables["streaks"].append(_streak_stats(run, daily, trades))
        tables["top_concentration"].append(_concentration(run, trades))
        tables["session_stats"].append(_session_stats(run, trades))
        matrix = trades.pivot_table(
            index="entry_session", columns="exit_session", values="pnl_net", aggfunc=["count", "sum"], fill_value=0.0,
        )
        matrix.columns = [f"{metric}_{session}" for metric, session in matrix.columns]
        matrix = matrix.reset_index().assign(run_id=run.run_id)
        tables["session_matrix"].append(matrix)
        tables["exit_reason_stats"].append(_exit_reason_stats(run, trades))
        tables["activity_stats"].append(_calendar_and_activity(run, daily, trades))
        dd = _max_drawdown_details(daily.set_index("date")["equity"], START_EQUITY)
        tables["drawdown_stats"].append({"run_id": run.run_id, **dd})
        tables["year_side_stats"].append(_year_side_stats(run, trades))
        tables["symbol_stats"].append(_symbol_stats(run, trades))
        tables["allocation_stress_2_5pct"].append(_risk_stress(run, daily, trades))
        structural = _structural_risk_budget(run, trades)
        if not structural.empty:
            tables["structural_stop_risk_2_5pct"].append(structural)
        for frequency in ("day", "week", "month"):
            periods = _period_series(daily, frequency).assign(run_id=run.run_id, frequency=frequency)
            tables["period_returns"].append(periods)
            tables["period_stats"].append(_period_stats(run, frequency, periods))
        attribution = _shock_attribution(run, trades, hourly_root, BLACK_SWAN_DAY)
        tables["october_2025_attribution"].append(attribution)
        tables["october_2025"].append(_october_summary(run, daily, attribution))

    out_dir.mkdir(parents=True, exist_ok=True)
    overall.to_csv(out_dir / "portfolio_overview.csv", index=False)
    materialized: dict[str, pd.DataFrame] = {}
    for name, parts in tables.items():
        if not parts:
            continue
        if isinstance(parts[0], pd.DataFrame):
            frame = pd.concat(parts, ignore_index=True)
        else:
            frame = pd.DataFrame(parts)
        frame.to_csv(out_dir / f"{name}.csv", index=False)
        materialized[name] = frame

    metadata = {
        "report_version": "trader_summary_v1",
        "descriptive_only": True,
        "is_start": str(selected["is_start"].min()),
        "is_end": str(selected["is_end"].max()),
        "black_swan_day": BLACK_SWAN_DAY.isoformat(),
        "run_status": {run.run_id: run.status for run in RUNS},
        "source_sha256": source_hashes,
        "hourly_source_file_count": len(list(hourly_root.glob("*.parquet"))),
        "session_definition": {
            "asia": "00:00-07:59 UTC", "europe": "08:00-15:59 UTC", "us": "16:00-23:59 UTC",
        },
    }
    (out_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8",
    )
    _write_markdown(
        out_dir,
        overall,
        materialized["trade_stats_by_side"],
        materialized["october_2025"],
        materialized["top_concentration"],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--hourly-root", type=Path, default=DEFAULT_HOURLY_ROOT)
    parser.add_argument(
        "--out-dir", type=Path, default=DEFAULT_RESULTS_ROOT / "trader_summary_v1",
    )
    args = parser.parse_args()
    build_report(args.results_root, args.hourly_root, args.out_dir)
    print(f"Trader summary written to {args.out_dir}", flush=True)


if __name__ == "__main__":
    main()
