"""T 日监控用：上一交易日收盘价（相对涨幅基准）。"""

from __future__ import annotations

import logging

from src.modules.market.daily_kline import prev_day_closes_as_of

logger = logging.getLogger(__name__)


def prev_trading_close(symbol: str, trade_date: str) -> float | None:
    """trade_date 当日盘中策略使用的昨收 = T-1 交易日 close。"""
    closes = prev_day_closes_as_of(symbol, trade_date, min_closes=1)
    if not closes:
        logger.warning("early_surge_monitor no prev close %s %s", symbol, trade_date)
        return None
    return float(closes[-1])
