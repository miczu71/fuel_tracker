"""API ustawień/pojazdu i powiadomień (0.7.0/0.9.0) — Flask test client."""
import pytest

from fuel_tracker import db as dbm
from fuel_tracker.web import create_app


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "web.db")
    c = dbm.get_conn(db_path)
    dbm.migrate(c)
    vid = dbm.ensure_vehicle(c, "Testowy", 66.0, "PB95")
    c.close()

    app = create_app(
        db_path=db_path, config={},
        ha_services=lambda: ["notify.family", "notify.mobile_app_op12"])
    app.testing = True
    app.test_vehicle_id = vid
    return app.test_client()


def test_get_settings_returns_defaults(client):
    s = client.get("/api/settings").get_json()
    assert s["monthly_fuel_budget"] == 0.0
    assert s["default_currency"] == "PLN"
    assert s["price_region"] == ""


def test_put_settings_updates_and_persists(client):
    r = client.put("/api/settings", json={
        "monthly_fuel_budget": 984.0, "price_region": "dolnośląskie"})
    assert r.status_code == 200
    s = client.get("/api/settings").get_json()
    assert s["monthly_fuel_budget"] == 984.0
    assert s["price_region"] == "dolnośląskie"


def test_put_settings_takes_effect_without_restart(client):
    """Budżet zmieniony przez /api/settings widoczny natychmiast w /api/summary."""
    before = client.get("/api/summary").get_json()
    assert before["monthly_budget"] == 0.0
    client.put("/api/settings", json={"monthly_fuel_budget": 500.0})
    after = client.get("/api/summary").get_json()
    assert after["monthly_budget"] == 500.0


def test_get_settings_alert_defaults(client):
    s = client.get("/api/settings").get_json()
    assert s["notify_service"] == "notify.mobile_app_op12"
    assert s["alert_budget_enabled"] == 1
    assert s["alert_cheap_fuel_enabled"] == 1
    assert s["alert_lease_enabled"] == 1
    assert s["alert_budget_threshold"] == 100.0
    assert s["alert_cheap_fuel_delta"] == 0.20
    assert s["alert_lease_km_threshold"] == 1000


def test_put_alert_settings_roundtrip(client):
    r = client.put("/api/settings", json={
        "notify_service": "notify.family",
        "alert_budget_enabled": 0,
        "alert_budget_threshold": 250.0,
        "alert_cheap_fuel_delta": 0.35,
        "alert_lease_km_threshold": 2000})
    assert r.status_code == 200
    s = client.get("/api/settings").get_json()
    assert s["notify_service"] == "notify.family"
    # int 0/1, nie bool — "0" po stronie bazy musi wrócić jako 0 (falsy)
    assert s["alert_budget_enabled"] == 0
    assert s["alert_budget_threshold"] == 250.0
    assert s["alert_cheap_fuel_delta"] == 0.35
    assert s["alert_lease_km_threshold"] == 2000


def test_notify_service_slash_format_normalized_on_read(client):
    client.put("/api/settings", json={"notify_service": "notify/family"})
    s = client.get("/api/settings").get_json()
    assert s["notify_service"] == "notify.family"


def test_ha_services_returns_notify_list(client):
    r = client.get("/api/ha-services")
    assert r.status_code == 200
    assert r.get_json()["services"] == [
        "notify.family", "notify.mobile_app_op12"]


def test_ha_services_empty_without_callable(tmp_path):
    db_path = str(tmp_path / "web2.db")
    c = dbm.get_conn(db_path)
    dbm.migrate(c)
    dbm.ensure_vehicle(c, "Testowy", 66.0, "PB95")
    c.close()
    app = create_app(db_path=db_path, config={})
    app.testing = True
    r = app.test_client().get("/api/ha-services")
    assert r.status_code == 200
    assert r.get_json()["services"] == []


def test_get_vehicle(client):
    v = client.get("/api/vehicles/1").get_json()
    assert v["name"] == "Testowy"
    assert v["tank_capacity_l"] == 66.0
    assert v["fuel_type"] == "PB95"


def test_put_vehicle_updates_and_affects_prefill(client):
    r = client.put("/api/vehicles/1", json={
        "name": "Skoda Superb", "tank_capacity_l": 70.0, "fuel_type": "PB98"})
    assert r.status_code == 200
    v = client.get("/api/vehicles/1").get_json()
    assert v["name"] == "Skoda Superb"
    assert v["fuel_type"] == "PB98"
    pre = client.get("/api/prefill").get_json()
    assert pre["fuel_type"] == "PB98"


def test_put_vehicle_unknown_id_404(client):
    assert client.put("/api/vehicles/999", json={"name": "x"}).status_code == 404


def test_create_vehicle_with_lease_fields(client):
    r = client.post("/api/vehicles", json={
        "name": "Mazda", "tank_capacity_l": 45.0, "fuel_type": "PB95",
        "lease_start": "2025-01-01", "lease_end": "2028-12-31",
        "lease_km_limit": 90000, "monthly_rate": 1850.0})
    assert r.status_code == 201
    v = client.get(f"/api/vehicles/{r.get_json()['id']}").get_json()
    assert v["lease_start"] == "2025-01-01"
    assert v["lease_end"] == "2028-12-31"
    assert v["lease_km_limit"] == 90000
    assert v["monthly_rate"] == 1850.0


def test_create_vehicle_without_lease_fields(client):
    r = client.post("/api/vehicles", json={
        "name": "Fabia", "tank_capacity_l": 45.0, "fuel_type": "PB95"})
    assert r.status_code == 201
    v = client.get(f"/api/vehicles/{r.get_json()['id']}").get_json()
    assert v["lease_start"] is None
    assert v["lease_km_limit"] is None
