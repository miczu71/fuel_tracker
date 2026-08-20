"""Stacje paliw: tożsamość+adres z paragonu, dopasowanie po GPS, lookup OSM
Overpass, dane do mapy.

resolve_station() (0.16.0, docs/PLAN-0.16.0-stations.md) to jedyne miejsce,
które decyduje JAKA stacja odpowiada tankowaniu — priorytet: numer stacji
z paragonu (ref+brand, deterministyczne) > adres z paragonu (geokodowany,
Krok 3) > pozycja GPS telefonu > ostatnio użyta nazwa (tylko jako SUGESTIA,
nigdy jako dopasowanie — pozycja telefonu bywa km od stacji, patrz plan).
"""
from __future__ import annotations

import logging
import math
import sqlite3

import requests

from . import geocode

log = logging.getLogger(__name__)

# Promień dopasowania zapisanej stacji do bieżącej pozycji (metry).
MATCH_RADIUS_M = 300
# Promień zapytania Overpass o stacje w okolicy (metry).
OVERPASS_RADIUS_M = 500
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT_S = 5
# Źródła współrzędnych, od najbardziej do najmniej wiarygodnego — pozycja
# telefonu (gps_phone) nigdy nie nadpisuje adresu ze stacji/paragonu.
_COORD_PRIORITY = {"receipt": 3, "nominatim": 3, "osm": 2, "gps_phone": 1,
                   "legacy": 0}


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Odległość po kuli ziemskiej w metrach."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_station(conn: sqlite3.Connection, lat: float, lon: float,
                    radius_m: float = MATCH_RADIUS_M) -> dict | None:
    """Najbliższa zapisana stacja w promieniu radius_m, albo None."""
    best = None
    for row in conn.execute(
        "SELECT * FROM stations WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    ):
        d = haversine_m(lat, lon, row["latitude"], row["longitude"])
        if d <= radius_m and (best is None or d < best["distance_m"]):
            best = dict(row) | {"distance_m": round(d)}
    return best


def compose_name(street: str | None, city: str | None,
                 brand: str | None) -> str | None:
    """Nazwa stacji z adresu, konwencja "{ulica} {nr}, {miasto} - {Marka}"
    (wybrana przez użytkownika — dokładnie to, co ręcznie wpisywał dla
    Orlenu). Degraduje łagodnie, gdy części brakuje:
    ulica+miasto+marka → "Maślicka 218, Wrocław - Orlen"
    miasto+marka       → "Wrocław - Orlen" (dotychczasowe zachowanie)
    tylko marka        → "Orlen"
    nic                → None
    """
    street = (street or "").strip()
    city = (city or "").strip()
    brand = (brand or "").strip()
    if street and city and brand:
        return f"{street}, {city} - {brand}"
    if city and brand:
        return f"{city} - {brand}"
    if brand:
        return brand
    if street and city:
        return f"{street}, {city}"
    return city or street or None


def _tags_to_result(tags: dict, elat: float, elon: float, lat: float,
                    lon: float) -> dict | None:
    """OSM tags → wynik podpowiedzi. Buduje nazwę z addr:* + brand zamiast
    gołego tags.name (dla Orlenu w OSM name == "Orlen" — bez tego wszystkie
    stacje sieci kolidują pod jedną nazwą, patrz plan)."""
    brand = tags.get("brand") or tags.get("name")
    street = tags.get("addr:street")
    house = tags.get("addr:housenumber")
    city = tags.get("addr:city")
    full_street = f"{street} {house}".strip() if street else None
    name = compose_name(full_street, city, brand) or tags.get("name")
    if not name:
        return None
    return {
        "name": name,
        "brand": tags.get("brand"),
        "street": full_street,
        "city": city,
        "postcode": tags.get("addr:postcode"),
        "latitude": elat,
        "longitude": elon,
        "distance_m": round(haversine_m(lat, lon, elat, elon)),
    }


def overpass_lookup(lat: float, lon: float,
                    radius_m: float = OVERPASS_RADIUS_M) -> list[dict]:
    """Stacje paliw z OSM w okolicy; pusta lista przy błędzie/timeout."""
    query = (
        f"[out:json][timeout:{OVERPASS_TIMEOUT_S}];"
        f"nwr[amenity=fuel](around:{radius_m},{lat},{lon});out center;"
    )
    try:
        resp = requests.post(OVERPASS_URL, data={"data": query},
                             timeout=OVERPASS_TIMEOUT_S + 2)
        resp.raise_for_status()
        elements = resp.json().get("elements", [])
    except Exception as exc:  # sieć/timeout — funkcja jest tylko podpowiedzią
        log.warning("Overpass niedostępny: %s", exc)
        return []
    results = []
    for el in elements:
        tags = el.get("tags", {})
        elat = el.get("lat") or el.get("center", {}).get("lat")
        elon = el.get("lon") or el.get("center", {}).get("lon")
        if elat is None or elon is None:
            continue
        r = _tags_to_result(tags, elat, elon, lat, lon)
        if r:
            results.append(r)
    results.sort(key=lambda s: s["distance_m"])
    return results


def upsert_station(conn: sqlite3.Connection, name: str,
                   lat: float | None = None, lon: float | None = None,
                   brand: str | None = None, country: str = "PL", *,
                   ref: str | None = None, street: str | None = None,
                   city: str | None = None, postcode: str | None = None,
                   source: str = "legacy") -> int:
    """Dodaje stację po nazwie lub uzupełnia brakujące dane istniejącej.
    source odróżnia jakość współrzędnych (patrz _COORD_PRIORITY) — gorsze
    źródło (np. pozycja telefonu) nigdy nie nadpisuje lepszego (adres
    ze stacji/paragonu), nawet gdy istniejące pole akurat jest puste."""
    name = name.strip()
    row = conn.execute("SELECT * FROM stations WHERE name = ?", (name,)).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO stations (name, brand, latitude, longitude, country,"
            " ref, street, city, postcode, source)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, brand, lat, lon, country, ref, street, city, postcode,
             source))
        conn.commit()
        return cur.lastrowid
    # Uzupełnij tylko braki — ręcznie ustawionych danych nie nadpisujemy.
    updates, params = [], []
    existing_prio = _COORD_PRIORITY.get(row["source"] or "legacy", 0)
    new_prio = _COORD_PRIORITY.get(source, 0)
    coords_missing = row["latitude"] is None
    coords_upgradeable = new_prio > existing_prio
    if lat is not None and (coords_missing or coords_upgradeable):
        updates += ["latitude = ?", "longitude = ?", "source = ?"]
        params += [lat, lon, source]
    if row["brand"] is None and brand:
        updates.append("brand = ?")
        params.append(brand)
    if row["ref"] is None and ref:
        updates.append("ref = ?")
        params.append(ref)
    if row["street"] is None and street:
        updates.append("street = ?")
        params.append(street)
    if row["city"] is None and city:
        updates.append("city = ?")
        params.append(city)
    if row["postcode"] is None and postcode:
        updates.append("postcode = ?")
        params.append(postcode)
    if updates:
        conn.execute(f"UPDATE stations SET {', '.join(updates)} WHERE id = ?",
                     (*params, row["id"]))
        conn.commit()
    return row["id"]


