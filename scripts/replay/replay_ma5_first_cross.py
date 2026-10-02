"""回放验证: 股价上穿当日5日线(仅首次捕获) —— 002437 誉衡药业 2026-09-30。

数据(fixtures/, 腾讯行情源, 2026-10-01 节后抓取):
- tx_daily_qfq.json  日K(前复权): 提供 09-30 之前已完成交易日收盘
- tx_min1.json       1分钟K线: 09-30 全天 241 根(09:31~11:30, 13:01~15:00)

回放语义:
- 规则: {type: ma, op: ">=", value: 5} + repeat_mode=once(仅首次) + 交易时段
- 滚动均线: MA5 = (前4个已完成交易日收盘之和 + 现价) / 5
- 扫描时钟: 模拟 PriceAlertScheduler 默认 60s 节拍对齐分钟收盘时刻,
  第 T 分钟的扫描看到第 T 分钟收盘价(即 T:00 时刻的现价)。
- 走 PanWatch 真实评估路径 PriceAlertEngine.eval_rule(K线摘要注入缓存),
  触发记账(enabled/once)复刻 scan_once 行为; 回放不落库、不发通知。

用法:
    python scripts/replay/replay_ma5_first_cross.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time as time_mod
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.modules.market.price_alert_engine import PriceAlertEngine
from src.platform.persistence.models import PriceAlertRule, Stock

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SYMBOL = "002437"
NAME = "誉衡药业"
MARKET = "CN"
REPLAY_DATE = "2026-09-30"
REPLAY_DATE_COMPACT = "20260930"
PERIOD = 5
SCAN_INTERVAL_SEC = 60  # PanWatch PriceAlertScheduler 默认 60s


def load_prev_day_closes(target_date: str) -> list[float]:
    """目标交易日之前的全部已完成交易日收盘(升序)。"""
    payload = json.loads((FIXTURES / "tx_daily_qfq.json").read_text(encoding="utf-8"))
    bars = payload["data"]["sz002437"]["qfqday"]
    return [float(b[2]) for b in bars if b[0] < target_date]


def load_minute_bars(day_compact: str) -> list[dict]:
    """腾讯 m1 字段: [时间, 开, 收, 高, 低, 量]; bar 时间戳=分钟收盘时刻。"""
    payload = json.loads((FIXTURES / "tx_min1.json").read_text(encoding="utf-8"))
    data = payload["data"]["sz002437"]
    key = next(k for k in data if k.startswith("m1"))
    bars = []
    for b in data[key]:
        if b[0][:8] == day_compact:
            bars.append(
                {
                    "ts": b[0],
                    "open": float(b[1]),
                    "close": float(b[2]),
                    "high": float(b[3]),
                    "low": float(b[4]),
                    "volume": float(b[5]),
                }
            )
    return bars


def fmt_ts(ts: str) -> str:
    return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}:00"


async def replay() -> dict:
    prev_closes = load_prev_day_closes(REPLAY_DATE)
    bars = load_minute_bars(REPLAY_DATE_COMPACT)
    if not bars:
        raise SystemExit(f"fixtures 中没有 {REPLAY_DATE} 的分钟数据")

    prev_close = prev_closes[-1]
    threshold = round(sum(prev_closes[-(PERIOD - 1):]) / (PERIOD - 1), 6)

    stock = Stock(symbol=SYMBOL, market=MARKET, name=NAME)
    rule = PriceAlertRule(
        stock=stock,
        name=f"{NAME} 现价上穿当日{PERIOD}日线(仅首次)",
        enabled=True,
        condition_group={"op": "and", "items": [{"type": "ma", "op": ">=", "value": PERIOD}]},
        market_hours_mode="trading_only",
        cooldown_minutes=30,
        max_triggers_per_day=1,
        repeat_mode="once",
    )

    engine = PriceAlertEngine()
    engine._kline_cache[f"{MARKET}:{SYMBOL}"] = (
        time_mod.monotonic(),
        {"prev_day_closes": prev_closes[-60:]},
    )

    # ── 数据真相: 等价阈值下的首次触及/首次分钟收盘站上 ──────────────
    first_touch = next(
        (b for b in bars if b["high"] >= threshold - 1e-9), None
    )
    first_close_above = next(
        (b for b in bars if b["close"] >= threshold - 1e-9), None
    )

    # ── 引擎回放: 60s 扫描时钟, 复刻 scan_once 的评估与 once 记账 ──
    capture = None
    trace: list[dict] = []
    for bar in bars:
        if not rule.enabled:
            break
        quote = {
            "current_price": bar["close"],
            "change_pct": round((bar["close"] / prev_close - 1) * 100, 2),
        }
        ev = await engine.eval_rule(rule, quote)
        if ev.matched:
            cond = next(h for h in ev.hits if h.get("type") == "ma")
            capture = {
                "scan_time": fmt_ts(bar["ts"]),
                "price": bar["close"],
                "ma5": cond.get("target"),
                "scan_clock": f"60s节拍, 对齐分钟收盘时刻",
            }
            rule.last_trigger_at = bar["ts"]
            rule.trigger_count_today = 1
            rule.enabled = False  # repeat_mode=once: 首次捕获后停用
            trace.append(
                {"ts": fmt_ts(bar["ts"]), "close": bar["close"], "ma5": cond.get("target"), "matched": True}
            )
        elif abs(bar["close"] - threshold) < 0.03 and len(trace) < 12:
            trace.append(
                {"ts": fmt_ts(bar["ts"]), "close": bar["close"], "matched": False}
            )

    result = {
        "symbol": SYMBOL,
        "name": NAME,
        "replay_date": REPLAY_DATE,
        "rule": {
            "condition": f"现价 >= 当日滚动{PERIOD}日线",
            "repeat_mode": "once(仅首次捕获)",
            "scan_interval_sec": SCAN_INTERVAL_SEC,
        },
        "ma_base": {
            "prev_trading_day_closes": prev_closes[-(PERIOD - 1):],
            "equivalent_threshold": threshold,
            "note": f"现价>={PERIOD}日线 ⟺ 现价>=前{PERIOD-1}日收盘均值(当日常数)",
        },
        "data_truth": {
            "open": bars[0]["open"],
            "open_above_threshold": bars[0]["open"] >= threshold,
            "first_touch_time": fmt_ts(first_touch["ts"]) if first_touch else None,
            "first_touch_high": first_touch["high"] if first_touch else None,
            "first_minute_close_above_time": (
                fmt_ts(first_close_above["ts"]) if first_close_above else None
            ),
        },
        "engine_capture": capture,
        "near_event_trace": trace,
        "day_close": bars[-1]["close"],
        "bars_replayed": len(bars),
    }
    return result


def main() -> None:
    result = asyncio.run(replay())
    out_path = Path(__file__).resolve().parent / "result_002437_20260930.json"
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    base = result["ma_base"]
    truth = result["data_truth"]
    cap = result["engine_capture"]
    print("=" * 62)
    print(f"{result['name']}({result['symbol']}) {result['replay_date']} "
          f"现价上穿当日{PERIOD}日线 · 仅首次捕获")
    print("=" * 62)
    print(f"前{PERIOD-1}个已完成交易日收盘: {base['prev_trading_day_closes']}")
    print(f"等价阈值(当日常数): {base['equivalent_threshold']}")
    print(f"开盘价: {truth['open']} (开盘{'已' if truth['open_above_threshold'] else '未'}站上阈值)")
    print(f"盘中首次触及阈值(分钟最高价): {truth['first_touch_time']} "
          f"(high={truth['first_touch_high']})")
    print(f"首个收盘站上阈值的分钟: {truth['first_minute_close_above_time']}")
    print("-" * 62)
    if cap:
        print(f"★ 信号捕获时间(引擎, 60s扫描): {cap['scan_time']}")
        print(f"  捕获时现价={cap['price']}  滚动MA{PERIOD}={cap['ma5']}")
    else:
        print("★ 当日未触发")
    print("-" * 62)
    print(f"收盘价: {result['day_close']}  回放分钟数: {result['bars_replayed']}")
    print(f"明细已写入: {out_path}")


if __name__ == "__main__":
    main()
