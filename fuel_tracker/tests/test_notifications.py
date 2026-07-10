"""Silnik powiadomień w add-onie (0.9.0) — stany, dedup, anty-flap 24 h."""
from datetime import datetime, timedelta

import pytest

from fuel_tracker import notifications

NOW = datetime(2026, 7, 10, 12, 0, 0)


@pytest.fixture
def sent():
    return []


@pytest.fixture
def notify(sent):
    def _notify(service, title, message):
        sent.append((service, title, message))
        return True
    return _notify


def _settings(**over):
    s = {
        "monthly_fuel_budget": 984.0,
        "notify_service": "notify.mobile_app_op12",
        "alert_budget_enabled": 1,
        "alert_cheap_fuel_enabled": 1,
        "alert_lease_enabled": 1,
        "alert_budget_threshold": 100.0,
        "alert_cheap_fuel_delta": 0.20,
        "alert_lease_km_threshold": 1000,
    }
    s.update(over)
    return s


def _values(**over):
    v = {
        "budget_left_month": 500.0,
        "month_forecast_cost": 900.0,
        "price_vs_region": 0.05,
        "region_fuel_price": 5.80,
        "last_fillup_price": 5.85,
        "lease_km_margin": 5000,
        "projected_annual_km": 20000,
    }
    v.update(over)
    return v


def test_budget_warning_notifies_with_slash_service(conn, notify, sent):
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=80.0), notify, now=NOW)
    assert len(sent) == 1
    service, title, message = sent[0]
    assert service == "notify/mobile_app_op12"
    assert title == "⛽ Budżet paliwowy na wyczerpaniu"
    assert "80.00 PLN" in message


def test_same_state_does_not_renotify(conn, notify, sent):
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=80.0), notify, now=NOW)
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=70.0), notify,
                           now=NOW + timedelta(minutes=15))
    assert len(sent) == 1


def test_escalation_warning_to_exceeded_notifies_again(conn, notify, sent):
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=80.0), notify, now=NOW)
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=-50.0), notify,
                           now=NOW + timedelta(hours=1))
    assert len(sent) == 2
    assert sent[1][1] == "⛽ Budżet paliwowy przekroczony"
    assert "50.00 PLN" in sent[1][2]


def test_deescalation_is_silent(conn, notify, sent):
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=-50.0), notify, now=NOW)
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=80.0), notify,
                           now=NOW + timedelta(hours=1))
    assert len(sent) == 1


def test_return_to_ok_is_silent_and_rearms(conn, notify, sent):
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=80.0), notify, now=NOW)
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=500.0), notify,
                           now=NOW + timedelta(hours=1))
    assert len(sent) == 1  # powrót do ok bez "all clear"


def test_antiflap_suppresses_recross_within_24h(conn, notify, sent):
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=80.0), notify, now=NOW)
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=500.0), notify,
                           now=NOW + timedelta(hours=1))
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=90.0), notify,
                           now=NOW + timedelta(hours=2))
    assert len(sent) == 1  # ponowne przekroczenie <24 h — cicho


def test_antiflap_allows_recross_after_24h(conn, notify, sent):
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=80.0), notify, now=NOW)
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=500.0), notify,
                           now=NOW + timedelta(hours=1))
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=90.0), notify,
                           now=NOW + timedelta(hours=25))
    assert len(sent) == 2


def test_disabled_alert_is_silent(conn, notify, sent):
    notifications.evaluate(conn, _settings(alert_budget_enabled=0),
                           _values(budget_left_month=-100.0), notify, now=NOW)
    assert sent == []


def test_zero_budget_is_silent(conn, notify, sent):
    notifications.evaluate(conn, _settings(monthly_fuel_budget=0.0),
                           _values(budget_left_month=None), notify, now=NOW)
    assert sent == []


def test_cheap_fuel_notifies_at_delta(conn, notify, sent):
    notifications.evaluate(conn, _settings(),
                           _values(price_vs_region=0.25), notify, now=NOW)
    assert len(sent) == 1
    assert sent[0][1] == "⛽ Tanie paliwo w regionie"
    assert "5.80 PLN/L" in sent[0][2]


def test_cheap_fuel_below_delta_resets_state(conn, notify, sent):
    notifications.evaluate(conn, _settings(),
                           _values(price_vs_region=0.25), notify, now=NOW)
    notifications.evaluate(conn, _settings(),
                           _values(price_vs_region=0.05), notify,
                           now=NOW + timedelta(hours=1))
    notifications.evaluate(conn, _settings(),
                           _values(price_vs_region=0.30), notify,
                           now=NOW + timedelta(hours=26))
    assert len(sent) == 2


def test_lease_warning_and_exceeded(conn, notify, sent):
    notifications.evaluate(conn, _settings(),
                           _values(lease_km_margin=500), notify, now=NOW)
    assert sent[-1][1] == "🚗 Zapas km leasingu topnieje"
    notifications.evaluate(conn, _settings(),
                           _values(lease_km_margin=-200), notify,
                           now=NOW + timedelta(hours=1))
    assert sent[-1][1] == "🚗 Limit km leasingu przekroczony"
    assert "200 km" in sent[-1][2]


def test_lease_margin_none_is_silent(conn, notify, sent):
    notifications.evaluate(conn, _settings(),
                           _values(lease_km_margin=None), notify, now=NOW)
    assert sent == []


def test_empty_notify_service_skips_without_crash(conn, notify, sent):
    notifications.evaluate(conn, _settings(notify_service=""),
                           _values(budget_left_month=80.0), notify, now=NOW)
    assert sent == []
    # Stan zapisany — po skonfigurowaniu usługi brak wstecznego spamu
    row = conn.execute(
        "SELECT state FROM alert_state WHERE alert = 'budget'").fetchone()
    assert row["state"] == "warning"


def test_failed_send_retries_next_tick(conn, sent):
    def failing_notify(service, title, message):
        sent.append((service, title, message))
        return False
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=80.0), failing_notify,
                           now=NOW)
    # Stan nieutrwalony — następny tick ponawia
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=80.0), failing_notify,
                           now=NOW + timedelta(minutes=15))
    assert len(sent) == 2


def test_migration_v7_creates_alert_state_and_drops_legacy_keys(tmp_path):
    from fuel_tracker import db as dbm
    c = dbm.get_conn(str(tmp_path / "m.db"))
    dbm.migrate(c)
    # Tabela alert_state istnieje i przyjmuje wpisy
    c.execute("INSERT INTO alert_state (alert, state) VALUES ('budget', 'ok')")
    # Stare klucze automatyzacji nie wracają (usunięte w v7)
    rows = c.execute("SELECT key FROM settings WHERE key LIKE "
                     "'alert_%_automation'").fetchall()
    assert rows == []
    c.close()
