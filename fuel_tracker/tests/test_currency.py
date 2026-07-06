"""Kursy NBP: cache, fallback i przeliczanie tankowań zagranicznych."""
import pytest

from fuel_tracker import currency, db as dbm
from fuel_tracker.web import create_app


@pytest.fixture
def conn(tmp_path):
    c = dbm.get_conn(str(tmp_path / "cur.db"))
    dbm.migrate(c)
    yield c
    c.close()


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
                "vehicle_name": "Testowy"})
    app.testing = True
    return app.test_client()


def _mock_nbp(monkeypatch, rate=4.30, effective="2026-07-03", fail=False):
    calls = []

    def fake(code, on_date):
        calls.append((code, on_date))
        if fail:
            return None
        return {"rate": rate, "effective_date": effective}

    monkeypatch.setattr(currency, "_fetch_nbp", fake)
    return calls


def test_pln_is_identity(conn):
    assert currency.get_rate(conn, "PLN", "2026-07-06") == {
        "rate": 1.0, "effective_date": "2026-07-06"}


def test_rate_fetched_once_then_cached(conn, monkeypatch):
    calls = _mock_nbp(monkeypatch)
    r1 = currency.get_rate(conn, "eur", "2026-07-06")
    r2 = currency.get_rate(conn, "EUR", "2026-07-06")
    assert r1 == r2 == {"rate": 4.30, "effective_date": "2026-07-03"}
    assert len(calls) == 1  # drugi odczyt z cache


def test_stale_cache_fallback_when_nbp_down(conn, monkeypatch):
    _mock_nbp(monkeypatch)
    currency.get_rate(conn, "EUR", "2026-07-01")
    _mock_nbp(monkeypatch, fail=True)
    stale = currency.get_rate(conn, "EUR", "2026-07-06")
    assert stale == {"rate": 4.30, "effective_date": "2026-07-03"}


def test_no_rate_at_all(conn, monkeypatch):
    _mock_nbp(monkeypatch, fail=True)
    assert currency.get_rate(conn, "EUR", "2026-07-06") is None


def test_foreign_fillup_converted_to_pln(client):
    r = client.post("/api/fillups", json={
        "date": "2026-07-01T12:00", "odometer": 1000, "volume_l": 40,
        "price_per_l": 1.50, "currency": "EUR", "exchange_rate": 4.30})
    assert r.status_code == 201
    row = client.get("/api/fillups").get_json()[0]
    assert row["currency"] == "EUR"
    assert row["exchange_rate"] == 4.30
    assert row["price_per_l_orig"] == 1.50
    assert row["total_cost_orig"] == 60.0   # 2 z 3 w walucie oryginalnej
    assert row["price_per_l"] == 6.45       # 1.50 × 4.30
    assert row["total_cost"] == 258.0       # 60 × 4.30


def test_foreign_fillup_without_rate_rejected(client, monkeypatch):
    _mock_nbp(monkeypatch, fail=True)
    r = client.post("/api/fillups", json={
        "date": "2026-07-01T12:00", "odometer": 1000, "volume_l": 40,
        "price_per_l": 1.50, "currency": "EUR"})
    assert r.status_code == 400
    assert "kurs" in r.get_json()["error"].lower()


def test_foreign_fillup_rate_autofetched(client, monkeypatch):
    _mock_nbp(monkeypatch, rate=0.19)  # CZK
    r = client.post("/api/fillups", json={
        "date": "2026-07-01T12:00", "odometer": 1000, "volume_l": 40,
        "total_cost": 1500, "currency": "CZK"})
    assert r.status_code == 201
    row = client.get("/api/fillups").get_json()[0]
    assert row["total_cost"] == 285.0  # 1500 CZK × 0.19
    assert row["exchange_rate"] == 0.19


def test_pln_fillup_has_no_orig_columns(client):
    r = client.post("/api/fillups", json={
        "date": "2026-07-01T12:00", "odometer": 1000, "volume_l": 40,
        "price_per_l": 6.0})
    assert r.status_code == 201
    row = client.get("/api/fillups").get_json()[0]
    assert row["currency"] == "PLN"
    assert row["exchange_rate"] is None
    assert row["price_per_l_orig"] is None


def test_api_rate_endpoint(client, monkeypatch):
    _mock_nbp(monkeypatch)
    r = client.get("/api/rate?currency=EUR&date=2026-07-06")
    assert r.status_code == 200
    assert r.get_json() == {"rate": 4.30, "effective_date": "2026-07-03",
                            "currency": "EUR"}
    assert client.get("/api/rate").status_code == 400
    _mock_nbp(monkeypatch, fail=True)
    assert client.get("/api/rate?currency=CHF").status_code == 502
