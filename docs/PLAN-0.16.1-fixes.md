# Fuel Tracker 0.16.1 — naprawa skutków ubocznych 0.16.0

## Kontekst

0.16.0 ("lokalizacja stacji z adresu na paragonie") jest **wydane, zainstalowane
i uruchomione** (`ha_get_app`: version 0.16.0, state started, update_available
false). `/code-review` znalazł, że wydanie **odtwarza dokładnie ten błąd, który
miało naprawić**, i dokłada drugi mechanizm produkujący stacje-duchy.

Dwa błędy są aktywne na produkcji **teraz**:

1. **Współrzędne każdej istniejącej stacji zostaną nadpisane pozycją telefonu
   przy najbliższym zapisie tankowania.** Przed 0.16.0 `upsert_station` miał
   twardy warunek `if row["latitude"] is None and lat is not None` — współrzędne
   tylko *uzupełniano*, nigdy nie nadpisywano. 0.16.0 zastąpiło to regułą
   priorytetów `coords_upgradeable = new_prio > existing_prio`
   (`stations.py:161`), a w `_COORD_PRIORITY` (`stations.py:30`) `gps_phone`=1
   **przebija** `legacy`=0. Migracja v11 (`db.py:279`) nadaje
   `source NOT NULL DEFAULT 'legacy'` wszystkim istniejącym stacjom, a
   `_remember_station` (`web.py:470`) woła `upsert_station(..., source="gps_phone")`
   przy każdym zapisie formularza. Czyli: pierwszy zapis tankowania po
   aktualizacji przesuwa stację o tyle, ile telefon się mylił (na produkcji
   udokumentowane 3,7 km). Jedyny test tej reguły
   (`test_upsert_station_gps_phone_never_downgrades_address_source`) sprawdza
   wyłącznie parę `nominatim`/`gps_phone` i tego nie łapie.

2. **`POST /api/receipts/parse` zapisuje do tabeli `stations`.** `web.py:847`
   woła `resolve_station`, które wstawia wiersz (`stations.py:228`). Każdy skan —
   także źle odczytany albo porzucony bez zapisu tankowania — zostawia trwałą
   stację. To ta sama "stacja-duch", którą narzędzie porządków z tego samego
   wydania ma sprzątać.

Do tego trzy błędy, które sprawiają, że nazewnictwo i geokodowanie degradują się
do stanu gorszego niż w 0.15.0, oraz zestaw problemów w samym narzędziu
porządków (gubi dane przy scalaniu, blokuje UI na ~84 s, brak escapowania HTML
dla tekstu z OSM).

**Zakres uzgodniony z użytkownikiem: pełny (A–D)** — blokery, narzędzie
porządków, hardening i refaktory jakościowe. Naprawa danych: **najpierw sprawdzić
stan produkcji**, potem dobrać zakres repair.

> **Do czasu wydania 0.16.1 nie zapisywać tankowań przez UI** — każdy zapis
> psuje współrzędne kolejnej stacji, a oryginałów nie da się odtworzyć z bazy.

Repo: `/config/addons/fuel_tracker` (zagnieżdżone, `origin/main` =
`miczu71/fuel_tracker`, HEAD `8cce924`). Gałąź: `main`.

---

## Krok 0 — ustalić realne szkody (przed jakąkolwiek zmianą kodu)

Nie da się tego odczytać z `/config` (baza siedzi w wolumenie add-onu,
`/share/fuel_tracker` niezamontowane, sesja ingress zablokowana przez classifier).
Zgodnie z `reference_playwright_ha_token` + `reference_ui_verification_no_browser`:

1. Playwright MCP → wstrzyknąć LLAT do `localStorage.hassTokens` → wejść na panel
   ingress Fuel Trackera.
2. Odczytać listę stacji (Ustawienia → tabela stacji) i mapę: ile z 12 stacji ma
   już `source='gps_phone'` i czy któraś wyraźnie odjechała od swojej ulicy.
