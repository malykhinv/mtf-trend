"""Exact external-swing stop follow-up for registered regime-atlas candidates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


BASE = Path(".output/results/xsect_momentum/hourly_reentry_regime_atlas_v1")
OUT = Path(".output/results/xsect_momentum/hourly_macro_stop_rotation_v1")
CONTEXT = BASE / "event_context.parquet"
PROTOCOL = Path("docs/strategies/xsect_momentum_1h_macro_stop_rotation_protocol_v1.md")


def _candidate_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "btc_slow_rotation_eth_strength":
            (frame["btc_fast_minus_slow_mean"] < 0.0)
            & (frame["eth_minus_btc_168h"] >= 0.0),
        "eth_strength_coin_weakness":
            (frame["eth_minus_btc_168h"] >= 0.0)
            & (frame["coin_minus_btc_168h"] < 0.0),
        "btc_strong_alt_weak_isolated":
            (frame["btc_close_vs_mean_720h"] >= 0.0)
            & (frame["market_positive_share_7d"] < 0.40)
            & (frame["book_core_z2_share"] <= 0.10),
        "asia_session": frame["signal_hour_utc"].between(0, 7),
    }


def _bootstrap_q95(values: np.ndarray, seed: int) -> float:
    if len(values) == 0:
        return np.nan
    rng = np.random.default_rng(seed)
    draws = rng.choice(values, size=(2_000, len(values)), replace=True).mean(axis=1)
    return float(np.quantile(draws, 0.95))


def main() -> None:
    for path in (CONTEXT, PROTOCOL):
        if not path.exists():
            raise FileNotFoundError(path)
    OUT.mkdir(parents=True, exist_ok=True)
    frame = pd.read_parquet(CONTEXT)
    frame["future_high_24h"] = frame["signal_close"] * (1.0 + frame["future_mae_24h"])
    frame["stop_event_high"] = frame["event_high_price"]
    frame["stop_external_7d_high"] = frame[["event_high_price", "coin_prior_high_168h"]].max(axis=1)
    frame["stop_external_30d_high"] = frame[["event_high_price", "coin_prior_high_720h"]].max(axis=1)
    masks = _candidate_masks(frame)
    rows: list[dict[str, object]] = []
    event_distance = frame["stop_event_high"] / frame["signal_close"] - 1.0
    for candidate_i, (candidate, mask) in enumerate(masks.items()):
        pooled = frame.loc[mask, "future_close_return_24h"].dropna().to_numpy(dtype=float)
        q95 = _bootstrap_q95(pooled, 20_260_805 + candidate_i)
        for stop_name in ("event_high", "external_7d_high", "external_30d_high"):
            stop = frame[f"stop_{stop_name}"]
            stop_distance = stop / frame["signal_close"] - 1.0
            scale = (event_distance / stop_distance.where(stop_distance > 0.0)).clip(upper=1.0)
            row: dict[str, object] = {
                "candidate": candidate,
                "stop_anchor": stop_name,
                "bootstrap_mean_q95": q95,
            }
            support_ok = True
            means: list[float] = []
            negatives: list[float] = []
            breaches: list[float] = []
            for year in (2023, 2024, 2025):
                selected = frame.loc[mask & (frame["entry_year"] == year)].copy()
                selected_stop = stop.loc[selected.index]
                selected_distance = stop_distance.loc[selected.index]
                selected_scale = scale.loc[selected.index]
                returns = selected["future_close_return_24h"].dropna()
                breach = selected["future_high_24h"] >= selected_stop
                mean = float(returns.mean()) if len(returns) else np.nan
                negative = float((returns < 0.0).mean()) if len(returns) else np.nan
                breach_share = float(breach.mean()) if len(breach) else np.nan
                row.update({
                    f"events_{year}": len(returns),
                    f"mean_return_24h_{year}": mean,
                    f"negative_share_{year}": negative,
                    f"stop_breach_{year}": breach_share,
                    f"median_stop_distance_{year}": float(selected_distance.median()),
                    f"q90_stop_distance_{year}": float(selected_distance.quantile(0.90)),
                    f"median_position_scale_{year}": float(selected_scale.median()),
                    f"q10_position_scale_{year}": float(selected_scale.quantile(0.10)),
                })
                support_ok &= len(returns) >= 30
                means.append(mean)
                negatives.append(negative)
                breaches.append(breach_share)
            row.update({
                "support_ok": support_ok,
                "all_year_means_negative": all(value < 0.0 for value in means),
                "min_negative_share": float(np.nanmin(negatives)),
                "max_stop_breach": float(np.nanmax(breaches)),
                "exploratory_pass": bool(
                    support_ok
                    and all(value < 0.0 for value in means)
                    and min(negatives) > 0.50
                    and max(breaches) <= 0.25
                    and q95 < 0.0
                ),
            })
            rows.append(row)
    result = pd.DataFrame(rows).sort_values(
        ["exploratory_pass", "max_stop_breach", "candidate", "stop_anchor"],
        ascending=[False, True, True, True],
        kind="stable",
    )
    result.to_parquet(OUT / "macro_stop_results.parquet", index=False)
    result.to_csv(OUT / "macro_stop_results.csv", index=False)
    metadata = {
        "protocol": str(PROTOCOL),
        "protocol_sha256": hashlib.sha256(PROTOCOL.read_bytes()).hexdigest(),
        "context": str(CONTEXT),
        "context_sha256": hashlib.sha256(CONTEXT.read_bytes()).hexdigest(),
        "candidate_count": len(masks),
        "result_rows": len(result),
        "exploratory_passes": int(result["exploratory_pass"].sum()),
    }
    (OUT / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    print(result.to_string(index=False), flush=True)
    print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()

