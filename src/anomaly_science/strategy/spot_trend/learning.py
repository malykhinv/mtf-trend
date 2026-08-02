from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

import numpy as np
import pandas as pd

from .contracts import AdmissionConfig, CatBoostConfig, SpotTrendContractError
from .features import model_feature_columns
from .universe import validate_daily_bars


class Regressor(Protocol):
    def fit(self, x: pd.DataFrame, y: pd.Series, **kwargs: Any) -> Any: ...

    def predict(self, x: pd.DataFrame) -> np.ndarray: ...


RegressorFactory = Callable[[dict[str, object]], Regressor]


@dataclass(frozen=True, slots=True)
class WalkForwardSplit:
    model_origin: pd.Timestamp
    application_start: pd.Timestamp
    application_end: pd.Timestamp
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    purge_start: pd.Timestamp
    purge_end: pd.Timestamp
    validation_start: pd.Timestamp
    validation_end: pd.Timestamp


@dataclass(frozen=True, slots=True)
class PredictabilityAudit:
    row_count: int
    date_count: int
    mae: float
    zero_benchmark_mae: float
    mae_improvement: float
    mean_daily_rank_ic: float
    calibration_intercept: float
    calibration_slope: float
    top_quintile_expected_target: float
    top_bottom_target_spread: float
    positive_rank_ic_halfyears: int
    passed: bool
    failures: tuple[str, ...]


def build_learning_dataset(
    features: pd.DataFrame,
    daily_bars: pd.DataFrame,
    *,
    horizon_days: int = 20,
    round_trip_cost: float = 0.005,
) -> pd.DataFrame:
    """Attach strictly future labels while preserving unresolved rows."""

    if horizon_days <= 0:
        raise SpotTrendContractError("horizon_days must be positive")
    bars = validate_daily_bars(daily_bars)
    anchors = features[["date", "symbol"]].copy()
    anchors["future_start_date"] = anchors["date"] + pd.Timedelta(days=1)
    anchors["label_end_date"] = anchors["date"] + pd.Timedelta(days=horizon_days)
    next_opens = bars[["date", "symbol", "open"]].rename(
        columns={"date": "future_start_date", "open": "future_open"}
    )
    future_closes = bars[["date", "symbol", "close"]].rename(
        columns={"date": "label_end_date", "close": "future_close"}
    )
    future = anchors.merge(next_opens, on=["future_start_date", "symbol"], how="left", validate="one_to_one")
    future = future.merge(future_closes, on=["label_end_date", "symbol"], how="left", validate="one_to_one")
    dataset = features.merge(future, on=["date", "symbol"], how="left", validate="one_to_one")
    risk = dataset["vol_90"] * np.sqrt(horizon_days / 365.0)
    forward_return = np.log(dataset["future_close"] / dataset["future_open"])
    dataset["forward_return"] = forward_return
    dataset["forward_risk"] = risk
    dataset["target"] = ((forward_return - round_trip_cost) / risk.clip(lower=0.02)).clip(-3.0, 3.0)
    dataset["target_horizon_days"] = horizon_days
    resolved = dataset["target"].notna()
    invalid_time = resolved & (
        (dataset["feature_cutoff_date"] > dataset["date"])
        | (dataset["future_start_date"] <= dataset["date"])
        | (dataset["label_end_date"] < dataset["future_start_date"])
    )
    if invalid_time.any():
        raise SpotTrendContractError("learning rows violate features <= snapshot < labels")
    return dataset.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)


def weekly_split(origin: pd.Timestamp, config: CatBoostConfig) -> WalkForwardSplit:
    origin = pd.Timestamp(origin).tz_convert("UTC").normalize() if pd.Timestamp(origin).tzinfo else pd.Timestamp(origin, tz="UTC").normalize()
    application_start = origin + pd.Timedelta(days=1)
    application_end = application_start + pd.Timedelta(days=6)
    validation_end = origin - pd.Timedelta(days=config.target_horizon_days)
    validation_start = validation_end - pd.Timedelta(days=config.validation_days - 1)
    purge_end = validation_start - pd.Timedelta(days=1)
    purge_start = purge_end - pd.Timedelta(days=config.purge_days - 1)
    train_end = purge_start - pd.Timedelta(days=1)
    train_start = origin - pd.Timedelta(days=config.training_lookback_days)
    return WalkForwardSplit(
        model_origin=origin,
        application_start=application_start,
        application_end=application_end,
        train_start=train_start,
        train_end=train_end,
        purge_start=purge_start,
        purge_end=purge_end,
        validation_start=validation_start,
        validation_end=validation_end,
    )


