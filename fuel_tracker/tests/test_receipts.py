"""Parser paragonów: normalizacja, ekstrakcja JSON, API + wiązanie załączników."""
import io
import json
from pathlib import Path

import pytest

from fuel_tracker import db as dbm, receipts
from fuel_tracker.web import create_app

FIXTURES = Path(__file__).parent / "fixtures"

# Syntetyczne wartości w formacie dowodu wydania z karty flotowej.
FLEET_PARSED = {
    "receipt_type": "fleet_card",
    "station_name": "Stacja Testowa",
    "date": "2026-01-15",
    "time": "12:34",
    "odometer_km": 12345,
    "fuel_name": "",
    "fuel_volume_l": 45.500,
    "fuel_price_per_l": 0,
    "fuel_total": 300.30,
    "currency": "PLN",
    "non_fuel_items": [],
}


def test_normalize_fleet_receipt_derives_price_and_odometer():
    n = receipts.normalize(FLEET_PARSED, "PB95")
    assert n["date"] == "2026-01-15T12:34"
    assert n["odometer"] == 12345
    assert n["volume_l"] == 45.5
    assert n["total_cost"] == 300.30
    assert n["price_per_l"] == round(300.30 / 45.5, 3)  # wyliczona: brak na paragonie
    assert n["fuel_type"] == "PB95"  # dowód wydania nie ma nazwy paliwa
    assert n["station"] == "Stacja Testowa"
    assert n["non_fuel_total"] == 0
    assert n["currency"] == "PLN"


