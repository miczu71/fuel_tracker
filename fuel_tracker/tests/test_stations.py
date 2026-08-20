"""Stacje: haversine, dopasowanie GPS, upsert, agregaty mapy, backfill."""
import pytest

from fuel_tracker import db as dbm, stations as stn

# Dwa fikcyjne punkty na tym samym południku — ~3.6 km w linii prostej.
ST_A = (50.0000, 20.0000)
ST_B = (50.0324, 20.0000)


def test_haversine_known_distance():
    d = stn.haversine_m(*ST_A, *ST_B)
    assert 3000 < d < 4000  # ~3.6 km
    assert stn.haversine_m(*ST_A, *ST_A) == 0


def test_upsert_station_insert_and_fill_gaps(conn):
    row = stn.upsert_station(conn, "Stacja A")
    sid = row["id"]
    assert row["latitude"] is None

    # Drugi zapis uzupełnia brakujące współrzędne i markę…
    row = stn.upsert_station(conn, "Stacja A", *ST_A, brand="MarkaA")
    assert row["id"] == sid
    assert row["latitude"] == ST_A[0] and row["brand"] == "MarkaA"

    # …ale nie nadpisuje już ustawionych.
    row = stn.upsert_station(conn, "Stacja A", 0.0, 0.0, brand="MarkaB")
    assert row["latitude"] == ST_A[0] and row["brand"] == "MarkaA"


def test_nearest_station_radius(conn):
    stn.upsert_station(conn, "Stacja A", *ST_A)
    near = stn.nearest_station(conn, ST_A[0] + 0.001, ST_A[1])  # ~110 m
    assert near and near["name"] == "Stacja A"
    assert near["distance_m"] < 300
    assert stn.nearest_station(conn, *ST_B) is None  # ~3.6 km — poza promieniem


def test_map_data_aggregates(conn, vehicle_id):
    stn.upsert_station(conn, "Stacja A", *ST_A)
    for i, (paid, cur) in enumerate([("fleet_card", "PLN"), ("own", "PLN"),
                                     ("fleet_card", "EUR")]):
        conn.execute(
            """INSERT INTO fillups (vehicle_id, date, odometer, volume_l,
               price_per_l, total_cost, station, paid_by, currency)
               VALUES (?,?,?,40,6,240,'Stacja A',?,?)""",
            (vehicle_id, f"2025-0{i+1}-01 12:00", 1000 + 500 * i, paid, cur))
    conn.commit()
    data = stn.map_data(conn, vehicle_id)
    s = next(d for d in data if d["name"] == "Stacja A")
    assert s["visits"] == 3
    assert s["total_cost"] == 720.0
    assert s["own_paid"] == 1
    assert s["foreign_cnt"] == 1
    assert s["last_date"].startswith("2025-03-01")


def test_migration_backfills_stations(tmp_path):
    c = dbm.get_conn(str(tmp_path / "old.db"))
    c.executescript(dbm._MIGRATIONS[0])
    c.execute("PRAGMA user_version = 1")
    c.execute("INSERT INTO vehicles (name) VALUES ('t')")
    c.execute("""INSERT INTO fillups (vehicle_id, date, odometer, volume_l,
                 price_per_l, total_cost, station, latitude, longitude)
                 VALUES (1,'2025-01-01',100,40,6,240,'Stacja B',?,?)""",
              ST_A)
    c.commit()
    dbm.migrate(c)
    row = c.execute("SELECT * FROM stations WHERE name = 'Stacja B'").fetchone()
    assert row and row["latitude"] == ST_A[0] and row["country"] == "PL"
    assert c.execute("SELECT paid_by FROM fillups").fetchone()[0] == "fleet_card"
    c.close()