def _default_factory(parameters: dict[str, object]) -> Regressor:
    from catboost import CatBoostRegressor

    return CatBoostRegressor(**parameters)


def _sample_weights(frame: pd.DataFrame) -> pd.Series:
    counts = frame.groupby("date")["symbol"].transform("count")
    return 1.0 / counts.astype(float)


def _prediction_rank(values: pd.Series) -> pd.Series:
    present = values.notna().sum()
    if present == 0:
        return pd.Series(np.nan, index=values.index)
    if present == 1:
        result = pd.Series(np.nan, index=values.index)
        result.loc[values.notna()] = 0.5
        return result
    return values.rank(method="average", pct=True)


def fit_predict_weekly(
    dataset: pd.DataFrame,
    *,
    prediction_start: pd.Timestamp | str,
    prediction_end: pd.Timestamp | str,
    config: CatBoostConfig = CatBoostConfig(),
    feature_columns: tuple[str, ...] | None = None,
    regressor_factory: RegressorFactory = _default_factory,
) -> pd.DataFrame:
    """Fit one ensemble per week and keep it frozen for every daily OOS row."""

    frame = dataset.copy()
    frame["date"] = pd.to_datetime(frame["date"], utc=True).dt.normalize()
    frame["label_end_date"] = pd.to_datetime(frame["label_end_date"], utc=True).dt.normalize()
    columns = feature_columns or model_feature_columns(frame)
    if "symbol" in columns or not columns:
        raise SpotTrendContractError("model features must be non-empty and symbol-free")
    start = pd.Timestamp(prediction_start)
    end = pd.Timestamp(prediction_end)
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    end = end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")
    prediction_rows = frame.loc[frame["date"].between(start.normalize(), end.normalize())].copy()
    if prediction_rows.empty:
        return pd.DataFrame()
    week_starts = prediction_rows["date"] - pd.to_timedelta(prediction_rows["date"].dt.weekday, unit="D")
    prediction_rows["application_week_start"] = week_starts
    outputs: list[pd.DataFrame] = []

    for week_start, current in prediction_rows.groupby("application_week_start", sort=True):
        split = weekly_split(week_start - pd.Timedelta(days=1), config)
        train = frame.loc[
            frame["date"].between(split.train_start, split.train_end)
            & frame["target"].notna()
            & frame["label_end_date"].le(split.train_end)
        ].sort_values(["date", "symbol"], kind="stable")
        validation = frame.loc[
            frame["date"].between(split.validation_start, split.validation_end)
            & frame["target"].notna()
            & frame["label_end_date"].le(split.model_origin)
        ].sort_values(["date", "symbol"], kind="stable")
        output = current[["date", "symbol"]].copy()
        output["model_origin"] = split.model_origin
        output["train_start"] = split.train_start
        output["train_end"] = split.train_end
        output["purge_start"] = split.purge_start
        output["purge_end"] = split.purge_end
        output["validation_start"] = split.validation_start
        output["validation_end"] = split.validation_end
        output["train_rows"] = len(train)
        output["validation_rows"] = len(validation)
        if train.empty or validation.empty:
            output["prediction"] = np.nan
            output["model_available"] = False
            outputs.append(output)
            continue
        seed_predictions: list[np.ndarray] = []
        for seed in config.seeds:
            model = regressor_factory(config.model_parameters(seed))
            model.fit(
                train.loc[:, columns],
                train["target"],
                sample_weight=_sample_weights(train),
                eval_set=(validation.loc[:, columns], validation["target"]),
                sample_weight_eval_set=[_sample_weights(validation)],
            )
            seed_predictions.append(np.asarray(model.predict(current.loc[:, columns]), dtype=np.float64))
        output["prediction"] = np.mean(np.vstack(seed_predictions), axis=0)
        output["model_available"] = True
        outputs.append(output)
    forecasts = pd.concat(outputs, ignore_index=True).sort_values(["date", "symbol"], kind="stable")
    forecasts["ml_rank"] = forecasts.groupby("date", group_keys=False)["prediction"].apply(_prediction_rank)
    forecasts["ml_multiplier"] = np.where(
        forecasts["model_available"], 0.5 + 0.5 * forecasts["ml_rank"], 1.0
    )
    return forecasts.reset_index(drop=True)