def resolve_station(conn: sqlite3.Connection, *, ref: str | None = None,
                    brand: str | None = None, street: str | None = None,
                    city: str | None = None, postcode: str | None = None,
                    gps: tuple[float, float] | None = None,
                    name_hint: str | None = None) -> dict:
    """Jedno miejsce decydujące, jaka stacja odpowiada tankowaniu. Priorytet
    źródeł (patrz moduł docstring): ref+brand > adres z paragonu (geokodowany)
    > GPS telefonu > name_hint (ostatnio użyta nazwa — TYLKO jako sugestia,
    matched=False, nigdy jako dopasowanie). Zwraca dict, station_id=None gdy
    nic się nie dało ustalić."""
    empty = {"station_id": None, "name": None, "latitude": None,
             "longitude": None, "source": None, "matched": False}

    # 1. Numer stacji z paragonu — deterministyczne, po marce+ref.
    if ref and brand:
        row = conn.execute(
            "SELECT * FROM stations WHERE brand = ? AND ref = ?",
            (brand, ref)).fetchone()
        if row is not None:
            return {"station_id": row["id"], "name": row["name"],
                    "latitude": row["latitude"], "longitude": row["longitude"],
                    "source": "ref", "matched": True}

    # 2. Adres z paragonu — złóż nazwę, dopasuj po nazwie albo geokoduj
    #    i utwórz nową stację z prawdziwymi współrzędnymi.
    composed = compose_name(street, city, brand)
    if composed:
        row = conn.execute(
            "SELECT * FROM stations WHERE name = ?", (composed,)).fetchone()
        if row is not None:
            sid = upsert_station(conn, composed, row["latitude"], row["longitude"],
                                 brand=brand, ref=ref, street=street, city=city,
                                 postcode=postcode, source=row["source"] or "legacy")
            row = conn.execute("SELECT * FROM stations WHERE id = ?",
                               (sid,)).fetchone()
            return {"station_id": row["id"], "name": row["name"],
                    "latitude": row["latitude"], "longitude": row["longitude"],
                    "source": "address", "matched": True}
        coords = geocode.geocode_address(conn, street or city, city, postcode) \
            if (street or city) else None
        lat, lon = coords if coords else (None, None)
        sid = upsert_station(conn, composed, lat, lon, brand=brand, ref=ref,
                             street=street, city=city, postcode=postcode,
                             source="nominatim" if coords else "legacy")
        row = conn.execute("SELECT * FROM stations WHERE id = ?",
                           (sid,)).fetchone()
        return {"station_id": row["id"], "name": row["name"],
                "latitude": row["latitude"], "longitude": row["longitude"],
                "source": "address", "matched": True}

    # 3. Pozycja GPS telefonu — dopasowanie do istniejącej stacji w promieniu.
    if gps:
        near = nearest_station(conn, *gps)
        if near:
            return {"station_id": near["id"], "name": near["name"],
                    "latitude": near["latitude"], "longitude": near["longitude"],
                    "source": "gps", "matched": True}

    # 4. Ostatnio użyta nazwa — TYLKO sugestia, nigdy dopasowanie (pozycja
    #    telefonu bywa km od stacji, patrz plan — silent fallback usunięty).
    if name_hint:
        return empty | {"name": name_hint, "source": "hint"}

    return empty


