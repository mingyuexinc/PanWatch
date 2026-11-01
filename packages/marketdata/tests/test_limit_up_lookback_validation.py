"""前 N 日涨停 + 前一日未涨停 问句后置过滤。"""

from __future__ import annotations

from marketdata.nl_screener_validation import (
    evaluate_limit_up_lookback_row,
    filter_limit_up_lookback_rows,
    parse_limit_up_lookback_rule,
    resolve_limit_up_lookback_calendar,
)


def test_parse_limit_up_lookback_rule():
    q = "前10个交易日至少1次涨停，前一日未涨停；"
    rule = parse_limit_up_lookback_rule(q)
    assert rule is not None
    assert rule.lookback_trading_days == 10


def test_reject_prev_day_limit_up():
    changes = {
        "2026-10-29": 10.02,
        "2026-10-30": -9.94,
    }
    ok, reason = evaluate_limit_up_lookback_row(
        "600592",
        "龙溪股份",
        change_by_date=changes,
        prev_trade_date="2026-10-29",
        lookback_dates=["2026-10-20", "2026-10-29"],
    )
    assert ok is False
    assert reason and "2026-10-29" in reason


def test_filter_rows_with_injected_changes():
    rows = [{"股票代码": "600592", "股票名称": "龙溪股份"}]

    def _fetch(_code: str):
        return {"2026-10-29": 10.02, "2026-10-28": 1.0, "2026-10-27": 10.0}

    kept, report = filter_limit_up_lookback_rows(
        rows,
        parse_limit_up_lookback_rule("前10个交易日至少1次涨停，前一日未涨停") or None,
        prev_trade_date="2026-10-29",
        lookback_dates=["2026-10-27", "2026-10-28", "2026-10-29"],
        fetch_changes=_fetch,
    )
    assert kept == []
    assert any("600592" in f for f in report.failures)


def test_resolve_calendar():
    days = [f"2026-10-{d:02d}" for d in range(20, 31)]
    cal = resolve_limit_up_lookback_calendar(days, lookback_trading_days=10)
    assert cal is not None
    anchor, prev, lookback = cal
    assert anchor == "2026-10-30"
    assert prev == "2026-10-29"
    assert len(lookback) == 10
