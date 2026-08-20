"""Geokodowanie adresu stacji przez Nominatim (0.16.0,
docs/PLAN-0.16.0-stations.md; naprawione 0.16.1, docs/PLAN-0.16.1-fixes.md).

Zamienia adres z paragonu (station_street/city/postcode w receipts.py) na
współrzędne, żeby nowa stacja w bazie miała PRAWDZIWĄ pozycję zamiast pozycji
telefonu w chwili tankowania (bywa kilka km od stacji — zweryfikowane na
produkcji, patrz plan). Wzorzec błędów jak stations.overpass_lookup:
best-effort, None przy każdym problemie, nigdy nie wysypuje żądania.

Cache w tabeli geocode_cache (migracja v11, db.py) — polityka Nominatim to
maks. 1 zapytanie/s, a narzędzie porządków (web.py: /api/stations/cleanup)
odpytuje wiele stacji pod rząd. Trafienia negatywne mają TTL (Krok 2) —
dawniej blokowały adres bezterminowo (resolved_at zapisywane, ale nigdy nie
czytane), więc jeden przejściowy błąd/luka w indeksie OSM czynił adres
nierozwiązywalnym na zawsze.
"""
from __future__ import annotations

import logging
import sqlite3
import unicodedata

import requests

from . import __version__

log = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_TIMEOUT_S = 5
USER_AGENT = f"fuel_tracker/{__version__} (Home Assistant add-on)"
# Trafienie negatywne (brak wyniku) starsze niż tyle dni odpytuje Nominatim
# ponownie — trafienia pozytywne zostają w cache bezterminowo.
_MISS_TTL_DAYS = 30


def _cache_key(street: str, city: str, postcode: str | None, country: str) -> str:
    return "|".join([street.strip().lower(), city.strip().lower(),
                     (postcode or "").strip().lower(), country.strip().lower()])


def _fold(s: str) -> str:
    """Zdejmuje znaki diakrytyczne + casefold — do porównania nazw miast
    niewrażliwego na "Będzino" vs "bedzino"/"BĘDZINO"."""
    decomposed = unicodedata.normalize("NFKD", s)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def geocode_address(conn: sqlite3.Connection, street: str, city: str,
                    postcode: str | None = None,
                    country: str = "Polska") -> tuple[float, float] | None:
    """Adres → (lat, lon) albo None (brak trafienia/błąd/timeout/adres z innego
    miasta niż żądane). Wynik cache'owany w geocode_cache — ta sama para
    street+city+postcode nie odpytuje Nominatim drugi raz (poza pudłami
    starszymi niż _MISS_TTL_DAYS, patrz moduł docstring)."""
    street = (street or "").strip()
    city = (city or "").strip()
    if not street and not city:
        return None

    key = _cache_key(street, city, postcode, country)
    cached = conn.execute(
        "SELECT latitude, longitude, resolved_at FROM geocode_cache"
        " WHERE query = ?", (key,)).fetchone()
    if cached is not None:
        if cached["latitude"] is not None:
            return cached["latitude"], cached["longitude"]
        # Pudło — ważne tylko przez _MISS_TTL_DAYS, potem próbujemy ponownie
        # (julianday różnica w dniach; resolved_at ma domyślne datetime('now')).
        stale = conn.execute(
            "SELECT julianday('now') - julianday(?) > ?",
            (cached["resolved_at"], _MISS_TTL_DAYS)).fetchone()[0]
        if not stale:
            return None  # zapamiętane "brak trafienia", jeszcze świeże

    params = {
        "format": "json",
        "limit": 1,
        "country": country,
        "addressdetails": 1,
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
            hit = hits[0]
            # Kontrola miasta (Krok 2): bez tego trafienie w adres centrali
            # spółki (np. Płock dla paragonów ORLEN) ląduje jako źródło
            # 'nominatim' — najwyższy priorytet współrzędnych, nieodwracalny
            # przez pozycję telefonu. Nominatim czasem nie zwraca 'city'
            # (miejscowość wiejska), sprawdzamy więc kilka pól addressdetails.
            addr = hit.get("address") or {}
            hit_city = (addr.get("city") or addr.get("town")
                       or addr.get("village") or addr.get("municipality") or "")
            if city and hit_city and _fold(hit_city) != _fold(city):
                log.warning(
                    "Nominatim: adres '%s' rozwiązał się do innego miasta "
                    "('%s' != żądane '%s') — traktuję jak brak trafienia",
                    key, hit_city, city)
            else:
                result = (float(hit["lat"]), float(hit["lon"]))
    except Exception as exc:  # sieć/timeout/JSON — funkcja jest best-effort
        log.warning("Nominatim niedostępny dla '%s': %s", key, exc)
        return None  # błąd sieci NIE trafia do cache — spróbuj ponownie później

    conn.execute(
        "INSERT OR REPLACE INTO geocode_cache (query, latitude, longitude,"
        " resolved_at) VALUES (?, ?, ?, datetime('now'))",
        (key, result[0] if result else None, result[1] if result else None))
    conn.commit()
    return result
