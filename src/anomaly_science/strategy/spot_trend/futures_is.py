from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

from .contracts import ResearchConfig, SpotTrendContractError
from .features import build_feature_matrix
from .futures_breakout import BreakoutFlowConfig, build_breakout_flow_features
from .futures_data import IS_END, IS_START, OOS_START, build_data_inferred_futures_master
from .futures_features import (
    FUTURES_FEATURE_SCHEMA_VERSION,
    build_futures_feature_matrix,
    futures_feature_catalog,
)
from .learning import build_learning_dataset
from .trend import build_trend_state
from .universe import PointInTimeUniverse, build_point_in_time_universe


FUTURES_IS_HORIZONS: tuple[int, ...] = (3, 5, 7, 10, 14, 20, 30, 60, 90)


def futures_is_research_config() -> ResearchConfig:
    """Short-history specification preregistered for the local calendar-2025 IS."""

    base = ResearchConfig()
    return replace(
        base,
        universe=replace(base.universe, minimum_history_bars=90),
        portfolio=replace(base.portfolio, horizons=FUTURES_IS_HORIZONS),
        strategy_version="binance_usdm_trend_is_2025_v1",
    )


@dataclass(frozen=True, slots=True)
class FuturesISConfig:
    analysis_start: str = IS_START.isoformat()
    analysis_end: str = IS_END.isoformat()
    target_horizon_days: int = 20
    minimum_population_rows: int = 1_000
    bootstrap_samples: int = 1_000
    bootstrap_seed: int = 20_250_319
    negative_control_seed: int = 20_250_320
    fdr_alpha: float = 0.10

    def __post_init__(self) -> None:
        if pd.Timestamp(self.analysis_start).date() != IS_START or pd.Timestamp(self.analysis_end).date() != IS_END:
            raise SpotTrendContractError(f"futures IS is frozen to calendar year {IS_START.year}")
        if self.bootstrap_samples < 100:
            raise SpotTrendContractError("bootstrap_samples must be >= 100")


@dataclass(frozen=True, slots=True)
class FuturesISResult:
    config: FuturesISConfig
    universe: PointInTimeUniverse
    futures_master: pd.DataFrame
    breakout_events: pd.DataFrame
    features: pd.DataFrame
    learning_dataset: pd.DataFrame
    feature_audit: pd.DataFrame
    family_audit: pd.DataFrame
    redundancy_audit: pd.DataFrame
    signal_ev_audit: pd.DataFrame
    breakout_anomaly_audit: pd.DataFrame
    data_audit: dict[str, object]


def _daily_spearman(frame: pd.DataFrame, feature: str, target: str) -> pd.Series:
    values: dict[pd.Timestamp, float] = {}
    for day, group in frame[["date", feature, target]].dropna().groupby("date", sort=True):
        if len(group) < 5 or group[feature].nunique() < 3 or group[target].nunique() < 3:
            continue
        values[day] = float(group[feature].corr(group[target], method="spearman"))
    return pd.Series(values, dtype=float, name="rank_ic")


def _month_block_pvalue(
    daily_ic: pd.Series,
    *,
    samples: int,
    seed: int,
) -> tuple[float, int]:
    if daily_ic.empty:
        return np.nan, 0
    month_index = daily_ic.index.tz_localize(None).to_period("M")
    monthly = daily_ic.groupby(month_index).mean().dropna().to_numpy(dtype=float)
    if len(monthly) < 6:
        return np.nan, len(monthly)
    observed = float(monthly.mean())
    centered = monthly - observed
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(centered), size=(samples, len(centered)))
    null_means = centered[indices].mean(axis=1)
    pvalue = float((1 + np.count_nonzero(np.abs(null_means) >= abs(observed))) / (samples + 1))
    return pvalue, len(monthly)


def _benjamini_hochberg(pvalues: pd.Series) -> pd.Series:
    result = pd.Series(np.nan, index=pvalues.index, dtype=float)
    valid = pvalues.dropna().sort_values()
    if valid.empty:
        return result
    count = len(valid)
    raw = valid.to_numpy(dtype=float) * count / np.arange(1, count + 1)
    adjusted = np.minimum.accumulate(raw[::-1])[::-1].clip(0.0, 1.0)
    result.loc[valid.index] = adjusted
    return result


