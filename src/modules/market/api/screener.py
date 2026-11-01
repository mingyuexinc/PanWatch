"""自然语言选股 HTTP API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.modules.market import nl_screener_service as svc
from src.modules.market import screener_monitor_service as monitor_svc
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


class ScreenerStrategyUpdate(BaseModel):
    query_id: int | None = None
    monitor_start: str | None = Field(default=None, description="HH:MM 整分")
    monitor_end: str | None = Field(default=None, description="HH:MM 整分")
    gain_pct: int | None = Field(default=None, ge=0, le=10)
    notify_channel_id: int | None = Field(default=None, description="设置中注册的通知渠道 ID；0 表示清空绑定")
    auto_monitor_enabled: bool | None = None


@router.get("/strategy")
def get_screener_strategy(db: Session = Depends(get_db)):
    return monitor_svc.get_strategy(db)


@router.put("/strategy")
def update_screener_strategy(body: ScreenerStrategyUpdate, db: Session = Depends(get_db)):
    return monitor_svc.update_strategy(
        db,
        query_id=body.query_id,
        monitor_start=body.monitor_start,
        monitor_end=body.monitor_end,
        gain_pct=body.gain_pct,
        notify_channel_id=body.notify_channel_id,
        auto_monitor_enabled=body.auto_monitor_enabled,
    )


@router.post("/strategy/run-morning")
def screener_run_morning_job(db: Session = Depends(get_db)):
    """手动触发 09:25 问句建池 + 竞价扫（与定时任务相同）。"""
    return monitor_svc.run_morning_open_job(db)


@router.post("/strategy/poll-live")
def screener_poll_live(db: Session = Depends(get_db)):
    """手动触发一轮 live 监控（需在监控窗口内且池已就绪）。"""
    result = monitor_svc.run_live_monitor_poll(db)
    if result is None:
        return {"skipped": "unknown"}
    return result


@router.post("/strategy/build-pool")
def build_screener_pool(db: Session = Depends(get_db)):
    return monitor_svc.build_today_pool(db, run_screener=True)


@router.post("/strategy/scan-once")
def screener_scan_once(db: Session = Depends(get_db)):
    return monitor_svc.scan_once(db)


@router.post("/strategy/test-trigger")
def screener_test_trigger(db: Session = Depends(get_db)):
    return monitor_svc.test_trigger(db)