def test_normalize_fiscal_receipt_with_fluids():
    n = receipts.normalize({
        "receipt_type": "fiscal",
        "station_name": "Stacja Miejska",
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


# ── adres stacji z paragonu (0.16.0, docs/PLAN-0.16.0-stations.md) ─────────

def test_normalize_extracts_station_address_fields():
    """Prawdziwy kształt danych z receipt_orlen_fleet.jpg (Będzino, stacja
    nr 4282). 0.16.1: normalize() już nie SKŁADA nazwy z adresu — to robi
    wyłącznie stations.resolve_station() (web.py), żeby konwencja nazwy była
    ustalana w jednym miejscu. "station" zostaje surowym fallbackiem
    (station_name), pola station_* są kanonizowane (ORLEN → Orlen)."""
    n = receipts.normalize({
        "receipt_type": "fleet_card", "station_name": "ORLEN Będzino",
        "station_brand": "ORLEN", "station_street": "BĘDZINO 87",
        "station_city": "BĘDZINO", "station_postcode": "76-037",
        "station_ref": "4282",
        "date": "2026-07-03", "time": "15:56", "odometer_km": 31462,
        "fuel_name": "", "fuel_volume_l": 52.47, "fuel_price_per_l": 0,
        "fuel_total": 357.85, "currency": "PLN", "non_fuel_items": [],
    }, "PB95")
    assert n["station"] == "ORLEN Będzino"  # surowy fallback, nie złożony
    assert n["station_brand"] == "Orlen"  # kanonizowane z ORLEN
    assert n["station_street"] == "Będzino 87"  # kanonizowane z BĘDZINO 87
    assert n["station_city"] == "Będzino"
    assert n["station_postcode"] == "76-037"
    assert n["station_ref"] == "4282"


def test_canon_brand_and_place_preserve_mixed_case_and_diacritics():
    """Kanonizacja (0.16.1, Krok 3) nie psuje wejścia, które model już oddał
    poprawnie — tylko WERSALIKI dostają .title()/mapę marek."""
    assert receipts._canon_brand("Orlen") == "Orlen"
    assert receipts._canon_brand("ORLEN") == "Orlen"
    assert receipts._canon_brand("PKN ORLEN") == "Orlen"
    assert receipts._canon_brand("Circle K") == "Circle K"
    assert receipts._canon_brand(None) is None
    assert receipts._canon_place("Maślicka 218") == "Maślicka 218"
    assert receipts._canon_place("BĘDZINO 87") == "Będzino 87"
    assert receipts._canon_place(None) is None


def test_normalize_falls_back_to_station_name_without_address():
    n = receipts.normalize(dict(FLEET_PARSED), "PB95")
    assert n["station"] == "Stacja Testowa"  # brak pól adresu — stary sposób
    assert n["station_brand"] is None
    assert n["station_street"] is None
    assert n["station_ref"] is None


def test_prompt_teaches_model_to_skip_company_headquarters():
    # Regresja: model brał adres centrali w Płocku zamiast adresu stacji
    # (wszystkie 4 skany Orlenu w bazie dały gołe "ORLEN Wrocław"/"ORLEN
    # Będzino" — bez adresu, patrz plan, przyczyna nr 2).
    assert "CENTRALA" in receipts.PROMPT
    assert "STACJI" in receipts.PROMPT


def test_structure_has_station_address_fields():
    props = receipts.STRUCTURE["properties"]
    for field in ("station_brand", "station_street", "station_city",
                  "station_postcode", "station_ref"):
        assert field in props


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


# ── analyze(): łańcuch providerów (0.15.0) ─────────────────────────────────
# gemini (bezpośrednio) -> local (freellmapi) -> llmvision (przez HA).
# Regresja: fallback modeli był martwy od 0.5.1 — call_service() na HTTP 500
# zwracał None, analyze() rzucał natychmiast zamiast próbować kolejny model.

FULL_CONFIG = {
    "gemini_api_key": "gem-key", "gemini_model": "",
    "local_llm_base_url": "http://192.168.0.106:3003/v1",
    "local_llm_api_key": "local-key", "local_llm_model": "gemini-3.1-flash-lite",
}


def test_analyze_gemini_primary_succeeds_without_touching_other_links(monkeypatch):
    monkeypatch.setattr(receipts.vision, "call_gemini",
                        lambda *a, **kw: dict(FLEET_PARSED))
    monkeypatch.setattr(receipts.vision, "call_local",
                        lambda *a, **kw: pytest.fail("local nie powinno być wołane"))
    monkeypatch.setattr(receipts.ha_client, "find_config_entry",
                        lambda *a, **kw: pytest.fail("llmvision nie powinno być wołane"))

    result = receipts.analyze("img.jpg", FULL_CONFIG)
    assert result == FLEET_PARSED


def test_analyze_falls_back_to_local_when_gemini_fails(monkeypatch):
    monkeypatch.setattr(receipts.vision, "call_gemini",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            receipts.vision.VisionError("gemini: HTTP 429 — quota")))
    monkeypatch.setattr(receipts.vision, "call_local",
                        lambda *a, **kw: dict(FLEET_PARSED))
    monkeypatch.setattr(receipts.ha_client, "find_config_entry",
                        lambda *a, **kw: pytest.fail("llmvision nie powinno być wołane"))

    result = receipts.analyze("img.jpg", FULL_CONFIG)
    assert result == FLEET_PARSED


def test_analyze_falls_back_to_llmvision_when_gemini_and_local_fail(monkeypatch):
    monkeypatch.setattr(receipts.vision, "call_gemini",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            receipts.vision.VisionError("gemini: HTTP 429 — quota")))
    monkeypatch.setattr(receipts.vision, "call_local",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            receipts.vision.VisionError("freellmapi: HTTP 502 — provider_error")))
    monkeypatch.setattr(receipts.ha_client, "find_config_entry", lambda domain: "entry-1")
    monkeypatch.setattr(receipts.ha_client, "call_service",
                        lambda *a, **kw: {"service_response": {
                            "structured_response": dict(FLEET_PARSED)}})

    result = receipts.analyze("img.jpg", FULL_CONFIG)
    assert result == FLEET_PARSED


