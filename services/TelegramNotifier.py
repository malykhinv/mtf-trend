from typing import Protocol

import requests

from utils.logger import log


class TelegramNotifier(Protocol):

    """
    Класс отправки сообщений и изображений в Telegram.
    """
    def __init__(self, token: str, chat_id: str) -> None:
        """Создаёт объект отправки сообщений в Telegram."""
        self.token: str = token
        self.chat_id: str = chat_id


    async def send_message(self, text: str, image_path: str = None) -> int | None:
        """Отправляет текст и картинку в Telegram."""
        if not self.token or not self.chat_id:
            log("Отсутствуют данные Telegram. Сообщение не отправлено.")
            return None
        if image_path:
            url: str = f"https://api.telegram.org/bot{self.token}/sendPhoto"
            with open(image_path, "rb") as image_file:
                payload = {
                    "chat_id": self.chat_id,
                    "caption": text,
                    "parse_mode": "Markdown"
                }
                files = {
                    "photo": image_file
                }
                try:
                    response = requests.post(url, data=payload, files=files)
                    response.raise_for_status()
                    result = response.json().get("result", {})
                    log("Сообщение с изображением успешно отправлено.")
                    if isinstance(result, dict):
                        msg_id = result.get("message_id")
                        log(f"Получен message_id: {msg_id}")
                except Exception as error:
                    log(f"Ошибка при отправке изображения с сообщением: {error}")
                    return None
        else:
            url: str = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "Markdown"
            }
            try:
                response = requests.post(url, json=payload)
                response.raise_for_status()
                result = response.json().get("result", {})
                log("Сообщение успешно отправлено.")
                if isinstance(result, dict):
                    msg_id = result.get("message_id")
                    log(f"Получен message_id: {msg_id}")
            except Exception as error:
                log(f"Ошибка при отправке сообщения: {error}")
                return None

        if isinstance(result, dict):
            return result.get("message_id")
        return None