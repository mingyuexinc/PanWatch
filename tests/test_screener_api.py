"""自然语言选股 API 测试。"""

from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.platform.persistence.models as _models  # noqa: F401
from src.modules.administration.api.auth import get_current_user
from src.modules.market.api import screener
from src.platform.persistence.database import Base, get_db
from src.platform.persistence.models import NaturalLanguageScreenerQuery
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
