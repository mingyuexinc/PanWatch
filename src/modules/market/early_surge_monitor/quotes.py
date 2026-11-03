from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from marketdata.http import market_get
from marketdata.symbol import Market, Symbol

CN_TZ = ZoneInfo("Asia/Shanghai")
_TENCENT_Q_URL = "http://qt.gtimg.cn/q="
_TENCENT_Q_HOST = "qt.gtimg.cn"


def _parse_quote_ts(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if len(text) >= 14 and text[:14].isdigit():
        try:
            return datetime.strptime(text[:14], "%Y%m%d%H%M%S").replace(tzinfo=CN_TZ)
        except ValueError:
            pass
    if len(text) >= 8 and text[:8].isdigit():
        try:
            return datetime.strptime(text[:8], "%Y%m%d").replace(tzinfo=CN_TZ)
        except ValueError:
            pass
    return None


def _norm_code(code: str) -> str:
    digits = re.sub(r"\D", "", code or "")
    return digits[-6:].zfill(6) if digits else ""


def _to_tencent(code: str) -> str:
    return Symbol(Market.CN, _norm_code(code)).to_tencent()


def parse_tencent_quote_line(
    line: str,
) -> tuple[str, float, float | None, datetime | None, float | None] | None:
    if '=""' in line or not line.strip():
        return None
    try:
        _, value = line.split('="', 1)
        parts = value.rstrip('";').split("~")
        if len(parts) < 35:
            return None
        sym = parts[2]
        if "." in sym:
            sym = sym.split(".")[0]
        price = float(parts[3] or 0)
        prev_raw = (parts[4] or "").strip()
        prev_close = float(prev_raw) if prev_raw else None
        open_raw = (parts[5] or "").strip()
        open_price = float(open_raw) if open_raw else None
        qts = _parse_quote_ts(parts[30])
        return sym, price, prev_close, qts, open_price
    except (ValueError, IndexError):
        return None


def open_auction_effective_price(last_price: float, open_price: float | None) -> float:
    """竞价扫：现价(parts[3]) 在 9:25 附近常仍为昨收，需纳入开盘价(parts[5])。"""
    lp = float(last_price or 0)
    if open_price is not None and open_price > 0:
        return max(lp, float(open_price))
    return lp


def fetch_quote_ticks(
    symbols: list[str],
    *,
    batch_size: int = 60,
    timeout: float = 4.0,
    retries: int = 0,
) -> list[tuple[str, float, datetime | None]]:
    """批量拉腾讯 L1；返回 (symbol6, price, quote_ts)。"""
    return [
        (s, p, ts)
        for s, p, _pc, ts, _op in fetch_quote_snapshots(
            symbols, batch_size=batch_size, timeout=timeout, retries=retries
        )
    ]


def fetch_quote_snapshots(
    symbols: list[str],
    *,
    batch_size: int = 60,
    timeout: float = 4.0,
    retries: int = 0,
) -> list[tuple[str, float, float | None, datetime | None, float | None]]:
    """批量拉腾讯 L1；返回 (symbol6, price, prev_close, quote_ts, open_price)。"""
    codes = [_norm_code(s) for s in symbols if _norm_code(s)]
    if not codes:
        return []
    out: list[tuple[str, float, float | None, datetime | None, float | None]] = []
    for i in range(0, len(codes), batch_size):
        chunk = codes[i : i + batch_size]
        tsyms = ",".join(_to_tencent(c) for c in chunk)
        content = market_get(
            _TENCENT_Q_URL + tsyms,
            host_key=_TENCENT_Q_HOST,
            min_interval_s=0.15,
            timeout=timeout,
            retries=retries,
            parse="content",
            log_label="early_surge quote",
        )
        if not content:
            continue
        text = content.decode("gbk", errors="ignore") if isinstance(content, (bytes, bytearray)) else str(content)
        for line in text.strip().split(";"):
            parsed = parse_tencent_quote_line(line)
            if not parsed:
                continue
            sym, price, prev_close, qts, open_price = parsed
            out.append((_norm_code(sym), price, prev_close, qts, open_price))
    return out
