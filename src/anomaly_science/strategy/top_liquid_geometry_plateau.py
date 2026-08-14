"""Registered geometry plateau audit over cached causal future paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


SOURCE = Path(".output/results/top_liquid_regime_directional_v1")
OUT = SOURCE / "geometry_plateau_v2"


@dataclass(frozen=True)
class GeometryRule:
    name: str
    verticality_min: float | None = None
    verticality_max: float | None = None
    purity_min: float | None = None
    efficiency_min: float | None = None

    def select(self, paths: pd.DataFrame) -> pd.Series:
        selected = pd.Series(True, index=paths.index)
        verticality = paths["verticality_12h"]
        purity = paths["directional_purity_12h"]
        efficiency = paths["path_efficiency_12h"]
        if self.verticality_min is not None:
            selected &= verticality >= self.verticality_min
        if self.verticality_max is not None:
            selected &= verticality < self.verticality_max
        if self.purity_min is not None:
            selected &= purity >= self.purity_min
        if self.efficiency_min is not None:
            selected &= efficiency >= self.efficiency_min
        return selected


VERTICALITY_BANDS = ((0.75, 1.50), (0.75, 2.00), (1.00, 2.00), (1.25, 2.00))
PURITY_MINIMUMS = (0.65, 0.70, 0.75)
RULES = (
    GeometryRule("baseline"),
    *(GeometryRule(f"v_{low:.2f}_{high:.2f}", low, high) for low, high in VERTICALITY_BANDS),
    *(GeometryRule(f"purity_{minimum:.2f}", purity_min=minimum) for minimum in PURITY_MINIMUMS),
    *(GeometryRule(f"efficiency_{minimum:.2f}", efficiency_min=minimum) for minimum in (0.40, 0.55, 0.70)),
    *(
        GeometryRule(
            f"v_{low:.2f}_{high:.2f}__purity_{minimum:.2f}",
            low,
            high,
            purity_min=minimum,
        )
        for low, high in VERTICALITY_BANDS
        for minimum in PURITY_MINIMUMS
    ),
)


def _metrics(group: pd.DataFrame) -> dict[str, float | int]:
    values = group["signed_return"]
    top_count = max(1, int(np.ceil(len(values) * 0.01)))
    return {
        "events": int(group["path_id"].nunique()),
        "mean_return": float(values.mean()),
        "median_return": float(values.median()),
        "win_share": float((values > 0.0).mean()),
        "top1_drop_mean": float(
            (values.sum() - values.nlargest(top_count).sum()) / len(values)
        ),
        "mean_mfe": float(group["mfe"].mean()),
        "mean_mae": float(group["mae"].mean()),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    signals = pd.read_parquet(SOURCE / "signals.parquet")
    paths = pd.read_parquet(SOURCE / "paths.parquet").set_index("path_id", drop=False)
    basket = pd.read_parquet(SOURCE / "basket_events.parquet").set_index("path_id", drop=False)
    config_columns = ["universe_n", "breadth_threshold", "ema_hours", "direction"]
    transitions = signals.loc[signals["rule"] == "transition"]
    rows: list[dict[str, object]] = []
    grouped = transitions.groupby(config_columns, observed=True, sort=False)
    for config_keys, config_signals in grouped:
        config = dict(zip(config_columns, config_keys, strict=True))
        path_ids = np.intersect1d(
            config_signals["path_id"].unique(), basket.index.unique(), assume_unique=False,
        )
        config_paths = paths.loc[path_ids]
        for rule in RULES:
            selected_ids = config_paths.loc[rule.select(config_paths), "path_id"].to_numpy()
            if len(selected_ids) == 0:
                continue
            selected = basket.loc[selected_ids]
            for (year, horizon), future in selected.groupby(
                ["entry_year", "horizon_hours"], observed=True, sort=False,
            ):
                rows.append({
                    **config,
                    "geometry_rule": rule.name,
                    "entry_year": int(year),
                    "horizon_hours": int(horizon),
                    **_metrics(future),
                })
    metrics = pd.DataFrame(rows)
    metrics.to_parquet(OUT / "metrics.parquet", index=False)
    metrics.to_csv(OUT / "metrics.csv", index=False)

    config = config_columns + ["geometry_rule", "horizon_hours"]
    pooled_rows: list[dict[str, object]] = []
    for keys, group in metrics.groupby(config, observed=True, sort=False):
        weights = group["events"].to_numpy()
        pooled_rows.append({
            **dict(zip(config, keys, strict=True)),
            "events": int(group["events"].sum()),
            "mean_return": float(np.average(group["mean_return"], weights=weights)),
            "win_share": float(np.average(group["win_share"], weights=weights)),
            "top1_drop_mean": float(np.average(group["top1_drop_mean"], weights=weights)),
            "positive_years": int((group["mean_return"] > 0.0).sum()),
            "min_year_mean": float(group["mean_return"].min()),
            "years": int(group["entry_year"].nunique()),
        })
    pooled = pd.DataFrame(pooled_rows)
    pooled.to_parquet(OUT / "pooled_config_metrics.parquet", index=False)
    pooled.to_csv(OUT / "pooled_config_metrics.csv", index=False)

    summary = pooled.groupby(
        ["geometry_rule", "direction", "horizon_hours"], observed=True, as_index=False,
    ).agg(
        configurations=("mean_return", "size"),
        median_events=("events", "median"),
        median_mean_return=("mean_return", "median"),
        positive_configuration_share=("mean_return", lambda value: (value > 0.0).mean()),
        all_year_positive_share=("positive_years", lambda value: (value == 3).mean()),
        median_min_year_mean=("min_year_mean", "median"),
        median_top1_drop_mean=("top1_drop_mean", "median"),
    )
    summary.to_parquet(OUT / "summary.parquet", index=False)
    summary.to_csv(OUT / "summary.csv", index=False)
    print(f"completed rules={len(RULES)} metric_rows={len(metrics)}", flush=True)


if __name__ == "__main__":
    main()
