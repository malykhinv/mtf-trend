# notifier/telegram.py
import requests
from config.credentials import TELEGRAM_BOT_CHAT_ID
from utils.logger import log


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str = TELEGRAM_BOT_CHAT_ID):
        self.token = token
        self.chat_id = chat_id

    def send_message(self, text: str, image_path: str = None) -> None:
        if not self.token or not self.chat_id:
            log("Отсутствуют данные Telegram. Сообщение не отправлено.")
            return

        if image_path:
            url = f"https://api.telegram.org/bot{self.token}/sendPhoto"
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
                    log("Сообщение с изображением успешно отправлено.")
                except Exception as error:
                    log(f"Ошибка при отправке изображения с сообщением: {error}")
                    raise
        else:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "Markdown"
            }
            try:
                response = requests.post(url, json=payload)
                response.raise_for_status()
                log("Сообщение успешно отправлено.")
            except Exception as error:
                log(f"Ошибка при отправке сообщения: {error}")
                raise
