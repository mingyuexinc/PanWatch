from marketdata.vendors.eastmoney_hosts import em_kline_params, resolve_em_http_proxy


def test_resolve_em_http_proxy(monkeypatch):
    monkeypatch.setenv("PANWATCH_EM_HTTP_PROXY", "http://u:p@proxy.test:8080")
    assert resolve_em_http_proxy() == "http://u:p@proxy.test:8080"
    monkeypatch.delenv("PANWATCH_EM_HTTP_PROXY", raising=False)
    assert resolve_em_http_proxy() is None


def test_em_kline_params_includes_rtntype_and_fields():
    p = em_kline_params("0.002437", beg="20261030", end="20261030", klt="1")
    assert p["rtntype"] == "6"
    assert p["secid"] == "0.002437"
    assert "f59" in p["fields2"]
    assert "ut" not in p

    legacy = em_kline_params("0.002437", beg="20261030", end="20261030", with_ut=True)
    assert legacy["ut"]
