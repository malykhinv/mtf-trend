import logging
import main


def test_warning_suppressed_within_cooldown(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    main._METRIC_WARNINGS.clear()

    fake_time = 1000.0

    def fake_time_func():
        return fake_time

    monkeypatch.setattr(main.time, "time", fake_time_func)

    main._log_incomplete_metrics("binance", "BTCUSDT", ["x"])
    main._log_incomplete_metrics("binance", "BTCUSDT", ["x"])

    warnings = [r.message for r in caplog.records if "Пропуск" in r.message]
    assert len(warnings) == 1
