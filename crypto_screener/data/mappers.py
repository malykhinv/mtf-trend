from datetime import datetime, timezone
from typing import Optional

from crypto_screener.domain.models.bar import Bar


def map_ohlcv(
        raw: list[list[float]],
        end: Optional[datetime] = None,
) -> list[Bar]:
    mapped: list[Bar] = []
    end_ms = (
        int(end.astimezone(timezone.utc).timestamp() * 1000)
        if end else None
    )
    for ts_ms, o, h, l, c, v in raw:
        if end_ms is None or ts_ms <= end_ms:
            time = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            mapped.append(
                Bar(
                    time=time,
                    open=float(o),
                    high=float(h),
                    low=float(l),
                    close=float(c),
                    volume=float(v),
                    swing=None
                )
            )
    return mapped
