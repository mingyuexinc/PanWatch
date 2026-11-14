"""监控池价格规则：扫描健康状态（供列表展示与问题定位）。"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

# API / DB 取值
MONITOR_HEALTH_OK = "ok"
MONITOR_HEALTH_DATA_SOURCE = "data_source"
MONITOR_HEALTH_DATA_FORMAT = "data_format"
MONITOR_HEALTH_SCAN_ROUND = "scan_round"
MONITOR_HEALTH_ENGINE = "engine"

MONITOR_HEALTH_VALUES = frozenset(
    {
        MONITOR_HEALTH_OK,
        MONITOR_HEALTH_DATA_SOURCE,
        MONITOR_HEALTH_DATA_FORMAT,
        MONITOR_HEALTH_SCAN_ROUND,
        MONITOR_HEALTH_ENGINE,
    }
)

_ENGINE_LAST_OK_MONO: float | None = None
_ENGINE_LAST_ERROR: str = ""
# 超过 (扫描间隔 * 倍数 + 余量) 无成功扫描 → 引擎异常
_ENGINE_STALE_FACTOR = 2.5
_ENGINE_STALE_PADDING_SEC = 90


def touch_engine_scan_ok() -> None:
    global _ENGINE_LAST_OK_MONO, _ENGINE_LAST_ERROR
    _ENGINE_LAST_OK_MONO = time.monotonic()
    _ENGINE_LAST_ERROR = ""


def touch_engine_scan_failed(message: str = "") -> None:
    global _ENGINE_LAST_ERROR
    _ENGINE_LAST_ERROR = message or "scan_failed"


def engine_scan_is_stale(interval_seconds: int = 60) -> bool:
    if _ENGINE_LAST_OK_MONO is None:
        return True
    limit = max(60, int(interval_seconds)) * _ENGINE_STALE_FACTOR + _ENGINE_STALE_PADDING_SEC
    return (time.monotonic() - _ENGINE_LAST_OK_MONO) > limit


def quote_price_is_valid(quote: dict | None) -> bool:
    if not quote or not isinstance(quote, dict):
        return False
    raw = quote.get("current_price")
    if raw is None or raw == "":
        return False
    try:
        return float(raw) > 0
    except (TypeError, ValueError):
        return False


def classify_scan_item(
    *,
    status: str,
    reason: str = "",
    quote: dict | None = None,
) -> str:
    """将单轮 scan 条目结果映射为五类健康状态之一。"""
    if status in ("no_quote",):
        return MONITOR_HEALTH_DATA_SOURCE
    if status in ("bad_price",):
        return MONITOR_HEALTH_DATA_FORMAT
    if status == "no_stock":
        return MONITOR_HEALTH_SCAN_ROUND
    if quote is not None and not quote_price_is_valid(quote):
        return MONITOR_HEALTH_DATA_FORMAT
    if status == "gated":
        if reason == "non_trading":
            return MONITOR_HEALTH_OK
        return MONITOR_HEALTH_SCAN_ROUND
    if status in ("not_matched", "would_trigger", "triggered"):
        if quote is not None and not quote_price_is_valid(quote):
            return MONITOR_HEALTH_DATA_FORMAT
        return MONITOR_HEALTH_OK
    if status in ("duplicated", "scan_error", "bad_price"):
        return MONITOR_HEALTH_SCAN_ROUND
    return MONITOR_HEALTH_SCAN_ROUND


def persist_rule_health(
    rule: Any,
    health: str,
    *,
    at: datetime | None = None,
) -> None:
    if health not in MONITOR_HEALTH_VALUES:
        health = MONITOR_HEALTH_SCAN_ROUND
    rule.monitor_health = health
    rule.monitor_health_at = (at or datetime.now(timezone.utc)).replace(tzinfo=None)


def resolve_list_health(
    *,
    monitor_health: str | None,
    monitor_health_at: datetime | None,
    rule_enabled: bool,
    rule_id: int | None,
    triggered: bool,
    price_threshold: float | None,
    engine_stale: bool,
) -> str | None:
    """列表展示用健康状态；返回 None 表示不适用（待设价等）。"""
    if triggered:
        return monitor_health if monitor_health in MONITOR_HEALTH_VALUES else MONITOR_HEALTH_OK
    if price_threshold is None or rule_id is None or not rule_enabled:
        return None
    if engine_stale:
        return MONITOR_HEALTH_ENGINE
    if monitor_health in MONITOR_HEALTH_VALUES:
        return monitor_health
    return MONITOR_HEALTH_ENGINE


def stale_health_on_list(
    db: Session, interval_seconds: int = 60
) -> bool:
    _ = db
    return engine_scan_is_stale(interval_seconds)