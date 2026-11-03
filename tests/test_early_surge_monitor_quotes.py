from datetime import datetime
from zoneinfo import ZoneInfo

from src.modules.market.early_surge_monitor.config import MonitorConfig
from src.modules.market.early_surge_monitor.quotes import open_auction_effective_price, parse_tencent_quote_line
from src.modules.market.early_surge_monitor.rules import evaluate_quote_cross

CN = ZoneInfo("Asia/Shanghai")


def test_parse_tencent_includes_open():
    parts = ["0"] * 50
    parts[1] = "stock"
    parts[2] = "002307"
    parts[3] = "3.22"
    parts[4] = "3.22"
    parts[5] = "3.49"
    parts[30] = "20261103092500"
    line = 'v_sz002307="' + "~".join(parts) + '";'
    parsed = parse_tencent_quote_line(line)
    assert parsed is not None, line
    sym, last, prev, qts, op = parsed
    assert sym == "002307"
    assert last == 3.22 and prev == 3.22 and op == 3.49
    assert open_auction_effective_price(last, op) == 3.49


def test_open_auction_effective_price_without_open():
    assert open_auction_effective_price(3.22, None) == 3.22


def test_live_window_uses_poll_clock_not_stale_quote_ts():
    """根因 B：轮询时刻在 9:30–9:40，行情时间戳仍可为 9:25。"""
    prev = 3.22
    cfg = MonitorConfig()
    stale = datetime(2026, 11, 3, 9, 25, 0, tzinfo=CN)
    poll = datetime(2026, 11, 3, 9, 34, 5, tzinfo=CN)
    d_old = evaluate_quote_cross(
        prev_close=prev,
        price=3.49,
        quote_ts=stale,
        cfg=cfg,
        already_triggered=False,
    )
    assert not d_old.fired and d_old.reason == "outside_window"
    d_fix = evaluate_quote_cross(
        prev_close=prev,
        price=3.49,
        quote_ts=stale,
        cfg=cfg,
        already_triggered=False,
        window_ts=poll,
    )
    assert d_fix.fired
    assert d_fix.trigger_at and d_fix.trigger_at.minute == 34
