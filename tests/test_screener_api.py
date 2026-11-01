"""自然语言选股 API 测试。"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.platform.persistence.models as _models  # noqa: F401
from src.modules.administration.api.auth import get_current_user
from src.modules.market.api import screener
from src.platform.persistence.database import Base, get_db
from src.modules.market.early_surge_monitor.replay import PoolScanStats
from src.modules.market.early_surge_monitor.rules import TriggerDecision
from src.platform.persistence.models import NaturalLanguageScreenerQuery, NotifyChannel
from src.platform.persistence import migrations as migrations_mod


def _client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        migrations_mod._m134_nl_screener_queries(conn)
        migrations_mod._m135_nl_screener_intraday_scores(conn)
        migrations_mod._m137_nl_screener_monitor_strategy(conn)
        migrations_mod._m138_screener_monitor_notify(conn)
        migrations_mod._m139_screener_action_timestamps(conn)
        migrations_mod._m140_screener_auto_monitor(conn)
    Session = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(screener.router, prefix="/api/screener")

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: "test-user"
    return TestClient(app), Session


def test_screener_crud_and_run_mock():
    client, Session = _client()

    r = client.get("/api/screener/queries")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 1

    r = client.post(
        "/api/screener/queries",
        json={"label": "测试", "query_text": "今日连板的个股", "select_type": "A股"},
    )
    assert r.status_code == 200
    qid = r.json()["id"]

    fake_result = type(
        "R",
        (),
        {
            "rows": ({"股票代码": "600000", "股票名称": "浦发银行", "连板天数": "2", "涨跌幅": "10.01"},),
            "column_names": ("股票代码", "股票名称", "连板天数", "涨跌幅"),
            "row_count": 1,
            "data_source": "dataList",
        },
    )()

    fake_validation = type(
        "V",
        (),
        {"ok": True, "checked_codes": ["600000"], "failures": [], "warnings": []},
    )()

    class FakeMd:
        def natural_language_screen(self, *a, **k):
            return fake_result

        def validate_continuous_limit_up_screen(self, *a, **k):
            return fake_validation

    with patch("src.modules.market.nl_screener_service.resolve_em_api_key", return_value="test-key"):
        with patch("src.modules.market.nl_screener_service.get_market_data", return_value=FakeMd()):
            r = client.post(f"/api/screener/queries/{qid}/run")
    assert r.status_code == 200
    body = r.json()
    assert body["row_count"] == 1
    assert body["status"] == "fresh"

    r = client.put(f"/api/screener/queries/{qid}", json={"label": "测试2"})
    assert r.status_code == 200
    assert r.json()["label"] == "测试2"

    r = client.get("/api/screener/merged")
    assert r.status_code == 200
    assert r.json()["mock"] is True

    with patch(
        "src.modules.market.nl_screener_service.compute_strength_score",
        return_value=72.5,
    ):
        r = client.post(f"/api/screener/queries/{qid}/compute-intraday-scores", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["intraday_scores"]["scores"]["600000"]["score"] == 72.5
    assert body["intraday_compute"]["computed_count"] == 1

    r = client.delete(f"/api/screener/queries/{qid}")
    assert r.status_code == 200

    db = Session()
    try:
        assert db.query(NaturalLanguageScreenerQuery).filter_by(id=qid).first() is None
    finally:
        db.close()


def test_screener_strategy_get_and_update():
    client, _Session = _client()

    r = client.get("/api/screener/strategy")
    assert r.status_code == 200
    body = r.json()
    assert "config" in body
    assert body["config"]["gain_pct"] == 3
    assert body["config"]["monitor_start"] == "09:30"

    r = client.post(
        "/api/screener/queries",
        json={"label": "策略问句", "query_text": "测试", "select_type": "A股"},
    )
    qid = r.json()["id"]

    r = client.put(
        "/api/screener/strategy",
        json={"query_id": qid, "monitor_start": "09:31", "monitor_end": "09:45", "gain_pct": 5},
    )
    assert r.status_code == 200
    cfg = r.json()["config"]
    assert cfg["query_id"] == qid
    assert cfg["monitor_start"] == "09:31"
    assert cfg["monitor_end"] == "09:45"
    assert cfg["gain_pct"] == 5

    r = client.put(
        "/api/screener/strategy",
        json={"monitor_start": "09:45", "monitor_end": "09:40"},
    )
    assert r.status_code == 400


def test_screener_test_trigger_closed_loop_mock():
    """问句结果 → 建池 → 1m 回放触发 → 企微推送（全 mock）。"""
    client, Session = _client()
    db = Session()
    try:
        ch = NotifyChannel(
            name="拉升测试",
            type="wecom",
            config={"webhook_key": "test-webhook"},
            enabled=True,
        )
        db.add(ch)
        db.commit()
        db.refresh(ch)
        channel_id = ch.id
    finally:
        db.close()

    r = client.post(
        "/api/screener/queries",
        json={"label": "N-10", "query_text": "前10日有涨停", "select_type": "A股"},
    )
    qid = r.json()["id"]

    fake_result = type(
        "R",
        (),
        {
            "rows": ({"股票代码": "600000", "股票名称": "浦发银行"},),
            "column_names": ("股票代码", "股票名称"),
            "row_count": 1,
            "data_source": "dataList",
        },
    )()
    fake_validation = type("V", (), {"ok": True, "checked_codes": ["600000"], "failures": [], "warnings": []})()

    class FakeMd:
        def natural_language_screen(self, *a, **k):
            return fake_result

        def validate_continuous_limit_up_screen(self, *a, **k):
            return fake_validation

    with patch("src.modules.market.nl_screener_service.resolve_em_api_key", return_value="test-key"):
        with patch("src.modules.market.nl_screener_service.get_market_data", return_value=FakeMd()):
            r = client.post(f"/api/screener/queries/{qid}/run")
    assert r.status_code == 200

    r = client.put(
        "/api/screener/strategy",
        json={"query_id": qid, "gain_pct": 3, "notify_channel_id": channel_id},
    )
    assert r.status_code == 200

    cn = ZoneInfo("Asia/Shanghai")
    trig_at = datetime(2026, 10, 30, 9, 34, 0, tzinfo=cn)
    fired = [
        (
            "600000",
            TriggerDecision(
                True,
                False,
                "ok",
                prev_close=10.0,
                trigger_price=10.35,
                trigger_at=trig_at,
            ),
        )
    ]
    stats = PoolScanStats(symbols_in_pool=1, replay_poll_points=5, hits=1)

    with patch(
        "src.modules.market.screener_monitor_service._build_prev_close_map",
        return_value={"600000": 10.0},
    ):
        with patch(
            "src.modules.market.screener_monitor_service.scan_pool_from_mkline",
            return_value=(fired, stats),
        ):
            with patch(
                "src.modules.market.screener_monitor_notify._send_one",
                new=AsyncMock(return_value=(True, "")),
            ):
                with patch(
                    "src.modules.market.screener_monitor_service._market_trade_date_on_or_before",
                    return_value="2026-10-30",
                ):
                    r = client.post("/api/screener/strategy/test-trigger")

    assert r.status_code == 200
    body = r.json()
    scan = body["scan"]
    assert scan["new_triggers"] == 1
    assert scan["mode"] == "mkline_replay"
    assert scan["push"]["sent"] == 1
    assert scan["push"]["channel"]["name"] == "拉升测试"
    assert scan.get("finished_at") and len(scan["finished_at"]) == 19
    assert body["config"].get("last_test_trigger_at") == scan["finished_at"]

    db = Session()
    try:
        from src.platform.persistence.models import NlScreenerMonitorTrigger

        rows = db.query(NlScreenerMonitorTrigger).filter_by(trade_date="2026-10-30").all()
        assert len(rows) == 1
        assert rows[0].symbol == "600000"
        assert rows[0].notify_sent_at is not None
    finally:
        db.close()