3. Sprawdzić, czy przybyły stacje-duchy z porzuconych skanów (0 tankowań).
4. Screenshot → `/config/playwright/fuel_tracker_0.16.0_stations_before.jpg`.

**Decyzja wynikająca z kroku 0:** jeżeli którakolwiek stacja ma już zepsute
współrzędne, do zakresu wchodzi dodatkowa akcja naprawcza w karcie porządków —
*„Przelicz współrzędne z adresu"* (ponowne geokodowanie każdej stacji, która ma
zapisany street/city, niezależnie od OSM). Jeśli szkód nie ma, wystarcza
poprawione wzbogacanie z OSM (Krok 5), które i tak podnosi współrzędne słabszego
źródła.

---

## Krok 1 — blokery korupcji danych

### 1a. `gps_phone` nigdy nie nadpisuje istniejących współrzędnych
`fuel_tracker/fuel_tracker/stations.py`

Rozdzielić dwie różne rzeczy, które 0.16.0 skleiło w jeden warunek:
uzupełnienie pustego pola (wolno każdemu źródłu) od **korekty** istniejących
współrzędnych (wolno wyłącznie źródłom geokodowanym).

```python
_COORD_PRIORITY = {"nominatim": 3, "osm": 2, "gps_phone": 1, "legacy": 0}
_GEOCODED_SOURCES = {"nominatim", "osm"}
...
coords_missing = row["latitude"] is None
coords_upgradeable = (source in _GEOCODED_SOURCES
                      and new_prio > existing_prio)
```

Przywraca zachowanie sprzed 0.16.0 dla pozycji telefonu, a zostawia to, co
0.16.0 chciało osiągnąć: adres z paragonu (`nominatim`) i OSM nadal *poprawiają*
stację zapisaną wcześniej jako `legacy`/`gps_phone`. Przy okazji znika martwy
poziom `"receipt"` — grep po `source=` w całym pakiecie pokazuje, że nikt go nie
zapisuje, a i tak miał tę samą wagę co `nominatim`.

### 1b. Skan paragonu przestaje zapisywać stacje
`stations.py`, `web.py`

- `resolve_station(..., persist: bool = True)`. Przy `persist=False` funkcja
  dopasowuje istniejące stacje (ref, nazwa) i geokoduje adres, ale **nie robi
  `INSERT` do `stations`**. Zwraca złożoną nazwę, współrzędne i nowy klucz
  `coord_source` (`"nominatim"` albo `None`). Zapis do `geocode_cache` zostaje —
  to cache adresów, nie encja widoczna dla użytkownika.
- `/api/receipts/parse` (`web.py:847`) woła z `persist=False`. Wynik trafia do
  `norm` i — jak dziś — do `attachments.parsed_json`.
- **Jedyny pisarz to zapis tankowania.** `_remember_station` (`web.py:470`)
  dostaje `data.get("attachment_id")`; jeżeli załącznik ma `parsed_json`, a
  `f["station"]` zgadza się z zapisaną tam nazwą, stacja powstaje ze
  współrzędnych z adresu i `source=parsed["coord_source"]`. W przeciwnym razie —
  jak dotąd — pozycja telefonu i `source="gps_phone"`.

Rozstrzygnięcie: wariant serwerowy (odczyt `parsed_json` po `attachment_id`),
nie ukryte pola formularza — nie trzeba ufać danym z klienta ani dokładać pól.

### 1c. `compose_name` — goła marka przestaje wygrywać z adresem
`stations.py:57-78`

Dziś `if brand: return brand` (linia 74) stoi **przed** gałęzią street+city, więc
paragon z marką i ulicą, ale bez miasta, degraduje się do `"Orlen"` — czyli do
kolizji całej sieci pod jedną nazwą, tej samej, którą 0.16.0 miało usunąć.
Nowa drabina, od najbardziej do najmniej konkretnej:

