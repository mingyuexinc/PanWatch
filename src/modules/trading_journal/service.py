"""自定义交易记录的读写与统计口径。

层级: 策略 → 版本 → 记录。列表/排序/统计均按 version_id 隔离;
一键统计仅针对当前策略下选定版本内的记录。

- 列表按 (sort_order, id) 升序分页;新记录插到该版本最前;
- 页内拖拽排序在同版本内全表重编号;
- 统计口径: 收益率为空的记录不参与计算。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from src.platform.persistence.models import (
    TradeJournalEntry,
    TradeJournalStrategy,
    TradeJournalVersion,
)

DEFAULT_PAGE_SIZE = 20
DEFAULT_STRATEGY_NAME = "默认策略"
DEFAULT_VERSION_NAME = "默认版本"


def _ordered_entries_query(db: Session, version_id: int):
    return (
        db.query(TradeJournalEntry)
        .filter(TradeJournalEntry.version_id == version_id)
        .order_by(TradeJournalEntry.sort_order.asc(), TradeJournalEntry.id.asc())
    )


def ensure_default_hierarchy(db: Session) -> tuple[TradeJournalStrategy, TradeJournalVersion]:
    """保证至少存在一个默认策略与默认版本(幂等)。"""
    strategy = (
        db.query(TradeJournalStrategy)
        .order_by(TradeJournalStrategy.sort_order.asc(), TradeJournalStrategy.id.asc())
        .first()
    )
    if strategy is None:
        strategy = TradeJournalStrategy(
            name=DEFAULT_STRATEGY_NAME,
            description="",
            sort_order=1,
        )
        db.add(strategy)
        db.flush()
    version = (
        db.query(TradeJournalVersion)
        .filter(TradeJournalVersion.strategy_id == strategy.id)
        .order_by(TradeJournalVersion.sort_order.asc(), TradeJournalVersion.id.asc())
        .first()
    )
    if version is None:
        version = TradeJournalVersion(
            strategy_id=strategy.id,
            name=DEFAULT_VERSION_NAME,
            description="",
            sort_order=1,
        )
        db.add(version)
        db.commit()
        db.refresh(strategy)
        db.refresh(version)
    else:
        db.commit()
        db.refresh(strategy)
        db.refresh(version)
    return strategy, version


def serialize_strategy(row: TradeJournalStrategy) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description or "",
        "sort_order": row.sort_order,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def serialize_version(row: TradeJournalVersion) -> dict[str, Any]:
    return {
        "id": row.id,
        "strategy_id": row.strategy_id,
        "name": row.name,
        "description": row.description or "",
        "sort_order": row.sort_order,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_strategies(db: Session) -> list[dict[str, Any]]:
    ensure_default_hierarchy(db)
    rows = (
        db.query(TradeJournalStrategy)
        .order_by(TradeJournalStrategy.sort_order.asc(), TradeJournalStrategy.id.asc())
        .all()
    )
    return [serialize_strategy(r) for r in rows]


def create_strategy(db: Session, *, name: str, description: str = "") -> TradeJournalStrategy:
    max_order = (
        db.query(TradeJournalStrategy.sort_order)
        .order_by(TradeJournalStrategy.sort_order.desc())
        .first()
    )
    row = TradeJournalStrategy(
        name=name,
        description=description,
        sort_order=(max_order[0] + 1) if max_order and max_order[0] is not None else 1,
    )
    db.add(row)
    db.flush()
    db.add(
        TradeJournalVersion(
            strategy_id=row.id,
            name="v1",
            description="",
            sort_order=1,
        )
    )
    db.commit()
    db.refresh(row)
    return row


def update_strategy(
    db: Session, strategy_id: int, **fields: Any
) -> TradeJournalStrategy | None:
    row = db.query(TradeJournalStrategy).filter(TradeJournalStrategy.id == strategy_id).first()
    if row is None:
        return None
    for name in ("name", "description"):
        if name in fields:
            setattr(row, name, fields[name])
    db.commit()
    db.refresh(row)
    return row


def delete_strategy(db: Session, strategy_id: int) -> bool:
    row = db.query(TradeJournalStrategy).filter(TradeJournalStrategy.id == strategy_id).first()
    if row is None:
        return False
    total_strategies = db.query(TradeJournalStrategy).count()
    if total_strategies <= 1:
        return False
    version_ids = [
        v.id
        for v in db.query(TradeJournalVersion)
        .filter(TradeJournalVersion.strategy_id == strategy_id)
        .all()
    ]
    if version_ids:
        db.query(TradeJournalEntry).filter(TradeJournalEntry.version_id.in_(version_ids)).delete(
            synchronize_session=False
        )
        db.query(TradeJournalVersion).filter(TradeJournalVersion.strategy_id == strategy_id).delete(
            synchronize_session=False
        )
    db.delete(row)
    db.commit()
    return True


def list_versions(db: Session, strategy_id: int) -> list[dict[str, Any]] | None:
    if db.query(TradeJournalStrategy).filter(TradeJournalStrategy.id == strategy_id).first() is None:
        return None
    rows = (
        db.query(TradeJournalVersion)
        .filter(TradeJournalVersion.strategy_id == strategy_id)
        .order_by(TradeJournalVersion.sort_order.asc(), TradeJournalVersion.id.asc())
        .all()
    )
    return [serialize_version(r) for r in rows]


def get_version(db: Session, version_id: int) -> TradeJournalVersion | None:
    return db.query(TradeJournalVersion).filter(TradeJournalVersion.id == version_id).first()


def create_version(
    db: Session, strategy_id: int, *, name: str, description: str = ""
) -> TradeJournalVersion | None:
    if db.query(TradeJournalStrategy).filter(TradeJournalStrategy.id == strategy_id).first() is None:
        return None
    max_order = (
        db.query(TradeJournalVersion.sort_order)
        .filter(TradeJournalVersion.strategy_id == strategy_id)
        .order_by(TradeJournalVersion.sort_order.desc())
        .first()
    )
    row = TradeJournalVersion(
        strategy_id=strategy_id,
        name=name,
        description=description,
        sort_order=(max_order[0] + 1) if max_order and max_order[0] is not None else 1,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_version(
    db: Session, version_id: int, **fields: Any
) -> TradeJournalVersion | None:
    row = get_version(db, version_id)
    if row is None:
        return None
    for name in ("name", "description"):
        if name in fields:
            setattr(row, name, fields[name])
    db.commit()
    db.refresh(row)
    return row


def delete_version(db: Session, version_id: int) -> str | None:
    """删除版本。返回 None 表示成功;否则为错误码字符串。"""
    row = get_version(db, version_id)
    if row is None:
        return "not_found"
    version_count = (
        db.query(TradeJournalVersion)
        .filter(TradeJournalVersion.strategy_id == row.strategy_id)
        .count()
    )
    if version_count <= 1:
        return "last_version"
    entry_count = (
        db.query(TradeJournalEntry).filter(TradeJournalEntry.version_id == version_id).count()
    )
    if entry_count > 0:
        return "has_entries"
    db.delete(row)
    db.commit()
    return None


def move_entries_to_version(
    db: Session, entry_ids: list[int], target_version_id: int
) -> int | None:
    """将记录移至目标版本(须与源记录同属一个策略)。返回移动条数;失败返回 None。"""
    if not entry_ids:
        return 0
    target = get_version(db, target_version_id)
    if target is None:
        return None
    entries = (
        db.query(TradeJournalEntry).filter(TradeJournalEntry.id.in_(entry_ids)).all()
    )
    if len(entries) != len(set(entry_ids)):
        return None
    source_version_ids = {e.version_id for e in entries}
    if not source_version_ids:
        return 0
    source_versions = (
        db.query(TradeJournalVersion)
        .filter(TradeJournalVersion.id.in_(source_version_ids))
        .all()
    )
    if any(v.strategy_id != target.strategy_id for v in source_versions):
        return None
    min_order = (
        db.query(TradeJournalEntry.sort_order)
        .filter(TradeJournalEntry.version_id == target_version_id)
        .order_by(TradeJournalEntry.sort_order.asc())
        .first()
    )
    base = (min_order[0] - 1) if min_order and min_order[0] is not None else 1
    for idx, entry in enumerate(entries):
        entry.version_id = target_version_id
        entry.sort_order = base - idx
    db.commit()
    return len(entries)


def serialize_entry(entry: TradeJournalEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "version_id": entry.version_id,
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
    db: Session,
    version_id: int,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> dict[str, Any] | None:
    if get_version(db, version_id) is None:
        return None
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    q = db.query(TradeJournalEntry).filter(TradeJournalEntry.version_id == version_id)
    total = q.count()
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    rows = (
        _ordered_entries_query(db, version_id)
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
        "version_id": version_id,
    }


def create_entry(db: Session, version_id: int, **fields: Any) -> TradeJournalEntry | None:
    if get_version(db, version_id) is None:
        return None
    min_order = (
        db.query(TradeJournalEntry.sort_order)
        .filter(TradeJournalEntry.version_id == version_id)
        .order_by(TradeJournalEntry.sort_order.asc())
        .first()
    )
    entry = TradeJournalEntry(
        version_id=version_id,
        trade_date=fields["trade_date"],
        stock_name=fields["stock_name"],
        sector=fields.get("sector") or "",
        open_change_pct=fields.get("open_change_pct"),
        seal_result=fields.get("seal_result") or "",
        sell_timing=fields.get("sell_timing") or "",
        return_pct=fields.get("return_pct"),
        review_note=fields.get("review_note") or "",
        sort_order=(min_order[0] - 1) if min_order and min_order[0] is not None else 1,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def update_entry(
    db: Session, entry_id: int, version_id: int | None = None, **fields: Any
) -> TradeJournalEntry | None:
    entry = db.query(TradeJournalEntry).filter(TradeJournalEntry.id == entry_id).first()
    if entry is None:
        return None
    if version_id is not None:
        if get_version(db, version_id) is None:
            return None
        entry.version_id = version_id
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


def reorder_entries(db: Session, version_id: int, ordered_ids: list[int]) -> bool:
    if len(ordered_ids) < 2:
        return True
    if get_version(db, version_id) is None:
        return False
    rows = _ordered_entries_query(db, version_id).all()
    id_set = set(ordered_ids)
    members = [r for r in rows if r.id in id_set]
    if not members or len(members) != len(ordered_ids):
        return False
    group_start = rows.index(members[0])
    kept = [r for r in rows if r.id not in id_set]
    order_by_input = {entry_id: idx for idx, entry_id in enumerate(ordered_ids)}
    moved = sorted(members, key=lambda r: order_by_input[r.id])
    final = kept[:group_start] + moved + kept[group_start:]
    for idx, row in enumerate(final, start=1):
        row.sort_order = idx
    db.commit()
    return True


def compute_stats(db: Session, version_id: int) -> dict[str, Any] | None:
    if get_version(db, version_id) is None:
        return None
    q = db.query(TradeJournalEntry).filter(TradeJournalEntry.version_id == version_id)
    total = q.count()
    values = [
        row[0]
        for row in q.with_entities(TradeJournalEntry.return_pct)
        .filter(TradeJournalEntry.return_pct.isnot(None))
        .all()
    ]
    win_count = sum(1 for v in values if v > 0)
    compounded = 1.0
    for v in values:
        compounded *= 1.0 + v / 100.0
    return {
        "version_id": version_id,
        "total": total,
        "counted": len(values),
        "win_count": win_count,
        "win_rate": round(win_count / len(values), 6) if values else None,
        "total_return_pct": round((compounded - 1.0) * 100.0, 6) if values else None,
    }
