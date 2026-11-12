"""监控池突破推送。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.market.monitor_universe_notify import (
    build_monitor_pool_hit_message,
    send_monitor_pool_price_hit,
)
from src.platform.persistence.database import Base
from src.platform.persistence.models import NotifyChannel, PriceAlertRule, Stock


def _db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    return session


def test_build_message_format():
    stock = Stock(symbol="600519", market="CN", name="贵州茅台")
    rule = PriceAlertRule(
        stock=stock,
        name="价格突破监控·贵州茅台(600519)>1500",
        condition_group={
            "op": "and",
            "items": [{"type": "price", "op": ">", "value": 1500}],
        },
    )
    title, body = build_monitor_pool_hit_message(
        rule,
        {"quote": {"current_price": 1501.2}},
        threshold=1500,
    )
    assert "600519" in title
    assert "1501.20" in body
    assert "1500" in body


def test_send_uses_default_notify_channel():
    db = _db()
    try:
        ch = NotifyChannel(
            name="企微",
            type="wecom",
            config={"webhook_key": "k"},
            enabled=True,
            is_default=True,
        )
        stock = Stock(symbol="002437", market="CN", name="誉衡药业")
        db.add(ch)
        db.add(stock)
        db.flush()
        rule = PriceAlertRule(
            stock_id=stock.id,
            stock=stock,
            name="价格突破监控·誉衡药业(002437)>5",
            enabled=True,
            condition_group={
                "op": "and",
                "items": [{"type": "price", "op": ">", "value": 5}],
            },
            notify_channel_ids=[],
        )
        db.add(rule)
        db.commit()

        with patch(
            "src.modules.market.monitor_universe_notify.NotifierManager.notify_with_result",
            new_callable=AsyncMock,
            return_value={"success": True},
        ) as mock_notify:
            ok, err = asyncio.run(
                send_monitor_pool_price_hit(db, rule, {"quote": {"current_price": 5.1}})
            )
            assert ok is True
            assert err == ""
            mock_notify.assert_awaited()
    finally:
        db.close()
