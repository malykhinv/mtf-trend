"""HTF/LTF runner discovery for pump-awakening research.

The tool separates two jobs that are easy to mix up:

* future labels: did an HTF anomaly become a +10% runner within the next hour,
  and was the anomaly low broken before that happened;
* executable replay: would a live bot, using only closed LTF candles available
  at the decision time, enter and manage the trade with structural stop/trailing
  and no take-profit.

Artifacts are research-only. They are meant to learn runner nature, not to
declare a live-ready edge from one run.
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from constants import DEFAULT_CACHE_DIR, DEFAULT_RESULTS_DIR
from data.storage.parquet_storage import ParquetStorage
from domain.enums.timeframe import Timeframe


@dataclass(frozen=True, slots=True)
class HtfLtfRunnerDiscoveryConfig:
    cache_dir: Path = Path(DEFAULT_CACHE_DIR)
    output_dir: Path = Path(DEFAULT_RESULTS_DIR) / "htf_ltf_runner_discovery"
    htf_timeframe: str = "1m"
    ltf_timeframe: str = "5s"
    days: int = 30
    end_timestamp_ms: int | None = None
    baseline_candles: int = 60
    dormancy_candles: int = 30
    pregrowth_candles: int = 5
    min_htf_quote_ratio: float = 5.0
    min_htf_trade_ratio: float = 5.0
    min_htf_return_pct: float = 0.010
    min_dormancy_to_anomaly_quote_ratio: float = 6.0
    min_dormancy_to_anomaly_trade_ratio: float = 5.0
    max_dormancy_range_pct_median: float = 0.004
    min_pregrowth_return_pct: float = 0.002
    max_pregrowth_single_candle_return_pct: float = 0.020
    min_pregrowth_positive_step_share: float = 0.55
    min_pregrowth_oi_change_pct: float = 0.0
    require_pregrowth_oi: bool = False
    runner_horizon_minutes: int = 60
    runner_target_return_pct: float = 0.10
    ltf_min_confirm_candles: int = 6
    ltf_max_confirm_candles: int = 24
    min_ltf_confirm_return_pct: float = 0.004
    min_ltf_quote_pace_ratio: float = 3.0
    min_ltf_trade_pace_ratio: float = 3.0
    min_ltf_taker_buy_share: float | None = None
    min_ltf_second_half_return_pct: float = 0.0
    min_ltf_quote_acceleration: float = 1.0
    min_ltf_trade_acceleration: float = 1.0
    max_entry_drift_pct: float = 0.004
    max_initial_risk_pct: float = 0.05
    structural_stop_buffer_pct: float = 0.0005
    trail_lookback_candles: int = 6
    trail_buffer_pct: float = 0.0005
    max_hold_candles: int = 720
    fee_rate: float = 0.0004
    entry_slippage_pct: float = 0.0005
    exit_slippage_pct: float = 0.0005
    max_open_positions: int = 1

    def __post_init__(self) -> None:
        if self.htf_timeframe == self.ltf_timeframe:
            raise ValueError("htf_timeframe and ltf_timeframe must differ")
        if _timeframe_ms(self.ltf_timeframe) >= _timeframe_ms(self.htf_timeframe):
            raise ValueError("ltf_timeframe must be lower than htf_timeframe")
        for name in (
            "days",
            "baseline_candles",
            "dormancy_candles",
            "pregrowth_candles",
            "runner_horizon_minutes",
            "ltf_min_confirm_candles",
            "ltf_max_confirm_candles",
            "trail_lookback_candles",
            "max_hold_candles",
            "max_open_positions",
        ):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be > 0")
        if self.ltf_max_confirm_candles < self.ltf_min_confirm_candles:
            raise ValueError("ltf_max_confirm_candles must be >= ltf_min_confirm_candles")


def run_htf_ltf_runner_discovery(
    config: HtfLtfRunnerDiscoveryConfig,
    *,
    symbols: Iterable[str] | None = None,
) -> Path:
    started_at = time.monotonic()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    selected_symbols = tuple(_resolve_symbols(config.cache_dir, config.htf_timeframe, symbols))
    storage = ParquetStorage(config.cache_dir)
    htf_ms = _timeframe_ms(config.htf_timeframe)
    ltf_ms = _timeframe_ms(config.ltf_timeframe)
    end_ms = _resolve_end_timestamp_ms(config, storage, selected_symbols)
    start_ms = int(end_ms) - int(config.days) * 24 * 60 * 60 * 1000

    candidate_rows: list[dict[str, object]] = []
    signal_rows: list[dict[str, object]] = []
    trade_rows: list[dict[str, object]] = []
    quality_rows: list[dict[str, object]] = []

    for index, symbol in enumerate(selected_symbols, start=1):
        print(f"runner discovery: {index}/{len(selected_symbols)} {symbol}", flush=True)
        htf = _load_frame(storage, symbol, config.htf_timeframe, start_ms=start_ms, end_ms=end_ms)
        ltf = _load_frame(
            storage,
            symbol,
            config.ltf_timeframe,
            start_ms=start_ms - htf_ms * max(config.baseline_candles, config.dormancy_candles),
            end_ms=end_ms + config.runner_horizon_minutes * 60_000,
        )
        oi = _load_oi_frame(storage, symbol, config=config, start_ms=start_ms, end_ms=end_ms)
        quality_rows.append(_data_quality_row(symbol=symbol, htf=htf, ltf=ltf, oi=oi, config=config))
        if htf.empty or ltf.empty:
            continue

        candidates = _collect_symbol_candidates(symbol=symbol, htf=htf, ltf=ltf, oi=oi, config=config)
        candidate_rows.extend(candidates)
        for candidate in candidates:
            signal = _build_first_ltf_signal(candidate, ltf=ltf, oi=oi, config=config)
            if signal is None:
                continue
            signal_rows.append(signal)
            trade_rows.append(_simulate_no_tp_runner_trade(signal, ltf=ltf, config=config))

    candidates_frame = pd.DataFrame(candidate_rows)
    signals_frame = pd.DataFrame(signal_rows)
    trades_frame = pd.DataFrame(trade_rows)
    live_filtered = _apply_live_portfolio_filter(trades_frame, max_open_positions=config.max_open_positions)

    artifacts = [
        (config.output_dir / "htf_ltf_runner_candidates.csv", candidates_frame),
        (config.output_dir / "htf_ltf_runner_signals.csv", signals_frame),
        (config.output_dir / "htf_ltf_runner_trades_raw.csv", trades_frame),
        (config.output_dir / "htf_ltf_runner_trades_live_filtered.csv", live_filtered),
        (config.output_dir / "htf_ltf_runner_profitability_summary.csv", _summarize_trades(trades_frame)),
        (
            config.output_dir / "htf_ltf_runner_profitability_summary_live_filtered.csv",
            _summarize_trades(live_filtered),
        ),
        (config.output_dir / "htf_ltf_runner_by_setup_nature.csv", _summarize_by_column(trades_frame, "setup_nature")),
        (
            config.output_dir / "htf_ltf_runner_by_setup_nature_live_filtered.csv",
            _summarize_by_column(live_filtered, "setup_nature"),
        ),
        (config.output_dir / "htf_ltf_runner_label_distribution.csv", _label_distribution(candidates_frame)),
        (config.output_dir / "htf_ltf_runner_funnel.csv", _build_funnel(candidates_frame, signals_frame, trades_frame)),
        (config.output_dir / "htf_ltf_runner_skip_reasons.csv", _skip_reasons(trades_frame)),
        (config.output_dir / "htf_ltf_runner_top_dependency.csv", _top_dependency(trades_frame)),
        (
            config.output_dir / "htf_ltf_runner_top_dependency_live_filtered.csv",
            _top_dependency(live_filtered),
        ),
        (config.output_dir / "htf_ltf_runner_data_quality_summary.csv", pd.DataFrame(quality_rows)),
        (config.output_dir / "htf_ltf_runner_honesty_report.csv", _honesty_report(config)),
        (
            config.output_dir / "run_config.csv",
            pd.DataFrame(
                [
                    {
                        **asdict(config),
                        "cache_dir": str(config.cache_dir),
                        "output_dir": str(config.output_dir),
                        "symbols": len(selected_symbols),
                        "start_timestamp_ms": int(start_ms),
                        "start_timestamp_utc": _timestamp_to_utc(start_ms),
                        "end_timestamp_ms": int(end_ms),
                        "end_timestamp_utc": _timestamp_to_utc(end_ms),
                        "execution_model": "next_ltf_open_after_closed_ltf_confirmation",
                        "exit_model": "structural_stop_plus_structural_trailing_no_tp",
                        "future_label_model": "separate_next_hour_10pct_label_not_used_for_entry",
                        "runtime_seconds": round(time.monotonic() - started_at, 3),
                    }
                ]
            ),
        ),
    ]
    for path, frame in artifacts:
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False, encoding="utf-8-sig")

    summary = _metric_map(_summarize_trades(live_filtered))
    print(
        "runner discovery done: "
        f"candidates={len(candidates_frame)} signals={len(signals_frame)} "
        f"closed={int(summary.get('closed_trades', 0))} "
        f"avg_net={float(summary.get('avg_net_return', 0.0)):.4%} "
        f"win_rate={float(summary.get('win_rate', 0.0)):.2%}",
        flush=True,
    )
    return config.output_dir


def _collect_symbol_candidates(
    *,
    symbol: str,
    htf: pd.DataFrame,
    ltf: pd.DataFrame,
    oi: pd.DataFrame,
    config: HtfLtfRunnerDiscoveryConfig,
) -> list[dict[str, object]]:
    htf_ms = _timeframe_ms(config.htf_timeframe)
    horizon_ms = int(config.runner_horizon_minutes) * 60_000
    rows: list[dict[str, object]] = []
    prepared = htf.copy()
    prepared["timestamp"] = pd.to_numeric(prepared["timestamp"], errors="coerce")
    prepared = prepared.dropna(subset=["timestamp", "open", "high", "low", "close"]).sort_values("timestamp")
    quote_series = _numeric_column(prepared, "quote_volume", fallback=_numeric_column(prepared, "close") * _numeric_column(prepared, "volume"))
    trade_series = _numeric_column(prepared, "number_of_trades", fallback=pd.Series(np.nan, index=prepared.index))
    prepared["_quote_volume"] = quote_series
    prepared["_number_of_trades"] = trade_series

    min_index = max(config.baseline_candles, config.dormancy_candles, config.pregrowth_candles) + 1
    for idx in range(min_index, len(prepared)):
        row = prepared.iloc[idx]
        ts = int(row["timestamp"])
        close_ts = ts + htf_ms
        if close_ts + horizon_ms > int(prepared["timestamp"].max()) + htf_ms:
            continue
        baseline = prepared.iloc[idx - config.baseline_candles : idx]
        dormancy = prepared.iloc[idx - config.dormancy_candles : idx]
        pregrowth = prepared.iloc[idx - config.pregrowth_candles : idx]
        if baseline.empty or dormancy.empty or pregrowth.empty:
            continue

        anomaly_open = float(row["open"])
        anomaly_high = float(row["high"])
        anomaly_low = float(row["low"])
        anomaly_close = float(row["close"])
        anomaly_quote = float(row["_quote_volume"])
        anomaly_trades = float(row["_number_of_trades"]) if np.isfinite(float(row["_number_of_trades"])) else float("nan")
        baseline_quote = _positive_median(baseline["_quote_volume"])
        baseline_trades = _positive_median(baseline["_number_of_trades"])
        dormancy_quote = _positive_median(dormancy["_quote_volume"])
        dormancy_trades = _positive_median(dormancy["_number_of_trades"])
        quote_ratio = _safe_divide(anomaly_quote, baseline_quote)
        trade_ratio = _safe_divide(anomaly_trades, baseline_trades)
        dormancy_quote_ratio = _safe_divide(anomaly_quote, dormancy_quote)
        dormancy_trade_ratio = _safe_divide(anomaly_trades, dormancy_trades)
        htf_return = _safe_divide(anomaly_close - anomaly_open, anomaly_open)
        htf_range_pct = _safe_divide(anomaly_high - anomaly_low, anomaly_open)
        dormancy_range_pct_median = _positive_median((dormancy["high"].astype(float) - dormancy["low"].astype(float)) / dormancy["open"].astype(float))

        pregrowth_features = _pregrowth_features(pregrowth)
        oi_features = _oi_pregrowth_features(
            oi,
            start_ms=int(pregrowth.iloc[0]["timestamp"]),
            decision_ms=close_ts,
        )
        label = _future_runner_label(
            ltf,
            start_ms=close_ts,
            horizon_ms=horizon_ms,
            reference_price=anomaly_close,
            anomaly_low=anomaly_low,
            target_return_pct=config.runner_target_return_pct,
        )

        anomaly_gate = (
            quote_ratio >= config.min_htf_quote_ratio
            and trade_ratio >= config.min_htf_trade_ratio
            and htf_return >= config.min_htf_return_pct
        )
        dormancy_ok = (
            dormancy_quote_ratio >= config.min_dormancy_to_anomaly_quote_ratio
            and dormancy_trade_ratio >= config.min_dormancy_to_anomaly_trade_ratio
            and dormancy_range_pct_median <= config.max_dormancy_range_pct_median
        )
        smooth_price_ok = (
            pregrowth_features["pregrowth_return_pct"] >= config.min_pregrowth_return_pct
            and pregrowth_features["pregrowth_max_single_return_pct"] <= config.max_pregrowth_single_candle_return_pct
            and pregrowth_features["pregrowth_positive_step_share"] >= config.min_pregrowth_positive_step_share
        )
        oi_ok = (
            oi_features["pregrowth_oi_status"] == "ok"
            and oi_features["pregrowth_oi_change_pct"] >= config.min_pregrowth_oi_change_pct
        )
        if not config.require_pregrowth_oi and oi_features["pregrowth_oi_status"] != "ok":
            oi_ok = True
        status = "ok" if anomaly_gate else "rejected_htf_anomaly_gate"
        setup_nature = _setup_nature(
            anomaly_gate=anomaly_gate,
            dormancy_ok=dormancy_ok,
            smooth_price_ok=smooth_price_ok,
            oi_ok=oi_ok,
            label=label,
            quote_ratio=quote_ratio,
            trade_ratio=trade_ratio,
        )
        rows.append(
            {
                "symbol": symbol,
                "status": status,
                "setup_nature": setup_nature,
                "timestamp_ms": ts,
                "timestamp_utc": _timestamp_to_utc(ts),
                "htf_close_ms": close_ts,
                "htf_close_utc": _timestamp_to_utc(close_ts),
                "htf_timeframe": config.htf_timeframe,
                "ltf_timeframe": config.ltf_timeframe,
                "future_label_available_at_entry": False,
                "anomaly_open": anomaly_open,
                "anomaly_high": anomaly_high,
                "anomaly_low": anomaly_low,
                "anomaly_close": anomaly_close,
                "htf_return_pct": htf_return,
                "htf_range_pct": htf_range_pct,
                "anomaly_quote_volume": anomaly_quote,
                "anomaly_number_of_trades": anomaly_trades,
                "baseline_quote_volume_median": baseline_quote,
                "baseline_number_of_trades_median": baseline_trades,
                "htf_quote_ratio": quote_ratio,
                "htf_trade_ratio": trade_ratio,
                "dormancy_quote_volume_median": dormancy_quote,
                "dormancy_number_of_trades_median": dormancy_trades,
                "dormancy_to_anomaly_quote_ratio": dormancy_quote_ratio,
                "dormancy_to_anomaly_trade_ratio": dormancy_trade_ratio,
                "dormancy_range_pct_median": dormancy_range_pct_median,
                "dormancy_ok": dormancy_ok,
                "smooth_price_growth_ok": smooth_price_ok,
                "pregrowth_oi_ok": oi_ok,
                "htf_anomaly_gate": anomaly_gate,
                **pregrowth_features,
                **oi_features,
                **label,
            }
        )
    return rows


def _build_first_ltf_signal(
    candidate: dict[str, object],
    *,
    ltf: pd.DataFrame,
    oi: pd.DataFrame,
    config: HtfLtfRunnerDiscoveryConfig,
) -> dict[str, object] | None:
    if candidate.get("status") != "ok":
        return None
    ltf_ms = _timeframe_ms(config.ltf_timeframe)
    htf_ms = _timeframe_ms(config.htf_timeframe)
    start_ms = int(candidate["htf_close_ms"])
    end_ms = start_ms + int(config.runner_horizon_minutes) * 60_000
    segment = ltf.loc[(ltf["timestamp"].astype("int64") >= start_ms) & (ltf["timestamp"].astype("int64") < end_ms)].copy()
    if len(segment) <= config.ltf_min_confirm_candles:
        return None
    baseline_quote = float(candidate.get("baseline_quote_volume_median") or float("nan"))
    baseline_trades = float(candidate.get("baseline_number_of_trades_median") or float("nan"))
    anomaly_low = float(candidate["anomaly_low"])
    anomaly_close = float(candidate["anomaly_close"])

    for confirm_count in range(config.ltf_min_confirm_candles, min(config.ltf_max_confirm_candles, len(segment) - 1) + 1):
        closed = segment.head(confirm_count).copy()
        decision_row = closed.iloc[-1]
        decision_ts = int(decision_row["timestamp"])
        decision_available_ts = decision_ts + ltf_ms
        entry_row = segment.iloc[confirm_count]
        entry_ts = int(entry_row["timestamp"])
        if entry_ts < decision_available_ts:
            continue
        signal_features = _ltf_confirmation_features(
            closed,
            baseline_quote=baseline_quote,
            baseline_trades=baseline_trades,
            htf_ms=htf_ms,
        )
        ltf_ok = (
            signal_features["ltf_confirm_return_pct"] >= config.min_ltf_confirm_return_pct
            and signal_features["ltf_quote_pace_ratio"] >= config.min_ltf_quote_pace_ratio
            and signal_features["ltf_trade_pace_ratio"] >= config.min_ltf_trade_pace_ratio
            and signal_features["ltf_second_half_return_pct"] >= config.min_ltf_second_half_return_pct
            and signal_features["ltf_quote_acceleration"] >= config.min_ltf_quote_acceleration
            and signal_features["ltf_trade_acceleration"] >= config.min_ltf_trade_acceleration
            and float(closed["low"].min()) >= anomaly_low
        )
        taker_share = signal_features["ltf_taker_buy_quote_share"]
        if config.min_ltf_taker_buy_share is not None:
            ltf_ok = ltf_ok and np.isfinite(taker_share) and taker_share >= config.min_ltf_taker_buy_share
        if not ltf_ok:
            continue
        raw_entry_price = float(entry_row["open"])
        entry_price = raw_entry_price * (1.0 + config.entry_slippage_pct)
        decision_close = float(decision_row["close"])
        entry_drift = abs(_safe_divide(entry_price - decision_close, decision_close))
        structural_low = min(anomaly_low, float(closed["low"].min()))
        initial_stop = structural_low * (1.0 - config.structural_stop_buffer_pct)
        initial_risk = entry_price - initial_stop
        initial_risk_pct = _safe_divide(initial_risk, entry_price)
        if not np.isfinite(initial_risk) or initial_risk <= 0.0:
            continue
        if entry_drift > config.max_entry_drift_pct:
            continue
        if initial_risk_pct > config.max_initial_risk_pct:
            continue
        oi_at_signal = _oi_asof(oi, decision_available_ts)
        return {
            **candidate,
            "signal_status": "selected",
            "decision_timestamp_ms": decision_ts,
            "decision_timestamp_utc": _timestamp_to_utc(decision_ts),
            "decision_available_timestamp_ms": decision_available_ts,
            "decision_available_timestamp_utc": _timestamp_to_utc(decision_available_ts),
            "decision_close": decision_close,
            "confirmation_candles": confirm_count,
            "entry_timestamp_ms": entry_ts,
            "entry_timestamp_utc": _timestamp_to_utc(entry_ts),
            "raw_entry_price": raw_entry_price,
            "entry_price": entry_price,
            "entry_price_model": "next_ltf_open_plus_adverse_slippage",
            "entry_drift_pct": entry_drift,
            "initial_stop": initial_stop,
            "initial_risk": initial_risk,
            "initial_risk_pct": initial_risk_pct,
            "structural_stop_model": "min_anomaly_low_and_closed_ltf_lows_before_decision_minus_buffer",
            "tp_model": "none",
            "signal_oi_status": oi_at_signal["status"],
            "signal_oi_timestamp_ms": oi_at_signal["timestamp_ms"],
            "signal_oi_available_timestamp_ms": oi_at_signal["available_timestamp_ms"],
            "signal_oi_open_interest": oi_at_signal["open_interest"],
            **signal_features,
        }
    return None


def _simulate_no_tp_runner_trade(
    signal: dict[str, object],
    *,
    ltf: pd.DataFrame,
    config: HtfLtfRunnerDiscoveryConfig,
) -> dict[str, object]:
    entry_ts = int(signal["entry_timestamp_ms"])
    entry_price = float(signal["entry_price"])
    initial_stop = float(signal["initial_stop"])
    initial_risk = entry_price - initial_stop
    if not np.isfinite(initial_risk) or initial_risk <= 0.0:
        return _skipped_trade(signal, "invalid_initial_risk")
    future = ltf.loc[ltf["timestamp"].astype("int64") >= entry_ts].head(config.max_hold_candles).copy()
    if future.empty:
        return _skipped_trade(signal, "no_post_entry_ltf_candles")

    active_stop = initial_stop
    trail_updates = 0
    max_high = entry_price
    min_low = entry_price
    exit_reason = "time_exit"
    exit_ts = int(future.iloc[-1]["timestamp"])
    raw_exit_price = float(future.iloc[-1]["close"])
    exit_price = raw_exit_price * (1.0 - config.exit_slippage_pct)
    exit_stop_before_update = active_stop

    for idx, row in future.iterrows():
        candle_ts = int(row["timestamp"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        max_high = max(max_high, high)
        min_low = min(min_low, low)
        if low <= active_stop:
            exit_reason = "initial_stop" if trail_updates == 0 else "structural_trailing_stop"
            exit_ts = candle_ts
            raw_exit_price = active_stop
            exit_price = raw_exit_price * (1.0 - config.exit_slippage_pct)
            exit_stop_before_update = active_stop
            break
        prior = future.loc[future["timestamp"].astype("int64") < candle_ts].tail(config.trail_lookback_candles)
        if len(prior) >= config.trail_lookback_candles and high >= max_high:
            structural_low = float(prior["low"].min())
            candidate_stop = structural_low * (1.0 - config.trail_buffer_pct)
            if candidate_stop > active_stop and candidate_stop < close:
                active_stop = candidate_stop
                trail_updates += 1

    gross_return = (exit_price - entry_price) / entry_price
    net_return = gross_return - 2.0 * float(config.fee_rate)
    gross_r = (exit_price - entry_price) / initial_risk
    result = {
        **signal,
        "status": "closed",
        "skip_reason": "",
        "execution_guard": True,
        "exit_timestamp_ms": exit_ts,
        "exit_timestamp_utc": _timestamp_to_utc(exit_ts),
        "exit_reason": exit_reason,
        "exit_raw_price": raw_exit_price,
        "exit_price": exit_price,
        "exit_price_model": "stop_or_time_exit_minus_adverse_slippage",
        "exit_stop_before_update": exit_stop_before_update,
        "trail_updates": trail_updates,
        "final_trailing_stop": active_stop,
        "tp1_hit": False,
        "tp_model": "none",
        "mfe_pct": _safe_divide(max_high - entry_price, entry_price),
        "mae_pct": _safe_divide(min_low - entry_price, entry_price),
        "gross_r": gross_r,
        "gross_return": gross_return,
        "net_return": net_return,
        "win": net_return > 0.0,
        "fee_rate": float(config.fee_rate),
    }
    return result


def _skipped_trade(signal: dict[str, object], reason: str) -> dict[str, object]:
    return {
        **signal,
        "status": "skipped",
        "skip_reason": reason,
        "execution_guard": False,
        "exit_timestamp_ms": float("nan"),
        "exit_timestamp_utc": "",
        "exit_reason": "",
        "tp1_hit": False,
        "gross_r": float("nan"),
        "gross_return": float("nan"),
        "net_return": float("nan"),
    }


def _future_runner_label(
    ltf: pd.DataFrame,
    *,
    start_ms: int,
    horizon_ms: int,
    reference_price: float,
    anomaly_low: float,
    target_return_pct: float,
) -> dict[str, object]:
    future = ltf.loc[
        (ltf["timestamp"].astype("int64") >= int(start_ms))
        & (ltf["timestamp"].astype("int64") < int(start_ms + horizon_ms))
    ].copy()
    if future.empty or not np.isfinite(reference_price) or reference_price <= 0:
        return {
            "future_label_status": "missing_ltf_future_window",
            "runner_10pct_next_hour": False,
            "runner_first_hit_ms": float("nan"),
            "runner_first_hit_utc": "",
            "future_max_return_pct": float("nan"),
            "future_min_return_pct": float("nan"),
            "anomaly_low_broken_before_runner": False,
            "clean_runner_without_low_break": False,
        }
    highs = pd.to_numeric(future["high"], errors="coerce")
    lows = pd.to_numeric(future["low"], errors="coerce")
    max_return = float(highs.max() / reference_price - 1.0)
    min_return = float(lows.min() / reference_price - 1.0)
    hit = future.loc[highs.ge(reference_price * (1.0 + target_return_pct))]
    first_hit_ms = int(hit.iloc[0]["timestamp"]) if not hit.empty else None
    before = future.loc[future["timestamp"].astype("int64") <= (first_hit_ms if first_hit_ms is not None else int(start_ms + horizon_ms))]
    low_broken = bool(not before.empty and pd.to_numeric(before["low"], errors="coerce").min() < float(anomaly_low))
    runner = first_hit_ms is not None
    return {
        "future_label_status": "ok",
        "runner_10pct_next_hour": runner,
        "runner_first_hit_ms": first_hit_ms if first_hit_ms is not None else float("nan"),
        "runner_first_hit_utc": _timestamp_to_utc(first_hit_ms) if first_hit_ms is not None else "",
        "future_max_return_pct": max_return,
        "future_min_return_pct": min_return,
        "anomaly_low_broken_before_runner": low_broken,
        "clean_runner_without_low_break": bool(runner and not low_broken),
    }


def _pregrowth_features(frame: pd.DataFrame) -> dict[str, object]:
    if frame.empty:
        return {
            "pregrowth_return_pct": float("nan"),
            "pregrowth_positive_step_share": float("nan"),
            "pregrowth_max_single_return_pct": float("nan"),
            "pregrowth_max_pullback_pct": float("nan"),
        }
    opens = pd.to_numeric(frame["open"], errors="coerce")
    closes = pd.to_numeric(frame["close"], errors="coerce")
    highs = pd.to_numeric(frame["high"], errors="coerce")
    lows = pd.to_numeric(frame["low"], errors="coerce")
    step_returns = closes.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    first_open = float(opens.iloc[0])
    last_close = float(closes.iloc[-1])
    rolling_peak = highs.cummax()
    pullback = ((rolling_peak - lows) / rolling_peak).replace([np.inf, -np.inf], np.nan)
    return {
        "pregrowth_return_pct": _safe_divide(last_close - first_open, first_open),
        "pregrowth_positive_step_share": float((step_returns > 0).mean()) if not step_returns.empty else 0.0,
        "pregrowth_max_single_return_pct": float(step_returns.max()) if not step_returns.empty else 0.0,
        "pregrowth_max_pullback_pct": float(pullback.max()) if not pullback.empty else 0.0,
    }


def _oi_pregrowth_features(oi: pd.DataFrame, *, start_ms: int, decision_ms: int) -> dict[str, object]:
    start = _oi_asof(oi, int(start_ms))
    end = _oi_asof(oi, int(decision_ms))
    if start["status"] != "ok" or end["status"] != "ok":
        return {
            "pregrowth_oi_status": "missing",
            "pregrowth_oi_change_pct": float("nan"),
            "pregrowth_oi_start": start["open_interest"],
            "pregrowth_oi_end": end["open_interest"],
            "pregrowth_oi_start_available_ms": start["available_timestamp_ms"],
            "pregrowth_oi_end_available_ms": end["available_timestamp_ms"],
        }
    change = _safe_divide(float(end["open_interest"]) - float(start["open_interest"]), float(start["open_interest"]))
    return {
        "pregrowth_oi_status": "ok",
        "pregrowth_oi_change_pct": change,
        "pregrowth_oi_start": start["open_interest"],
        "pregrowth_oi_end": end["open_interest"],
        "pregrowth_oi_start_available_ms": start["available_timestamp_ms"],
        "pregrowth_oi_end_available_ms": end["available_timestamp_ms"],
    }


def _ltf_confirmation_features(
    closed: pd.DataFrame,
    *,
    baseline_quote: float,
    baseline_trades: float,
    htf_ms: int,
) -> dict[str, float]:
    duration_ms = max(1, len(closed) * _infer_step_ms(closed))
    quote = float(_numeric_column(closed, "quote_volume", fallback=_numeric_column(closed, "close") * _numeric_column(closed, "volume")).sum())
    trades = float(_numeric_column(closed, "number_of_trades", fallback=pd.Series(np.nan, index=closed.index)).sum())
    expected_quote = baseline_quote * duration_ms / htf_ms
    expected_trades = baseline_trades * duration_ms / htf_ms
    first_open = float(closed.iloc[0]["open"])
    last_close = float(closed.iloc[-1]["close"])
    first_half = closed.head(max(1, len(closed) // 2))
    second_half = closed.tail(len(closed) - len(first_half))
    first_quote = float(_numeric_column(first_half, "quote_volume", fallback=_numeric_column(first_half, "close") * _numeric_column(first_half, "volume")).sum())
    second_quote = float(_numeric_column(second_half, "quote_volume", fallback=_numeric_column(second_half, "close") * _numeric_column(second_half, "volume")).sum())
    first_trades = float(_numeric_column(first_half, "number_of_trades", fallback=pd.Series(np.nan, index=first_half.index)).sum())
    second_trades = float(_numeric_column(second_half, "number_of_trades", fallback=pd.Series(np.nan, index=second_half.index)).sum())
    taker_quote = _numeric_column(closed, "taker_buy_quote_volume", fallback=pd.Series(np.nan, index=closed.index)).sum()
    return {
        "ltf_confirm_return_pct": _safe_divide(last_close - first_open, first_open),
        "ltf_quote_volume": quote,
        "ltf_number_of_trades": trades,
        "ltf_quote_pace_ratio": _safe_divide(quote, expected_quote),
        "ltf_trade_pace_ratio": _safe_divide(trades, expected_trades),
        "ltf_second_half_return_pct": _safe_divide(float(second_half.iloc[-1]["close"]) - float(second_half.iloc[0]["open"]), float(second_half.iloc[0]["open"])) if not second_half.empty else float("nan"),
        "ltf_quote_acceleration": _safe_divide(second_quote, first_quote),
        "ltf_trade_acceleration": _safe_divide(second_trades, first_trades),
        "ltf_taker_buy_quote_share": _safe_divide(float(taker_quote), quote),
    }


def _setup_nature(
    *,
    anomaly_gate: bool,
    dormancy_ok: bool,
    smooth_price_ok: bool,
    oi_ok: bool,
    label: dict[str, object],
    quote_ratio: float,
    trade_ratio: float,
) -> str:
    if not anomaly_gate:
        return "not_htf_anomaly"
    if dormancy_ok and smooth_price_ok and oi_ok:
        return "dormant_smooth_price_oi_wakeup"
    if dormancy_ok and smooth_price_ok:
        return "dormant_smooth_price_oi_missing_or_down"
    if quote_ratio >= 20.0 and trade_ratio >= 10.0 and not smooth_price_ok:
        return "violent_flow_spike_no_smooth_pregrowth"
    if label.get("anomaly_low_broken_before_runner"):
        return "anomaly_low_broken_after_wakeup"
    return "generic_htf_flow_wakeup"


def _load_frame(
    storage: ParquetStorage,
    symbol: str,
    timeframe: str,
    *,
    start_ms: int,
    end_ms: int,
) -> pd.DataFrame:
    try:
        result = storage.load_window_result(
            symbol,
            Timeframe(str(timeframe)),
            start_timestamp_ms=int(start_ms),
            end_timestamp_ms=int(end_ms),
        )
    except Exception:
        return pd.DataFrame()
    if not result.ok or result.frame.empty:
        return pd.DataFrame()
    frame = result.frame.copy()
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        return pd.DataFrame()
    frame["timestamp"] = pd.to_numeric(frame["timestamp"], errors="coerce")
    frame = frame.dropna(subset=["timestamp"]).copy()
    for column in ("open", "high", "low", "close", "volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)


def _load_oi_frame(
    storage: ParquetStorage,
    symbol: str,
    *,
    config: HtfLtfRunnerDiscoveryConfig,
    start_ms: int,
    end_ms: int,
) -> pd.DataFrame:
    frame = _load_frame(storage, symbol, "5m", start_ms=start_ms - 24 * 60 * 60 * 1000, end_ms=end_ms)
    if frame.empty or "open_interest" not in frame.columns:
        return pd.DataFrame()
    oi = frame.loc[frame["open_interest"].notna(), ["timestamp", "open_interest"]].copy()
    if oi.empty:
        return pd.DataFrame()
    oi["available_timestamp_ms"] = oi["timestamp"].astype("int64") + _timeframe_ms("5m")
    return oi.reset_index(drop=True)


def _oi_asof(oi: pd.DataFrame, decision_ms: int) -> dict[str, object]:
    if oi.empty or "available_timestamp_ms" not in oi.columns:
        return {"status": "missing", "timestamp_ms": float("nan"), "available_timestamp_ms": float("nan"), "open_interest": float("nan")}
    available = pd.to_numeric(oi["available_timestamp_ms"], errors="coerce")
    rows = oi.loc[available <= int(decision_ms)]
    if rows.empty:
        return {"status": "missing", "timestamp_ms": float("nan"), "available_timestamp_ms": float("nan"), "open_interest": float("nan")}
    row = rows.iloc[-1]
    return {
        "status": "ok",
        "timestamp_ms": int(row["timestamp"]),
        "available_timestamp_ms": int(row["available_timestamp_ms"]),
        "open_interest": float(row["open_interest"]),
    }


def _data_quality_row(
    *,
    symbol: str,
    htf: pd.DataFrame,
    ltf: pd.DataFrame,
    oi: pd.DataFrame,
    config: HtfLtfRunnerDiscoveryConfig,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "htf_timeframe": config.htf_timeframe,
        "ltf_timeframe": config.ltf_timeframe,
        "htf_rows": int(len(htf)),
        "ltf_rows": int(len(ltf)),
        "oi_rows": int(len(oi)),
        "htf_quote_volume_source": "quote_volume" if "quote_volume" in htf.columns else "close_times_volume_proxy",
        "htf_trade_count_source": "number_of_trades" if "number_of_trades" in htf.columns else "missing",
        "ltf_quote_volume_source": "quote_volume" if "quote_volume" in ltf.columns else "close_times_volume_proxy",
        "ltf_trade_count_source": "number_of_trades" if "number_of_trades" in ltf.columns else "missing",
        "taker_buy_quote_source": "taker_buy_quote_volume" if "taker_buy_quote_volume" in ltf.columns else "missing",
        "oi_source": "cached_5m_open_interest" if not oi.empty else "missing",
    }


def _resolve_symbols(cache_dir: Path, timeframe: str, symbols: Iterable[str] | None) -> list[str]:
    if symbols:
        return sorted({_canonical_futures_symbol(str(symbol)) for symbol in symbols if str(symbol).strip()})
    result: list[str] = []
    for path in sorted(cache_dir.iterdir()) if cache_dir.exists() else []:
        if not path.is_dir():
            continue
        if (path / timeframe / "data.parquet").exists():
            result.append(ParquetStorage.decode_symbol_from_path(path.name))
    return result


def _canonical_futures_symbol(symbol: str) -> str:
    normalized = str(symbol).strip().upper()
    if not normalized:
        return ""
    if ":" in normalized:
        return normalized
    if "/" in normalized:
        return f"{normalized}:USDT" if normalized.endswith("/USDT") else normalized
    if normalized.endswith("USDT") and len(normalized) > 4:
        return f"{normalized[:-4]}/USDT:USDT"
    return normalized


def _resolve_end_timestamp_ms(
    config: HtfLtfRunnerDiscoveryConfig,
    storage: ParquetStorage,
    symbols: tuple[str, ...],
) -> int:
    if config.end_timestamp_ms is not None:
        return int(config.end_timestamp_ms)
    max_ts = 0
    timeframe = Timeframe(str(config.htf_timeframe))
    for symbol in symbols:
        try:
            last_ts = storage.get_last_timestamp(symbol, timeframe)
        except Exception:
            last_ts = None
        if last_ts is not None:
            max_ts = max(max_ts, int(last_ts))
    if max_ts <= 0:
        raise ValueError("no cached HTF data found")
    return max_ts


def _numeric_column(frame: pd.DataFrame, column: str, *, fallback: pd.Series | None = None) -> pd.Series:
    if column in frame.columns:
        return pd.to_numeric(frame[column], errors="coerce")
    if fallback is not None:
        return pd.to_numeric(fallback, errors="coerce")
    return pd.Series(np.nan, index=frame.index)


def _positive_median(values: pd.Series) -> float:
    series = pd.to_numeric(values, errors="coerce")
    series = series.loc[series > 0].dropna()
    return float(series.median()) if not series.empty else float("nan")


def _safe_divide(numerator: float, denominator: float) -> float:
    try:
        numerator = float(numerator)
        denominator = float(denominator)
    except (TypeError, ValueError):
        return float("nan")
    if not np.isfinite(numerator) or not np.isfinite(denominator) or denominator == 0.0:
        return float("nan")
    return numerator / denominator


def _timeframe_ms(value: str) -> int:
    return Timeframe(str(value)).to_milliseconds()


def _infer_step_ms(frame: pd.DataFrame) -> int:
    if len(frame) < 2:
        return 1
    diffs = pd.to_numeric(frame["timestamp"], errors="coerce").dropna().diff().dropna()
    if diffs.empty:
        return 1
    return max(1, int(diffs.median()))


def _timestamp_to_utc(timestamp_ms: object) -> str:
    try:
        parsed = int(float(timestamp_ms))
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(parsed):
        return ""
    return pd.to_datetime(parsed, unit="ms", utc=True).isoformat()


def _apply_live_portfolio_filter(trades: pd.DataFrame, *, max_open_positions: int) -> pd.DataFrame:
    if trades.empty or "status" not in trades.columns:
        return trades.copy()
    closed = trades.copy()
    closed["_order"] = np.arange(len(closed))
    closed["entry_timestamp_ms"] = pd.to_numeric(closed["entry_timestamp_ms"], errors="coerce")
    closed["exit_timestamp_ms"] = pd.to_numeric(closed["exit_timestamp_ms"], errors="coerce")
    closed.sort_values(["entry_timestamp_ms", "decision_timestamp_ms", "symbol", "_order"], inplace=True)
    open_positions: list[tuple[str, int]] = []
    rows: list[pd.Series] = []
    for _, row in closed.iterrows():
        if row.get("status") != "closed":
            rows.append(row)
            continue
        entry_ts = int(row["entry_timestamp_ms"])
        exit_ts = int(row["exit_timestamp_ms"])
        symbol = str(row["symbol"])
        open_positions = [(s, e) for s, e in open_positions if e >= entry_ts]
        skip_reason = ""
        if any(s == symbol for s, _ in open_positions):
            skip_reason = "live_portfolio_filter_overlapping_symbol_position_at_entry"
        elif len(open_positions) >= int(max_open_positions):
            skip_reason = "live_portfolio_filter_max_open_positions_at_entry"
        if skip_reason:
            skipped = row.copy()
            skipped["status"] = "skipped"
            skipped["skip_reason"] = skip_reason
            skipped["execution_guard"] = False
            skipped["would_have_net_return"] = row.get("net_return", float("nan"))
            rows.append(skipped)
        else:
            kept = row.copy()
            kept["portfolio_open_positions_at_entry"] = len(open_positions)
            rows.append(kept)
            open_positions.append((symbol, exit_ts))
    result = pd.DataFrame(rows).sort_values("_order").drop(columns=["_order"], errors="ignore")
    return result.reset_index(drop=True)


def _summarize_trades(trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if trades.empty or "status" not in trades.columns:
        return pd.DataFrame([{"metric": "closed_trades", "value": 0}, {"metric": "skipped_trades", "value": 0}])
    closed = trades.loc[trades["status"].eq("closed")].copy()
    skipped = trades.loc[trades["status"].eq("skipped")].copy()
    rows.extend(
        [
            {"metric": "closed_trades", "value": int(len(closed))},
            {"metric": "skipped_trades", "value": int(len(skipped))},
        ]
    )
    if closed.empty:
        return pd.DataFrame(rows)
    net = pd.to_numeric(closed["net_return"], errors="coerce")
    rows.extend(
        [
            {"metric": "symbols", "value": int(closed["symbol"].nunique())},
            {"metric": "win_rate", "value": float((net > 0).mean())},
            {"metric": "avg_net_return", "value": float(net.mean())},
            {"metric": "median_net_return", "value": float(net.median())},
            {"metric": "sum_net_return", "value": float(net.sum())},
            {"metric": "avg_gross_r", "value": float(pd.to_numeric(closed["gross_r"], errors="coerce").mean())},
            {"metric": "median_gross_r", "value": float(pd.to_numeric(closed["gross_r"], errors="coerce").median())},
            {"metric": "avg_mfe_pct", "value": float(pd.to_numeric(closed["mfe_pct"], errors="coerce").mean())},
            {"metric": "avg_mae_pct", "value": float(pd.to_numeric(closed["mae_pct"], errors="coerce").mean())},
            {"metric": "runner_10pct_label_share", "value": float(closed["runner_10pct_next_hour"].astype(bool).mean()) if "runner_10pct_next_hour" in closed.columns else 0.0},
            {"metric": "clean_runner_label_share", "value": float(closed["clean_runner_without_low_break"].astype(bool).mean()) if "clean_runner_without_low_break" in closed.columns else 0.0},
        ]
    )
    for reason, count in closed["exit_reason"].astype(str).value_counts().items():
        rows.append({"metric": f"exit_reason:{reason}", "value": int(count)})
    return pd.DataFrame(rows)


def _metric_map(summary: pd.DataFrame) -> dict[str, object]:
    if summary.empty:
        return {}
    return {str(row["metric"]): row["value"] for _, row in summary.iterrows()}


def _summarize_by_column(trades: pd.DataFrame, column: str) -> pd.DataFrame:
    if trades.empty or column not in trades.columns or "status" not in trades.columns:
        return pd.DataFrame(columns=[column, "closed_trades", "win_rate", "avg_net_return", "sum_net_return"])
    closed = trades.loc[trades["status"].eq("closed")].copy()
    if closed.empty:
        return pd.DataFrame(columns=[column, "closed_trades", "win_rate", "avg_net_return", "sum_net_return"])
    closed["net_return"] = pd.to_numeric(closed["net_return"], errors="coerce")
    grouped = closed.assign(win=closed["net_return"] > 0).groupby(column, dropna=False).agg(
        closed_trades=("net_return", "size"),
        win_rate=("win", "mean"),
        avg_net_return=("net_return", "mean"),
        median_net_return=("net_return", "median"),
        sum_net_return=("net_return", "sum"),
        runner_10pct_label_share=("runner_10pct_next_hour", "mean"),
        clean_runner_label_share=("clean_runner_without_low_break", "mean"),
    )
    return grouped.reset_index().sort_values(["sum_net_return", "closed_trades"], ascending=[False, False])


def _label_distribution(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame(columns=["label", "count"])
    rows = []
    for column in ("runner_10pct_next_hour", "clean_runner_without_low_break", "anomaly_low_broken_before_runner", "setup_nature"):
        if column not in candidates.columns:
            continue
        for value, count in candidates[column].value_counts(dropna=False).items():
            rows.append({"label": column, "value": value, "count": int(count), "share": float(count / len(candidates))})
    return pd.DataFrame(rows)


def _build_funnel(candidates: pd.DataFrame, signals: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    rows = [
        {"stage": "htf_rows_after_scan", "count": int(len(candidates))},
        {"stage": "htf_anomaly_gate_ok", "count": int(candidates["htf_anomaly_gate"].astype(bool).sum()) if "htf_anomaly_gate" in candidates.columns else 0},
        {"stage": "runner_10pct_next_hour_labels", "count": int(candidates["runner_10pct_next_hour"].astype(bool).sum()) if "runner_10pct_next_hour" in candidates.columns else 0},
        {"stage": "clean_runner_without_low_break_labels", "count": int(candidates["clean_runner_without_low_break"].astype(bool).sum()) if "clean_runner_without_low_break" in candidates.columns else 0},
        {"stage": "ltf_signals_selected", "count": int(len(signals))},
        {"stage": "closed_trades", "count": int(trades["status"].eq("closed").sum()) if "status" in trades.columns else 0},
        {"stage": "skipped_trades", "count": int(trades["status"].eq("skipped").sum()) if "status" in trades.columns else 0},
    ]
    return pd.DataFrame(rows)


def _skip_reasons(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "skip_reason" not in trades.columns:
        return pd.DataFrame(columns=["skip_reason", "count"])
    skipped = trades.loc[trades["status"].eq("skipped")]
    if skipped.empty:
        return pd.DataFrame(columns=["skip_reason", "count"])
    return skipped["skip_reason"].value_counts().rename_axis("skip_reason").reset_index(name="count")


def _top_dependency(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "status" not in trades.columns:
        return pd.DataFrame(columns=["scope", "closed_trades", "sum_net_return", "top20pct_positive_share"])
    closed = trades.loc[trades["status"].eq("closed")].copy()
    if closed.empty:
        return pd.DataFrame(columns=["scope", "closed_trades", "sum_net_return", "top20pct_positive_share"])
    net = pd.to_numeric(closed["net_return"], errors="coerce").dropna().sort_values(ascending=False)
    positive = net.loc[net > 0]
    top_n = max(1, int(math.ceil(len(net) * 0.20)))
    positive_total = float(positive.sum())
    return pd.DataFrame(
        [
            {
                "scope": "all_closed",
                "closed_trades": int(len(net)),
                "sum_net_return": float(net.sum()),
                "top20pct_sum_net_return": float(net.head(top_n).sum()),
                "top20pct_positive_share": float(net.head(top_n).loc[net.head(top_n) > 0].sum() / positive_total) if positive_total > 0 else float("inf"),
                "top20pct_trade_count": int(top_n),
            }
        ]
    )


def _honesty_report(config: HtfLtfRunnerDiscoveryConfig) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "check": "future_label_separation",
                "status": "ok",
                "detail": "runner_10pct_next_hour and low-break labels are written after the event scan and are not used to select entry.",
            },
            {
                "check": "entry_availability",
                "status": "ok",
                "detail": "entry is the next LTF open after the closed confirmation candle; no entry inside the confirming candle.",
            },
            {
                "check": "exit_model",
                "status": "ok",
                "detail": "no TP is simulated; exits are structural stop, structural trailing stop, or max-hold time exit.",
            },
            {
                "check": "oi_availability",
                "status": "ok",
                "detail": "OI is used only from cached 5m rows whose timestamp plus 5m availability is <= decision time.",
            },
            {
                "check": "costs",
                "status": "ok",
                "detail": f"entry_slippage={config.entry_slippage_pct}; exit_slippage={config.exit_slippage_pct}; round_trip_fee={2 * config.fee_rate}.",
            },
        ]
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run HTF/LTF runner discovery backtest.")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    parser.add_argument("--output-dir", default=str(Path(DEFAULT_RESULTS_DIR) / "htf_ltf_runner_discovery"))
    parser.add_argument("--htf-timeframe", default="1m")
    parser.add_argument("--ltf-timeframe", default="5s")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--end-timestamp-ms", type=int, default=None)
    parser.add_argument("--runner-target-return-pct", type=float, default=0.10)
    parser.add_argument("--runner-horizon-minutes", type=int, default=60)
    args = parser.parse_args(argv)
    config = HtfLtfRunnerDiscoveryConfig(
        cache_dir=Path(args.cache_dir),
        output_dir=Path(args.output_dir),
        htf_timeframe=str(args.htf_timeframe),
        ltf_timeframe=str(args.ltf_timeframe),
        days=int(args.days),
        end_timestamp_ms=args.end_timestamp_ms,
        runner_target_return_pct=float(args.runner_target_return_pct),
        runner_horizon_minutes=int(args.runner_horizon_minutes),
    )
    run_htf_ltf_runner_discovery(config, symbols=args.symbols)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
