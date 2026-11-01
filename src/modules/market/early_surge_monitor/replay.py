from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from marketdata.vendors.real_1m import fetch_tencent_mkline_1m

from src.modules.market.early_surge_monitor.config import MonitorConfig
from src.modules.market.early_surge_monitor.engine import EarlySurgeMonitorEngine
from src.modules.market.early_surge_monitor.prev_close import prev_trading_close
from src.modules.market.early_surge_monitor.quotes import fetch_quote_ticks
from src.modules.market.early_surge_monitor.rules import TriggerDecision, reference_trigger_software_minute

CN_TZ = ZoneInfo("Asia/Shanghai")


@dataclass
class PoolScanStats:
    symbols_in_pool: int = 0
    skipped_no_prev_close: int = 0
    skipped_no_bars: int = 0
    replay_poll_points: int = 0
    hits: int = 0


@dataclass(frozen=True)
class ReplayResult:
    symbol: str
    trade_date: str
    prev_close: float | None
    ref_trigger_at: datetime | None
    ref_trigger_minute: str | None
    synth_trigger_at: datetime | None
    synth_trigger_minute: str | None
    synth_notify_at: datetime | None
    synth_trigger_price: float | None
    minute_delta: int | None
    notify_minus_ref_trigger_s: float | None
    confirm_reason: str | None
    quote_rows_live: int | None = None


def _bar_index(bars: list[dict]) -> dict[tuple[int, int], dict]:
    out: dict[tuple[int, int], dict] = {}
    for b in bars:
        dt = b["datetime"].astimezone(CN_TZ)
        out[(dt.hour, dt.minute)] = b
    return out


def _price_at_time(bars_by_hm: dict[tuple[int, int], dict], t: datetime) -> float | None:
    local = t.astimezone(CN_TZ)
    key = (local.hour, local.minute)
    bar = bars_by_hm.get(key)
    if not bar:
        return None
    if local.second == 0 and local.microsecond == 0:
        # 分钟起点 poll：与软件栏切换时展示的最新价一致（回放取该分钟 close）
        return float(bar["close"])
    start = local.replace(second=0, microsecond=0)
    elapsed = max(0.0, min(59.99, (local - start).total_seconds()))
    o = float(bar["open"])
    c = float(bar["close"])
    return o + (c - o) * (elapsed / 60.0)


def _simulated_poll_times(
    trade_date: str,
    cfg: MonitorConfig,
    *,
    until: datetime | None = None,
) -> list[datetime]:
    day = datetime.fromisoformat(trade_date).date()
    sh, sm = divmod(int(cfg.session_start_minute), 60)
    eh, em = divmod(int(cfg.session_end_minute) + 1, 60)
    t0 = datetime(day.year, day.month, day.day, sh, sm, 0, tzinfo=CN_TZ)
    t1 = datetime(day.year, day.month, day.day, eh, em, 0, tzinfo=CN_TZ)
    times: list[datetime] = []
    t = t0
    while t < t1:
        times.append(t)
        t += timedelta(seconds=cfg.poll_interval_s)
    for minute in range(cfg.session_start_minute, cfg.session_end_minute + 2):
        h, m = divmod(minute, 60)
        base = datetime(day.year, day.month, day.day, h, m, 0, tzinfo=CN_TZ)
        for extra in (base, base + timedelta(seconds=cfg.extra_poll_second)):
            if t0 <= extra < t1 and extra not in times:
                times.append(extra)
    times.sort()
    if until is not None:
        cap = until.astimezone(CN_TZ)
        times = [x for x in times if x <= cap]
    return times


def scan_pool_from_mkline(
    symbols: list[str],
    trade_date: str,
    *,
    cfg: MonitorConfig | None = None,
    prev_close_by_symbol: dict[str, float] | None = None,
    triggered_symbols: set[str] | None = None,
    until: datetime | None = None,
) -> tuple[list[tuple[str, TriggerDecision]], PoolScanStats]:
    """按指定交易日 1 分钟 K 线回放监控窗口，检测池内标的是否触发。"""
    from src.modules.intraday_quality.baostock_fetch import normalize_cn_symbol

    cfg = cfg or MonitorConfig()
    prev_map = {normalize_cn_symbol(k): float(v) for k, v in (prev_close_by_symbol or {}).items()}
    triggered = {normalize_cn_symbol(s) for s in (triggered_symbols or set())}
    fired_all: list[tuple[str, TriggerDecision]] = []
    stats = PoolScanStats(symbols_in_pool=len(symbols))
    poll_times = _simulated_poll_times(trade_date, cfg, until=until)
    stats.replay_poll_points = len(poll_times)

    for raw in symbols:
        sym = normalize_cn_symbol(raw)
        if not sym or sym in triggered:
            continue
        pc = prev_map.get(sym)
        if pc is None or pc <= 0:
            pc = prev_trading_close(sym, trade_date)
        if pc is None or pc <= 0:
            stats.skipped_no_prev_close += 1
            continue
        bars = fetch_tencent_mkline_1m(sym, trade_date)
        if not bars:
            stats.skipped_no_bars += 1
            continue
        by_hm = _bar_index(bars)
        engine = EarlySurgeMonitorEngine(cfg)
        engine.load_session(prev_close_by_symbol={sym: float(pc)}, triggered_symbols=triggered)
        had_price = False
        for pt in poll_times:
            price = _price_at_time(by_hm, pt)
            if price is None:
                continue
            had_price = True
            fired = engine.ingest_quotes([(sym, price, pt)], trade_date=trade_date, fallback_ts=pt)
            if fired:
                fired_all.append(fired[0])
                triggered.add(sym)
                stats.hits += 1
                break
        if not had_price:
            stats.skipped_no_bars += 1
    return fired_all, stats


