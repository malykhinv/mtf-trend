# notifier/telegram.py
import requests
from config.settings.credentials import TELEGRAM_ORDERS_BOT_TOKEN, TELEGRAM_BOT_CHAT_ID
from utils.logger import log


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str = TELEGRAM_BOT_CHAT_ID):
        self.token = token
        self.chat_id = chat_id

    def send_message(self, text: str) -> None:
        if not self.token or not self.chat_id:
            log("Отсутствуют данные Telegram. Сообщение не отправлено.")
            return

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }

        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            log("Сообщение успешно отправлено в Telegram.")
        except Exception as error:
            log(f"Ошибка при отправке в Telegram: {error}")
