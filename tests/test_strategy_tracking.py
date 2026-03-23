from __future__ import annotations

from tracking import MemoryAlertSink, StrategyConfig, StrategyTrackingService


def test_drawdown_breach_sends_alert_and_stops_strategy():
    alerts = MemoryAlertSink()
    svc = StrategyTrackingService(alert_sink=alerts)
    svc.register_strategy(StrategyConfig(name="lr_alpha", max_drawdown_usd=50.0))

    svc.update_pnl("lr_alpha", +100.0)
    state = svc.update_pnl("lr_alpha", -160.0)

    assert not state.active
    assert state.drawdown_usd > 50.0
    assert any(e["title"] == "Strategy halted" for e in alerts.events)


def test_hourly_update_reports_active_and_resets_hourly_pnl():
    alerts = MemoryAlertSink()
    svc = StrategyTrackingService(alert_sink=alerts)
    svc.register_strategy(StrategyConfig(name="s1", max_drawdown_usd=1000.0))
    svc.register_strategy(StrategyConfig(name="s2", max_drawdown_usd=1000.0))

    svc.update_pnl("s1", 10.0)
    svc.update_pnl("s2", -3.0)

    rows = svc.publish_hourly_update()
    assert len(rows) == 2
    assert {r.strategy for r in rows} == {"s1", "s2"}
    assert any(e["title"] == "Hourly strategy PnL update" for e in alerts.events)

    s1 = svc.get_state("s1")
    s2 = svc.get_state("s2")
    assert s1.hourly_pnl_usd == 0.0
    assert s2.hourly_pnl_usd == 0.0
