"""选股监控触发推送。"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.platform.persistence.models as _models  # noqa: F401
from src.modules.market.screener_monitor_notify import (
    _build_message,
    push_trigger_notifications_async,
    resolve_notify_channel,
)
from src.platform.persistence.database import Base
from src.platform.persistence.models import NlScreenerMonitorTrigger, NlScreenerStrategyConfig, NotifyChannel
from src.platform.persistence import migrations as migrations_mod
from src.modules.market.early_surge_monitor.config import MonitorConfig


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        migrations_mod._m137_nl_screener_monitor_strategy(conn)
        migrations_mod._m138_screener_monitor_notify(conn)
        migrations_mod._m139_screener_action_timestamps(conn)
        migrations_mod._m140_screener_auto_monitor(conn)
    Session = sessionmaker(bind=engine)
    return Session()


def test_resolve_requires_explicit_channel_id():
    db = _session()
    try:
        ch = NotifyChannel(
            name="拉升测试",
            type="wecom",
            config={"webhook_key": "test-key"},
            enabled=True,
        )
        db.add(ch)
        db.flush()
        db.add(NlScreenerStrategyConfig(query_id=1, notify_channel_id=None))
        db.commit()
        cfg = db.query(NlScreenerStrategyConfig).first()
        assert resolve_notify_channel(db, cfg) is None
        cfg.notify_channel_id = ch.id
        db.commit()
        resolved = resolve_notify_channel(db, cfg)
        assert resolved is not None
        assert resolved.name == "拉升测试"
    finally:
        db.close()


def test_build_message_format():
    trig = NlScreenerMonitorTrigger(
        trade_date="2026-10-30",
        symbol="600592",
        name="龙溪股份",
        trigger_at=datetime(2026, 10, 30, 9, 34, 0),
        trigger_price=13.37,
        prev_close=12.89,
    )
    title, body = _build_message(
        trig,
        trade_date="2026-10-30",
        gain_pct=3,
        monitor_start="09:30",
        monitor_end="09:40",
    )
    assert "600592" in title
    assert "2026-10-30 09:34:00" in body


def test_push_marks_notify_sent():
    db = _session()
    try:
        ch = NotifyChannel(
            name="拉升测试",
            type="wecom",
            config={"webhook_key": "k"},
            enabled=True,
        )
        db.add(ch)
        db.flush()
        cfg = NlScreenerStrategyConfig(query_id=1, notify_channel_id=ch.id)
        db.add(cfg)
        db.flush()
        trig = NlScreenerMonitorTrigger(
            trade_date="2026-10-30",
            symbol="600592",
            name="龙溪股份",
            trigger_at=datetime(2026, 10, 30, 9, 34, 0),
            trigger_price=13.37,
            prev_close=12.89,
        )
        db.add(trig)
        db.commit()

        with patch(
            "src.modules.market.screener_monitor_notify._send_one",
            new=AsyncMock(return_value=(True, "")),
        ):
            result = __import__("asyncio").run(
                push_trigger_notifications_async(
                    db,
                    cfg,
                    [trig],
                    trade_date="2026-10-30",
                    mon_cfg=MonitorConfig(mkline_confirm=False),
                )
            )
        assert result["sent"] == 1
        db.refresh(trig)
        assert trig.notify_sent_at is not None
    finally:
        db.close()
