import logging

from domain.ports.ws_client import WsClient

logger = logging.getLogger(__name__)


async def ws_stream(ws: WsClient) -> None:
    """Consume websocket stream indefinitely."""
    logger.info("ws_stream started")
    await ws.stream()
