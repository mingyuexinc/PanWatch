from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from src.modules.intraday_quality.baostock_fetch import normalize_cn_symbol
from src.modules.market.early_surge_monitor.buffer import QuoteTick, SymbolQuoteState
from src.modules.market.early_surge_monitor.config import MonitorConfig
from src.modules.market.early_surge_monitor.prev_close import prev_trading_close
from src.modules.market.early_surge_monitor.quotes import fetch_quote_ticks
from src.modules.market.early_surge_monitor.rules import (
    TriggerDecision,
    evaluate_quote_cross,
    in_monitor_window,
    mkline_confirms_quote_cross,
)
from src.modules.market.early_surge_monitor.scan_stats import LivePollStats

logger = logging.getLogger(__name__)
CN_TZ = ZoneInfo("Asia/Shanghai")


class EarlySurgeMonitorEngine:
    """L1 最新价 crossing；触发钟面 = 所在分钟 :00（与行情软件一致）。"""

    def __init__(self, cfg: MonitorConfig | None = None):
        self.cfg = cfg or MonitorConfig()
        self._states: dict[str, SymbolQuoteState] = {}
        self._prev_close: dict[str, float] = {}
        self._triggered: set[str] = set()
        self._last_decision: dict[str, TriggerDecision] = {}

    def _state(self, symbol: str) -> SymbolQuoteState:
        sym = symbol.strip()
        if sym not in self._states:
            self._states[sym] = SymbolQuoteState()
        return self._states[sym]

    def _ensure_prev_close(self, sym: str, trade_date: str) -> float | None:
        if sym in self._prev_close:
            return self._prev_close.get(sym)
        pc = prev_trading_close(sym, trade_date)
        if pc is not None:
            self._prev_close[sym] = pc
        return self._prev_close.get(sym)

    def load_session(
        self,
        *,
        prev_close_by_symbol: dict[str, float],
        triggered_symbols: set[str] | None = None,
    ) -> None:
        self._prev_close = {normalize_cn_symbol(k): float(v) for k, v in prev_close_by_symbol.items()}
        self._triggered = set(triggered_symbols or [])

    def try_trigger(
        self,
        symbol: str,
        price: float,
        quote_ts: datetime,
        trade_date: str,
        *,
        window_ts: datetime | None = None,
        stats: LivePollStats | None = None,
    ) -> TriggerDecision | None:
        sym = symbol.strip()
        if sym in self._triggered:
            return None
        ts = quote_ts.astimezone(CN_TZ) if quote_ts.tzinfo else quote_ts.replace(tzinfo=CN_TZ)
        wt = window_ts.astimezone(CN_TZ) if window_ts and window_ts.tzinfo else (
            window_ts.replace(tzinfo=CN_TZ) if window_ts else None
        )
        prev_close = self._ensure_prev_close(sym, trade_date)
        if (
            stats
            and wt
            and prev_close
            and prev_close > 0
            and not in_monitor_window(ts, self.cfg)
            and in_monitor_window(wt, self.cfg)
            and price >= prev_close * self.cfg.trigger_ratio
        ):
            stats.stale_quote_ts += 1
            logger.info(
                "early_surge live would miss on stale quote_ts %s quote_ts=%s poll_ts=%s price=%s",
                sym,
                ts.strftime("%H:%M:%S"),
                wt.strftime("%H:%M:%S"),
                price,
            )
        decision = evaluate_quote_cross(
            prev_close=prev_close,
            price=price,
            quote_ts=ts,
            cfg=self.cfg,
            already_triggered=False,
            window_ts=wt,
        )
        if not decision.fired:
            if stats:
                if decision.reason == "missing_prev_close":
                    stats.missing_prev_close += 1
                elif decision.reason == "below_threshold":
                    stats.below_threshold += 1
                elif decision.reason == "outside_window":
                    stats.outside_window += 1
            return decision

        if decision.need_mkline_confirm:
            ok = mkline_confirms_quote_cross(
                sym, trade_date, decision.trigger_at or wt or ts, prev_close or 0, self.cfg
            )
            if not ok:
                if stats:
                    stats.mkline_reject += 1
                return TriggerDecision(
                    False,
                    True,
                    "mkline_reject",
                    prev_close=prev_close,
                    trigger_price=price,
                    quote_ts=ts,
                )

        self._triggered.add(sym)
        self._last_decision[sym] = decision
        if stats:
            stats.fired += 1
        return decision

    def ingest_quotes(
        self,
        rows: list[tuple[str, float, datetime | None]],
        *,
        trade_date: str,
        fallback_ts: datetime,
        window_ts: datetime | None = None,
        stats: LivePollStats | None = None,
    ) -> list[tuple[str, TriggerDecision]]:
        fired: list[tuple[str, TriggerDecision]] = []
        wt = window_ts or fallback_ts
        for sym, price, qts in rows:
            ts = qts or fallback_ts
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=CN_TZ)
            self._state(sym).push(QuoteTick(quote_ts=ts.astimezone(CN_TZ), price=price))
            decision = self.try_trigger(sym, price, ts, trade_date, window_ts=wt, stats=stats)
            if decision and decision.fired:
                fired.append((sym, decision))
        return fired

    def poll_pool(
        self,
        symbols: list[str],
        trade_date: str,
        *,
        now: datetime | None = None,
    ) -> tuple[list[tuple[str, TriggerDecision]], LivePollStats]:
        now = (now or datetime.now(CN_TZ)).astimezone(CN_TZ)
        norm = [normalize_cn_symbol(s) for s in symbols if normalize_cn_symbol(s)]
        stats = LivePollStats(pool_size=len(norm))
        rows = fetch_quote_ticks(
            norm,
            batch_size=self.cfg.batch_size,
            timeout=self.cfg.quote_timeout_s,
            retries=self.cfg.quote_retries,
        )
        stats.quotes_received = len(rows)
        seen = {normalize_cn_symbol(s) for s, _, _ in rows}
        stats.missing_quotes = sum(1 for c in norm if c not in seen)
        if stats.missing_quotes and stats.missing_quotes >= max(3, stats.pool_size // 10):
            logger.warning(
                "early_surge live missing quotes pool=%s got=%s missing=%s",
                stats.pool_size,
                stats.quotes_received,
                stats.missing_quotes,
            )
        fired = self.ingest_quotes(
            rows,
            trade_date=trade_date,
            fallback_ts=now,
            window_ts=now,
            stats=stats,
        )
        return fired, stats

    def get_decision(self, symbol: str) -> TriggerDecision | None:
        return self._last_decision.get(symbol.strip())
