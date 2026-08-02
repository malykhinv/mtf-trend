from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .contracts import AdmissionConfig
from .learning import PredictabilityAudit
from anomaly_science.strategy.spot_trend.simulation import SimulationResult


@dataclass(frozen=True, slots=True)
class PerformanceReport:
    metrics: dict[str, float | int]
    halfyear_returns: pd.DataFrame
    symbol_profit: pd.DataFrame
    year_profit: pd.DataFrame


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    accepted: bool
    selected_variant: str
    checks: dict[str, bool]
    failures: tuple[str, ...]


def _maximum_drawdown(nav: pd.Series) -> float:
    running_peak = nav.cummax()
    drawdown = nav / running_peak - 1.0
    return float(-drawdown.min()) if len(drawdown) else np.nan


def _period_returns(daily: pd.DataFrame, period: str) -> pd.DataFrame:
    frame = daily.copy()
    if period == "halfyear":
        frame["period"] = frame["date"].dt.year.astype(str) + "H" + np.where(frame["date"].dt.month <= 6, "1", "2")
    elif period == "year":
        frame["period"] = frame["date"].dt.year.astype(str)
    else:
        raise ValueError(period)
    rows = []
    for label, group in frame.groupby("period", sort=True):
        valid = group["net_return"].dropna()
        rows.append({period: label, "net_return": float(np.prod(1.0 + valid) - 1.0) if len(valid) else np.nan})
    return pd.DataFrame(rows)


