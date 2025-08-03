import asyncio
import pathlib
import sys
from unittest.mock import Mock

import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

from utils import telegram


@pytest.mark.asyncio
async def test_run_background_logs_notification_exception(monkeypatch):
    async def failing_notify():
        raise RuntimeError("boom")

    mock_exc = Mock()
    monkeypatch.setattr(telegram.logger, "exception", mock_exc)

    telegram.run_background(failing_notify())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    mock_exc.assert_called_once_with("Background task failed")
