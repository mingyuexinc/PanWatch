"""选股监控 scan_once / mkline 池扫描。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.modules.market.early_surge_monitor.config import MonitorConfig
from src.modules.market.early_surge_monitor.replay import scan_pool_from_mkline

CN = ZoneInfo("Asia/Shanghai")


def _bar(dt: datetime, o: float, c: float) -> dict:
    return {"datetime": dt, "open": o, "high": max(o, c), "low": min(o, c), "close": c, "volume": 1.0}


def test_scan_pool_from_mkline_fires(monkeypatch):
    prev = 12.89
    bars = [
        _bar(datetime(2026, 10, 30, 9, 33, tzinfo=CN), 13.2, 13.24),
        _bar(datetime(2026, 10, 30, 9, 34, tzinfo=CN), 13.25, 13.37),
    ]

    def _fake_mkline(symbol: str, trade_date: str):
        assert symbol == "000995"
        assert trade_date == "2026-10-30"
        return bars

    monkeypatch.setattr(
        "src.modules.market.early_surge_monitor.replay.fetch_tencent_mkline_1m",
        _fake_mkline,
    )
    cfg = MonitorConfig(
        session_start_minute=9 * 60 + 30,
        session_end_minute=9 * 60 + 40,
        trigger_ratio=1.03,
        mkline_confirm=False,
    )
    fired, stats = scan_pool_from_mkline(
        ["000995"],
        "2026-10-30",
        cfg=cfg,
        prev_close_by_symbol={"000995": prev},
        triggered_symbols=set(),
        until=datetime(2026, 10, 30, 15, 0, tzinfo=CN),
    )
    assert stats.symbols_in_pool == 1
    assert len(fired) == 1
    sym, decision = fired[0]
    assert sym == "000995"
    assert decision.fired
    assert decision.trigger_at and decision.trigger_at.strftime("%H:%M") == "09:34"