def _top_bottom_spread(frame: pd.DataFrame, feature: str) -> tuple[float, float, float]:
    subset = frame[["date", feature, "target"]].dropna().copy()
    if subset.empty:
        return np.nan, np.nan, np.nan
    subset["rank"] = subset.groupby("date")[feature].rank(method="average", pct=True)
    top = subset.loc[subset["rank"] > 0.8, "target"]
    bottom = subset.loc[subset["rank"] <= 0.2, "target"]
    return (
        float(top.mean()) if len(top) else np.nan,
        float(bottom.mean()) if len(bottom) else np.nan,
        float(top.mean() - bottom.mean()) if len(top) and len(bottom) else np.nan,
    )


def audit_futures_features(
    learning_dataset: pd.DataFrame,
    *,
    config: FuturesISConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    catalog = futures_feature_catalog(learning_dataset)
    start = pd.Timestamp(config.analysis_start, tz="UTC")
    end = pd.Timestamp(config.analysis_end, tz="UTC")
    population = learning_dataset.loc[
        learning_dataset["date"].between(start, end)
        & learning_dataset["target"].notna()
        & learning_dataset["trend_signal"].gt(0)
    ].copy()
    if len(population) < config.minimum_population_rows:
        raise SpotTrendContractError(
            f"insufficient futures IS meta-filter population: {len(population)} < {config.minimum_population_rows}"
        )
    rng = np.random.default_rng(config.negative_control_seed)
    shuffled_parts = []
    for _, group in population.groupby("date", sort=True):
        part = group[["date", "symbol", "target"]].copy()
        part["shuffled_target"] = rng.permutation(part["target"].to_numpy())
        shuffled_parts.append(part)
    shuffled = pd.concat(shuffled_parts, ignore_index=True)
    population = population.merge(
        shuffled[["date", "symbol", "shuffled_target"]],
        on=["date", "symbol"],
        how="left",
        validate="one_to_one",
    )

    rows: list[dict[str, object]] = []
    for index, spec in enumerate(catalog):
        coverage = float(population[spec.name].notna().mean())
        finite_count = int(population[spec.name].notna().sum())
        unique_count = int(population[spec.name].nunique(dropna=True))
        daily_ic = _daily_spearman(population, spec.name, "target")
        control_ic = _daily_spearman(population, spec.name, "shuffled_target")
        pvalue, month_count = _month_block_pvalue(
            daily_ic,
            samples=config.bootstrap_samples,
            seed=config.bootstrap_seed + index,
        )
        top, bottom, spread = _top_bottom_spread(population, spec.name)
        if not daily_ic.empty:
            halfyear = daily_ic.index.year.astype(str) + "H" + np.where(daily_ic.index.month <= 6, "1", "2")
            halfyear_ic = daily_ic.groupby(halfyear).mean()
            observed_sign = np.sign(daily_ic.mean())
            sign_stable = int((np.sign(halfyear_ic) == observed_sign).sum())
        else:
            halfyear_ic = pd.Series(dtype=float)
            sign_stable = 0
        rows.append(
            {
                "feature": spec.name,
                "family": spec.family,
                "rationale": spec.rationale,
                "coverage": coverage,
                "finite_rows": finite_count,
                "unique_values": unique_count,
                "daily_ic_days": len(daily_ic),
                "mean_daily_rank_ic": float(daily_ic.mean()) if len(daily_ic) else np.nan,
                "median_daily_rank_ic": float(daily_ic.median()) if len(daily_ic) else np.nan,
                "control_mean_daily_rank_ic": float(control_ic.mean()) if len(control_ic) else np.nan,
                "excess_absolute_ic_vs_control": (
                    abs(float(daily_ic.mean())) - abs(float(control_ic.mean()))
                    if len(daily_ic) and len(control_ic)
                    else np.nan
                ),
                "top_quintile_expected_target": top,
                "bottom_quintile_expected_target": bottom,
                "top_bottom_target_spread": spread,
                "halfyears_observed": len(halfyear_ic),
                "halfyears_same_ic_sign": sign_stable,
                "month_blocks": month_count,
                "block_bootstrap_pvalue": pvalue,
            }
        )
    feature_audit = pd.DataFrame(rows)
    feature_audit["fdr_qvalue"] = _benjamini_hochberg(feature_audit["block_bootstrap_pvalue"])
    feature_audit["passes_exploratory_gate"] = (
        feature_audit["coverage"].ge(0.60)
        & feature_audit["unique_values"].ge(10)
        & feature_audit["excess_absolute_ic_vs_control"].gt(0.0)
        & feature_audit["fdr_qvalue"].le(config.fdr_alpha)
        & feature_audit["halfyears_same_ic_sign"].ge(
            np.ceil(feature_audit["halfyears_observed"] * 0.60)
        )
    )
    family_rows = []
    for family, group in feature_audit.groupby("family", sort=True):
        family_rows.append(
            {
                "family": family,
                "feature_count": len(group),
                "median_coverage": float(group["coverage"].median()),
                "median_absolute_ic": float(group["mean_daily_rank_ic"].abs().median()),
                "maximum_absolute_ic": float(group["mean_daily_rank_ic"].abs().max()),
                "exploratory_gate_feature_count": int(group["passes_exploratory_gate"].sum()),
                "family_candidate_for_catboost_ablation": int(group["passes_exploratory_gate"].sum()) >= 3,
            }
        )
    family_audit = pd.DataFrame(family_rows)

    usable = [
        name
        for name in feature_audit.loc[feature_audit["coverage"].ge(0.60), "feature"]
        if name in population
    ]
    sampled = population[usable].sample(min(len(population), 20_000), random_state=config.bootstrap_seed)
    correlation = sampled.corr(method="spearman", min_periods=500)
    redundancy_rows = []
    for left_index, left in enumerate(usable):
        for right in usable[left_index + 1 :]:
            value = correlation.at[left, right]
            if np.isfinite(value) and abs(value) >= 0.98:
                redundancy_rows.append(
                    {"left_feature": left, "right_feature": right, "spearman_correlation": float(value)}
                )
    redundancy = pd.DataFrame(redundancy_rows)
    return feature_audit.sort_values(
        ["passes_exploratory_gate", "fdr_qvalue", "excess_absolute_ic_vs_control"],
        ascending=[False, True, False],
        kind="stable",
    ).reset_index(drop=True), family_audit, redundancy


def build_signal_ev_audit(dataset: pd.DataFrame, config: FuturesISConfig) -> pd.DataFrame:
    start = pd.Timestamp(config.analysis_start, tz="UTC")
    end = pd.Timestamp(config.analysis_end, tz="UTC")
    population = dataset.loc[
        dataset["date"].between(start, end) & dataset["target"].notna()
    ].copy()
    population["active_models"] = population["active_count"].astype(int)
    rows = []
    for active_models, group in population.groupby("active_models", sort=True):
        halfyear = group["date"].dt.year.astype(str) + "H" + np.where(group["date"].dt.month <= 6, "1", "2")
        halfyear_target = group.groupby(halfyear)["target"].mean()
        rows.append(
            {
                "active_models": int(active_models),
                "trend_signal": active_models / 9.0,
                "rows": len(group),
                "mean_forward_return": float(group["forward_return"].mean()),
                "median_forward_return": float(group["forward_return"].median()),
                "mean_risk_adjusted_target": float(group["target"].mean()),
                "positive_forward_return_share": float((group["forward_return"] > 0).mean()),
                "positive_halfyears": int((halfyear_target > 0).sum()),
                "halfyears_observed": len(halfyear_target),
            }
        )
    return pd.DataFrame(rows)


def build_breakout_anomaly_audit(
    dataset: pd.DataFrame,
    feature_audit: pd.DataFrame,
    config: FuturesISConfig,
) -> pd.DataFrame:
    """Test whether larger pre-crossing flow anomalies improve forward outcomes monotonically."""

    population = dataset.loc[
        dataset["date"].between(
            pd.Timestamp(config.analysis_start, tz="UTC"),
            pd.Timestamp(config.analysis_end, tz="UTC"),
        )
        & dataset["target"].notna()
        & dataset["trend_signal"].gt(0)
    ].copy()
    control_lookup = feature_audit.set_index("feature")["control_mean_daily_rank_ic"].to_dict()
    rows: list[dict[str, object]] = []
    for measure in ("quote_volume", "trade_count"):
        for window in (5, 15, 30, 60):
            for representation in ("log_ratio", "robust_z", "percentile"):
                feature = f"pre_breakout_{measure}_{window}m_{representation}"
                if feature not in population:
                    raise SpotTrendContractError(f"missing frozen breakout anomaly feature: {feature}")
                available = population[["date", "target", "forward_return", feature]].dropna().copy()
                counts = available.groupby("date").size()
                qualified = available.loc[available["date"].isin(counts.loc[counts >= 5].index)].copy()
                qualified["within_day_rank"] = qualified.groupby("date")[feature].rank(
                    method="average", pct=True
                )
                qualified["quintile"] = np.ceil(qualified["within_day_rank"] * 5).clip(1, 5).astype(int)
                daily_ic = _daily_spearman(qualified, feature, "target")
                quintiles = qualified.groupby("quintile").agg(
                    rows=("target", "size"),
                    mean_target=("target", "mean"),
                    mean_forward_return=("forward_return", "mean"),
                    positive_forward_return_share=("forward_return", lambda values: (values > 0).mean()),
                )
                target_means = quintiles["mean_target"].reindex(range(1, 6))
                q1_target = float(target_means.loc[1])
                q5_target = float(target_means.loc[5])
                q5_return = float(quintiles.at[5, "mean_forward_return"])
                monotonicity = float(
                    pd.Series(range(1, 6), dtype=float).corr(
                        target_means.reset_index(drop=True), method="spearman"
                    )
                )
                observed_ic = float(daily_ic.mean())
                control_ic = float(control_lookup.get(feature, np.nan))
                excess_ic = abs(observed_ic) - abs(control_ic)
                rows.append(
                    {
                        "feature": feature,
                        "measure": measure,
                        "window_minutes": window,
                        "representation": representation,
                        "available_rows": len(available),
                        "qualified_rows": len(qualified),
                        "qualified_days": qualified["date"].nunique(),
                        "coverage_in_meta_population": len(available) / len(population),
                        "mean_daily_rank_ic": observed_ic,
                        "control_mean_daily_rank_ic": control_ic,
                        "excess_absolute_ic_vs_control": excess_ic,
                        "q1_mean_target": q1_target,
                        "q5_mean_target": q5_target,
                        "q5_minus_q1_target": q5_target - q1_target,
                        "q5_mean_forward_return": q5_return,
                        "q5_positive_forward_return_share": float(
                            quintiles.at[5, "positive_forward_return_share"]
                        ),
                        "quintile_monotonicity": monotonicity,
                        "supports_anomaly_hypothesis": bool(
                            observed_ic > 0
                            and excess_ic > 0
                            and q5_target > q1_target
                            and q5_return > 0
                            and monotonicity >= 0.8
                        ),
                    }
                )
    return pd.DataFrame(rows).sort_values(
        ["supports_anomaly_hypothesis", "excess_absolute_ic_vs_control", "q5_minus_q1_target"],
        ascending=[False, False, False],
        kind="stable",
    ).reset_index(drop=True)


def run_futures_is_feature_experiment(
    daily_bars: pd.DataFrame,
    *,
    carry: pd.DataFrame | None = None,
    breakout_config: BreakoutFlowConfig = BreakoutFlowConfig(),
    research_config: ResearchConfig | None = None,
    is_config: FuturesISConfig = FuturesISConfig(),
) -> FuturesISResult:
    research_config = futures_is_research_config() if research_config is None else research_config
    bars = daily_bars.copy()
    bars["date"] = pd.to_datetime(bars["date"], utc=True).dt.normalize()
    if bars.empty:
        raise SpotTrendContractError("futures IS received no market rows")
    if bars["date"].dt.date.min() < IS_START or bars["date"].dt.date.max() > IS_END:
        raise SpotTrendContractError("run_futures_is_feature_experiment received a row outside calendar-2025 IS")
    if "complete_daily_bar" in bars:
        bars = bars.loc[bars["complete_daily_bar"].eq(True)].copy()
    if bars.empty:
        raise SpotTrendContractError("futures IS contains no complete daily bars")
    master = build_data_inferred_futures_master(bars)
    universe = build_point_in_time_universe(bars, master, research_config.universe)
    trend = build_trend_state(bars, research_config.portfolio.horizons)
    base_features = build_feature_matrix(
        bars,
        universe,
        trend,
        research_config.portfolio.horizons,
    )
    breakout_events = build_breakout_flow_features(
        bars,
        trend,
        research_config.portfolio.horizons,
        set(base_features["symbol"]),
        config=breakout_config,
    )
    features = build_futures_feature_matrix(
        base_features,
        bars,
        carry=carry,
        breakout_events=breakout_events,
    )
    dataset = build_learning_dataset(
        features,
        bars,
        horizon_days=is_config.target_horizon_days,
    )
    feature_audit, family_audit, redundancy = audit_futures_features(dataset, config=is_config)
    signal_ev = build_signal_ev_audit(dataset, is_config)
    breakout_anomaly = build_breakout_anomaly_audit(dataset, feature_audit, is_config)
    member_symbols = sorted(set(universe.daily_membership.get("symbol", pd.Series(dtype=str))))
    data_audit = {
        "schema_version": FUTURES_FEATURE_SCHEMA_VERSION,
        "is_start": is_config.analysis_start,
        "is_end_inclusive": is_config.analysis_end,
        "oos_start_locked": OOS_START.isoformat(),
        "observed_market_start": str(bars["date"].min().date()),
        "observed_market_end": str(bars["date"].max().date()),
        "requested_horizons": list(research_config.portfolio.horizons),
        "market_row_count": len(bars),
        "market_symbol_count": bars["symbol"].nunique(),
        "universe_member_symbol_count": len(member_symbols),
        "universe_member_symbols": member_symbols,
        "feature_row_count": len(features),
        "feature_count": len(futures_feature_catalog(features)),
        "resolved_label_count": int(dataset["target"].notna().sum()),
        "meta_filter_population_count": int(
            (dataset["target"].notna() & dataset["trend_signal"].gt(0)).sum()
        ),
        "carry_rows": 0 if carry is None else len(carry),
        "breakout_event_count": len(breakout_events),
        "breakout_event_symbol_count": breakout_events["symbol"].nunique() if len(breakout_events) else 0,
        "open_interest_history_available_in_is": "open_interest_close" in bars,
        "open_interest_daily_row_coverage": (
            float(bars["open_interest_close"].notna().mean()) if "open_interest_close" in bars else 0.0
        ),
    }
    return FuturesISResult(
        config=is_config,
        universe=universe,
        futures_master=master,
        breakout_events=breakout_events,
        features=features,
        learning_dataset=dataset,
        feature_audit=feature_audit,
        family_audit=family_audit,
        redundancy_audit=redundancy,
        signal_ev_audit=signal_ev,
        breakout_anomaly_audit=breakout_anomaly,
        data_audit=data_audit,
    )


def write_futures_is_artifacts(result: FuturesISResult, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    result.universe.snapshots.to_parquet(output_dir / "universe_snapshots.parquet", index=False)
    result.universe.daily_membership.to_parquet(output_dir / "universe_daily_membership.parquet", index=False)
    result.universe.audit.to_parquet(output_dir / "universe_audit.parquet", index=False)
    result.futures_master.to_parquet(output_dir / "data_inferred_futures_master.parquet", index=False)
    result.breakout_events.to_parquet(output_dir / "breakout_flow_events.parquet", index=False)
    result.features.to_parquet(output_dir / "futures_feature_matrix.parquet", index=False)
    result.learning_dataset.to_parquet(output_dir / "futures_learning_dataset.parquet", index=False)
    result.feature_audit.to_csv(output_dir / "feature_is_audit.csv", index=False)
    result.family_audit.to_csv(output_dir / "feature_family_is_audit.csv", index=False)
    result.redundancy_audit.to_csv(output_dir / "feature_redundancy_audit.csv", index=False)
    result.signal_ev_audit.to_csv(output_dir / "trend_signal_ev_audit.csv", index=False)
    result.breakout_anomaly_audit.to_csv(output_dir / "breakout_anomaly_monotonicity_is.csv", index=False)
    (output_dir / "is_manifest.json").write_text(
        json.dumps(
            {"config": asdict(result.config), "data_audit": result.data_audit},
            indent=2,
            sort_keys=True,
            default=str,
        ),
        encoding="utf-8",
    )
