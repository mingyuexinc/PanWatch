"""A 股日 K：严格早于 trade_date 的已完成日收盘序列（监控昨收、策略 MA 等共用）。"""

from __future__ import annotations

import logging
from datetime import date, datetime

from marketdata.symbol import Market, Symbol
from marketdata.vendors.kline import _em_secid, fetch_eastmoney_kline

from src.modules.intraday_quality.baostock_fetch import normalize_cn_symbol
from src.modules.market.screener_datetime import CN_TZ
from src.platform.marketdata.collectors.kline_collector import KlineCollector, kline_source
from src.platform.marketdata.models import MarketCode

logger = logging.getLogger(__name__)


def _cn_today_iso() -> str:
    return datetime.now(CN_TZ).date().isoformat()


def _fetch_days_for(trade_date: str) -> int:
    """保证日 K 窗口能覆盖 trade_date 之前的已完成交易日。"""
    try:
        td = date.fromisoformat(trade_date.strip())
        today = date.fromisoformat(_cn_today_iso())
        span = max(0, (today - td).days)
        return min(2000, max(120, span + 90))
    except ValueError:
        return 120


def _closes_before(klines: list, trade_date: str) -> list[float]:
    td = trade_date.strip()
    closes: list[float] = []
    for bar in klines:
        day = str(getattr(bar, "date", "") or "")[:10]
        if not day or day >= td:
            continue
        try:
            closes.append(float(bar.close))
        except (TypeError, ValueError):
            continue
    return closes


def _closes_from_eastmoney(symbol: str, trade_date: str, *, days: int) -> list[float]:
    sym = Symbol(Market.CN, symbol.strip())
    bars = fetch_eastmoney_kline(_em_secid(sym), days)
    return _closes_before(bars, trade_date)


def prev_day_closes_as_of(symbol: str, trade_date: str, *, min_closes: int = 4) -> list[float] | None:
    """严格早于 trade_date 的已完成日收盘（升序）；末段最多返回 60 根。"""
    td = (trade_date or "").strip()
    if not td:
        return None
    sym = normalize_cn_symbol(symbol)
    if not sym:
        return None

    need_days = _fetch_days_for(td)
    closes: list[float] = []

    try:
        with kline_source("daily_prev_close"):
            klines = KlineCollector(MarketCode.CN).get_klines(sym, days=need_days)
        closes = _closes_before(klines, td)
    except Exception as exc:
        logger.warning("daily kline collector failed %s: %s", sym, exc)

    if len(closes) < min_closes:
        try:
            em = _closes_from_eastmoney(sym, td, days=need_days)
            if len(em) > len(closes):
                closes = em
        except Exception as exc:
            logger.warning("daily kline eastmoney fallback failed %s: %s", sym, exc)

    if len(closes) < min_closes:
        return None
    return closes[-60:]
