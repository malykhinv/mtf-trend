"""Derived asymmetric and causal-regime grid over position-management v1.

Protocol: docs/strategies/xsect_momentum_position_management_hypothesis_grid_v2.md
The command uses only frozen v1 mandate-policy results and causal entry context.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import ttest_1samp

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.position_management_screen import (
    OUT,
    RUN_ROOT,
    SCREEN_OUT,
    registered_policies,
)


GRID_OUT = OUT / "position_management_hypothesis_grid_v2"
PROTOCOL = Path("docs/strategies/xsect_momentum_position_management_hypothesis_grid_v2.md")
CONDITIONS = (
    "btc_above_30d_mean",
    "btc_below_30d_mean",
    "btc_fast_above_slow",
    "btc_fast_below_slow",
    "btc_30d_up",
    "btc_30d_down",
    "btc_drawdown_30d_gt10",
    "breadth_7d_bull",
    "breadth_7d_bear",
    "btc_vol_expansion",
    "btc_vol_compression",
    "weekend_entry",
)


def _market_context() -> pd.DataFrame:
    panel = pn.load_panel(is_only=True, source_glob=str(Path(pn.KLINES_DAILY_PANEL)))
    close = pn.pivot(panel, "close").sort_index()
    btc = close["BTCUSDT"]
    btc_ret = btc.pct_change(fill_method=None)
    mean_7 = btc.rolling(7, min_periods=7).mean()
    mean_30 = btc.rolling(30, min_periods=30).mean()
    vol_7 = btc_ret.rolling(7, min_periods=7).std()
    vol_30 = btc_ret.rolling(30, min_periods=30).std()
    returns_7d = close.pct_change(7, fill_method=None)
    breadth_7d = returns_7d.gt(0.0).where(returns_7d.notna()).mean(axis=1)
    return pd.DataFrame({
        "btc_above_30d_mean": btc > mean_30,
        "btc_below_30d_mean": btc <= mean_30,
        "btc_fast_above_slow": mean_7 > mean_30,
        "btc_fast_below_slow": mean_7 <= mean_30,
        "btc_30d_up": btc.pct_change(30, fill_method=None) > 0.0,
        "btc_30d_down": btc.pct_change(30, fill_method=None) <= 0.0,
        "btc_drawdown_30d_gt10": btc / btc.rolling(30, min_periods=30).max() - 1.0 <= -0.10,
        "breadth_7d_bull": breadth_7d > 0.60,
        "breadth_7d_bear": breadth_7d < 0.40,
        "btc_vol_expansion": vol_7 > 1.25 * vol_30,
        "btc_vol_compression": vol_7 < 0.75 * vol_30,
    }).fillna(False)


def _metrics(run_id: str, policy_id: str, family: str, pnl: np.ndarray, info: pd.DataFrame, **extra: object) -> dict[str, object]:
    positive = float(pnl[pnl > 0.0].sum())
    negative = float(-pnl[pnl < 0.0].sum())
    count = max(1, int(np.ceil(len(pnl) * 0.01)))
    top = float(np.partition(pnl, len(pnl) - count)[-count:].sum())
    years = info["year"].to_numpy(dtype=int)
    return {
        "run_id": run_id,
        "policy_id": policy_id,
        "family": family,
        **extra,
        "trades": len(pnl),
        "net_pnl": float(pnl.sum()),
        "return_on_entry_notional": float(pnl.sum() / info["entry_notional"].sum()),
        "profit_factor": float(positive / negative) if negative else np.nan,
        "win_rate": float((pnl > 0.0).mean()),
        "top1_drop_remaining_pnl": float(pnl.sum() - top),
        **{f"pnl_{year}": float(pnl[years == year].sum()) for year in (2023, 2024, 2025)},
    }


def _benjamini_hochberg(p_values: pd.Series) -> pd.Series:
    values = p_values.to_numpy(dtype=float)
    order = np.argsort(values)
    ranked = values[order]
    adjusted = np.minimum.accumulate((ranked * len(values) / np.arange(1, len(values) + 1))[::-1])[::-1]
    out = np.empty_like(adjusted)
    out[order] = np.clip(adjusted, 0.0, 1.0)
    return pd.Series(out, index=p_values.index)


def _plateau(config_summary: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for (family, policy_id), group in config_summary.groupby(["family", "policy_id"], sort=False):
        positive = group.loc[group["net_pnl"] > 0.0, "net_pnl"]
        total_positive = float(positive.sum())
        returns = group["return_on_entry_notional"].to_numpy(dtype=float)
        t_result = ttest_1samp(returns, popmean=0.0, alternative="greater")
        rows.append({
            "family": family,
            "policy_id": policy_id,
            "admissible": bool(group["admissible"].fillna(True).all()) if "admissible" in group else True,
            "positive_configs": int((group["net_pnl"] > 0.0).sum()),
            "median_profit_factor": float(group["profit_factor"].median()),
            "pooled_pnl_2023": float(group["pnl_2023"].sum()),
            "pooled_pnl_2024": float(group["pnl_2024"].sum()),
            "pooled_pnl_2025": float(group["pnl_2025"].sum()),
            "pooled_top1_drop_remaining": float(group["top1_drop_remaining_pnl"].sum(min_count=1)),
            "max_positive_config_share": float(positive.max() / total_positive) if total_positive else np.nan,
            "mean_config_return_on_notional": float(np.mean(returns)),
            "one_sided_config_t_p": float(t_result.pvalue),
        })
    out = pd.DataFrame(rows)
    out["fdr_q"] = out.groupby("family", group_keys=False)["one_sided_config_t_p"].apply(_benjamini_hochberg)
    out["core_gates_pass"] = (
        out["admissible"]
        & (out["positive_configs"] >= 12)
        & (out["median_profit_factor"] >= 1.05)
        & (out[["pooled_pnl_2023", "pooled_pnl_2024", "pooled_pnl_2025"]].min(axis=1) > 0.0)
        & (out["pooled_top1_drop_remaining"] > 0.0)
        & (out["max_positive_config_share"] <= 0.25)
    )
    family_support = out.groupby("family")["core_gates_pass"].transform("sum") >= 2
    out["neighbourhood_supported"] = out["core_gates_pass"] & family_support
    return out


def _pair_prefilter(pair_summary: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for policy_id, group in pair_summary.groupby("policy_id", sort=False):
        positive = group.loc[group["net_pnl"] > 0.0, "net_pnl"]
        total_positive = float(positive.sum())
        rows.append({
            "policy_id": policy_id,
            "positive_configs": int((group["net_pnl"] > 0.0).sum()),
            "median_profit_factor": float(group["profit_factor"].median()),
            "pooled_pnl_2023": float(group["pnl_2023"].sum()),
            "pooled_pnl_2024": float(group["pnl_2024"].sum()),
            "pooled_pnl_2025": float(group["pnl_2025"].sum()),
            "max_positive_config_share": float(positive.max() / total_positive) if total_positive else np.nan,
        })
    out = pd.DataFrame(rows)
    out["prefilter_pass"] = (
        (out["positive_configs"] >= 12)
        & (out["median_profit_factor"] >= 1.05)
        & (out[["pooled_pnl_2023", "pooled_pnl_2024", "pooled_pnl_2025"]].min(axis=1) > 0.0)
        & (out["max_positive_config_share"] <= 0.25)
    )
    return out


def main() -> None:
    GRID_OUT.mkdir(parents=True, exist_ok=True)
    context = _market_context()
    policy_specs = {policy.policy_id: policy for policy in registered_policies()}
    policy_ids = list(policy_specs)
    admissible = [policy_id for policy_id in policy_ids if policy_specs[policy_id].admissible]
    run_ids = sorted(path.stem for path in (SCREEN_OUT / "mandate_policy_results").glob("*.parquet"))
    side_rows: list[dict[str, object]] = []
    regime_rows: list[dict[str, object]] = []
    pair_rows: list[dict[str, object]] = []
    run_cache: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}
    for run_number, run_id in enumerate(run_ids, start=1):
        print(f"[{run_number}/{len(run_ids)}] derived grids {run_id}", flush=True)
        partition = pd.read_parquet(SCREEN_OUT / "mandate_policy_results" / f"{run_id}.parquet")
        pivot = partition.pivot(index="trade_id", columns="policy_id", values="pnl").sort_index()
        info = partition.drop_duplicates("trade_id").set_index("trade_id").sort_index()[
            ["year", "side", "entry_notional"]
        ]
        ledger = pd.read_parquet(RUN_ROOT / f"{run_id}_trades.parquet").reset_index(drop=True)
        ledger["rebalance_date"] = pd.to_datetime(ledger["rebalance_date"], utc=True)
        ledger["entry_time"] = pd.to_datetime(ledger["entry_time"], utc=True)
        trade_context = context.reindex(ledger["rebalance_date"]).reset_index(drop=True)
        trade_context["weekend_entry"] = ledger["entry_time"].dt.dayofweek.ge(5).to_numpy()
        trade_context.index = pivot.index
        hold = pivot["hold"].to_numpy(dtype=float)
        side_array = info["side"].to_numpy()

        for policy_id in policy_ids:
            candidate = pivot[policy_id].to_numpy(dtype=float)
            for side in ("long", "short"):
                pnl = np.where(side_array == side, candidate, hold)
                derived_id = f"{side}_only::{policy_id}"
                side_rows.append(_metrics(
                    run_id, derived_id, "side_specific", pnl, info,
                    side=side, primitive_policy=policy_id,
                    admissible=policy_specs[policy_id].admissible,
                ))
                for condition in CONDITIONS:
                    mask = (side_array == side) & trade_context[condition].to_numpy(dtype=bool)
                    regime_pnl = np.where(mask, candidate, hold)
                    regime_id = f"{side}::{condition}::{policy_id}"
                    regime_rows.append(_metrics(
                        run_id, regime_id, "causal_regime", regime_pnl, info,
                        side=side, condition=condition, primitive_policy=policy_id,
                        admissible=policy_specs[policy_id].admissible,
                    ))

        long_mask = side_array == "long"
        short_mask = ~long_mask
        long_info = info.iloc[np.flatnonzero(long_mask)]
        short_info = info.iloc[np.flatnonzero(short_mask)]
        long_agg: dict[str, dict[str, float]] = {}
        short_agg: dict[str, dict[str, float]] = {}
        for policy_id in admissible:
            for mask, subset_info, destination in (
                (long_mask, long_info, long_agg), (short_mask, short_info, short_agg),
            ):
                values = pivot[policy_id].to_numpy(dtype=float)[mask]
                years = subset_info["year"].to_numpy(dtype=int)
                destination[policy_id] = {
                    "net": float(values.sum()),
                    "positive": float(values[values > 0.0].sum()),
                    "negative": float(-values[values < 0.0].sum()),
                    **{f"y{year}": float(values[years == year].sum()) for year in (2023, 2024, 2025)},
                }
        total_notional = float(info["entry_notional"].sum())
        for long_policy in admissible:
            left = long_agg[long_policy]
            for short_policy in admissible:
                right = short_agg[short_policy]
                positive = left["positive"] + right["positive"]
                negative = left["negative"] + right["negative"]
                policy_id = f"L={long_policy}|S={short_policy}"
                pair_rows.append({
                    "run_id": run_id,
                    "policy_id": policy_id,
                    "family": "long_short_pair",
                    "long_policy": long_policy,
                    "short_policy": short_policy,
                    "net_pnl": left["net"] + right["net"],
                    "return_on_entry_notional": (left["net"] + right["net"]) / total_notional,
                    "profit_factor": positive / negative if negative else np.nan,
                    "pnl_2023": left["y2023"] + right["y2023"],
                    "pnl_2024": left["y2024"] + right["y2024"],
                    "pnl_2025": left["y2025"] + right["y2025"],
                    "top1_drop_remaining_pnl": np.nan,
                })
        run_cache[run_id] = (pivot, info)

    side_summary = pd.DataFrame(side_rows)
    regime_summary = pd.DataFrame(regime_rows)
    pair_summary = pd.DataFrame(pair_rows)
    pair_prefilter = _pair_prefilter(pair_summary)
    candidate_pairs = pair_prefilter.loc[pair_prefilter["prefilter_pass"], "policy_id"].tolist()
    print(f"pair prefilter survivors={len(candidate_pairs)}; computing exact concentration", flush=True)
    if candidate_pairs:
        for run_id, (pivot, info) in run_cache.items():
            side_array = info["side"].to_numpy()
            for policy_id in candidate_pairs:
                long_policy, short_policy = policy_id.removeprefix("L=").split("|S=")
                pnl = np.where(
                    side_array == "long",
                    pivot[long_policy].to_numpy(dtype=float),
                    pivot[short_policy].to_numpy(dtype=float),
                )
                count = max(1, int(np.ceil(len(pnl) * 0.01)))
                top1_remaining = float(pnl.sum() - np.partition(pnl, len(pnl) - count)[-count:].sum())
                mask = (pair_summary["run_id"] == run_id) & (pair_summary["policy_id"] == policy_id)
                pair_summary.loc[mask, "top1_drop_remaining_pnl"] = top1_remaining

    summaries = pd.concat([side_summary, regime_summary, pair_summary], ignore_index=True, sort=False)
    plateau = _plateau(summaries)
    for name, frame in (
        ("side_specific_config_summary", side_summary),
        ("regime_config_summary", regime_summary),
        ("pair_config_summary", pair_summary),
        ("pair_prefilter", pair_prefilter),
        ("derived_policy_plateau", plateau),
    ):
        frame.to_parquet(GRID_OUT / f"{name}.parquet", index=False)
        frame.to_csv(GRID_OUT / f"{name}.csv", index=False)
    metadata = {
        "protocol": str(PROTOCOL),
        "protocol_sha256": hashlib.sha256(PROTOCOL.read_bytes()).hexdigest(),
        "v1_metadata_sha256": hashlib.sha256((SCREEN_OUT / "metadata.json").read_bytes()).hexdigest(),
        "run_count": len(run_ids),
        "primitive_policy_count": len(policy_ids),
        "admissible_pair_policy_count": len(admissible) ** 2,
        "regime_condition_count": len(CONDITIONS),
        "stage": "derived_mandate_level_screen_not_exact_portfolio_replay",
    }
    (GRID_OUT / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    print(
        f"completed side={side_summary.policy_id.nunique()} regime={regime_summary.policy_id.nunique()} "
        f"pairs={pair_summary.policy_id.nunique()} strict_survivors={int(plateau.neighbourhood_supported.sum())}",
        flush=True,
    )


if __name__ == "__main__":
    main()
