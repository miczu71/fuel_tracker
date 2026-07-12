"""Pełny multi-vehicle w web.py (0.11.0): przełącznik ?vehicle_id= per
request, rozdzielenie active_vehicle_id (globalny wybór) od
viewing_vehicle_id (auto aktualnie przeglądane na stronie)."""
from unittest.mock import MagicMock

import pytest

from fuel_tracker import db as dbm
from fuel_tracker import publisher as pub
from fuel_tracker.web import create_app


@pytest.fixture
def ctx(tmp_path):
    db_path = str(tmp_path / "multi.db")
    c = dbm.get_conn(db_path)
    dbm.migrate(c)
    active_id = dbm.ensure_vehicle(
        c, "Superb", 66.0, "PB95", odometer_entity="sensor.superb_odo",
        monthly_fuel_budget=984.0)
    other_id = dbm.create_vehicle(
        c, "Mazda", 45.0, "PB95", odometer_entity="sensor.mazda_odo",
        monthly_fuel_budget=500.0)
    archived_id = dbm.create_vehicle(c, "Stary", 50.0, "PB95")
    dbm.archive_vehicle(c, archived_id)
    c.close()

    def ha_state(entity):
        return {
            "sensor.superb_odo": {"state": "10000"},
            "sensor.mazda_odo": {"state": "20000"},
        }.get(entity)

    app = create_app(db_path=db_path, config={}, ha_state=ha_state)
    app.testing = True
    return app.test_client(), active_id, other_id, archived_id


def _add_fillup(client, vehicle_id=None, **kw):
    body = {"date": "2025-01-01T12:00", "odometer": 1000, "volume_l": 40,
            "price_per_l": 6.0, "full_tank": True}
    body.update(kw)
    url = "/api/fillups"
    if vehicle_id is not None:
        url += f"?vehicle_id={vehicle_id}"
    return client.post(url, json=body)


def test_summary_scoped_by_viewing_param(ctx):
    client, active_id, other_id, _ = ctx
    _add_fillup(client, vehicle_id=active_id)
    _add_fillup(client, vehicle_id=other_id, date="2025-02-01T12:00", odometer=500)
    active_summary = client.get("/api/summary").get_json()
    other_summary = client.get(f"/api/summary?vehicle_id={other_id}").get_json()
    assert active_summary["fillup_count"] == 1
    assert active_summary["monthly_budget"] == 984.0
    assert other_summary["fillup_count"] == 1
    assert other_summary["monthly_budget"] == 500.0


def test_fillups_list_scoped_by_viewing_param(ctx):
    client, active_id, other_id, _ = ctx
    _add_fillup(client, vehicle_id=active_id, station="Aktywne")
    _add_fillup(client, vehicle_id=other_id, station="Inne",
               date="2025-02-01T12:00", odometer=500)
    active_rows = client.get("/api/fillups").get_json()
    other_rows = client.get(f"/api/fillups?vehicle_id={other_id}").get_json()
    assert [r["station"] for r in active_rows] == ["Aktywne"]
    assert [r["station"] for r in other_rows] == ["Inne"]


def test_write_falls_back_to_active_when_vehicle_id_omitted(ctx):
    client, active_id, other_id, _ = ctx
    r = _add_fillup(client)  # brak ?vehicle_id= — kompat wsteczna
    assert r.status_code == 201
    assert client.get("/api/fillups").get_json()[0]["station"] is None
    assert client.get(f"/api/fillups?vehicle_id={other_id}").get_json() == []


def test_write_scoped_to_explicit_valid_vehicle_id(ctx):
    client, active_id, other_id, _ = ctx
    r = _add_fillup(client, vehicle_id=other_id)
    assert r.status_code == 201
    assert client.get("/api/fillups").get_json() == []
    assert len(client.get(f"/api/fillups?vehicle_id={other_id}").get_json()) == 1


def test_write_rejects_explicit_unknown_vehicle_id(ctx):
    client, active_id, other_id, _ = ctx
    r = _add_fillup(client, vehicle_id=9999)
    assert r.status_code == 400


def test_write_rejects_explicit_archived_vehicle_id(ctx):
    client, active_id, other_id, archived_id = ctx
    r = _add_fillup(client, vehicle_id=archived_id)
    assert r.status_code == 400


