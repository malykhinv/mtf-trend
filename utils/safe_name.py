import re


def safe_name(text: str) -> str:
    text = text.replace('/', '-')  # исторически
    return re.sub(r'[\\/:*?"<>|]+', '-', text).strip().strip('.')