def list_stations(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM stations ORDER BY name")]


# ── Porządki (0.16.0): scalanie duplikatów + uzupełnianie z OSM ────────────
# Podgląd → zatwierdzenie (web.py: /api/stations/cleanup/*) — nic tu nie
# zapisuje samo z siebie, patrz apply_merge/apply_enrichment.

DUPLICATE_RADIUS_M = 100
ENRICH_RADIUS_M = 150


def find_duplicate_stations(conn: sqlite3.Connection) -> list[dict]:
    """Pary zapisanych stacji bliżej niż DUPLICATE_RADIUS_M — kandydaci do
    scalenia (np. stacja przemianowana ręcznie zostawia ducha pod starą
    nazwą — identyczne współrzędne, 0 m). "keep" to ta z większą liczbą
    tankowań (przy remisie — istniejąca)."""
    rows = [dict(r) for r in conn.execute(
        "SELECT s.*, (SELECT COUNT(*) FROM fillups f WHERE f.station = s.name)"
        " AS visits FROM stations s"
        " WHERE s.latitude IS NOT NULL AND s.longitude IS NOT NULL"
        " ORDER BY s.id")]
    pairs = []
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            d = haversine_m(a["latitude"], a["longitude"],
                            b["latitude"], b["longitude"])
            if d > DUPLICATE_RADIUS_M:
                continue
            keep, remove = (a, b) if a["visits"] >= b["visits"] else (b, a)
            pairs.append({
                "keep_id": keep["id"], "keep_name": keep["name"],
                "keep_visits": keep["visits"],
                "remove_id": remove["id"], "remove_name": remove["name"],
                "remove_visits": remove["visits"],
                "distance_m": round(d),
            })
    return pairs


def find_enrichable_stations(conn: sqlite3.Connection) -> list[dict]:
    """Stacje bez marki/ulicy — dociąga propozycję z Overpass wokół
    zapisanych współrzędnych. Sama nic nie zapisuje."""
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM stations WHERE latitude IS NOT NULL"
        " AND longitude IS NOT NULL AND (brand IS NULL OR street IS NULL)"
        " ORDER BY name")]
    proposals = []
    for s in rows:
        near = overpass_lookup(s["latitude"], s["longitude"], ENRICH_RADIUS_M)
        if not near:
            continue
        best = near[0]  # overpass_lookup sortuje po distance_m
        proposed = compose_name(best.get("street"), best.get("city"),
                                 best.get("brand"))
        if not proposed or (proposed == s["name"] and best.get("brand") == s["brand"]):
            continue
        proposals.append({
            "station_id": s["id"], "current_name": s["name"],
            "brand": best.get("brand"), "street": best.get("street"),
            "city": best.get("city"), "postcode": best.get("postcode"),
            "proposed_name": proposed, "distance_m": best.get("distance_m"),
        })
    return proposals


