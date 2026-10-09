"""自定义交易记录服务行为: 建档/分页/更新/删除/拖拽排序/一键统计口径。"""

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


def _create(db, *, name="测试股", date="2026-10-09", return_pct=None, **extra):
    return svc.create_entry(
        db,
        trade_date=date,
        stock_name=name,
        sector=extra.get("sector", "半导体"),
        open_change_pct=extra.get("open_change_pct", 5.2),
        seal_result=extra.get("seal_result", "2连板"),
        sell_timing=extra.get("sell_timing", "次日竞价"),
        return_pct=return_pct if return_pct is not None else extra.get("return_pct"),
        review_note=extra.get("review_note", ""),
    )


def test_create_inserts_at_top_of_first_page(db):
    first = _create(db, name="早记录")
    second = _create(db, name="晚记录")
    payload = svc.list_entries(db, page=1, page_size=20)
    assert [i["stock_name"] for i in payload["items"]] == ["晚记录", "早记录"]
    assert payload["total"] == 2
    assert payload["pages"] == 1
    assert second.sort_order < first.sort_order


def test_list_pages_twenty_per_page_and_clamps_overflow(db):
    for i in range(25):
        _create(db, name=f"股{i:02d}")
    page1 = svc.list_entries(db, page=1, page_size=20)
    page2 = svc.list_entries(db, page=2, page_size=20)
    page3 = svc.list_entries(db, page=3, page_size=20)
    assert page1["total"] == 25 and len(page1["items"]) == 20
    assert len(page2["items"]) == 5
    # 页码越界收敛到最后一页
    assert page3["page"] == 2 and len(page3["items"]) == 5
    # 各页之间不重叠
    ids = {i["id"] for i in page1["items"]} | {i["id"] for i in page2["items"]}
    assert len(ids) == 25


def test_update_entry_fields(db):
    entry = _create(db, return_pct=5.0)
    updated = svc.update_entry(
        db, entry.id, return_pct=-2.5, review_note="炸板,纪律性离场"
    )
    assert updated.return_pct == -2.5
    assert updated.review_note == "炸板,纪律性离场"
    assert svc.update_entry(db, 9999, review_note="x") is None


def test_delete_single_and_batch(db):
    a, b, c = _create(db, name="A"), _create(db, name="B"), _create(db, name="C")
    assert svc.delete_entries(db, [a.id]) == 1
    assert svc.delete_entries(db, [b.id, c.id, 9999]) == 2
    assert svc.list_entries(db)["total"] == 0


def test_reorder_keeps_group_at_original_range(db):
    entries = [_create(db, name=f"股{i}") for i in range(1, 6)]
    by_name = {e.stock_name: e.id for e in entries}
    # 全局顺序(后建在前): 股5 股4 股3 股2 股1
    # 把第 2~4 条(股4 股3 股2)拖拽为 股2 股4 股3
    svc.reorder_entries(db, [by_name["股2"], by_name["股4"], by_name["股3"]])
    order = [e.stock_name for e in db.query(TradeJournalEntry).order_by(
        TradeJournalEntry.sort_order.asc()).all()]
    assert order == ["股5", "股2", "股4", "股3", "股1"]
    # 重编号后序号连续 1..N
    orders = [e.sort_order for e in db.query(TradeJournalEntry).order_by(
        TradeJournalEntry.sort_order.asc()).all()]
    assert orders == [1, 2, 3, 4, 5]


def test_stats_win_rate_and_compounded_return(db):
    _create(db, return_pct=10.0)   # 盈
    _create(db, return_pct=-5.0)   # 亏
    _create(db, return_pct=10.0)   # 盈
    _create(db, return_pct=None)   # 未填收益率 → 不参与
    stats = svc.compute_stats(db)
    assert stats["total"] == 4
    assert stats["counted"] == 3
    assert stats["win_count"] == 2
    assert stats["win_rate"] == pytest.approx(2 / 3)
    # 1.1 * 0.95 * 1.1 - 1 = 0.1495 → 14.95%
    assert stats["total_return_pct"] == pytest.approx(14.95)


def test_stats_empty_when_no_return_values(db):
    _create(db, return_pct=None)
    stats = svc.compute_stats(db)
    assert stats == {
        "total": 1,
        "counted": 0,
        "win_count": 0,
        "win_rate": None,
        "total_return_pct": None,
    }
