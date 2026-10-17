"""Baostock 5m 数据源可行性探测（5m 统一口径 → Wyckoff analyze_intraday）。

依赖: pip install baostock pandas
可选: 克隆 Wyckoff-Analysis 并传入 --wyckoff-root，跑 strength_score 端到端。

用法:
    python scripts/research/test_baostock_5m_feasibility.py
    python scripts/research/test_baostock_5m_feasibility.py --code sz.002437 --day 2026-09-30 \\
        --wyckoff-root .tmp/Wyckoff-Analysis
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import baostock as bs
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_TX_M1 = ROOT / "scripts/replay/fixtures/tx_min1.json"

_BS_TIME_RE = re.compile(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})")


def parse_bs_time(raw: str) -> pd.Timestamp:
    m = _BS_TIME_RE.match(str(raw))
    if not m:
        return pd.NaT
    y, mo, d, h, mi, se = m.groups()
    return pd.Timestamp(f"{y}-{mo}-{d} {h}:{mi}:{se}")


def fetch_klines(
    code: str,
    start: str,
    end: str,
    *,
    frequency: str = "5",
    adjustflag: str = "2",
) -> pd.DataFrame:
    """adjustflag: 1=后复权, 2=前复权, 3=不复权（Baostock 文档）。"""
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login: {lg.error_msg}")
    rs = bs.query_history_k_data_plus(
        code,
        "date,time,open,high,low,close,volume,amount,adjustflag",
        start_date=start,
        end_date=end,
        frequency=frequency,
        adjustflag=adjustflag,
    )
    if rs.error_code != "0":
        bs.logout()
        raise RuntimeError(f"baostock query: {rs.error_msg}")
    rows: list[list[str]] = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    bs.logout()
    if not rows:
        return pd.DataFrame(columns=rs.fields)
    return pd.DataFrame(rows, columns=rs.fields)


def to_intraday_df(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    df["datetime"] = df["time"].map(parse_bs_time)
    for col in ("open", "high", "low", "close", "volume", "amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    out = df.dropna(subset=["datetime", "close"]).sort_values("datetime")
    return out[["datetime", "open", "high", "low", "close", "volume", "amount"]].reset_index(drop=True)


def compare_tencent_close(day: str, bs_close: float) -> dict[str, float | None]:
    if not FIXTURE_TX_M1.exists():
        return {"tencent_m1_last_close": None, "close_diff": None}
    payload = json.loads(FIXTURE_TX_M1.read_text(encoding="utf-8"))
    m1 = payload.get("data", {}).get("sz002437", {}).get("m1") or []
    compact = day.replace("-", "")
    bars = [b for b in m1 if str(b[0]).startswith(compact)]
    if not bars:
        return {"tencent_m1_last_close": None, "close_diff": None}
    tx_close = float(bars[-1][2])
    return {"tencent_m1_last_close": tx_close, "close_diff": round(bs_close - tx_close, 4)}


def run_wyckoff(df_5m: pd.DataFrame, df_15m: pd.DataFrame, wyckoff_root: Path):
    sys.path.insert(0, str(wyckoff_root.resolve()))
    from core.intraday_analysis import analyze_intraday  # noqa: WPS433

    profile = analyze_intraday(df_5m, df_5m, df_15m)
    return profile


def main() -> int:
    parser = argparse.ArgumentParser(description="Baostock 5m 可行性探测")
    parser.add_argument("--code", default="sz.002437", help="Baostock 代码，如 sz.002437")
    parser.add_argument("--day", default="2026-09-30", help="探测用交易日 YYYY-MM-DD")
    parser.add_argument("--window-start", default="2026-04-01", help="长窗口起点（测耗时）")
    parser.add_argument("--window-end", default="2026-10-16", help="长窗口终点")
    parser.add_argument("--wyckoff-root", type=Path, default=None, help="Wyckoff-Analysis 仓库根目录")
    args = parser.parse_args()

    day = args.day
    print(f"=== Baostock 5m 探测 code={args.code} day={day} ===")

    t0 = time.perf_counter()
    raw_day = fetch_klines(args.code, day, day, frequency="5", adjustflag="2")
    fetch_day_sec = time.perf_counter() - t0
    df5 = to_intraday_df(raw_day)

    t0 = time.perf_counter()
    raw15 = fetch_klines(args.code, day, day, frequency="15", adjustflag="2")
    fetch_15_sec = time.perf_counter() - t0
    df15 = to_intraday_df(raw15)

    imp = (df5["amount"] / df5["volume"].replace(0, pd.NA)).median()
    close_last = float(df5["close"].iloc[-1]) if len(df5) else float("nan")
    cmp_tx = compare_tencent_close(day, close_last)

    print(f"  5m bars: {len(df5)} (期望 48)  耗时: {fetch_day_sec:.3f}s")
    print(f"  15m bars: {len(df15)} (期望 16)  耗时: {fetch_15_sec:.3f}s")
    if len(df5):
        print(f"  时间: {df5['datetime'].iloc[0]} ~ {df5['datetime'].iloc[-1]}")
        print(f"  amount/volume 中位数 vs close: {imp:.4f} vs {df5['close'].median():.4f} (≈1 为股+元)")
    if cmp_tx["tencent_m1_last_close"] is not None:
        print(
            f"  对照 fixture 腾讯 1m 收盘: {cmp_tx['tencent_m1_last_close']} "
            f"Baostock 5m 收盘: {close_last} diff={cmp_tx['close_diff']}"
        )

    t0 = time.perf_counter()
    raw_win = fetch_klines(
        args.code, args.window_start, args.window_end, frequency="5", adjustflag="2"
    )
    win_sec = time.perf_counter() - t0
    n_days = raw_win["date"].nunique() if not raw_win.empty else 0
    print(
        f"  长窗口 {args.window_start}~{args.window_end}: "
        f"{len(raw_win)} bars / {n_days} 日, 单次 login 耗时 {win_sec:.2f}s"
    )

    ok = (
        len(df5) == 48
        and len(df15) == 16
        and df5["amount"].notna().all()
        and fetch_day_sec < 5.0
    )
    print(f"  数据层结论: {'PASS' if ok else 'NEEDS_REVIEW'}")

    if args.wyckoff_root:
        if not args.wyckoff_root.is_dir():
            print(f"  Wyckoff 路径不存在: {args.wyckoff_root}", file=sys.stderr)
            return 1
        profile = run_wyckoff(df5, df15, args.wyckoff_root)
        print("  --- Wyckoff analyze_intraday (df_1m=df_5m=5m, df_15m=15m) ---")
        print(f"  strength_score={profile.strength_score} bars(after clean)={profile.bars}")
        print(
            f"  vwap_pos={profile.vwap_pos} close_pos={profile.close_pos} "
            f"trend_short={profile.trend_short} trend_mid={profile.trend_mid}"
        )
        print(f"  momentum_30m={profile.momentum_30m} momentum_15m={profile.momentum_15m}")
        print("  计算层结论: PASS（已产出 profile）")

    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
