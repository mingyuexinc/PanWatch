"""Safe type coercion and dict-cleanup helpers shared across core/agents/tools/integrations."""

from __future__ import annotations

import math
from typing import Any


def safe_float(value: Any, default: float | None = 0.0) -> float | None:
    """Best-effort float conversion; returns `default` for non-numeric or NaN/inf input."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def finite_float(value: Any) -> float | None:
    """Float conversion returning ``None`` for non-numeric, NaN, or inf input."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def parse_cn_num(raw: Any) -> float | None:
    """Parse a numeric string with optional Chinese suffixes (亿/万) and sentinels.

    Handles ``None``, booleans, sentinel strings (``"-"``, ``"--"``, ``"nan"``,
    ``"none"``), comma separators, and Chinese magnitude suffixes.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        value = float(raw)
        return value if math.isfinite(value) else None
    text = str(raw).strip().replace(",", "")
    if text.lower() in {"", "-", "--", "nan", "none"}:
        return None
    multiplier = 1.0
    if "亿" in text:
        multiplier = 100_000_000.0
    elif "万" in text:
        multiplier = 10_000.0
    text = text.replace("%", "").replace("亿", "").replace("万", "")
    try:
        value = float(text) * multiplier
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def has_value(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def drop_empty(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if has_value(value)}
