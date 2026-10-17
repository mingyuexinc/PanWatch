"""Wyckoff analyze_intraday → strength_score（全日 5m 试验口径）。"""

from __future__ import annotations

import pandas as pd

from src.modules.intraday_quality.baostock_fetch import BaostockSession, fetch_intraday_bars
from src.modules.intraday_quality.wyckoff.intraday_analysis import analyze_intraday


def compute_strength_score(
    symbol: str,
    market: str,
    trade_date: str,
    *,
    session: BaostockSession | None = None,
) -> float:
    """全日 5m + 15m，df_1m=df_5m，剔除集合竞价由 Wyckoff 内部处理。"""
    td = (trade_date or "").strip()
    df5 = fetch_intraday_bars(symbol, market, td, frequency="5", session=session)
    if df5.empty or len(df5) < 10:
        raise ValueError(f"{td} 无足够 5 分钟 K 线数据")
    try:
        df15 = fetch_intraday_bars(symbol, market, td, frequency="15", session=session)
    except Exception:
        df15 = None
    profile = analyze_intraday(
        df5,
        df5,
        df15 if df15 is not None and not df15.empty else None,
    )
    return round(float(profile.strength_score), 1)
