# Fuel Tracker 0.16.0 — lokalizacja stacji z paragonu

## Kontekst

Przy tankowaniach w Orlenie użytkownik musiał **ręcznie poprawiać adresy stacji**
po każdym skanie paragonu. Diagnoza na żywych danych (`/api/backup/export.json`
z działającego add-onu, slug `f123e1f2_fuel_tracker`) wykazała siedem
współdziałających przyczyn — nie jedną.

### Potwierdzone empirycznie

1. **Wynik skanu paragonu nigdy nie trafia do formularza.** `static/app.js:297`
   ma warunek `if (p.station && !form.station.value)`, ale `app.js:348`
   (`/api/prefill`) wypełnia to pole **wcześniej**, przy ładowaniu strony, i
   robi to prawie zawsze (fallback na ostatnie tankowanie). Nazwa stacji
   z paragonu jest więc systematycznie odrzucana. To główna przyczyna.
2. **Schemat vision każe modelowi wyrzucić adres.** `receipts.py:51` —
   `station_name`: *„Sieć + miasto, np. 'ORLEN Warszawa'"*. Wszystkie skany
   Orlenu w bazie dały dosłownie `'ORLEN Wrocław'` (3×) i `'ORLEN Będzino'`.
3. **Paragon ma komplet danych.** `tests/fixtures/receipt_orlen_fleet.jpg`:
   `STACJA PALIW NR 4282 W BĘDZINIE` + `BĘDZINO 87, 76-037 BĘDZINO`. Numer
   stacji **i** pełny adres. Pułapka: wyżej stoi adres centrali
   (`09-411 PŁOCK, UL. CHEMIKÓW 7`), którego nie wolno wziąć.
4. **Cichy fallback na ostatnią stację.** `web.py:602` — brak dopasowania GPS →
   wpisuje nazwę z poprzedniego tankowania, bez żadnego oznaczenia.
5. **Pozycja telefonu ≠ pozycja stacji.** Tankowanie `2026-08-14 12:54` ma
   zapisane współrzędne **3.7 km od stacji** (telefon już w drodze) — poza
   promieniem 300 m `MATCH_RADIUS_M`, więc zadziałał fallback z p. 4. Przez
   `_remember_station` (`web.py:470`) takie współrzędne trafiają do `stations`
   jako pozycja stacji.
6. **Zmiana nazwy tworzy ducha.** `stations.name` jest UNIQUE i to jedyna
   tożsamość, a `fillups.station` trzyma **nazwę**, nie klucz obcy. Efekt
   w bazie: id 10 `Wrocław - Orlen` ma dziś **0 tankowań**, a id 12
   `Szybowcowa 27, Wrocław - Orlen` ma **identyczne** współrzędne
   51.1301/16.96649. `nearest_station` trafia w oba niedeterministycznie.
7. **`brand` jest `NULL` we wszystkich 12 wierszach** — `overpass_lookup`
   (`stations.py:65`) bierze tylko `tags.name`, a `_remember_station` nigdy
   nie podaje marki.

### Co potwierdziłem, że zadziała

- **Nominatim** na adresie z paragonu: `Będzino 87, Będzino, Poland` →
  `54.2088119, 15.9835218`, `display_name` = „Orlen, 87, Będzino" — **30 m**
  od zapisanych współrzędnych stacji.
- **Konwencja nazw użytkownika = tagi OSM.** Węzeł 6754512655 ma
  `addr:street=Maślicka`, `addr:housenumber=218`, `addr:city=Wrocław`,
  `brand=Orlen` — czyli dokładnie ręcznie wpisane „Maślicka 218, Wrocław -
  Orlen". To samo dla Szybowcowej 27 (węzeł 2413128231).
- OSM **nie ma** tagu `ref` z numerem stacji Orlenu — numer z paragonu nadaje
  się tylko jako wewnętrzny klucz tożsamości, nie do korelacji z OSM.

### Zamierzony efekt

