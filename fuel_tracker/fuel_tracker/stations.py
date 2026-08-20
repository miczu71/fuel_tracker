"""Stacje paliw: tożsamość+adres z paragonu, dopasowanie po GPS, lookup OSM
Overpass, dane do mapy.

resolve_station() (0.16.0, docs/PLAN-0.16.0-stations.md; naprawione 0.16.1,
docs/PLAN-0.16.1-fixes.md) to jedyne miejsce, które decyduje JAKA stacja
odpowiada tankowaniu — priorytet: numer stacji z paragonu (ref+brand,
deterministyczne) > adres z paragonu (geokodowany, Krok 3) > pozycja GPS
telefonu > ostatnio użyta nazwa (tylko jako SUGESTIA, nigdy jako
dopasowanie — pozycja telefonu bywa km od stacji, patrz plan).
"""
from __future__ import annotations

import logging
import math
import sqlite3
import time

import requests

from . import __version__, geocode

log = logging.getLogger(__name__)

# Promień dopasowania zapisanej stacji do bieżącej pozycji (metry).
MATCH_RADIUS_M = 300
# Promień zapytania Overpass o stacje w okolicy (metry).
OVERPASS_RADIUS_M = 500
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT_S = 5
# Bez User-Agent overpass-api.de odpowiada 406 na KAŻDE zapytanie (0.16.1:
# odkryte dopiero po wdrożeniu Kroku 5 — dawniej _overpass_raw/overpass_lookup
# łykały ten błąd jako pustą listę, więc funkcja wzbogacania nigdy nie
# działała, tylko wyglądała jak "brak braków do uzupełnienia").
USER_AGENT = f"fuel_tracker/{__version__} (Home Assistant add-on)"
# Źródła współrzędnych, od najbardziej do najmniej wiarygodnego. Tylko
# źródła geokodowane (patrz _GEOCODED_SOURCES) mogą NADPISAĆ już zapisane
# współrzędne — pozycja telefonu (gps_phone) tylko UZUPEŁNIA puste pole,
# tak jak przed 0.16.0 (bug 0.16.0: gps_phone=1 > legacy=0 nadpisywał
# współrzędne każdej z 12 istniejących stacji przy pierwszym zapisie
# tankowania po aktualizacji — patrz docs/PLAN-0.16.1-fixes.md, Krok 1a).
_COORD_PRIORITY = {"nominatim": 3, "osm": 2, "gps_phone": 1, "legacy": 0}
_GEOCODED_SOURCES = {"nominatim", "osm"}


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
    Orlenu). Degraduje łagodnie, od najbardziej do najmniej konkretnej —
    naprawione 0.16.1: gołą marką (linia niżej byłaby zbyt wcześnie w 0.16.0)
    tylko jako OSTATNIA deska ratunku, żeby paragon z marką+ulicą, ale bez
    miasta, nie kolidował z całą siecią pod jedną nazwą "Orlen"
    (docs/PLAN-0.16.1-fixes.md, Krok 1c):
    ulica+miasto+marka → "Maślicka 218, Wrocław - Orlen"
    ulica+miasto       → "Maślicka 218, Wrocław"
    miasto+marka       → "Wrocław - Orlen"
    ulica+marka        → "Maślicka 218 - Orlen"
    tylko marka        → "Orlen" (ostatnia deska ratunku)
    nic                → None
    """
    street = (street or "").strip()
    city = (city or "").strip()
    brand = (brand or "").strip()
    if street and city and brand:
        return f"{street}, {city} - {brand}"
    if street and city:
        return f"{street}, {city}"
    if city and brand:
        return f"{city} - {brand}"
    if street and brand:
        return f"{street} - {brand}"
    return brand or city or street or None


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
                   source: str = "legacy") -> dict:
    """Dodaje stację po nazwie lub uzupełnia brakujące dane istniejącej —
    zwraca PEŁNY wiersz (0.16.1: dawniej sam id, a jedyne dwa miejsca
    wołające w resolve_station musiały go zaraz re-SELECT-ować).

    source odróżnia jakość współrzędnych (patrz _COORD_PRIORITY) — tylko
    INNE źródło geokodowane (_GEOCODED_SOURCES) może nadpisać już zapisane
    współrzędne; pozycja telefonu (gps_phone), tak jak każde inne źródło,
    tylko UZUPEŁNIA puste pole, nigdy nie nadpisuje (fix 0.16.1 bloku
    korupcji danych z 0.16.0 — patrz docs/PLAN-0.16.1-fixes.md, Krok 1a)."""
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
        return dict(conn.execute(
            "SELECT * FROM stations WHERE id = ?", (cur.lastrowid,)).fetchone())
    # Uzupełnij tylko braki — ręcznie ustawionych danych nie nadpisujemy.
    updates, params = [], []
    existing_prio = _COORD_PRIORITY.get(row["source"] or "legacy", 0)
    new_prio = _COORD_PRIORITY.get(source, 0)
    coords_missing = row["latitude"] is None
    coords_upgradeable = source in _GEOCODED_SOURCES and new_prio > existing_prio
    if lat is not None and (coords_missing or coords_upgradeable):
        updates += ["latitude = ?", "longitude = ?", "source = ?"]
        params += [lat, lon, source]
    for col, val in (("brand", brand), ("ref", ref), ("street", street),
                     ("city", city), ("postcode", postcode)):
        if row[col] is None and val:
            updates.append(f"{col} = ?")
            params.append(val)
    if updates:
        conn.execute(f"UPDATE stations SET {', '.join(updates)} WHERE id = ?",
                     (*params, row["id"]))
        conn.commit()
        row = conn.execute("SELECT * FROM stations WHERE id = ?",
                           (row["id"],)).fetchone()
    return dict(row)


def _result(station_id, name, lat, lon, source: str, matched: bool,
           coord_source: str | None = None) -> dict:
    """Kształt wyniku resolve_station — jedno miejsce zamiast czterech
    ręcznie przepisanych dictów (0.16.1). coord_source to tag jakości
    współrzędnych (_COORD_PRIORITY) — potrzebny web.py, żeby wiedzieć, pod
    jakim source zapisać stację przy faktycznym zapisie tankowania, gdy
    resolve_station wołane było z persist=False (patrz Krok 1b)."""
    return {"station_id": station_id, "name": name, "latitude": lat,
            "longitude": lon, "source": source, "matched": matched,
            "coord_source": coord_source}


def resolve_station(conn: sqlite3.Connection, *, ref: str | None = None,
                    brand: str | None = None, street: str | None = None,
                    city: str | None = None, postcode: str | None = None,
                    gps: tuple[float, float] | None = None,
                    name_hint: str | None = None,
                    persist: bool = True) -> dict:
    """Jedno miejsce decydujące, jaka stacja odpowiada tankowaniu. Priorytet
    źródeł (patrz moduł docstring): ref+brand > adres z paragonu (geokodowany)
    > GPS telefonu > name_hint (ostatnio użyta nazwa — TYLKO jako sugestia,
    matched=False, nigdy jako dopasowanie). Zwraca dict, station_id=None gdy
    nic się nie dało ustalić.

    persist=False (0.16.1, docs/PLAN-0.16.1-fixes.md Krok 1b): dopasowuje
    istniejące stacje i geokoduje adres (geocode_cache to cache, nie encja
    widoczna dla użytkownika — zapisuje się zawsze), ale NIE zapisuje do
    `stations` — używane przez podgląd skanu paragonu (/api/receipts/parse),
    gdzie skan może zostać porzucony bez zapisania tankowania. Jedynym
    pisarzem jest zapis tankowania (web.py: _remember_station)."""
    empty = _result(None, None, None, None, None, False)

    # 1. Numer stacji z paragonu — deterministyczne, po marce+ref.
    if ref and brand:
        row = conn.execute(
            "SELECT * FROM stations WHERE brand = ? AND ref = ?",
            (brand, ref)).fetchone()
        if row is not None:
            return _result(row["id"], row["name"], row["latitude"],
                           row["longitude"], "ref", True,
                           coord_source=row["source"])

    # 2. Adres z paragonu — TYLKO gdy jest ulica albo miasto (sama marka nie
    #    jest tożsamością stacji — patrz compose_name — i nie ma prawa
    #    utworzyć/dopasować wiersza, inaczej wszystkie stacje jednej sieci
    #    kolidują pod nazwą marki). Dopasuj po złożonej nazwie albo
    #    geokoduj i utwórz nową stację z prawdziwymi współrzędnymi.
    if street or city:
        composed = compose_name(street, city, brand)
        row = conn.execute(
            "SELECT * FROM stations WHERE name = ?", (composed,)).fetchone()
        if row is not None:
            if persist:
                row = upsert_station(
                    conn, composed, row["latitude"], row["longitude"],
                    brand=brand, ref=ref, street=street, city=city,
                    postcode=postcode, source=row["source"] or "legacy")
            return _result(row["id"], row["name"], row["latitude"],
                           row["longitude"], "address", True,
                           coord_source=row["source"])
        coords = geocode.geocode_address(conn, street, city, postcode)
        lat, lon = coords if coords else (None, None)
        coord_source = "nominatim" if coords else "legacy"
        if persist:
            row = upsert_station(conn, composed, lat, lon, brand=brand,
                                 ref=ref, street=street, city=city,
                                 postcode=postcode, source=coord_source)
            return _result(row["id"], row["name"], row["latitude"],
                           row["longitude"], "address", True,
                           coord_source=row["source"])
        return _result(None, composed, lat, lon, "address", True,
                       coord_source=coord_source)

    # 3. Pozycja GPS telefonu — dopasowanie do istniejącej stacji w promieniu.
    if gps:
        near = nearest_station(conn, *gps)
        if near:
            return _result(near["id"], near["name"], near["latitude"],
                           near["longitude"], "gps", True,
                           coord_source=near["source"])

    # 4. Ostatnio użyta nazwa — TYLKO sugestia, nigdy dopasowanie (pozycja
    #    telefonu bywa km od stacji, patrz plan — silent fallback usunięty).
    if name_hint:
        return empty | {"name": name_hint, "source": "hint"}

    return empty


def list_stations(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM stations ORDER BY name")]


# ── Porządki (0.16.0, naprawione 0.16.1): scalanie duplikatów + uzupełnianie
# z OSM. Podgląd → zatwierdzenie (web.py: /api/stations/cleanup/*) — nic tu
# nie zapisuje samo z siebie, patrz apply_merge/apply_enrichment.

DUPLICATE_RADIUS_M = 100
ENRICH_RADIUS_M = 150
# Limit stacji sprawdzanych o Overpass na jeden przebieg preview — bez tego
# find_enrichable_stations robi jeden blokujący POST *na stację*, szeregowo,
# w środku żądania GET (na 12 stacjach po migracji v11 to ~84 s, patrz
# docs/PLAN-0.16.1-fixes.md, Krok 5). "Sprawdź stacje" kliknięte ponownie
# sprawdza kolejną paczkę.
ENRICH_MAX_STATIONS = 8
ENRICH_THROTTLE_S = 1
# Cache w procesie na (lat/lon zaokrąglone, promień) — powtórny klik nie
# odpytuje Overpassu o stacje, które się nie ruszyły od ostatniego sprawdzenia.
_ENRICH_CACHE_TTL_S = 3600
_enrich_cache: dict[tuple, tuple[float, list[dict]]] = {}


def _richness(row: dict) -> int:
    """Ile pól tożsamości ma wypełnionych stacja — do wyboru ocalałego przy
    remisie liczby tankowań (apply_merge, Krok 4a): bogatszy wiersz (adres z
    paragonu) wygrywa z ubogim (legacy z samą nazwą i pozycją telefonu)."""
    return sum(1 for f in ("ref", "street", "city", "postcode") if row.get(f))


def find_duplicate_stations(conn: sqlite3.Connection) -> list[dict]:
    """Klastry zapisanych stacji bliżej niż DUPLICATE_RADIUS_M — kandydaci do
    scalenia (np. stacja przemianowana ręcznie zostawia ducha pod starą
    nazwą — identyczne współrzędne, 0 m). Domknięcie przechodnie w promieniu
    (0.16.1, Krok 4c) — dawniej trzy stacje w klastrze dawały trzy PARY;
    zaznaczenie wszystkich kończyło się jednym scaleniem i dwoma
    ValueError("Stacja nie istnieje"), bo wcześniejsze scalenie kasowało
    wiersz, na który wskazywała kolejna para. Teraz jeden ocalały na klaster,
    po jednej parze *nie-ocalały → ocalały*. "keep" w klastrze: najwięcej
    tankowań → przy remisie bogatszy wiersz (_richness) → przy remisie
    niższe id."""
    rows = [dict(r) for r in conn.execute(
        "SELECT s.*, COUNT(f.id) AS visits FROM stations s"
        " LEFT JOIN fillups f ON f.station = s.name"
        " WHERE s.latitude IS NOT NULL AND s.longitude IS NOT NULL"
        " GROUP BY s.id ORDER BY s.id")]

    # Union-find: sąsiedzi w promieniu DUPLICATE_RADIUS_M trafiają do
    # jednego klastra, nawet gdy A~B i B~C, ale A daleko od C.
    parent = {r["id"]: r["id"] for r in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if haversine_m(a["latitude"], a["longitude"],
                           b["latitude"], b["longitude"]) <= DUPLICATE_RADIUS_M:
                ra, rb = find(a["id"]), find(b["id"])
                if ra != rb:
                    parent[ra] = rb

    clusters: dict[int, list[dict]] = {}
    for r in rows:
        clusters.setdefault(find(r["id"]), []).append(r)

    pairs = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        keep = max(members, key=lambda r: (r["visits"], _richness(r), -r["id"]))
        for r in members:
            if r["id"] == keep["id"]:
                continue
            d = haversine_m(keep["latitude"], keep["longitude"],
                            r["latitude"], r["longitude"])
            pairs.append({
                "keep_id": keep["id"], "keep_name": keep["name"],
                "keep_visits": keep["visits"],
                "remove_id": r["id"], "remove_name": r["name"],
                "remove_visits": r["visits"],
                "distance_m": round(d),
            })
    return pairs


def _overpass_raw(lat: float, lon: float,
                  radius_m: float = OVERPASS_RADIUS_M) -> list[dict]:
    """Jak overpass_lookup, ale RZUCA na błędzie zamiast łykać go i zwracać
    [] — find_enrichable_stations potrzebuje odróżnić "nic w pobliżu" od
    "Overpass nie odpowiedział/dał 429" (0.16.1, Krok 5); bez tego throttling
    wyglądał jak "wszystko uzupełnione"."""
    query = (
        f"[out:json][timeout:{OVERPASS_TIMEOUT_S}];"
        f"nwr[amenity=fuel](around:{radius_m},{lat},{lon});out center;"
    )
    resp = requests.post(OVERPASS_URL, data={"data": query},
                         headers={"User-Agent": USER_AGENT},
                         timeout=OVERPASS_TIMEOUT_S + 2)
    resp.raise_for_status()
    elements = resp.json().get("elements", [])
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


def _overpass_cached(lat: float, lon: float, radius_m: float) -> list[dict]:
    key = (round(lat, 4), round(lon, 4), radius_m)
    cached = _enrich_cache.get(key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _ENRICH_CACHE_TTL_S:
        return cached[1]
    result = _overpass_raw(lat, lon, radius_m)
    _enrich_cache[key] = (now, result)
    return result


def overpass_lookup(lat: float, lon: float,
                    radius_m: float = OVERPASS_RADIUS_M) -> list[dict]:
    """Stacje paliw z OSM w okolicy; pusta lista przy błędzie/timeout —
    best-effort, używane jako podpowiedź w formularzu (/api/stations/nearby).
    Wariant, który rzuca zamiast łykać błąd: _overpass_raw."""
    try:
        return _overpass_raw(lat, lon, radius_m)
    except Exception as exc:  # sieć/timeout — funkcja jest tylko podpowiedzią
        log.warning("Overpass niedostępny: %s", exc)
        return []


def find_enrichable_stations(conn: sqlite3.Connection,
                             limit: int = ENRICH_MAX_STATIONS) -> dict:
    """Stacje bez marki/ulicy — dociąga propozycję z Overpass wokół
    zapisanych współrzędnych. Sama nic nie zapisuje. Limit + throttle +
    cache (0.16.1, Krok 5) — bez nich to jeden blokujący POST na stację,
    szeregowo, w środku żądania GET (do ~84 s na 12 stacjach). Zwraca
    {proposals, checked, remaining, errors} zamiast gołej listy, żeby UI
    mogło rozróżnić "sprawdzono część" od "sprawdzono wszystko, nic nie
    trzeba uzupełniać" i pokazać komunikat błędu zamiast fałszywej ciszy."""
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM stations WHERE latitude IS NOT NULL"
        " AND longitude IS NOT NULL AND (brand IS NULL OR street IS NULL)"
        " ORDER BY name")]
    batch, remaining_rows = rows[:limit], rows[limit:]
    proposals, errors = [], []
    for i, s in enumerate(batch):
        if i > 0:
            time.sleep(ENRICH_THROTTLE_S)
        try:
            near = _overpass_cached(s["latitude"], s["longitude"], ENRICH_RADIUS_M)
        except Exception as exc:
            log.warning("Overpass niedostępny dla stacji %s: %s", s["id"], exc)
            errors.append(f"{s['name']}: Overpass niedostępny ({exc})")
            continue
        if not near:
            continue
        best = near[0]  # overpass_lookup sortuje po distance_m
        # Propozycja bez ulicy i bez miasta nie jest lepsza od tego, co już
        # mamy — apply_enrichment by ją i tak przepisała jako rename całej
        # historii tankowań (Krok 4b), np. "Wrocław - Orlen" → "Orlen".
        if not (best.get("street") or best.get("city")):
            continue
        proposed = compose_name(best.get("street"), best.get("city"),
                                 best.get("brand"))
        if not proposed or (proposed == s["name"] and best.get("brand") == s["brand"]):
            continue
        proposals.append({
            "station_id": s["id"], "current_name": s["name"],
            "brand": best.get("brand"), "street": best.get("street"),
            "city": best.get("city"), "postcode": best.get("postcode"),
            "latitude": best.get("latitude"), "longitude": best.get("longitude"),
            "proposed_name": proposed, "distance_m": best.get("distance_m"),
        })
    return {"proposals": proposals, "checked": len(batch),
            "remaining": len(remaining_rows), "errors": errors}


