"""东财自然语言选股(方案 A):直连 HTTP selectSecurity,结构化返回,不经 CSV/子进程。

与 OpenClaw mx-stocks-screener Skill 同源 API:
  POST https://ai-saas.eastmoney.com/proxy/b/mcp/tool/selectSecurity
  Header: em_api_key
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from marketdata.errors import MarketDataError

SELECT_SECURITY_URL = "https://ai-saas.eastmoney.com/proxy/b/mcp/tool/selectSecurity"

SELECT_TYPES = frozenset({"A股", "港股", "美股", "基金", "ETF", "可转债", "板块"})

_CODE_ALIASES = ("股票代码", "证券代码", "代码")
_CONTINUOUS_LIMIT_ALIASES = ("连板天数", "连板数", "连板高度", "连续涨停天数", "涨停板数")


class NaturalLanguageScreenerError(MarketDataError):
    """自然语言选股调用或解析失败。"""


@dataclass(frozen=True)
class NaturalLanguageScreenResult:
    """一次 NL 选股的标准化结果。"""

    query: str
    select_type: str
    rows: tuple[dict[str, str], ...]
    column_names: tuple[str, ...]
    row_count: int
    data_source: str  # dataList | partialResults
    security_count: int | None = None


def _build_metadata(*, query: str, select_type: str) -> dict[str, Any]:
    return {
        "query": query,
        "selectType": select_type,
        "toolContext": {
            "callId": f"call_{uuid.uuid4().hex[:8]}",
            "userInfo": {"userId": f"user_{uuid.uuid4().hex[:8]}"},
        },
    }


def _build_column_map(columns: list[dict]) -> dict[str, str]:
    name_map: dict[str, str] = {}
    for col in columns or []:
        if not isinstance(col, dict):
            continue
        en_key = col.get("field", "") or col.get("name", "") or col.get("key", "")
        cn_name = col.get("displayName", "") or col.get("title", "") or col.get("label", "")
        date_msg = col.get("dateMsg")
        if date_msg:
            cn_name = f"{cn_name} {date_msg}"
        if en_key is not None and cn_name is not None:
            name_map[str(en_key)] = str(cn_name)
    return name_map


def _columns_order(columns: list[dict]) -> list[str]:
    order: list[str] = []
    for col in columns or []:
        if not isinstance(col, dict):
            continue
        en_key = col.get("field") or col.get("name") or col.get("key")
        if en_key is not None:
            order.append(str(en_key))
    return order


def _parse_partial_results_table(partial_results: str) -> list[dict[str, str]]:
    if not partial_results or not isinstance(partial_results, str):
        return []
    lines = [ln.strip() for ln in partial_results.strip().splitlines() if ln.strip()]
    if not lines:
        return []

    def split_cells(line: str) -> list[str]:
        return [c.strip() for c in line.split("|") if c.strip() != ""]

    header_cells = split_cells(lines[0])
    if not header_cells:
        return []
    data_start = 1
    if data_start < len(lines) and re.match(r"^[\s|\-]+$", lines[data_start]):
        data_start = 2
    rows: list[dict[str, str]] = []
    for i in range(data_start, len(lines)):
        cells = split_cells(lines[i])
        if len(cells) != len(header_cells):
            if len(cells) < len(header_cells):
                cells.extend([""] * (len(header_cells) - len(cells)))
            else:
                cells = cells[: len(header_cells)]
        rows.append(dict(zip(header_cells, cells)))
    return rows


def _datalist_to_rows(
    datalist: list[dict],
    column_map: dict[str, str],
    column_order: list[str],
) -> list[dict[str, str]]:
    if not datalist:
        return []
    rows: list[dict[str, str]] = []
    for row in datalist:
        if not isinstance(row, dict):
            continue
        cn_row: dict[str, str] = {}
        for en_key in column_order:
            if en_key not in row:
                continue
            cn_name = column_map.get(en_key, en_key)
            val = row[en_key]
            if val is None:
                cn_row[cn_name] = ""
            elif isinstance(val, (dict, list)):
                cn_row[cn_name] = json.dumps(val, ensure_ascii=False)
            else:
                cn_row[cn_name] = str(val)
        rows.append(cn_row)
    return rows


def _drop_columns_for_sector(rows: list[dict[str, str]], select_type: str) -> list[dict[str, str]]:
    if select_type != "板块" or not rows:
        return rows
    blocked = {"板块编码", "指数内码"}
    cleaned: list[dict[str, str]] = []
    for row in rows:
        cleaned.append({k: v for k, v in row.items() if str(k).strip() not in blocked})
    return cleaned


def parse_select_security_payload(
    raw: dict[str, Any],
    *,
    query: str,
    select_type: str,
) -> NaturalLanguageScreenResult:
    """将 selectSecurity 响应体解析为 NaturalLanguageScreenResult。"""
    raw_message = raw.get("message")
    if isinstance(raw_message, str) and raw_message.strip():
        raise NaturalLanguageScreenerError(raw_message.strip())

    all_results = raw.get("allResults")
    if not isinstance(all_results, dict):
        all_results = {}
    result_node = all_results.get("result")
    if not isinstance(result_node, dict):
        result_node = {}

    data_list = result_node.get("dataList", [])
    if not isinstance(data_list, list):
        data_list = []
    columns = result_node.get("columns", [])
    if not isinstance(columns, list):
        columns = []

    rows: list[dict[str, str]] = []
    data_source = ""
    if data_list:
        column_map = _build_column_map(columns)
        column_order = _columns_order(columns)
        rows = _datalist_to_rows(data_list, column_map, column_order)
        data_source = "dataList"

    if not rows:
        partial_results = raw.get("partialResults")
        if partial_results:
            rows = _parse_partial_results_table(str(partial_results))
            data_source = "partialResults"

    if not rows:
        if raw.get("securityCount", -1) == 0:
            return NaturalLanguageScreenResult(
                query=query,
                select_type=select_type,
                rows=(),
                column_names=(),
                row_count=0,
                data_source="empty",
                security_count=0,
            )
        raise NaturalLanguageScreenerError(
            "返回中无有效 dataList 且 partialResults 无法解析或为空"
        )

    rows = _drop_columns_for_sector(rows, select_type)
    if not rows:
        raise NaturalLanguageScreenerError("过滤板块字段后无可输出数据")

    fieldnames = tuple(rows[0].keys())
    sec_count = raw.get("securityCount")
    if sec_count is not None and not isinstance(sec_count, int):
        try:
            sec_count = int(sec_count)
        except (TypeError, ValueError):
            sec_count = None

    return NaturalLanguageScreenResult(
        query=query,
        select_type=select_type,
        rows=tuple(rows),
        column_names=fieldnames,
        row_count=len(rows),
        data_source=data_source,
        security_count=sec_count,
    )


def fetch_natural_language_screen(
    query: str,
    *,
    select_type: str = "A股",
    api_key: str,
    timeout: float = 60.0,
) -> NaturalLanguageScreenResult:
    """调用东财 NL 选股 HTTP 接口并解析为结构化结果。"""
    normalized_query = (query or "").strip()
    normalized_type = (select_type or "").strip()
    if not normalized_query:
        raise NaturalLanguageScreenerError("query 不能为空")
    if not normalized_type:
        raise NaturalLanguageScreenerError("select_type 不能为空")
    if normalized_type not in SELECT_TYPES:
        raise NaturalLanguageScreenerError(f"不支持的 select_type: {normalized_type}")
    key = (api_key or "").strip()
    if not key:
        raise NaturalLanguageScreenerError("EM_API_KEY 未配置")

    meta = _build_metadata(query=normalized_query, select_type=normalized_type)
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                SELECT_SECURITY_URL,
                json=meta,
                headers={"Content-Type": "application/json", "em_api_key": key},
            )
            response.raise_for_status()
            payload = response.json()
    except NaturalLanguageScreenerError:
        raise
    except httpx.HTTPError as exc:
        raise NaturalLanguageScreenerError(f"HTTP 调用失败: {exc}") from exc
    except ValueError as exc:
        raise NaturalLanguageScreenerError(f"响应 JSON 解析失败: {exc}") from exc

    if not isinstance(payload, dict):
        raise NaturalLanguageScreenerError("响应 JSON 顶层不是对象")
    raw = payload.get("data")
    if not isinstance(raw, dict):
        raise NaturalLanguageScreenerError(
            f"响应缺少 data 字段,顶层键: {list(payload.keys())}"
        )
    return parse_select_security_payload(
        raw, query=normalized_query, select_type=normalized_type
    )


def extract_stock_codes(rows: tuple[dict[str, str], ...] | list[dict[str, str]]) -> list[str]:
    """从选股结果行中提取 6 位 A 股代码(去重保序)。"""
    seen: set[str] = set()
    codes: list[str] = []
    for row in rows:
        code = ""
        for alias in _CODE_ALIASES:
            if alias in row and row[alias]:
                code = str(row[alias]).strip()
                break
        if not code:
            continue
        digits = re.sub(r"\D", "", code)
        if len(digits) >= 6:
            norm = digits[-6:]
        else:
            continue
        if norm not in seen:
            seen.add(norm)
            codes.append(norm)
    return codes


def parse_continuous_limit_up_days(row: dict[str, str]) -> int | None:
    """解析连板天数字段;无法解析时返回 None。"""
    raw = ""
    for alias in _CONTINUOUS_LIMIT_ALIASES:
        if alias in row and str(row[alias]).strip():
            raw = str(row[alias]).strip()
            break
    if not raw:
        return None
    match = re.match(r"^(?:连续\s*)?(?P<days>\d+)", raw)
    if match:
        return int(match.group("days"))
    if raw.isdigit():
        return int(raw)
    return None
