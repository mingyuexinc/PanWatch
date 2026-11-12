"""监控池价格突破触发 → 设置页配置的默认通知渠道（如企业微信机器人）。"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from src.platform.notifications.notifier import NotifierManager
from src.platform.persistence.models import MonitorUniverseItem, NotifyChannel, PriceAlertRule

logger = logging.getLogger(__name__)

PRICE_MONITOR_SIGNAL = "price_above"
_MONITOR_PRICE_RULE_NAME_RE = re.compile(r"^价格突破监控·")
_CN_TZ = ZoneInfo("Asia/Shanghai")


def _is_price_monitor_rule(rule) -> bool:
    if not _MONITOR_PRICE_RULE_NAME_RE.match(rule.name or ""):
        return False
    for item in (rule.condition_group or {}).get("items") or []:
        if (
            isinstance(item, dict)
            and item.get("type") == "price"
            and str(item.get("op") or "").strip() == ">"
        ):
            try:
                return float(item.get("value")) > 0
            except (TypeError, ValueError):
                return False
    return False


def _rule_price_threshold(rule) -> float | None:
    for item in (rule.condition_group or {}).get("items") or []:
        if isinstance(item, dict) and item.get("type") == "price":
            try:
                v = float(item.get("value"))
                return v if v > 0 else None
            except (TypeError, ValueError):
                return None
    return None


def _format_trigger_at(dt: datetime | None) -> str:
    if not dt:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(_CN_TZ)
    return local.strftime("%Y/%m/%d（%H：%M：%S）")


def _resolve_channels_for_rule(db: Session, rule: PriceAlertRule) -> list[NotifyChannel]:
    """与价格提醒引擎一致：规则未指定渠道时使用设置页标记为默认的已启用渠道。"""
    ids = [int(x) for x in (rule.notify_channel_ids or []) if int(x) > 0]
    if ids:
        return (
            db.query(NotifyChannel)
            .filter(NotifyChannel.enabled == True, NotifyChannel.id.in_(ids))
            .all()
        )
    return (
        db.query(NotifyChannel)
        .filter(NotifyChannel.enabled == True, NotifyChannel.is_default == True)
        .all()
    )


def _threshold_for_rule(db: Session, rule: PriceAlertRule) -> float | None:
    th = _rule_price_threshold(rule)
    if th is not None:
        return th
    item = (
        db.query(MonitorUniverseItem)
        .filter(
            MonitorUniverseItem.stock_id == rule.stock_id,
            MonitorUniverseItem.monitor_signal == PRICE_MONITOR_SIGNAL,
        )
        .first()
    )
    if item and item.price_threshold:
        return float(item.price_threshold)
    return None


def build_monitor_pool_hit_message(
    rule: PriceAlertRule,
    snapshot: dict,
    *,
    triggered_at: datetime | None = None,
    threshold: float | None = None,
) -> tuple[str, str]:
    stock = rule.stock
    symbol = stock.symbol if stock else ""
    name = (stock.name if stock else "") or symbol
    quote = snapshot.get("quote") or {}
    price = quote.get("current_price")
    try:
        price_f = float(price) if price is not None else None
    except (TypeError, ValueError):
        price_f = None
    title = f"【监控池】{name}({symbol}) 价格突破"
    when = _format_trigger_at(triggered_at or datetime.now(timezone.utc))
    lines = [f"触发时刻：{when}"]
    if price_f is not None:
        lines.append(f"触发价：{price_f:.2f}（L1）")
    if threshold is not None:
        lines.append(f"监控价格：{threshold:.6g}（现价严格大于）")
    return title, "\n".join(lines)


async def send_monitor_pool_price_hit(
    db: Session, rule: PriceAlertRule, snapshot: dict
) -> tuple[bool, str]:
    """向企微等默认渠道推送监控池命中消息。"""
    if not _is_price_monitor_rule(rule):
        return False, "not_monitor_pool_rule"
    channels = _resolve_channels_for_rule(db, rule)
    if not channels:
        return False, "没有可用的通知渠道（请在设置页添加企业微信等渠道并设为默认）"

    notifier = NotifierManager()
    for ch in channels:
        notifier.add_channel(ch.type, ch.config or {})

    threshold = _threshold_for_rule(db, rule)
    title, content = build_monitor_pool_hit_message(
        rule,
        snapshot,
        triggered_at=rule.last_trigger_at,
        threshold=threshold,
    )
    try:
        result = await notifier.notify_with_result(title, content)
        if result.get("success"):
            return True, ""
        err = str(result.get("error") or result.get("skipped") or "notify_failed")
        return False, err
    except Exception as exc:
        logger.exception("监控池推送失败 rule=%s", rule.id)
        return False, str(exc)
