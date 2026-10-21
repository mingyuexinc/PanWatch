"""PanWatch 宿主接线:东财自然语言选股 + 第三方校验。"""

from __future__ import annotations

import os

from marketdata import MarketData
from marketdata.nl_screener_validation import ScreenerValidationReport
from marketdata.vendors.nl_screener import NaturalLanguageScreenResult

from src.platform.marketdata.marketdata_client import get_market_data


def resolve_em_api_key() -> str:
    key = (os.environ.get("EM_API_KEY") or os.environ.get("MX_STOCKS_SCREENER") or "").strip()
    if not key:
        raise RuntimeError("未配置 EM_API_KEY 或 MX_STOCKS_SCREENER")
    return key


def run_natural_language_screen(
    query: str,
    *,
    select_type: str = "A股",
    market_data: MarketData | None = None,
    api_key: str | None = None,
) -> NaturalLanguageScreenResult:
    md = market_data or get_market_data()
    key = (api_key or resolve_em_api_key()).strip()
    return md.natural_language_screen(query, select_type=select_type, api_key=key)


def validate_screen_with_third_party(
    screen: NaturalLanguageScreenResult,
    *,
    market_data: MarketData | None = None,
    min_continuous_days: int = 2,
) -> ScreenerValidationReport:
    md = market_data or get_market_data()
    return md.validate_continuous_limit_up_screen(
        screen,
        min_continuous_days=min_continuous_days,
    )
