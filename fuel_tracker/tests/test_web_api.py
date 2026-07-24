"""REST API i strony (Flask test client, tymczasowa baza SQLite)."""
import re
from pathlib import Path

import pytest

from fuel_tracker import db as dbm
from fuel_tracker import settings as settingsm
from fuel_tracker.web import create_app

FIXTURE = (Path(__file__).parent / "fixtures" / "sample_export.csv").read_bytes()


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "web.db")
    c = dbm.get_conn(db_path)
    dbm.migrate(c)
    vid = dbm.ensure_vehicle(c, "Testowy", 66.0, "PB95")
    dbm.update_vehicle(c, vid, {"monthly_fuel_budget": 800.0,
                               "odometer_entity": "sensor.odo"})
    c.close()
    app = create_app(
        db_path=db_path, config={},
        ha_state=lambda e: {"state": "12345"},
    )
    app.testing = True
    return app.test_client()


def _add_fillup(client, **kw):
    body = {"date": "2025-01-01T12:00", "odometer": 1000, "volume_l": 40,
            "price_per_l": 6.0, "full_tank": True}
    body.update(kw)
    return client.post("/api/fillups", json=body)


def test_fillup_crud_and_two_of_three(client):
    r = _add_fillup(client)
    assert r.status_code == 201
    fid = r.get_json()["id"]

    rows = client.get("/api/fillups").get_json()
    assert len(rows) == 1
    assert rows[0]["total_cost"] == 240.0  # wyliczone z litry × cena

    r = client.put(f"/api/fillups/{fid}", json={
        "date": "2025-01-01T12:00", "odometer": 1000,
        "volume_l": 40, "total_cost": 250.0})
    assert r.status_code == 200
    row = client.get(f"/api/fillups/{fid}").get_json()
    assert row["price_per_l"] == 6.25  # wyliczone z kwoty / litrów

    assert client.delete(f"/api/fillups/{fid}").status_code == 200
    assert client.get("/api/fillups").get_json() == []


def test_fillup_validation_and_conflict(client):
    assert _add_fillup(client, volume_l=0).status_code == 400
    assert _add_fillup(client).status_code == 201
    assert _add_fillup(client).status_code == 409  # duplikat (data, odometr)


def test_summary_and_budget(client):
    _add_fillup(client)
    _add_fillup(client, date="2025-02-01T12:00", odometer=1500, volume_l=30)
    s = client.get("/api/summary").get_json()
    assert s["fillup_count"] == 2
    assert s["avg_consumption"] == 6.0
    assert s["monthly_budget"] == 800.0
    assert len(s["monthly"]) == 2


def test_prefill_uses_ha_odometer(client):
    pre = client.get("/api/prefill").get_json()
    assert pre["odometer"] == 12345
    assert pre["fuel_type"] == "PB95"


def test_expense_crud(client):
    cats = client.get("/api/categories").get_json()
    serwis = next(c["id"] for c in cats if c["name"] == "Serwis")
    r = client.post("/api/expenses", json={
        "date": "2025-01-10T09:00", "cost": 120.5, "category_id": serwis,
        "description": "przegląd"})
    assert r.status_code == 201
    rows = client.get("/api/expenses").get_json()
    assert rows[0]["category"] == "Serwis"
    assert client.delete(f"/api/expenses/{rows[0]['id']}").status_code == 200


def test_csv_import_and_export(client):
    r = client.post("/api/import/csv", data={
        "file": (__import__("io").BytesIO(FIXTURE), "export.csv")})
    assert r.status_code == 200
    assert r.get_json()["fillups_added"] == 3
    exp = client.get("/api/export/log.csv")
    assert exp.status_code == 200
    assert b"## Log" in exp.data


def test_import_drivvo_requires_credentials(client):
    r = client.post("/api/import/drivvo", json={})
    assert r.status_code == 400
    # Dane w body przechodza walidacje (blad dopiero przy logowaniu do API).
    r = client.post("/api/import/drivvo",
                    json={"email": "a@b.c", "password": "x"})
    assert r.status_code == 502


