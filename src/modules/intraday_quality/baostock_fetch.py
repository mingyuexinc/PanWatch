"""Baostock 分钟 K 线拉取（A 股 5m/15m，前复权）。"""

from __future__ import annotations

import re
import threading
import time
from contextlib import contextmanager
from datetime import date
from typing import Iterator

import baostock as bs
import pandas as pd

_BS_TIME_RE = re.compile(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})")
_BS_LOGIN_LOCK = threading.Lock()


def normalize_cn_symbol(symbol: str) -> str:
    """A 股六位代码补零（001216 若被存成 1216 会导致 Baostock 代码错误）。"""
    code = (symbol or "").strip()
    if not code:
        return code
    lower = code.lower()
    for prefix in ("sh.", "sz.", "bj."):
        if lower.startswith(prefix):
            body = code[len(prefix) :]
            if body.isdigit():
                return f"{prefix[:2].upper()}.{body.zfill(6)}"
            return code
    if code.isdigit():
        return code.zfill(6)
    return code


def symbol_to_baostock(symbol: str, market: str) -> str:
    code = normalize_cn_symbol(symbol)
    mk = (market or "CN").upper()
    if mk != "CN":
        raise ValueError("当前仅支持 A 股(CN) Baostock 分钟数据")
    if code.lower().startswith(("sh.", "sz.", "bj.")):
        return code.lower()
    if code.startswith(("sh", "sz", "bj")) and len(code) > 2 and code[2].isdigit():
        return f"{code[:2].lower()}.{code[2:]}"
    if code.startswith(("6", "9")):
        return f"sh.{code}"
    if code.startswith(("0", "3", "2")):
        return f"sz.{code}"
    if code.startswith(("4", "8")):
        return f"bj.{code}"
    return f"sh.{code}"


def _parse_bs_time(raw: str) -> pd.Timestamp:
    m = _BS_TIME_RE.match(str(raw))
    if not m:
        return pd.NaT
    y, mo, d, h, mi, se = m.groups()
    return pd.Timestamp(f"{y}-{mo}-{d} {h}:{mi}:{se}")


class BaostockSession:
    """复用单次 Baostock 登录，避免批量算分时的「用户未登录」竞态。"""

    def __init__(self) -> None:
        self._active = False

    def __enter__(self) -> BaostockSession:
        with _BS_LOGIN_LOCK:
            lg = bs.login()
            if lg.error_code != "0":
                raise RuntimeError(f"Baostock 登录失败: {lg.error_msg}")
            self._active = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._active:
            with _BS_LOGIN_LOCK:
                bs.logout()
            self._active = False

    def fetch_intraday_bars(
        self,
        symbol: str,
        market: str,
        trade_date: str,
        *,
        frequency: str = "5",
    ) -> pd.DataFrame:
        if not self._active:
            raise RuntimeError("BaostockSession 未登录")
        return _query_intraday_bars(symbol, market, trade_date, frequency=frequency)


@contextmanager
def baostock_session() -> Iterator[BaostockSession]:
    with BaostockSession() as session:
        yield session


def _query_intraday_bars(
    symbol: str,
    market: str,
    trade_date: str,
    *,
    frequency: str = "5",
    retries: int = 2,
) -> pd.DataFrame:
    td = (trade_date or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", td):
        raise ValueError("trade_date 格式应为 YYYY-MM-DD")
    bs_code = symbol_to_baostock(symbol, market)
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,time,open,high,low,close,volume,amount",
            start_date=td,
            end_date=td,
            frequency=frequency,
            adjustflag="2",
        )
        if rs.error_code == "0":
            rows: list[list[str]] = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                return pd.DataFrame(
                    columns=["datetime", "open", "high", "low", "close", "volume", "amount"]
                )
            df = pd.DataFrame(rows, columns=rs.fields)
            df["datetime"] = df["time"].map(_parse_bs_time)
            for col in ("open", "high", "low", "close", "volume", "amount"):
                df[col] = pd.to_numeric(df[col], errors="coerce")
            out = df.dropna(subset=["datetime", "close"]).sort_values("datetime")
            return out[
                ["datetime", "open", "high", "low", "close", "volume", "amount"]
            ].reset_index(drop=True)
        msg = rs.error_msg or "未知错误"
        last_err = RuntimeError(f"Baostock 查询失败({bs_code}): {msg}")
        if "未登录" in msg and attempt < retries:
            time.sleep(0.15 * (attempt + 1))
            lg = bs.login()
            if lg.error_code != "0":
                raise RuntimeError(f"Baostock 重新登录失败: {lg.error_msg}") from last_err
            continue
        raise last_err
    raise last_err or RuntimeError("Baostock 查询失败")


def fetch_intraday_bars(
    symbol: str,
    market: str,
    trade_date: str,
    *,
    frequency: str = "5",
    session: BaostockSession | None = None,
) -> pd.DataFrame:
    """拉取单日分钟线，返回 datetime/open/high/low/close/volume/amount。"""
    if session is not None:
        return session.fetch_intraday_bars(symbol, market, trade_date, frequency=frequency)
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"Baostock 登录失败: {lg.error_msg}")
    try:
        return _query_intraday_bars(symbol, market, trade_date, frequency=frequency)
    finally:
        bs.logout()


def today_iso() -> str:
    return date.today().isoformat()
