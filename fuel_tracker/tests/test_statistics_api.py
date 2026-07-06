"""Endpoint /api/statistics i eksport raportu CSV."""
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
        db_path=db_path, vehicle_id=vid,
        config={"monthly_budget": 0.0, "default_fuel_type": "PB95",
                "vehicle_name": "Testowy", "price_region": "dolnośląskie",
                "tank_capacity_l": 66.0, "lease_km_limit": 90000,
                "odo_budget_entity": "sensor.odo_vs_budget"},
        ha_state=lambda e: {"state": "7672"}
                 if e == "sensor.odo_vs_budget" else None)
    app.testing = True
    return app.test_client()


def _seed(client):
    fills = [
        ("2026-01-10T10:00", 10000, 40, 6.20, "Orlen A", "fleet_card"),
        ("2026-02-10T10:00", 10700, 42, 6.00, "BP B", "own"),
        ("2026-03-10T10:00", 11400, 41, 5.80, "Orlen A", "fleet_card"),
    ]
    for date, odo, vol, price, station, paid in fills:
        r = client.post("/api/fillups", json={
            "date": date, "odometer": odo, "volume_l": vol,
            "price_per_l": price, "station": station, "paid_by": paid,
            "full_tank": True})
        assert r.status_code == 201
    cats = {c["name"]: c["id"] for c in client.get("/api/categories").get_json()}
    client.post("/api/expenses", json={
        "date": "2026-02-15T10:00", "cost": 45.0,
        "category_id": cats["Płyny"], "description": "AdBlue"})
    client.post("/api/expenses", json={
        "date": "2026-03-15T10:00", "cost": 120.0,
        "category_id": cats["Myjnia"]})


def test_statistics_endpoint(client):
    _seed(client)
    s = client.get("/api/statistics").get_json()

    assert s["split"] == {"fuel_card": round(40 * 6.20 + 41 * 5.80, 2),
                          "fuel_own": 252.0, "fluids": 45.0,
                          "other_expenses": 120.0}
    assert s["records"]["cheapest_fillup"]["price_per_l"] == 5.80
    assert s["stations"][0]["station"] == "Orlen A"
    assert s["monthly_km"] == [{"month": "2026-02", "km": 700},
                               {"month": "2026-03", "km": 700}]
    assert s["leasing"]["odo_vs_budget"] == 7672.0
    assert s["leasing"]["km_limit"] == 90000
    assert s["leasing"]["current_odometer"] == 11400
    assert s["leasing"]["projected_annual_km"] > 0
    assert s["leasing"]["limit_depletion_date"]  # data w przyszłości
    assert s["estimated_range_km"] > 0
    assert s["region"]["name"] == "dolnośląskie"
    assert s["region"]["latest"] is None  # scraper jeszcze nie działał


def test_statistics_empty_db(client):
    s = client.get("/api/statistics").get_json()
    assert s["records"]["best_consumption"] is None
    assert s["stations"] == []
    assert s["leasing"]["limit_depletion_date"] is None
    assert s["estimated_range_km"] is None


def test_report_csv(client):
    _seed(client)
    r = client.get("/api/report.csv")
    assert r.status_code == 200
    lines = r.data.decode().strip().splitlines()
    assert lines[0].startswith("Miesiac;")
    assert len(lines) == 4  # nagłówek + 3 miesiące
    feb = next(ln for ln in lines if ln.startswith("2026-02"))
    # luty: prywatne tankowanie 252, płyny 45, 700 km
    assert feb == "2026-02;0.0;252.0;45.0;0.0;42.0;700"
    # filtr po roku
    assert len(client.get("/api/report.csv?year=2025")
               .data.decode().strip().splitlines()) == 1
