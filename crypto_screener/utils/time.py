from datetime import datetime, timezone

from crypto_screener.config.config import cfg


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def local_now() -> datetime:
    return utc_now().astimezone(cfg.TIMEZONE)