| dane | nazwa |
|---|---|
| ulica + miasto + marka | `Maślicka 218, Wrocław - Orlen` |
| ulica + miasto | `Maślicka 218, Wrocław` |
| miasto + marka | `Wrocław - Orlen` |
| ulica + marka | `Maślicka 218 - Orlen` |
| sama marka | `Orlen` (ostatnia deska ratunku) |

Dodatkowo w `resolve_station` krok 2 wykonuje się **tylko gdy jest `street` albo
`city`** — sama marka nie jest tożsamością stacji i nie ma prawa utworzyć
wiersza. Bez adresu przepływ leci dalej do GPS/`name_hint`.

### 1d. Zapytanie do Nominatim
`stations.py:225`

`geocode.geocode_address(conn, street or city, city, postcode)` wysyła
`street=Wrocław&city=Wrocław` dla paragonu bez ulicy — zapytanie strukturalne,
które **nie może** trafić, a pudło ląduje w cache na zawsze. Poprawka:
`geocode_address(conn, street, city, postcode)`; `geocode.py:60-63` już pomija
puste parametry, a `geocode.py:43` zwraca `None` dopiero gdy oba są puste.

---

## Krok 2 — geokodowanie: TTL pudeł i kontrola miasta

`fuel_tracker/fuel_tracker/geocode.py`

- **TTL dla trafień negatywnych.** Dziś `(key, NULL, NULL)` blokuje adres
  bezterminowo — kolumna `resolved_at` jest zapisywana, ale nigdy nie czytana, a
  żaden endpoint ani UI nie kasuje wierszy. Wprowadzić `_MISS_TTL_DAYS = 30`:
  odczyt dobiera `resolved_at`, i jeżeli `latitude IS NULL` a wpis jest starszy
  niż TTL — pytanie leci do Nominatim jeszcze raz. Trafienia pozytywne zostają
  bezterminowo.
- **Zabezpieczenie przed adresem centrali.** Dziś jedyną ochroną jest akapit
  promptu (`receipts.py:115`) opisujący konkretnie płocką centralę ORLEN — dla
  Shella/BP/Circle K nie ma żadnej reguły, a `nominatim` to najwyższy priorytet
  współrzędnych, więc trafienie w centralę jest **nieodwracalne** przez GPS.
  Dodać `addressdetails=1` i porównać `city/town/village/municipality` z
  odpowiedzi z żądanym `city` (casefold + zdjęcie znaków diakrytycznych); przy
  rozbieżności traktować jak brak trafienia. Kontrola po GPS telefonu jest
  odrzucona świadomie: paragon bywa skanowany w domu, dni po tankowaniu.

---

## Krok 3 — normalizacja marki/adresu (klasa duplikatów ORLEN vs Orlen)

`fuel_tracker/fuel_tracker/receipts.py`, `stations.py`

Model dostaje z paragonu `ORLEN`, a prompt jako przykład podaje `Orlen`.
`WHERE brand = ? AND ref = ?` (`stations.py:203`) i `WHERE name = ?`
(`stations.py:215`) są w SQLite **wrażliwe na wielkość liter**, a
`idx_stations_ref UNIQUE(brand, ref)` też — `('ORLEN','4282')` i
`('Orlen','4282')` to dwa różne wpisy. Efekt: ten sam fizyczny obiekt dwa razy,
mimo mechanizmu, który reklamowaliśmy jako deterministyczny.

- `_canon_brand()` w `receipts.normalize` — mała mapa kanoniczna
  (`ORLEN`/`PKN ORLEN` → `Orlen`, `SHELL` → `Shell`, `BP`, `LOTOS`, `CIRCLE K`,
  `MOYA`, `AMIC`, `MOL`, `AVIA`, `TOTAL`…), fallback `.title()`.
- `_canon_place()` dla `station_street`/`station_city` — `.title()` tylko gdy
  wejście jest w całości wersalikami (paragony drukują `BĘDZINO 87`;
  `str.title()` radzi sobie z polskimi znakami), inaczej zostawić jak jest.
