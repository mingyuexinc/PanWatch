"""自然语言选股(方案 A)单元测试 + 可选 live 集成测试。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from marketdata import MarketData, StaticConfigProvider, SourceConfig
from marketdata.nl_screener_validation import (
    limit_up_change_pct_threshold,
    validate_continuous_limit_up_screen,
)
from marketdata.types import Quote
from marketdata.vendors.nl_screener import (
    extract_stock_codes,
    fetch_natural_language_screen,
    parse_select_security_payload,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "nl_screener_sample.json"


def test_parse_datalist_fixture():
    raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    result = parse_select_security_payload(raw, query="今日连板的个股", select_type="A股")
    assert result.row_count == 2
    assert result.data_source == "dataList"
    codes = extract_stock_codes(result.rows)
    assert codes == ["000001", "600000"]


def test_validate_against_tencent_quotes_mock():
    raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    screen = parse_select_security_payload(raw, query="q", select_type="A股")
    quotes = [
        Quote(symbol="000001", market="CN", name="示例A", current_price=11.0, change_pct=10.01),
        Quote(symbol="600000", market="CN", name="示例B", current_price=10.0, change_pct=9.99),
    ]
    report = validate_continuous_limit_up_screen(screen, quotes, reference_pool=[])
    assert report.ok is True
    assert report.checked_codes == ["000001", "600000"]


def test_validate_fails_when_not_limit_up():
    raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    screen = parse_select_security_payload(raw, query="q", select_type="A股")
    quotes = [
        Quote(symbol="000001", market="CN", name="示例A", current_price=10.5, change_pct=5.0),
    ]
    report = validate_continuous_limit_up_screen(screen, quotes, reference_pool=[])
    assert report.ok is False
    assert any("000001" in f for f in report.failures)


def test_limit_up_threshold_main_and_chinext():
    assert limit_up_change_pct_threshold("600519", "贵州茅台") == 9.5
    assert limit_up_change_pct_threshold("300750", "宁德时代") == 19.5
    assert limit_up_change_pct_threshold("688001", "华兴源创") == 19.5


@pytest.mark.skipif(
    not (os.environ.get("EM_API_KEY") or os.environ.get("MX_STOCKS_SCREENER")),
    reason="需要 EM_API_KEY 或 MX_STOCKS_SCREENER",
)
def test_live_continuous_limit_up_screen_and_validate():
    api_key = (os.environ.get("EM_API_KEY") or os.environ.get("MX_STOCKS_SCREENER") or "").strip()

    md = MarketData(
        config=StaticConfigProvider(
            {
                "quote": [
                    SourceConfig(vendor="tencent", priority=0),
                    SourceConfig(vendor="eastmoney", priority=5),
                ],
            }
        )
    )
    query = "今日连板的个股"
    screen = md.natural_language_screen(query, select_type="A股", api_key=api_key, timeout=90.0)
    assert screen.query == query
    print(f"nl_screener rows={screen.row_count} source={screen.data_source}")

    if screen.row_count == 0:
        pytest.skip("当前无连板结果(非交易时段或市场无标的),跳过校验")

    report = md.validate_continuous_limit_up_screen(screen, min_continuous_days=2)
    print("validation failures:", report.failures)
    print("validation warnings:", report.warnings)
    assert report.ok, report.failures
