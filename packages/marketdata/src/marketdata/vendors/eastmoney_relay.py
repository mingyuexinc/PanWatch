"""东财 push2his 中继客户端：本机不通时，由可访问东财的 VPS 代为拉取。

环境变量:
  PANWATCH_EM_RELAY_URL   中继根地址，如 https://your-vps:8765
  PANWATCH_EM_RELAY_TOKEN 与 relay server 一致的 Bearer token（建议强随机）

服务端进程请设 PANWATCH_EM_RELAY_SERVER=1，避免中继调用自身。
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from marketdata.http import market_get
from marketdata.vendors.eastmoney_hosts import EM_KLINE_PATH, EM_TRENDS2_PATH


def relay_config() -> tuple[str, str]:
    url = (os.environ.get("PANWATCH_EM_RELAY_URL") or "").strip().rstrip("/")
    token = (os.environ.get("PANWATCH_EM_RELAY_TOKEN") or "").strip()
    return url, token


def relay_enabled() -> bool:
    if (os.environ.get("PANWATCH_EM_RELAY_SERVER") or "").strip() in ("1", "true", "yes"):
        return False
    return bool(relay_config()[0])


def em_fetch_via_relay(
    api_path: str,
    params: dict,
    *,
    timeout: float = 18.0,
    symbol: str = "",
) -> dict | None:
    base, token = relay_config()
    if not base:
        return None
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    if api_path == EM_KLINE_PATH:
        relay_path = "/v1/kline/1m"
        keys = ("secid", "beg", "end", "klt", "lmt", "fqt", "rtntype")
    elif api_path == EM_TRENDS2_PATH:
        relay_path = "/v1/trends2"
        keys = ("secid", "ndays", "iscr", "ut", "fields1", "fields2")
    else:
        return None

    q = {k: str(params[k]) for k in keys if k in params}
    if api_path == EM_KLINE_PATH and "secid" in params:
        q.setdefault("secid", str(params["secid"]))
    host = urlparse(base).netloc or "em-relay"
    payload = market_get(
        f"{base}{relay_path}",
        host_key=host,
        params=q,
        headers=headers,
        timeout=timeout,
        retries=1,
        parse="json",
        log_label="东财中继",
        symbol=symbol,
        trust_env=True,
    )
    return payload if isinstance(payload, dict) else None
