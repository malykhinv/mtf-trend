from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from anomaly_science.strategy.spot_trend.contracts import (
    CatBoostConfig,
    PortfolioConfig,
    SimulationConfig,
    SpotTrendContractError,
    UniverseConfig,
)
from anomaly_science.strategy.spot_trend.learning import (
    build_learning_dataset,
    build_negative_control,
    fit_predict_weekly,
    weekly_split,
)
from anomaly_science.strategy.spot_trend.portfolio import build_target_weights
from anomaly_science.strategy.spot_trend.simulation import simulate_spot_portfolio
from anomaly_science.strategy.spot_trend.trend import build_trend_state
from anomaly_science.strategy.spot_trend.universe import build_point_in_time_universe


def _bars(symbols: tuple[str, ...], days: int, *, start: str = "2024-01-01") -> pd.DataFrame:
    dates = pd.date_range(start, periods=days, freq="D", tz="UTC")
    rows: list[dict[str, object]] = []
    for symbol_index, symbol in enumerate(symbols):
        for index, day in enumerate(dates):
            close = 100.0 + symbol_index * 10.0 + index * 0.1
            rows.append(
                {
                    "date": day,
                    "symbol": symbol,
                    "open": close - 0.05,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "base_volume": 10_000.0,
                    "quote_volume": 1_000.0 * (len(symbols) - symbol_index),
                    "number_of_trades": 100.0,
                    "taker_buy_quote_volume": 500.0 * (len(symbols) - symbol_index),
                }
            )
    return pd.DataFrame(rows)


def _master(symbols: tuple[str, ...], *, as_of: str = "2023-01-01") -> pd.DataFrame:
    rows = []
    for symbol in symbols:
        rows.append(
            {
                "as_of_date": pd.Timestamp(as_of, tz="UTC"),
                "symbol": symbol,
                "base_asset": symbol.removesuffix("USDT"),
                "canonical_asset_id": symbol.removesuffix("USDT"),
                "quote_asset": "USDT",
                "trading_start_date": pd.Timestamp("2020-01-01", tz="UTC"),
                "trading_end_date": pd.NaT,
                "spot_trading_allowed": True,
                "status": "TRADING",
                "delisting_announcement_date": pd.NaT,
                "delisting_date": pd.NaT,
                "monitoring_tag": False,
                "monitoring_tag_known": True,
                "is_stablecoin": False,
                "is_leveraged_token": False,
            }
        )
    return pd.DataFrame(rows)


def test_universe_uses_asof_master_and_activates_after_month_end() -> None:
    bars = _bars(("AAAUSDT", "BBBUSDT", "CCCUSDT"), 65)
    master = _master(("AAAUSDT", "BBBUSDT", "CCCUSDT"))
    # This future classification must not rewrite the January snapshot.
    future = master.loc[master["symbol"].eq("AAAUSDT")].copy()
    future["as_of_date"] = pd.Timestamp("2024-02-15", tz="UTC")
    future["monitoring_tag"] = True
    master = pd.concat([master, future], ignore_index=True)
    config = UniverseConfig(
        minimum_history_bars=5,
        minimum_median_quote_volume=100.0,
        entry_rank=2,
        retention_rank=3,
        maximum_members=2,
    )

    universe = build_point_in_time_universe(bars, master, config)

    january = universe.snapshots.loc[universe.snapshots["snapshot_date"].eq(pd.Timestamp("2024-01-31", tz="UTC"))]
    assert set(january["symbol"]) == {"AAAUSDT", "BBBUSDT"}
    assert set(january["effective_date"]) == {pd.Timestamp("2024-02-01", tz="UTC")}
    assert universe.daily_membership["date"].min() == pd.Timestamp("2024-02-01", tz="UTC")
    february_audit = universe.audit.loc[
        universe.audit["snapshot_date"].eq(pd.Timestamp("2024-02-29", tz="UTC"))
        & universe.audit["symbol"].eq("AAAUSDT")
    ].iloc[0]
    assert "monitoring_tag" in february_audit["exclusion_reasons"]
    assert pd.Timestamp("2024-02-14", tz="UTC") not in set(universe.snapshots["snapshot_date"])


