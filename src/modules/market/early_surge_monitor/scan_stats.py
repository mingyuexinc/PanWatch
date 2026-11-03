"""监控扫描统计（竞价 / live 轮询日志用）。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AuctionScanStats:
    pool_size: int = 0
    quotes_received: int = 0
    missing_quotes: int = 0
    missing_prev_close: int = 0
    below_threshold: int = 0
    fired: int = 0
    fired_via_open: int = 0
    sample_below: list[str] = field(default_factory=list)
    sample_missing_quote: list[str] = field(default_factory=list)

    def to_log_dict(self) -> dict[str, int | list[str]]:
        return {
            "pool": self.pool_size,
            "quotes": self.quotes_received,
            "missing_quote": self.missing_quotes,
            "missing_prev": self.missing_prev_close,
            "below_threshold": self.below_threshold,
            "fired": self.fired,
            "fired_via_open": self.fired_via_open,
        }


@dataclass
class LivePollStats:
    pool_size: int = 0
    quotes_received: int = 0
    missing_quotes: int = 0
    missing_prev_close: int = 0
    below_threshold: int = 0
    outside_window: int = 0
    mkline_reject: int = 0
    fired: int = 0
    stale_quote_ts: int = 0

    def to_log_dict(self) -> dict[str, int]:
        return {
            "pool": self.pool_size,
            "quotes": self.quotes_received,
            "missing_quote": self.missing_quotes,
            "missing_prev": self.missing_prev_close,
            "below_threshold": self.below_threshold,
            "outside_window": self.outside_window,
            "mkline_reject": self.mkline_reject,
            "stale_quote_ts": self.stale_quote_ts,
            "fired": self.fired,
        }
