import pathlib
import sys
from typing import Any, Dict

import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

from exchanges.binance import BinanceExchange  # noqa: E402


class DummyResponse:
    def __init__(self, data: Dict[str, Any], status: int = 200) -> None:
        self.data = data
        self.status = status

    async def json(self) -> Dict[str, Any]:
        return self.data

    async def text(self) -> str:  # pragma: no cover - used for errors
        return ""

    async def __aenter__(self) -> "DummyResponse":
        return self

    async def __aexit__(
        self, exc_type: Any, exc: Any, tb: Any
    ) -> None:  # pragma: no cover - no cleanup needed
        pass


class DummySession:
    @staticmethod
    def get(url: str, **_: Any) -> DummyResponse:
        if "ticker/24hr" in url:
            return DummyResponse({"quoteVolume": "500", "volume": "5"})
        if "openInterest" in url:
            return DummyResponse({"openInterest": "10"})
        return DummyResponse({})


@pytest.mark.asyncio
async def test_get_stats_uses_quote_volume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ex = BinanceExchange("key", "secret")
    dummy = DummySession()

    async def session_get() -> DummySession:
        return dummy

    monkeypatch.setattr(ex, "_session_get", session_get)

    stats = await ex.get_stats("BTCUSDT")
    assert stats["volume_24h"] == 500.0
    assert stats["open_interest"] == 10.0
