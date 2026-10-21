"""自然语言选股问句 CRUD 与运行(东财 selectSecurity)。"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from src.modules.intraday_quality.baostock_fetch import baostock_session, normalize_cn_symbol, today_iso
from src.modules.intraday_quality.scoring import compute_strength_score
from src.platform.marketdata.marketdata_client import get_market_data
from src.platform.marketdata.nl_screener import resolve_em_api_key
from src.platform.persistence.models import NaturalLanguageScreenerQuery
from src.web.errors import api_error

logger = logging.getLogger(__name__)

SELECT_TYPES = frozenset({"A股", "港股", "美股", "基金", "ETF", "可转债", "板块"})
MIN_QUERIES = 1

_CODE_KEY_PATTERNS = (
    re.compile(r"代码", re.I),
    re.compile(r"^code$", re.I),
)
_SH_TZ = ZoneInfo("Asia/Shanghai")


def _query_hash(query_text: str, select_type: str) -> str:
    raw = f"{select_type.strip()}\n{query_text.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _compute_status(row: NaturalLanguageScreenerQuery) -> str:
    if row.last_error and not row.last_run_at:
        return "error"
    if not row.last_run_at:
        return "draft"
    current = _query_hash(row.query_text or "", row.select_type or "A股")
    if row.last_run_query_hash and current != row.last_run_query_hash:
        return "stale"
    if row.last_error:
        return "error"
    return "fresh"


def _row_to_dict(row: NaturalLanguageScreenerQuery, *, include_result: bool) -> dict[str, Any]:
    status = _compute_status(row)
    out: dict[str, Any] = {
        "id": row.id,
        "label": row.label or "",
        "query_text": row.query_text or "",
        "select_type": row.select_type or "A股",
        "sort_order": row.sort_order,
        "status": status,
        "last_run_at": row.last_run_at.isoformat() if row.last_run_at else None,
        "row_count": int(row.last_row_count or 0),
        "data_source": row.last_data_source or "",
        "last_error": row.last_error or "",
        "validation": row.last_validation_json or {},
        "intraday_scores": row.last_intraday_scores_json or {},
    }
    if include_result:
        out["columns"] = list(row.last_columns_json or [])
        out["rows"] = list(row.last_result_json or [])
    return out


def ensure_seed_queries(db: Session) -> None:
    count = db.query(NaturalLanguageScreenerQuery).count()
    if count > 0:
        return
    db.add(
        NaturalLanguageScreenerQuery(
            label="连板",
            query_text="今日连板的个股",
            select_type="A股",
            sort_order=0,
        )
    )
    db.commit()


def list_queries(db: Session) -> list[dict[str, Any]]:
    ensure_seed_queries(db)
    rows = (
        db.query(NaturalLanguageScreenerQuery)
        .order_by(NaturalLanguageScreenerQuery.sort_order, NaturalLanguageScreenerQuery.id)
        .all()
    )
    return [_row_to_dict(r, include_result=False) for r in rows]


def get_query(db: Session, query_id: int, *, include_result: bool = True) -> dict[str, Any]:
    row = db.get(NaturalLanguageScreenerQuery, query_id)
    if row is None:
        raise api_error(404, "screener_query_not_found", "问句不存在")
    return _row_to_dict(row, include_result=include_result)


def create_query(
    db: Session,
    *,
    label: str,
    query_text: str,
    select_type: str = "A股",
) -> dict[str, Any]:
    st = (select_type or "A股").strip()
    if st not in SELECT_TYPES:
        raise api_error(400, "screener_invalid_select_type", f"不支持的 select_type: {st}")
    max_order = db.query(NaturalLanguageScreenerQuery.sort_order).order_by(
        NaturalLanguageScreenerQuery.sort_order.desc()
    ).first()
    order = (max_order[0] + 1) if max_order else 0
    row = NaturalLanguageScreenerQuery(
        label=(label or "新问句").strip() or "新问句",
        query_text=(query_text or "").strip(),
        select_type=st,
        sort_order=order,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _row_to_dict(row, include_result=True)


def update_query(
    db: Session,
    query_id: int,
    *,
    label: str | None = None,
    query_text: str | None = None,
    select_type: str | None = None,
) -> dict[str, Any]:
    row = db.get(NaturalLanguageScreenerQuery, query_id)
    if row is None:
        raise api_error(404, "screener_query_not_found", "问句不存在")
    if label is not None:
        row.label = label.strip() or "新问句"
    if query_text is not None:
        row.query_text = query_text.strip()
    if select_type is not None:
        st = select_type.strip()
        if st not in SELECT_TYPES:
            raise api_error(400, "screener_invalid_select_type", f"不支持的 select_type: {st}")
        row.select_type = st
    db.commit()
    db.refresh(row)
    return _row_to_dict(row, include_result=True)


def delete_query(db: Session, query_id: int) -> None:
    total = db.query(NaturalLanguageScreenerQuery).count()
    if total <= MIN_QUERIES:
        raise api_error(400, "screener_min_queries", "至少保留 1 条问句")
    row = db.get(NaturalLanguageScreenerQuery, query_id)
    if row is None:
        raise api_error(404, "screener_query_not_found", "问句不存在")
    db.delete(row)
    db.commit()


def run_query(db: Session, query_id: int) -> dict[str, Any]:
    row = db.get(NaturalLanguageScreenerQuery, query_id)
    if row is None:
        raise api_error(404, "screener_query_not_found", "问句不存在")
    text = (row.query_text or "").strip()
    if not text:
        raise api_error(400, "screener_empty_query", "问句内容不能为空")
    try:
        api_key = resolve_em_api_key()
    except RuntimeError as exc:
        raise api_error(503, "screener_api_key_missing", str(exc)) from exc

    md = get_market_data()
    row.last_error = ""
    db.commit()

    try:
        result = md.natural_language_screen(
            text,
            select_type=row.select_type or "A股",
            api_key=api_key,
            timeout=90.0,
        )
        blob = f"{row.label or ''} {text}"
        if any(k in blob for k in ("连板", "涨停")):
            validation = md.validate_continuous_limit_up_screen(result, min_continuous_days=2)
            row.last_validation_json = {
                "ok": validation.ok,
                "checked_codes": validation.checked_codes,
                "failures": validation.failures,
                "warnings": validation.warnings,
                "mode": "limit_up",
            }
        else:
            row.last_validation_json = {"ok": True, "mode": "skipped", "warnings": [], "failures": []}
        row.last_result_json = [dict(r) for r in result.rows]
        row.last_columns_json = list(result.column_names)
        row.last_row_count = result.row_count
        row.last_data_source = result.data_source
        row.last_run_at = datetime.now(timezone.utc).replace(tzinfo=None)
        row.last_run_query_hash = _query_hash(text, row.select_type or "A股")
        row.last_intraday_scores_json = {}
        row.last_error = ""
    except Exception as exc:
        logger.exception("nl screener run failed query_id=%s", query_id)
        row.last_error = str(exc)
        db.commit()
        raise api_error(502, "screener_run_failed", str(exc)) from exc

    db.commit()
    db.refresh(row)
    return _row_to_dict(row, include_result=True)


def _pick_row_code(row: dict[str, Any]) -> str:
    for key, val in row.items():
        if val is None or str(val).strip() == "":
            continue
        key_s = str(key)
        if any(p.search(key_s) for p in _CODE_KEY_PATTERNS):
            return str(val).strip()
    return ""


def _select_type_to_market(select_type: str) -> str:
    st = (select_type or "A股").strip()
    if st == "港股":
        return "HK"
    if st == "美股":
        return "US"
    return "CN"


def _score_trade_date(row: NaturalLanguageScreenerQuery) -> str:
    if row.last_run_at:
        dt = row.last_run_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_SH_TZ).date().isoformat()
    return today_iso()


def _score_key(symbol: str, market: str) -> str:
    mk = (market or "CN").upper()
    if mk == "CN":
        return normalize_cn_symbol(symbol)
    return (symbol or "").strip()


def _sort_rows_by_intraday_scores(
    rows: list[dict[str, Any]],
    *,
    market: str,
    scores: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    def sort_key(row: dict[str, Any]) -> tuple[int, float]:
        raw = _pick_row_code(row)
        key = _score_key(raw, market)
        entry = scores.get(key) or scores.get(raw) or {}
        score = entry.get("score")
        if score is None:
            return (1, 0.0)
        try:
            return (0, -float(score))
        except (TypeError, ValueError):
            return (1, 0.0)

    return sorted(rows, key=sort_key)


def compute_intraday_scores(
    db: Session,
    query_id: int,
    *,
    codes: list[str] | None = None,
) -> dict[str, Any]:
    """对当前问句最近一次选股结果计算 Wyckoff strength_score（同评估页算法）。"""
    row = db.get(NaturalLanguageScreenerQuery, query_id)
    if row is None:
        raise api_error(404, "screener_query_not_found", "问句不存在")
    result_rows = list(row.last_result_json or [])
    if not result_rows:
        raise api_error(400, "screener_no_result", "暂无选股结果，请先运行问句")

    market = _select_type_to_market(row.select_type or "A股")
    if market != "CN":
        raise api_error(400, "screener_intraday_cn_only", "分时 K 线得分当前仅支持 A 股问句")

    trade_date = _score_trade_date(row)
    want: set[str] | None = None
    if codes is not None:
        normalized = [_score_key(c, market) for c in codes if (c or "").strip()]
        want = set(dict.fromkeys(normalized))
        if not want:
            raise api_error(400, "screener_intraday_no_selection", "请至少选择一条结果")

    existing = dict((row.last_intraday_scores_json or {}).get("scores") or {})
    targets: list[tuple[str, dict[str, Any]]] = []
    for r in result_rows:
        raw = _pick_row_code(r)
        if not raw:
            continue
        key = _score_key(raw, market)
        if want is not None and key not in want and raw not in want:
            continue
        targets.append((key, r))

    if want is not None:
        found = {k for k, _ in targets}
        missing = [c for c in want if c not in found]
        if missing:
            raise api_error(400, "screener_intraday_code_missing", f"结果中未找到代码: {missing[:5]}")

    errors: list[dict[str, Any]] = []
    with baostock_session() as session:
        for key, _r in targets:
            try:
                score = compute_strength_score(key, market, trade_date, session=session)
                existing[key] = {"score": score, "error": None}
            except Exception as exc:
                logger.warning("screener intraday score failed %s %s: %s", key, trade_date, exc)
                existing[key] = {"score": None, "error": str(exc)}
                errors.append({"symbol": key, "test_date": trade_date, "message": str(exc)})

    payload_scores = {
        "test_date": trade_date,
        "sorted_by_score": True,
        "scores": existing,
        "computed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    row.last_intraday_scores_json = payload_scores
    row.last_result_json = _sort_rows_by_intraday_scores(
        result_rows,
        market=market,
        scores=existing,
    )
    db.commit()
    db.refresh(row)
    out = _row_to_dict(row, include_result=True)
    out["intraday_compute"] = {
        "computed_count": len(targets),
        "errors": errors,
        "computed_at": payload_scores["computed_at"],
    }
    return out


def merged_mock() -> dict[str, Any]:
    """合并结果占位(mock),后续接真实 merge。"""
    return {
        "mock": True,
        "total": 47,
        "contributing_queries": 3,
        "rows": [
            {
                "code": "600000",
                "name": "示例股份",
                "change_pct": 10.01,
                "sources": ["连板", "MA5 回踩"],
                "note": "mock",
            },
            {
                "code": "000001",
                "name": "示例银行",
                "change_pct": 9.99,
                "sources": ["连板"],
                "note": "mock",
            },
        ],
    }