def calculate_performance(result: SimulationResult) -> PerformanceReport:
    daily = result.daily.sort_values("date", kind="stable").copy()
    if daily.empty:
        return PerformanceReport({}, pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
    returns = daily["net_return"].dropna()
    elapsed_days = max((daily["date"].iloc[-1] - daily["date"].iloc[0]).days, 1)
    years = elapsed_days / 365.0
    initial_nav = float(daily["nav_before_return"].iloc[0]) if "nav_before_return" in daily else float(daily["nav"].iloc[0])
    total_return = float(daily["nav"].iloc[-1] / initial_nav - 1.0)
    cagr = float((daily["nav"].iloc[-1] / initial_nav) ** (1.0 / years) - 1.0) if years > 0 else np.nan
    volatility = float(returns.std(ddof=1) * np.sqrt(365.0)) if len(returns) >= 2 else np.nan
    sharpe = float(returns.mean() / returns.std(ddof=1) * np.sqrt(365.0)) if len(returns) >= 2 and returns.std(ddof=1) > 0 else np.nan
    downside = returns.loc[returns < 0]
    sortino = float(returns.mean() / downside.std(ddof=1) * np.sqrt(365.0)) if len(downside) >= 2 and downside.std(ddof=1) > 0 else np.nan
    max_drawdown = _maximum_drawdown(daily["nav"])
    calmar = cagr / max_drawdown if max_drawdown > 0 else np.nan
    fills = result.fills.copy()
    episodes = result.episodes.copy()
    annual_turnover = (
        float(fills["notional"].sum() / daily["nav"].mean() / years) if not fills.empty and years > 0 else 0.0
    )
    wins = episodes.loc[episodes["net_pnl"] > 0, "net_pnl"] if not episodes.empty else pd.Series(dtype=float)
    losses = episodes.loc[episodes["net_pnl"] < 0, "net_pnl"] if not episodes.empty else pd.Series(dtype=float)
    gross_profit = float(wins.sum())
    gross_loss = float(-losses.sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (np.inf if gross_profit > 0 else np.nan)
    top_three_trade_share = float(wins.nlargest(3).sum() / gross_profit) if gross_profit > 0 else np.nan
    if episodes.empty:
        symbol_profit = pd.DataFrame(columns=["symbol", "net_pnl"])
    else:
        symbol_profit = episodes.groupby("symbol", as_index=False)["net_pnl"].sum().sort_values("net_pnl", ascending=False)
    positive_symbol_profit = symbol_profit.loc[symbol_profit["net_pnl"] > 0, "net_pnl"]
    total_positive_symbol_profit = float(positive_symbol_profit.sum())
    top_three_symbol_share = (
        float(positive_symbol_profit.nlargest(3).sum() / total_positive_symbol_profit)
        if total_positive_symbol_profit > 0
        else np.nan
    )
    halfyear = _period_returns(daily, "halfyear")
    if episodes.empty:
        years_frame = pd.DataFrame(columns=["year", "net_pnl"])
    else:
        episode_years = episodes.copy()
        episode_years["year"] = episode_years["exit_date"].dt.year.astype(str)
        years_frame = episode_years.groupby("year", as_index=False)["net_pnl"].sum().sort_values("year")
    total_closed_profit = float(episodes["net_pnl"].sum()) if not episodes.empty else 0.0
    best_coin_profit = float(symbol_profit["net_pnl"].max()) if not symbol_profit.empty else 0.0
    best_year_profit = float(years_frame["net_pnl"].max()) if not years_frame.empty else 0.0
    metrics: dict[str, float | int] = {
        "total_return": total_return,
        "cagr": cagr,
        "annualized_volatility": volatility,
        "sharpe": sharpe,
        "sortino": sortino,
        "maximum_drawdown": max_drawdown,
        "calmar": calmar,
        "average_exposure": float(daily["gross_exposure"].mean()),
        "maximum_exposure": float(daily["gross_exposure"].max()),
        "annual_turnover": annual_turnover,
        "trade_count": int(len(fills)),
        "average_position_duration_days": float(episodes["duration_days"].mean()) if not episodes.empty else np.nan,
        "win_rate": float((episodes["net_pnl"] > 0).mean()) if not episodes.empty else np.nan,
        "profit_factor": profit_factor,
        "top_three_trade_profit_share": top_three_trade_share,
        "top_three_symbol_profit_share": top_three_symbol_share,
        "delisting_losses": float(daily["delisting_loss"].sum()),
        "average_participation": float(fills["participation"].mean()) if not fills.empty else np.nan,
        "partial_fill_count": int(fills["partial_fill"].sum()) if not fills.empty else 0,
        "average_usdt_weight": float(daily["cash_weight"].mean()),
        "full_usdt_day_share": float((daily["cash_weight"] >= 1.0 - 1e-12).mean()),
        "missing_return_day_count": int(daily["net_return"].isna().sum()),
        "closed_episode_profit": total_closed_profit,
        "profit_after_removing_best_coin": total_closed_profit - best_coin_profit,
        "profit_after_removing_best_year": total_closed_profit - best_year_profit,
    }
    return PerformanceReport(metrics, halfyear, symbol_profit.reset_index(drop=True), years_frame)


def _positive_halfyears(report: PerformanceReport, count: int) -> int:
    values = report.halfyear_returns.tail(count)["net_return"]
    return int((values > 0).sum())


def evaluate_baseline_admission(
    cost_reports: dict[int, PerformanceReport],
    horizon_sensitivity_reports: tuple[PerformanceReport, ...],
    config: AdmissionConfig = AdmissionConfig(),
) -> AdmissionDecision:
    main = cost_reports[25]
    stress = cost_reports[50]
    metrics = main.metrics
    checks = {
        "sharpe": float(metrics.get("sharpe", np.nan)) > config.baseline_min_sharpe,
        "maximum_drawdown": float(metrics.get("maximum_drawdown", np.nan)) < config.baseline_max_drawdown,
        "positive_halfyears": _positive_halfyears(main, config.evaluated_halfyears) >= config.required_positive_halfyears,
        "trade_concentration": float(metrics.get("top_three_trade_profit_share", np.inf)) < config.maximum_top_three_trade_profit_share,
        "positive_at_50bps": float(stress.metrics.get("total_return", np.nan)) > 0,
        "horizon_sensitivity": bool(horizon_sensitivity_reports)
        and all(float(report.metrics.get("total_return", np.nan)) > 0 for report in horizon_sensitivity_reports),
        "not_one_coin": float(metrics.get("profit_after_removing_best_coin", -np.inf)) > 0,
        "not_one_year": float(metrics.get("profit_after_removing_best_year", -np.inf)) > 0,
    }
    failures = tuple(name for name, passed in checks.items() if not passed)
    return AdmissionDecision(not failures, "baseline" if not failures else "rejected", checks, failures)


def evaluate_overlay_admission(
    *,
    baseline_reports: dict[int, PerformanceReport],
    overlay_reports: dict[int, PerformanceReport],
    negative_control_report: PerformanceReport,
    robustness_by_horizon: dict[int, tuple[PerformanceReport, PerformanceReport]],
    predictability: PredictabilityAudit,
    config: AdmissionConfig = AdmissionConfig(),
) -> AdmissionDecision:
    baseline_main = baseline_reports[25]
    overlay_main = overlay_reports[25]
    halfyears = baseline_main.halfyear_returns.merge(
        overlay_main.halfyear_returns, on="halfyear", suffixes=("_base", "_overlay")
    ).tail(config.evaluated_halfyears)
    improved_halfyears = int((halfyears["net_return_overlay"] > halfyears["net_return_base"]).sum())
    checks = {
        "predictability_gate": predictability.passed,
        "sharpe_improvement_25bps": float(overlay_reports[25].metrics["sharpe"]) - float(baseline_reports[25].metrics["sharpe"]) >= config.overlay_min_sharpe_improvement,
        "sharpe_improvement_50bps": float(overlay_reports[50].metrics["sharpe"]) - float(baseline_reports[50].metrics["sharpe"]) >= config.overlay_min_sharpe_improvement,
        "drawdown": float(overlay_main.metrics["maximum_drawdown"]) <= float(baseline_main.metrics["maximum_drawdown"]) + config.overlay_max_drawdown_degradation,
        "turnover": float(overlay_main.metrics["annual_turnover"]) <= float(baseline_main.metrics["annual_turnover"]) * config.overlay_max_turnover_ratio,
        "halfyear_improvement": improved_halfyears >= config.required_positive_halfyears,
        "negative_control": float(overlay_main.metrics["sharpe"]) - float(negative_control_report.metrics["sharpe"]) >= config.negative_control_min_sharpe_advantage,
        "target_horizon_15_and_25": set(robustness_by_horizon) == {15, 25}
        and all(float(overlay.metrics["sharpe"]) > float(baseline.metrics["sharpe"]) for baseline, overlay in robustness_by_horizon.values()),
    }
    failures = tuple(name for name, passed in checks.items() if not passed)
    return AdmissionDecision(not failures, "overlay" if not failures else "baseline", checks, failures)


def select_final_strategy(
    baseline: AdmissionDecision,
    overlay: AdmissionDecision | None,
) -> str:
    if not baseline.accepted:
        return "rejected"
    if overlay is not None and overlay.accepted:
        return "overlay"
    return "baseline"