def apply_merge(conn: sqlite3.Connection, keep_id: int, remove_id: int) -> None:
    """Scala remove_id w keep_id: przenosi do ocalałego każde pole tożsamości
    (ref/street/city/postcode), którego ocalały nie ma, a usuwany ma — ref
    tylko gdy nie łamie UNIQUE(brand, ref) — oraz współrzędne, jeśli usuwany
    ma je z lepszego źródła (_COORD_PRIORITY); dopiero potem przepisuje
    fillups.station (złączenie po nazwie, patrz
    docs/PLAN-0.16.0-stations.md, "Poza zakresem") i usuwa zdublowany
    wiersz — w jednej transakcji. Naprawione 0.16.1 (Krok 4a): dawniej
    DELETE kasował bezpowrotnie ref/adres/lepsze współrzędne usuwanej
    stacji, jeśli to ona miała mniej tankowań — na produkcji dokładnie ten
    scenariusz (legacy z pozycją telefonu i 15 tankowań kontra świeżo
    zgeokodowana z paragonu i 1 tankowaniem)."""
    keep = conn.execute("SELECT * FROM stations WHERE id = ?", (keep_id,)).fetchone()
    remove = conn.execute("SELECT * FROM stations WHERE id = ?", (remove_id,)).fetchone()
    if keep is None or remove is None:
        raise ValueError("Stacja nie istnieje")
    if keep_id == remove_id:
        raise ValueError("Nie można scalić stacji z samą sobą")
    keep, remove = dict(keep), dict(remove)

    updates, params = [], []
    for col in ("street", "city", "postcode"):
        if not keep[col] and remove[col]:
            updates.append(f"{col} = ?")
            params.append(remove[col])
    if not keep["ref"] and remove["ref"]:
        # remove_id wykluczone też — to JEGO WŁASNY (brand, ref) inaczej
        # zawsze "koliduje" sam ze sobą i transfer nigdy by nie zaszedł.
        dup = conn.execute(
            "SELECT 1 FROM stations WHERE brand = ? AND ref = ?"
            " AND id NOT IN (?, ?)",
            (keep["brand"] or remove["brand"], remove["ref"],
             keep_id, remove_id)).fetchone()
        if not dup:
            if not keep["brand"] and remove["brand"]:
                updates.append("brand = ?")
                params.append(remove["brand"])
            updates.append("ref = ?")
            params.append(remove["ref"])
    keep_prio = _COORD_PRIORITY.get(keep["source"] or "legacy", 0)
    remove_prio = _COORD_PRIORITY.get(remove["source"] or "legacy", 0)
    if remove["latitude"] is not None and remove_prio > keep_prio:
        updates += ["latitude = ?", "longitude = ?", "source = ?"]
        params += [remove["latitude"], remove["longitude"], remove["source"]]

    # DELETE musi iść PRZED UPDATE: dopóki "remove" istnieje, ono samo
    # trzyma (brand, ref) będące celem transferu — UPDATE na "keep" wpadłby
    # w chwilowe naruszenie UNIQUE(brand, ref) tego samego wiersza.
    conn.execute("UPDATE fillups SET station = ? WHERE station = ?",
                 (keep["name"], remove["name"]))
    conn.execute("DELETE FROM stations WHERE id = ?", (remove_id,))
    if updates:
        conn.execute(f"UPDATE stations SET {', '.join(updates)} WHERE id = ?",
                     (*params, keep_id))
    conn.commit()