def test_migration_v11_adds_station_columns_without_losing_data(tmp_path):
    """Baza na user_version=10 (przed 0.16.0) z dwiema stacjami (jedna z
    ref=NULL) przechodzi na v11 bez utraty wierszy; idx_stations_ref
    (UNIQUE WHERE ref IS NOT NULL) nie wybucha na wielu NULL-ach."""
    c = dbm.get_conn(str(tmp_path / "old_v10.db"))
    for script in dbm._MIGRATIONS[:10]:
        c.executescript(script)
    c.execute("PRAGMA user_version = 10")
    c.execute("INSERT INTO vehicles (name) VALUES ('t')")
    c.execute("INSERT INTO stations (name, latitude, longitude) "
              "VALUES ('Stacja A', ?, ?)", ST_A)
    c.execute("INSERT INTO stations (name, latitude, longitude) "
              "VALUES ('Stacja B', ?, ?)", ST_B)
    c.commit()

    dbm.migrate(c)

    assert c.execute("PRAGMA user_version").fetchone()[0] == len(dbm._MIGRATIONS)
    rows = c.execute("SELECT name, ref, source FROM stations ORDER BY name"
                     ).fetchall()
    assert [r["name"] for r in rows] == ["Stacja A", "Stacja B"]
    assert all(r["ref"] is None for r in rows)
    assert all(r["source"] == "legacy" for r in rows)
    # Druga stacja z ref=NULL nie łamie unikalności (brand, ref).
    c.execute("INSERT INTO stations (name, brand, ref) VALUES ('Stacja C', NULL, NULL)")
    c.commit()
    c.close()


def test_overpass_lookup_survives_network_failure(monkeypatch):
    def boom(*a, **kw):
        raise OSError("network down")
    monkeypatch.setattr(stn.requests, "post", boom)
    assert stn.overpass_lookup(*ST_A) == []


def test_overpass_lookup_builds_name_from_osm_address_tags(monkeypatch):
    """Regresja: tags.name samo w sobie bywa gołe "Orlen" dla całej sieci —
    wszystkie stacje kolidowałyby pod jedną nazwą (patrz
    docs/PLAN-0.16.0-stations.md, przyczyna nr 7). Nazwa musi wyjść z
    addr:street/housenumber/city + brand, jak dla Maślickiej 218."""
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"elements": [{
                "type": "node", "lat": ST_A[0], "lon": ST_A[1],
                "tags": {"name": "Orlen", "brand": "Orlen",
                        "addr:street": "Maślicka", "addr:housenumber": "218",
                        "addr:city": "Wrocław", "addr:postcode": "54-104"},
            }]}
    monkeypatch.setattr(stn.requests, "post", lambda *a, **kw: FakeResp())
    results = stn.overpass_lookup(*ST_A)
    assert results[0]["name"] == "Maślicka 218, Wrocław - Orlen"
    assert results[0]["brand"] == "Orlen"
    assert results[0]["street"] == "Maślicka 218"
    assert results[0]["city"] == "Wrocław"


# ── compose_name / resolve_station (0.16.0) ─────────────────────────────

def test_compose_name_degrades_gracefully():
    assert stn.compose_name("Maślicka 218", "Wrocław", "Orlen") == \
        "Maślicka 218, Wrocław - Orlen"
    assert stn.compose_name(None, "Wrocław", "Orlen") == "Wrocław - Orlen"
    assert stn.compose_name(None, None, "Orlen") == "Orlen"
    assert stn.compose_name("Maślicka 218", "Wrocław", None) == \
        "Maślicka 218, Wrocław"
    assert stn.compose_name(None, None, None) is None
    assert stn.compose_name("", "", "") is None


def test_resolve_station_by_ref_is_deterministic(conn):
    stn.upsert_station(conn, "Będzino 87, Będzino - Orlen", 54.2088, 15.9835,
                       brand="Orlen", ref="4282", source="nominatim")
    r = stn.resolve_station(conn, ref="4282", brand="Orlen")
    assert r["matched"] is True
    assert r["source"] == "ref"
    assert r["name"] == "Będzino 87, Będzino - Orlen"


