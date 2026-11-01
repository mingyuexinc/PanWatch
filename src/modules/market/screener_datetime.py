"""选股/监控相关时间：存库用北京时间 naive，API 展示 YYYY-MM-DD HH:mm:ss。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
BEIJING_DISPLAY_FMT = "%Y-%m-%d %H:%M:%S"


def now_cn_naive() -> datetime:
    return datetime.now(CN_TZ).replace(tzinfo=None)


def format_beijing_display(dt: datetime | None) -> str | None:
    """将 datetime 格式化为北京时间字符串（无时区后缀）。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        local = dt.replace(tzinfo=CN_TZ)
    else:
        local = dt.astimezone(CN_TZ)
    return local.strftime(BEIJING_DISPLAY_FMT)