def replay_day_from_mkline(
    symbol: str,
    trade_date: str,
    *,
    cfg: MonitorConfig | None = None,
    bars_1m: list[dict] | None = None,
) -> ReplayResult:
    cfg = cfg or MonitorConfig()
    bars = bars_1m if bars_1m is not None else fetch_tencent_mkline_1m(symbol, trade_date)
    pc = prev_trading_close(symbol, trade_date)
    ref_at: datetime | None = None
    if pc is not None:
        _, ref_at = reference_trigger_software_minute(
            bars, prev_close=pc, trigger_end_minute=40, trigger_ratio=cfg.trigger_ratio
        )

    by_hm = _bar_index(bars)
    engine = EarlySurgeMonitorEngine(cfg)
    poll_times = _simulated_poll_times(trade_date, cfg)

    synth_decision = None
    for pt in poll_times:
        price = _price_at_time(by_hm, pt)
        if price is None:
            continue
        fired = engine.ingest_quotes([(symbol, price, pt)], trade_date=trade_date, fallback_ts=pt)
        if fired:
            synth_decision = fired[0][1]
            break

    ref_min = ref_at.strftime("%H:%M") if ref_at else None
    synth_min = None
    synth_notify = None
    synth_price = None
    confirm_reason = None
    if synth_decision:
        if synth_decision.trigger_at:
            synth_min = synth_decision.trigger_at.strftime("%H:%M")
        synth_notify = synth_decision.notify_at
        synth_price = synth_decision.trigger_price
        confirm_reason = synth_decision.reason

    minute_delta = None
    notify_minus_ref = None
    if ref_at and synth_decision and synth_decision.trigger_at:
        ref_m = ref_at.hour * 60 + ref_at.minute
        syn_m = synth_decision.trigger_at.hour * 60 + synth_decision.trigger_at.minute
        minute_delta = syn_m - ref_m
        if synth_decision.notify_at:
            notify_minus_ref = (synth_decision.notify_at - ref_at).total_seconds()

    return ReplayResult(
        symbol=symbol,
        trade_date=trade_date,
        prev_close=pc,
        ref_trigger_at=ref_at,
        ref_trigger_minute=ref_min,
        synth_trigger_at=synth_decision.trigger_at if synth_decision else None,
        synth_trigger_minute=synth_min,
        synth_notify_at=synth_notify,
        synth_trigger_price=synth_price,
        minute_delta=minute_delta,
        notify_minus_ref_trigger_s=notify_minus_ref,
        confirm_reason=confirm_reason,
    )


def replay_day_live_quotes(symbol: str, trade_date: str, *, cfg: MonitorConfig | None = None) -> ReplayResult:
    cfg = cfg or MonitorConfig()
    bars = fetch_tencent_mkline_1m(symbol, trade_date)
    pc = prev_trading_close(symbol, trade_date)
    ref_at = None
    if pc is not None:
        _, ref_at = reference_trigger_software_minute(bars, prev_close=pc, trigger_end_minute=40)
    engine = EarlySurgeMonitorEngine(cfg)
    rows = fetch_quote_ticks([symbol], batch_size=1, timeout=cfg.quote_timeout_s, retries=cfg.quote_retries)
    now = datetime.now(CN_TZ)
    fired = engine.ingest_quotes(
        [(symbol, r[1], r[2]) for r in [(symbol, rows[0][1], rows[0][2])] if rows],
        trade_date=trade_date,
        fallback_ts=now,
    )
    d = fired[0][1] if fired else engine.try_trigger(symbol, rows[0][1], now, trade_date) if rows else None
    return ReplayResult(
        symbol=symbol,
        trade_date=trade_date,
        prev_close=pc,
        ref_trigger_at=ref_at,
        ref_trigger_minute=ref_at.strftime("%H:%M") if ref_at else None,
        synth_trigger_at=d.trigger_at if d and d.fired else None,
        synth_trigger_minute=d.trigger_at.strftime("%H:%M") if d and d.fired and d.trigger_at else None,
        synth_notify_at=d.notify_at if d and d.fired else None,
        synth_trigger_price=d.trigger_price if d and d.fired else None,
        minute_delta=None,
        notify_minus_ref_trigger_s=None,
        confirm_reason=d.reason if d else None,
        quote_rows_live=len(rows),
    )