def apply_merge(conn: sqlite3.Connection, keep_id: int, remove_id: int) -> None:
    """Scala remove_id w keep_id: przepisuje fillups.station (złączenie po
    nazwie, patrz docs/PLAN-0.16.0-stations.md, "Poza zakresem") i usuwa
    zdublowany wiersz — w jednej transakcji."""
    keep = conn.execute("SELECT * FROM stations WHERE id = ?", (keep_id,)).fetchone()
    remove = conn.execute("SELECT * FROM stations WHERE id = ?", (remove_id,)).fetchone()
    if keep is None or remove is None:
        raise ValueError("Stacja nie istnieje")
    if keep_id == remove_id:
        raise ValueError("Nie można scalić stacji z samą sobą")
    conn.execute("UPDATE fillups SET station = ? WHERE station = ?",
                 (keep["name"], remove["name"]))
    conn.execute("DELETE FROM stations WHERE id = ?", (remove_id,))
    conn.commit()


def apply_enrichment(conn: sqlite3.Connection, station_id: int, *, name: str,
                     brand: str | None = None, street: str | None = None,
                     city: str | None = None, postcode: str | None = None) -> None:
    """Przemianowuje + uzupełnia stację propozycją z OSM; przepisuje
    fillups.station razem z nazwą, w jednej transakcji."""
    row = conn.execute("SELECT * FROM stations WHERE id = ?", (station_id,)).fetchone()
    if row is None:
        raise ValueError("Stacja nie istnieje")
    old_name = row["name"]
    conn.execute(
        "UPDATE stations SET name = ?, brand = COALESCE(brand, ?),"
        " street = COALESCE(street, ?), city = COALESCE(city, ?),"
        " postcode = COALESCE(postcode, ?), source = 'osm' WHERE id = ?",
        (name, brand, street, city, postcode, station_id))
    if name != old_name:
        conn.execute("UPDATE fillups SET station = ? WHERE station = ?",
                     (name, old_name))
    conn.commit()


def map_data(conn: sqlite3.Connection, vehicle_id: int) -> list[dict]:
    """Agregaty per stacja pod mapę: wizyty, koszty, ceny, flagi."""
    rows = conn.execute(
        """
        SELECT s.name, s.brand, s.latitude, s.longitude, s.country,
               COUNT(f.id) AS visits,
               ROUND(COALESCE(SUM(f.total_cost), 0), 2) AS total_cost,
               ROUND(AVG(f.price_per_l), 2) AS avg_price,
               MAX(f.date) AS last_date,
               SUM(CASE WHEN f.paid_by = 'own' THEN 1 ELSE 0 END) AS own_paid,
               SUM(CASE WHEN f.currency != 'PLN' THEN 1 ELSE 0 END) AS foreign_cnt
        FROM stations s
        LEFT JOIN fillups f
          ON f.station = s.name AND f.vehicle_id = ? AND f.draft = 0
        GROUP BY s.id
        ORDER BY visits DESC, s.name
        """, (vehicle_id,))
    return [dict(r) for r in rows]
