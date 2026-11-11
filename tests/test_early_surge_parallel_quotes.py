"""并行 batch 报价拉取的回归与副作用探针（不触网）。"""
from __future__ import annotations

from unittest.mock import patch

from src.modules.market.early_surge_monitor.quotes import fetch_quote_snapshots


def _line_for_code(code: str) -> str:
    parts = ["0"] * 50
    parts[2] = code
    parts[3] = "10.00"
    parts[4] = "9.00"
    parts[5] = "10.00"
    parts[30] = "20261103093000"
    return f'v_{code}="' + "~".join(parts) + '";'


def _fake_content_for_chunk(chunk: list[str]) -> bytes:
    body = ";".join(_line_for_code(c) for c in chunk)
    return body.encode("gbk")


def test_parallel_matches_serial_symbol_set():
    codes = [f"{i:06d}" for i in range(1, 125)]
    call_idx = [0]

    def fake_get(_url, **_kwargs):
        i = call_idx[0]
        call_idx[0] += 1
        chunk = codes[i * 60 : (i + 1) * 60]
        return _fake_content_for_chunk(chunk)

    with patch("src.modules.market.early_surge_monitor.quotes.market_get", side_effect=fake_get):
        serial = fetch_quote_snapshots(codes, batch_size=60, max_parallel_batches=1)
    call_idx[0] = 0
    with patch("src.modules.market.early_surge_monitor.quotes.market_get", side_effect=fake_get):
        parallel = fetch_quote_snapshots(codes, batch_size=60, max_parallel_batches=3)

    assert len(serial) == 124
    assert len(parallel) == 124
    assert {r[0] for r in serial} == {r[0] for r in parallel} == set(codes)


def test_parallel_one_chunk_empty_partial_ok():
    codes = [f"{i:06d}" for i in range(1, 121)]
    call_idx = [0]

    def fake_get(_url, **_kwargs):
        i = call_idx[0]
        call_idx[0] += 1
        if i == 1:
            return None
        chunk = codes[i * 60 : (i + 1) * 60]
        return _fake_content_for_chunk(chunk)

    with patch("src.modules.market.early_surge_monitor.quotes.market_get", side_effect=fake_get):
        rows = fetch_quote_snapshots(codes, batch_size=60, max_parallel_batches=2)

    assert 60 <= len(rows) < 120


def test_parallel_batch_count_same_as_serial():
    codes = [f"{i:06d}" for i in range(1, 181)]
    call_idx = [0]

    def fake_get(_url, **_kwargs):
        i = call_idx[0]
        call_idx[0] += 1
        chunk = codes[i * 60 : (i + 1) * 60]
        return _fake_content_for_chunk(chunk)

    with patch("src.modules.market.early_surge_monitor.quotes.market_get", side_effect=fake_get) as mg:
        fetch_quote_snapshots(codes, batch_size=60, max_parallel_batches=1)
        serial_calls = mg.call_count

    call_idx[0] = 0
    with patch("src.modules.market.early_surge_monitor.quotes.market_get", side_effect=fake_get) as mg:
        fetch_quote_snapshots(codes, batch_size=60, max_parallel_batches=4)
        parallel_calls = mg.call_count

    assert serial_calls == 3 == parallel_calls


def test_max_parallel_default_is_serial_single_batch():
    codes = ["600519", "000001"]

    with patch("src.modules.market.early_surge_monitor.quotes.market_get") as mg:
        mg.return_value = _fake_content_for_chunk(["600519", "000001"])
        fetch_quote_snapshots(codes)
        assert mg.call_count == 1
