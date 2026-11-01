"""A 股真 1m 统一拉取（Tier A）：腾讯 mkline → 东财 → Tushare → 腾讯当日 → 新浪。

不含 5m 展开；供盘中监控、策略采集、评估真 1m 路径共用。
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from marketdata.http import market_get
from marketdata.symbol import Market, Symbol
from marketdata.vendors.eastmoney_hosts import (
    EM_KLINE_PATH,
    EM_TRENDS2_PATH,
    EM_UT_TRENDS,
    em_get_json,
    em_kline_params,
)
from marketdata.vendors.kline import _em_secid
from marketdata.vendors.tushare_mins import fetch_tushare_1m, resolve_tushare_token

logger = logging.getLogger(__name__)
CN_TZ = ZoneInfo("Asia/Shanghai")

REAL_1M_VENDORS = frozenset({"eastmoney", "tencent", "tencent_mkline", "sina", "tushare"})

_TENCENT_MINUTE_QUERY = "https://ifzq.gtimg.cn/appstock/app/minute/query"
_TENCENT_FQKLINE = "https://ifzq.gtimg.cn/appstock/app/fqkline/get"
_TENCENT_MKLINE = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
# 实测：第三参数为截止时间 YYYYMMDDHHMM 可向前翻页；服务端约保留最近 1 个月（~17 个交易日）的 1m。
TENCENT_MKLINE_MAX_BARS = 640


def is_real_1m_vendor(vendor: str | None) -> bool:
    if not vendor:
        return False
    if "_derived_1m" in vendor or "_syn_1m" in vendor:
        return False
    return vendor in REAL_1M_VENDORS


def _norm_code(code: str) -> str:
    digits = re.sub(r"\D", "", code or "")
    return digits[-6:].zfill(6) if digits else ""


def _to_sina_symbol(code: str) -> str:
    c = _norm_code(code)
    if c.startswith(("6", "9")):
        return f"sh{c}"
    return f"sz{c}"


def _market_code(symbol: str) -> int:
    return 1 if _norm_code(symbol).startswith("6") else 0


def _parse_row_datetime(raw: str) -> datetime | None:
    text = (raw or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=CN_TZ)
        except ValueError:
            continue
    return None


def filter_cn_session_bars(bars: list[dict], trade_date: str) -> list[dict]:
    """保留 trade_date 当日 A 股连续竞价时段 bar。"""
    out: list[dict] = []
    for b in bars:
        dt: datetime = b["datetime"]
        if dt.date().isoformat() != trade_date:
            continue
        mod = dt.hour * 60 + dt.minute
        if (9 * 60 + 30) <= mod <= (11 * 60 + 30):
            out.append(b)
        elif (13 * 60) <= mod <= (15 * 60):
            out.append(b)
    return sorted(out, key=lambda x: x["datetime"])


def _bars_from_em_klines(raw: list, trade_date: str) -> list[dict]:
    bars: list[dict] = []
    for row in raw or []:
        parts = str(row).split(",")
        if len(parts) < 6:
            continue
        dt = _parse_row_datetime(parts[0])
        if dt is None:
            continue
        try:
            bars.append(
                {
                    "datetime": dt,
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "volume": float(parts[5]),
                }
            )
        except (TypeError, ValueError):
            continue
    return filter_cn_session_bars(bars, trade_date)


def fetch_eastmoney_trends2_1m(symbol: str, trade_date: str, *, ndays: int = 5) -> list[dict]:
    """东财 trends2 分时（近 ndays 自然日，含 1 分钟点）。"""
    sym = _norm_code(symbol)
    td = trade_date.strip()
    params = {
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "ut": EM_UT_TRENDS,
        "ndays": str(ndays),
        "iscr": "0",
        "secid": f"{_market_code(sym)}.{sym}",
    }
    payload = em_get_json(
        EM_TRENDS2_PATH,
        params,
        symbol=sym,
        log_label="东财真1m trends2",
        timeout=20,
    )
    if payload:
        trends = (payload.get("data") or {}).get("trends") or []
        bars: list[dict] = []
        for item in trends:
            parts = str(item).split(",")
            if len(parts) < 6:
                continue
            dt = _parse_row_datetime(parts[0])
            if dt is None or dt.date().isoformat() != td:
                continue
            try:
                bars.append(
                    {
                        "datetime": dt,
                        "open": float(parts[1]),
                        "close": float(parts[2]),
                        "high": float(parts[3]),
                        "low": float(parts[4]),
                        "volume": float(parts[5]),
                    }
                )
            except (TypeError, ValueError):
                continue
        if bars:
            return sorted(bars, key=lambda x: x["datetime"])
    return []


def fetch_eastmoney_kline_1m(symbol: str, trade_date: str) -> list[dict]:
    """东财 kline/get klt=1，按日 beg/end。"""
    sym = _norm_code(symbol)
    td = trade_date.strip()
    ymd = td.replace("-", "")
    secid = _em_secid(Symbol(Market.CN, sym))
    primary = em_kline_params(secid, beg=ymd, end=ymd, klt="1", lmt="500", with_ut=False)
    legacy = em_kline_params(secid, beg=ymd, end=ymd, klt="1", lmt="500", with_ut=True)
    payload = em_get_json(
        EM_KLINE_PATH,
        primary,
        symbol=sym,
        log_label="东财真1m kline",
        timeout=20,
        param_variants=(primary, legacy),
        curl_first=True,
    )
    if not payload:
        return []
    raw = (payload.get("data") or {}).get("klines") or []
    bars = _bars_from_em_klines(raw, td)
    return bars if bars else []


def fetch_tencent_minute_query(symbol: str, trade_date: str) -> list[dict]:
    """腾讯 minute/query：返回**接口标注当日**的 1m（非 fqkline）。"""
    sym = _norm_code(symbol)
    td = trade_date.strip()
    ymd = td.replace("-", "")
    tsym = _to_sina_symbol(sym)
    payload = market_get(
        _TENCENT_MINUTE_QUERY,
        host_key="ifzq.gtimg.cn",
        min_interval_s=0.15,
        params={"code": tsym},
        headers={"User-Agent": "Mozilla/5.0", "Referer": f"https://gu.qq.com/{tsym}/gp"},
        timeout=15,
        retries=2,
        parse="json",
        log_label="腾讯minute/query",
        symbol=sym,
        trust_env=False,
    )
    if not isinstance(payload, dict):
        return []
    block = ((payload.get("data") or {}).get(tsym) or {}).get("data") or {}
    if str(block.get("date") or "") != ymd:
        return []
    bars: list[dict] = []
    for line in block.get("data") or []:
        text = str(line).strip()
        m = re.match(r"^(\d{4})\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", text)
        if not m:
            continue
        hhmm, price, vol, _amount = m.groups()
        try:
            dt = datetime.strptime(f"{ymd}{hhmm}", "%Y%m%d%H%M").replace(tzinfo=CN_TZ)
            p = float(price)
            v = float(vol)
            bars.append(
                {
                    "datetime": dt,
                    "open": p,
                    "close": p,
                    "high": p,
                    "low": p,
                    "volume": v,
                }
            )
        except (TypeError, ValueError):
            continue
    return sorted(bars, key=lambda x: x["datetime"])


def fetch_tencent_mkline_1m(symbol: str, trade_date: str) -> list[dict]:
    """腾讯 kline/mkline m1：真 OHLC 1m，可按截止时间回溯约 1 个月，免 token / 免代理。"""
    sym = _norm_code(symbol)
    td = trade_date.strip()
    ymd = td.replace("-", "")
    tsym = _to_sina_symbol(sym)
    payload = market_get(
        _TENCENT_MKLINE,
        host_key="ifzq.gtimg.cn",
        min_interval_s=0.15,
        params={"param": f"{tsym},m1,{ymd}1500,{TENCENT_MKLINE_MAX_BARS}"},
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
        timeout=15,
        retries=2,
        parse="json",
        log_label="腾讯mkline",
        symbol=sym,
        trust_env=False,
    )
    if not isinstance(payload, dict):
        return []
    rows = (((payload.get("data") or {}).get(tsym)) or {}).get("m1") or []
    bars: list[dict] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        stamp = str(row[0])
        if not stamp.startswith(ymd) or len(stamp) < 12:
            continue
        try:
            dt = datetime.strptime(stamp[:12], "%Y%m%d%H%M").replace(tzinfo=CN_TZ)
            bars.append(
                {
                    "datetime": dt,
                    "open": float(row[1]),
                    "close": float(row[2]),
                    "high": float(row[3]),
                    "low": float(row[4]),
                    "volume": float(row[5]),
                }
            )
        except (TypeError, ValueError):
            continue
    return sorted(bars, key=lambda x: x["datetime"])


def fetch_tencent_fqkline_1m(symbol: str, trade_date: str) -> list[dict]:
    """腾讯 fqkline m1（部分网络易 501，仅作末位备源）。"""
    sym = _norm_code(symbol)
    td = trade_date.strip()
    tsym = Symbol(Market.CN, sym).to_tencent()
    text = market_get(
        _TENCENT_FQKLINE,
        host_key="ifzq.gtimg.cn",
        min_interval_s=0.15,
        params={"param": f"{tsym},m1,,,800,qfq", "_var": "kline_min"},
        timeout=10,
        retries=1,
        parse="text",
        log_label="腾讯fqkline 1m",
        symbol=sym,
        trust_env=False,
    )
    if not text or "=" not in text:
        return []
    js = text.split("=", 1)[1].strip().rstrip(";")
    try:
        data = json.loads(js)
    except json.JSONDecodeError:
        return []
    raw = data.get("data", {}) if isinstance(data, dict) else {}
    series: list = []
    if isinstance(raw, dict):
        sd = raw.get(tsym, {})
        if isinstance(sd, dict):
            series = sd.get("m1") or sd.get("qfqm1") or []
    bars: list[dict] = []
    for it in series or []:
        if len(it) < 5:
            continue
        ts_raw = str(it[0])
        dt = _parse_row_datetime(ts_raw)
        if dt is None and re.match(r"^\d{12}$", ts_raw):
            dt = datetime.strptime(ts_raw, "%Y%m%d%H%M").replace(tzinfo=CN_TZ)
        if dt is None:
            continue
        try:
            bars.append(
                {
                    "datetime": dt,
                    "open": float(it[1]),
                    "close": float(it[2]),
                    "high": float(it[3]),
                    "low": float(it[4]),
                    "volume": float(it[5]) if len(it) > 5 else 0.0,
                }
            )
        except (TypeError, ValueError):
            continue
    return filter_cn_session_bars(bars, td)


def fetch_sina_1m(symbol: str, trade_date: str) -> list[dict]:
    """新浪分钟线（akshare stock_zh_a_minute），仅保留 trade_date 当日。"""
    import akshare as ak

    td = (trade_date or "").strip()
    tsym = _to_sina_symbol(symbol)
    df = ak.stock_zh_a_minute(symbol=tsym, period="1", adjust="")
    if df is None or df.empty or "day" not in df.columns:
        return []
    bars: list[dict] = []
    for _, row in df.iterrows():
        dt = _parse_row_datetime(str(row["day"]))
        if dt is None or dt.date().isoformat() != td:
            continue
        try:
            bars.append(
                {
                    "datetime": dt,
                    "open": float(row["open"]),
                    "close": float(row["close"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "volume": float(row.get("volume") or 0.0),
                }
            )
        except (TypeError, ValueError):
            continue
    return sorted(bars, key=lambda x: x["datetime"])


_EM_FAIL_STREAK = 0
_EM_SKIP_UNTIL = 0.0
_EM_BREAKER_THRESHOLD = 3
_EM_BREAKER_COOLDOWN_S = 600.0


def _em_circuit_open() -> bool:
    return time.time() < _EM_SKIP_UNTIL


def _em_record(success: bool) -> None:
    global _EM_FAIL_STREAK, _EM_SKIP_UNTIL
    if success:
        _EM_FAIL_STREAK = 0
        return
    _EM_FAIL_STREAK += 1
    if _EM_FAIL_STREAK >= _EM_BREAKER_THRESHOLD:
        _EM_SKIP_UNTIL = time.time() + _EM_BREAKER_COOLDOWN_S
        _EM_FAIL_STREAK = 0
        logger.warning("东财 1m 连续失败，熔断 %.0fs（改走腾讯/其它源）", _EM_BREAKER_COOLDOWN_S)


def fetch_real_1m(symbol: str, trade_date: str) -> tuple[list[dict], str | None]:
    """按优先级尝试真 1m；全部失败返回 ([], None)。

    顺序：腾讯 mkline（近 ~1 个月，免 token）→ 东财（可熔断）→ Tushare（有 token 时）→ 腾讯当日 → 新浪。
    """
    sym = _norm_code(symbol)
    td = trade_date or ""

    steps: list[tuple[str, callable]] = [
        ("tencent_mkline", lambda: fetch_tencent_mkline_1m(sym, td)),
    ]
    em_steps: list[tuple[str, callable]] = [
        ("eastmoney", lambda: fetch_eastmoney_trends2_1m(sym, td)),
        ("eastmoney", lambda: fetch_eastmoney_kline_1m(sym, td)),
    ]
    tail: list[tuple[str, callable]] = []
    if resolve_tushare_token():
        tail.append(("tushare", lambda: fetch_tushare_1m(sym, td)))
    tail.extend(
        [
            ("tencent", lambda: fetch_tencent_minute_query(sym, td)),
            ("tencent", lambda: fetch_tencent_fqkline_1m(sym, td)),
            ("sina", lambda: fetch_sina_1m(sym, td)),
        ]
    )

    def _run(vendor: str, fn) -> list[dict]:
        try:
            return fn() or []
        except Exception as exc:
            logger.warning("real 1m %s %s %s: %s", vendor, sym, td, exc)
            return []

    for vendor, fn in steps:
        bars = _run(vendor, fn)
        if bars:
            return bars, vendor

    if not _em_circuit_open():
        for vendor, fn in em_steps:
            bars = _run(vendor, fn)
            if bars:
                _em_record(True)
                return bars, vendor
        _em_record(False)

    for vendor, fn in tail:
        bars = _run(vendor, fn)
        if bars:
            return bars, vendor
    return [], None