def test_fillup_paid_by_roundtrip(client):
    fid = _add_fillup(client, paid_by="own", latitude=50.11, longitude=20.98
                      ).get_json()["id"]
    row = client.get(f"/api/fillups/{fid}").get_json()
    assert row["paid_by"] == "own"
    assert row["latitude"] == 50.11
    # Domyślnie karta flotowa.
    fid2 = _add_fillup(client, date="2025-02-01T12:00", odometer=1500
                       ).get_json()["id"]
    assert client.get(f"/api/fillups/{fid2}").get_json()["paid_by"] == "fleet_card"


def test_self_paid_total_in_summary(client):
    _add_fillup(client)  # fleet_card, 240 PLN
    _add_fillup(client, date="2025-02-01T12:00", odometer=1500, paid_by="own")
    s = client.get("/api/summary").get_json()
    assert s["self_paid_fuel_total"] == 240.0


def test_odometer_monotonic_validation(client):
    _add_fillup(client)  # 2025-01-01, odo 1000
    # Późniejsza data z mniejszym przebiegiem → 400.
    r = _add_fillup(client, date="2025-02-01T12:00", odometer=900)
    assert r.status_code == 400
    assert "mniejszy" in r.get_json()["error"]
    # Wcześniejsza data z większym przebiegiem → 400.
    r = _add_fillup(client, date="2024-12-01T12:00", odometer=1100)
    assert r.status_code == 400
    # missed_previous wyłącza kontrolę.
    r = _add_fillup(client, date="2025-02-01T12:00", odometer=900,
                    missed_previous=True)
    assert r.status_code == 201


def test_fillup_saves_station(client):
    _add_fillup(client, station="Stacja A",
                latitude=50.0000, longitude=20.0000)
    rows = client.get("/api/stations").get_json()
    s = next(x for x in rows if x["name"] == "Stacja A")
    assert s["latitude"] == 50.0000


def test_map_data_endpoint(client):
    _add_fillup(client, station="Stacja A",
                latitude=50.0000, longitude=20.0000, paid_by="own")
    data = client.get("/api/map-data").get_json()
    s = next(x for x in data if x["name"] == "Stacja A")
    assert s["visits"] == 1 and s["own_paid"] == 1


def test_stations_nearby_requires_coords(client):
    assert client.get("/api/stations/nearby").status_code == 400
    assert client.get("/api/stations/nearby?lat=x&lon=y").status_code == 400


def test_prefill_matches_station_by_gps(tmp_path):
    db_path = str(tmp_path / "gps.db")
    c = dbm.get_conn(db_path)
    dbm.migrate(c)
    vid = dbm.ensure_vehicle(c, "T", 66.0, "PB95")
    c.execute("INSERT INTO stations (name, latitude, longitude) "
              "VALUES ('Stacja A', 50.0000, 20.0000)")
    dbm.update_vehicle(c, vid, {"odometer_entity": "sensor.odo",
                               "location_entity": "device_tracker.telefon"})
    c.commit()
    c.close()

    def ha_state(entity):
        if entity == "device_tracker.telefon":
            return {"state": "not_home",
                    "attributes": {"latitude": 50.0001, "longitude": 20.0001}}
        return {"state": "12345"}

    app = create_app(
        db_path=db_path, config={}, ha_state=ha_state)
    app.testing = True
    pre = app.test_client().get("/api/prefill").get_json()
    assert pre["station"] == "Stacja A"
    assert pre["station_matched"] is True
    assert pre["latitude"] == 50.0001


