from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class QuoteTick:
    quote_ts: datetime
    price: float


@dataclass
class SymbolQuoteState:
    ticks: list[QuoteTick] = field(default_factory=list)
    max_ticks: int = 400

    def push(self, tick: QuoteTick) -> None:
        if tick.price <= 0:
            return
        self.ticks.append(tick)
        if len(self.ticks) > self.max_ticks:
            self.ticks = self.ticks[-self.max_ticks :]

    def last_before(self, boundary: datetime) -> QuoteTick | None:
        out: QuoteTick | None = None
        for t in self.ticks:
            if t.quote_ts < boundary:
                out = t
        return out


@dataclass(frozen=True)
class SyntheticMinuteBar:
    minute_start: datetime
    open: float
    high: float
    low: float
    close: float
    last_quote_ts: datetime | None


def minute_start(dt: datetime) -> datetime:
    local = dt.astimezone(CN_TZ)
    return local.replace(second=0, microsecond=0)


def freeze_minute_bar(state: SymbolQuoteState, boundary: datetime) -> SyntheticMinuteBar | None:
    """boundary = 下一分钟起点；上一根 bar 为 [boundary-1min, boundary)。"""
    bar_start = boundary.astimezone(CN_TZ) - timedelta(minutes=1)
    last = state.last_before(boundary)
    if last is None:
        return None
    in_bar = [t for t in state.ticks if bar_start <= t.quote_ts.astimezone(CN_TZ) < boundary.astimezone(CN_TZ)]
    if not in_bar:
        return None
    prices = [t.price for t in in_bar]
    return SyntheticMinuteBar(
        minute_start=bar_start,
        open=prices[0],
        high=max(prices),
        low=min(prices),
        close=last.price,
        last_quote_ts=last.quote_ts,
    )
