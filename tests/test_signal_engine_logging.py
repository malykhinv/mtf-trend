import logging
import time

from domain.services.signal_engine import SignalEngine, SymbolRegistry, _format_condition
from domain.models.state import SymbolState
from domain.models.metrics import SymbolMetrics
from domain.models.enums import BotState, Profile
from domain.models.config import (
    TriggerParams,
    ConfirmationParams,
    EntryParams,
    RiskParams,
    ProfileConfig,
)


def _make_config() -> ProfileConfig:
    trigger = TriggerParams(
        z_px=1.0,
        z_vol=1.0,
        delta_price_sigma_mult=1.0,
        delta_price_abs_pct=1.0,
        upper_wick_body_ratio_max=0.8,
    )
    confirm = ConfirmationParams(
        delta_oi_max_pct=1.0,
        taker_ratio_max=1.0,
        premium_max_pct=1.0,
        latency_min_sec=0,
        ask_imb_min=0.0,
        top5ask_vs_base_min=0.0,
        cvd_gap_pct_min=0.0,
        cvd_gap_sec_min=0,
    )
    entry = EntryParams(
        allow_low_break=False,
        allow_avwap_loss=False,
        require_both=False,
    )
    risk = RiskParams(
        stop_abs_pct=0.0,
        stop_sigma_mult=0.0,
        tp1_pct=0.0,
        tp2_pct=0.0,
        tail_pct=0.0,
        trail_abs_pct=0.0,
        trail_sigma_mult=0.0,
    )
    return ProfileConfig(
        profile=Profile.ACTIVE,
        trigger=trigger,
        confirmation=confirm,
        entry=entry,
        risk=risk,
        max_margin_usdt=0.0,
        risk_per_trade_pct=0.0,
    )


def test_partial_condition_logging(caplog):
    registry = SymbolRegistry()
    metrics = SymbolMetrics(
        z_px=2.0,
        z_vol=0.5,
        delta_price_sigma_mult=1.1,
        delta_price_abs_pct=1.2,
        upper_wick_body_ratio=0.5,
        end_ts=int(time.time() * 1000),
    )
    state = SymbolState(symbol="XYZ", state=BotState.WATCHING, metrics=metrics)
    registry.put(state)

    engine = SignalEngine(_make_config(), registry)
    with caplog.at_level(logging.DEBUG):
        engine.on_minute_close("XYZ")

    info_record = next(
        r for r in caplog.records if r.levelno == logging.INFO and "частичная аномалия" in r.message
    )
    debug_record = next(
        r for r in caplog.records if r.levelno == logging.DEBUG and "частичная аномалия" in r.message
    )

    trig = engine._config.trigger
    assert _format_condition("delta_price_abs_pct", metrics, trig) in info_record.message
    assert _format_condition("z_px", metrics, trig) in info_record.message
    assert _format_condition("delta_price_sigma_mult", metrics, trig) in debug_record.message
    assert _format_condition("upper_wick_body_ratio", metrics, trig) in debug_record.message


def test_partial_logging_rate_limit(monkeypatch, caplog):
    registry = SymbolRegistry()
    metrics = SymbolMetrics(
        z_px=2.0,
        z_vol=0.5,
        delta_price_sigma_mult=1.1,
        delta_price_abs_pct=1.2,
        upper_wick_body_ratio=0.5,
        end_ts=0,
    )
    state = SymbolState(symbol="XYZ", state=BotState.WATCHING, metrics=metrics)
    registry.put(state)

    engine = SignalEngine(_make_config(), registry)

    current_time = 1_000.0

    def fake_time():
        return current_time

    monkeypatch.setattr(time, "time", fake_time)

    with caplog.at_level(logging.INFO):
        metrics.end_ts = int(current_time * 1000)
        engine.on_minute_close("XYZ")
        current_time += 30
        metrics.end_ts = int(current_time * 1000)
        engine.on_minute_close("XYZ")
        current_time += 31
        metrics.end_ts = int(current_time * 1000)
        engine.on_minute_close("XYZ")

    records = [
        r
        for r in caplog.records
        if r.levelno == logging.INFO and "частичная аномалия" in r.message
    ]
    assert len(records) == 2
