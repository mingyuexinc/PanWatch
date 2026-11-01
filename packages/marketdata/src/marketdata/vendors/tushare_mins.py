"""Tushare Pro stk_mins：A 股历史 1 分钟（需 token +「A股历史分钟」权限）。"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)
CN_TZ = ZoneInfo("Asia/Shanghai")
_TUSHARE_HTTP = "http://api.tushare.pro"


def resolve_tushare_token() -> str:
    return (os.environ.get("TUSHARE_TOKEN") or os.environ.get("TUSHARE_PRO_TOKEN") or "").strip()


def symbol_to_ts_code(code: str) -> str:
    digits = "".join(c for c in (code or "") if c.isdigit())[-6:].zfill(6)
    suffix = "SH" if digits.startswith("6") else "SZ"
    return f"{digits}.{suffix}"


def _parse_trade_time(raw: str) -> datetime | None:
    text = (raw or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=CN_TZ)
        except ValueError:
            continue
    return None


def _filter_session(bars: list[dict], trade_date: str) -> list[dict]:
    out: list[dict] = []
    for b in bars:
        dt: datetime = b["datetime"]
        if dt.date().isoformat() != trade_date:
            continue
        mod = dt.hour * 60 + dt.minute
        if (9 * 60 + 30) <= mod <= (11 * 60 + 30) or (13 * 60) <= mod <= (15 * 60):
            out.append(b)
    return sorted(out, key=lambda x: x["datetime"])


def tushare_stk_mins_raw(
    ts_code: str,
    *,
    freq: str = "1min",
    start_date: str,
    end_date: str,
    token: str | None = None,
    timeout: float = 25.0,
) -> tuple[list[dict], str | None]:
    """返回 (rows, error)。rows 为字段 dict 列表。"""
    tok = (token or resolve_tushare_token()).strip()
    if not tok:
        return [], "missing TUSHARE_TOKEN"
    body = {
        "api_name": "stk_mins",
        "token": tok,
        "params": {
            "ts_code": ts_code,
            "freq": freq,
            "start_date": start_date,
            "end_date": end_date,
        },
        "fields": "ts_code,trade_time,open,close,high,low,vol,amount",
    }
    try:
        with httpx.Client(trust_env=True, timeout=timeout) as client:
            resp = client.post(_TUSHARE_HTTP, json=body)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"

    code = payload.get("code")
    if code not in (0, None):
        return [], f"tushare code={code} msg={payload.get('msg')}"

    data = payload.get("data") or {}
    fields = data.get("fields") or []
    items = data.get("items") or []
    if not fields or not items:
        return [], None
    rows: list[dict] = []
    for item in items:
        rows.append(dict(zip(fields, item)))
    return rows, None


def fetch_tushare_1m(symbol: str, trade_date: str) -> list[dict]:
    td = (trade_date or "").strip()
    ymd = td.replace("-", "")
    ts_code = symbol_to_ts_code(symbol)
    start = f"{td} 09:30:00"
    end = f"{td} 15:00:00"
    rows, err = tushare_stk_mins_raw(
        ts_code,
        start_date=start,
        end_date=end,
    )
    if err:
        logger.debug("tushare stk_mins %s %s: %s", ts_code, td, err)
        return []
    bars: list[dict] = []
    for row in rows:
        dt = _parse_trade_time(str(row.get("trade_time") or ""))
        if dt is None:
            continue
        try:
            bars.append(
                {
                    "datetime": dt,
                    "open": float(row["open"]),
                    "close": float(row["close"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "volume": float(row.get("vol") or 0.0),
                }
            )
        except (TypeError, ValueError, KeyError):
            continue
    return _filter_session(bars, td)
