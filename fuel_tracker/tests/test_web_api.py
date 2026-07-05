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


def test_verify_endpoint(client):
    v = client.get("/api/verify").get_json()
    assert "checks" in v
    assert set(v["checks"]) == {"count", "cost", "volume"}


def test_pages_render_without_absolute_urls(client):
    # Ingress: żadnych href/src zaczynających się od "/" (poza X-Ingress-Path).
    for path in ("/", "/fillups", "/fillup-form", "/expenses", "/settings"):
        r = client.get(path, headers={"X-Ingress-Path": "/api/hassio_ingress/tok"})
        assert r.status_code == 200
        html = r.data.decode()
        bad = [m for m in re.findall(r'(?:href|src)="(/[^"]*)"', html)
               if not m.startswith("/api/hassio_ingress/tok")]
        assert bad == [], f"{path}: bezwzględne URL-e {bad}"


def test_pages_render_without_ingress_header(client):
    for path in ("/", "/fillups", "/settings"):
        assert client.get(path).status_code == 200