- Zapasowo `COLLATE NOCASE` w obu lookupach w `stations.py`.
  **Uwaga do zapisania w komentarzu:** `NOCASE` w SQLite składa wyłącznie ASCII,
  więc `Ę`/`ę` nie złoży — właściwą naprawą jest normalizacja na wejściu, a
  kolacja jest tylko siatką bezpieczeństwa.

---

## Krok 4 — narzędzie porządków: przestaje gubić dane

`stations.py`

### 4a. `apply_merge` (`stations.py:319`)
Dziś wybiera ocalałego wyłącznie po liczbie tankowań i **kasuje drugi wiersz w
całości** — razem z `ref`, `street`, `city`, `postcode` i lepiej udokumentowanymi
współrzędnymi. Na produkcji to znaczy: wiersz z jednym tankowaniem i pozycją
telefonu wygrywa z wierszem geokodowanym z paragonu, po czym „braki" wracają
przy następnym skanowaniu.

- Wybór ocalałego: liczba tankowań → przy remisie bogatszy wiersz (licznik
  niepustych `ref/street/city/postcode`) → przy remisie niższe `id`.
- Przed `DELETE`: przenieść do ocalałego każde pole, które u niego jest `NULL`, a
  w usuwanym nie jest. `ref` tylko wtedy, gdy nie łamie `UNIQUE(brand, ref)`.
- Współrzędne: jeśli `_COORD_PRIORITY` usuwanego jest wyższy — przejąć jego
  `latitude/longitude/source`.

### 4b. `apply_enrichment` (`stations.py:335`)
- Dziś ustawia `source = 'osm'` **bezwarunkowo, nie ruszając współrzędnych** —
  czyli oznacza cudzą (często telefonową) pozycję jako zaufaną i blokuje
  późniejszą korektę. Nowa reguła: `source` zmienia się **tylko razem ze
  współrzędnymi**. Gdy obecne źródło jest słabsze niż `osm`, przyjąć pozycję z
  OSM (`_tags_to_result` już ją zwraca — dołożyć `latitude`/`longitude` do
  propozycji i do wywołania w `web.py:672`); inaczej zostawić oba pola.
  To jest jednocześnie ścieżka naprawy stacji zepsutych przez błąd 1a.
- Kolizja nazw: sprawdzić `UNIQUE(name)` **przed** `UPDATE` i rzucić czytelne
  `ValueError("Nazwa „…" jest już zajęta — scal stacje zamiast uzupełniać")`
  zamiast wpuszczać surowy `sqlite3.IntegrityError` do JSON-a pokazywanego
  użytkownikowi (dwie propozycje z tego samego węzła OSM potrafią wskazać tę samą
  nazwę).
- Propozycje bez `street` i bez `city` odrzucać — inaczej wzbogacanie proponuje
  przemianowanie `Wrocław - Orlen` → `Orlen`, a `apply_enrichment` przepisuje
  przy tym `fillups.station` dla całej historii.

