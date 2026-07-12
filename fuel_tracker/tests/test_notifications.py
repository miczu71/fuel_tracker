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


def test_budget_warning_notifies_with_slash_service(conn, vehicle_id, notify, sent):
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=80.0), vehicle_id, notify, now=NOW)
    assert len(sent) == 1
    service, title, message = sent[0]
    assert service == "notify/mobile_app_op12"
    assert title == "⛽ Budżet paliwowy na wyczerpaniu"
    assert "80.00 PLN" in message


def test_same_state_does_not_renotify(conn, vehicle_id, notify, sent):
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=80.0), vehicle_id, notify, now=NOW)
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=70.0), vehicle_id, notify,
                           now=NOW + timedelta(minutes=15))
    assert len(sent) == 1


def test_escalation_warning_to_exceeded_notifies_again(conn, vehicle_id, notify, sent):
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=80.0), vehicle_id, notify, now=NOW)
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=-50.0), vehicle_id, notify,
                           now=NOW + timedelta(hours=1))
    assert len(sent) == 2
    assert sent[1][1] == "⛽ Budżet paliwowy przekroczony"
    assert "50.00 PLN" in sent[1][2]


def test_deescalation_is_silent(conn, vehicle_id, notify, sent):
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=-50.0), vehicle_id, notify, now=NOW)
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=80.0), vehicle_id, notify,
                           now=NOW + timedelta(hours=1))
    assert len(sent) == 1


def test_return_to_ok_is_silent_and_rearms(conn, vehicle_id, notify, sent):
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=80.0), vehicle_id, notify, now=NOW)
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=500.0), vehicle_id, notify,
                           now=NOW + timedelta(hours=1))
    assert len(sent) == 1  # powrót do ok bez "all clear"


def test_antiflap_suppresses_recross_within_24h(conn, vehicle_id, notify, sent):
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=80.0), vehicle_id, notify, now=NOW)
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=500.0), vehicle_id, notify,
                           now=NOW + timedelta(hours=1))
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=90.0), vehicle_id, notify,
                           now=NOW + timedelta(hours=2))
    assert len(sent) == 1  # ponowne przekroczenie <24 h — cicho


def test_antiflap_allows_recross_after_24h(conn, vehicle_id, notify, sent):
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=80.0), vehicle_id, notify, now=NOW)
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=500.0), vehicle_id, notify,
                           now=NOW + timedelta(hours=1))
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=90.0), vehicle_id, notify,
                           now=NOW + timedelta(hours=25))
    assert len(sent) == 2


def test_disabled_alert_is_silent(conn, vehicle_id, notify, sent):
    notifications.evaluate(conn, _settings(alert_budget_enabled=0),
                           _values(budget_left_month=-100.0), vehicle_id, notify, now=NOW)
    assert sent == []


def test_zero_budget_is_silent(conn, vehicle_id, notify, sent):
    notifications.evaluate(conn, _settings(monthly_fuel_budget=0.0),
                           _values(budget_left_month=None), vehicle_id, notify, now=NOW)
    assert sent == []


def test_cheap_fuel_notifies_at_delta(conn, vehicle_id, notify, sent):
    notifications.evaluate(conn, _settings(),
                           _values(price_vs_region=0.25), vehicle_id, notify, now=NOW)
    assert len(sent) == 1
    assert sent[0][1] == "⛽ Tanie paliwo w regionie"
    assert "5.80 PLN/L" in sent[0][2]


def test_cheap_fuel_below_delta_resets_state(conn, vehicle_id, notify, sent):
    notifications.evaluate(conn, _settings(),
                           _values(price_vs_region=0.25), vehicle_id, notify, now=NOW)
    notifications.evaluate(conn, _settings(),
                           _values(price_vs_region=0.05), vehicle_id, notify,
                           now=NOW + timedelta(hours=1))
    notifications.evaluate(conn, _settings(),
                           _values(price_vs_region=0.30), vehicle_id, notify,
                           now=NOW + timedelta(hours=26))
    assert len(sent) == 2


def test_lease_warning_and_exceeded(conn, vehicle_id, notify, sent):
    notifications.evaluate(conn, _settings(),
                           _values(lease_km_margin=500), vehicle_id, notify, now=NOW)
    assert sent[-1][1] == "🚗 Zapas km leasingu topnieje"
    notifications.evaluate(conn, _settings(),
                           _values(lease_km_margin=-200), vehicle_id, notify,
                           now=NOW + timedelta(hours=1))
    assert sent[-1][1] == "🚗 Limit km leasingu przekroczony"
    assert "200 km" in sent[-1][2]


def test_lease_margin_none_is_silent(conn, vehicle_id, notify, sent):
    notifications.evaluate(conn, _settings(),
                           _values(lease_km_margin=None), vehicle_id, notify, now=NOW)
    assert sent == []


def test_empty_notify_service_skips_without_crash(conn, vehicle_id, notify, sent):
    notifications.evaluate(conn, _settings(notify_service=""),
                           _values(budget_left_month=80.0), vehicle_id, notify, now=NOW)
    assert sent == []
    # Stan zapisany — po skonfigurowaniu usługi brak wstecznego spamu
    row = conn.execute(
        "SELECT state FROM alert_state WHERE alert = 'budget' AND "
        "vehicle_id = ?", (vehicle_id,)).fetchone()
    assert row["state"] == "warning"


def test_failed_send_retries_next_tick(conn, vehicle_id, sent):
    def failing_notify(service, title, message):
        sent.append((service, title, message))
        return False
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=80.0), vehicle_id,
                           failing_notify, now=NOW)
    # Stan nieutrwalony — następny tick ponawia
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=80.0), vehicle_id,
                           failing_notify, now=NOW + timedelta(minutes=15))
    assert len(sent) == 2


