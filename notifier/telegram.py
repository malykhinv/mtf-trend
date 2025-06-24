# notifier/telegram.py
import requests
from config.settings.credentials import TELEGRAM_ORDERS_BOT_TOKEN, TELEGRAM_BOT_CHAT_ID


class TelegramNotifier:
    def __init__(self, token: str = TELEGRAM_ORDERS_BOT_TOKEN, chat_id: str = TELEGRAM_BOT_CHAT_ID):
        self.token = token
        self.chat_id = chat_id

    def send_message(self, text: str) -> None:
        if not self.token or not self.chat_id:
            print("[TELEGRAM] Missing credentials — message not sent.")
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
        except Exception as e:
            print(f"[TELEGRAM ERROR] {e}")
