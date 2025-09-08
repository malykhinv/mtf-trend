from __future__ import annotations

from domain.models import metrics as M, signals as S
from domain.models.config import EntryParams, ProfileConfig, TriggerParams
from domain.models.enums import Side

from .symbol_registry import SymbolRegistry


def _taker_ratio(metrics: M.SymbolMetrics) -> float:
    taker_buy = metrics.taker_buy_volume
    taker_sell = metrics.taker_sell_volume
    if taker_sell > 0:
        return taker_buy / taker_sell
    return float("inf") if taker_buy > 0 else 0.0


def _meets_trigger(metrics: M.SymbolMetrics, trig: TriggerParams) -> bool:
    return (
        metrics.z_px >= trig.z_px
        and metrics.z_vol >= trig.z_vol
        and metrics.delta_price_sigma_mult >= trig.delta_price_sigma_mult
        and metrics.delta_price_abs_pct >= trig.delta_price_abs_pct
        and metrics.close_pos >= trig.close_pos
        and metrics.liqs_z >= trig.liqs_z
    )


def _evaluate_entry(metrics: M.SymbolMetrics, entry_cfg: EntryParams) -> bool:
    low_break = metrics.low_break
    avwap_loss = metrics.avwap_loss
    if entry_cfg.require_both:
        return low_break and avwap_loss
    return (
        (entry_cfg.allow_low_break and low_break)
        or (entry_cfg.allow_avwap_loss and avwap_loss)
    )


class SignalEngine:
    """Very small placeholder signal engine."""

    def __init__(self, config: ProfileConfig, registry: SymbolRegistry) -> None:
        self._config = config
        # ``SignalEngine`` needs access to the registry in order to retrieve
        # the latest metrics for every symbol.
        self._registry = registry

    def on_minute_close(self, symbol: str) -> S.PumpSignal | None:
        """Evaluate minute metrics and possibly emit a pump signal."""

        state = self._registry.get(symbol)
        metrics = state.metrics

        trig = self._config.trigger

        if not _meets_trigger(metrics, trig):
            return None

        window = M.PumpWindow(
            high=metrics.high,
            low=metrics.low,
            start_ts=int(metrics.start_ts),
            end_ts=int(metrics.end_ts),
        )
        return S.PumpSignal(symbol=symbol, window=window)

    def confirm_failure(self, symbol: str, window: M.PumpWindow) -> bool:
        """Determine whether the pump window should be rejected."""

        state = self._registry.get(symbol)
        metrics = state.metrics

        confirm = self._config.confirmation

        if metrics.delta_oi_pct > confirm.delta_oi_max_pct:
            return True

        taker_ratio = _taker_ratio(metrics)
        if taker_ratio > confirm.taker_ratio_max:
            return True

        if metrics.premium_pct > confirm.premium_max_pct:
            return True

        if metrics.latency_sec < confirm.latency_min_sec:
            return True

        lob_ok = (
            metrics.ask_imb >= confirm.ask_imb_min
            or metrics.top5ask_vs_base >= confirm.top5ask_vs_base_min
        )
        if not lob_ok:
            return True

        if (
            metrics.cvd_gap_pct < confirm.cvd_gap_pct_min
            or metrics.cvd_gap_sec < confirm.cvd_gap_sec_min
        ):
            return True

        return False

    def make_entry(self, symbol: str, window: M.PumpWindow) -> S.EntrySignal | None:
        """Evaluate entry conditions and possibly emit an entry signal."""

        state = self._registry.get(symbol)
        metrics = state.metrics

        if window.high <= window.low:
            return None

        if metrics.premium_pct > self._config.confirmation.premium_max_pct:
            return None

        entry_cfg = self._config.entry

        if not _evaluate_entry(metrics, entry_cfg):
            return None

        direction: Side = (
            metrics.direction if metrics.direction is not None else Side.SHORT
        )

        if metrics.entry_price > 0:
            price = metrics.entry_price
        else:
            if direction is Side.SHORT:
                price = metrics.best_bid or window.low
            else:
                price = metrics.best_ask or window.high

        return S.EntrySignal(symbol=symbol, side=direction, price=price)