def test_different_vehicles_do_not_share_alert_state(conn, notify, sent):
    """0.11.0: dwa auta ewaluowane niezależnie — stan jednego nie wycisza
    powiadomienia drugiego (migracja #8 rozdzieliła alert_state per auto)."""
    from fuel_tracker import db as dbm
    v1 = dbm.ensure_vehicle(conn, "A", 50.0, "PB95")
    v2 = dbm.create_vehicle(conn, "B", 50.0, "PB95")
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=80.0), v1, notify, now=NOW)
    notifications.evaluate(conn, _settings(),
                           _values(budget_left_month=80.0), v2, notify, now=NOW)
    assert len(sent) == 2  # auto B nie widzi stanu "już ostrzeżono" auta A


def test_parallel_evaluate_sends_once(tmp_path, sent):
    """Regresja 0.9.0: równoległe joby schedulera wysyłały alert podwójnie."""
    import threading
    import time
    from fuel_tracker import db as dbm
    path = str(tmp_path / "race.db")
    c = dbm.get_conn(path)
    dbm.migrate(c)
    vid = dbm.ensure_vehicle(c, "Testowy", 66.0, "PB95")
    c.close()

    def slow_notify(service, title, message):
        sent.append(title)
        time.sleep(0.2)  # przytrzymaj blokadę, żeby drugi wątek czekał
        return True

    def run():
        conn = dbm.get_conn(path)
        try:
            notifications.evaluate(conn, _settings(),
                                   _values(budget_left_month=80.0), vid,
                                   slow_notify, now=NOW)
        finally:
            conn.close()

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(sent) == 1


def test_migration_v7_creates_alert_state_and_drops_legacy_keys(tmp_path):
    from fuel_tracker import db as dbm
    c = dbm.get_conn(str(tmp_path / "m.db"))
    dbm.migrate(c)
    # Tabela alert_state istnieje i przyjmuje wpisy (schemat aktualny —
    # v8 dodał vehicle_id, więc trzeba go podać razem z prawdziwym pojazdem).
    vid = dbm.ensure_vehicle(c, "Testowy", 66.0, "PB95")
    c.execute("INSERT INTO alert_state (alert, vehicle_id, state) "
             "VALUES ('budget', ?, 'ok')", (vid,))
    # Stare klucze automatyzacji nie wracają (usunięte w v7)
    rows = c.execute("SELECT key FROM settings WHERE key LIKE "
                     "'alert_%_automation'").fetchall()
    assert rows == []
    c.close()


def _migrate_to(conn, version):
    """Pomocnik testowy: migruje tylko do podanej wersji (symulacja upgrade'u
    ze starszego schematu, żeby przetestować backfill migracji #8)."""
    from fuel_tracker import db as dbm
    for script in dbm._MIGRATIONS[:version]:
        conn.executescript(script)
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()


def test_migration_v8_backfills_alert_state_to_configured_active_vehicle(tmp_path):
    from fuel_tracker import db as dbm
    c = dbm.get_conn(str(tmp_path / "m8.db"))
    _migrate_to(c, 7)
    c.execute("INSERT INTO vehicles (name, tank_capacity_l, fuel_type) "
             "VALUES ('A', 50, 'PB95')")
    c.execute("INSERT INTO vehicles (name, tank_capacity_l, fuel_type) "
             "VALUES ('B', 50, 'PB95')")
    c.execute("INSERT INTO settings (key, value) "
             "VALUES ('active_vehicle_id', '2')")
    c.execute("INSERT INTO alert_state (alert, state) "
             "VALUES ('budget', 'warning')")
    c.commit()
    dbm.migrate(c)
    row = c.execute(
        "SELECT vehicle_id FROM alert_state WHERE alert = 'budget'").fetchone()
    assert row["vehicle_id"] == 2
    c.close()


def test_migration_v8_backfills_to_first_non_archived_when_active_unset(tmp_path):
    from fuel_tracker import db as dbm
    c = dbm.get_conn(str(tmp_path / "m8b.db"))
    _migrate_to(c, 7)
    c.execute("INSERT INTO vehicles (name, tank_capacity_l, fuel_type, "
             "archived) VALUES ('A', 50, 'PB95', 1)")
    c.execute("INSERT INTO vehicles (name, tank_capacity_l, fuel_type, "
             "archived) VALUES ('B', 50, 'PB95', 0)")
    c.execute("INSERT INTO alert_state (alert, state) VALUES ('lease', 'ok')")
    c.commit()
    dbm.migrate(c)
    row = c.execute(
        "SELECT vehicle_id FROM alert_state WHERE alert = 'lease'").fetchone()
    assert row["vehicle_id"] == 2  # pierwszy nie-zarchiwizowany
    c.close()


def test_migration_v8_alert_state_pk_allows_same_alert_per_vehicle(tmp_path):
    from fuel_tracker import db as dbm
    c = dbm.get_conn(str(tmp_path / "m8c.db"))
    dbm.migrate(c)
    v1 = dbm.ensure_vehicle(c, "A", 50, "PB95")
    v2 = dbm.create_vehicle(c, "B", 50, "PB95")
    c.execute("INSERT INTO alert_state (alert, vehicle_id, state) "
             "VALUES ('budget', ?, 'warning')", (v1,))
    c.execute("INSERT INTO alert_state (alert, vehicle_id, state) "
             "VALUES ('budget', ?, 'ok')", (v2,))
    c.commit()
    rows = c.execute(
        "SELECT vehicle_id, state FROM alert_state WHERE alert = 'budget' "
        "ORDER BY vehicle_id").fetchall()
    assert [dict(r) for r in rows] == [
        {"vehicle_id": v1, "state": "warning"}, {"vehicle_id": v2, "state": "ok"}]
    c.close()
