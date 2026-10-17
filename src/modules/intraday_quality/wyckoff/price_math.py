"""Shared price/volume math helpers used across candidate and mainline scoring modules.

These were duplicated verbatim in 5-7 files; centralized here so each formula has one
implementation to keep correct.
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)
DATE_SORTED_ATTR = "_wyckoff_date_sorted"


def sort_by_date_if_needed(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or "date" not in df.columns:
        return df
    if df.attrs.get(DATE_SORTED_ATTR) is True:
        return df
    try:
        if df["date"].is_monotonic_increasing:
            return df
    except Exception:
        logger.debug("Monotonic check failed, falling back to sort", exc_info=True)
    return df.sort_values("date")


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def range_pos(value: float, low: float, high: float) -> float:
    """Position of *value* in [low, high]; returns 0.5 when range is empty."""
    return 0.5 if high <= low else clamp((value - low) / (high - low))


def to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def swing_values(series: pd.Series, *, kind: str, window: int) -> list[float]:
    values = to_numeric(series).reset_index(drop=True)
    width = max(int(window), 1)
    if kind not in {"low", "high"} or len(values) < width * 2 + 1:
        return []
    swings: list[float] = []
    for index in range(width, len(values) - width):
        current = values.iloc[index]
        if pd.isna(current):
            continue
        span = values.iloc[index - width : index + width + 1].dropna()
        boundary = span.min() if kind == "low" else span.max()
        if not span.empty and float(current) == float(boundary):
            swings.append(float(current))
    return swings


def numeric_column(df: pd.DataFrame, column: str, *, dropna: bool = True) -> pd.Series:
    if column not in df.columns:
        return pd.Series(dtype=float)
    series = pd.to_numeric(df[column], errors="coerce")
    return series.dropna() if dropna else series


def ret_pct(close: pd.Series, lookback: int) -> float:
    if len(close) <= lookback:
        return 0.0
    start = float(close.iloc[-lookback - 1])
    return 0.0 if start <= 0 else (float(close.iloc[-1]) / start - 1.0) * 100.0


def dist_pct(value: float, base: float) -> float:
    return 0.0 if base <= 0 else (float(value) / float(base) - 1.0) * 100.0


def drawdown_pct(close: pd.Series, lookback: int) -> float:
    recent = close.tail(max(lookback, 1))
    if recent.empty:
        return 0.0
    high = float(recent.max())
    return 0.0 if high <= 0 else (float(recent.iloc[-1]) / high - 1.0) * -100.0


def upper_shadow_pct(df: pd.DataFrame, open_: pd.Series, high: pd.Series, close: pd.Series) -> float:
    if high.empty or close.empty:
        return 0.0
    base = float(close.iloc[-1])
    body_top = max(base, float(open_.iloc[-1]) if not open_.empty else base)
    return 0.0 if base <= 0 else max(float(high.iloc[-1]) - body_top, 0.0) / base * 100.0


def day_close_pos(close: pd.Series, high: pd.Series, low: pd.Series, *, use_tail: bool = False) -> float:
    if high.empty or low.empty:
        return 0.5
    last_close = float(close.iloc[-1])
    if use_tail:
        lo = float(low.tail(1).min()) if not low.empty else float(close.tail(1).min())
        hi = float(high.tail(1).max()) if not high.empty else float(close.tail(1).max())
    else:
        lo = float(low.iloc[-1])
        hi = float(high.iloc[-1])
    return range_pos(last_close, lo, hi)


def vol_ratio(volume: pd.Series) -> float:
    if len(volume) < 20:
        return 1.0
    base = float(volume.tail(20).mean())
    return 1.0 if base <= 0 else float(volume.tail(5).mean()) / base
