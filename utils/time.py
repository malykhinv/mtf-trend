from datetime import datetime

def ago(timestamp: datetime) -> str:
    delta = datetime.utcnow() - timestamp
    minutes = int(delta.total_seconds() / 60)
    if minutes < 1:
        return "только что"
    elif minutes == 1:
        return "1 минуту назад"
    elif minutes < 5:
        return f"{minutes} минуты назад"
    else:
        return f"{minutes} мин назад"
