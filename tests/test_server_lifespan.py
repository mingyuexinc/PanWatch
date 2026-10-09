"""Exercise the production lifespan, including registered assistant recovery."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.orm import sessionmaker


def _isolate_startup(monkeypatch, server):
    initialization = []
    for name in (
        "init_db", "setup_logging", "setup_proxy", "setup_ssl", "setup_playwright",
        "seed_agents", "seed_strategies", "seed_sample_stocks", "reconcile_data_sources",
        "register_mcp_log_cleanup", "warmup_javascript_runtime",
        # 启动即补跑监控池同步:mock 掉,避免单测经默认线程池触碰真实 data/panwatch.db
        "_sync_monitor_rules_job",
    ):
        monkeypatch.setattr(server, name, Mock(side_effect=lambda *args, _name=name, **kwargs: initialization.append(_name)))
    monkeypatch.setattr(server, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(server, "Settings", lambda: SimpleNamespace(app_timezone="Asia/Shanghai"))
    schedulers = [Mock() for _ in range(4)]
    monkeypatch.setattr(server, "build_scheduler", lambda: schedulers[0])
    for index, name in enumerate(("PriceAlertScheduler", "PaperTradingScheduler", "ContextMaintenanceScheduler"), start=1):
        monkeypatch.setattr(server, name, lambda _index=index, **kwargs: schedulers[_index])
    for name in ("scheduler", "price_alert_scheduler", "paper_trading_scheduler", "context_maintenance_scheduler"):
        monkeypatch.setattr(server, name, None)
    import threading
    from src.modules.administration.api import auth
    from src.modules.automation.tradingagents import operations
    from src.platform.observability import otel
    from src.platform.scheduling import trading_calendar
    # 只拦截 lifespan 的"股票列表缓存后台刷新"线程,其余一律放行真 Thread。
    # 绝不能全局 Mock threading.Thread: asyncio.run() 收尾时
    # shutdown_default_executor() 也要用 threading.Thread 起关闭辅助线程,
    # 全局 Mock 会让该线程静默不启动,每个 lifespan 用例 teardown 都要干等
    # THREAD_JOIN_TIMEOUT(300 秒)才放行(曾把全量单测拖到 15 分钟)。
    real_thread = threading.Thread

    def _spy_thread(*args, **kwargs):
        target = kwargs.get("target") or (args[0] if args else None)
        if getattr(target, "__name__", "") == "refresh_stock_cache":
            return Mock()
        return real_thread(*args, **kwargs)

    monkeypatch.setattr(threading, "Thread", _spy_thread)
    monkeypatch.setattr(auth, "init_auth_from_env", lambda db: False)
    monkeypatch.setattr(operations, "backfill_tradingagents_suggestions", Mock())
    monkeypatch.setattr(otel, "init_otel", Mock())
    monkeypatch.setattr(trading_calendar, "refresh", AsyncMock())
    return initialization, schedulers


@pytest.mark.parametrize("body_fails", [False, True])
def test_production_lifespan_recovers_tasks_once_and_dispatches_shutdown(monkeypatch, body_fails):
    import server
    from src.modules.assistant.task_runner import assistant_task_runner
    from src.platform.tasking.contracts import TaskEventType, TaskStatus
    from tests.test_assistant_task_events import _repository

    initialization, schedulers = _isolate_startup(monkeypatch, server)
    engine, session, repository, queued = _repository()
    running = repository.create_task(conversation_id=queued.conversation_id, user_message_id=None, context={})
    repository.claim_task(running.id)
    waiting = repository.create_task(conversation_id=queued.conversation_id, user_message_id=None, context={})
    waiting.status = TaskStatus.WAITING_APPROVAL.value
    session.commit()
    repository.append_task_event(waiting.id, TaskEventType.TASK_PAUSED, status=TaskStatus.WAITING_APPROVAL)
    completed = repository.create_task(conversation_id=queued.conversation_id, user_message_id=None, context={})
    repository.claim_task(completed.id)
    message = repository.complete_task_with_message(completed.id, queued.conversation_id, "已完成的回答")
    task_ids = (queued.id, running.id, waiting.id, completed.id)
    conversation_id, message_id = queued.conversation_id, message.id
    started, startup_calls, shutdown_calls = [], [], []
    monkeypatch.setattr(assistant_task_runner, "_session_factory", sessionmaker(bind=engine))
    monkeypatch.setattr(assistant_task_runner, "start_message", lambda task_id, cid: started.append((task_id, cid)))

    def startup_probe():
        assert "init_db" in initialization and "seed_sample_stocks" in initialization
        startup_calls.append(True)

    monkeypatch.setattr(server.app.router, "on_startup", [*server.app.router.on_startup, startup_probe])
    monkeypatch.setattr(server.app.router, "on_shutdown", [lambda: shutdown_calls.append(True)])

    async def run():
        async with server.app.router.lifespan_context(server.app):
            session.expire_all()
            assert repository.get_task_snapshot(task_ids[1])["error_code"] == "worker_restarted"
            if body_fails:
                raise RuntimeError("test lifespan body failure")

    try:
        if body_fails:
            with pytest.raises(RuntimeError, match="test lifespan body failure"):
                asyncio.run(run())
        else:
            asyncio.run(run())
        session.expire_all()
        assert started == [(task_ids[0], conversation_id)]
        assert startup_calls == shutdown_calls == [True]
        assert repository.get_task_snapshot(task_ids[1])["status"] == "failed"
        assert repository.get_task_snapshot(task_ids[2])["status"] == "awaiting_approval"
        assert repository.get_task_snapshot(task_ids[3])["status"] == "completed"
        assert repository.get_task_run(task_ids[3]).final_message_id == message_id
        failures = [n for n in repository.get_activity()["notifications"] if n["task_id"] == task_ids[1]]
        assert len(failures) == 1 and failures[0]["kind"] == "failed"
        for scheduler in schedulers:
            scheduler.start.assert_called_once()
            scheduler.shutdown.assert_called_once()
    finally:
        session.close()
        engine.dispose()


def test_production_lifespan_does_not_recover_before_database_initialization(monkeypatch):
    import server

    _, schedulers = _isolate_startup(monkeypatch, server)
    startup = Mock()
    monkeypatch.setattr(server.app.router, "on_startup", [startup])
    monkeypatch.setattr(server, "init_db", Mock(side_effect=RuntimeError("database unavailable")))

    async def run():
        async with server.app.router.lifespan_context(server.app):
            pytest.fail("startup must fail")

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(run())
    startup.assert_not_called()
    for scheduler in schedulers:
        scheduler.start.assert_not_called()


def test_startup_isolation_keeps_real_threads_for_asyncio_teardown(monkeypatch):
    """_isolate_startup 只允许屏蔽股票缓存刷新线程,其余 Thread 必须是真家伙。

    asyncio.run() 收尾的 shutdown_default_executor() 依赖 threading.Thread 起
    关闭辅助线程;若这里被全局 Mock,每个走默认线程池的 lifespan 用例 teardown
    都会静默等待 THREAD_JOIN_TIMEOUT(300 秒)。
    """
    import threading
    from unittest.mock import Mock as _Mock

    import server

    real_thread = threading.Thread
    with pytest.MonkeyPatch.context() as mp:
        _isolate_startup(mp, server)

        def refresh_stock_cache():  # 与 server.lifespan 内的闭包同名
            raise AssertionError("stock-cache refresh thread must be suppressed")

        assert isinstance(threading.Thread(target=lambda: None), real_thread)
        assert isinstance(threading.Thread(target=lambda: None, daemon=True), real_thread)
        assert not isinstance(threading.Thread(target=refresh_stock_cache, daemon=True), real_thread)
        assert isinstance(threading.Thread(target=refresh_stock_cache, daemon=True), _Mock)
    assert threading.Thread is real_thread  # monkeypatch 退出后完全还原


def test_reload_worker_double_import_initializes_server_once(monkeypatch):
    import importlib
    import runpy
    from pathlib import Path
    import server

    # Uvicorn's spawned worker executes the entrypoint as __mp_main__, then
    # imports server:app. Both names share the bootstrap app/router.
    monkeypatch.setattr(server.app.router, "lifespan_context", server.app.router.lifespan_context)
    worker = runpy.run_path(str(Path(server.__file__)), run_name="__mp_main__")
    nested_startup = Mock(side_effect=AssertionError("entrypoint startup must not be nested"))
    worker["lifespan"].__wrapped__.__globals__["init_db"] = nested_startup
    importlib.reload(server)
    initialization, schedulers = _isolate_startup(monkeypatch, server)
    startup, shutdown = Mock(), Mock()
    monkeypatch.setattr(server.app.router, "on_startup", [startup])
    monkeypatch.setattr(server.app.router, "on_shutdown", [shutdown])

    async def run():
        async with server.app.router.lifespan_context(server.app):
            assert initialization.count("init_db") == 1
            assert initialization.index("warmup_javascript_runtime") < initialization.index("register_mcp_log_cleanup")

    asyncio.run(run())
    nested_startup.assert_not_called()
    startup.assert_called_once()
    shutdown.assert_called_once()
    for scheduler in schedulers:
        scheduler.start.assert_called_once()
        scheduler.shutdown.assert_called_once()
