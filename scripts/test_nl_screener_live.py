#!/usr/bin/env python3
"""手动运行:东财 NL 选股 + 第三方校验。需环境变量 EM_API_KEY 或 MX_STOCKS_SCREENER。"""

from __future__ import annotations

import sys

from marketdata import MarketData, StaticConfigProvider, SourceConfig

from src.platform.marketdata.nl_screener import (
    run_natural_language_screen,
    validate_screen_with_third_party,
)


def main() -> int:
    query = sys.argv[1] if len(sys.argv) > 1 else "今日连板的个股"
    md = MarketData(
        config=StaticConfigProvider(
            {"quote": [SourceConfig(vendor="tencent", priority=0)]}
        )
    )
    screen = run_natural_language_screen(query, select_type="A股", market_data=md)
    print(f"query={screen.query!r} rows={screen.row_count} source={screen.data_source}")
    for i, row in enumerate(screen.rows[:10], 1):
        print(f"  [{i}] {row}")
    if screen.row_count > 10:
        print(f"  ... 共 {screen.row_count} 行")

    if screen.row_count == 0:
        print("无结果,跳过校验")
        return 0

    report = validate_screen_with_third_party(screen, market_data=md)
    print("validation ok:", report.ok)
    if report.warnings:
        print("warnings:", *report.warnings, sep="\n  ")
    if report.failures:
        print("failures:", *report.failures, sep="\n  ")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
