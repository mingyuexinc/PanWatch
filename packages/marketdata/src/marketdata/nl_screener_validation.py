"""NL 选股结果校验:用独立于妙想 API 的行情/涨停池数据交叉验证。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from marketdata.http import market_get
from marketdata.types import Quote
from marketdata.vendors.nl_screener import (
    NaturalLanguageScreenResult,
    extract_stock_codes,
    parse_continuous_limit_up_days,
)

_ZT_POOL_URL = "https://push2ex.eastmoney.com/getTopicZTPool"
_ZT_POOL_HOST = "push2ex.eastmoney.com"
_ZT_UT = "fa5fd1943c7b386f344d50f14e532481"


@dataclass
class LimitUpReferenceRow:
    code: str
    name: str = ""
    continuous_days: int | None = None


@dataclass
class ScreenerValidationReport:
    ok: bool
    checked_codes: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reference_zt_pool_size: int | None = None
    reference_zt_continuous_ge2: set[str] = field(default_factory=set)


def limit_up_change_pct_threshold(code: str, name: str = "") -> float:
    """按板块/ST 规则估算涨停幅度下限(略低于理论值以容忍四舍五入)。"""
    upper_name = (name or "").upper()
    if "ST" in upper_name or (name or "").startswith("*"):
        return 4.5
    c = re.sub(r"\D", "", code)[-6:] if code else ""
    if c.startswith(("688", "300", "301")):
        return 19.5
    if c.startswith(("8", "4")):
        return 29.5
    return 9.5


def fetch_eastmoney_limit_up_pool(trade_date: date | None = None) -> list[LimitUpReferenceRow]:
    """东财涨停池(第三方参考,与 NL 选股 API 不同域名)。非交易日或盘前可能为空。"""
    d = trade_date or date.today()
    date_text = d.strftime("%Y%m%d")
    params = {
        "ut": _ZT_UT,
        "d": date_text,
        "Pageindex": 0,
        "pagesize": 10000,
        "sort": "fbt:asc",
        "fild": "fbt:asc",
        "_": 1,
    }
    payload = market_get(
        _ZT_POOL_URL,
        params=params,
        host_key=_ZT_POOL_HOST,
        timeout=20.0,
        parse="json",
    )
    if not isinstance(payload, dict):
        return []
    if payload.get("rc") not in (0, None):
        return []
    data = payload.get("data") or {}
    pool = data.get("pool") if isinstance(data, dict) else None
    if not isinstance(pool, list):
        return []
    out: list[LimitUpReferenceRow] = []
    for item in pool:
        if not isinstance(item, dict):
            continue
        code = str(item.get("c") or item.get("f12") or "").strip()
        digits = re.sub(r"\D", "", code)
        if len(digits) < 6:
            continue
        norm = digits[-6:]
        name = str(item.get("n") or item.get("f14") or "").strip()
        lbc = item.get("lbc") if item.get("lbc") is not None else item.get("f23")
        continuous: int | None = None
        if lbc is not None:
            try:
                continuous = int(lbc)
            except (TypeError, ValueError):
                continuous = None
        out.append(LimitUpReferenceRow(code=norm, name=name, continuous_days=continuous))
    return out


def validate_continuous_limit_up_screen(
    screen: NaturalLanguageScreenResult,
    quotes: list[Quote],
    *,
    reference_pool: list[LimitUpReferenceRow] | None = None,
    min_continuous_days: int = 2,
    require_continuous_column: bool = False,
) -> ScreenerValidationReport:
    """校验「连板」类选股:今日涨停(腾讯/东财行情) + 连板天数 >= min(若列存在)。"""
    codes = extract_stock_codes(screen.rows)
    report = ScreenerValidationReport(ok=True, checked_codes=codes)

    quote_by_code = {q.symbol.zfill(6)[-6:]: q for q in quotes if q.market == "CN"}

    ref_ge2: set[str] = set()
    if reference_pool is not None:
        report.reference_zt_pool_size = len(reference_pool)
        for row in reference_pool:
            if row.continuous_days is not None and row.continuous_days >= min_continuous_days:
                ref_ge2.add(row.code)
        report.reference_zt_continuous_ge2 = ref_ge2

    if not codes and screen.row_count == 0:
        report.warnings.append("选股结果为空(可能非交易时段或无匹配)")
        return report

    if not codes:
        report.ok = False
        report.failures.append("结果行存在但未能解析股票代码")
        return report

    for row in screen.rows:
        row_codes = extract_stock_codes((row,))
        code = row_codes[0] if row_codes else "?"
        days = parse_continuous_limit_up_days(row)
        if days is None and require_continuous_column:
            report.ok = False
            report.failures.append(f"{code}: 缺少连板天数字段")
            continue
        if days is not None and days < min_continuous_days:
            report.ok = False
            report.failures.append(f"{code}: 连板天数 {days} < {min_continuous_days}")

    for code in codes:
        q = quote_by_code.get(code)
        if q is None:
            report.ok = False
            report.failures.append(f"{code}: 第三方腾讯行情未返回")
            continue
        threshold = limit_up_change_pct_threshold(code, q.name)
        pct = q.change_pct
        if pct is None:
            report.ok = False
            report.failures.append(f"{code}: 涨跌幅缺失")
            continue
        if pct < threshold:
            report.ok = False
            report.failures.append(
                f"{code}({q.name}): 涨跌幅 {pct}% 低于涨停阈值 {threshold}%"
            )

        if ref_ge2 and code not in ref_ge2:
            # 涨停池连板统计可能与 NL 字段口径不同,记 warning 不直接 fail
            report.warnings.append(
                f"{code}: 不在东财涨停池连板>={min_continuous_days} 参考集合中"
            )

    return report
