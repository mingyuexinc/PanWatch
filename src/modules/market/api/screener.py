"""自然语言选股 HTTP API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.modules.market import nl_screener_service as svc
from src.platform.persistence.database import get_db

router = APIRouter()


class ScreenerQueryCreate(BaseModel):
    label: str = Field(default="新问句", max_length=120)
    query_text: str = Field(default="", max_length=8000)
    select_type: str = Field(default="A股", max_length=16)


class ScreenerQueryUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    query_text: str | None = Field(default=None, max_length=8000)
    select_type: str | None = Field(default=None, max_length=16)


class ScreenerIntradayComputePayload(BaseModel):
    codes: list[str] | None = Field(
        default=None,
        description="指定股票代码；为空则计算当前问句全部结果",
    )


@router.get("/queries")
def list_screener_queries(db: Session = Depends(get_db)):
    return {"items": svc.list_queries(db)}


@router.get("/queries/{query_id}")
def get_screener_query(query_id: int, db: Session = Depends(get_db)):
    return svc.get_query(db, query_id, include_result=True)


@router.post("/queries")
def create_screener_query(body: ScreenerQueryCreate, db: Session = Depends(get_db)):
    return svc.create_query(
        db,
        label=body.label,
        query_text=body.query_text,
        select_type=body.select_type,
    )


@router.put("/queries/{query_id}")
def update_screener_query(
    query_id: int,
    body: ScreenerQueryUpdate,
    db: Session = Depends(get_db),
):
    return svc.update_query(
        db,
        query_id,
        label=body.label,
        query_text=body.query_text,
        select_type=body.select_type,
    )


@router.delete("/queries/{query_id}")
def delete_screener_query(query_id: int, db: Session = Depends(get_db)):
    svc.delete_query(db, query_id)
    return {"ok": True}


@router.post("/queries/{query_id}/run")
def run_screener_query(query_id: int, db: Session = Depends(get_db)):
    return svc.run_query(db, query_id)


@router.post("/queries/{query_id}/compute-intraday-scores")
def compute_screener_intraday_scores(
    query_id: int,
    body: ScreenerIntradayComputePayload = ScreenerIntradayComputePayload(),
    db: Session = Depends(get_db),
):
    return svc.compute_intraday_scores(db, query_id, codes=body.codes)


@router.get("/merged")
def get_merged_screener_mock():
    return svc.merged_mock()