def build_negative_control(forecasts: pd.DataFrame, *, seed: int = 20_250_319) -> pd.DataFrame:
    """Permute forecasts within date; the frozen RNG prevents seed selection."""

    rng = np.random.default_rng(seed)
    parts: list[pd.DataFrame] = []
    for _, group in forecasts.groupby("date", sort=True):
        part = group.copy()
        available = part["model_available"].to_numpy(dtype=bool)
        values = part.loc[available, "prediction"].to_numpy(copy=True)
        part.loc[available, "prediction"] = rng.permutation(values)
        parts.append(part)
    control = pd.concat(parts, ignore_index=True).sort_values(["date", "symbol"], kind="stable")
    control["ml_rank"] = control.groupby("date", group_keys=False)["prediction"].apply(_prediction_rank)
    control["ml_multiplier"] = np.where(control["model_available"], 0.5 + 0.5 * control["ml_rank"], 1.0)
    return control.reset_index(drop=True)


def evaluate_predictability(
    forecasts: pd.DataFrame,
    dataset: pd.DataFrame,
    admission: AdmissionConfig = AdmissionConfig(),
) -> PredictabilityAudit:
    joined = forecasts[["date", "symbol", "prediction", "model_available"]].merge(
        dataset[["date", "symbol", "target"]], on=["date", "symbol"], how="inner", validate="one_to_one"
    )
    joined = joined.loc[joined["model_available"] & joined["prediction"].notna() & joined["target"].notna()].copy()
    if joined.empty:
        return PredictabilityAudit(
            0,
            0,
            np.nan,
            np.nan,
            np.nan,
            np.nan,
            np.nan,
            np.nan,
            np.nan,
            np.nan,
            0,
            False,
            ("no_resolved_oos_predictions",),
        )
    error = joined["prediction"] - joined["target"]
    mae = float(error.abs().mean())
    zero_mae = float(joined["target"].abs().mean())
    daily_ic = joined.groupby("date").apply(
        lambda group: group["prediction"].corr(group["target"], method="spearman") if len(group) >= 3 else np.nan,
        include_groups=False,
    ).dropna()
    x = joined["prediction"].to_numpy(dtype=float)
    y = joined["target"].to_numpy(dtype=float)
    slope, intercept = np.polyfit(x, y, 1) if np.std(x) > 0 else (np.nan, np.nan)
    ranks = joined.groupby("date")["prediction"].rank(method="average", pct=True)
    top = joined.loc[ranks > 0.8, "target"]
    bottom = joined.loc[ranks <= 0.2, "target"]
    spread = float(top.mean() - bottom.mean()) if len(top) and len(bottom) else np.nan
    top_expected_target = float(top.mean()) if len(top) else np.nan
    if len(daily_ic):
        ic_frame = daily_ic.rename("rank_ic").reset_index()
        ic_frame["halfyear"] = ic_frame["date"].dt.year.astype(str) + "H" + np.where(ic_frame["date"].dt.month <= 6, "1", "2")
        positive_halfyears = int((ic_frame.groupby("halfyear")["rank_ic"].mean() > 0).sum())
    else:
        positive_halfyears = 0
    failures: list[str] = []
    mean_ic = float(daily_ic.mean()) if len(daily_ic) else np.nan
    mae_improvement = zero_mae - mae
    if not np.isfinite(mean_ic) or mean_ic <= admission.minimum_daily_rank_ic:
        failures.append("daily_rank_ic_not_positive")
    if mae_improvement <= admission.minimum_mae_improvement:
        failures.append("mae_does_not_beat_zero_benchmark")
    if not np.isfinite(spread) or spread <= admission.minimum_top_bottom_target_spread:
        failures.append("top_bottom_target_spread_not_positive")
    if not np.isfinite(top_expected_target) or top_expected_target <= admission.minimum_top_quintile_expected_target:
        failures.append("top_quintile_expected_value_not_positive")
    return PredictabilityAudit(
        row_count=len(joined),
        date_count=joined["date"].nunique(),
        mae=mae,
        zero_benchmark_mae=zero_mae,
        mae_improvement=mae_improvement,
        mean_daily_rank_ic=mean_ic,
        calibration_intercept=float(intercept),
        calibration_slope=float(slope),
        top_quintile_expected_target=top_expected_target,
        top_bottom_target_spread=spread,
        positive_rank_ic_halfyears=positive_halfyears,
        passed=not failures,
        failures=tuple(failures),
    )
