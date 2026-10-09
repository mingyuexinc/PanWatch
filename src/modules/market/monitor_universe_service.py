"""监控池(Monitor Universe)共享领域服务。

监控池是人工在看板维护的"盘中监控股票集合":
- 看板的每次增删/监控条件修改即时落库(即自动保存,无独立保存按钮);
- 每次变更后同步生成/回收/对齐对应的 MA 监控提醒规则;
- 每日开盘前(09:10)与进程启动时重臂(re-arm)监控规则,
  使自动保存的集合成为次日盘中的监控股票集合。

监控信号共 6 种: 现价上穿(下穿)当日 5/10/20 日均线。
- 上穿: 现价 >= 当日滚动 N 日线(沿用既有语义);
- 下穿: 现价 < 当日滚动 N 日线(严格低于,不含等于)。

MA 监控规则是系统托管的模板规则: 名称形如 "MA5上穿监控·名称(代码)"
并携带 ma(周期) 条件;repeat_mode=once 保证当日仅首次捕获,
重臂只清除"非当日"的触发痕迹,重启/盘中重臂都不会造成当日重复捕获。
"""

from __future__ import annotations

import re
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

MONITOR_MA_PERIODS = (5, 10, 20)
DEFAULT_MONITOR_SIGNAL = "above_5"

# 信号 → (方向, 均线周期, 比较op)。上穿含等于(现价>=MA),下穿严格低于(现价<MA)。
MONITOR_SIGNALS: dict[str, tuple[str, int, str]] = {
    f"{direction}_{period}": (
        direction,
        period,
        ">=" if direction == "above" else "<",
    )
    for direction in ("above", "below")
    for period in MONITOR_MA_PERIODS
}

# 规则名: "MA5上穿监控·贵州茅台(600519)"。历史遗留命名 "MA5监控·" 无方向段,同样识别。
MONITOR_RULE_NAME_RE = re.compile(r"^MA(\d+)(上穿|下穿)?监控·")
_SIGNAL_DIRECTION_LABEL = {"above": "上穿", "below": "下穿"}


def normalize_monitor_signal(value: Any) -> str:
    """把任意输入归一化为合法信号 key,非法值回退默认(上穿5日线)。"""
    key = str(value or "").strip()
    return key if key in MONITOR_SIGNALS else DEFAULT_MONITOR_SIGNAL


def require_monitor_signal(value: Any) -> str:
    """校验信号 key,非法时抛 ValueError(给 API 层返回 400)。"""
    key = str(value or "").strip()
    if key not in MONITOR_SIGNALS:
        allowed = "/".join(MONITOR_SIGNALS)
        raise ValueError(f"不支持的监控信号: {key!r}(可选: {allowed})")
    return key


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _rule_ma_condition(rule: PriceAlertRule) -> dict | None:
    for item in (rule.condition_group or {}).get("items") or []:
        if isinstance(item, dict) and item.get("type") == "ma":
            return item
    return None


def _rule_signal(rule: PriceAlertRule) -> str | None:
    """从规则的 ma 条件反解信号 key(条件是信号的唯一事实来源)。"""
    cond = _rule_ma_condition(rule)
    if not cond:
        return None
    try:
        period = int(cond.get("value") or 0)
    except (TypeError, ValueError):
        return None
    op = str(cond.get("op") or "")
    for key, (_, p, o) in MONITOR_SIGNALS.items():
        if p == period and o == op:
            return key
    return None


def is_monitor_rule(rule: PriceAlertRule) -> bool:
    """识别系统托管的 MA 监控模板规则(命名模式+ma条件双重要求)。"""
    if not MONITOR_RULE_NAME_RE.match(rule.name or ""):
        return False
    return _rule_signal(rule) is not None


def _monitor_rule_name(stock: Stock, signal: str) -> str:
    direction, period, _ = MONITOR_SIGNALS[signal]
    label = _SIGNAL_DIRECTION_LABEL[direction]
    return f"MA{period}{label}监控·{stock.name or stock.symbol}({stock.symbol})"