Po zeskanowaniu paragonu pole „Stacja" zawiera nazwę zbudowaną z adresu
z paragonu w konwencji `{ulica} {nr}, {miasto} - {Marka}`, a współrzędne stacji
pochodzą z geokodowania tego adresu, nie z pozycji telefonu. Zero ręcznych
poprawek. Istniejący duplikat Orlenu zostaje scalony narzędziem z podglądem.

### Decyzje użytkownika

- Konwencja nazw: **`{ulica} {nr}, {miasto} - {Marka}`**.
- Nowe źródła: **adres + numer stacji z paragonu** oraz **geokodowanie
  Nominatim**. Geolokalizacja przeglądarki — odrzucona.
- Istniejące dane: **scal ducha + uzupełnij z OSM, z podglądem przed zapisem**.

---

## Zakres

Wydanie **0.16.0** add-onu (repo `/config/addons/fuel_tracker`, katalog add-onu
`fuel_tracker/`, pakiet `fuel_tracker/fuel_tracker/`).

**Poza zakresem (świadomie):** zamiana `fillups.station` (TEXT) na
`fillups.station_id` (FK). To poprawna docelowa naprawa p. 6, ale dotyka
`stats.py`, `queries.py`, `csv_io.py`, `backup.py`, `importer_drivvo.py`
i eksportów — osobne wydanie. Tu utrzymujemy złączenie po nazwie
(`map_data`: `ON f.station = s.name`), a narzędzie porządków przepisuje
**obie strony** w jednej transakcji.

---

## Krok 0 — plan do repo

Skopiować ten plik do `docs/PLAN-0.16.0-stations.md` (konwencja jak
istniejący `docs/PLAN-0.15.0-vision.md`) **przed** pierwszą zmianą w kodzie.

---

## Krok 1 — `receipts.py`: wyciągnij adres i numer stacji

`STRUCTURE` — dołożyć pola (wszystkie opcjonalne, `""` gdy brak):

| pole | opis w schemacie |
|---|---|
| `station_brand` | Sieć, np. „Orlen", „BP", „Shell" |
| `station_street` | Ulica z numerem, np. „Maślicka 218", „Będzino 87" |
| `station_city` | Miasto stacji, np. „Wrocław" |
| `station_postcode` | Kod pocztowy stacji, np. „76-037" |
| `station_ref` | Numer stacji z paragonu, np. „4282" |

`station_name` zostaje (kompatybilność wsteczna z 12 zapisanymi
`attachments.parsed_json`), ale opis zmienia się na „pełny nagłówek stacji
z paragonu" — przestaje wymuszać „sieć + miasto".

`PROMPT` — dopisać akapit uczący rozróżniać adres stacji od siedziby spółki:

> Adres STACJI, nie siedziby spółki. Na paragonach ORLEN pierwszy adres
> (`09-411 PŁOCK, UL. CHEMIKÓW 7`) to CENTRALA — pomiń go. Adres stacji stoi
> niżej, przy linii `STACJA PALIW NR <numer> W <MIASTO>`, np.
> `BĘDZINO 87, 76-037 BĘDZINO`. `station_ref` = numer z tej linii, `""` gdy
> paragon go nie ma.

`normalize()` — zwraca dodatkowo `station_brand/street/city/postcode/ref`
oraz `station` zbudowane przez `stations.compose_name()` (Krok 2), z odwrotem
na dotychczasowe `parsed["station_name"]`, gdy adresu nie ma.

**Uwaga do zweryfikowania:** `vision.call_local` wysyła schemat z
`"strict": True` (`vision.py:124`). Obecny schemat już łamie ścisły tryb
OpenAI (`required` to podzbiór `properties`, brak `additionalProperties`)
i router freellmapi to toleruje — nowe pola opcjonalne nie pogarszają sytuacji,
ale ogniwo lokalne trzeba przetestować osobno przed wydaniem.

## Krok 2 — `stations.py`: tożsamość stacji i budowa nazwy