def test_resolve_station_geocodes_new_address(conn, monkeypatch):
    monkeypatch.setattr(stn.geocode, "geocode_address",
                        lambda *a, **kw: (54.2088, 15.9835))
    r = stn.resolve_station(conn, brand="Orlen", street="Będzino 87",
                            city="Będzino", postcode="76-037")
    assert r["matched"] is True
    assert r["name"] == "Będzino 87, Będzino - Orlen"
    assert r["latitude"] == 54.2088
    row = conn.execute("SELECT * FROM stations WHERE name = ?",
                       (r["name"],)).fetchone()
    assert row["source"] == "nominatim"
    assert row["street"] == "Będzino 87"


def test_resolve_station_ref_takes_priority_over_address(conn):
    # Adres inny niż to, co jest już zapisane pod tym ref+brand — ref wygrywa.
    stn.upsert_station(conn, "Będzino 87, Będzino - Orlen", 54.2088, 15.9835,
                       brand="Orlen", ref="4282", source="nominatim")
    r = stn.resolve_station(conn, ref="4282", brand="Orlen",
                            street="Inna 1", city="Inne Miasto")
    assert r["name"] == "Będzino 87, Będzino - Orlen"
    assert r["source"] == "ref"


def test_resolve_station_gps_when_no_address(conn):
    stn.upsert_station(conn, "Stacja A", *ST_A, source="nominatim")
    r = stn.resolve_station(conn, gps=(ST_A[0] + 0.0005, ST_A[1]))
    assert r["matched"] is True
    assert r["source"] == "gps"
    assert r["name"] == "Stacja A"


def test_resolve_station_name_hint_never_matches(conn):
    """Ostatnio użyta nazwa to TYLKO sugestia — nigdy dopasowanie (silent
    fallback usunięty, patrz plan przyczyna nr 4)."""
    r = stn.resolve_station(conn, name_hint="Stacja z poprzedniego wpisu")
    assert r["matched"] is False
    assert r["station_id"] is None
    assert r["name"] == "Stacja z poprzedniego wpisu"


def test_resolve_station_nothing_known_returns_empty(conn):
    r = stn.resolve_station(conn)
    assert r["matched"] is False
    assert r["station_id"] is None
    assert r["name"] is None


def test_upsert_station_dedup_by_brand_and_ref(conn):
    sid = stn.upsert_station(conn, "Będzino 87, Będzino - Orlen",
                             brand="Orlen", ref="4282")["id"]
    same = stn.upsert_station(conn, "Będzino 87, Będzino - Orlen",
                              brand="Orlen", ref="4282")["id"]
    assert same == sid
    assert conn.execute(
        "SELECT COUNT(*) FROM stations WHERE ref = '4282'"
    ).fetchone()[0] == 1


def test_upsert_station_gps_phone_never_downgrades_address_source(conn):
    """Regresja: pozycja telefonu w chwili tankowania bywa km od stacji
    (przyczyna nr 5) — nie może nadpisać współrzędnych z adresu/geokodowania,
    nawet gdy trafia jako kolejny zapis tej samej stacji."""
    sid = stn.upsert_station(conn, "Stacja A", 54.2088, 15.9835,
                             source="nominatim")["id"]
    stn.upsert_station(conn, "Stacja A", 54.30, 16.00, source="gps_phone")
    row = conn.execute("SELECT * FROM stations WHERE id = ?", (sid,)).fetchone()
    assert row["latitude"] == 54.2088
    assert row["source"] == "nominatim"


def test_upsert_station_gps_phone_never_downgrades_legacy_source(conn):
    """Regresja 0.16.0 (bug bloker naprawiony 0.16.1, Krok 1a): _COORD_
    PRIORITY dawało gps_phone(1) > legacy(0), więc KAŻDA z 12 istniejących
    produkcyjnych stacji (source='legacy' po migracji v11) miała współrzędne
    nadpisane pozycją telefonu przy najbliższym zapisie tankowania — dokładnie
    ten sam bug, który 0.16.0 miało naprawić. Jedyny istniejący test regresji
    pokrywał tylko parę nominatim/gps_phone i tego nie łapał."""
    sid = stn.upsert_station(conn, "Stacja A", 54.2088, 15.9835,
                             source="legacy")["id"]
    stn.upsert_station(conn, "Stacja A", 54.30, 16.00, source="gps_phone")
    row = conn.execute("SELECT * FROM stations WHERE id = ?", (sid,)).fetchone()
    assert row["latitude"] == 54.2088
    assert row["source"] == "legacy"


