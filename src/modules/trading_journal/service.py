"""自定义交易记录的读写与统计口径。

- 列表按 (sort_order, id) 升序分页;新记录插到最前,出现在第一页顶部;
- 页内拖拽排序按"新顺序放回首条原位置、其余条目保持相对顺序"全表重编号,
  天然修复删除留下的序号空洞;
- 统计口径: 收益率为空的记录不参与计算;
  盈利概率 = 收益率>0 的条数 / 有效条数;
  总收益率 = ∏(1 + r/100) - 1(乘法复合,百分比)。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from src.platform.persistence.models import TradeJournalEntry

DEFAULT_PAGE_SIZE = 20


def _ordered_query(db: Session):
    return db.query(TradeJournalEntry).order_by(
        TradeJournalEntry.sort_order.asc(), TradeJournalEntry.id.asc()
    )


def serialize_entry(entry: TradeJournalEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "trade_date": entry.trade_date,
        "stock_name": entry.stock_name,
        "sector": entry.sector,
        "open_change_pct": entry.open_change_pct,
        "seal_result": entry.seal_result,
        "sell_timing": entry.sell_timing,
        "return_pct": entry.return_pct,
        "review_note": entry.review_note,
        "sort_order": entry.sort_order,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
    }


def list_entries(
    db: Session, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE
) -> dict[str, Any]:
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    total = db.query(TradeJournalEntry).count()
    pages = max(1, (total + page_size - 1) // page_size)
    # 页码越界时收敛到最后一页,避免删除末页唯一记录后停留在空页
    page = min(page, pages)
    rows = (
        _ordered_query(db)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": [serialize_entry(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


def create_entry(db: Session, **fields: Any) -> TradeJournalEntry:
    min_order = db.query(TradeJournalEntry.sort_order).order_by(
        TradeJournalEntry.sort_order.asc()
    ).first()
    entry = TradeJournalEntry(
        trade_date=fields["trade_date"],
        stock_name=fields["stock_name"],
        sector=fields.get("sector") or "",
        open_change_pct=fields.get("open_change_pct"),
        seal_result=fields.get("seal_result") or "",
        sell_timing=fields.get("sell_timing") or "",
        return_pct=fields.get("return_pct"),
        review_note=fields.get("review_note") or "",
        # 新记录插到最前:比当前最小序号再小 1,落位第一页顶部
        sort_order=(min_order[0] - 1) if min_order and min_order[0] is not None else 1,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def update_entry(
    db: Session, entry_id: int, **fields: Any
) -> TradeJournalEntry | None:
    entry = db.query(TradeJournalEntry).filter(TradeJournalEntry.id == entry_id).first()
    if entry is None:
        return None
    column_attrs = (
        "trade_date",
        "stock_name",
        "sector",
        "open_change_pct",
        "seal_result",
        "sell_timing",
        "return_pct",
        "review_note",
    )
    for name in column_attrs:
        if name in fields:
            setattr(entry, name, fields[name])
    db.commit()
    db.refresh(entry)
    return entry


def delete_entries(db: Session, ids: list[int]) -> int:
    if not ids:
        return 0
    removed = (
        db.query(TradeJournalEntry)
        .filter(TradeJournalEntry.id.in_(ids))
        .delete(synchronize_session=False)
    )
    db.commit()
    return removed


def reorder_entries(db: Session, ordered_ids: list[int]) -> None:
    """把 ordered_ids(通常是当前页的全部条目)按给定顺序放回这组条目原在的
    全局位置,其余条目保持相对顺序,然后全表 1..N 重编号。"""
    if len(ordered_ids) < 2:
        return
    rows = _ordered_query(db).all()
    id_set = set(ordered_ids)
    members = [r for r in rows if r.id in id_set]
    if not members:
        return
    # 首个成员在全局顺序中的位置即这组条目的落点;该位置之前的条目都不属于
    # 这组,kept[:group_start] 恰好是它们
    group_start = rows.index(members[0])
    kept = [r for r in rows if r.id not in id_set]
    order_by_input = {entry_id: idx for idx, entry_id in enumerate(ordered_ids)}
    moved = sorted(members, key=lambda r: order_by_input[r.id])
    final = kept[:group_start] + moved + kept[group_start:]
    for idx, row in enumerate(final, start=1):
        row.sort_order = idx
    db.commit()


def compute_stats(db: Session) -> dict[str, Any]:
    total = db.query(TradeJournalEntry).count()
    values = [
        row[0]
        for row in db.query(TradeJournalEntry.return_pct)
        .filter(TradeJournalEntry.return_pct.isnot(None))
        .all()
    ]
    win_count = sum(1 for v in values if v > 0)
    compounded = 1.0
    for v in values:
        compounded *= 1.0 + v / 100.0
    return {
        "total": total,
        "counted": len(values),
        "win_count": win_count,
        # 盈利概率 = 盈利次数 / 有效次数;无有效记录时为 None(前端展示 --)
        "win_rate": round(win_count / len(values), 6) if values else None,
        # 总收益率 = ∏(1 + r/100) - 1,以百分比表达
        "total_return_pct": round((compounded - 1.0) * 100.0, 6) if values else None,
    }
