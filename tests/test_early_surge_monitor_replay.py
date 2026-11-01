from datetime import datetime
from zoneinfo import ZoneInfo

from src.modules.market.early_surge_monitor.config import MonitorConfig
from src.modules.market.early_surge_monitor.engine import EarlySurgeMonitorEngine
from src.modules.market.early_surge_monitor.replay import reference_trigger_software_minute, replay_day_from_mkline
from src.modules.market.early_surge_monitor.rules import evaluate_quote_cross, software_trigger_clock

CN = ZoneInfo("Asia/Shanghai")


def _bar(dt: datetime, o: float, h: float, l: float, c: float) -> dict:
    return {"datetime": dt, "open": o, "high": h, "low": l, "close": c, "volume": 1.0}


def test_software_trigger_clock_floors_to_minute():
    ts = datetime(2026, 10, 30, 9, 34, 18, tzinfo=CN)
    assert software_trigger_clock(ts).strftime("%H:%M:%S") == "09:34:00"


def test_reference_first_minute_with_high():
    prev = 10.0
    bars = [
        _bar(datetime(2026, 10, 30, 9, 33, tzinfo=CN), 10.0, 10.2, 10.0, 10.2),
        _bar(datetime(2026, 10, 30, 9, 34, tzinfo=CN), 10.25, 10.4, 10.25, 10.37),
    ]
    _, at = reference_trigger_software_minute(bars, prev_close=prev, trigger_end_minute=40)
    assert at and at.minute == 34


def test_quote_cross_trigger_at_minute_start():
    prev = 12.89
    cfg = MonitorConfig()
    d = evaluate_quote_cross(
        prev_close=prev,
        price=13.37,
        quote_ts=datetime(2026, 10, 30, 9, 34, 0, tzinfo=CN),
        cfg=cfg,
        already_triggered=False,
    )
    assert d.fired
    assert d.trigger_at.strftime("%H:%M:%S") == "09:34:00"


def test_replay_000995_style(monkeypatch):
    prev = 12.89
    bars = [
        _bar(datetime(2026, 10, 30, 9, 33, tzinfo=CN), 13.2, 13.25, 13.17, 13.24),
        _bar(datetime(2026, 10, 30, 9, 34, tzinfo=CN), 13.25, 13.48, 13.25, 13.37),
    ]
    monkeypatch.setattr(
        "src.modules.market.early_surge_monitor.replay.prev_trading_close",
        lambda *_a, **_k: prev,
    )
    monkeypatch.setattr(
        "src.modules.market.early_surge_monitor.engine.prev_trading_close",
        lambda *_a, **_k: prev,
    )
    cfg = MonitorConfig(mkline_confirm=False)
    r = replay_day_from_mkline("000995", "2026-10-30", cfg=cfg, bars_1m=bars)
    assert r.ref_trigger_minute == "09:34"
    assert r.synth_trigger_minute == "09:34"
    assert r.minute_delta == 0


def test_engine_triggers_on_ingest(monkeypatch):
    monkeypatch.setattr(
        "src.modules.market.early_surge_monitor.engine.prev_trading_close",
        lambda *_a, **_k: 10.0,
    )
    eng = EarlySurgeMonitorEngine(MonitorConfig(mkline_confirm=False))
    fired = eng.ingest_quotes(
        [("000001", 10.31, datetime(2026, 10, 30, 9, 34, 5, tzinfo=CN))],
        trade_date="2026-10-30",
        fallback_ts=datetime(2026, 10, 30, 9, 34, 5, tzinfo=CN),
    )
    assert len(fired) == 1
    assert fired[0][1].trigger_at.minute == 34
