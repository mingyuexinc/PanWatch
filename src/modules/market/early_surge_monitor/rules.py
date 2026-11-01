from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from marketdata.vendors.real_1m import fetch_tencent_mkline_1m

from src.modules.market.early_surge_monitor.config import MonitorConfig

CN_TZ = ZoneInfo("Asia/Shanghai")


def _minute_of(dt: datetime) -> int:
    local = dt.astimezone(CN_TZ)
    return local.hour * 60 + local.minute


def in_monitor_window(dt: datetime, cfg: MonitorConfig) -> bool:
    m = _minute_of(dt)
    return cfg.session_start_minute <= m <= cfg.session_end_minute


def software_trigger_clock(quote_ts: datetime) -> datetime:
    """与软件分时/1 分钟栏一致：触发时间显示为所在分钟起点。"""
    local = quote_ts.astimezone(CN_TZ)
    return local.replace(second=0, microsecond=0, tzinfo=CN_TZ)


def is_near_threshold(price: float, prev_close: float, cfg: MonitorConfig) -> bool:
    target = prev_close * cfg.trigger_ratio
    if target <= 0:
        return False
    diff_bps = abs(price / target - 1.0) * 10_000
    return diff_bps <= cfg.near_threshold_bps


@dataclass(frozen=True)
class TriggerDecision:
    fired: bool
    need_mkline_confirm: bool
    reason: str
    prev_close: float | None = None
    trigger_price: float | None = None
    quote_ts: datetime | None = None
    trigger_at: datetime | None = None
    notify_at: datetime | None = None


def evaluate_quote_cross(
    *,
    prev_close: float | None,
    price: float,
    quote_ts: datetime,
    cfg: MonitorConfig,
    already_triggered: bool,
) -> TriggerDecision:
    """最新价 crossing：与软件「涨幅 vs 昨收」同源。"""
    if already_triggered:
        return TriggerDecision(False, False, "already_triggered")
    if prev_close is None or prev_close <= 0:
        return TriggerDecision(False, False, "missing_prev_close")
    if not in_monitor_window(quote_ts, cfg):
        return TriggerDecision(False, False, "outside_window")

    threshold = prev_close * cfg.trigger_ratio
    if price < threshold:
        return TriggerDecision(False, False, "below_threshold")

    near = is_near_threshold(price, prev_close, cfg)
    need_confirm = cfg.mkline_confirm and near
    trig_at = software_trigger_clock(quote_ts)
    from datetime import timedelta

    notify_at = trig_at + timedelta(seconds=cfg.notify_delay_s)
    return TriggerDecision(
        True,
        need_confirm,
        "near_threshold" if near else "ok",
        prev_close=prev_close,
        trigger_price=price,
        quote_ts=quote_ts.astimezone(CN_TZ),
        trigger_at=trig_at,
        notify_at=notify_at,
    )


def mkline_confirms_quote_cross(
    symbol: str,
    trade_date: str,
    trigger_at: datetime,
    prev_close: float,
    cfg: MonitorConfig,
) -> bool:
    """复核：该分钟 mkline 的 high/close 至少一项 >= 阈值。"""
    bars = fetch_tencent_mkline_1m(symbol, trade_date)
    if not bars:
        return False
    target = prev_close * cfg.trigger_ratio
    want = trigger_at.astimezone(CN_TZ).replace(second=0, microsecond=0)
    for b in bars:
        dt = b["datetime"].astimezone(CN_TZ).replace(second=0, microsecond=0)
        if dt != want:
            continue
        return float(b["high"]) >= target or float(b["close"]) >= target
    return False


def reference_trigger_software_minute(
    bars: list[dict],
    *,
    prev_close: float,
    trigger_end_minute: int = 40,
    trigger_ratio: float = 1.03,
) -> tuple[float, datetime | None]:
    """参照软件：首个 high>=阈值的分钟 K，触发显示时刻 = 该分钟  M:00。"""
    threshold = prev_close * trigger_ratio

    def _in_window(dt: datetime) -> bool:
        m = _minute_of(dt)
        return (9 * 60 + 30) <= m <= (9 * 60 + trigger_end_minute)

    session = sorted((b for b in bars if _in_window(b["datetime"])), key=lambda x: x["datetime"])
    for b in session:
        if float(b["high"]) >= threshold or float(b["close"]) >= threshold:
            dt = b["datetime"].astimezone(CN_TZ).replace(second=0, microsecond=0, tzinfo=CN_TZ)
            return prev_close, dt
    return prev_close, None
