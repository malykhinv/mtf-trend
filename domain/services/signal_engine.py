from __future__ import annotations

import logging

from domain.models import metrics as M, signals as S
from domain.models.config import EntryParams, ProfileConfig, TriggerParams
from domain.models.enums import Side

from .symbol_registry import SymbolRegistry

logger = logging.getLogger(__name__)


def _taker_ratio(metrics: M.SymbolMetrics) -> float:
    taker_buy = metrics.taker_buy_volume
    taker_sell = metrics.taker_sell_volume
    if taker_sell > 0:
        return taker_buy / taker_sell
    return float("inf") if taker_buy > 0 else 0.0


def _meets_trigger(metrics: M.SymbolMetrics, trig: TriggerParams) -> tuple[bool, str]:
    if metrics.z_px < trig.z_px:
        return False, f"z_px {metrics.z_px:.3f} < {trig.z_px:.3f}"
    if metrics.z_vol < trig.z_vol:
        return False, f"z_vol {metrics.z_vol:.3f} < {trig.z_vol:.3f}"
    if metrics.delta_price_sigma_mult < trig.delta_price_sigma_mult:
        return (
            False,
            "delta_price_sigma_mult %.3f < %.3f"
            % (metrics.delta_price_sigma_mult, trig.delta_price_sigma_mult),
        )
    if metrics.delta_price_abs_pct < trig.delta_price_abs_pct:
        return (
            False,
            "delta_price_abs_pct %.3f < %.3f"
            % (metrics.delta_price_abs_pct, trig.delta_price_abs_pct),
        )
    if metrics.close_pos < trig.close_pos:
        return False, f"close_pos {metrics.close_pos:.3f} < {trig.close_pos:.3f}"
    if metrics.liqs_z < trig.liqs_z:
        return False, f"liqs_z {metrics.liqs_z:.3f} < {trig.liqs_z:.3f}"
    return True, ""


def _evaluate_entry(metrics: M.SymbolMetrics, entry_cfg: EntryParams) -> bool:
    low_break = metrics.low_break
    avwap_loss = metrics.avwap_loss
    if entry_cfg.require_both:
        return low_break and avwap_loss
    return (entry_cfg.allow_low_break and low_break) or (
        entry_cfg.allow_avwap_loss and avwap_loss
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
        logger.debug("Checking minute close for %s", symbol)

        try:
            state = self._registry.get(symbol)
            metrics = state.metrics

            trig = self._config.trigger

            ok, reason = _meets_trigger(metrics, trig)
            if not ok:
                logger.debug("%s pump skipped: %s", symbol, reason)
                return None

            window = M.PumpWindow(
                high=metrics.high,
                low=metrics.low,
                start_ts=int(metrics.start_ts),
                end_ts=int(metrics.end_ts),
            )
            logger.info(f"{symbol}: обнаружена аномалия, окно {window}")
            return S.PumpSignal(symbol=symbol, window=window)
        except Exception:
            logger.exception("Ошибка on_minute_close %s :>", symbol)
            raise

    def confirm_failure(self, symbol: str) -> bool:
        """Determine whether the pump window should be rejected."""
        try:
            state = self._registry.get(symbol)
            metrics = state.metrics

            confirm = self._config.confirmation

            if metrics.delta_oi_pct > confirm.delta_oi_max_pct:
                logger.info(
                    f"{symbol}: сигнал отклонён — delta_oi_pct {metrics.delta_oi_pct:.3f} > {confirm.delta_oi_max_pct:.3f}"
                )
                return True

            taker_ratio = _taker_ratio(metrics)
            if taker_ratio > confirm.taker_ratio_max:
                logger.info(
                    f"{symbol}: сигнал отклонён — taker_ratio {taker_ratio:.3f} > {confirm.taker_ratio_max:.3f}"
                )
                return True

            if metrics.premium_pct > confirm.premium_max_pct:
                logger.info(
                    f"{symbol}: сигнал отклонён — premium_pct {metrics.premium_pct:.3f} > {confirm.premium_max_pct:.3f}"
                )
                return True

            if metrics.latency_sec < confirm.latency_min_sec:
                logger.info(
                    f"{symbol}: сигнал отклонён — latency_sec {metrics.latency_sec:.3f} < {confirm.latency_min_sec:.3f}"
                )
                return True

            lob_ok = (
                metrics.ask_imb >= confirm.ask_imb_min
                or metrics.top5ask_vs_base >= confirm.top5ask_vs_base_min
            )
            if not lob_ok:
                logger.info(f"{symbol}: сигнал отклонён — условия LOB не выполнены")
                return True

            if (
                metrics.cvd_gap_pct < confirm.cvd_gap_pct_min
                or metrics.cvd_gap_sec < confirm.cvd_gap_sec_min
            ):
                logger.info(
                    f"{symbol}: сигнал отклонён — cvd_gap {metrics.cvd_gap_pct:.3f}/{metrics.cvd_gap_sec:.3f} < {confirm.cvd_gap_pct_min:.3f}/{confirm.cvd_gap_sec_min:.3f}"
                )
                return True

            logger.info(f"{symbol}: сигнал подтверждён")
            return False
        except Exception:
            logger.exception("Ошибка confirm_failure %s :>", symbol)
            raise

    def make_entry(self, symbol: str, window: M.PumpWindow) -> S.EntrySignal | None:
        """Evaluate entry conditions and possibly emit an entry signal."""
        try:
            state = self._registry.get(symbol)
            metrics = state.metrics

            if window.high <= window.low:
                logger.info(f"{symbol}: сигнал отклонён — некорректное окно")
                return None

            if metrics.premium_pct > self._config.confirmation.premium_max_pct:
                logger.info(
                    f"{symbol}: сигнал отклонён — premium_pct {metrics.premium_pct:.3f} > {self._config.confirmation.premium_max_pct:.3f}"
                )
                return None

            entry_cfg = self._config.entry

            if not _evaluate_entry(metrics, entry_cfg):
                logger.info(f"{symbol}: сигнал отклонён — условия входа не выполнены")
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
        except Exception:
            logger.exception("Ошибка make_entry %s :>", symbol)
            raise
