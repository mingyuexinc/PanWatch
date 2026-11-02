"""自定义交易记录 REST API。

层级: 策略 → 版本 → 记录。列表/统计/排序按 version_id 隔离;
一键统计仅统计指定版本下的记录。
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.modules.trading_journal import service as svc
from src.platform.persistence.database import get_db
from src.web.errors import api_error

logger = logging.getLogger(__name__)
router = APIRouter()

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class StrategyCreate(BaseModel):
    name: str = Field(..., min_length=1, description="策略名称")
    description: str = Field("", description="策略简介")


class StrategyUpdate(BaseModel):
    name: str | None = Field(None, min_length=1)
    description: str | None = None


class VersionCreate(BaseModel):
    name: str = Field(..., min_length=1, description="版本名称")
    description: str = Field("", description="版本简介")


class VersionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1)
    description: str | None = None


class EntryCreate(BaseModel):
    version_id: int = Field(..., ge=1, description="所属版本 id")
    trade_date: str = Field(..., description="开仓日期 YYYY-MM-DD")
    stock_name: str = Field(..., min_length=1, description="股票名称")
    sector: str = Field("", description="板块/身位")
    open_change_pct: float | None = Field(None, description="开盘涨幅(%数值)")
    seal_result: str = Field("", description="封板结果")
    sell_timing: str = Field("", description="卖出时机")
    return_pct: float | None = Field(None, description="收益率(%数值)")
    review_note: str = Field("", description="复盘备注")


class EntryUpdate(BaseModel):
    trade_date: str | None = None
    stock_name: str | None = None
    sector: str | None = None
    open_change_pct: float | None = None
    seal_result: str | None = None
    sell_timing: str | None = None
    return_pct: float | None = None
    review_note: str | None = None
    version_id: int | None = Field(None, ge=1, description="移至其他版本(同策略)")


class EntryBatchDelete(BaseModel):
    ids: list[int] = Field(..., description="要删除的记录 id 列表")


class EntryMoveRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1, description="要移动的记录 id")
    target_version_id: int = Field(..., ge=1, description="目标版本 id")


class ReorderItem(BaseModel):
    id: int
    sort_order: int = Field(..., ge=1, description="页内序号,1 起")


class ReorderRequest(BaseModel):
    version_id: int = Field(..., ge=1)
    items: list[ReorderItem] = Field(..., description="当前页全部条目按新顺序排列")


def _validate_entry_fields(payload: EntryCreate | EntryUpdate) -> None:
    if isinstance(payload, EntryCreate):
        if not _DATE_RE.match(payload.trade_date or ""):
            raise api_error(400, "trade_journal_payload_invalid", "开仓日期格式应为 YYYY-MM-DD")
        if not (payload.stock_name or "").strip():
            raise api_error(400, "trade_journal_payload_invalid", "股票名称不能为空")
    else:
        if payload.trade_date is not None and not _DATE_RE.match(payload.trade_date):
            raise api_error(400, "trade_journal_payload_invalid", "开仓日期格式应为 YYYY-MM-DD")
        if payload.stock_name is not None and not payload.stock_name.strip():
            raise api_error(400, "trade_journal_payload_invalid", "股票名称不能为空")
    for name, field_desc, low, high in (
        ("open_change_pct", "开盘涨幅", -100.0, 1000.0),
        ("return_pct", "收益率", -100.0, 100000.0),
    ):
        value = getattr(payload, name)
        if value is not None and not (low <= value <= high):
            raise api_error(400, "trade_journal_payload_invalid", f"{field_desc}数值超出合理范围")


def _validate_meta_name(name: str | None, label: str) -> str:
    text = (name or "").strip()
    if not text:
        raise api_error(400, "trade_journal_payload_invalid", f"{label}不能为空")
    return text


# ---- 策略 ----


@router.get("/strategies")
def list_strategies(db: Session = Depends(get_db)):
    return {"items": svc.list_strategies(db)}


@router.post("/strategies")
def create_strategy(payload: StrategyCreate, db: Session = Depends(get_db)):
    name = _validate_meta_name(payload.name, "策略名称")
    row = svc.create_strategy(db, name=name, description=(payload.description or "").strip())
    return svc.serialize_strategy(row)


@router.put("/strategies/{strategy_id}")
def update_strategy(
    strategy_id: int, payload: StrategyUpdate, db: Session = Depends(get_db)
):
    fields: dict = {}
    if payload.name is not None:
        fields["name"] = _validate_meta_name(payload.name, "策略名称")
    if payload.description is not None:
        fields["description"] = payload.description.strip()
    row = svc.update_strategy(db, strategy_id, **fields)
    if row is None:
        raise api_error(404, "trade_journal_strategy_not_found", "策略不存在")
    return svc.serialize_strategy(row)


@router.delete("/strategies/{strategy_id}")
def delete_strategy(strategy_id: int, db: Session = Depends(get_db)):
    ok = svc.delete_strategy(db, strategy_id)
    if not ok:
        row = svc.list_strategies(db)
        exists = any(s["id"] == strategy_id for s in row)
        if not exists:
            raise api_error(404, "trade_journal_strategy_not_found", "策略不存在")
        raise api_error(
            409,
            "trade_journal_strategy_last",
            "至少保留一个策略,无法删除最后一个",
        )
    return {"removed": 1}


# ---- 版本 ----


@router.get("/strategies/{strategy_id}/versions")
def list_versions(strategy_id: int, db: Session = Depends(get_db)):
    items = svc.list_versions(db, strategy_id)
    if items is None:
        raise api_error(404, "trade_journal_strategy_not_found", "策略不存在")
    return {"items": items}


@router.post("/strategies/{strategy_id}/versions")
def create_version(
    strategy_id: int, payload: VersionCreate, db: Session = Depends(get_db)
):
    name = _validate_meta_name(payload.name, "版本名称")
    row = svc.create_version(
        db, strategy_id, name=name, description=(payload.description or "").strip()
    )
    if row is None:
        raise api_error(404, "trade_journal_strategy_not_found", "策略不存在")
    return svc.serialize_version(row)


@router.put("/versions/{version_id}")
def update_version(version_id: int, payload: VersionUpdate, db: Session = Depends(get_db)):
    fields: dict = {}
    if payload.name is not None:
        fields["name"] = _validate_meta_name(payload.name, "版本名称")
    if payload.description is not None:
        fields["description"] = payload.description.strip()
    row = svc.update_version(db, version_id, **fields)
    if row is None:
        raise api_error(404, "trade_journal_version_not_found", "版本不存在")
    return svc.serialize_version(row)


@router.delete("/versions/{version_id}")
def delete_version(version_id: int, db: Session = Depends(get_db)):
    err = svc.delete_version(db, version_id)
    if err == "not_found":
        raise api_error(404, "trade_journal_version_not_found", "版本不存在")
    if err == "last_version":
        raise api_error(
            409,
            "trade_journal_version_last",
            "每个策略至少保留一个版本,无法删除最后一个",
        )
    if err == "has_entries":
        raise api_error(
            409,
            "trade_journal_version_has_entries",
            "该版本下仍有交易记录,请先移走或删除记录后再删版本",
        )
    return {"removed": 1}


@router.post("/entries/move-to-version")
def move_entries(payload: EntryMoveRequest, db: Session = Depends(get_db)):
    moved = svc.move_entries_to_version(db, payload.ids, payload.target_version_id)
    if moved is None:
        raise api_error(
            400,
            "trade_journal_move_invalid",
            "无法移动:记录或目标版本不存在,或跨策略移动",
        )
    return {"moved": moved}


# ---- 记录 ----


@router.get("")
def list_entries(
    version_id: int = Query(..., ge=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=svc.DEFAULT_PAGE_SIZE, ge=1, le=100),
    db: Session = Depends(get_db),
):
    payload = svc.list_entries(db, version_id=version_id, page=page, page_size=page_size)
    if payload is None:
        raise api_error(404, "trade_journal_version_not_found", "版本不存在")
    return payload


@router.get("/stats")
def get_stats(
    version_id: int = Query(..., ge=1),
    db: Session = Depends(get_db),
):
    stats = svc.compute_stats(db, version_id)
    if stats is None:
        raise api_error(404, "trade_journal_version_not_found", "版本不存在")
    return stats


@router.post("")
def create_entry(payload: EntryCreate, db: Session = Depends(get_db)):
    _validate_entry_fields(payload)
    entry = svc.create_entry(
        db,
        payload.version_id,
        trade_date=payload.trade_date,
        stock_name=payload.stock_name.strip(),
        sector=payload.sector.strip(),
        open_change_pct=payload.open_change_pct,
        seal_result=payload.seal_result.strip(),
        sell_timing=payload.sell_timing.strip(),
        return_pct=payload.return_pct,
        review_note=payload.review_note.strip(),
    )
    if entry is None:
        raise api_error(404, "trade_journal_version_not_found", "版本不存在")
    return svc.serialize_entry(entry)


@router.put("/reorder/batch")
def reorder_entries(payload: ReorderRequest, db: Session = Depends(get_db)):
    ok = svc.reorder_entries(
        db, payload.version_id, [item.id for item in payload.items]
    )
    if not ok:
        raise api_error(400, "trade_journal_reorder_invalid", "排序失败:版本或条目无效")
    return {"ok": True}


@router.post("/batch-delete")
def batch_delete(payload: EntryBatchDelete, db: Session = Depends(get_db)):
    removed = svc.delete_entries(db, payload.ids)
    return {"removed": removed}


@router.put("/{entry_id}")
def update_entry(entry_id: int, payload: EntryUpdate, db: Session = Depends(get_db)):
    _validate_entry_fields(payload)
    data = payload.model_dump(exclude_unset=True, exclude_none=False)
    version_id = data.pop("version_id", None)
    entry = svc.update_entry(db, entry_id, version_id=version_id, **data)
    if entry is None:
        raise api_error(404, "trade_journal_entry_not_found", "交易记录不存在")
    return svc.serialize_entry(entry)


@router.delete("/{entry_id}")
def delete_entry(entry_id: int, db: Session = Depends(get_db)):
    removed = svc.delete_entries(db, [entry_id])
    if removed == 0:
        raise api_error(404, "trade_journal_entry_not_found", "交易记录不存在")
    return {"removed": removed}
