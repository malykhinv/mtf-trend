from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import pandas as pd

from .contracts import ResearchConfig, SimulationConfig
from .features import build_feature_matrix, model_feature_columns
from .learning import (
    PredictabilityAudit,
    RegressorFactory,
    build_learning_dataset,
    build_negative_control,
    evaluate_predictability,
    fit_predict_weekly,
)
from .metrics import (
    AdmissionDecision,
    PerformanceReport,
    calculate_performance,
    evaluate_baseline_admission,
    evaluate_overlay_admission,
    select_final_strategy,
)
from .portfolio import build_target_weights
from anomaly_science.strategy.spot_trend.simulation import SimulationResult, simulate_spot_portfolio
from .trend import build_trend_state
from .universe import PointInTimeUniverse, build_point_in_time_universe


RunKey = tuple[str, int, float]


@dataclass(frozen=True, slots=True)
class SpotTrendResearchRun:
    config: ResearchConfig
    universe: PointInTimeUniverse
    trend_state: pd.DataFrame
    features: pd.DataFrame
    learning_dataset: pd.DataFrame
    forecasts: pd.DataFrame
    negative_control_forecasts: pd.DataFrame
    predictability: PredictabilityAudit
    targets: dict[str, pd.DataFrame]
    simulations: dict[RunKey, SimulationResult]
    reports: dict[RunKey, PerformanceReport]
    horizon_sensitivity_reports: tuple[PerformanceReport, ...]
    target_horizon_robustness: dict[int, tuple[PerformanceReport, PerformanceReport]]
    baseline_admission: AdmissionDecision
    overlay_admission: AdmissionDecision | None
    selected_variant: str


def donchian_horizon_perturbations(horizons: tuple[int, ...]) -> tuple[tuple[int, ...], ...]:
    """Perturb one registered horizon at a time by exactly +/-20%."""

    variants: list[tuple[int, ...]] = []
    for index, horizon in enumerate(horizons):
        for multiplier in (0.8, 1.2):
            changed = list(horizons)
            changed[index] = max(1, int(round(horizon * multiplier)))
            if len(set(changed)) != len(changed):
                continue
            variants.append(tuple(sorted(changed)))
    return tuple(variants)


def _fit_forecasts(
    dataset: pd.DataFrame,
    prediction_start: pd.Timestamp | str,
    prediction_end: pd.Timestamp | str,
    config,
    factory: RegressorFactory | None,
) -> pd.DataFrame:
    kwargs = {
        "prediction_start": prediction_start,
        "prediction_end": prediction_end,
        "config": config,
        "feature_columns": model_feature_columns(dataset),
    }
    if factory is not None:
        kwargs["regressor_factory"] = factory
    return fit_predict_weekly(dataset, **kwargs)