def test_read_falls_back_silently_on_invalid_viewing_param(ctx):
    client, active_id, other_id, _ = ctx
    _add_fillup(client, vehicle_id=active_id)
    r = client.get("/api/fillups?vehicle_id=9999")
    assert r.status_code == 200
    assert len(r.get_json()) == 1  # ciche przełączenie na aktywne


def test_prefill_uses_viewing_vehicles_own_ha_entities(ctx):
    """Dowód naprawy realnego buga: dziś prefill przy przeglądaniu auta B
    czytał GPS/odometr auta A (aktywnego) — teraz czyta encje przeglądanego."""
    client, active_id, other_id, _ = ctx
    active_pre = client.get("/api/prefill").get_json()
    other_pre = client.get(f"/api/prefill?vehicle_id={other_id}").get_json()
    assert active_pre["odometer"] == 10000
    assert other_pre["odometer"] == 20000


def test_verify_endpoint_ignores_viewing_param_stays_on_active_vehicle(ctx):
    client, active_id, other_id, _ = ctx
    _add_fillup(client, vehicle_id=active_id)
    _add_fillup(client, vehicle_id=other_id, date="2025-02-01T12:00", odometer=500)
    active_verify = client.get("/api/verify").get_json()
    other_verify = client.get(f"/api/verify?vehicle_id={other_id}").get_json()
    # api_verify() zostaje przypięty do aktywnego auta — parametr ignorowany.
    assert active_verify == other_verify
    assert active_verify["checks"]["count"]["local"] == 1


@pytest.fixture
def ctx_mqtt(tmp_path):
    """Jak ctx, ale z mockiem mqtt_unpublish do weryfikacji 0.11.1 hotfixu
    (usunięcie/archiwizacja pojazdu musi czyścić jego MQTT discovery —
    znalezione przy weryfikacji produkcyjnej 0.11.0: DELETE zostawiał
    osierocone sensor.testowe_auto_* w rejestrze HA)."""
    db_path = str(tmp_path / "multi_mqtt.db")
    c = dbm.get_conn(db_path)
    dbm.migrate(c)
    active_id = dbm.ensure_vehicle(c, "Superb", 66.0, "PB95")
    other_id = dbm.create_vehicle(c, "Mazda", 45.0, "PB95")
    c.close()

    mqtt_unpublish = MagicMock()
    app = create_app(db_path=db_path, config={}, mqtt_unpublish=mqtt_unpublish)
    app.testing = True
    return app.test_client(), active_id, other_id, mqtt_unpublish


def test_delete_vehicle_unpublishes_its_mqtt_device(ctx_mqtt):
    client, active_id, other_id, mqtt_unpublish = ctx_mqtt
    resp = client.delete(f"/api/vehicles/{other_id}")
    assert resp.status_code == 200
    mqtt_unpublish.assert_called_once_with(
        pub.device_id_for_vehicle(other_id, active_id))


def test_archive_vehicle_unpublishes_its_mqtt_device(ctx_mqtt):
    client, active_id, other_id, mqtt_unpublish = ctx_mqtt
    resp = client.post(f"/api/vehicles/{other_id}/archive")
    assert resp.status_code == 200
    mqtt_unpublish.assert_called_once_with(
        pub.device_id_for_vehicle(other_id, active_id))


def test_delete_active_vehicle_unpublishes_bare_device_id_before_new_active_republish(ctx_mqtt):
    """Rożek: usunięcie AKTYWNEGO pojazdu musi wyczyścić gołe 'fuel_tracker'
    (topic, pod którym był publikowany, bo był aktywny w momencie usunięcia),
    nie prefiksowany '_<id>' wyliczony już PO usunięciu z nowym aktywnym."""
    client, active_id, other_id, mqtt_unpublish = ctx_mqtt
    resp = client.delete(f"/api/vehicles/{active_id}")
    assert resp.status_code == 200
    mqtt_unpublish.assert_called_once_with("fuel_tracker")


def test_unarchive_does_not_call_mqtt_unpublish(ctx_mqtt):
    client, active_id, other_id, mqtt_unpublish = ctx_mqtt
    client.post(f"/api/vehicles/{other_id}/archive")
    mqtt_unpublish.reset_mock()
    resp = client.post(f"/api/vehicles/{other_id}/unarchive")
    assert resp.status_code == 200
    mqtt_unpublish.assert_not_called()
