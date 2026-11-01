"""选股监控：09:25:05 问句建池+竞价扫；监控窗口内 live 轮询。"""

from __future__ import annotations

import asyncio
import logging
from functools import partial

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.modules.market import screener_monitor_service as monitor_svc
from src.platform.persistence.database import SessionLocal

logger = logging.getLogger(__name__)


class ScreenerMonitorScheduler:
    def __init__(self, timezone: str = "Asia/Shanghai", poll_interval_s: float = 3.0):
        self.scheduler = AsyncIOScheduler(timezone=timezone)
        self.poll_interval_s = max(2.0, float(poll_interval_s))
        self._morning_running = False
        self._live_running = False

    @staticmethod
    def _with_db(fn):
        db = SessionLocal()
        try:
            return fn(db)
        finally:
            db.close()

    async def _morning_job(self) -> None:
        if self._morning_running:
            logger.debug("[选股监控] 09:25 任务仍在执行，跳过")
            return
        self._morning_running = True
        try:
            result = await asyncio.get_running_loop().run_in_executor(
                None,
                partial(self._with_db, monitor_svc.run_morning_open_job),
            )
            if result.get("skipped"):
                logger.debug("[选股监控] 09:25 跳过: %s", result.get("skipped"))
            elif result.get("ok"):
                auc = result.get("auction") or {}
                logger.info(
                    "[选股监控] 09:25 完成 pool=%s auction_new=%s push_sent=%s",
                    result.get("pool_count"),
                    auc.get("new_triggers"),
                    (auc.get("push") or {}).get("sent"),
                )
            else:
                logger.warning("[选股监控] 09:25 失败: %s", result)
        except Exception:
            logger.exception("[选股监控] 09:25 任务异常")
        finally:
            self._morning_running = False

    async def _live_poll_job(self) -> None:
        if self._live_running:
            return
        self._live_running = True
        try:
            result = await asyncio.get_running_loop().run_in_executor(
                None,
                partial(self._with_db, monitor_svc.run_live_monitor_poll),
            )
            if not result:
                return
            if result.get("skipped"):
                return
            n = result.get("new_triggers", 0)
            if n:
                logger.info(
                    "[选股监控] live 新增触发 %s push_sent=%s",
                    n,
                    (result.get("push") or {}).get("sent"),
                )
        except Exception:
            logger.exception("[选股监控] live 轮询异常")
        finally:
            self._live_running = False

    async def trigger_morning_once(self) -> dict:
        return await asyncio.get_running_loop().run_in_executor(
            None,
            partial(self._with_db, monitor_svc.run_morning_open_job),
        )

    async def trigger_live_once(self) -> dict | None:
        return await asyncio.get_running_loop().run_in_executor(
            None,
            partial(self._with_db, monitor_svc.run_live_monitor_poll),
        )

    def start(self) -> None:
        self.scheduler.add_job(
            self._morning_job,
            "cron",
            hour=9,
            minute=25,
            second=5,
            id="screener_monitor_morning",
            replace_existing=True,
            misfire_grace_time=3600,
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            self._live_poll_job,
            "interval",
            seconds=self.poll_interval_s,
            jitter=0.8,
            id="screener_monitor_live",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.start()
        from src.platform.scheduling.scheduler_registry import register

        register("screener_monitor", self.scheduler)
        logger.info(
            "选股监控调度器已启动（09:25:05 问句建池+竞价扫，live 间隔 %.1fs）",
            self.poll_interval_s,
        )

    def shutdown(self) -> None:
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass
        logger.info("选股监控调度器已关闭")