def run_spot_trend_research(
    daily_bars: pd.DataFrame,
    symbol_master: pd.DataFrame,
    *,
    prediction_start: pd.Timestamp | str,
    prediction_end: pd.Timestamp | str,
    config: ResearchConfig = ResearchConfig(),
    regressor_factory: RegressorFactory | None = None,
    run_horizon_sensitivity: bool = True,
    run_target_horizon_robustness: bool = True,
) -> SpotTrendResearchRun:
    """Run the frozen A/B/C protocol without selecting on final OOS results."""

    universe = build_point_in_time_universe(daily_bars, symbol_master, config.universe)
    trend = build_trend_state(daily_bars, config.portfolio.horizons)
    features = build_feature_matrix(daily_bars, universe, trend, config.portfolio.horizons)
    dataset = build_learning_dataset(
        features,
        daily_bars,
        horizon_days=config.model.target_horizon_days,
    )
    forecasts = _fit_forecasts(
        dataset, prediction_start, prediction_end, config.model, regressor_factory
    )
    negative = build_negative_control(forecasts, seed=config.negative_control_seed)
    predictability = evaluate_predictability(forecasts, dataset, config.admission)
    targets = {
        "baseline": build_target_weights(
            features,
            daily_bars,
            symbol_master,
            variant="baseline",
            daily_membership=universe.daily_membership,
            portfolio=config.portfolio,
            universe=config.universe,
        ),
        "overlay": build_target_weights(
            features,
            daily_bars,
            symbol_master,
            variant="overlay",
            forecasts=forecasts,
            daily_membership=universe.daily_membership,
            portfolio=config.portfolio,
            universe=config.universe,
        ),
        "negative_control": build_target_weights(
            features,
            daily_bars,
            symbol_master,
            variant="negative_control",
            forecasts=negative,
            daily_membership=universe.daily_membership,
            portfolio=config.portfolio,
            universe=config.universe,
        ),
    }
    simulations: dict[RunKey, SimulationResult] = {}
    reports: dict[RunKey, PerformanceReport] = {}
    for variant, target in targets.items():
        for cost_bps in (10, 25, 50):
            for participation_cap in (0.001, 0.0005):
                key = (variant, cost_bps, participation_cap)
                simulations[key] = simulate_spot_portfolio(
                    daily_bars,
                    symbol_master,
                    target,
                    simulation=SimulationConfig(
                        one_way_cost_bps=cost_bps,
                        participation_cap=participation_cap,
                    ),
                    portfolio=config.portfolio,
                    evaluation_start=prediction_start,
                    evaluation_end=prediction_end,
                )
                reports[key] = calculate_performance(simulations[key])

    sensitivity_reports: list[PerformanceReport] = []
    if run_horizon_sensitivity:
        for horizons in donchian_horizon_perturbations(config.portfolio.horizons):
            changed_portfolio = replace(config.portfolio, horizons=horizons)
            changed_trend = build_trend_state(daily_bars, horizons)
            changed_features = build_feature_matrix(daily_bars, universe, changed_trend, horizons)
            changed_target = build_target_weights(
                changed_features,
                daily_bars,
                symbol_master,
                variant="baseline",
                daily_membership=universe.daily_membership,
                portfolio=changed_portfolio,
                universe=config.universe,
            )
            changed_result = simulate_spot_portfolio(
                daily_bars,
                symbol_master,
                changed_target,
                simulation=SimulationConfig(one_way_cost_bps=25, participation_cap=0.001),
                portfolio=changed_portfolio,
                evaluation_start=prediction_start,
                evaluation_end=prediction_end,
            )
            sensitivity_reports.append(calculate_performance(changed_result))

    target_robustness: dict[int, tuple[PerformanceReport, PerformanceReport]] = {}
    if run_target_horizon_robustness:
        baseline_main = reports[("baseline", 25, 0.001)]
        for horizon in (15, 25):
            model_config = replace(
                config.model,
                target_horizon_days=horizon,
                purge_days=max(config.model.purge_days, horizon),
            )
            alternate_dataset = build_learning_dataset(features, daily_bars, horizon_days=horizon)
            alternate_forecasts = _fit_forecasts(
                alternate_dataset,
                prediction_start,
                prediction_end,
                model_config,
                regressor_factory,
            )
            alternate_target = build_target_weights(
                features,
                daily_bars,
                symbol_master,
                variant="overlay",
                forecasts=alternate_forecasts,
                daily_membership=universe.daily_membership,
                portfolio=config.portfolio,
                universe=config.universe,
            )
            alternate_result = simulate_spot_portfolio(
                daily_bars,
                symbol_master,
                alternate_target,
                simulation=SimulationConfig(one_way_cost_bps=25, participation_cap=0.001),
                portfolio=config.portfolio,
                evaluation_start=prediction_start,
                evaluation_end=prediction_end,
            )
            target_robustness[horizon] = (baseline_main, calculate_performance(alternate_result))

    baseline_cost_reports = {cost: reports[("baseline", cost, 0.001)] for cost in (10, 25, 50)}
    baseline_admission = evaluate_baseline_admission(
        baseline_cost_reports,
        tuple(sensitivity_reports),
        config.admission,
    )
    overlay_admission: AdmissionDecision | None = None
    if baseline_admission.accepted:
        overlay_admission = evaluate_overlay_admission(
            baseline_reports=baseline_cost_reports,
            overlay_reports={cost: reports[("overlay", cost, 0.001)] for cost in (10, 25, 50)},
            negative_control_report=reports[("negative_control", 25, 0.001)],
            robustness_by_horizon=target_robustness,
            predictability=predictability,
            config=config.admission,
        )
    selected = select_final_strategy(baseline_admission, overlay_admission)
    return SpotTrendResearchRun(
        config=config,
        universe=universe,
        trend_state=trend,
        features=features,
        learning_dataset=dataset,
        forecasts=forecasts,
        negative_control_forecasts=negative,
        predictability=predictability,
        targets=targets,
        simulations=simulations,
        reports=reports,
        horizon_sensitivity_reports=tuple(sensitivity_reports),
        target_horizon_robustness=target_robustness,
        baseline_admission=baseline_admission,
        overlay_admission=overlay_admission,
        selected_variant=selected,
    )


def write_research_artifacts(run: SpotTrendResearchRun, output_dir: Path) -> None:
    """Persist auditable inputs, OOS forecasts, executions and decisions."""

    output_dir.mkdir(parents=True, exist_ok=True)
    run.universe.snapshots.to_parquet(output_dir / "universe_snapshots.parquet", index=False)
    run.universe.daily_membership.to_parquet(output_dir / "universe_daily_membership.parquet", index=False)
    run.universe.audit.to_parquet(output_dir / "universe_audit.parquet", index=False)
    run.trend_state.to_parquet(output_dir / "trend_state.parquet", index=False)
    run.features.to_parquet(output_dir / "feature_matrix.parquet", index=False)
    run.learning_dataset.to_parquet(output_dir / "learning_dataset.parquet", index=False)
    run.forecasts.to_parquet(output_dir / "weekly_oos_forecasts.parquet", index=False)
    run.negative_control_forecasts.to_parquet(output_dir / "negative_control_forecasts.parquet", index=False)
    for variant, frame in run.targets.items():
        frame.to_parquet(output_dir / f"targets_{variant}.parquet", index=False)
    report_payload: dict[str, object] = {}
    for (variant, cost, participation), report in run.reports.items():
        label = f"{variant}__cost_{cost}bps__participation_{participation}"
        report_payload[label] = report.metrics
        simulation = run.simulations[(variant, cost, participation)]
        simulation.daily.to_parquet(output_dir / f"daily_{label}.parquet", index=False)
        simulation.fills.to_parquet(output_dir / f"fills_{label}.parquet", index=False)
        simulation.episodes.to_parquet(output_dir / f"episodes_{label}.parquet", index=False)
    manifest = {
        "strategy_version": run.config.strategy_version,
        "feature_schema_version": run.config.feature_schema_version,
        "holdout_status": run.config.holdout.candidate_status,
        "holdout": asdict(run.config.holdout),
        "model": asdict(run.config.model),
        "predictability": asdict(run.predictability),
        "baseline_admission": asdict(run.baseline_admission),
        "overlay_admission": asdict(run.overlay_admission) if run.overlay_admission else None,
        "selected_variant": run.selected_variant,
        "reports": report_payload,
    }
    (output_dir / "research_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
