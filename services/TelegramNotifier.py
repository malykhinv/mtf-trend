# services/TelegramNotifier.py
from __future__ import annotations

import os
from typing import Optional

import aiohttp

from utils.logger import log


class TelegramNotifier:
    """
    Отправка сообщений и изображений в Telegram без блокировки event loop.
    Использует aiohttp. Возвращает message_id или None.
    """

    def __init__(self, token: Optional[str], chat_id: Optional[str]) -> None:
        self.token: str = token or ""
        self.chat_id: str = chat_id or ""

    async def send_message(self, text: str, image_path: str | None = None) -> int | None:
        if not self.token or not self.chat_id:
            log("Отсутствуют данные Telegram. Сообщение не отправлено.")
            return None

        if image_path:
            url = f"https://api.telegram.org/bot{self.token}/sendPhoto"
            try:
                async with aiohttp.ClientSession() as session:
                    form = aiohttp.FormData()
                    form.add_field("chat_id", self.chat_id)
                    form.add_field("caption", text)
                    form.add_field("parse_mode", "Markdown")

                    filename = os.path.basename(image_path)
                    # Чтение файла синхронное (незначительное I/O), сетевой вызов — async
                    with open(image_path, "rb") as f:
                        form.add_field("photo", f, filename=filename)

                        async with session.post(url, data=form) as resp:
                            resp.raise_for_status()
                            payload = await resp.json()
            except Exception as error:
                log(f"Ошибка при отправке изображения с сообщением: {error}")
                return None
        else:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"}
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload) as resp:
                        resp.raise_for_status()
                        payload = await resp.json()
            except Exception as error:
                log(f"Ошибка при отправке сообщения: {error}")
                return None

        result = payload.get("result") if isinstance(payload, dict) else None
        if isinstance(result, dict):
            msg_id = result.get("message_id")
            if msg_id is not None:
                log(f"Получен message_id: {msg_id}")
            return msg_id
        return None