Nowe funkcje w istniejącym module (nie nowy plik — moduł ma 127 linii):

```python
def compose_name(street, city, brand) -> str | None
```
Degradacja, od najbogatszej formy:
`"Maślicka 218, Wrocław - Orlen"` → `"Wrocław - Orlen"` (dzisiejsze
zachowanie) → `"Orlen"` → `None`.

```python
def resolve_station(conn, *, ref=None, brand=None, street=None, city=None,
                    postcode=None, gps=None, name_hint=None) -> dict
```
Jedno miejsce z priorytetem źródeł; zwraca
`{"station_id", "name", "latitude", "longitude", "source", "matched"}`:

1. `ref` + `brand` → dopasowanie po `stations.ref` — deterministyczne;
2. adres z paragonu → `compose_name` → dopasowanie po nazwie; brak → geokoduj
   (Krok 3) i **utwórz stację z prawdziwymi współrzędnymi**;
3. `gps` ≤ `MATCH_RADIUS_M` → istniejące `nearest_station`;
4. `name_hint` (ostatnia stacja) → zwracane wyłącznie jako **sugestia**
   (`matched=False`), nigdy jako wartość pola.

`upsert_station` — dołożyć parametry `ref/street/city/postcode/source`,
zachowując dotychczasową regułę „uzupełniaj tylko braki, nie nadpisuj".
Dodatkowo: współrzędne o `source='gps_phone'` nie mogą zastąpić tych
o `source in ('receipt','nominatim','osm')`.

## Krok 3 — geokodowanie Nominatim

Nowy moduł `fuel_tracker/geocode.py` (wzorzec błędów jak `stations.overpass_lookup`:
best-effort, `None` przy każdym problemie, `log.warning`, nigdy nie wysypuje żądania):

```python
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
def geocode_address(conn, street, city, postcode=None, country="Polska") -> tuple[float, float] | None
```

- zapytanie **strukturalne** (`street`, `city`, `postcode`, `country`,
  `format=json`, `limit=1`) — zweryfikowane na adresie z paragonu;
- **`User-Agent` obowiązkowy** — `fuel_tracker/<__version__> (Home Assistant add-on)`;
  Nominatim odrzuca żądania bez UA;
- timeout 5 s;
- **cache w bazie** (Krok 4) — polityka Nominatim to maks. 1 req/s; pojedyncze
  tankowanie robi jedno zapytanie, ale narzędzie porządków (Krok 6) idzie
  pętlą po 12 stacjach → cache + `time.sleep(1)` między nietrafionymi w cache.

## Krok 4 — migracja bazy v11

`db.py`, `_MIGRATIONS` (obecnie 10 pozycji, `PRAGMA user_version` = 10) —
dopisać **wyłącznie schemat**, żadnych wywołań sieciowych w migracji:

```sql
ALTER TABLE stations ADD COLUMN ref TEXT;
ALTER TABLE stations ADD COLUMN street TEXT;
ALTER TABLE stations ADD COLUMN city TEXT;
ALTER TABLE stations ADD COLUMN postcode TEXT;
ALTER TABLE stations ADD COLUMN source TEXT NOT NULL DEFAULT 'legacy';
CREATE UNIQUE INDEX idx_stations_ref ON stations(brand, ref) WHERE ref IS NOT NULL;

CREATE TABLE geocode_cache (
    query TEXT PRIMARY KEY,
    latitude REAL, longitude REAL,
    resolved_at TEXT DEFAULT (datetime('now'))
);
```

`backup.py` / `/api/backup/export.json` obejmują tabele generycznie — sprawdzić,
czy `geocode_cache` ma być w kopii (proponuję **wykluczyć**: to cache,
nie dane użytkownika).

## Krok 5 — `web.py`: koniec cichego fallbacku, skan wygrywa

**`/api/prefill`** (`web.py:578`) — rozdzielić dopasowanie od sugestii:

```python
"station": matched["name"] if matched else None,      # tylko realne dopasowanie
"station_source": "gps" if matched else None,
"station_suggestion": last["station"] if last else None,   # NOWE, osobne pole
```

