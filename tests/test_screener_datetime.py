from datetime import datetime
from zoneinfo import ZoneInfo

from src.modules.market.screener_datetime import format_beijing_display, now_cn_naive


def test_format_beijing_display():
    dt = datetime(2026, 11, 1, 10, 26, 25, 307727, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert format_beijing_display(dt) == "2026-11-01 10:26:25"
    naive = datetime(2026, 11, 1, 10, 26, 25)
    assert format_beijing_display(naive) == "2026-11-01 10:26:25"


def test_now_cn_naive_has_no_tz():
    dt = now_cn_naive()
    assert dt.tzinfo is None