def test_donchian_breakout_is_strict_and_exit_uses_prior_stop() -> None:
    closes = [1.0, 2.0, 3.0, 3.0, 4.0, 10.0, 6.0]
    bars = _bars(("AAAUSDT",), len(closes))
    bars["open"] = closes
    bars["high"] = np.asarray(closes) + 0.1
    bars["low"] = np.asarray(closes) - 0.1
    bars["close"] = closes

    state = build_trend_state(bars, (3,))

    assert not bool(state.loc[3, "active_3"])  # equality with prior maximum
    assert bool(state.loc[4, "entered_3"])
    assert state.loc[4, "stop_3"] == pytest.approx(3.5)
    assert state.loc[5, "stop_3"] == pytest.approx(6.5)
    assert bool(state.loc[6, "exited_3"])
    assert state.loc[6, "trend_signal"] == 0.0


def test_gap_is_not_silently_bridged_by_donchian_window_or_future_label() -> None:
    bars = _bars(("AAAUSDT",), 7)
    missing_day = pd.Timestamp("2024-01-03", tz="UTC")
    bars = bars.loc[~bars["date"].eq(missing_day)].copy()
    bars["close"] = np.arange(1.0, len(bars) + 1.0)
    bars["open"] = bars["close"]
    bars["high"] = bars["close"] + 0.1
    bars["low"] = bars["close"] - 0.1

    state = build_trend_state(bars, (3,))

    assert not state.loc[state["date"].le(pd.Timestamp("2024-01-06", tz="UTC")), "entered_3"].any()
    features = pd.DataFrame(
        {
            "date": [pd.Timestamp("2024-01-02", tz="UTC")],
            "symbol": ["AAAUSDT"],
            "feature_cutoff_date": [pd.Timestamp("2024-01-02", tz="UTC")],
            "vol_90": [0.5],
        }
    )
    dataset = build_learning_dataset(features, bars, horizon_days=2)
    assert dataset.loc[0, "future_start_date"] == missing_day
    assert pd.isna(dataset.loc[0, "future_open"])
    assert pd.isna(dataset.loc[0, "target"])


def test_heavy_model_contract_rejects_monthly_refit_and_short_purge() -> None:
    with pytest.raises(SpotTrendContractError, match="weekly"):
        CatBoostConfig(refit_frequency="monthly")  # type: ignore[arg-type]
    with pytest.raises(SpotTrendContractError, match="cover"):
        CatBoostConfig(target_horizon_days=25, purge_days=20)


def test_weekly_walk_forward_freezes_models_and_purges_unresolved_labels() -> None:
    dates = pd.date_range("2021-01-01", periods=1_300, freq="D", tz="UTC")
    rows = []
    for day_index, day in enumerate(dates):
        for symbol_index, symbol in enumerate(("AAAUSDT", "BBBUSDT")):
            rows.append(
                {
                    "date": day,
                    "symbol": symbol,
                    "x": day_index + symbol_index,
                    "target": float(symbol_index),
                    "label_end_date": day + pd.Timedelta(days=20),
                }
            )
    dataset = pd.DataFrame(rows)
    fit_records: list[dict[str, object]] = []

    class FakeRegressor:
        def __init__(self, parameters: dict[str, object]) -> None:
            self.parameters = parameters

        def fit(self, x, y, **kwargs):
            fit_records.append(
                {
                    "seed": self.parameters["random_seed"],
                    "max_x": float(x["x"].max()),
                    "rows": len(x),
                    "eval_rows": len(kwargs["eval_set"][0]),
                }
            )
            return self

        def predict(self, x):
            return np.full(len(x), float(self.parameters["random_seed"]))

    prediction_start = dates[-14]
    prediction_end = dates[-1]
    forecasts = fit_predict_weekly(
        dataset,
        prediction_start=prediction_start,
        prediction_end=prediction_end,
        config=CatBoostConfig(iterations=1),
        feature_columns=("x",),
        regressor_factory=FakeRegressor,
    )

    assert forecasts["model_available"].all()
    assert forecasts.groupby("application_week_start" if "application_week_start" in forecasts else "model_origin")["model_origin"].nunique().max() == 1
    assert set(record["seed"] for record in fit_records) == {17, 43, 91}
    assert len(fit_records) in {6, 9}  # 14 calendar days can touch two or three weeks.
    for origin in forecasts["model_origin"].unique():
        split = weekly_split(pd.Timestamp(origin), CatBoostConfig(iterations=1))
        eligible = dataset.loc[
            dataset["date"].between(split.train_start, split.train_end)
            & dataset["label_end_date"].le(split.train_end)
        ]
        assert not eligible.empty


