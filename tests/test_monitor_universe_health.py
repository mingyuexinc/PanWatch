from src.modules.market.monitor_universe_health import (
    MONITOR_HEALTH_DATA_FORMAT,
    MONITOR_HEALTH_DATA_SOURCE,
    MONITOR_HEALTH_ENGINE,
    MONITOR_HEALTH_OK,
    MONITOR_HEALTH_SCAN_ROUND,
    classify_scan_item,
    engine_scan_is_stale,
    resolve_list_health,
    touch_engine_scan_ok,
)


def test_classify_scan_item_mapping():
    assert classify_scan_item(status="no_quote") == MONITOR_HEALTH_DATA_SOURCE
    assert (
        classify_scan_item(status="bad_price", quote={"current_price": None})
        == MONITOR_HEALTH_DATA_FORMAT
    )
    assert (
        classify_scan_item(status="gated", reason="non_trading", quote={"current_price": 1})
        == MONITOR_HEALTH_OK
    )
    assert (
        classify_scan_item(status="gated", reason="disabled", quote={"current_price": 1})
        == MONITOR_HEALTH_SCAN_ROUND
    )
    assert (
        classify_scan_item(status="not_matched", quote={"current_price": 9.5})
        == MONITOR_HEALTH_OK
    )


def test_resolve_list_engine_stale():
    touch_engine_scan_ok()
    assert (
        resolve_list_health(
            monitor_health=MONITOR_HEALTH_OK,
            monitor_health_at=None,
            rule_enabled=True,
            rule_id=1,
            triggered=False,
            price_threshold=10.0,
            engine_stale=False,
        )
        == MONITOR_HEALTH_OK
    )
    assert (
        resolve_list_health(
            monitor_health=MONITOR_HEALTH_OK,
            monitor_health_at=None,
            rule_enabled=True,
            rule_id=1,
            triggered=False,
            price_threshold=10.0,
            engine_stale=True,
        )
        == MONITOR_HEALTH_ENGINE
    )
    assert (
        resolve_list_health(
            monitor_health=None,
            monitor_health_at=None,
            rule_enabled=True,
            rule_id=1,
            triggered=False,
            price_threshold=10.0,
            engine_stale=False,
        )
        == MONITOR_HEALTH_ENGINE
    )


def test_engine_stale_before_first_scan():
    import src.modules.market.monitor_universe_health as h

    h._ENGINE_LAST_OK_MONO = None
    assert engine_scan_is_stale(60) is True
    touch_engine_scan_ok()
    assert engine_scan_is_stale(60) is False