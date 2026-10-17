"""分时线质量评估 REST API。"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.modules.intraday_quality import service as svc
from src.platform.persistence.database import get_db
from src.web.errors import api_error

logger = logging.getLogger(__name__)
router = APIRouter()

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SampleAddPayload(BaseModel):
    symbol: str = Field(..., description="股票代码")
    market: str = Field("CN", description="市场 CN/HK/US")
    name: str = Field("", description="名称")
    test_date: str | None = Field(None, description="测试日 YYYY-MM-DD，默认今日")
    sample_type: str = Field("positive", description="positive/negative/boundary")
    note: str = Field("", description="备注")


class SampleUpdatePayload(BaseModel):
    test_date: str | None = None
    sample_type: str | None = None
    note: str | None = None


class SampleBatchRemovePayload(BaseModel):
    ids: list[int] = Field(..., description="样本 id 列表")


class SampleComputePayload(BaseModel):
    ids: list[int] | None = Field(
        None,
        description="指定样本 id；为空或未传则计算全部",
    )


def _validate_date(value: str | None) -> None:
    if value is not None and not _DATE_RE.match(value):
        raise api_error(400, "intraday_quality_invalid_date", "日期格式应为 YYYY-MM-DD")


@router.get("")
def list_samples(db: Session = Depends(get_db)):
    return svc.list_samples(db)


@router.post("")
def add_sample(payload: SampleAddPayload, db: Session = Depends(get_db)):
    _validate_date(payload.test_date)
    try:
        svc.add_sample(
            db,
            symbol=payload.symbol,
            market=payload.market,
            name=payload.name,
            test_date=payload.test_date,
            sample_type=payload.sample_type,
            note=payload.note,
        )
    except ValueError as exc:
        raise api_error(400, "intraday_quality_invalid_payload", str(exc))
    return svc.list_samples(db)


@router.patch("/{item_id}")
def update_sample(item_id: int, payload: SampleUpdatePayload, db: Session = Depends(get_db)):
    _validate_date(payload.test_date)
    try:
        item = svc.update_sample(
            db,
            item_id,
            test_date=payload.test_date,
            sample_type=payload.sample_type,
            note=payload.note,
        )
    except ValueError as exc:
        raise api_error(400, "intraday_quality_invalid_payload", str(exc))
    if item is None:
        raise api_error(404, "intraday_quality_not_found", "样本不存在")
    return svc.list_samples(db)


@router.delete("/batch")
def batch_remove(body: SampleBatchRemovePayload, db: Session = Depends(get_db)):
    removed = svc.remove_samples(db, body.ids)
    return {"removed": removed, **svc.list_samples(db)}


@router.delete("/{item_id}")
def remove_item(item_id: int, db: Session = Depends(get_db)):
    removed = svc.remove_samples(db, [item_id])
    if removed == 0:
        raise api_error(404, "intraday_quality_not_found", "样本不存在")
    return {"removed": removed, **svc.list_samples(db)}


@router.post("/compute")
def compute_scores(
    payload: SampleComputePayload = Body(default_factory=SampleComputePayload),
    db: Session = Depends(get_db),
):
    """计算 strength_score（Baostock 5m + Wyckoff）；可指定 ids 仅算选中项。"""
    ids = payload.ids
    try:
        return svc.compute_samples(db, ids=ids)
    except ValueError as exc:
        raise api_error(400, "intraday_quality_invalid_payload", str(exc))
