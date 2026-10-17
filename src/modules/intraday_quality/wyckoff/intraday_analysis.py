"""
多周期分钟线分析模块（纯计算层）。

输入: 已获取的分钟线 DataFrame
输出: IntradayProfile 结构化特征
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from src.modules.intraday_quality.wyckoff.price_math import ret_pct as _ret_pct
from src.modules.intraday_quality.wyckoff.safe import safe_float as _safe_float

CN_TZ = ZoneInfo("Asia/Shanghai")


@dataclass
class IntradayProfile:
    bars: int
    last_close: float
    vwap: float
    vwap_pos: float
    close_pos: float
    trend_short: str
    trend_mid: str
    momentum_30m: float
    momentum_15m: float
    volume_concentration: str
    vol_price_corr: float
    effort_vs_result: float
    smart_money_score: float
    spring_quality: float | None
    strength_score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_volume_lot_unit(volume: pd.Series, amount: pd.Series, close: pd.Series) -> pd.Series:
    """修正 volume 的股/手单位混淆。

    A 股法定最小交易单位是"手"（100股），部分数据源的分钟线 volume 字段
    以"手"计数而非"股"，此时 amount(元)/volume 会systematically偏大约100倍。
    这里在数据清洗层一次性识别并换算，而不是让每个下游特征各自猜测——
    否则 volume 本身、amount/volume 派生的所有特征（VWAP、量比、尾盘份额）
    会出现不一致的单位口径。
    只在 amount 与 close*volume 的量级差恰好落在 100 倍附近时才换算，
    避免误伤本就自洽的数据。
    """
    valid = (volume > 0) & (close > 0) & amount.notna() & (amount > 0)
    if valid.sum() < 5:
        return volume
    implied_price = (amount[valid] / volume[valid]).median()
    ref_price = close[valid].median()
    if not (implied_price > 0 and ref_price > 0):
        return volume
    ratio = implied_price / ref_price
    if 60.0 <= ratio <= 160.0:
        return volume * 100.0
    return volume


_TAIL_AUCTION_START_CN = (14, 57)  # A 股尾盘集合竞价起点（含）
_TAIL_AUCTION_START_HK = (16, 0)  # 港股收盘集合竞价起点（含），16:00-16:10
_TAIL_AUCTION_FILTER_MAX_INTERVAL_MIN = 5.0  # 仅对分钟级细粒度K线生效，避免误伤30/60分钟等粗粒度K线


def _is_minute_grain(df: pd.DataFrame) -> bool:
    if len(df) < 2:
        return False
    deltas = df["datetime"].diff().dropna().dt.total_seconds() / 60.0
    if deltas.empty:
        return False
    return bool(deltas.median() <= _TAIL_AUCTION_FILTER_MAX_INTERVAL_MIN)


def strip_tail_auction_bars(df: pd.DataFrame, *, market: str = "cn") -> pd.DataFrame:
    """剔除尾盘集合竞价分钟线（仅对分钟级细粒度K线生效）。

    A 股 14:57-15:00 集合竞价；港股 16:00-16:10 收盘竞价。
    集合竞价是全天挂单在收盘瞬间一次性撮合，单根"1分钟K线"的成交量会脉冲式
    放大、价格也可能与前一刻连续竞价出现跳跃，直接计入"尾盘N分钟"类特征
    （尾盘缩量/放量占比、尾盘涨跌幅）会产生与真实盘口行为无关的假信号。
    30/60分钟等粗粒度K线本身已横跨集合竞价窗口，整根剔除没有意义，故跳过。
    """
    if df.empty or "datetime" not in df.columns or not _is_minute_grain(df):
        return df
    start = _TAIL_AUCTION_START_HK if market == "hk" else _TAIL_AUCTION_START_CN
    minute_of_day = df["datetime"].dt.hour * 60 + df["datetime"].dt.minute
    auction_cutoff = start[0] * 60 + start[1]
    kept = df[minute_of_day < auction_cutoff]
    return kept.reset_index(drop=True) if not kept.empty else df


def ensure_intraday_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if "datetime" not in out.columns:
        if "timestamp" in out.columns:
            dt = pd.to_datetime(out["timestamp"], errors="coerce", utc=True)
            out["datetime"] = dt.dt.tz_convert(CN_TZ)
        else:
            return pd.DataFrame()
    out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    if out["datetime"].dt.tz is None:
        out["datetime"] = out["datetime"].dt.tz_localize(CN_TZ, nonexistent="shift_forward", ambiguous="NaT")
    else:
        out["datetime"] = out["datetime"].dt.tz_convert(CN_TZ)
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col not in out.columns:
            out[col] = None
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["datetime", "close"]).sort_values("datetime").reset_index(drop=True)
    if out.empty:
        return out
    market = getattr(df, "attrs", {}).get("market", "cn")
    out = strip_tail_auction_bars(out, market=market)
    if out["amount"].isna().all():
        out["amount"] = out["close"] * out["volume"].fillna(0.0)
    else:
        out["volume"] = _normalize_volume_lot_unit(out["volume"], out["amount"], out["close"])
    return out


def infer_session_vwap(close: pd.Series, total_volume: float, total_amount: float) -> tuple[float, float]:
    """全天成交额加权均价（VWAP）。

    TickFlow 分钟线自带的 amount 已是真实成交额（元），与 volume 同源、单位自洽，
    直接相除即为真实 VWAP，无需猜测 volume 的股/手换算比例。
    仅当 amount 缺失/异常（相对价格中位数偏离过大，说明取数或单位有问题）时，
    才退化为用收盘价中位数近似，此时该值只应作为粗粒度参考，不用于精细阈值判断。
    """
    last_close = _safe_float(close.iloc[-1]) if len(close) else 0.0
    ref_price = _safe_float(close.tail(min(len(close), 30)).median(), last_close)
    if total_volume <= 0 or total_amount <= 0:
        return ref_price, 1.0
    vwap = total_amount / total_volume
    rel_err = abs(vwap - ref_price) / max(ref_price, 1e-8)
    if vwap <= 0 or rel_err > 0.5:
        return ref_price, 1.0
    return float(vwap), 1.0


def _compute_trend(df: pd.DataFrame, min_bars: int = 4) -> str:
    if df.empty or len(df) < min_bars:
        return "flat"
    close = pd.to_numeric(df["close"], errors="coerce").dropna()
    if len(close) < min_bars:
        return "flat"
    x = np.arange(len(close), dtype=float)
    y = close.values.astype(float)
    slope = float(np.polyfit(x, y, 1)[0])
    mean_price = float(y.mean()) if y.mean() != 0 else 1.0
    pct_slope = slope / mean_price * 100.0
    if pct_slope > 0.03:
        return "up"
    if pct_slope < -0.03:
        return "down"
    return "flat"


def _compute_volume_concentration(df: pd.DataFrame) -> str:
    if df.empty or len(df) < 20:
        return "even"
    close = df["close"].values.astype(float)
    volume = df["volume"].fillna(0.0).values.astype(float)
    mid = float(np.median(close))
    vol_above = float(volume[close >= mid].sum())
    vol_below = float(volume[close < mid].sum())
    total = vol_above + vol_below
    if total <= 0:
        return "even"
    ratio = vol_above / total
    if ratio > 0.62:
        return "high"
    if ratio < 0.38:
        return "low"
    return "even"


def compute_vol_price_corr(df: pd.DataFrame) -> float:
    """量价相关性：涨的时候放量、跌的时候缩量 → 正值（健康）；反之为负。"""
    if df.empty or len(df) < 20:
        return 0.0
    close = df["close"].ffill().values.astype(float)
    volume = df["volume"].fillna(0.0).values.astype(float)
    price_chg = np.diff(close)
    vol_chg = np.diff(volume)
    if len(price_chg) < 10:
        return 0.0
    std_p = float(np.std(price_chg))
    std_v = float(np.std(vol_chg))
    if std_p < 1e-9 or std_v < 1e-9:
        return 0.0
    corr = float(np.corrcoef(price_chg, vol_chg)[0, 1])
    return round(corr, 3) if np.isfinite(corr) else 0.0


def compute_effort_vs_result(df: pd.DataFrame) -> float:
    """盘中 Effort vs Result：高量低波=吸筹承接(正分)，低量高波=虚假波动(负分)。"""
    if df.empty or len(df) < 20:
        return 0.0
    close = df["close"].ffill().values.astype(float)
    volume = df["volume"].fillna(0.0).values.astype(float)
    n = len(close)
    window = min(20, n // 2)
    recent_vol = float(volume[-window:].mean())
    earlier_vol = float(volume[:-window].mean()) if n > window else recent_vol
    recent_range = float(np.abs(np.diff(close[-window:])).mean())
    earlier_range = float(np.abs(np.diff(close[:-window])).mean()) if n > window else recent_range
    if earlier_vol < 1e-9 or earlier_range < 1e-9:
        return 0.0
    vol_ratio = recent_vol / earlier_vol
    range_ratio = recent_range / earlier_range
    if vol_ratio > 1.3 and range_ratio < 0.7:
        return round(min((vol_ratio - range_ratio) * 30, 100.0), 1)
    if vol_ratio < 0.6 and range_ratio > 1.3:
        return round(max(-(range_ratio - vol_ratio) * 30, -100.0), 1)
    return 0.0


def compute_smart_money_score(df: pd.DataFrame) -> float:
    """聪明钱指标：尾盘（最后30根）vs 早盘（前30根）的量价方向对比。"""
    if df.empty or len(df) < 60:
        return 0.0
    close = df["close"].ffill().values.astype(float)
    volume = df["volume"].fillna(0.0).values.astype(float)
    early_vol = volume[:30]
    late_close = close[-30:]
    late_vol = volume[-30:]
    late_ret = (late_close[-1] - late_close[0]) / max(late_close[0], 1e-8) * 100
    early_vol_sum = float(early_vol.sum())
    late_vol_sum = float(late_vol.sum())
    vol_shift = (late_vol_sum / max(early_vol_sum, 1e-8)) - 1.0
    if late_ret > 0 and vol_shift > 0.1:
        return round(min((late_ret + vol_shift * 20), 100.0), 1)
    if late_ret < 0 and vol_shift > 0.2:
        return round(max((late_ret - vol_shift * 10), -100.0), 1)
    return round(late_ret * 0.5, 1)


def compute_spring_quality(df_1m: pd.DataFrame, daily_context: dict) -> float | None:
    support = _safe_float(daily_context.get("support_level"), 0.0)
    if support <= 0:
        return None
    close = df_1m["close"].values.astype(float)
    low = df_1m["low"].fillna(df_1m["close"]).values.astype(float)
    breach_mask = low < support * 0.998
    if not breach_mask.any():
        return None
    first_breach_idx = int(np.argmax(breach_mask))
    after_breach = close[first_breach_idx:]
    reclaim_mask = after_breach >= support * 1.001
    if not reclaim_mask.any():
        return 10.0
    bars_to_reclaim = int(np.argmax(reclaim_mask))
    if bars_to_reclaim <= 5:
        return 90.0
    if bars_to_reclaim <= 15:
        return 70.0
    if bars_to_reclaim <= 30:
        return 50.0
    return 30.0


def _score_price_and_trend(
    vwap_pos: float,
    close_pos: float,
    momentum_30m: float,
    momentum_15m: float,
    volume_concentration: str,
    trend_short: str,
    trend_mid: str,
) -> float:
    s = 0.0
    s += 10.0 if vwap_pos >= 0.8 else (4.0 if vwap_pos >= 0.0 else -7.0)
    s += 8.0 if close_pos >= 0.8 else (3.0 if close_pos >= 0.6 else (-8.0 if close_pos < 0.35 else 0.0))
    s += 6.0 if momentum_30m >= 0.8 else (2.0 if momentum_30m >= 0.3 else (-6.0 if momentum_30m <= -0.8 else 0.0))
    s += -4.0 if momentum_15m <= -0.5 else (2.0 if momentum_15m >= 0.4 else 0.0)
    s += 4.0 if volume_concentration == "high" else (-4.0 if volume_concentration == "low" else 0.0)
    s += 3.0 if trend_short == "up" else (-3.0 if trend_short == "down" else 0.0)
    s += 2.0 if trend_mid == "up" else (-2.0 if trend_mid == "down" else 0.0)
    return s


def _score_vol_price_quality(
    vol_price_corr: float,
    effort_vs_result: float,
    smart_money: float,
) -> float:
    s = 0.0
    if vol_price_corr > 0.3:
        s += 8.0
    elif vol_price_corr > 0.1:
        s += 4.0
    elif vol_price_corr < -0.3:
        s -= 8.0
    elif vol_price_corr < -0.1:
        s -= 4.0
    if effort_vs_result > 30:
        s += 6.0
    elif effort_vs_result > 10:
        s += 3.0
    elif effort_vs_result < -30:
        s -= 6.0
    elif effort_vs_result < -10:
        s -= 3.0
    if smart_money > 1.0:
        s += 5.0
    elif smart_money > 0.3:
        s += 2.0
    elif smart_money < -1.0:
        s -= 5.0
    elif smart_money < -0.3:
        s -= 2.0
    return s


def _compute_strength_score(
    vwap_pos: float,
    close_pos: float,
    momentum_30m: float,
    momentum_15m: float,
    volume_concentration: str,
    trend_short: str,
    trend_mid: str,
    vol_price_corr: float = 0.0,
    effort_vs_result: float = 0.0,
    smart_money_score: float = 0.0,
) -> float:
    score = 50.0
    score += _score_price_and_trend(
        vwap_pos,
        close_pos,
        momentum_30m,
        momentum_15m,
        volume_concentration,
        trend_short,
        trend_mid,
    )
    score += _score_vol_price_quality(vol_price_corr, effort_vs_result, smart_money_score)
    return max(0.0, min(100.0, score))


def _build_profile(bars: int, feat: dict[str, Any], **kwargs: Any) -> IntradayProfile:
    return IntradayProfile(
        bars=bars,
        last_close=float(feat["last_close"]),
        vwap=float(round(feat["vwap"], 3)),
        vwap_pos=float(round(feat["vwap_pos"], 3)),
        close_pos=float(round(feat["close_pos"], 3)),
        trend_short=kwargs["trend_short"],
        trend_mid=kwargs["trend_mid"],
        momentum_30m=float(round(feat["momentum_30m"], 3)),
        momentum_15m=float(round(feat["momentum_15m"], 3)),
        volume_concentration=kwargs["vol_conc"],
        vol_price_corr=float(kwargs["vpc"]),
        effort_vs_result=float(kwargs["evr"]),
        smart_money_score=float(kwargs["sms"]),
        spring_quality=float(round(kwargs["spring_q"], 1)) if kwargs["spring_q"] is not None else None,
        strength_score=float(round(kwargs["strength"], 1)),
    )


def _empty_profile(bars: int = 0) -> IntradayProfile:
    return IntradayProfile(
        bars=bars,
        last_close=0.0,
        vwap=0.0,
        vwap_pos=0.0,
        close_pos=0.0,
        trend_short="flat",
        trend_mid="flat",
        momentum_30m=0.0,
        momentum_15m=0.0,
        volume_concentration="even",
        vol_price_corr=0.0,
        effort_vs_result=0.0,
        smart_money_score=0.0,
        spring_quality=None,
        strength_score=0.0,
    )


def _compute_price_features(df: pd.DataFrame) -> dict[str, Any]:
    close = df["close"].ffill()
    volume = df["volume"].fillna(0.0)
    amount = df["amount"].fillna(close * volume)
    high = df["high"].fillna(close)
    low = df["low"].fillna(close)
    last_close = _safe_float(close.iloc[-1])
    day_high = _safe_float(high.max(), last_close)
    day_low = _safe_float(low.min(), last_close)
    day_range = max(day_high - day_low, 1e-8)
    close_pos = max(0.0, min(1.0, (last_close - day_low) / day_range))
    total_volume = float(volume.sum())
    total_amount = float(amount.sum())
    vwap, _ = infer_session_vwap(close, total_volume, total_amount)
    vwap_pos = (last_close / vwap - 1.0) * 100.0 if vwap > 0 else 0.0
    return {
        "last_close": last_close,
        "vwap": vwap,
        "vwap_pos": vwap_pos,
        "close_pos": close_pos,
        "close": close,
        "momentum_30m": _ret_pct(close, 30),
        "momentum_15m": _ret_pct(close, 15),
    }


def analyze_intraday(
    df_1m: pd.DataFrame,
    df_5m: pd.DataFrame | None = None,
    df_15m: pd.DataFrame | None = None,
    daily_context: dict | None = None,
) -> IntradayProfile:
    df = ensure_intraday_df(df_1m)
    if df.empty or len(df) < 10:
        return _empty_profile(len(df))

    feat = _compute_price_features(df)
    df_5 = ensure_intraday_df(df_5m) if df_5m is not None else pd.DataFrame()
    df_15 = ensure_intraday_df(df_15m) if df_15m is not None else pd.DataFrame()
    trend_short = _compute_trend(df_5) if not df_5.empty else _compute_trend(df)
    trend_mid = _compute_trend(df_15) if not df_15.empty else "flat"
    vol_conc = _compute_volume_concentration(df)
    vpc = compute_vol_price_corr(df)
    evr = compute_effort_vs_result(df)
    sms = compute_smart_money_score(df)

    spring_q = (
        compute_spring_quality(df, daily_context) if daily_context and daily_context.get("support_level") else None
    )
    strength = _compute_strength_score(
        feat["vwap_pos"],
        feat["close_pos"],
        feat["momentum_30m"],
        feat["momentum_15m"],
        vol_conc,
        trend_short,
        trend_mid,
        vpc,
        evr,
        sms,
    )
    return _build_profile(
        len(df),
        feat,
        trend_short=trend_short,
        trend_mid=trend_mid,
        vol_conc=vol_conc,
        vpc=vpc,
        evr=evr,
        sms=sms,
        spring_q=spring_q,
        strength=strength,
    )


# ---------------------------------------------------------------------------
# 60m 结构救援分析（战略旁路用）
# ---------------------------------------------------------------------------


@dataclass
class IntradayRescueResult:
    rescue_score: float
    rescue_reasons: list[str]
    vol_price_corr: float
    trend_strength: float
    breakout_confirmed: bool
    vwap_reclaim: bool
    bars_analyzed: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _empty_rescue(bars: int = 0) -> IntradayRescueResult:
    return IntradayRescueResult(
        rescue_score=0.0,
        rescue_reasons=[],
        vol_price_corr=0.0,
        trend_strength=0.0,
        breakout_confirmed=False,
        vwap_reclaim=False,
        bars_analyzed=bars,
    )


def _detect_platform_breakout(df: pd.DataFrame) -> tuple[bool, float]:
    """检测价格突破近期盘整平台。返回 (是否突破, 突破幅度%)。"""
    n = len(df)
    if n < 20:
        return False, 0.0
    split = int(n * 0.7)
    close = df["close"].values.astype(float)
    high = df["high"].fillna(df["close"]).values.astype(float)
    consol_high = float(high[:split].max())
    consol_low = float(df["low"].fillna(df["close"]).values[:split].astype(float).min())
    consol_range = consol_high - consol_low
    if consol_range <= 0:
        return False, 0.0
    last_close = float(close[-1])
    threshold = consol_high * 1.005
    if last_close >= threshold:
        strength = (last_close - consol_high) / consol_high * 100.0
        return True, round(strength, 2)
    return False, 0.0


def _detect_vwap_reclaim(df: pd.DataFrame) -> tuple[bool, float]:
    """检测从 VWAP 下方收复到上方。返回 (是否收复, 偏离%)。"""
    close = df["close"].ffill()
    volume = df["volume"].fillna(0.0)
    amount = df["amount"].fillna(close * volume)
    total_vol = float(volume.sum())
    total_amt = float(amount.sum())
    vwap, _ = infer_session_vwap(close, total_vol, total_amt)
    if vwap <= 0:
        return False, 0.0
    last_close = float(close.iloc[-1])
    half = len(close) // 2
    first_half_below = float(close.iloc[:half].mean()) < vwap
    now_above = last_close > vwap
    if first_half_below and now_above:
        dist = (last_close / vwap - 1.0) * 100.0
        return True, round(dist, 2)
    return False, 0.0


def _detect_trend_establishment(df: pd.DataFrame) -> tuple[str, float]:
    """检测 60m 持续趋势。返回 (方向, 斜率强度)。"""
    n = len(df)
    if n < 12:
        return "flat", 0.0
    close = df["close"].values.astype(float)
    x = np.arange(n, dtype=float)
    slope = float(np.polyfit(x, close, 1)[0])
    mean_price = float(close.mean()) if close.mean() != 0 else 1.0
    pct_slope = slope / mean_price * 100.0
    recent = close[-max(n // 2, 5) :]
    up_bars = sum(1 for i in range(1, len(recent)) if recent[i] > recent[i - 1])
    consistency = up_bars / max(len(recent) - 1, 1)
    if pct_slope > 0.05 and consistency >= 0.55:
        return "up", round(pct_slope, 4)
    if pct_slope < -0.05 and consistency <= 0.45:
        return "down", round(pct_slope, 4)
    return "flat", round(pct_slope, 4)


def _validate_volume_support(df: pd.DataFrame, is_breakout: bool) -> tuple[bool, float]:
    """验证突破区相对盘整区的放量程度。返回 (是否放量, 放量倍数)。"""
    n = len(df)
    if n < 20:
        return False, 1.0
    volume = df["volume"].fillna(0.0).values.astype(float)
    split = int(n * 0.7)
    consol_vol = float(volume[:split].mean()) if split > 0 else 1.0
    breakout_vol = float(volume[split:].mean()) if n > split else consol_vol
    if consol_vol < 1e-9:
        return False, 1.0
    ratio = breakout_vol / consol_vol
    confirmed = is_breakout and ratio >= 1.2
    return confirmed, round(ratio, 2)


def _score_rescue(
    breakout: bool,
    breakout_strength: float,
    vwap_reclaim: bool,
    vwap_dist: float,
    trend_dir: str,
    trend_slope: float,
    vol_confirmed: bool,
    vol_ratio: float,
    vpc: float,
) -> tuple[float, list[str]]:
    """综合打分。无量突破封顶30。"""
    score = 0.0
    reasons: list[str] = []
    if breakout and vol_confirmed:
        score += 30.0
        reasons.append(f"平台突破+放量({vol_ratio:.1f}x, +{breakout_strength:.1f}%)")
    elif breakout:
        score += 10.0
        reasons.append(f"平台突破但量能不足({vol_ratio:.1f}x)")
    if vwap_reclaim:
        score += 20.0
        reasons.append(f"VWAP收复(偏离+{vwap_dist:.1f}%)")
    if trend_dir == "up":
        score += 25.0
        reasons.append(f"60m趋势确立(slope={trend_slope:.3f}%)")
    if vpc > 0.2:
        score += 15.0
        reasons.append(f"量价正相关({vpc:.2f})")
    elif vpc < -0.2:
        score -= 20.0
        reasons.append(f"量价背离({vpc:.2f})")
    if vol_ratio > 1.5 and vol_confirmed:
        score += 10.0
    if breakout and not vol_confirmed:
        score = min(score, 30.0)
        if "无量突破封顶" not in str(reasons):
            reasons.append("无量突破封顶30分")
    return max(0.0, min(100.0, score)), reasons


def analyze_rescue_structure(
    df_60m: pd.DataFrame,
    df_30m: pd.DataFrame | None = None,
) -> IntradayRescueResult:
    """60m 结构救援分析入口。"""
    df = ensure_intraday_df(df_60m)
    if df.empty or len(df) < 16:
        return _empty_rescue(len(df) if not df.empty else 0)
    breakout, breakout_str = _detect_platform_breakout(df)
    vwap_reclaim, vwap_dist = _detect_vwap_reclaim(df)
    trend_dir, trend_slope = _detect_trend_establishment(df)
    vol_confirmed, vol_ratio = _validate_volume_support(df, breakout)
    vpc = compute_vol_price_corr(df)
    score, reasons = _score_rescue(
        breakout,
        breakout_str,
        vwap_reclaim,
        vwap_dist,
        trend_dir,
        trend_slope,
        vol_confirmed,
        vol_ratio,
        vpc,
    )
    if df_30m is not None:
        df_30 = ensure_intraday_df(df_30m)
        if not df_30.empty and len(df_30) >= 8:
            t30_dir, _ = _detect_trend_establishment(df_30)
            if t30_dir == "up" and trend_dir == "up":
                score = min(score + 5.0, 100.0)
                reasons.append("30m趋势共振确认")
    return IntradayRescueResult(
        rescue_score=float(round(score, 1)),
        rescue_reasons=reasons,
        vol_price_corr=float(vpc),
        trend_strength=float(abs(trend_slope)),
        breakout_confirmed=breakout and vol_confirmed,
        vwap_reclaim=vwap_reclaim,
        bars_analyzed=len(df),
    )
