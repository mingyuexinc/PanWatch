import marketdata.vendors.eastmoney_relay as relay
from marketdata.vendors.eastmoney_hosts import EM_KLINE_PATH


def test_relay_disabled_on_server(monkeypatch):
    monkeypatch.setenv("PANWATCH_EM_RELAY_URL", "http://relay.test:8765")
    monkeypatch.setenv("PANWATCH_EM_RELAY_SERVER", "1")
    assert relay.relay_enabled() is False


def test_em_fetch_via_relay_builds_request(monkeypatch):
    monkeypatch.delenv("PANWATCH_EM_RELAY_SERVER", raising=False)
    monkeypatch.setenv("PANWATCH_EM_RELAY_URL", "http://relay.test:8765")
    monkeypatch.setenv("PANWATCH_EM_RELAY_TOKEN", "t")
    captured: dict = {}

    def fake_get(url, **kw):
        captured["url"] = url
        captured["headers"] = kw.get("headers")
        captured["params"] = kw.get("params")
        return {"rc": 0, "data": {"klines": ["2026-10-30 09:31,1,1,1,1,1"]}}

    monkeypatch.setattr(relay, "market_get", fake_get)
    out = relay.em_fetch_via_relay(
        EM_KLINE_PATH,
        {"secid": "0.002437", "beg": "20261030", "end": "20261030", "klt": "1"},
    )
    assert captured["url"] == "http://relay.test:8765/v1/kline/1m"
    assert captured["headers"]["Authorization"] == "Bearer t"
    assert captured["params"]["secid"] == "0.002437"
    assert out and out["data"]["klines"]
