"""ma 条件(现价 vs 盘中滚动均线)的评估与校验。

滚动定义: MA(N) = (前N-1个已完成交易日收盘之和 + 现价) / N,
因此 "现价 >= 当日N日线" 等价于 "现价 >= 前(N-1)日收盘均值",
且该等价阈值在当日盘内为常数。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.modules.market.price_alert_engine import PriceAlertEngine
from src.modules.market.price_alert_service import validate_condition_group
from src.platform.marketdata.collectors.kline_collector import (
    KlineData,
    _completed_day_closes,
)
from src.platform.marketdata.models import MarketCode

# 002437 誉衡药业 2026-09-30 前的真实前4个已完成交易日收盘
# (09-23/09-24/09-28/09-29, 09-25 中秋休市), 来自腾讯日K。
CLOSES_002437 = [3.64, 3.50, 3.45, 3.47]


def _engine_with_closes(closes: list[float]) -> PriceAlertEngine:
    eng = PriceAlertEngine()

    async def fake_kline(market, symbol):
        return {"prev_day_closes": closes}

    eng._get_kline_summary_cached = fake_kline
    return eng


def _eval_ma(eng: PriceAlertEngine, price: float, op: str = ">=", period: float = 5):
    return asyncio.run(
        eng._eval_condition(
            {"type": "ma", "op": op, "value": period},
            {"current_price": price},
            MarketCode.CN,
            "002437",
        )
    )


def test_ma_matches_exactly_at_threshold():
    """现价恰好等于均线时 '>=' 应命中(浮点累积误差不得误判)。"""
    eng = _engine_with_closes(CLOSES_002437)
    # 等价阈值 = 14.06/4 = 3.515; 滚动MA5(3.515)=(14.06+3.515)/5=3.515
    ok, detail = _eval_ma(eng, 3.515)
    assert ok is True
    assert detail["matched"] is True
    assert detail["target"] == pytest.approx(3.515)
    assert detail["actual"] == 3.515
    assert detail["period"] == 5


def test_ma_below_threshold_not_matched():
    eng = _engine_with_closes(CLOSES_002437)
    ok, detail = _eval_ma(eng, 3.51)
    assert ok is False
    assert detail["target"] == pytest.approx((14.06 + 3.51) / 5)


def test_ma_above_threshold_matched():
    eng = _engine_with_closes(CLOSES_002437)
    ok, detail = _eval_ma(eng, 3.52)
    assert ok is True
    assert detail["target"] == pytest.approx((14.06 + 3.52) / 5)


def test_ma_equivalent_to_prev_closes_average():
    """滚动定义等价性: P >= MA5(P) 当且仅当 P >= 前4日收盘均值。"""
    eng = _engine_with_closes(CLOSES_002437)
    threshold = sum(CLOSES_002437) / 4
    for price in (3.40, 3.514, 3.5149, 3.515, 3.5151, 3.60):
        ok, _ = _eval_ma(eng, price)
        assert ok == (price >= threshold - 1e-9), price


def test_ma_insufficient_history_fails_closed():
    eng = _engine_with_closes(CLOSES_002437[:3])  # 仅3根,MA5需4根
    ok, detail = _eval_ma(eng, 3.60)
    assert ok is False
    assert detail["error"] == "insufficient_history"


def test_ma_invalid_period_fails_closed():
    eng = _engine_with_closes(CLOSES_002437)
    for bad in (1, 61, 5.5):
        ok, detail = _eval_ma(eng, 3.60, period=bad)
        assert ok is False
        assert detail["error"] == "invalid_ma_params"


def test_service_accepts_ma_condition_and_normalizes():
    group = validate_condition_group(
        {"op": "and", "items": [{"type": "ma", "op": ">=", "value": 5}]}
    )
    assert group["items"][0] == {"type": "ma", "op": ">=", "value": 5}


def test_service_rejects_ma_between_and_bad_period():
    with pytest.raises(ValueError, match="between"):
        validate_condition_group(
            {"op": "and", "items": [{"type": "ma", "op": "between", "value": [1, 2]}]}
        )
    with pytest.raises(ValueError, match="均线周期"):
        validate_condition_group(
            {"op": "and", "items": [{"type": "ma", "op": ">=", "value": 1}]}
        )
    with pytest.raises(ValueError, match="均线周期"):
        validate_condition_group(
            {"op": "and", "items": [{"type": "ma", "op": ">=", "value": 61}]}
        )


def _bar(date: str, close: float) -> KlineData:
    return KlineData(date=date, open=close, close=close, high=close, low=close, volume=1)


def test_completed_day_closes_drops_in_progress_today_bar():
    tz = ZoneInfo("Asia/Shanghai")
    klines = [_bar("2026-09-29", 3.47), _bar("2026-09-30", 3.50)]
    # 盘中(10:00,未到15:00收盘): 当日实时bar未定稿,应剔除
    now = datetime(2026, 9, 30, 10, 0, tzinfo=tz)
    assert _completed_day_closes(klines, MarketCode.CN, now=now) == [3.47]
    # 午休(12:00): 当日bar仍只定稿一半,同样剔除
    now = datetime(2026, 9, 30, 12, 0, tzinfo=tz)
    assert _completed_day_closes(klines, MarketCode.CN, now=now) == [3.47]
    # 收盘后(15:00): 当日bar已定稿,保留
    now = datetime(2026, 9, 30, 15, 0, tzinfo=tz)
    assert _completed_day_closes(klines, MarketCode.CN, now=now) == [3.47, 3.50]
    # 次日(节假日): 末根已是历史,保留
    now = datetime(2026, 10, 1, 10, 0, tzinfo=tz)
    assert _completed_day_closes(klines, MarketCode.CN, now=now) == [3.47, 3.50]