def test_analyze_skips_local_when_not_configured(monkeypatch):
    monkeypatch.setattr(receipts.vision, "call_gemini",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            receipts.vision.VisionError("gemini: HTTP 429 — quota")))
    monkeypatch.setattr(receipts.vision, "call_local",
                        lambda *a, **kw: pytest.fail("local bez configu nie powinno być wołane"))
    monkeypatch.setattr(receipts.ha_client, "find_config_entry", lambda domain: "entry-1")
    monkeypatch.setattr(receipts.ha_client, "call_service",
                        lambda *a, **kw: {"service_response": {
                            "structured_response": dict(FLEET_PARSED)}})

    cfg = dict(FULL_CONFIG, local_llm_base_url="", local_llm_api_key="")
    result = receipts.analyze("img.jpg", cfg)
    assert result == FLEET_PARSED


def test_analyze_llmvision_tries_next_model_after_http_500(monkeypatch):
    """Regresja martwego fallbacku (0.5.1-0.14.0): call_service zwracał None
    na HTTP 500, analyze() rzucał natychmiast zamiast próbować drugi model."""
    monkeypatch.setattr(receipts.vision, "call_gemini",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            receipts.vision.VisionError("gemini: brak klucza API")))
    monkeypatch.setattr(receipts.ha_client, "find_config_entry", lambda domain: "entry-1")

    calls = []
    def fake_call_service(domain, service, data, **kw):
        calls.append(data.get("model"))
        if len(calls) == 1:
            return None  # HTTP 500 z ha_client.call_service
        return {"service_response": {"structured_response": dict(FLEET_PARSED)}}
    monkeypatch.setattr(receipts.ha_client, "call_service", fake_call_service)

    cfg = dict(FULL_CONFIG, local_llm_base_url="", local_llm_api_key="")
    result = receipts.analyze("img.jpg", cfg)
    assert result == FLEET_PARSED
    assert len(calls) == 2  # pierwszy model padł, drugi zadziałał
    assert calls[0] != calls[1]


def test_analyze_all_links_fail_raises_with_all_reasons(monkeypatch):
    monkeypatch.setattr(receipts.vision, "call_gemini",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            receipts.vision.VisionError("gemini: HTTP 429 — quota")))
    monkeypatch.setattr(receipts.vision, "call_local",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            receipts.vision.VisionError("freellmapi: HTTP 502 — provider_error")))
    monkeypatch.setattr(receipts.ha_client, "find_config_entry", lambda domain: "entry-1")
    monkeypatch.setattr(receipts.ha_client, "call_service", lambda *a, **kw: None)

    with pytest.raises(receipts.ReceiptError) as exc:
        receipts.analyze("img.jpg", FULL_CONFIG)
    msg = str(exc.value)
    assert "gemini" in msg and "quota" in msg
    assert "freellmapi" in msg and "provider_error" in msg


def test_analyze_no_providers_configured_raises_clear_message(monkeypatch):
    monkeypatch.setattr(receipts.ha_client, "find_config_entry", lambda domain: None)
    with pytest.raises(receipts.ReceiptError):
        receipts.analyze("img.jpg", {})


def test_dead_model_gemini_2_5_flash_lite_not_in_models():
    # HTTP 404 „no longer available to new users" na nowych kluczach (zmierzone
    # 2026-08-14, docs/PLAN-0.15.0-vision.md Krok 0) — nie może wrócić do listy.
    assert "gemini-2.5-flash-lite" not in receipts.MODELS


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "web.db")
    c = dbm.get_conn(db_path)
    dbm.migrate(c)
    dbm.ensure_vehicle(c, "Testowy", 66.0, "PB95")
    c.close()
    app = create_app(
        db_path=db_path,
        config={"monthly_budget": 0.0, "default_fuel_type": "PB95",
                "vehicle_name": "Testowy",
                "share_dir": str(tmp_path / "share")})
    app.testing = True
    monkeypatch.setattr(receipts, "analyze", lambda path, config: dict(FLEET_PARSED))
    return app.test_client()


def _parse_receipt(client):
    return client.post("/api/receipts/parse", data={
        "file": (io.BytesIO(b"fake-jpeg-bytes"), "receipt.jpg"),
    }, content_type="multipart/form-data")


