"""Parser paragonów: normalizacja, ekstrakcja JSON, API + wiązanie załączników."""
import io
import json
from pathlib import Path

import pytest

from fuel_tracker import db as dbm, receipts
from fuel_tracker.web import create_app

FIXTURES = Path(__file__).parent / "fixtures"

# Wartości z prawdziwego dowodu wydania FLOTA (tests/fixtures/receipt_orlen_fleet.jpg)
FLEET_PARSED = {
    "receipt_type": "fleet_card",
    "station_name": "ORLEN Będzino",
    "date": "2026-07-03",
    "time": "15:56",
    "odometer_km": 31462,
    "fuel_name": "",
    "fuel_volume_l": 52.470,
    "fuel_price_per_l": 0,
    "fuel_total": 357.85,
    "currency": "PLN",
    "non_fuel_items": [],
}


def test_normalize_fleet_receipt_derives_price_and_odometer():
    n = receipts.normalize(FLEET_PARSED, "PB95")
    assert n["date"] == "2026-07-03T15:56"
    assert n["odometer"] == 31462
    assert n["volume_l"] == 52.47
    assert n["total_cost"] == 357.85
    assert n["price_per_l"] == round(357.85 / 52.47, 3)  # wyliczona: brak na paragonie
    assert n["fuel_type"] == "PB95"  # dowód wydania nie ma nazwy paliwa
    assert n["station"] == "ORLEN Będzino"
    assert n["non_fuel_total"] == 0
    assert n["currency"] == "PLN"


def test_normalize_fiscal_receipt_with_fluids():
    n = receipts.normalize({
        "receipt_type": "fiscal",
        "station_name": "ORLEN Wrocław",
        "date": "2026-06-20", "time": "09:12",
        "odometer_km": 0,
        "fuel_name": "EFECTA 95",
        "fuel_volume_l": 40.0, "fuel_price_per_l": 6.10, "fuel_total": 0,
        "currency": "PLN",
        "non_fuel_items": [
            {"description": "AdBlue 5L", "total": 39.99},
            {"description": "Płyn do spryskiwaczy", "total": 19.99},
        ],
    }, "PB95")
    assert n["total_cost"] == 244.0  # litry × cena
    assert n["odometer"] is None  # 0 = brak na paragonie
    assert n["fuel_type"] == "PB95"  # EFECTA 95 → PB95
    assert len(n["non_fuel_items"]) == 2
    assert n["non_fuel_total"] == 59.98


def test_normalize_tolerates_garbage():
    n = receipts.normalize({"receipt_type": "other", "date": "brak",
                            "currency": "", "fuel_volume_l": "x",
                            "non_fuel_items": [{"description": "", "total": 5}]})
    assert n["date"] == ""
    assert n["volume_l"] is None
    assert n["currency"] == "PLN"
    assert n["non_fuel_items"] == []  # pozycja bez opisu odpada


def test_map_fuel():
    assert receipts._map_fuel("EFECTA 95") == "PB95"
    assert receipts._map_fuel("VERVA 98") == "PB98"
    assert receipts._map_fuel("EFECTA DIESEL") == "ON"
    assert receipts._map_fuel("LPG") == "LPG"
    assert receipts._map_fuel("") is None


def test_extract_json_from_fenced_text():
    text = 'Oto wynik:\n```json\n{"a": 1, "b": [2, 3]}\n```\nkoniec'
    assert receipts.extract_json(text) == {"a": 1, "b": [2, 3]}
    assert receipts.extract_json("bez jsona") is None


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "web.db")
    c = dbm.get_conn(db_path)
    dbm.migrate(c)
    vid = dbm.ensure_vehicle(c, "Testowy", 66.0, "PB95")
    c.close()
    app = create_app(
        db_path=db_path, vehicle_id=vid,
        config={"monthly_budget": 0.0, "default_fuel_type": "PB95",
                "vehicle_name": "Testowy",
                "share_dir": str(tmp_path / "share")})
    app.testing = True
    monkeypatch.setattr(receipts, "analyze", lambda path: dict(FLEET_PARSED))
    return app.test_client()


def _parse_receipt(client):
    return client.post("/api/receipts/parse", data={
        "file": (io.BytesIO(b"fake-jpeg-bytes"), "receipt.jpg"),
    }, content_type="multipart/form-data")


def test_parse_endpoint_saves_and_prefills(client):
    r = _parse_receipt(client)
    assert r.status_code == 200
    body = r.get_json()
    assert body["parsed"]["odometer"] == 31462
    assert body["parsed"]["volume_l"] == 52.47
    aid = body["attachment_id"]

    # Plik jest serwowany z powrotem
    img = client.get(f"/api/attachments/{aid}")
    assert img.status_code == 200
    assert img.data == b"fake-jpeg-bytes"


def test_fillup_links_attachment(client):
    aid = _parse_receipt(client).get_json()["attachment_id"]
    r = client.post("/api/fillups", json={
        "date": "2026-07-03T15:56", "odometer": 31462, "volume_l": 52.47,
        "total_cost": 357.85, "full_tank": True, "attachment_id": aid,
    })
    assert r.status_code == 201
    rows = client.get("/api/fillups").get_json()
    assert rows[0]["attachment_id"] == aid


def test_expense_links_attachment(client):
    aid = _parse_receipt(client).get_json()["attachment_id"]
    r = client.post("/api/expenses", json={
        "date": "2026-07-03T15:56", "cost": 39.99,
        "description": "AdBlue", "attachment_id": aid,
    })
    assert r.status_code == 201
    rows = client.get("/api/expenses").get_json()
    assert rows[0]["attachment_id"] == aid


def test_parse_endpoint_keeps_attachment_on_analyze_error(client, monkeypatch):
    def boom(path):
        raise receipts.ReceiptError("Model nie odpowiedział")
    monkeypatch.setattr(receipts, "analyze", boom)
    r = _parse_receipt(client)
    assert r.status_code == 502
    body = r.get_json()
    # Zdjęcie zostało — można je podpiąć do ręcznego wpisu
    assert body["attachment_id"]
    assert client.get(f"/api/attachments/{body['attachment_id']}").status_code == 200
