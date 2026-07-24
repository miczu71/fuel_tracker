"""Porównanie pojazdów (0.13.0): GET /api/compare — spalanie/koszt-km/TCO
side-by-side dla wszystkich nie-zarchiwizowanych pojazdów naraz."""
import pytest

from fuel_tracker import db as dbm
from fuel_tracker.web import create_app


@pytest.fixture
def ctx(tmp_path):
    db_path = str(tmp_path / "compare.db")
    c = dbm.get_conn(db_path)
    dbm.migrate(c)
    active_id = dbm.ensure_vehicle(c, "AutoA", 66.0, "PB95")
    dbm.update_vehicle(c, active_id, {"monthly_rate": 1000.0})
    other_id = dbm.create_vehicle(c, "AutoB", 45.0, "ON")
    archived_id = dbm.create_vehicle(c, "Stary", 50.0, "PB95")
    dbm.archive_vehicle(c, archived_id)
    c.close()

    app = create_app(db_path=db_path, config={})
    app.testing = True
    return app.test_client(), active_id, other_id, archived_id


def _fillup(client, vehicle_id, date, odo, vol=40, price=6.0):
    return client.post(f"/api/fillups?vehicle_id={vehicle_id}", json={
        "date": date, "odometer": odo, "volume_l": vol,
        "price_per_l": price, "full_tank": True})


def test_compare_returns_row_per_non_archived_vehicle(ctx):
    client, active_id, other_id, archived_id = ctx
    _fillup(client, active_id, "2026-01-10T10:00", 10000)
    _fillup(client, active_id, "2026-02-10T10:00", 10700, vol=42)
    _fillup(client, other_id, "2026-01-10T10:00", 20000)

    rows = client.get("/api/compare").get_json()
    ids = {r["id"] for r in rows}
    assert ids == {active_id, other_id}  # zarchiwizowany wykluczony

    a = next(r for r in rows if r["id"] == active_id)
    assert a["name"] == "AutoA"
    assert a["fillup_count"] == 2
    assert a["avg_consumption"] == 6.0  # 42L / 700km * 100
    assert a["tco"]["lease_total"] is not None  # ma monthly_rate

    b = next(r for r in rows if r["id"] == other_id)
    assert b["fillup_count"] == 1
    assert b["tco"]["lease_total"] is None  # brak monthly_rate


def test_compare_marks_active_vehicle(ctx):
    client, active_id, other_id, _ = ctx
    rows = client.get("/api/compare").get_json()
    assert next(r for r in rows if r["id"] == active_id)["active"] is True
    assert next(r for r in rows if r["id"] == other_id)["active"] is False


def test_compare_handles_vehicle_with_no_data(ctx):
    client, active_id, other_id, _ = ctx
    rows = client.get("/api/compare").get_json()
    b = next(r for r in rows if r["id"] == other_id)
    assert b["fillup_count"] == 0
    assert b["avg_consumption"] is None
    assert b["tco"]["grand_total"] == 0.0


def test_compare_page_renders(ctx):
    client, *_ = ctx
    r = client.get("/compare")
    assert r.status_code == 200
    assert "Porównanie".encode("utf-8") in r.data