def test_upsert_station_nominatim_still_upgrades_legacy_source(conn):
    """Korekta (nie tylko uzupełnienie) musi nadal działać: adres z paragonu
    (nominatim) POPRAWIA stację zapisaną wcześniej jako legacy/gps_phone —
    to właśnie ta ścieżka naprawia stacje uszkodzone przez bug 1a."""
    sid = stn.upsert_station(conn, "Stacja A", 54.30, 16.00,
                             source="legacy")["id"]
    stn.upsert_station(conn, "Stacja A", 54.2088, 15.9835, source="nominatim")
    row = conn.execute("SELECT * FROM stations WHERE id = ?", (sid,)).fetchone()
    assert row["latitude"] == 54.2088
    assert row["source"] == "nominatim"


def test_upsert_station_gps_phone_sets_coords_on_brand_new_station(conn):
    # Stacja bez adresu — pozycja telefonu jest lepsza niż nic.
    row = stn.upsert_station(conn, "Stacja Bez Adresu", 54.30, 16.00,
                             source="gps_phone")
    assert row["latitude"] == 54.30


# ── porządki: duplikaty + braki (0.16.0, klastry+repair 0.16.1) ─────────

def test_find_duplicate_stations_and_apply_merge(conn, vehicle_id):
    ghost = stn.upsert_station(conn, "Wrocław - Orlen", *ST_A)["id"]
    real = stn.upsert_station(conn, "Szybowcowa 27, Wrocław - Orlen", *ST_A)["id"]
    conn.execute(
        """INSERT INTO fillups (vehicle_id, date, odometer, volume_l,
           price_per_l, total_cost, station)
           VALUES (?,?,?,40,6,240,?)""",
        (vehicle_id, "2025-01-01", 1000, "Szybowcowa 27, Wrocław - Orlen"))
    conn.commit()

    dups = stn.find_duplicate_stations(conn)
    assert len(dups) == 1
    d = dups[0]
    assert {d["keep_id"], d["remove_id"]} == {ghost, real}
    assert d["keep_id"] == real  # więcej tankowań zostaje

    stn.apply_merge(conn, d["keep_id"], d["remove_id"])
    assert conn.execute("SELECT COUNT(*) FROM stations").fetchone()[0] == 1
    row = conn.execute("SELECT station FROM fillups").fetchone()
    assert row["station"] == "Szybowcowa 27, Wrocław - Orlen"


def test_apply_merge_rejects_unknown_ids(conn):
    with pytest.raises(ValueError):
        stn.apply_merge(conn, 1, 2)


def test_apply_merge_keeps_removed_identity_and_better_coords(conn):
    """Regresja 0.16.0 (naprawiona 0.16.1, Krok 4a): DELETE kasował
    bezpowrotnie ref/adres/lepsze współrzędne usuwanej stacji, jeśli to ona
    miała mniej tankowań — na produkcji dokładnie ten scenariusz (legacy
    z pozycją telefonu i więcej tankowań kontra świeżo zgeokodowana
    z paragonu i 1 tankowaniem)."""
    keep = stn.upsert_station(conn, "Wrocław - Orlen", 54.30, 16.00,
                              source="legacy")["id"]
    remove = stn.upsert_station(
        conn, "Maślicka 218, Wrocław - Orlen", 54.2088, 15.9835,
        brand="Orlen", ref="4282", street="Maślicka 218", city="Wrocław",
        postcode="54-104", source="nominatim")["id"]
    stn.apply_merge(conn, keep, remove)
    row = conn.execute("SELECT * FROM stations WHERE id = ?", (keep,)).fetchone()
    assert row["ref"] == "4282" and row["street"] == "Maślicka 218"
    assert row["postcode"] == "54-104"
    assert row["latitude"] == 54.2088  # lepsze źródło (nominatim) przejęte
    assert row["source"] == "nominatim"


