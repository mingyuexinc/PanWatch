"""东财 push2his / push2 API 镜像与统一 GET（分钟 K / 分时 / 日 K 共用 host 列表）。"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import urllib.parse
from typing import Any

from marketdata.http import market_get

logger = logging.getLogger(__name__)

# 镜像优先：主域 push2his 在部分网络/代理下易被 RST，33/63 往往更稳。
EM_PUSH2HIS_BASES: tuple[str, ...] = (
    "https://33.push2his.eastmoney.com",
    "https://63.push2his.eastmoney.com",
    "https://push2his.eastmoney.com",
)

EM_PUSH2_BASES: tuple[str, ...] = ("https://push2.eastmoney.com",)

EM_KLINE_PATH = "/api/qt/stock/kline/get"
EM_TRENDS2_PATH = "/api/qt/stock/trends2/get"

EM_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "*/*",
}

EM_UT_KLINE = "fa5fd1943c7b386f172d6893dbfba10b"
EM_UT_TRENDS = "7eea3edcaed734bea9cbfc24409ed989"

EM_KLINE_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
EM_KLINE_FIELDS1 = "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"


def em_host_key(base_url: str) -> str:
    return base_url.split("//", 1)[-1].split("/", 1)[0]


def resolve_em_http_proxy() -> str | None:
    """第三方/隧道 HTTP(S) 代理，仅用于东财 push 域（覆盖 NO_PROXY 直连策略）。

    示例: http://user:pass@gate.proxy.com:8080 或 socks5h://127.0.0.1:1080
    """
    raw = (os.environ.get("PANWATCH_EM_HTTP_PROXY") or "").strip()
    return raw or None


def em_kline_params(
    secid: str,
    *,
    klt: str = "1",
    beg: str,
    end: str,
    fqt: str = "1",
    lmt: str | None = "500",
    with_ut: bool = False,
) -> dict[str, str]:
    """与 efinance get_quote_history_single 对齐（含 rtntype=6；1m 历史靠 beg/end）。"""
    params: dict[str, str] = {
        "secid": secid,
        "klt": str(klt),
        "fqt": str(fqt),
        "beg": beg,
        "end": end,
        "rtntype": "6",
        "fields1": EM_KLINE_FIELDS1,
        "fields2": EM_KLINE_FIELDS2,
    }
    if lmt:
        params["lmt"] = str(lmt)
    if with_ut:
        params["ut"] = EM_UT_KLINE
    return params


_em_no_proxy_merged = False


def _ensure_em_no_proxy() -> None:
    global _em_no_proxy_merged
    if _em_no_proxy_merged:
        return
    from marketdata.http import merge_eastmoney_no_proxy

    merge_eastmoney_no_proxy()
    _em_no_proxy_merged = True


def _em_get_json_requests(
    url: str, params: dict, *, timeout: float, proxy: str | None = None
) -> dict | None:
    try:
        import warnings

        import requests
        import urllib3
    except ImportError:
        return None
    try:
        session = requests.Session()
        session.trust_env = False
        req_proxy = {"http": proxy, "https": proxy} if proxy else None
        with warnings.catch_warnings():
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            resp = session.get(
                url,
                params=params,
                headers=EM_DEFAULT_HEADERS,
                timeout=timeout,
                verify=False,
                proxies=req_proxy,
            )
        resp.raise_for_status()
        payload = resp.json()
        return payload if isinstance(payload, dict) else None
    except Exception as exc:
        logger.debug("东财 requests 回退失败 %s: %s", url, exc)
        return None


def _em_get_json_curl(
    url: str, params: dict, *, timeout: float, proxy: str | None = None
) -> dict | None:
    """Windows 上 push2his 常对 OpenSSL 客户端 RST；系统 curl(schannel) 偶发更稳。"""
    if not shutil.which("curl"):
        return None
    qs = urllib.parse.urlencode(params)
    full = f"{url}?{qs}" if qs else url
    cmd = ["curl", "-sS", "-m", str(max(5, int(timeout)))]
    if proxy:
        cmd.extend(["--proxy", proxy])
    else:
        cmd.extend(["--noproxy", "*"])
    cmd.extend(
        [
            "-H",
            f"Referer: {EM_DEFAULT_HEADERS['Referer']}",
            "-H",
            f"User-Agent: {EM_DEFAULT_HEADERS['User-Agent']}",
            full,
        ]
    )
    try:
        out = subprocess.check_output(cmd, text=True, errors="replace", stderr=subprocess.DEVNULL)
        payload = json.loads(out)
        return payload if isinstance(payload, dict) else None
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError) as exc:
        logger.debug("东财 curl 回退失败 %s: %s", url, exc)
        return None


def _payload_ok(payload: dict | None) -> bool:
    if not isinstance(payload, dict) or payload.get("data") is None:
        return False
    data = payload["data"]
    if isinstance(data, dict):
        if data.get("klines") or data.get("trends"):
            return True
    return payload.get("rc") in (None, 0)


def _fetch_em_url(
    url: str,
    params: dict,
    *,
    host: str,
    symbol: str,
    log_label: str,
    min_interval_s: float,
    timeout: float,
    retries: int,
    curl_first: bool,
) -> dict | None:
    em_proxy = resolve_em_http_proxy()
    if em_proxy:
        payload = market_get(
            url,
            host_key=host,
            min_interval_s=min_interval_s,
            params=params,
            headers=EM_DEFAULT_HEADERS,
            timeout=timeout,
            retries=retries,
            parse="json",
            log_label=log_label + "(经EM代理)",
            symbol=symbol,
            trust_env=False,
            proxy=em_proxy,
        )
        if _payload_ok(payload):
            return payload
        payload = _em_get_json_curl(url, params, timeout=timeout, proxy=em_proxy)
        if _payload_ok(payload):
            return payload
        payload = _em_get_json_requests(url, params, timeout=timeout, proxy=em_proxy)
        if _payload_ok(payload):
            return payload
        return None

    if curl_first:
        payload = _em_get_json_curl(url, params, timeout=timeout)
        if _payload_ok(payload):
            return payload
        payload = _em_get_json_requests(url, params, timeout=timeout)
        if _payload_ok(payload):
            return payload
    payload = market_get(
        url,
        host_key=host,
        min_interval_s=min_interval_s,
        params=params,
        headers=EM_DEFAULT_HEADERS,
        timeout=timeout,
        retries=0 if curl_first else retries,
        parse="json",
        log_label=log_label,
        symbol=symbol,
        trust_env=True,
    )
    if _payload_ok(payload):
        return payload
    if not curl_first:
        payload = _em_get_json_curl(url, params, timeout=timeout)
        if _payload_ok(payload):
            return payload
        payload = _em_get_json_requests(url, params, timeout=timeout)
        if _payload_ok(payload):
            return payload
    return None


def em_get_json(
    api_path: str,
    params: dict,
    *,
    symbol: str = "",
    log_label: str = "东财API",
    min_interval_s: float = 0.2,
    timeout: float = 18.0,
    retries: int = 2,
    bases: tuple[str, ...] = EM_PUSH2HIS_BASES,
    extra_bases: tuple[str, ...] = EM_PUSH2_BASES,
    param_variants: tuple[dict, ...] | None = None,
    curl_first: bool | None = None,
) -> dict | None:
    """按镜像轮询 GET JSON；任一有 body 即返回（由调用方解析 klines/trends）。"""
    _ensure_em_no_proxy()
    if curl_first is None:
        curl_first = api_path == EM_KLINE_PATH
    variants = param_variants or (params,)

    from marketdata.vendors.eastmoney_relay import em_fetch_via_relay, relay_enabled

    if relay_enabled():
        for variant in variants:
            relay_payload = em_fetch_via_relay(
                api_path,
                variant,
                timeout=timeout,
                symbol=symbol,
            )
            if _payload_ok(relay_payload):
                return relay_payload

    seen: set[str] = set()
    for base in (*bases, *extra_bases):
        if base in seen:
            continue
        seen.add(base)
        host = em_host_key(base)
        url = f"{base}{api_path}"
        for variant in variants:
            payload = _fetch_em_url(
                url,
                variant,
                host=host,
                symbol=symbol,
                log_label=log_label,
                min_interval_s=min_interval_s,
                timeout=timeout,
                retries=retries,
                curl_first=curl_first,
            )
            if _payload_ok(payload):
                return payload
    return None
