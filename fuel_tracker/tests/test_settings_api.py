"""API ustawień/pojazdu/toggle automatyzacji (0.7.0) — Flask test client."""
import pytest

from fuel_tracker import db as dbm
from fuel_tracker.web import create_app


@pytest.fixture
def calls():
    return []


@pytest.fixture
def client(tmp_path, calls):
    db_path = str(tmp_path / "web.db")
    c = dbm.get_conn(db_path)
    dbm.migrate(c)
    vid = dbm.ensure_vehicle(c, "Testowy", 66.0, "PB95")
    c.close()

    def ha_state(entity_id):
        if entity_id == "automation.budzet":
            return {"state": "on"}
        return None

    def ha_call_service(domain, service, data):
        calls.append((domain, service, data))
        if data.get("entity_id") == "automation.nieznana":
            return None
        return {"ok": True}

    app = create_app(
        db_path=db_path, config={},
        ha_state=ha_state, ha_call_service=ha_call_service)
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


def test_get_settings_includes_automation_state_when_configured(client):
    client.put("/api/settings",
              json={"alert_budget_automation": "automation.budzet"})
    s = client.get("/api/settings").get_json()
    assert s["alert_budget_automation_state"] == "on"


def test_get_settings_automation_state_null_when_not_configured(client):
    s = client.get("/api/settings").get_json()
    assert s["alert_budget_automation_state"] is None


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


def test_toggle_automation_success(client, calls):
    client.put("/api/settings",
              json={"alert_budget_automation": "automation.budzet"})
    r = client.post("/api/settings/toggle-automation",
                    json={"key": "alert_budget_automation", "turn_on": False})
    assert r.status_code == 200
    assert calls == [("automation", "turn_off", {"entity_id": "automation.budzet"})]


def test_toggle_automation_not_configured_400(client):
    r = client.post("/api/settings/toggle-automation",
                    json={"key": "alert_budget_automation", "turn_on": True})
    assert r.status_code == 400


def test_toggle_automation_unknown_key_400(client):
    r = client.post("/api/settings/toggle-automation",
                    json={"key": "bogus", "turn_on": True})
    assert r.status_code == 400


def test_toggle_automation_ha_failure_502(client):
    client.put("/api/settings",
              json={"alert_budget_automation": "automation.nieznana"})
    r = client.post("/api/settings/toggle-automation",
                    json={"key": "alert_budget_automation", "turn_on": True})
    assert r.status_code == 502
