import asyncio
import logging
import pathlib
import sys

import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

import ai.parameter_optimizer as po


@pytest.mark.asyncio
async def test_periodic_optimization_read_error(monkeypatch, caplog):
    """Эмулирует ошибку чтения файла истории и проверяет повтор."""
    # simulate read_excel raising an error
    def _raise(*args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(po.pd, "read_excel", _raise)

    sleep_calls = []

    async def fake_sleep(delay):
        sleep_calls.append(delay)
        raise RuntimeError

    monkeypatch.setattr(po.asyncio, "sleep", fake_sleep)

    # ensure log file exists so read_excel is called
    dummy_log = pathlib.Path("dummy.xlsx")
    dummy_log.touch()

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError):
            await po.periodic_optimization(min_hours=1, max_hours=1, log_path=dummy_log)

    assert sleep_calls and sleep_calls[0] == po.RETRY_DELAY
    assert "Failed to optimize thresholds" in caplog.text
