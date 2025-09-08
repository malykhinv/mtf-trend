from __future__ import annotations

import asyncio
import requests


async def send_message(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        r = await asyncio.to_thread(
            requests.post, url, json=payload, timeout=10.0
        )
        r.raise_for_status()
        data = r.json()
        return bool(data.get("ok", False)), data.get("description", "")
    except requests.RequestException as exc:  # pragma: no cover - network
        return False, str(exc)
    except ValueError as exc:  # pragma: no cover - network
        return False, f"Invalid response: {exc}"
