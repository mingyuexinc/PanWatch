"""分时线质量评估样本表 CRUD 与批量算分。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.modules.intraday_quality.baostock_fetch import baostock_session, normalize_cn_symbol, today_iso
from src.modules.intraday_quality.scoring import compute_strength_score
from src.platform.persistence.models import IntradayQualitySample

logger = logging.getLogger(__name__)

SAMPLE_TYPES = frozenset({"positive", "negative", "boundary"})


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _row(item: IntradayQualitySample) -> dict:
    return {
        "id": item.id,
        "symbol": item.symbol,
        "market": item.market,
        "name": item.name,
        "test_date": item.test_date,
        "sample_type": item.sample_type,
        "strength_score": item.strength_score,
        "note": item.note or "",
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def list_samples(db: Session) -> dict:
    items = (
        db.query(IntradayQualitySample)
        .order_by(IntradayQualitySample.id.asc())
        .all()
    )
    saved = max((i.updated_at for i in items if i.updated_at), default=None)
    return {
        "items": [_row(i) for i in items],
        "saved_at": saved.isoformat().replace("+00:00", "Z") if saved else None,
        "sample_types": sorted(SAMPLE_TYPES),
    }


def add_sample(
    db: Session,
    *,
    symbol: str,
    market: str,
    name: str,
    test_date: str | None = None,
    sample_type: str = "positive",
    note: str = "",
) -> IntradayQualitySample:
    mk = (market or "CN").upper()
    sym = normalize_cn_symbol(symbol) if mk == "CN" else (symbol or "").strip()
    if not sym:
        raise ValueError("股票代码不能为空")
    st = (sample_type or "positive").strip().lower()
    if st not in SAMPLE_TYPES:
        raise ValueError("样本类型无效")
    td = (test_date or today_iso()).strip()
    item = IntradayQualitySample(
        symbol=sym,
        market=mk,
        name=(name or sym).strip(),
        test_date=td,
        sample_type=st,
        note=(note or "").strip(),
        strength_score=None,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def update_sample(
    db: Session,
    item_id: int,
    *,
    test_date: str | None = None,
    sample_type: str | None = None,
    note: str | None = None,
) -> IntradayQualitySample | None:
    item = db.query(IntradayQualitySample).filter(IntradayQualitySample.id == item_id).first()
    if not item:
        return None
    if test_date is not None:
        item.test_date = test_date.strip()
    if sample_type is not None:
        st = sample_type.strip().lower()
        if st not in SAMPLE_TYPES:
            raise ValueError("样本类型无效")
        item.sample_type = st
    if note is not None:
        item.note = note.strip()
    db.commit()
    db.refresh(item)
    return item


def remove_samples(db: Session, ids: list[int]) -> int:
    if not ids:
        return 0
    q = db.query(IntradayQualitySample).filter(IntradayQualitySample.id.in_(ids))
    count = q.count()
    q.delete(synchronize_session=False)
    db.commit()
    return count


def _compute_sample_rows(db: Session, items: list[IntradayQualitySample]) -> list[dict]:
    errors: list[dict] = []
    with baostock_session() as session:
        for item in items:
            sym = normalize_cn_symbol(item.symbol) if (item.market or "CN").upper() == "CN" else item.symbol
            td = (item.test_date or "").strip()
            try:
                score = compute_strength_score(sym, item.market, td, session=session)
                item.strength_score = score
                if sym != item.symbol:
                    item.symbol = sym
            except Exception as exc:
                logger.warning(
                    "intraday quality score failed %s %s %s: %s",
                    item.symbol,
                    td,
                    item.market,
                    exc,
                )
                item.strength_score = None
                errors.append(
                    {
                        "id": item.id,
                        "symbol": item.symbol,
                        "test_date": td,
                        "message": str(exc),
                    }
                )
    db.commit()
    return errors


def compute_samples(db: Session, ids: list[int] | None = None) -> dict:
    q = db.query(IntradayQualitySample).order_by(IntradayQualitySample.id.asc())
    if ids is not None:
        id_list = list(dict.fromkeys(ids))
        if not id_list:
            raise ValueError("请至少选择一条样本")
        q = q.filter(IntradayQualitySample.id.in_(id_list))
        items = q.all()
        found = {i.id for i in items}
        missing = [i for i in id_list if i not in found]
        if missing:
            raise ValueError(f"样本不存在: {missing}")
    else:
        items = q.all()
    errors = _compute_sample_rows(db, items)
    payload = list_samples(db)
    payload["errors"] = errors
    payload["computed_count"] = len(items)
    payload["computed_at"] = _iso_now()
    return payload


def compute_all(db: Session) -> dict:
    return compute_samples(db, ids=None)
