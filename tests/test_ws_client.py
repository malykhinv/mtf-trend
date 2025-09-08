import asyncio
import pathlib
import sys

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

from infrastructure.binance.ws_client import WsClient


def test_handle_depth_accepts_short_keys():
    client = WsClient()
    payload = {
        "s": "BTCUSDT",
        "E": 123,
        "b": [["1.0", "2.0"]],
        "a": [["1.1", "3.0"]],
    }

    async def run_scenario():
        await client._handle_depth(payload)
        return await client.next_depth()

    depth = asyncio.run(run_scenario())
    assert depth.symbol == "BTCUSDT"
    assert depth.timestamp == 123
    assert depth.bids == ((1.0, 2.0),)
    assert depth.asks == ((1.1, 3.0),)