def test_apply_merge_does_not_overwrite_ref_on_conflict(conn):
    # Trzecia, niepowiązana stacja trzyma już (Orlen, 9999) — transfer refa
    # z "remove" (inny brand, ten sam numer) na "keep" (już ma brand=Orlen)
    # utworzyłby duplikat pary (brand, ref) łamiący UNIQUE — musi być pominięty.
    stn.upsert_station(conn, "Inna Stacja", brand="Orlen", ref="9999")
    keep = stn.upsert_station(conn, "Wrocław - Orlen", brand="Orlen")["id"]
    remove = stn.upsert_station(conn, "Duplikat", brand="BP", ref="9999")["id"]
    stn.apply_merge(conn, keep, remove)
    row = conn.execute("SELECT ref FROM stations WHERE id = ?", (keep,)).fetchone()
    assert row["ref"] is None  # kolidowałoby z UNIQUE(brand, ref) — nie przeniesione


def test_find_duplicate_stations_clusters_three_into_one_pair_set(conn):
    """Regresja 0.16.0 (naprawiona 0.16.1, Krok 4c): trzy stacje w klastrze
    dawały trzy PARY; zaznaczenie wszystkich kończyło się jednym scaleniem
    i dwoma ValueError("Stacja nie istnieje"), bo wcześniejsze scalenie
    kasowało wiersz, na który wskazywała kolejna para."""
    a = stn.upsert_station(conn, "Stacja A", *ST_A)["id"]
    b = stn.upsert_station(conn, "Stacja B", ST_A[0] + 0.0003, ST_A[1])["id"]
    c = stn.upsert_station(conn, "Stacja C", ST_A[0] + 0.0006, ST_A[1])["id"]

    dups = stn.find_duplicate_stations(conn)
    assert len(dups) == 2  # jeden ocalały na klaster, nie 3 pary
    keep_ids = {d["keep_id"] for d in dups}
    assert len(keep_ids) == 1
    keep_id = keep_ids.pop()
    assert {keep_id, *[d["remove_id"] for d in dups]} == {a, b, c}

    for d in dups:
        stn.apply_merge(conn, d["keep_id"], d["remove_id"])  # nie rzuca
    assert conn.execute("SELECT COUNT(*) FROM stations").fetchone()[0] == 1


def test_find_enrichable_stations_and_apply(conn, monkeypatch):
    sid = stn.upsert_station(conn, "Wrocław - Orlen", *ST_A)["id"]  # brak brand/street
    monkeypatch.setattr(stn, "_overpass_raw", lambda lat, lon, radius_m=None: [
        {"name": "Orlen", "brand": "Orlen", "street": "Maślicka 218",
         "city": "Wrocław", "postcode": "54-104", "latitude": ST_A[0],
         "longitude": ST_A[1], "distance_m": 5},
    ])
    result = stn.find_enrichable_stations(conn)
    props = result["proposals"]
    assert len(props) == 1
    assert result["checked"] == 1 and result["remaining"] == 0
    assert result["errors"] == []
    assert props[0]["station_id"] == sid
    assert props[0]["proposed_name"] == "Maślicka 218, Wrocław - Orlen"

    stn.apply_enrichment(conn, sid, name=props[0]["proposed_name"],
                         brand=props[0]["brand"], street=props[0]["street"],
                         city=props[0]["city"], postcode=props[0]["postcode"],
                         latitude=props[0]["latitude"],
                         longitude=props[0]["longitude"])
    row = conn.execute("SELECT * FROM stations WHERE id = ?", (sid,)).fetchone()
    assert row["name"] == "Maślicka 218, Wrocław - Orlen"
    assert row["brand"] == "Orlen"
    assert row["source"] == "osm"  # legacy < osm — współrzędne przejęte


