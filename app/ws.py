from domain.ports.ws_client import WsClient


async def ws_stream(ws: WsClient) -> None:
    """Consume websocket stream indefinitely."""
    await ws.stream()
