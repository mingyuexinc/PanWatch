"""服务启动时的调度任务注册契约。"""

from types import SimpleNamespace
from unittest.mock import Mock


def test_mcp_log_cleanup_is_registered_on_wrapped_apscheduler(monkeypatch):
    import server
    from src.modules.administration.api import mcp

    prune = Mock(name="prune_mcp_logs")
    monkeypatch.setattr(mcp, "prune_mcp_logs", prune)
    scheduler = SimpleNamespace(scheduler=Mock())

    server.register_mcp_log_cleanup(scheduler)

    scheduler.scheduler.add_job.assert_called_once_with(
        prune,
        "cron",
        hour=4,
        minute=0,
        id="mcp_log_retention",
        replace_existing=True,
        misfire_grace_time=3600,
    )


def test_monitor_universe_rearm_is_daily_0910_with_misfire_grace():
    import server

    scheduler = SimpleNamespace(scheduler=Mock())

    server.register_monitor_universe_rearm(scheduler)

    scheduler.scheduler.add_job.assert_called_once_with(
        server._sync_monitor_rules_job,
        "cron",
        hour=9,
        minute=10,
        id="monitor_universe_rearm",
        replace_existing=True,
        misfire_grace_time=3600,
    )


def test_system_jobs_register_both_daily_maintenance_tasks(monkeypatch):
    """调度器重建(配置包导入触发 reload_scheduler)后补注册的系统级任务。"""
    import server

    scheduler = SimpleNamespace(scheduler=Mock())

    server.register_system_jobs(scheduler)

    registered_ids = {
        call.kwargs.get("id") for call in scheduler.scheduler.add_job.call_args_list
    }
    assert registered_ids == {"mcp_log_retention", "monitor_universe_rearm"}