def test_negative_control_only_permutes_within_date_and_never_increases_weight() -> None:
    day1 = pd.Timestamp("2025-01-01", tz="UTC")
    day2 = day1 + pd.Timedelta(days=1)
    forecasts = pd.DataFrame(
        {
            "date": [day1] * 3 + [day2] * 3,
            "symbol": ["A", "B", "C"] * 2,
            "prediction": [1.0, 2.0, 3.0, 10.0, 20.0, 30.0],
            "model_available": [True] * 6,
        }
    )

    control = build_negative_control(forecasts, seed=7)

    for day in (day1, day2):
        assert sorted(control.loc[control["date"].eq(day), "prediction"]) == sorted(
            forecasts.loc[forecasts["date"].eq(day), "prediction"]
        )
    assert control["ml_multiplier"].between(0.5, 1.0).all()


def test_target_weights_zero_explicitly_when_common_covariance_is_unavailable() -> None:
    bars = _bars(("AAAUSDT",), 30)
    master = _master(("AAAUSDT",))
    features = bars[["date", "symbol"]].copy()
    features["median_quote_volume_30"] = 10_000_000.0
    features["vol_90"] = 0.2
    features["trend_signal"] = 1.0
    features["trend_state_changed"] = False

    targets = build_target_weights(
        features,
        bars,
        master,
        portfolio=replace(PortfolioConfig(), covariance_window=20),
        universe=replace(UniverseConfig(), minimum_median_quote_volume=100.0),
    )

    early = targets.loc[targets["date"].eq(targets["date"].min())].iloc[0]
    assert early["covariance_status"] == "insufficient_complete_common_returns"
    assert early["target_weight"] == 0.0
    late = targets.loc[targets["date"].eq(targets["date"].max())].iloc[0]
    assert late["covariance_status"] == "estimated"
    assert 0.0 < late["target_weight"] <= 0.05


def test_simulator_executes_after_signal_and_carries_capacity_limited_order() -> None:
    bars = _bars(("AAAUSDT",), 4)
    master = _master(("AAAUSDT",))
    first_day = bars["date"].min()
    targets = pd.DataFrame(
        {
            "date": [first_day],
            "symbol": ["AAAUSDT"],
            "target_weight": [0.05],
            "force_rebalance": [True],
            "median_quote_volume_30": [1_000.0],
        }
    )

    result = simulate_spot_portfolio(
        bars,
        master,
        targets,
        simulation=SimulationConfig(
            one_way_cost_bps=25,
            participation_cap=0.001,
            initial_capital=1_000.0,
        ),
    )

    assert not result.fills.empty
    assert result.fills["date"].min() == first_day + pd.Timedelta(days=1)
    assert (result.fills["signal_date"] < result.fills["date"]).all()
    assert result.fills["participation"].le(0.001 + 1e-12).all()
    assert result.fills["partial_fill"].all()
