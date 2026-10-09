"""监控池(盘中监控股票集合)看板 API。

看板的每次增删即时落库并同步监控规则 —— 即"自动保存";
保存的集合经每日开盘前重臂后成为次日盘中的监控股票集合。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.modules.market import monitor_universe_service as svc
from src.platform.persistence.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


class UniverseAddPayload(BaseModel):
    symbol: str = Field(..., description="股票代码,如 002437")
    market: str = Field("CN", description="市场: CN/HK/US")
    name: str = Field("", description="股票名称(缺省时以代码代替)")


class UniverseBatchRemovePayload(BaseModel):
    ids: list[int] = Field(..., description="监控池条目 id 列表")


class UniverseSignalPayload(BaseModel):
    monitor_signal: str = Field(
        ...,
        description="监控信号: above_5/below_5/above_10/below_10/above_20/below_20",
    )


@router.get("")
def list_universe(db: Session = Depends(get_db)):
    return svc.list_universe(db)


@router.post("")
def add_to_universe(payload: UniverseAddPayload, db: Session = Depends(get_db)):
    try:
        item, created = svc.add_to_universe(
            db,
            symbol=payload.symbol,
            market=payload.market,
            name=payload.name,
        )
    except ValueError as exc:
        logger.warning("监控池添加参数非法: %s", exc)
        from src.web.errors import api_error

        raise api_error(400, "invalid_payload", str(exc))
    return {
        "id": item.id,
        "stock_id": item.stock_id,
        "created": created,
        **svc.list_universe(db),
    }


@router.delete("/batch")
def batch_remove(body: UniverseBatchRemovePayload, db: Session = Depends(get_db)):
    removed = svc.remove_from_universe(db, body.ids)
    return {"removed": removed, **svc.list_universe(db)}


@router.delete("/{item_id}")
def remove_item(item_id: int, db: Session = Depends(get_db)):
    removed = svc.remove_from_universe(db, [item_id])
    if removed == 0:
        from src.web.errors import api_error

        raise api_error(404, "universe_item_not_found", "监控池条目不存在")
    return {"removed": removed, **svc.list_universe(db)}


@router.patch("/{item_id}")
def update_signal(item_id: int, payload: UniverseSignalPayload, db: Session = Depends(get_db)):
    """修改条目的监控信号(确认下拉选择后自动保存),并同步其监控规则。"""
    from src.web.errors import api_error

    try:
        item = svc.update_item_signal(db, item_id, payload.monitor_signal)
    except ValueError as exc:
        logger.warning("监控池信号参数非法(item=%s): %s", item_id, exc)
        raise api_error(400, "invalid_monitor_signal", str(exc))
    if item is None:
        raise api_error(404, "universe_item_not_found", "监控池条目不存在")
    return {"updated": True, **svc.list_universe(db)}


@router.post("/sync")
def sync_rules(db: Session = Depends(get_db)):
    """手动触发监控池→规则同步(诊断/运维用;日常由变更钩子与每日任务驱动)。"""
    return svc.sync_monitor_rules(db)