def test_expense_edit(client):
    cats = client.get("/api/categories").get_json()
    plyny = next(c["id"] for c in cats if c["name"] == "Płyny")
    eid = client.post("/api/expenses", json={
        "date": "2025-01-10T09:00", "cost": 50,
        "category_id": plyny, "description": "AdBlue"}).get_json()["id"]
    r = client.put(f"/api/expenses/{eid}", json={
        "date": "2025-01-10T09:00", "cost": 65.5,
        "category_id": plyny, "description": "AdBlue 10L"})
    assert r.status_code == 200
    row = client.get("/api/expenses").get_json()[0]
    assert row["cost"] == 65.5 and row["description"] == "AdBlue 10L"
    assert client.put("/api/expenses/9999", json={
        "date": "2025-01-10T09:00", "cost": 1}).status_code == 404


def test_category_hide_toggle(client):
    cats = client.get("/api/categories").get_json()
    assert all(c["hidden"] == 0 for c in cats)
    serwis = next(c["id"] for c in cats if c["name"] == "Serwis")
    assert client.put(f"/api/categories/{serwis}",
                      json={"hidden": True}).status_code == 200
    cats = client.get("/api/categories").get_json()
    assert next(c for c in cats if c["id"] == serwis)["hidden"] == 1


def test_category_list_includes_tco_group(client):
    cats = client.get("/api/categories").get_json()
    plyny = next(c for c in cats if c["name"] == "Płyny")
    assert plyny["tco_group"] == "fluids"


def test_category_create(client):
    r = client.post("/api/categories", json={
        "name": "Opony", "tco_group": "service"})
    assert r.status_code == 201
    cid = r.get_json()["id"]
    cats = client.get("/api/categories").get_json()
    row = next(c for c in cats if c["id"] == cid)
    assert row["name"] == "Opony" and row["tco_group"] == "service"


def test_category_create_requires_name(client):
    assert client.post("/api/categories", json={}).status_code == 400


def test_category_create_rejects_duplicate(client):
    client.post("/api/categories", json={"name": "Opony"})
    r = client.post("/api/categories", json={"name": "Opony"})
    assert r.status_code == 409


def test_category_rename_and_regroup(client):
    cid = client.post("/api/categories", json={"name": "Opony"}).get_json()["id"]
    r = client.put(f"/api/categories/{cid}", json={
        "name": "Opony sezonowe", "tco_group": "service"})
    assert r.status_code == 200
    cats = client.get("/api/categories").get_json()
    row = next(c for c in cats if c["id"] == cid)
    assert row["name"] == "Opony sezonowe" and row["tco_group"] == "service"


def test_category_delete(client):
    cid = client.post("/api/categories", json={"name": "Do usunięcia"}).get_json()["id"]
    assert client.delete(f"/api/categories/{cid}").status_code == 200
    cats = client.get("/api/categories").get_json()
    assert all(c["id"] != cid for c in cats)


def test_category_delete_with_expenses_refused(client):
    cats = client.get("/api/categories").get_json()
    plyny = next(c["id"] for c in cats if c["name"] == "Płyny")
    client.post("/api/expenses", json={
        "date": "2025-01-10T09:00", "cost": 50, "category_id": plyny})
    r = client.delete(f"/api/categories/{plyny}")
    assert r.status_code == 409


def test_pages_render_without_absolute_urls(client):
    # Ingress: żadnych href/src zaczynających się od "/" (poza X-Ingress-Path).
    for path in ("/", "/fillups", "/fillup-form", "/expenses", "/settings",
                 "/map", "/statistics", "/compare"):
        r = client.get(path, headers={"X-Ingress-Path": "/api/hassio_ingress/tok"})
        assert r.status_code == 200
        html = r.data.decode()
        bad = [m for m in re.findall(r'(?:href|src)="(/[^"]*)"', html)
               if not m.startswith("/api/hassio_ingress/tok")]
        assert bad == [], f"{path}: bezwzględne URL-e {bad}"


def test_pages_render_without_ingress_header(client):
    for path in ("/", "/fillups", "/settings"):
        assert client.get(path).status_code == 200