def test_parse_endpoint_saves_and_prefills(client):
    r = _parse_receipt(client)
    assert r.status_code == 200
    body = r.get_json()
    assert body["parsed"]["odometer"] == 12345
    assert body["parsed"]["volume_l"] == 45.5
    aid = body["attachment_id"]

    # Plik jest serwowany z powrotem
    img = client.get(f"/api/attachments/{aid}")
    assert img.status_code == 200
    assert img.data == b"fake-jpeg-bytes"


def test_fillup_links_attachment(client):
    aid = _parse_receipt(client).get_json()["attachment_id"]
    r = client.post("/api/fillups", json={
        "date": "2026-01-15T12:34", "odometer": 12345, "volume_l": 45.5,
        "total_cost": 300.30, "full_tank": True, "attachment_id": aid,
    })
    assert r.status_code == 201
    rows = client.get("/api/fillups").get_json()
    assert rows[0]["attachment_id"] == aid


def test_expense_links_attachment(client):
    aid = _parse_receipt(client).get_json()["attachment_id"]
    r = client.post("/api/expenses", json={
        "date": "2026-01-15T12:34", "cost": 39.99,
        "description": "AdBlue", "attachment_id": aid,
    })
    assert r.status_code == 201
    rows = client.get("/api/expenses").get_json()
    assert rows[0]["attachment_id"] == aid


def test_parse_endpoint_resolves_station_from_receipt_address(client, monkeypatch):
    """0.16.0: /api/receipts/parse geokoduje adres z paragonu i zwraca
    stację z PRAWDZIWYMI współrzędnymi — nie z pozycji telefonu. 0.16.1:
    receipts.py już nie importuje stations (Krok 7) — resolve_station jest
    wołane z web.py (stn), więc stub trafia w fuel_tracker.stations.geocode
    wprost, nie przez receipts.stations."""
    from fuel_tracker import stations as stn_mod
    parsed_with_address = dict(FLEET_PARSED, station_name="ORLEN Będzino",
                               station_brand="Orlen", station_street="Będzino 87",
                               station_city="Będzino", station_postcode="76-037",
                               station_ref="4282")
    monkeypatch.setattr(receipts, "analyze",
                        lambda path, config: parsed_with_address)
    monkeypatch.setattr(stn_mod.geocode, "geocode_address",
                        lambda *a, **kw: (54.2088119, 15.9835218))

    body = _parse_receipt(client).get_json()["parsed"]
    assert body["station"] == "Będzino 87, Będzino - Orlen"
    assert body["station_source"] == "address"
    assert body["latitude"] == 54.2088119
    assert body["longitude"] == 15.9835218
    # Krok 1b: skan JEST TYLKO podglądem — nie tworzy stacji. Tworzenie
    # dzieje się wyłącznie przy zapisie tankowania (web.py: _remember_station).
    assert client.get("/api/stations").get_json() == []


def test_parse_endpoint_does_not_create_station_on_geocode_miss(client, monkeypatch):
    """Regresja 0.16.0: skan bez trafienia geokodowania tworzył stację z
    NULL współrzędnymi i source='legacy', permanentnie — nawet porzucony
    bez zapisu. 0.16.1: żaden skan nie zapisuje do `stations`."""
    from fuel_tracker import stations as stn_mod
    parsed_with_address = dict(FLEET_PARSED, station_name="Nieznana",
                               station_brand="Orlen", station_street="Nigdzie 1",
                               station_city="Nikąd")
    monkeypatch.setattr(receipts, "analyze",
                        lambda path, config: parsed_with_address)
    monkeypatch.setattr(stn_mod.geocode, "geocode_address",
                        lambda *a, **kw: None)
    _parse_receipt(client)
    assert client.get("/api/stations").get_json() == []


def test_parse_endpoint_keeps_attachment_on_analyze_error(client, monkeypatch):
    def boom(path, config):
        raise receipts.ReceiptError("Model nie odpowiedział")
    monkeypatch.setattr(receipts, "analyze", boom)
    r = _parse_receipt(client)
    assert r.status_code == 502
    body = r.get_json()
    # Zdjęcie zostało — można je podpiąć do ręcznego wpisu
    assert body["attachment_id"]
    assert client.get(f"/api/attachments/{body['attachment_id']}").status_code == 200
