from dataclasses import dataclass
from notifier.telegram import TelegramNotifier

@dataclass
class UpdateDetails:
    """Данные для обновления ранее отправленного сообщения."""
    message_id: int
    notifier: TelegramNotifier
    with_photo: bool
    text: str
    high: float
    low: float
    entry_price: float
    max_price: float
    min_price: float