def _template_condition_group(signal: str) -> dict[str, Any]:
    _, period, op = MONITOR_SIGNALS[signal]
    return {
        "op": "and",
        "items": [
            {"type": "ma", "op": op, "value": period},
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
            PriceAlertRule.name.like("MA_%监控·%")
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
                "monitor_signal": normalize_monitor_signal(item.monitor_signal),
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
        "signals": list(MONITOR_SIGNALS),
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
        # 新条目默认上穿5日均线;monitor_signal 列的模型默认值兜底,显式赋值避免依赖列默认。
        item = MonitorUniverseItem(stock_id=stock.id, monitor_signal=DEFAULT_MONITOR_SIGNAL)
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


def update_item_signal(
    db: Session, item_id: int, signal: str
) -> MonitorUniverseItem | None:
    """修改监控池条目的监控信号并立即同步规则(即时自动保存)。

    信号变化会重写规则的 ma 条件并清除触发痕迹(强制重臂):
    旧信号当日是否触发过与新信号无关,新条件应立即恢复盘中监控。
    返回条目;条目不存在返回 None;信号非法抛 ValueError。
    """
    signal = require_monitor_signal(signal)
    item = (
        db.query(MonitorUniverseItem)
        .filter(MonitorUniverseItem.id == int(item_id))
        .first()
    )
    if item is None:
        return None
    if normalize_monitor_signal(item.monitor_signal) != signal:
        item.monitor_signal = signal
        db.commit()
    sync_monitor_rules(db)
    return item


def sync_monitor_rules(db: Session) -> dict[str, int]:
    """监控池 → MA 监控规则的双向同步(幂等,可安全重复调用)。

    - 池内股票缺规则 → 按该条目信号创建;
    - 池内规则的 ma 条件与条目信号不一致(含历史遗留模板) → 重写条件与
      名称并强制重臂(清除触发痕迹),旧信号当日的触发不阻塞新信号;
    - 池内规则因"仅首次"在历史上触发过而停用,且最近一次触发不在今日
      → 重臂,保证次日盘中恢复监控;最近一次触发就在今日的规则保持原样;
    - 不在池内的模板规则 → 连同命中历史一起回收。
    """
    signal_by_stock = {
        item.stock_id: normalize_monitor_signal(item.monitor_signal)
        for item in db.query(MonitorUniverseItem).all()
    }
    today = _utc_now().date()

    created = rearmed = recycled = 0
    monitor_rules = [
        rule
        for rule in db.query(PriceAlertRule).filter(
            PriceAlertRule.name.like("MA_%监控·%")
        )
        if is_monitor_rule(rule)
    ]
    rule_by_stock = {rule.stock_id: rule for rule in monitor_rules}

    for stock_id, signal in signal_by_stock.items():
        rule = rule_by_stock.get(stock_id)
        if rule is None:
            stock = db.query(Stock).filter(Stock.id == stock_id).first()
            if stock is None:
                continue
            db.add(
                PriceAlertRule(
                    stock_id=stock_id,
                    name=_monitor_rule_name(stock, signal),
                    enabled=True,
                    condition_group=_template_condition_group(signal),
                    market_hours_mode="trading_only",
                    cooldown_minutes=30,
                    max_triggers_per_day=1,
                    repeat_mode="once",
                    notify_channel_ids=[],
                )
            )
            created += 1
            continue
        desired_name = _monitor_rule_name(rule.stock, signal)
        condition_changed = _rule_signal(rule) != signal
        if condition_changed:
            rule.name = desired_name
            rule.condition_group = _template_condition_group(signal)
            rule.enabled = True
            rule.last_trigger_at = None
            rule.last_trigger_price = None
            rule.trigger_count_today = 0
            rule.trigger_date = ""
            rearmed += 1
            continue
        if rule.name != desired_name:
            rule.name = desired_name
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
        if rule.stock_id in signal_by_stock:
            continue
        db.query(PriceAlertHit).filter(
            PriceAlertHit.rule_id == rule.id
        ).delete(synchronize_session=False)
        db.delete(rule)
        recycled += 1

    db.commit()
    return {"created": created, "rearmed": rearmed, "recycled": recycled}
