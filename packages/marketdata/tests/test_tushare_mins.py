from marketdata.vendors.tushare_mins import fetch_tushare_1m, symbol_to_ts_code


def test_symbol_to_ts_code():
    assert symbol_to_ts_code("002437") == "002437.SZ"
    assert symbol_to_ts_code("600519") == "600519.SH"


def test_fetch_tushare_1m_parses_rows(monkeypatch):
    def fake_raw(ts_code, **kw):
        return [
            {
                "trade_time": "2026-10-30 09:31:00",
                "open": 1.0,
                "close": 1.1,
                "high": 1.2,
                "low": 0.9,
                "vol": 100,
            }
        ], None

    monkeypatch.setattr("marketdata.vendors.tushare_mins.tushare_stk_mins_raw", fake_raw)
    monkeypatch.setattr("marketdata.vendors.tushare_mins.resolve_tushare_token", lambda: "x")
    bars = fetch_tushare_1m("002437", "2026-10-30")
    assert len(bars) == 1
    assert bars[0]["close"] == 1.1
