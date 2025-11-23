from datetime import datetime, timezone

from crypto_screener.domain.models.bar import Bar


def map_ohlcv(
        raw: list[list[float]],
        end: datetime
) -> list[Bar]:
    mapped: list[Bar] = []
    for ts_ms, o, h, l, c, v in raw:
        end_ms = int(end.astimezone(timezone.utc).timestamp() * 1000)
        if ts_ms <= end_ms:
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
