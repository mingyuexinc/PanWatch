"""自定义交易记录 REST API。

每笔记录的列: 开仓日期(日期组件)/股票名称/板块身位/开盘涨幅/封板结果/
卖出时机/收益率/复盘备注(编辑框输入)。支持单删、批量删、页内拖拽排序、
分页(默认每页 20 条)与一键统计(盈利概率、乘法复合总收益率)。
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

# 开仓日期由前端日期组件给出,固定 ISO 格式
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class EntryCreate(BaseModel):
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


class EntryBatchDelete(BaseModel):
    ids: list[int] = Field(..., description="要删除的记录 id 列表")


class ReorderItem(BaseModel):
    id: int
    sort_order: int = Field(..., ge=1, description="页内序号,1 起")


class ReorderRequest(BaseModel):
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
    # 两个百分比列统一按 %数值 校验:开盘涨幅[-100,1000],收益率[-100,100000]
    for name, field_desc, low, high in (
        ("open_change_pct", "开盘涨幅", -100.0, 1000.0),
        ("return_pct", "收益率", -100.0, 100000.0),
    ):
        value = getattr(payload, name)
        if value is not None and not (low <= value <= high):
            raise api_error(400, "trade_journal_payload_invalid", f"{field_desc}数值超出合理范围")


@router.get("")
def list_entries(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=svc.DEFAULT_PAGE_SIZE, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """分页获取交易记录(按手动排序顺序)。"""
    return svc.list_entries(db, page=page, page_size=page_size)


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    """一键统计: 盈利概率与乘法复合总收益率(范围为全部记录)。"""
    return svc.compute_stats(db)


@router.post("")
def create_entry(payload: EntryCreate, db: Session = Depends(get_db)):
    _validate_entry_fields(payload)
    entry = svc.create_entry(
        db,
        trade_date=payload.trade_date,
        stock_name=payload.stock_name.strip(),
        sector=payload.sector.strip(),
        open_change_pct=payload.open_change_pct,
        seal_result=payload.seal_result.strip(),
        sell_timing=payload.sell_timing.strip(),
        return_pct=payload.return_pct,
        review_note=payload.review_note.strip(),
    )
    return svc.serialize_entry(entry)


@router.put("/reorder/batch")
def reorder_entries(payload: ReorderRequest, db: Session = Depends(get_db)):
    """拖拽排序: 提交当前页全部条目的新顺序,服务端全表重编号。"""
    svc.reorder_entries(db, [item.id for item in payload.items])
    return {"ok": True}


@router.post("/batch-delete")
def batch_delete(payload: EntryBatchDelete, db: Session = Depends(get_db)):
    removed = svc.delete_entries(db, payload.ids)
    return {"removed": removed}


@router.put("/{entry_id}")
def update_entry(entry_id: int, payload: EntryUpdate, db: Session = Depends(get_db)):
    _validate_entry_fields(payload)
    entry = svc.update_entry(
        db,
        entry_id,
        **payload.model_dump(exclude_unset=True, exclude_none=False),
    )
    if entry is None:
        raise api_error(404, "trade_journal_entry_not_found", "交易记录不存在")
    return svc.serialize_entry(entry)


@router.delete("/{entry_id}")
def delete_entry(entry_id: int, db: Session = Depends(get_db)):
    removed = svc.delete_entries(db, [entry_id])
    if removed == 0:
        raise api_error(404, "trade_journal_entry_not_found", "交易记录不存在")
    return {"removed": removed}
