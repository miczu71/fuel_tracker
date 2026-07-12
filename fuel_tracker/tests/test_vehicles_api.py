"""API pojazdów i dynamiczny aktywny pojazd bez restartu add-onu (0.8.0)."""
import pytest

from fuel_tracker import db as dbm
from fuel_tracker.web import create_app


@pytest.fixture
def app_ctx(tmp_path):
    db_path = str(tmp_path / "vehicles.db")
    c = dbm.get_conn(db_path)
    dbm.migrate(c)
    vid = dbm.ensure_vehicle(c, "AutoA", 66.0, "PB95")
    c.close()
    app = create_app(db_path=db_path, config={})
    app.testing = True
    return app.test_client(), db_path, vid


def _add_vehicle(client, name="Mazda"):
    return client.post("/api/vehicles", json={
        "name": name, "tank_capacity_l": 45.0, "fuel_type": "PB95"})


def test_list_vehicles_returns_active_flag(app_ctx):
    client, db_path, vid = app_ctx
    rows = client.get("/api/vehicles").get_json()
    assert len(rows) == 1
    assert rows[0]["id"] == vid
    assert rows[0]["active"] is True


def test_create_vehicle_via_api(app_ctx):
    client, db_path, vid = app_ctx
    r = _add_vehicle(client)
    assert r.status_code == 201
    new_id = r.get_json()["id"]
    rows = client.get("/api/vehicles").get_json()
    assert len(rows) == 2
    assert any(v["id"] == new_id and v["name"] == "Mazda" for v in rows)


def test_activate_vehicle_switches_active_without_restart(app_ctx):
    client, db_path, vid = app_ctx
    new_id = _add_vehicle(client).get_json()["id"]
    assert client.post(f"/api/vehicles/{new_id}/activate").status_code == 200
    summary = client.get("/api/summary").get_json()
    assert summary["fillup_count"] == 0  # Mazda ma pustą historię
    rows = client.get("/api/vehicles").get_json()
    active = next(v for v in rows if v["active"])
    assert active["id"] == new_id


def test_activate_refuses_archived_vehicle(app_ctx):
    client, db_path, vid = app_ctx
    new_id = _add_vehicle(client).get_json()["id"]
    client.post(f"/api/vehicles/{new_id}/archive")
    assert client.post(f"/api/vehicles/{new_id}/activate").status_code == 400


def test_archive_endpoint_refuses_last_vehicle(app_ctx):
    client, db_path, vid = app_ctx
    assert client.post(f"/api/vehicles/{vid}/archive").status_code == 400


def test_archive_and_unarchive_roundtrip(app_ctx):
    client, db_path, vid = app_ctx
    new_id = _add_vehicle(client).get_json()["id"]
    assert client.post(f"/api/vehicles/{new_id}/archive").status_code == 200
    rows = client.get("/api/vehicles").get_json()
    mazda = next(v for v in rows if v["id"] == new_id)
    assert mazda["archived"] == 1
    assert client.post(f"/api/vehicles/{new_id}/unarchive").status_code == 200


def test_delete_vehicle_without_history_ok(app_ctx):
    client, db_path, vid = app_ctx
    new_id = _add_vehicle(client).get_json()["id"]
    assert client.delete(f"/api/vehicles/{new_id}").status_code == 200
    assert client.get(f"/api/vehicles/{new_id}").status_code == 404


def test_delete_vehicle_with_history_returns_409(app_ctx):
    client, db_path, vid = app_ctx
    client.post("/api/fillups", json={
        "date": "2025-01-01T12:00", "odometer": 1000, "volume_l": 40,
        "price_per_l": 6.0, "full_tank": True})
    assert client.delete(f"/api/vehicles/{vid}").status_code == 409


def test_get_vehicle_404_for_unknown_id(app_ctx):
    client, db_path, vid = app_ctx
    assert client.get("/api/vehicles/9999").status_code == 404


def test_statistics_includes_lease_km_margin(app_ctx):
    client, db_path, vid = app_ctx
    c = dbm.get_conn(db_path)
    dbm.update_vehicle(c, vid, {
        "lease_start": "2024-10-10", "lease_end": "2028-10-09",
        "lease_km_limit": 90000})
    c.close()
    stats = client.get("/api/statistics").get_json()
    assert "lease_km_margin" in stats["leasing"]
