"""Shared display dates for downloadable reports; source timestamps stay unchanged."""
from datetime import datetime
from backend.config import TZ


def local_time(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError('导出时间必须带时区')
    return dt.astimezone(TZ)


def date_time(value):
    dt = local_time(value)
    return f'{dt.year}年{dt.month}月{dt.day}日 {dt:%H:%M}'