def apply_enrichment(conn: sqlite3.Connection, station_id: int, *, name: str,
                     brand: str | None = None, street: str | None = None,
                     city: str | None = None, postcode: str | None = None,
                     latitude: float | None = None,
                     longitude: float | None = None) -> None:
    """Przemianowuje + uzupełnia stację propozycją z OSM; przepisuje
    fillups.station razem z nazwą, w jednej transakcji.

    Naprawione 0.16.1 (Krok 4b): source zmienia się TYLKO razem ze
    współrzędnymi — dawniej `source = 'osm'` był bezwarunkowy, więc
    wzbogacenie oznaczało cudzą (często telefonową) pozycję jako zaufaną
    i blokowało jej późniejszą korektę. Gdy obecne źródło jest słabsze niż
    'osm', przyjmuje pozycję z OSM (naprawia też stacje uszkodzone przez
    bug 1a) — to jedyna dodatkowa ścieżka: brak osobnego narzędzia repair,
    bo ta i tak podnosi współrzędne słabszego źródła. Kolizja nazw (dwie
    propozycje z tego samego węzła OSM) zgłasza się czytelnym ValueError
    zamiast surowego sqlite3.IntegrityError pokazywanego użytkownikowi."""
    row = conn.execute("SELECT * FROM stations WHERE id = ?", (station_id,)).fetchone()
    if row is None:
        raise ValueError("Stacja nie istnieje")
    row = dict(row)
    old_name = row["name"]
    if name != old_name:
        dup = conn.execute(
            "SELECT 1 FROM stations WHERE name = ? AND id != ?",
            (name, station_id)).fetchone()
        if dup:
            raise ValueError(
                f"Nazwa „{name}” jest już zajęta — scal stacje zamiast uzupełniać")

    updates = ["brand = COALESCE(brand, ?)", "street = COALESCE(street, ?)",
              "city = COALESCE(city, ?)", "postcode = COALESCE(postcode, ?)"]
    params = [brand, street, city, postcode]
    existing_prio = _COORD_PRIORITY.get(row["source"] or "legacy", 0)
    if latitude is not None and existing_prio < _COORD_PRIORITY["osm"]:
        updates += ["latitude = ?", "longitude = ?", "source = 'osm'"]
        params += [latitude, longitude]
    conn.execute(
        f"UPDATE stations SET name = ?, {', '.join(updates)} WHERE id = ?",
        (name, *params, station_id))
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
