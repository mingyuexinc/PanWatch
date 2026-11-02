"""自定义交易记录服务行为: 策略/版本/建档/分页/排序/统计口径。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.trading_journal import service as svc
from src.platform.persistence.database import Base
from src.platform.persistence.models import TradeJournalEntry


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def version_id(db):
    _, version = svc.ensure_default_hierarchy(db)
    return version.id


def _create(db, version_id, *, name="测试股", date="2026-10-09", return_pct=None, **extra):
    return svc.create_entry(
        db,
        version_id,
        trade_date=date,
        stock_name=name,
        sector=extra.get("sector", "半导体"),
        open_change_pct=extra.get("open_change_pct", 5.2),
        seal_result=extra.get("seal_result", "2连板"),
        sell_timing=extra.get("sell_timing", "次日竞价"),
        return_pct=return_pct if return_pct is not None else extra.get("return_pct"),
        review_note=extra.get("review_note", ""),
    )


def test_strategy_and_version_crud(db):
    strategies = svc.list_strategies(db)
    assert len(strategies) >= 1
    default_id = strategies[0]["id"]
    versions = svc.list_versions(db, default_id)
    assert versions and len(versions) >= 1

    s = svc.create_strategy(db, name="打板策略", description="短线")
    assert s.name == "打板策略"
    vers = svc.list_versions(db, s.id)
    assert len(vers) == 1 and vers[0]["name"] == "v1"

    v2 = svc.create_version(db, s.id, name="v2", description="迭代")
    assert v2 and v2.name == "v2"

    updated = svc.update_strategy(db, s.id, name="打板A", description="改简介")
    assert updated.name == "打板A"
    v2u = svc.update_version(db, v2.id, name="v2.1")
    assert v2u.name == "v2.1"


def test_create_inserts_at_top_of_first_page(db, version_id):
    first = _create(db, version_id, name="早记录")
    second = _create(db, version_id, name="晚记录")
    payload = svc.list_entries(db, version_id, page=1, page_size=20)
    assert [i["stock_name"] for i in payload["items"]] == ["晚记录", "早记录"]
    assert payload["total"] == 2
    assert second.sort_order < first.sort_order


def test_list_scoped_by_version(db, version_id):
    _, default_version = svc.ensure_default_hierarchy(db)
    other = svc.create_version(db, default_version.strategy_id, name="v2")
    _create(db, version_id, name="A")
    _create(db, other.id, name="B")
    assert svc.list_entries(db, version_id)["total"] == 1
    assert svc.list_entries(db, other.id)["total"] == 1


def test_move_entries_same_strategy(db, version_id):
    _, default_version = svc.ensure_default_hierarchy(db)
    v2 = svc.create_version(db, default_version.strategy_id, name="v2")
    a = _create(db, version_id, name="A")
    b = _create(db, version_id, name="B")
    moved = svc.move_entries_to_version(db, [a.id, b.id], v2.id)
    assert moved == 2
    assert svc.list_entries(db, version_id)["total"] == 0
    assert svc.list_entries(db, v2.id)["total"] == 2


def test_reorder_within_version(db, version_id):
    entries = [_create(db, version_id, name=f"股{i}") for i in range(1, 6)]
    by_name = {e.stock_name: e.id for e in entries}
    assert svc.reorder_entries(
        db, version_id, [by_name["股2"], by_name["股4"], by_name["股3"]]
    )
    order = [
        e.stock_name
        for e in db.query(TradeJournalEntry)
        .filter(TradeJournalEntry.version_id == version_id)
        .order_by(TradeJournalEntry.sort_order.asc())
        .all()
    ]
    assert order == ["股5", "股2", "股4", "股3", "股1"]


def test_stats_scoped_by_version(db, version_id):
    _, default_version = svc.ensure_default_hierarchy(db)
    v2 = svc.create_version(db, default_version.strategy_id, name="v2")
    _create(db, version_id, return_pct=10.0)
    _create(db, v2.id, return_pct=-50.0)
    stats = svc.compute_stats(db, version_id)
    assert stats["total"] == 1
    assert stats["win_count"] == 1


def test_stats_win_rate_and_compounded_return(db, version_id):
    _create(db, version_id, return_pct=10.0)
    _create(db, version_id, return_pct=-5.0)
    _create(db, version_id, return_pct=10.0)
    _create(db, version_id, return_pct=None)
    stats = svc.compute_stats(db, version_id)
    assert stats["total"] == 4
    assert stats["counted"] == 3
    assert stats["win_count"] == 2
    assert stats["win_rate"] == pytest.approx(2 / 3)
    assert stats["total_return_pct"] == pytest.approx(14.95)


def test_delete_version_with_entries_blocked(db, version_id):
    _, default_version = svc.ensure_default_hierarchy(db)
    svc.create_version(db, default_version.strategy_id, name="v2")
    _create(db, version_id, name="x")
    assert svc.delete_version(db, version_id) == "has_entries"


def test_delete_last_strategy_blocked(db):
    strategies = svc.list_strategies(db)
    assert svc.delete_strategy(db, strategies[0]["id"]) is False
