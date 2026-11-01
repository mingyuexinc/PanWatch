"""选股监控触发 → 通知渠道（测试/正式共用）。"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from src.modules.market.early_surge_monitor.config import MonitorConfig
from src.platform.notifications.notifier import NotifierManager
from src.platform.persistence.models import NlScreenerMonitorTrigger, NlScreenerStrategyConfig, NotifyChannel

logger = logging.getLogger(__name__)

def _format_trigger_at(dt: datetime | None) -> str:
    if not dt:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _build_message(
    trig: NlScreenerMonitorTrigger,
    *,
    trade_date: str,
    gain_pct: int,
    monitor_start: str,
    monitor_end: str,
) -> tuple[str, str]:
    prev = float(trig.prev_close or 0)
    price = float(trig.trigger_price or 0)
    pct = ((price - prev) / prev * 100.0) if prev else 0.0
    name = trig.name or trig.symbol
    title = f"【选股监控】{name}({trig.symbol}) 涨幅触发"
    lines = [
        f"行情日：{trade_date}",
        f"触发时刻：{_format_trigger_at(trig.trigger_at)}",
        f"触发价：{price:.2f}  昨收：{prev:.2f}  较昨收：{pct:+.2f}%",
        f"策略：监控 {monitor_start}–{monitor_end}，阈值 +{gain_pct}%",
    ]
    return title, "\n".join(lines)


def resolve_notify_channel(db: Session, cfg: NlScreenerStrategyConfig) -> NotifyChannel | None:
    """仅使用策略中显式绑定的通知渠道（设置 → 通知渠道注册）。"""
    if not cfg.notify_channel_id:
        return None
    row = db.get(NotifyChannel, int(cfg.notify_channel_id))
    if row and row.enabled:
        return row
    return None


async def _send_one(
    channel: NotifyChannel,
    title: str,
    content: str,
) -> tuple[bool, str]:
    notifier = NotifierManager()
    notifier.add_channel(channel.type, channel.config or {})
    try:
        result = await notifier.notify_with_result(title, content)
        if result.get("success"):
            return True, ""
        return False, str(result.get("error") or "notify_failed")
    except Exception as exc:
        logger.exception("screener monitor notify failed")
        return False, str(exc)


async def push_trigger_notifications_async(
    db: Session,
    cfg: NlScreenerStrategyConfig,
    triggers: list[NlScreenerMonitorTrigger],
    *,
    trade_date: str,
    mon_cfg: MonitorConfig,
) -> dict[str, Any]:
    if not triggers:
        return {"attempted": 0, "sent": 0, "channel": None, "errors": []}

    channel = resolve_notify_channel(db, cfg)
    if not channel:
        return {
            "attempted": len(triggers),
            "sent": 0,
            "channel": None,
            "errors": ["未选择通知渠道，或所选渠道未启用（请在策略中选择「设置」里注册的渠道）"],
        }

    h0, m0 = divmod(int(mon_cfg.session_start_minute), 60)
    h1, m1 = divmod(int(mon_cfg.session_end_minute), 60)
    start_label = f"{h0:02d}:{m0:02d}"
    end_label = f"{h1:02d}:{m1:02d}"
    gain_pct = int(round((mon_cfg.trigger_ratio - 1.0) * 100))

    sent = 0
    errors: list[str] = []
    for trig in triggers:
        if trig.notify_sent_at:
            continue
        title, content = _build_message(
            trig,
            trade_date=trade_date,
            gain_pct=gain_pct,
            monitor_start=start_label,
            monitor_end=end_label,
        )
        ok, err = await _send_one(channel, title, content)
        if ok:
            trig.notify_sent_at = datetime.now(UTC).replace(tzinfo=None)
            sent += 1
        else:
            errors.append(f"{trig.symbol}: {err}")
    db.commit()
    return {
        "attempted": len(triggers),
        "sent": sent,
        "channel": {"id": channel.id, "name": channel.name, "type": channel.type},
        "errors": errors,
    }


def push_trigger_notifications(
    db: Session,
    cfg: NlScreenerStrategyConfig,
    triggers: list[NlScreenerMonitorTrigger],
    *,
    trade_date: str,
    mon_cfg: MonitorConfig,
) -> dict[str, Any]:
    return asyncio.run(
        push_trigger_notifications_async(db, cfg, triggers, trade_date=trade_date, mon_cfg=mon_cfg)
    )
