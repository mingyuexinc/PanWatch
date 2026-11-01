"""选股问句 + 当日监控 runtime（触发记录 + 测试推送）。"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.modules.intraday_quality.baostock_fetch import normalize_cn_symbol
from src.modules.market import nl_screener_service as screener_svc
from src.modules.market.early_surge_monitor.config import MonitorConfig
from src.modules.market.early_surge_monitor.engine import EarlySurgeMonitorEngine
from src.modules.market.early_surge_monitor.prev_close import prev_trading_close
from src.modules.market.early_surge_monitor.quotes import fetch_quote_snapshots, fetch_quote_ticks
from src.modules.market.early_surge_monitor.replay import scan_pool_from_mkline
from src.modules.market.early_surge_monitor.rules import TriggerDecision, in_monitor_window
from src.modules.market.screener_datetime import format_beijing_display, now_cn_naive
from src.modules.market.screener_monitor_notify import push_trigger_notifications
from src.platform.marketdata.models import MarketCode
from src.platform.scheduling.trading_calendar import is_trading_day
from src.modules.market.nl_screener_service import _pick_row_code
from src.platform.persistence.models import (
    NlScreenerDaySession,
    NlScreenerMonitorTrigger,
    NlScreenerStrategyConfig,
    NaturalLanguageScreenerQuery,
    NotifyChannel,
)
from src.web.errors import api_error

logger = logging.getLogger(__name__)
CN_TZ = ZoneInfo("Asia/Shanghai")

DEFAULT_START = 9 * 60 + 30
DEFAULT_END = 9 * 60 + 40
DEFAULT_GAIN = 3


def _cn_today() -> str:
    return datetime.now(CN_TZ).date().isoformat()


def _market_trade_date_on_or_before(d: date | None = None) -> str:
    """A 股最近一个交易日（含当日；休市日则回退）。"""
    cur = d or datetime.now(CN_TZ).date()
    for _ in range(400):
        if is_trading_day(MarketCode.CN, cur):
            return cur.isoformat()
        cur -= timedelta(days=1)
    return cur.isoformat()


def _get_session_row(db: Session, trade_date: str) -> NlScreenerDaySession | None:
    row = (
        db.query(NlScreenerDaySession)
        .filter(NlScreenerDaySession.trade_date == trade_date)
        .first()
    )
    if row:
        return row
    return (
        db.query(NlScreenerDaySession)
        .order_by(NlScreenerDaySession.trade_date.desc())
        .first()
    )


def _get_ready_session(db: Session, trade_date: str) -> NlScreenerDaySession | None:
    row = _get_session_row(db, trade_date)
    if row and row.pool_status == "ready":
        return row
    return (
        db.query(NlScreenerDaySession)
        .filter(NlScreenerDaySession.pool_status == "ready")
        .order_by(NlScreenerDaySession.trade_date.desc())
        .first()
    )


def _minute_label(total_min: int) -> str:
    h, m = divmod(int(total_min), 60)
    return f"{h:02d}:{m:02d}"


def _parse_minute(hhmm: str) -> int:
    parts = hhmm.strip().split(":")
    if len(parts) != 2:
        raise ValueError("invalid time")
    h, m = int(parts[0]), int(parts[1])
    return h * 60 + m


def _get_or_create_config(db: Session) -> NlScreenerStrategyConfig:
    row = db.query(NlScreenerStrategyConfig).order_by(NlScreenerStrategyConfig.id.asc()).first()
    if row:
        return row
    q = db.query(NaturalLanguageScreenerQuery).order_by(NaturalLanguageScreenerQuery.sort_order).first()
    row = NlScreenerStrategyConfig(
        query_id=q.id if q else None,
        monitor_start_minute=DEFAULT_START,
        monitor_end_minute=DEFAULT_END,
        gain_pct=DEFAULT_GAIN,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _config_dict(row: NlScreenerStrategyConfig) -> dict[str, Any]:
    return {
        "query_id": row.query_id,
        "monitor_start_minute": row.monitor_start_minute,
        "monitor_end_minute": row.monitor_end_minute,
        "monitor_start": _minute_label(row.monitor_start_minute),
        "monitor_end": _minute_label(row.monitor_end_minute),
        "gain_pct": int(row.gain_pct or DEFAULT_GAIN),
        "notify_channel_id": row.notify_channel_id,
        "last_test_trigger_at": format_beijing_display(row.last_test_trigger_at),
        "auto_monitor_enabled": bool(row.auto_monitor_enabled),
    }


def get_strategy(db: Session) -> dict[str, Any]:
    cfg = _get_or_create_config(db)
    market_date = _market_trade_date_on_or_before()
    session = _get_session_row(db, market_date)
    trigger_date = session.trade_date if session else market_date
    triggers = (
        db.query(NlScreenerMonitorTrigger)
        .filter(NlScreenerMonitorTrigger.trade_date == trigger_date)
        .order_by(NlScreenerMonitorTrigger.trigger_at.asc())
        .all()
    )
    return {
        "config": _config_dict(cfg),
        "market_trade_date": market_date,
        "session": _session_dict(session) if session else None,
        "triggers": [_trigger_dict(t) for t in triggers],
    }


def update_strategy(
    db: Session,
    *,
    query_id: int | None = None,
    monitor_start: str | None = None,
    monitor_end: str | None = None,
    gain_pct: int | None = None,
    notify_channel_id: int | None = None,
    auto_monitor_enabled: bool | None = None,
) -> dict[str, Any]:
    cfg = _get_or_create_config(db)
    if query_id is not None:
        if db.get(NaturalLanguageScreenerQuery, query_id) is None:
            raise api_error(404, "screener_query_not_found", "问句不存在")
        cfg.query_id = query_id
    if monitor_start is not None:
        cfg.monitor_start_minute = _parse_minute(monitor_start)
    if monitor_end is not None:
        cfg.monitor_end_minute = _parse_minute(monitor_end)
    if gain_pct is not None:
        if not (0 <= int(gain_pct) <= 10):
            raise api_error(400, "screener_strategy_invalid", "涨幅阈值应为 0–10 的整数")
        cfg.gain_pct = int(gain_pct)
    if cfg.monitor_start_minute >= cfg.monitor_end_minute:
        raise api_error(400, "screener_strategy_invalid", "监控开始须早于结束")
    if notify_channel_id is not None:
        if notify_channel_id == 0:
            cfg.notify_channel_id = None
        else:
            ch = db.get(NotifyChannel, notify_channel_id)
            if ch is None:
                raise api_error(404, "notify_channel_not_found", "通知渠道不存在")
            if not ch.enabled:
                raise api_error(400, "notify_channel_disabled", "通知渠道未启用")
            cfg.notify_channel_id = int(notify_channel_id)
    if auto_monitor_enabled is not None:
        cfg.auto_monitor_enabled = bool(auto_monitor_enabled)
    db.commit()
    db.refresh(cfg)
    return get_strategy(db)


def _session_dict(row: NlScreenerDaySession) -> dict[str, Any]:
    return {
        "trade_date": row.trade_date,
        "query_id": row.query_id,
        "pool_status": row.pool_status or "",
        "pool_count": len(row.pool_codes_json or []),
        "run_attempts": int(row.run_attempts or 0),
        "last_error": row.last_error or "",
        "pool_built_at": format_beijing_display(row.pool_built_at),
    }


def _trigger_dict(row: NlScreenerMonitorTrigger) -> dict[str, Any]:
    prev = float(row.prev_close or 0)
    price = float(row.trigger_price or 0)
    pct = ((price - prev) / prev * 100.0) if prev else None
    return {
        "symbol": row.symbol,
        "name": row.name or "",
        "trigger_at": format_beijing_display(row.trigger_at),
        "trigger_price": price,
        "prev_close": prev,
        "change_pct": round(pct, 2) if pct is not None else None,
        "reason": row.reason or "",
    }


def _extract_pool_codes(rows: list[dict]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        raw = _pick_row_code(row)
        code = normalize_cn_symbol(raw)
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def _build_prev_close_map(codes: list[str], trade_date: str) -> dict[str, float]:
    snaps = fetch_quote_snapshots(codes)
    by_sym = {s: (p, pc) for s, p, pc, _ts in snaps}
    out: dict[str, float] = {}
    for code in codes:
        _price, pc = by_sym.get(code, (0.0, None))
        if pc and pc > 0:
            out[code] = float(pc)
            continue
        fallback = prev_trading_close(code, trade_date)
        if fallback:
            out[code] = float(fallback)
    return out


def _explain_pool_not_ready(db: Session, cfg: NlScreenerStrategyConfig, market_date: str) -> str:
    if not cfg.query_id:
        return "请先点击「保存策略」，将当前问句绑定为监控股票池来源。"
    session = _get_session_row(db, market_date)
    if session:
        if session.pool_status == "failed":
            err = (session.last_error or "").strip()
            return f"上次建池失败{'：' + err if err else ''}。请重试「当日建池」。"
        if session.pool_status == "running":
            return "建池进行中，请稍候再试。"
    q = screener_svc.get_query(db, int(cfg.query_id), include_result=True)
    if not (q.get("rows") or []):
        return "问句暂无选股结果。请先「运行本问句」，再测试扫描或点击「当日建池」。"
    return "股票池尚未就绪。请先点击「当日建池」，或再次测试扫描（将自动用当前问句结果建池）。"


def _materialize_pool_session(
    db: Session,
    cfg: NlScreenerStrategyConfig,
    *,
    run_screener: bool,
) -> NlScreenerDaySession:
    if not cfg.query_id:
        raise api_error(400, "screener_strategy_no_query", "请先在策略中绑定问句")
    trade_date = _market_trade_date_on_or_before()
    session = (
        db.query(NlScreenerDaySession)
        .filter(NlScreenerDaySession.trade_date == trade_date)
        .first()
    )
    if not session:
        session = NlScreenerDaySession(trade_date=trade_date, query_id=cfg.query_id)
        db.add(session)
        db.flush()

    session.query_id = cfg.query_id
    session.run_attempts = int(session.run_attempts or 0) + 1
    session.pool_status = "running"
    session.last_error = ""
    db.commit()

    try:
        if run_screener:
            screener_svc.run_query(db, int(cfg.query_id))
        q = screener_svc.get_query(db, int(cfg.query_id), include_result=True)
        codes = _extract_pool_codes(q.get("rows") or [])
        if not codes:
            raise api_error(400, "screener_pool_empty", "问句选股结果为空，无法建池")
        prev_map = _build_prev_close_map(codes, trade_date)
        session.pool_codes_json = codes
        session.prev_close_json = prev_map
        session.pool_status = "ready"
        session.pool_built_at = now_cn_naive()
        session.last_error = ""
    except HTTPException as exc:
        detail = exc.detail
        msg = detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail)
        session.pool_status = "failed"
        session.last_error = msg
        db.commit()
        raise
    except Exception as exc:
        logger.exception("build screener pool failed")
        session.pool_status = "failed"
        session.last_error = str(exc)
        db.commit()
        raise api_error(502, "screener_pool_failed", str(exc)) from exc

    db.commit()
    db.refresh(session)
    return session


def build_today_pool(db: Session, *, run_screener: bool = True) -> dict[str, Any]:
    cfg = _get_or_create_config(db)
    _materialize_pool_session(db, cfg, run_screener=run_screener)
    return get_strategy(db)


def _http_error_code(exc: HTTPException) -> str | None:
    detail = exc.detail
    if isinstance(detail, dict):
        return detail.get("code")
    return None


def _ensure_pool_for_test(db: Session, cfg: NlScreenerStrategyConfig) -> None:
    """建池（测试）：每次测试都刷新池与昨收；无问句结果时再跑一次问句。"""
    market_date = _market_trade_date_on_or_before()
    try:
        _materialize_pool_session(db, cfg, run_screener=False)
    except HTTPException as exc:
        if _http_error_code(exc) == "screener_pool_empty":
            _materialize_pool_session(db, cfg, run_screener=True)
            return
        raise
    except Exception as exc:
        logger.exception("test trigger build pool failed")
        detail = _explain_pool_not_ready(db, cfg, market_date)
        raise api_error(400, "screener_pool_not_ready", detail) from exc


def test_trigger(db: Session) -> dict[str, Any]:
    """测试：建池 + 按最近交易日 1m 回放扫描触发。"""
    cfg = _get_or_create_config(db)
    market_date = _market_trade_date_on_or_before()
    _ensure_pool_for_test(db, cfg)
    session = _get_ready_session(db, market_date)
    if not session or session.pool_status != "ready":
        detail = _explain_pool_not_ready(db, cfg, market_date)
        raise api_error(400, "screener_pool_not_ready", detail)
    return _run_mkline_scan(db, cfg, session, pool_auto_built=True)


def scan_once(db: Session) -> dict[str, Any]:
    """兼容旧 API；行为与 ``test_trigger`` 一致。"""
    return test_trigger(db)


def _monitor_config_for(cfg: NlScreenerStrategyConfig, *, mkline_confirm: bool) -> MonitorConfig:
    return MonitorConfig(
        session_start_minute=int(cfg.monitor_start_minute),
        session_end_minute=int(cfg.monitor_end_minute),
        trigger_ratio=1.0 + int(cfg.gain_pct) / 100.0,
        mkline_confirm=mkline_confirm,
    )


def _existing_trigger_symbols(db: Session, trade_date: str) -> set[str]:
    return {
        normalize_cn_symbol(r.symbol)
        for r in db.query(NlScreenerMonitorTrigger)
        .filter(NlScreenerMonitorTrigger.trade_date == trade_date)
        .all()
    }


def _auction_trigger_clock(trade_date: str) -> datetime:
    day = datetime.fromisoformat(trade_date).date()
    return datetime(day.year, day.month, day.day, 9, 25, 0, tzinfo=CN_TZ)


def _scan_pool_open_auction(
    codes: list[str],
    trade_date: str,
    *,
    cfg: NlScreenerStrategyConfig,
    prev_close_by_symbol: dict[str, float],
    triggered_symbols: set[str],
) -> list[tuple[str, TriggerDecision]]:
    mon_cfg = _monitor_config_for(cfg, mkline_confirm=False)
    prev_map = {normalize_cn_symbol(k): float(v) for k, v in prev_close_by_symbol.items()}
    trig_at = _auction_trigger_clock(trade_date)
    fired: list[tuple[str, TriggerDecision]] = []
    rows = fetch_quote_ticks(codes, batch_size=mon_cfg.batch_size, timeout=mon_cfg.quote_timeout_s)
    for sym, price, qts in rows:
        code = normalize_cn_symbol(sym)
        if not code or code in triggered_symbols:
            continue
        pc = prev_map.get(code)
        if pc is None or pc <= 0:
            continue
        if price < pc * mon_cfg.trigger_ratio:
            continue
        fired.append(
            (
                code,
                TriggerDecision(
                    True,
                    False,
                    "open_auction",
                    prev_close=pc,
                    trigger_price=price,
                    quote_ts=(qts or trig_at).astimezone(CN_TZ),
                    trigger_at=trig_at,
                ),
            )
        )
        triggered_symbols.add(code)
    return fired


def _persist_triggers_and_push(
    db: Session,
    cfg: NlScreenerStrategyConfig,
    *,
    trade_date: str,
    query_id: int | None,
    prev_map: dict[str, float],
    fired: list[tuple[str, TriggerDecision]],
    existing: set[str],
    mon_cfg: MonitorConfig,
) -> tuple[int, list[NlScreenerMonitorTrigger], dict[str, Any]]:
    name_by_code = _names_from_query(db, int(query_id)) if query_id else {}
    new_rows: list[NlScreenerMonitorTrigger] = []
    new_count = 0
    for sym, decision in fired:
        if not decision.fired or not decision.trigger_at:
            continue
        code = normalize_cn_symbol(sym)
        if code in existing:
            continue
        row = NlScreenerMonitorTrigger(
            trade_date=trade_date,
            symbol=code,
            name=name_by_code.get(code, ""),
            trigger_at=decision.trigger_at.replace(tzinfo=None),
            trigger_price=float(decision.trigger_price or 0),
            prev_close=float(decision.prev_close or prev_map.get(code) or 0),
            reason=decision.reason or "ok",
        )
        db.add(row)
        new_rows.append(row)
        existing.add(code)
        new_count += 1
    db.commit()
    for row in new_rows:
        db.refresh(row)
    push_result = push_trigger_notifications(
        db,
        cfg,
        new_rows,
        trade_date=trade_date,
        mon_cfg=mon_cfg,
    )
    return new_count, new_rows, push_result


def _auto_skip_reason(cfg: NlScreenerStrategyConfig) -> str | None:
    if not cfg.auto_monitor_enabled:
        return "auto_disabled"
    if not cfg.query_id:
        return "no_query"
    if not cfg.notify_channel_id:
        return "no_notify_channel"
    today = datetime.now(CN_TZ).date()
    if not is_trading_day(MarketCode.CN, today):
        return "not_trading_day"
    return None


def run_morning_open_job(db: Session) -> dict[str, Any]:
    """09:25:05：问句运行 → 建池 → 竞价开盘价达标扫描 → 推送。"""
    cfg = _get_or_create_config(db)
    skip = _auto_skip_reason(cfg)
    if skip:
        return {"ok": False, "skipped": skip}
    trade_date = _market_trade_date_on_or_before()
    try:
        session = _materialize_pool_session(db, cfg, run_screener=True)
    except HTTPException as exc:
        detail = exc.detail
        msg = detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail)
        return {"ok": False, "stage": "build_pool", "error": msg}
    except Exception as exc:
        logger.exception("morning open build pool failed")
        return {"ok": False, "stage": "build_pool", "error": str(exc)}

    auction = _run_open_auction_scan(db, cfg, session)
    return {
        "ok": True,
        "trade_date": trade_date,
        "pool_count": len(session.pool_codes_json or []),
        "auction": auction,
    }


def _run_open_auction_scan(
    db: Session,
    cfg: NlScreenerStrategyConfig,
    session: NlScreenerDaySession,
) -> dict[str, Any]:
    scan_date = session.trade_date or _market_trade_date_on_or_before()
    codes = list(session.pool_codes_json or [])
    prev_map = {k: float(v) for k, v in (session.prev_close_json or {}).items()}
    if not codes:
        return {"mode": "open_auction", "new_triggers": 0, "message": "股票池为空"}
    existing = _existing_trigger_symbols(db, scan_date)
    fired = _scan_pool_open_auction(
        codes,
        scan_date,
        cfg=cfg,
        prev_close_by_symbol=prev_map,
        triggered_symbols=set(existing),
    )
    mon_cfg = _monitor_config_for(cfg, mkline_confirm=False)
    new_count, _rows, push_result = _persist_triggers_and_push(
        db,
        cfg,
        trade_date=scan_date,
        query_id=cfg.query_id,
        prev_map=prev_map,
        fired=fired,
        existing=existing,
        mon_cfg=mon_cfg,
    )
    msg = f"竞价扫描：池内 {len(codes)} 只，新增触发 {new_count} 条。"
    if push_result.get("sent"):
        msg += f" 已推送 {push_result['sent']} 条。"
    elif new_count > 0 and push_result.get("errors"):
        msg += f" 推送失败：{push_result['errors'][0]}"
    return {
        "mode": "open_auction",
        "trade_date": scan_date,
        "new_triggers": new_count,
        "pool_count": len(codes),
        "message": msg,
        "push": push_result,
    }


def run_live_monitor_poll(db: Session) -> dict[str, Any] | None:
    """监控窗口内 L1 轮询（仅未触发票）。"""
    cfg = _get_or_create_config(db)
    skip = _auto_skip_reason(cfg)
    if skip:
        return {"skipped": skip}
    now = datetime.now(CN_TZ)
    trade_date = now.date().isoformat()
    if trade_date != _market_trade_date_on_or_before(now.date()):
        return {"skipped": "not_trading_day"}
    mon_cfg = _monitor_config_for(cfg, mkline_confirm=True)
    if not in_monitor_window(now, mon_cfg):
        return {"skipped": "outside_monitor_window"}

    session = _get_ready_session(db, trade_date)
    if not session or session.pool_status != "ready":
        return {"skipped": "pool_not_ready"}

    codes = list(session.pool_codes_json or [])
    prev_map = {k: float(v) for k, v in (session.prev_close_json or {}).items()}
    if not codes:
        return {"skipped": "empty_pool"}

    existing = _existing_trigger_symbols(db, trade_date)
    engine = EarlySurgeMonitorEngine(mon_cfg)
    engine.load_session(prev_close_by_symbol=prev_map, triggered_symbols=existing)
    fired = engine.poll_pool(codes, trade_date, now=now)
    new_count, _rows, push_result = _persist_triggers_and_push(
        db,
        cfg,
        trade_date=trade_date,
        query_id=cfg.query_id,
        prev_map=prev_map,
        fired=fired,
        existing=existing,
        mon_cfg=mon_cfg,
    )
    return {
        "mode": "live",
        "trade_date": trade_date,
        "new_triggers": new_count,
        "pool_count": len(codes),
        "push": push_result,
    }


def _record_test_trigger_time(db: Session, cfg: NlScreenerStrategyConfig) -> None:
    cfg.last_test_trigger_at = now_cn_naive()
    db.commit()
    db.refresh(cfg)


def _run_mkline_scan(
    db: Session,
    cfg: NlScreenerStrategyConfig,
    session: NlScreenerDaySession,
    *,
    pool_auto_built: bool,
) -> dict[str, Any]:
    market_date = _market_trade_date_on_or_before()
    scan_date = session.trade_date or market_date
    codes = list(session.pool_codes_json or [])
    prev_map = {k: float(v) for k, v in (session.prev_close_json or {}).items()}
    if not codes:
        _record_test_trigger_time(db, cfg)
        payload = get_strategy(db)
        finished = format_beijing_display(cfg.last_test_trigger_at)
        payload["scan"] = {
            "trade_date": scan_date,
            "mode": "mkline_replay",
            "new_triggers": 0,
            "pool_auto_built": pool_auto_built,
            "pool_count": 0,
            "hits": 0,
            "skipped_no_prev_close": 0,
            "skipped_no_bars": 0,
            "replay_poll_points": 0,
            "finished_at": finished,
            "message": "股票池为空，请确认问句已跑出结果且代码列可识别。",
        }
        return payload

    existing = _existing_trigger_symbols(db, scan_date)
    mon_cfg = _monitor_config_for(cfg, mkline_confirm=False)
    now = datetime.now(CN_TZ)
    same_calendar_day = scan_date == now.date().isoformat()
    until = now if same_calendar_day else None
    fired, stats = scan_pool_from_mkline(
        codes,
        scan_date,
        cfg=mon_cfg,
        prev_close_by_symbol=prev_map,
        triggered_symbols=existing,
        until=until,
    )
    new_count, new_rows, push_result = _persist_triggers_and_push(
        db,
        cfg,
        trade_date=scan_date,
        query_id=cfg.query_id,
        prev_map=prev_map,
        fired=fired,
        existing=existing,
        mon_cfg=mon_cfg,
    )
    scan_message = _scan_result_message(
        stats,
        scan_date=scan_date,
        new_count=new_count,
        same_calendar_day=same_calendar_day,
        now=now,
        cfg=mon_cfg,
    )
    _record_test_trigger_time(db, cfg)
    payload = get_strategy(db)
    finished = format_beijing_display(cfg.last_test_trigger_at)
    payload["scan"] = {
        "trade_date": scan_date,
        "mode": "mkline_replay",
        "new_triggers": new_count,
        "pool_auto_built": pool_auto_built,
        "pool_count": stats.symbols_in_pool,
        "hits": stats.hits,
        "skipped_no_prev_close": stats.skipped_no_prev_close,
        "skipped_no_bars": stats.skipped_no_bars,
        "replay_poll_points": stats.replay_poll_points,
        "finished_at": finished,
        "message": scan_message,
        "push": push_result,
    }
    if push_result.get("sent"):
        scan_message = f"{scan_message} 已推送 {push_result['sent']} 条至「{push_result.get('channel', {}).get('name', '渠道')}」。"
    elif new_count > 0 and push_result.get("errors"):
        scan_message = f"{scan_message} 推送失败：{push_result['errors'][0]}"
    payload["scan"]["message"] = scan_message
    return payload


def _scan_result_message(
    stats,
    *,
    scan_date: str,
    new_count: int,
    same_calendar_day: bool,
    now: datetime,
    cfg: MonitorConfig,
) -> str:
    if stats.symbols_in_pool == 0:
        return "股票池为空，请确认问句已跑出结果。"
    if stats.replay_poll_points == 0:
        if same_calendar_day:
            sh, sm = divmod(int(cfg.session_start_minute), 60)
            return (
                f"当前时间 {now.astimezone(CN_TZ).strftime('%H:%M')} 早于监控时段 "
                f"{sh:02d}:{sm:02d} 起，无法回放；请在收盘后或下一交易时段再测。"
            )
        return "监控时段内无有效回放采样点，请检查起止时间配置。"
    if stats.skipped_no_bars >= stats.symbols_in_pool:
        return f"{scan_date} 分钟线均未取到（共 {stats.symbols_in_pool} 只），请确认是否为交易日且网络正常。"
    if stats.skipped_no_prev_close > 0 and stats.hits == 0 and new_count == 0:
        return (
            f"已检查 {stats.symbols_in_pool} 只，{stats.skipped_no_prev_close} 只缺少昨收；"
            f"其余未达 +{int(round((cfg.trigger_ratio - 1) * 100))}% 阈值。"
        )
    if new_count > 0:
        return f"新增 {new_count} 条触发（回放命中 {stats.hits} 只）。"
    if stats.hits > 0:
        return f"回放命中 {stats.hits} 只，均已存在于触发记录（本次新增 0）。"
    return (
        f"已回放 {scan_date} 监控时段（{stats.replay_poll_points} 个采样点），"
        f"池内 {stats.symbols_in_pool} 只均未触发；可尝试放宽涨幅或延长监控结束时间。"
    )


def _names_from_query(db: Session, query_id: int) -> dict[str, str]:
    q = screener_svc.get_query(db, query_id, include_result=True)
    out: dict[str, str] = {}
    for row in q.get("rows") or []:
        code = normalize_cn_symbol(_pick_row_code(row))
        if not code:
            continue
        for key, val in row.items():
            if val and ("名称" in str(key) or str(key).lower() == "name"):
                out[code] = str(val).strip()
                break
    return out


def list_triggers(db: Session, trade_date: str | None = None) -> list[dict[str, Any]]:
    td = trade_date or _market_trade_date_on_or_before()
    rows = (
        db.query(NlScreenerMonitorTrigger)
        .filter(NlScreenerMonitorTrigger.trade_date == td)
        .order_by(NlScreenerMonitorTrigger.trigger_at.asc())
        .all()
    )
    return [_trigger_dict(r) for r in rows]
