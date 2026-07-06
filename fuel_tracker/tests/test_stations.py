"""Stacje: haversine, dopasowanie GPS, upsert, agregaty mapy, backfill."""
from fuel_tracker import db as dbm, stations as stn

# Orlen Legnicka i Rynek we Wrocławiu — ~1.9 km w linii prostej.
ORLEN = (51.1152, 16.9812)
RYNEK = (51.1100, 17.0320)


def test_haversine_known_distance():
    d = stn.haversine_m(*ORLEN, *RYNEK)
    assert 3000 < d < 4000  # ~3.6 km
    assert stn.haversine_m(*ORLEN, *ORLEN) == 0


def test_upsert_station_insert_and_fill_gaps(conn):
    sid = stn.upsert_station(conn, "Orlen Legnicka")
    row = conn.execute("SELECT * FROM stations WHERE id = ?", (sid,)).fetchone()
    assert row["latitude"] is None

    # Drugi zapis uzupełnia brakujące współrzędne i markę…
    assert stn.upsert_station(conn, "Orlen Legnicka", *ORLEN, brand="Orlen") == sid
    row = conn.execute("SELECT * FROM stations WHERE id = ?", (sid,)).fetchone()
    assert row["latitude"] == ORLEN[0] and row["brand"] == "Orlen"

    # …ale nie nadpisuje już ustawionych.
    stn.upsert_station(conn, "Orlen Legnicka", 0.0, 0.0, brand="Shell")
    row = conn.execute("SELECT * FROM stations WHERE id = ?", (sid,)).fetchone()
    assert row["latitude"] == ORLEN[0] and row["brand"] == "Orlen"


def test_nearest_station_radius(conn):
    stn.upsert_station(conn, "Orlen Legnicka", *ORLEN)
    near = stn.nearest_station(conn, ORLEN[0] + 0.001, ORLEN[1])  # ~110 m
    assert near and near["name"] == "Orlen Legnicka"
    assert near["distance_m"] < 300
    assert stn.nearest_station(conn, *RYNEK) is None  # ~3.6 km — poza promieniem


def test_map_data_aggregates(conn, vehicle_id):
    stn.upsert_station(conn, "Orlen Legnicka", *ORLEN)
    for i, (paid, cur) in enumerate([("fleet_card", "PLN"), ("own", "PLN"),
                                     ("fleet_card", "EUR")]):
        conn.execute(
            """INSERT INTO fillups (vehicle_id, date, odometer, volume_l,
               price_per_l, total_cost, station, paid_by, currency)
               VALUES (?,?,?,40,6,240,'Orlen Legnicka',?,?)""",
            (vehicle_id, f"2025-0{i+1}-01 12:00", 1000 + 500 * i, paid, cur))
    conn.commit()
    data = stn.map_data(conn, vehicle_id)
    s = next(d for d in data if d["name"] == "Orlen Legnicka")
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
                 VALUES (1,'2025-01-01',100,40,6,240,'Shell Bielany',?,?)""",
              ORLEN)
    c.commit()
    dbm.migrate(c)
    row = c.execute("SELECT * FROM stations WHERE name = 'Shell Bielany'").fetchone()
    assert row and row["latitude"] == ORLEN[0] and row["country"] == "PL"
    assert c.execute("SELECT paid_by FROM fillups").fetchone()[0] == "fleet_card"
    c.close()


def test_overpass_lookup_survives_network_failure(monkeypatch):
    def boom(*a, **kw):
        raise OSError("network down")
    monkeypatch.setattr(stn.requests, "post", boom)
    assert stn.overpass_lookup(*ORLEN) == []
