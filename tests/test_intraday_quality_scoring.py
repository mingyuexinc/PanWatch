"""分时质量评估：Baostock + Wyckoff 算分 smoke（需网络）。"""

import pytest

from src.modules.intraday_quality.scoring import compute_strength_score


@pytest.mark.integration
def test_compute_strength_score_603999_20260929():
    score = compute_strength_score("603999", "CN", "2026-09-29")
    assert 0 <= score <= 100
    assert score >= 70
