from datetime import datetime, timezone

from crypto_screener.config.config import cfg
from crypto_screener.domain.models.bar import Bar


def map_ohlcv(raw: list[list[float]]) -> list[Bar]:
    mapped: list[Bar] = []
    for ts_ms, o, h, l, c, v in raw:
        dt_utc = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        dt_local = dt_utc.astimezone(cfg.TIMEZONE)
        mapped.append(
            Bar(
                time=dt_local,
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                volume=float(v),
                swing=None
            )
        )
    return mapped