def test_find_enrichable_stations_respects_limit(conn, monkeypatch):
    for i in range(3):
        stn.upsert_station(conn, f"Stacja {i}", ST_A[0] + i * 0.01, ST_A[1])
    calls = []

    def fake_raw(lat, lon, radius_m=None):
        calls.append((lat, lon))
        return []
    monkeypatch.setattr(stn, "_overpass_raw", fake_raw)
    result = stn.find_enrichable_stations(conn, limit=2)
    assert result["checked"] == 2 and result["remaining"] == 1
    assert len(calls) == 2


def test_find_enrichable_stations_reports_overpass_errors(conn, monkeypatch):
    stn.upsert_station(conn, "Stacja A", *ST_A)

    def boom(lat, lon, radius_m=None):
        raise OSError("network down")
    monkeypatch.setattr(stn, "_overpass_raw", boom)
    result = stn.find_enrichable_stations(conn)
    assert result["proposals"] == []
    assert len(result["errors"]) == 1


def test_find_enrichable_stations_skips_proposal_without_street_or_city(conn, monkeypatch):
    """Krok 4b: propozycja bez ulicy i bez miasta (goła marka) nie jest
    lepsza niż to, co już mamy — apply_enrichment by ją przepisała jako
    rename całej historii tankowań, np. "Wrocław - Orlen" → "Orlen"."""
    stn.upsert_station(conn, "Wrocław - Orlen", *ST_A)
    monkeypatch.setattr(stn, "_overpass_raw", lambda lat, lon, radius_m=None: [
        {"name": "Orlen", "brand": "Orlen", "street": None, "city": None,
         "postcode": None, "latitude": ST_A[0], "longitude": ST_A[1],
         "distance_m": 5},
    ])
    result = stn.find_enrichable_stations(conn)
    assert result["proposals"] == []


def test_apply_enrichment_rewrites_fillups_station_name(conn, vehicle_id):
    sid = stn.upsert_station(conn, "Wrocław - Orlen", *ST_A)["id"]
    conn.execute(
        """INSERT INTO fillups (vehicle_id, date, odometer, volume_l,
           price_per_l, total_cost, station)
           VALUES (?,?,?,40,6,240,'Wrocław - Orlen')""",
        (vehicle_id, "2025-01-01", 1000))
    conn.commit()
    stn.apply_enrichment(conn, sid, name="Maślicka 218, Wrocław - Orlen",
                         brand="Orlen")
    row = conn.execute("SELECT station FROM fillups").fetchone()
    assert row["station"] == "Maślicka 218, Wrocław - Orlen"


def test_apply_enrichment_does_not_change_source_without_coords(conn):
    """Krok 4b: source zmienia się TYLKO razem ze współrzędnymi — dawniej
    'source = osm' był bezwarunkowy, więc wzbogacenie samą marką/adresem
    (bez współrzędnych) oznaczało istniejącą pozycję jako zaufaną i
    blokowało jej późniejszą korektę."""
    sid = stn.upsert_station(conn, "Wrocław - Orlen", 54.30, 16.00,
                             source="gps_phone")["id"]
    stn.apply_enrichment(conn, sid, name="Maślicka 218, Wrocław - Orlen",
                         brand="Orlen", street="Maślicka 218")
    row = conn.execute("SELECT * FROM stations WHERE id = ?", (sid,)).fetchone()
    assert row["source"] == "gps_phone"
    assert row["latitude"] == 54.30  # bez podanych coords — bez zmiany


def test_apply_enrichment_rejects_name_collision(conn):
    stn.upsert_station(conn, "Maślicka 218, Wrocław - Orlen", *ST_A)
    sid = stn.upsert_station(conn, "Wrocław - Orlen",
                             ST_A[0] + 0.001, ST_A[1])["id"]
    with pytest.raises(ValueError):
        stn.apply_enrichment(conn, sid, name="Maślicka 218, Wrocław - Orlen")