### 4c. Duplikaty klastrami, nie parami (`stations.py:265`)
Trzy stacje w promieniu 100 m dają dziś trzy pary; zaznaczenie wszystkich kończy
się jednym scaleniem i dwoma `ValueError: Stacja nie istnieje`. Policzyć
domknięcie przechodnie w promieniu `DUPLICATE_RADIUS_M`, wybrać jednego ocalałego
na klaster i wystawić po jednej parze *nie-ocalały → ocalały*. W pętli w
`web.py:661` zniknięty wiersz raportować jako pominięcie („już scalona"), nie
jako błąd. Przy okazji `LEFT JOIN … GROUP BY` zamiast skorelowanego podzapytania
liczącego tankowania (dziś jeden pełny skan `fillups` na stację).

---

## Krok 5 — koszt Overpass (dziś ~84 s w jednym żądaniu GET)

`stations.py:293`, `web.py:651`, `static/app.js`

`find_enrichable_stations` robi jedno blokujące `POST` do Overpass **na stację**,
szeregowo, z `timeout=OVERPASS_TIMEOUT_S + 2` = 7 s, w środku handlera GET. Filtr
to `brand IS NULL OR street IS NULL`, a migracja v11 dodaje `street` jako `NULL`
wszystkim — czyli po aktualizacji kwalifikują się wszystkie 12 stacji. Obietnica
`time.sleep(1)` z planu 0.16.0 (`docs/PLAN-0.16.0-stations.md:169-170, 308`) nie
została zaimplementowana nigdzie. `app.js:1122` powtarza cały przemiał zaraz po
„Zastosuj", podwajając koszt.

- **Rozdzielić endpointy.** `GET /api/stations/cleanup/preview` zwraca same
  duplikaty (czysty SQLite, milisekundy). Nowy `GET /api/stations/cleanup/enrich`
  robi przemiał OSM. Dziś tania i użyteczna połowa wyniku jest zakładnikiem tej
  sieciowej i przepada razem z nią przy timeoucie ingressu.
- **Limit + throttle:** `ENRICH_MAX_STATIONS = 8` na przebieg,
  `time.sleep(1)` między zapytaniami (poza pierwszym); zwracać `remaining`, żeby
  UI napisało „sprawdzono 8 z 12 — kliknij ponownie".
- **Cache w procesie** na (zaokrąglone lat, lon, promień) z TTL 1 h — powtórny
  klik nie odpytuje o stacje, które się nie ruszyły.
- **Odróżnić awarię od braku braków.** Dziś `overpass_lookup` łyka każdy wyjątek
  i zwraca `[]`, więc 429 od Overpassu wygląda jak „wszystko uzupełnione".
  Wydzielić `_overpass_raw()` (rzuca), zostawić `overpass_lookup()` bez zmian dla
  `/api/stations/nearby`, a `find_enrichable_stations` niech liczy błędy i zwraca
  je w odpowiedzi.
- Po „Zastosuj" front odświeża **tylko duplikaty**; listę wzbogaceń czyści
  lokalnie z zastosowanych pozycji.

---

## Krok 6 — front

`fuel_tracker/fuel_tracker/static/app.js`

- **Escapowanie HTML.** `renderStationsPreview` (`app.js:1074-1096`) wstawia
  nazwy stacji wprost do `innerHTML`. Do 0.16.0 były to wyłącznie teksty wpisane
  przez użytkownika; teraz `proposed_name` pochodzi z tagów OSM, czyli z bazy
  edytowalnej przez kogokolwiek. Dodać `esc()` i użyć go tu **oraz** w istniejącym
  renderze tabeli stacji (ta sama klasa). Bez tego nawet zwykłe `&` albo `<` w
  nazwie psuje render.
- **`try/catch/finally` na „Zastosuj zaznaczone"** (`app.js:1110`) — dziś odrzucone
  żądanie zostawia przycisk trwale wyszarzony, bez komunikatu, do przeładowania
  strony. Handler „Sprawdź stacje" ma `finally`, ale połyka błąd po cichu — dołożyć
  widoczny komunikat.
- Usunąć bezwarunkowe `stApplyBtn.disabled = false` po re-renderze (`app.js:1124`)
  — nadpisuje decyzję `renderStationsPreview` i zostawia klikalny przycisk przy
  pustej liście. Dołożyć tekst „Zapisuję…" na czas żądania.
- **Podpowiedź OSM przestaje wpisywać się sama.** `app.js:392` robi
  `form.station.value = near[0].name` bez akcji użytkownika. Ta gałąź była
  praktycznie martwa, dopóki prefill wypełniał pole ostatnią stacją; usunięcie
  tamtego fallbacku ją **odblokowało**, więc deklarowana w CHANGELOG-u zasada
  „nic nie wchodzi do pola samo" nadal jest łamana — tylko inną drogą. Zamienić na
  przycisk do kliknięcia, dokładnie jak `renderSuggestion`.
- **Skan nie rusza `form.latitude/longitude`** (`app.js:302`). Pozycja telefonu ma
  zostać pozycją tankowania; współrzędne stacji jadą teraz serwerowo (Krok 1b).
  Dodatkowo w trybie edycji skan uzupełnia pole stacji tylko wtedy, gdy jest puste
  — dziś nadpisuje zapisaną stację edytowanego wpisu bez ostrzeżenia.

---

## Krok 7 — refaktory jakościowe (Tier D)

Bez zmiany zachowania, wszystkie w miejscach dotkniętych powyżej:

- `resolve_station` — jeden `_result(row, source)` zamiast czterech ręcznie
  przepisanych dictów; jedno wyjście na gałąź zamiast dwóch bliźniaczych bloków
  „upsert → re-SELECT → return".
- `upsert_station` zwraca wiersz zamiast samego `id` (drugi i ostatni wywołujący,
  `_remember_station`, i tak ignoruje wynik) — znikają dwa `SELECT * FROM stations
  WHERE id = ?` tuż po zapisie.
- Pięć skopiowanych bloków „uzupełnij brak" w `upsert_station:165-179` → pętla po
  `(kolumna, wartość)`. SQL jest sklejany f-stringiem, więc rozjazd nazwy kolumny
  i wartości nie ma jak się ujawnić inaczej niż błędnymi danymi.
- `app.js`: jeden `renderCheckList(box, items, {...})` zamiast dwóch identycznych
  bloków, jeden `picked(box, attr, items)` zamiast dwóch zbieraczy checkboxów.
- `receipts.normalize` przestaje składać nazwę (`receipts.py:290`) i importować
  `stations` — zwraca surowe `station_name` + pola `station_*`, a nazwę ustala
  wyłącznie `resolve_station`, zgodnie z własnym docstringiem modułu („jedyne
  miejsce, które decyduje"). `web.py` bierze `resolved["name"] or norm["station"]`.
  Do poprawienia stub w `test_receipts.py`, który sięga dziś przez dwa moduły
  (`receipts.stations.geocode.geocode_address`).
- `web.py:613`: usunąć `station_source` z `/api/prefill` — nikt tego nie czyta
  (`grep station_source static/app.js` pusty), a wartość jest identyczna z
  sąsiednim `station_matched`.
- `backup.py:27`: `geocode_cache` zostaje poza eksportem (to cache), ale dopisać go
  do czyszczenia w `import_json`, żeby „pełne zastąpienie" nie zostawiało cudzych
  adresów.

**Świadomie bez migracji.** Wszystkie poprawki mieszczą się w kodzie, więc schemat
zostaje na v11 — `current_schema_version()` to `len(_MIGRATIONS)`, a `import_json`
wymaga równości, więc bump zepsułby import kopii JSON zrobionych na 0.16.0.

---

## Krok 8 — testy

Rozszerzyć `test_stations.py`, `test_geocode.py`, `test_receipts.py`,
`test_web_api.py`. Testy, których brak przepuścił te błędy:

- `legacy` + `gps_phone` → współrzędne **bez zmian** (dziura, przez którą przeszedł
  bloker 1a — istniejący test pokrywa tylko `nominatim`).
- `legacy` + `nominatim` → współrzędne podniesione (korekta ma nadal działać).
- `NULL` + `gps_phone` → uzupełnione.
- `/api/receipts/parse` **nie zwiększa** `COUNT(*) FROM stations`.
- Zapis tankowania z `attachment_id` → stacja ze współrzędnymi z adresu i
  `source='nominatim'`, nie z telefonu.
- `compose_name`: marka + ulica bez miasta → **nie** goła marka.
- `resolve_station` z samą marką → `matched=False`, zero nowych wierszy.
- `geocode_address` bez ulicy → `street` nie jest wysyłane; pudło starsze niż TTL
  → ponowne zapytanie; miasto z odpowiedzi ≠ żądane → traktowane jak pudło.
- `ORLEN`/`Orlen` z tym samym `ref` → jedna stacja.
- `apply_merge` przenosi `ref/street/city/postcode` i lepsze współrzędne.
- `apply_enrichment` nie zmienia `source` bez zmiany współrzędnych; kolizja nazw →
  czytelny `ValueError`.
- Trzy stacje w klastrze → dwie pary, zaznaczenie wszystkich = zero błędów.
- `find_enrichable_stations` respektuje limit i nie woła Overpassu ponad `remaining`
  (z zamockowanym `_overpass_raw` — dziś `test_web_api.py:194` chodzi do sieci
  naprawdę).

---

## Krok 9 — wydanie 0.16.1

Zgodnie z `feedback_ha_addon_release` + `feedback_release_notes` +
`feedback_no_local_rebuild`:

1. Bump **obu** plików: `fuel_tracker/config.yaml` → `0.16.1` i
   `fuel_tracker/fuel_tracker/__init__.py` → `__version__ = "0.16.1"`.
   (`config.yaml` w podkatalogu, nie w root — repo ma `repository.json`.)
2. `CHANGELOG.md` + sekcje README dotknięte zmianą zachowania (nazewnictwo stacji,
   podpowiedź OSM do kliknięcia, rozdzielone endpointy porządków).
3. Commit + push na `main`, potem **opublikowany** (nie draft) release `v0.16.1`
   z rozpisanym opisem; zweryfikować, że wersja w `config.yaml` == tag.
4. Aktualizacja przez Supervisora (`ha_manage_app`), nie lokalny rebuild.
   Przy „No update available" — `homeassistant.update_entity` na
   `update.fuel_tracker_update` + poll (`reference_supervisor_store_reload`).

Cache frontu jest już załatwiony infrastrukturalnie (`after_request` z `no-store`,
`?v={{ version }}` z `immutable`, badge wersji w nawigacji) — bump wersji sam
przestempluje statyki.

---

## Weryfikacja

1. `pytest` w `fuel_tracker/` — całość zielona, z nowymi testami z Kroku 8.
2. Po aktualizacji sprawdzić `ha_get_app(slug="f123e1f2_fuel_tracker")` →
   `version: 0.16.1`, `state: started`.
3. **Playwright na produkcji** (`feedback_playwright_self_review` +
   `feedback_playwright_check_console` — 0.16.0 wyszło bez tego, wbrew
   `/config/CLAUDE.md`; w `/config/playwright/` nie ma ani jednego zrzutu 0.16.0):
   - Ustawienia → „Sprawdź stacje": karta renderuje się **od razu** (duplikaty), a
     wzbogacanie z OSM osobno i z limitem.
   - Zaznaczyć i zastosować scalenie → sprawdzić w tabeli stacji, że ocalały wiersz
     **zachował** `ref`/ulicę/miasto i lepsze współrzędne.
   - Wymusić błąd (zatrzymany add-on) → przycisk wraca do stanu klikalnego z
     komunikatem, nie zostaje wyszarzony.
   - `browser_console_messages(error)` po każdym zrzucie.
   - Zrzuty → `/config/playwright/fuel_tracker_0.16.1_*.jpg`.
4. **Test kluczowego blokera na żywych danych:** zanotować współrzędne jednej
   stacji, zapisać tankowanie z telefonu, sprawdzić, że współrzędne stacji **się
   nie ruszyły** (na 0.16.0 przesuwają się o błąd pozycji telefonu).
5. Skan paragonu → `COUNT(*)` stacji rośnie **dopiero** po zapisaniu tankowania,
   nie po samym skanie; porzucony skan nie zostawia śladu.
6. Porównać mapę ze zrzutem `*_before.jpg` z Kroku 0.

## Poza zakresem

- `fillups.station` zostaje kolumną TEXT (złączenie po nazwie) — zmiana na FK to
  osobna migracja, świadomie odłożona już w planie 0.16.0.
- Świeży `/code-review` nokia_trackera 0.17.2 zwrócił 10 osobnych znalezisk
  (m.in. `_section_g` liczący wiersze dywidend zamiast wypłat — czwarty
  niepoprawiony konsument nieunikalnego `pay_date`). Inne repo, osobne zadanie.
