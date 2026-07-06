"""Stacje paliw: dopasowanie po GPS, lookup OSM Overpass, dane do mapy."""
from __future__ import annotations

import logging
import math
import sqlite3

import requests

log = logging.getLogger(__name__)

# Promień dopasowania zapisanej stacji do bieżącej pozycji (metry).
MATCH_RADIUS_M = 300
# Promień zapytania Overpass o stacje w okolicy (metry).
OVERPASS_RADIUS_M = 500
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT_S = 5


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
        name = tags.get("name") or tags.get("brand")
        if not name:
            continue
        results.append({
            "name": name,
            "brand": tags.get("brand"),
            "latitude": elat,
            "longitude": elon,
            "distance_m": round(haversine_m(lat, lon, elat, elon)),
        })
    results.sort(key=lambda s: s["distance_m"])
    return results


def upsert_station(conn: sqlite3.Connection, name: str,
                   lat: float | None = None, lon: float | None = None,
                   brand: str | None = None, country: str = "PL") -> int:
    """Dodaje stację po nazwie lub uzupełnia brakujące dane istniejącej."""
    name = name.strip()
    row = conn.execute("SELECT * FROM stations WHERE name = ?", (name,)).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO stations (name, brand, latitude, longitude, country)"
            " VALUES (?, ?, ?, ?, ?)", (name, brand, lat, lon, country))
        conn.commit()
        return cur.lastrowid
    # Uzupełnij tylko braki — ręcznie ustawionych danych nie nadpisujemy.
    updates, params = [], []
    if row["latitude"] is None and lat is not None:
        updates += ["latitude = ?", "longitude = ?"]
        params += [lat, lon]
    if row["brand"] is None and brand:
        updates.append("brand = ?")
        params.append(brand)
    if updates:
        conn.execute(f"UPDATE stations SET {', '.join(updates)} WHERE id = ?",
                     (*params, row["id"]))
        conn.commit()
    return row["id"]


def list_stations(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM stations ORDER BY name")]


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