**`static/app.js`**:
- `:348` — wypełnia pole tylko z `pre.station`; `station_suggestion` renderuje
  się w `#gps-hint` jako klikalna podpowiedź („Ostatnio: … — wstaw"), nie
  wchodzi do pola samo;
- `:297` — usunąć warunek `!form.station.value`: **paragon nadpisuje pole
  zawsze**, z etykietą źródła w `#scan-status` („stacja z paragonu"). To
  naprawia przyczynę nr 1 i ma sens dopiero łącznie z Krokiem 1.

**`/api/receipts/parse`** (`web.py:773`) — po `normalize()` wołać
`stn.resolve_station(...)` z danymi z paragonu, żeby stacja powstała
z geokodowanymi współrzędnymi już w chwili skanu; zwracać w odpowiedzi
`station`, `station_source`, `latitude`, `longitude`.

**`_remember_station`** (`web.py:470`) — przekazywać `source`; pozycję telefonu
zapisywać jako współrzędne stacji **tylko** przy tworzeniu nowej stacji i tylko
gdy nie ma pozycji z geokodowania. Naprawia przyczynę nr 5.

## Krok 6 — narzędzie porządków (podgląd → zatwierdzenie)

Dwa endpointy + karta „Stacje" w `templates/settings.html` (wzorzec jak
istniejące karty importu CSV/Drivvo z `#csv-report` / `#drivvo-report`):

- `GET /api/stations/cleanup/preview` — **nic nie zapisuje**, zwraca listę
  propozycji:
  - **duplikaty**: pary stacji bliżej niż 100 m (`haversine_m`) — znajdzie
    id 10 ↔ id 12 (odległość 0 m); przy każdej liczba tankowań; propozycja:
    scal w tę z tankowaniami;
  - **braki**: stacje bez `brand`/`street` — `overpass_lookup` wokół zapisanych
    współrzędnych (promień 150 m) → `addr:street` + `addr:housenumber` +
    `addr:city` + `brand`; propozycja nowej nazwy wg konwencji;
- `POST /api/stations/cleanup/apply` — stosuje **tylko zaznaczone** pozycje;
  scalenie/przemianowanie w jednej transakcji aktualizuje `stations`
  **i** `fillups.station` (złączenie po nazwie — patrz „Poza zakresem").

UI: lista z checkboxami, domyślnie **nic nie zaznaczone**, przycisk „Zastosuj"
i raport po wykonaniu. Zero automatyki w tle.

**Odstępstwo od wyboru użytkownika, do skreślenia przy akceptacji:** w opcji
„źródła" tagi `addr:*` w `overpass_lookup` nie zostały wybrane, ale Krok 6 i tak
buduje helper OSM → adres. Bez użycia go również w `overpass_lookup`
podpowiedź „stacja w pobliżu" dalej wstawia gołe `"Orlen"` (`stations.py:65`),
czyli odtwarza dokładnie tę kolizję nazw, którą naprawiamy. Proponuję objąć
tym helperem oba miejsca — koszt to kilka linii.

## Krok 7 — testy

Rozszerzyć istniejące pliki, bez sieci (mock `requests` jak w `test_vision.py`):

- `tests/test_receipts.py` — nowe pola w `normalize()`; nazwa złożona
  z konwencji; odporność na adres centrali (asercja: `station_street` nie jest
  „UL. CHEMIKÓW 7"); odwrót na `station_name` gdy brak adresu.
- `tests/test_stations.py` — `compose_name` we wszystkich 4 stopniach
  degradacji; `resolve_station` — kolejność priorytetów, w tym że `name_hint`
  nigdy nie daje `matched=True`; dedup po `(brand, ref)`; reguła „`gps_phone`
  nie nadpisuje `nominatim`".
- **nowy** `tests/test_geocode.py` — parsowanie odpowiedzi Nominatim, trafienie
  i chybienie w cache, `None` przy timeout/HTTP 5xx, obecność `User-Agent`.
- `tests/test_web_api.py` — `/api/prefill` **nie** wypełnia już `station`
  z ostatniego tankowania i zwraca `station_suggestion`.
- **regresja migracji** — baza na `user_version=10` z danymi przechodzi na v11
  bez utraty wierszy; `idx_stations_ref` nie wybucha na `ref IS NULL`.
- `tests/test_manifest.py` — pilnuje zgodności wersji, przejdzie po bumpie.

## Krok 8 — wydanie 0.16.0

Zgodnie z checklistą add-onów: bump **`fuel_tracker/config.yaml`** (`version`)
**i** `fuel_tracker/fuel_tracker/__init__.py` (`__version__`) — oba na `0.16.0`,
push do repo zewnętrznego, **opublikowany** (nie draft) release GitHub z notatkami
opisującymi nowe pola i endpointy, aktualizacja README.

---

## Weryfikacja

1. **Testy jednostkowe** — `pytest` w `fuel_tracker/`; wszystkie przechodzą,
   nowe testy z Kroku 7 czerwone przed implementacją.
2. **Ogniwo lokalne vision** — osobno sprawdzić, że freellmapi (`strict: True`)
   nadal parsuje rozszerzony schemat; jeśli nie, złagodzić schemat dla tego
   ogniwa.
3. **Skan na realnym paragonie** — `POST /api/receipts/parse` z
   `tests/fixtures/receipt_orlen_fleet.jpg`; oczekiwane:
   `station_street="Będzino 87"`, `station_city="Będzino"`,
   `station_ref="4282"`, `station="Będzino 87, Będzino - Orlen"`,
   współrzędne ≈ `54.2088, 15.9835` (a **nie** Płock).
4. **Podgląd porządków** — `GET /api/stations/cleanup/preview` na produkcji musi
   wskazać parę id 10 ↔ id 12 jako duplikat i zaproponować scalenie w id 12
   (to z tankowaniem). Sprawdzić **przed** zastosowaniem, że eksport
   `/api/backup/export.json` się zgadza; zrobić kopię przed „Zastosuj".
5. **Prefill** — otworzyć formularz bez pozycji GPS w zasięgu: pole „Stacja"
   ma zostać **puste**, z klikalną podpowiedzią ostatniej stacji.
6. **Playwright** — po wydaniu: formularz tankowania i karta „Stacje"
   w Ustawieniach, zrzuty do `/config/playwright/`, plus kontrola konsoli
   (`browser_console_messages(error)`).
7. **Kontrola danych po wydaniu** — ponowny `/api/backup/export.json`: 11 stacji
   zamiast 12, `brand` i `street` uzupełnione, żadne tankowanie nie zgubiło
   przypisania do stacji.

## Ryzyka

| Ryzyko | Ograniczenie |
|---|---|
| Scalenie stacji gubi przypisanie tankowań (`fillups.station` to TEXT) | Jedna transakcja na `stations` + `fillups`; podgląd przed zapisem; kopia zapasowa przed „Zastosuj"; test regresji |
| Nominatim odrzuca ruch (brak UA / limit 1 req/s) | Obowiązkowy `User-Agent`, cache w bazie, `sleep(1)` w pętli porządków, best-effort `None` |
| Model bierze adres centrali w Płocku | Jawna instrukcja w prompcie + test asercyjny na fixture |
| `strict: True` w ogniwie freellmapi odrzuca rozszerzony schemat | Osobny test ogniwa lokalnego przed wydaniem (p. 2 weryfikacji) |
| Nadpisywanie pola „Stacja" przez skan psuje ręczną edycję | Nadpisuje tylko wynik skanu (akcja użytkownika), z widoczną etykietą źródła; pole nadal edytowalne |
| Cache WebView Companion nie pokaże nowego `app.js` | Wersjonowany URL statyk `?v=<wersja>` + `no-store` na HTML — zgodnie z regułą projektu |
