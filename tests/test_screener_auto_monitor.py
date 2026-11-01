"""选股监控自动任务：竞价扫 / live / 跳过条件。"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.platform.persistence.models as _models  # noqa: F401
from src.modules.market.early_surge_monitor.rules import TriggerDecision
from src.modules.market import screener_monitor_service as svc
from src.platform.persistence.database import Base
from src.platform.persistence.models import (
    NlScreenerDaySession,
    NlScreenerStrategyConfig,
    NotifyChannel,
)
from src.platform.persistence import migrations as migrations_mod

CN = ZoneInfo("Asia/Shanghai")


def _db():
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
    return sessionmaker(bind=engine)


def test_auto_skip_when_disabled():
    Session = _db()
    db = Session()
    try:
        cfg = NlScreenerStrategyConfig(query_id=1, auto_monitor_enabled=False)
        db.add(cfg)
        db.commit()
        assert svc._auto_skip_reason(cfg) == "auto_disabled"
    finally:
        db.close()


def test_open_auction_scan_fires():
    Session = _db()
    db = Session()
    try:
        ch = NotifyChannel(name="t", type="wecom", config={}, enabled=True)
        db.add(ch)
        db.flush()
        cfg = NlScreenerStrategyConfig(
            query_id=1,
            notify_channel_id=ch.id,
            auto_monitor_enabled=True,
            gain_pct=3,
        )
        db.add(cfg)
        session = NlScreenerDaySession(
            trade_date="2026-11-03",
            query_id=1,
            pool_status="ready",
            pool_codes_json=["600000"],
            prev_close_json={"600000": 10.0},
        )
        db.add(session)
        db.commit()

        with patch(
            "src.modules.market.screener_monitor_service.fetch_quote_ticks",
            return_value=[("600000", 10.5, datetime(2026, 11, 3, 9, 25, tzinfo=CN))],
        ):
            with patch.object(svc, "_names_from_query", return_value={"600000": "浦发银行"}):
                with patch(
                    "src.modules.market.screener_monitor_service.push_trigger_notifications",
                    return_value={"sent": 1, "attempted": 1, "errors": [], "channel": {"name": "t"}},
                ):
                    result = svc._run_open_auction_scan(db, cfg, session)
        assert result["new_triggers"] == 1
        assert result["mode"] == "open_auction"
    finally:
        db.close()


def test_live_poll_respects_monitor_window():
    Session = _db()
    db = Session()
    try:
        ch = NotifyChannel(name="t", type="wecom", config={}, enabled=True)
        db.add(ch)
        db.flush()
        cfg = NlScreenerStrategyConfig(
            query_id=1,
            notify_channel_id=ch.id,
            auto_monitor_enabled=True,
            monitor_start_minute=9 * 60 + 30,
            monitor_end_minute=9 * 60 + 40,
        )
        db.add(cfg)
        db.commit()
        with patch("src.modules.market.screener_monitor_service.is_trading_day", return_value=True):
            with patch.object(
                svc,
                "_market_trade_date_on_or_before",
                side_effect=lambda d=None: (d or datetime.now(CN).date()).isoformat(),
            ):
                with patch("src.modules.market.screener_monitor_service.in_monitor_window", return_value=False):
                    out = svc.run_live_monitor_poll(db)
        assert out and out.get("skipped") == "outside_monitor_window"
    finally:
        db.close()
