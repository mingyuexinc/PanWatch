import asyncio
from unittest.mock import AsyncMock, patch

from src.modules.market.price_alert_scheduler import (
    PriceAlertScheduler,
    scheduled_scan_allowed,
)


def test_scheduled_scan_allowed_delegates_to_cn_calendar(monkeypatch):
    calls: list[bool] = []

    def fake_is_trading_day(market, d=None):
        calls.append(market)
        return True

    monkeypatch.setattr(
        "src.modules.market.price_alert_scheduler.is_trading_day",
        fake_is_trading_day,
    )
    assert scheduled_scan_allowed() is True
    from src.platform.marketdata.models import MarketCode

    assert calls == [MarketCode.CN]


def test_scan_job_skips_on_non_trading_day():
    sched = PriceAlertScheduler(interval_seconds=60)

    async def run():
        with patch(
            "src.modules.market.price_alert_scheduler.scheduled_scan_allowed",
            return_value=False,
        ):
            with patch(
                "src.modules.market.price_alert_scheduler.ENGINE.scan_once",
                new_callable=AsyncMock,
            ) as scan:
                await sched._scan_job()
                scan.assert_not_called()

    asyncio.run(run())


def test_scan_job_runs_on_trading_day():
    sched = PriceAlertScheduler(interval_seconds=60)

    async def run():
        with patch(
            "src.modules.market.price_alert_scheduler.scheduled_scan_allowed",
            return_value=True,
        ):
            with patch(
                "src.modules.market.price_alert_scheduler.ENGINE.scan_once",
                new_callable=AsyncMock,
                return_value={"total_rules": 0, "triggered": 0, "skipped": 0},
            ) as scan:
                await sched._scan_job()
                scan.assert_awaited_once()

    asyncio.run(run())