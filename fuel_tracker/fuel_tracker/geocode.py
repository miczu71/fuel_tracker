"""Geokodowanie adresu stacji przez Nominatim (0.16.0,
docs/PLAN-0.16.0-stations.md).

Zamienia adres z paragonu (station_street/city/postcode w receipts.py) na
współrzędne, żeby nowa stacja w bazie miała PRAWDZIWĄ pozycję zamiast pozycji
telefonu w chwili tankowania (bywa kilka km od stacji — zweryfikowane na
produkcji, patrz plan). Wzorzec błędów jak stations.overpass_lookup:
best-effort, None przy każdym problemie, nigdy nie wysypuje żądania.

Cache w tabeli geocode_cache (migracja v11, db.py) — polityka Nominatim to
maks. 1 zapytanie/s, a narzędzie porządków (web.py: /api/stations/cleanup)
odpytuje wiele stacji pod rząd.
"""
from __future__ import annotations

import logging
import sqlite3

import requests

from . import __version__

log = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_TIMEOUT_S = 5
USER_AGENT = f"fuel_tracker/{__version__} (Home Assistant add-on)"


def _cache_key(street: str, city: str, postcode: str | None, country: str) -> str:
    return "|".join([street.strip().lower(), city.strip().lower(),
                     (postcode or "").strip().lower(), country.strip().lower()])


def geocode_address(conn: sqlite3.Connection, street: str, city: str,
                    postcode: str | None = None,
                    country: str = "Polska") -> tuple[float, float] | None:
    """Adres → (lat, lon) albo None (brak trafienia/błąd/timeout). Wynik
    cache'owany w geocode_cache — ta sama para street+city+postcode nie
    odpytuje Nominatim drugi raz."""
    street = (street or "").strip()
    city = (city or "").strip()
    if not street and not city:
        return None

    key = _cache_key(street, city, postcode, country)
    cached = conn.execute(
        "SELECT latitude, longitude FROM geocode_cache WHERE query = ?",
        (key,)).fetchone()
    if cached is not None:
        if cached["latitude"] is None:
            return None  # zapamiętane "brak trafienia"
        return cached["latitude"], cached["longitude"]

    params = {
        "format": "json",
        "limit": 1,
        "country": country,
    }
    if street:
        params["street"] = street
    if city:
        params["city"] = city
    if postcode:
        params["postalcode"] = postcode

    result = None
    try:
        resp = requests.get(NOMINATIM_URL, params=params,
                            headers={"User-Agent": USER_AGENT},
                            timeout=NOMINATIM_TIMEOUT_S)
        resp.raise_for_status()
        hits = resp.json()
        if hits:
            result = (float(hits[0]["lat"]), float(hits[0]["lon"]))
    except Exception as exc:  # sieć/timeout/JSON — funkcja jest best-effort
        log.warning("Nominatim niedostępny dla '%s': %s", key, exc)
        return None  # błąd sieci NIE trafia do cache — spróbuj ponownie później

    conn.execute(
        "INSERT OR REPLACE INTO geocode_cache (query, latitude, longitude) "
        "VALUES (?, ?, ?)",
        (key, result[0] if result else None, result[1] if result else None))
    conn.commit()
    return result
