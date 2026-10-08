"""监控池(Monitor Universe)共享领域服务。

监控池是人工在看板维护的"盘中监控股票集合":
- 看板的每次增删即时落库(即自动保存,无独立保存按钮);
- 每次变更后同步生成/回收对应的 MA 监控提醒规则;
- 每日开盘前(09:10)与进程启动时重臂(re-arm)监控规则,
  使自动保存的集合成为次日盘中的监控股票集合。

MA 监控规则是系统托管的模板规则: 以 MONITOR_RULE_PREFIX 命名并携带
ma(周期=MONITOR_MA_PERIOD) 条件;repeat_mode=once 保证当日仅首次捕获,
重臂只清除"非当日"的触发痕迹,重启/盘中重臂都不会造成当日重复捕获。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.platform.persistence.models import (
    MonitorUniverseItem,
    PriceAlertHit,
    PriceAlertRule,
    Stock,
)

MONITOR_RULE_PREFIX = "MA5监控·"
MONITOR_MA_PERIOD = 5


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def is_monitor_rule(rule: PriceAlertRule) -> bool:
    """识别系统托管的 MA 监控模板规则(名称前缀+ma条件双重要求)。"""
    if not (rule.name or "").startswith(MONITOR_RULE_PREFIX):
        return False
    items = (rule.condition_group or {}).get("items") or []
    return any(
        isinstance(item, dict)
        and item.get("type") == "ma"
        and int(item.get("value") or 0) == MONITOR_MA_PERIOD
        for item in items
    )


def _monitor_rule_name(stock: Stock) -> str:
    return f"{MONITOR_RULE_PREFIX}{stock.name or stock.symbol}({stock.symbol})"


def _template_condition_group() -> dict[str, Any]:
    return {
        "op": "and",
        "items": [
            {"type": "ma", "op": ">=", "value": MONITOR_MA_PERIOD},
        ],
    }


def get_or_create_stock(
    db: Session, *, symbol: str, market: str, name: str = ""
) -> Stock:
    """按 (symbol, market) 取股票,不存在则创建(排序值追加到末尾)。"""
    symbol = str(symbol or "").strip()
    market = str(market or "").strip()
    if not symbol or not market:
        raise ValueError("symbol 与 market 不能为空")
    existing = (
        db.query(Stock)
        .filter(Stock.symbol == symbol, Stock.market == market)
        .first()
    )
    if existing:
        return existing
    max_order = db.query(func.max(Stock.sort_order)).scalar() or 0
    stock = Stock(
        symbol=symbol,
        market=market,
        name=str(name or "").strip() or symbol,
        sort_order=int(max_order) + 1,
    )
    db.add(stock)
    db.flush()
    return stock


def list_universe(db: Session) -> dict[str, Any]:
    """列出监控池(附股票信息与模板规则状态)与自动保存时间。"""
    items = (
        db.query(MonitorUniverseItem)
        .join(Stock)
        .order_by(MonitorUniverseItem.created_at.asc(), MonitorUniverseItem.id.asc())
        .all()
    )
    rules = {
        rule.stock_id: rule
        for rule in db.query(PriceAlertRule).filter(
            PriceAlertRule.name.startswith(MONITOR_RULE_PREFIX)
        )
        if is_monitor_rule(rule)
    }
    # 最近一次命中的推送结果:推送失败时前端用红色状态提示,而不是静默丢失。
    latest_hit_by_rule: dict[int, PriceAlertHit] = {}
    if rules:
        for hit in (
            db.query(PriceAlertHit)
            .filter(PriceAlertHit.rule_id.in_([r.id for r in rules.values()]))
            .order_by(PriceAlertHit.id.desc())
            .all()
        ):
            latest_hit_by_rule.setdefault(hit.rule_id, hit)
    rows: list[dict[str, Any]] = []
    saved_at: datetime | None = None
    for item in items:
        stock = item.stock
        rule = rules.get(item.stock_id)
        last_hit = latest_hit_by_rule.get(rule.id) if rule else None
        rows.append(
            {
                "id": item.id,
                "stock_id": item.stock_id,
                "symbol": stock.symbol if stock else "",
                "name": stock.name if stock else "",
                "market": stock.market if stock else "",
                "note": item.note or "",
                "added_at": _naive_utc(item.created_at).isoformat()
                if item.created_at
                else None,
                "updated_at": _naive_utc(item.updated_at).isoformat()
                if item.updated_at
                else None,
                "rule_id": rule.id if rule else None,
                "rule_enabled": bool(rule.enabled) if rule else False,
                "rule_last_trigger_at": _naive_utc(rule.last_trigger_at).isoformat()
                if rule and rule.last_trigger_at
                else None,
                "rule_last_trigger_price": rule.last_trigger_price
                if rule
                else None,
                "rule_last_hit_notify_success": (
                    bool(last_hit.notify_success) if last_hit else None
                ),
                "rule_last_hit_notify_error": (
                    last_hit.notify_error or "" if last_hit else ""
                ),
            }
        )
        item_updated = _naive_utc(item.updated_at) or _naive_utc(item.created_at)
        if item_updated and (saved_at is None or item_updated > saved_at):
            saved_at = item_updated
    return {
        "items": rows,
        "total": len(rows),
        "saved_at": saved_at.isoformat() if saved_at else None,
        "ma_period": MONITOR_MA_PERIOD,
    }


def add_to_universe(
    db: Session, *, symbol: str, market: str, name: str = ""
) -> tuple[MonitorUniverseItem, bool]:
    """把股票加入监控池(幂等),并立即同步监控规则(即时自动保存)。"""
    stock = get_or_create_stock(db, symbol=symbol, market=market, name=name)
    item = (
        db.query(MonitorUniverseItem)
        .filter(MonitorUniverseItem.stock_id == stock.id)
        .first()
    )
    created = item is None
    if created:
        item = MonitorUniverseItem(stock_id=stock.id)
        db.add(item)
        db.commit()
        db.refresh(item)
    sync_monitor_rules(db)
    return item, created


def remove_from_universe(db: Session, item_ids: list[int]) -> int:
    """按监控池条目 id 批量移除(单删即长度为1的批删),并回收其监控规则。"""
    ids = [int(i) for i in (item_ids or []) if int(i) > 0]
    if not ids:
        return 0
    removed = (
        db.query(MonitorUniverseItem)
        .filter(MonitorUniverseItem.id.in_(ids))
        .delete(synchronize_session=False)
    )
    db.commit()
    sync_monitor_rules(db)
    return int(removed)


def sync_monitor_rules(db: Session) -> dict[str, int]:
    """监控池 → MA 监控规则的双向同步(幂等,可安全重复调用)。

    - 池内股票缺规则 → 按模板创建;
    - 池内股票的规则因"仅首次"在历史上触发过而停用,且最近一次触发
      不在今日 → 重臂(清除触发痕迹并启用),保证次日盘中恢复监控;
      最近一次触发就在今日的规则保持原样,避免当日重复捕获;
    - 不在池内的模板规则 → 连同命中历史一起回收。
    """
    universe_stock_ids = {
        row[0]
        for row in db.query(MonitorUniverseItem.stock_id).distinct()
    }
    today = _utc_now().date()

    created = rearmed = recycled = 0
    monitor_rules = [
        rule
        for rule in db.query(PriceAlertRule).filter(
            PriceAlertRule.name.startswith(MONITOR_RULE_PREFIX)
        )
        if is_monitor_rule(rule)
    ]
    rule_by_stock = {rule.stock_id: rule for rule in monitor_rules}

    for stock_id in universe_stock_ids:
        rule = rule_by_stock.get(stock_id)
        if rule is None:
            stock = db.query(Stock).filter(Stock.id == stock_id).first()
            if stock is None:
                continue
            db.add(
                PriceAlertRule(
                    stock_id=stock_id,
                    name=_monitor_rule_name(stock),
                    enabled=True,
                    condition_group=_template_condition_group(),
                    market_hours_mode="trading_only",
                    cooldown_minutes=30,
                    max_triggers_per_day=1,
                    repeat_mode="once",
                    notify_channel_ids=[],
                )
            )
            created += 1
            continue
        last_trigger = _naive_utc(rule.last_trigger_at)
        triggered_today = last_trigger is not None and last_trigger.date() == today
        if triggered_today:
            continue
        if not rule.enabled or rule.last_trigger_at is not None:
            rule.enabled = True
            rule.last_trigger_at = None
            rule.last_trigger_price = None
            rearmed += 1

    for rule in monitor_rules:
        if rule.stock_id in universe_stock_ids:
            continue
        db.query(PriceAlertHit).filter(
            PriceAlertHit.rule_id == rule.id
        ).delete(synchronize_session=False)
        db.delete(rule)
        recycled += 1

    db.commit()
    return {"created": created, "rearmed": rearmed, "recycled": recycled}
