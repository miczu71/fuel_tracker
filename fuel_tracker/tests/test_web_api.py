"""REST API i strony (Flask test client, tymczasowa baza SQLite)."""
import re
from pathlib import Path

import pytest

from fuel_tracker import db as dbm
from fuel_tracker.web import create_app

FIXTURE = (Path(__file__).parent / "fixtures" / "fuelio_sample.csv").read_bytes()


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "web.db")
    c = dbm.get_conn(db_path)
    dbm.migrate(c)
    vid = dbm.ensure_vehicle(c, "Testowy", 66.0, "PB95")
    c.close()
    app = create_app(
        db_path=db_path, vehicle_id=vid,
        config={"monthly_budget": 984.0, "default_fuel_type": "PB95",
                "vehicle_name": "Testowy", "odometer_entity": "sensor.odo"},
        ha_state=lambda e: {"state": "31468"},
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
    assert s["monthly_budget"] == 984.0
    assert len(s["monthly"]) == 2


def test_prefill_uses_ha_odometer(client):
    pre = client.get("/api/prefill").get_json()
    assert pre["odometer"] == 31468
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
    exp = client.get("/api/export/fuelio.csv")
    assert exp.status_code == 200
    assert b"## Log" in exp.data


def test_import_drivvo_requires_credentials(client):
    r = client.post("/api/import/drivvo", json={})
    assert r.status_code == 400
    # Dane w body przechodza walidacje (blad dopiero przy logowaniu do API).
    r = client.post("/api/import/drivvo",
                    json={"email": "a@b.c", "password": "x"})
    assert r.status_code == 502


def test_verify_endpoint(client):
    v = client.get("/api/verify").get_json()
    assert "checks" in v
    assert set(v["checks"]) == {"count", "cost", "volume"}


def test_fillup_paid_by_roundtrip(client):
    fid = _add_fillup(client, paid_by="own", latitude=51.11, longitude=16.98
                      ).get_json()["id"]
    row = client.get(f"/api/fillups/{fid}").get_json()
    assert row["paid_by"] == "own"
    assert row["latitude"] == 51.11
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
    _add_fillup(client, station="Orlen Legnicka",
                latitude=51.1152, longitude=16.9812)
    rows = client.get("/api/stations").get_json()
    s = next(x for x in rows if x["name"] == "Orlen Legnicka")
    assert s["latitude"] == 51.1152


def test_map_data_endpoint(client):
    _add_fillup(client, station="Orlen Legnicka",
                latitude=51.1152, longitude=16.9812, paid_by="own")
    data = client.get("/api/map-data").get_json()
    s = next(x for x in data if x["name"] == "Orlen Legnicka")
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
              "VALUES ('Orlen Legnicka', 51.1152, 16.9812)")
    c.commit()
    c.close()

    def ha_state(entity):
        if entity == "device_tracker.op12":
            return {"state": "not_home",
                    "attributes": {"latitude": 51.1153, "longitude": 16.9813}}
        return {"state": "31468"}

    app = create_app(
        db_path=db_path, vehicle_id=vid,
        config={"default_fuel_type": "PB95", "vehicle_name": "T",
                "odometer_entity": "sensor.odo",
                "location_entity": "device_tracker.op12"},
        ha_state=ha_state)
    app.testing = True
    pre = app.test_client().get("/api/prefill").get_json()
    assert pre["station"] == "Orlen Legnicka"
    assert pre["station_matched"] is True
    assert pre["latitude"] == 51.1153


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


def test_pages_render_without_absolute_urls(client):
    # Ingress: żadnych href/src zaczynających się od "/" (poza X-Ingress-Path).
    for path in ("/", "/fillups", "/fillup-form", "/expenses", "/settings",
                 "/map", "/statistics"):
        r = client.get(path, headers={"X-Ingress-Path": "/api/hassio_ingress/tok"})
        assert r.status_code == 200
        html = r.data.decode()
        bad = [m for m in re.findall(r'(?:href|src)="(/[^"]*)"', html)
               if not m.startswith("/api/hassio_ingress/tok")]
        assert bad == [], f"{path}: bezwzględne URL-e {bad}"


def test_pages_render_without_ingress_header(client):
    for path in ("/", "/fillups", "/settings"):
        assert client.get(path).status_code == 200
