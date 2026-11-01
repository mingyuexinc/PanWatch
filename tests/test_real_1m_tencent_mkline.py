from marketdata.vendors import real_1m


def _payload(rows):
    return {"data": {"sz002437": {"m1": rows}}}


def test_mkline_parses_ohlc_and_filters_trade_date(monkeypatch):
    rows = [
        ["202610290931", "9", "9", "9", "9", "1.00", {}, "1"],  # 其它交易日，应被过滤
        ["202610300930", "2.85", "2.85", "2.85", "2.85", "5913.00", {}, "2.81"],
        ["202610300931", "2.84", "2.83", "2.85", "2.83", "32996.00", {}, "15.68"],
    ]
    captured = {}

    def fake_get(url, **kw):
        captured["params"] = kw.get("params")
        return _payload(rows)

    monkeypatch.setattr(real_1m, "market_get", fake_get)
    bars = real_1m.fetch_tencent_mkline_1m("002437", "2026-10-30")

    assert captured["params"]["param"] == "sz002437,m1,202610301500,640"
    assert [b["datetime"].strftime("%H:%M") for b in bars] == ["09:30", "09:31"]
    b = bars[1]
    # 腾讯行序: 时间, 开, 收, 高, 低, 量
    assert (b["open"], b["close"], b["high"], b["low"], b["volume"]) == (2.84, 2.83, 2.85, 2.83, 32996.0)


def test_mkline_returns_empty_outside_window(monkeypatch):
    monkeypatch.setattr(real_1m, "market_get", lambda *a, **k: _payload([]))
    assert real_1m.fetch_tencent_mkline_1m("002437", "2026-01-05") == []


def test_fetch_real_1m_prefers_mkline_and_skips_eastmoney(monkeypatch):
    bar = {"datetime": None, "open": 1, "close": 1, "high": 1, "low": 1, "volume": 1}
    monkeypatch.setattr(real_1m, "fetch_tencent_mkline_1m", lambda s, d: [bar])

    def boom(*a, **k):
        raise AssertionError("东财不应被调用")

    monkeypatch.setattr(real_1m, "fetch_eastmoney_trends2_1m", boom)
    bars, vendor = real_1m.fetch_real_1m("002437", "2026-10-30")
    assert vendor == "tencent_mkline" and bars == [bar]


def test_is_real_1m_vendor_includes_mkline():
    assert real_1m.is_real_1m_vendor("tencent_mkline")
    assert not real_1m.is_real_1m_vendor("baostock_derived_1m")
