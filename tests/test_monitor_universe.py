"""监控池(看板股票集合)服务与规则同步行为。

- 增删/监控条件修改即时落库(自动保存),无独立保存动作;
- 池内股票自动生成监控模板规则(仅首次捕获/交易时段),
  信号共 6 种: 上穿(现价>=均线)/下穿(现价<均线) × 5/10/20日均线;
- 信号变更会重写规则条件并强制重臂(旧信号当日触发不阻塞新信号);
- 每日重臂只清除"非当日"的触发痕迹:昨日触发的规则恢复,
  今日已触发的保持停用,避免当日重复捕获;
- 移出监控池的模板规则连同命中历史被回收;用户自建的非模板 ma 规则不受影响。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.market import monitor_universe_service as svc
from src.platform.persistence.database import Base
from src.platform.persistence.models import MonitorUniverseItem, PriceAlertHit, PriceAlertRule


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def _add(db, symbol="002437", market="CN", name="誉衡药业"):
    return svc.add_to_universe(db, symbol=symbol, market=market, name=name)


def _rules(db, stock_id):
    return (
        db.query(PriceAlertRule)
        .filter(PriceAlertRule.stock_id == stock_id)
        .all()
    )


def test_signal_catalog_covers_six_signals():
    assert list(svc.MONITOR_SIGNALS) == [
        "above_5", "above_10", "above_20", "below_5", "below_10", "below_20",
    ]
    assert svc.MONITOR_SIGNALS["above_5"] == ("above", 5, ">=")
    assert svc.MONITOR_SIGNALS["below_20"] == ("below", 20, "<")


def test_add_creates_stock_item_and_template_rule(db):
    item, created = _add(db)
    assert created is True
    rules = _rules(db, item.stock_id)
    assert len(rules) == 1
    rule = rules[0]
    # 默认信号: 上穿5日均线(现价>=当日5日线)
    assert rule.name == "MA5上穿监控·誉衡药业(002437)"
    assert svc.is_monitor_rule(rule) is True
    assert rule.enabled is True
    assert rule.repeat_mode == "once"
    assert rule.max_triggers_per_day == 1
    assert rule.market_hours_mode == "trading_only"
    assert rule.condition_group == {
        "op": "and",
        "items": [{"type": "ma", "op": ">=", "value": 5}],
    }
    assert item.monitor_signal == "above_5"


def test_add_is_idempotent_and_reuses_existing_stock(db):
    _add(db)
    item2, created2 = _add(db)
    assert created2 is False
    assert db.query(MonitorUniverseItem).count() == 1
    assert db.query(PriceAlertRule).count() == 1


def test_list_universe_reports_signal_catalog_and_default(db):
    _add(db, symbol="600519", market="CN", name="贵州茅台")
    payload = svc.list_universe(db)
    assert payload["total"] == 1
    row = payload["items"][0]
    assert row["symbol"] == "600519"
    assert row["monitor_signal"] == "above_5"
    assert row["rule_id"] is not None
    assert row["rule_enabled"] is True
    assert row["rule_last_hit_notify_success"] is None
    assert row["rule_last_hit_notify_error"] == ""
    assert payload["signals"] == [
        "above_5", "above_10", "above_20", "below_5", "below_10", "below_20",
    ]
    assert payload["saved_at"] is not None


def test_list_universe_exposes_latest_hit_notify_result(db):
    item, _ = _add(db)
    rule = _rules(db, item.stock_id)[0]
    db.add(
        PriceAlertHit(
            rule_id=rule.id,
            stock_id=item.stock_id,
            trigger_time=datetime.now(timezone.utc) - timedelta(days=1),
            trigger_bucket="202609300935",
            trigger_snapshot={},
            notify_success=True,
        )
    )
    db.add(
        PriceAlertHit(
            rule_id=rule.id,
            stock_id=item.stock_id,
            trigger_time=datetime.now(timezone.utc),
            trigger_bucket="202610080135",
            trigger_snapshot={},
            notify_success=False,
            notify_error="没有可用的通知渠道",
        )
    )
    db.commit()

    row = svc.list_universe(db)["items"][0]
    assert row["rule_last_hit_notify_success"] is False
    assert row["rule_last_hit_notify_error"] == "没有可用的通知渠道"


def test_update_signal_rewrites_rule_condition_and_name(db):
    item, _ = _add(db)
    updated = svc.update_item_signal(db, item.id, "below_10")
    assert updated is not None
    assert updated.monitor_signal == "below_10"

    rule = _rules(db, item.stock_id)[0]
    # 下穿=现价严格低于均线(不含等于)
    assert rule.condition_group == {
        "op": "and",
        "items": [{"type": "ma", "op": "<", "value": 10}],
    }
    assert rule.name == "MA10下穿监控·誉衡药业(002437)"
    assert svc.is_monitor_rule(rule) is True
    assert rule.enabled is True

    row = svc.list_universe(db)["items"][0]
    assert row["monitor_signal"] == "below_10"


def test_update_signal_rearms_even_if_triggered_today(db):
    item, _ = _add(db)
    rule = _rules(db, item.stock_id)[0]
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    rule.enabled = False
    rule.last_trigger_at = now_naive
    rule.last_trigger_price = 3.5
    rule.trigger_count_today = 1
    rule.trigger_date = now_naive.strftime("%Y-%m-%d")
    db.commit()

    svc.update_item_signal(db, item.id, "below_5")
    # 信号已换新,旧信号当日的触发痕迹不应阻塞新信号
    assert rule.condition_group["items"][0] == {"type": "ma", "op": "<", "value": 5}
    assert rule.enabled is True
    assert rule.last_trigger_at is None
    assert rule.last_trigger_price is None
    assert rule.trigger_count_today == 0


def test_update_signal_validates_input_and_missing_item(db):
    item, _ = _add(db)
    with pytest.raises(ValueError):
        svc.update_item_signal(db, item.id, "above_30")
    with pytest.raises(ValueError):
        svc.update_item_signal(db, item.id, "")
    assert svc.update_item_signal(db, 99999, "above_5") is None
    # 非法请求不改变现有信号
    assert svc.list_universe(db)["items"][0]["monitor_signal"] == "above_5"


def test_remove_recycles_template_rule_and_hits(db):
    item, _ = _add(db)
    rule = _rules(db, item.stock_id)[0]
    db.add(
        PriceAlertHit(
            rule_id=rule.id,
            stock_id=item.stock_id,
            trigger_time=datetime.now(timezone.utc),
            trigger_bucket="202609300934",
            trigger_snapshot={},
        )
    )
    db.commit()

    removed = svc.remove_from_universe(db, [item.id])
    assert removed == 1
    assert db.query(MonitorUniverseItem).count() == 0
    assert db.query(PriceAlertRule).count() == 0
    assert db.query(PriceAlertHit).count() == 0


def test_batch_remove_removes_only_requested_items(db):
    item_a, _ = _add(db, symbol="600519", market="CN", name="贵州茅台")
    item_b, _ = _add(db, symbol="000001", market="CN", name="平安银行")
    removed = svc.remove_from_universe(db, [item_a.id])
    assert removed == 1
    remaining = svc.list_universe(db)
    assert remaining["total"] == 1
    assert remaining["items"][0]["symbol"] == "000001"
    assert _rules(db, item_b.stock_id), "未移除的股票应保留监控规则"


def test_sync_rearms_yesterday_triggered_rule(db):
    item, _ = _add(db)
    rule = _rules(db, item.stock_id)[0]
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    rule.enabled = False
    rule.last_trigger_at = yesterday.replace(tzinfo=None)
    db.commit()

    result = svc.sync_monitor_rules(db)
    assert result["rearmed"] == 1
    assert rule.enabled is True
    assert rule.last_trigger_at is None


def test_sync_keeps_today_triggered_rule_disabled(db):
    item, _ = _add(db)
    rule = _rules(db, item.stock_id)[0]
    today = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    rule.enabled = False
    rule.last_trigger_at = today
    db.commit()

    result = svc.sync_monitor_rules(db)
    assert result["rearmed"] == 0
    assert rule.enabled is False, "当日已触发(仅首次)的规则不得被重臂"
    assert rule.last_trigger_at == today


def test_sync_adopts_and_renames_legacy_ma5_rule(db):
    """历史遗留的 "MA5监控·" 规则(条件同为上穿5日线)应被识别并改为新命名,不重臂。"""
    item, _ = _add(db)
    rule = _rules(db, item.stock_id)[0]
    rule.name = "MA5监控·誉衡药业(002437)"
    db.commit()

    result = svc.sync_monitor_rules(db)
    assert result == {"created": 0, "rearmed": 0, "recycled": 0}
    assert rule.name == "MA5上穿监控·誉衡药业(002437)"
    assert svc.is_monitor_rule(rule) is True


def test_sync_rewrites_drifted_rule_condition(db):
    """规则条件与条目信号漂移时(如手工改库),同步负责对齐并强制重臂。"""
    item, _ = _add(db)
    rule = _rules(db, item.stock_id)[0]
    rule.condition_group = {
        "op": "and",
        "items": [{"type": "ma", "op": ">=", "value": 20}],
    }
    rule.enabled = False
    rule.last_trigger_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()

    result = svc.sync_monitor_rules(db)
    assert result["rearmed"] == 1
    assert rule.condition_group["items"][0] == {"type": "ma", "op": ">=", "value": 5}
    assert rule.name == "MA5上穿监控·誉衡药业(002437)"
    assert rule.enabled is True
    assert rule.last_trigger_at is None


def test_sync_does_not_touch_user_created_ma_rules(db):
    item, _ = _add(db)
    user_rule = PriceAlertRule(
        stock_id=item.stock_id,
        name="我的10日线观察",
        enabled=True,
        condition_group={"op": "and", "items": [{"type": "ma", "op": "<=", "value": 10}]},
        repeat_mode="repeat",
    )
    db.add(user_rule)
    db.commit()

    svc.remove_from_universe(db, [item.id])
    assert db.query(PriceAlertRule).count() == 1
    assert db.query(PriceAlertRule).first().id == user_rule.id


def test_is_monitor_rule_rejects_non_catalog_periods_and_ops(db):
    item, _ = _add(db)
    stranger = PriceAlertRule(
        stock_id=item.stock_id,
        name="MA8监控·测试(002437)",
        enabled=True,
        condition_group={"op": "and", "items": [{"type": "ma", "op": ">=", "value": 8}]},
    )
    assert svc.is_monitor_rule(stranger) is False

    stranger_ma5_bad_op = PriceAlertRule(
        stock_id=item.stock_id,
        name="MA5监控·测试(002437)",
        enabled=True,
        condition_group={"op": "and", "items": [{"type": "ma", "op": "<=", "value": 5}]},
    )
    assert svc.is_monitor_rule(stranger_ma5_bad_op) is False
