# Changelog

## 0.16.2

- **Fix: Overpass odrzucał KAŻDE zapytanie (406 Not Acceptable) brakiem
  `User-Agent`** — odkryte dopiero po wdrożeniu 0.16.1 na produkcji: nowy
  jawny błąd (Krok 5) ujawnił coś, co wcześniej ciche `except: return []`
  ukrywało od 0.16.0 jako "brak braków do uzupełnienia". Wzbogacanie
  z OSM (i sugestie `/api/stations/nearby` w formularzu) w praktyce nigdy
  nie działało. Dodany `User-Agent` (ten sam wzorzec co `geocode.py` dla
  Nominatim) naprawia obie ścieżki.

## 0.16.1

- **Fix bloker: 0.16.0 odtwarzało dokładnie ten błąd, który miało
  naprawić — współrzędne KAŻDEJ z 12 istniejących stacji zostałyby
  nadpisane pozycją telefonu przy najbliższym zapisie tankowania.**
  `_COORD_PRIORITY` dawało `gps_phone`(1) > `legacy`(0) — a migracja v11
  nadaje `source='legacy'` wszystkim stacjom sprzed 0.16.0. Przywrócona
  reguła sprzed 0.16.0: pozycja telefonu tylko UZUPEŁNIA puste
  współrzędne, nigdy nie nadpisuje; NADPISAĆ (skorygować) może wyłącznie
  inne źródło geokodowane (adres z paragonu/OSM). Znaleziony przez
  `/code-review` przed jakimikolwiek stratami danych na produkcji.
- **Fix: `POST /api/receipts/parse` przestaje zapisywać do `stations`.**
  Każdy skan paragonu — także źle odczytany albo porzucony bez zapisania
  tankowania — zostawiał trwałą „stację-ducha" z zerem tankowań (dokładnie
  to, co narzędzie porządków z tego samego wydania miało sprzątać). Skan
  jest teraz czystym podglądem (`resolve_station(persist=False)`);
  jedynym pisarzem jest zapis tankowania, który czyta geokodowane
  współrzędne z załącznika skanu zamiast zawsze brać pozycję telefonu.
- **Fix: `compose_name()` degradowało do gołej marki ZA WCZEŚNIE** —
  paragon z marką i ulicą, ale bez miasta, kolidował z całą siecią pod
  jedną nazwą ("Orlen"), czyli w błąd, który 0.16.0 miało usunąć. Nowa
  drabina degradacji ulica+miasto → miasto+marka → ulica+marka → sama
  marka (ostatnia deska ratunku); `resolve_station()` w ogóle nie tworzy
  stacji z samej marki.
- **Fix: zapytanie do Nominatim bez ulicy wysyłało `street=<miasto>`**
  (gwarantowane pudło, zapamiętywane w cache bezterminowo). Geokodowanie
  dostaje teraz `street`/`city` osobno; puste pola są pomijane.
- Kanonizacja marki/adresu z paragonu (`ORLEN`→`Orlen`, `BĘDZINO`→
  `Będzino`) — SQLite dopasowuje `brand`/`name` dokładnym stringiem, więc
  bez tego ten sam fizyczny obiekt (ORLEN vs Orlen) tworzył dwie stacje.
- **TTL dla pudeł geokodowania** (30 dni) — dawniej brak trafienia
  blokował adres bezterminowo (`resolved_at` zapisywane, nigdy nie
  czytane). **Kontrola miasta** w odpowiedzi Nominatim — bez niej
  trafienie w adres centrali spółki (np. Płock dla paragonów ORLEN)
  lądowało jako źródło najwyższego priorytetu, nieodwracalne przez GPS.
- **Narzędzie porządków przestaje gubić dane przy scalaniu.** `apply_merge`
  kasowało bezpowrotnie ref/adres/lepsze współrzędne usuwanej stacji, gdy
  to ona miała mniej tankowań (dokładnie odwrotność pożądanego). Teraz
  przenosi brakujące pola tożsamości i lepiej udokumentowane współrzędne
  do ocalałej stacji przed usunięciem. `apply_enrichment` zmienia `source`
  TYLKO razem ze współrzędnymi (dawniej oznaczało istniejącą, często
  telefonową pozycję jako zaufaną „osm" bez jej zmiany) — ta ścieżka
  naprawia też stacje uszkodzone przez błąd priorytetów wyżej. Kolizja
  nazw przy wzbogacaniu zgłasza się czytelnym komunikatem zamiast
  surowego błędu SQLite.
- **Duplikaty grupowane klastrami, nie parami.** Trzy stacje w promieniu
  100 m dawały trzy pary — zaznaczenie wszystkich kończyło się jednym
  scaleniem i dwoma błędami „Stacja nie istnieje". Teraz jeden ocalały na
  klaster.
- **Podgląd porządków rozdzielony na dwa żądania.** Duplikaty (czysty
  SQLite, milisekundy) i braki marki/adresu (Overpass — jedno zapytanie
  sieciowe NA STACJĘ, do ~84 s na 12 stacjach) były jednym żądaniem;
  duplikaty ginęły razem z timeoutem sieciowym. `GET
  /api/stations/cleanup/enrich` jest teraz osobny, limitowany
  (8 stacji/przebieg) i throttlowany.
- Front: nazwy stacji w karcie porządków escapowane (`proposed_name`
  pochodzi teraz z OSM — bazy edytowalnej przez kogokolwiek, nie tylko od
  użytkownika); przycisk „Zastosuj" ma `try/catch` i nie zostaje trwale
  wyszarzony po błędzie; podpowiedź OSM w formularzu jest teraz klikalna,
  nie wpisuje się do pola sama (drugi cichy fallback, odblokowany przez
  usunięcie pierwszego w 0.16.0).
- Pełna diagnoza (`/code-review`) i plan w `docs/PLAN-0.16.1-fixes.md`.

## 0.16.0

- **Fix: stacja z paragonu (📷) nigdy nie trafiała do formularza —
  trzeba było ją poprawiać ręcznie po każdym tankowaniu w Orlenie.**
  Diagnoza na żywych danych (`/api/backup/export.json` z produkcji):
  siedem współdziałających przyczyn, opisanych w
  `docs/PLAN-0.16.0-stations.md`. Główna: `GET /api/prefill` wpisywał
  cicho ostatnio użytą stację do pola `station` PRZED skanem, a warunek
  `!form.station.value` we froncie później blokował skan przed
  nadpisaniem — nazwa z paragonu nigdy nie wygrywała.
- **Adres stacji wyciągany wprost z paragonu.** Schemat vision
  (`receipts.py`) dokłada `station_brand/street/city/postcode/ref`;
  prompt uczy model odróżniać adres STACJI od siedziby spółki (paragony
  ORLEN pokazują centralę w Płocku nad adresem stacji — trzeba ją
  pomijać). Nazwa stacji budowana wg konwencji
  „{ulica} {nr}, {miasto} - {Marka}" (`stations.compose_name()`).
- **Nowy moduł `geocode.py`** — adres z paragonu geokodowany przez
  Nominatim, z cache w bazie (tabela `geocode_cache`) i obowiązkowym
  `User-Agent`. Stacja dostaje prawdziwe współrzędne stacji zamiast
  pozycji telefonu w chwili tankowania (zaobserwowane na produkcji: do
  3,7 km rozjazdu, bo telefon bywa już w drodze).
- **`stations.resolve_station()`** — jedno miejsce decydujące, jaka
  stacja odpowiada tankowaniu, priorytet: numer stacji z paragonu
  (marka+ref, deterministyczne) → adres z paragonu (geokodowany) → GPS
  telefonu → ostatnio użyta nazwa (TYLKO jako podpowiedź do kliknięcia,
  nigdy jako ciche dopasowanie — usunięty silent fallback z `/api/prefill`).
- **`GET /api/stations/nearby` i sugestie OSM budują nazwę z tagów
  `addr:*` + `brand`, nie z gołego `tags.name`** (dla Orlenu w OSM
  `name == "Orlen"` — bez tego wszystkie stacje sieci kolidowały pod
  jedną nazwą).
- **Narzędzie porządków** (Ustawienia → „Stacje", nowe endpointy `GET
  /api/stations/cleanup/preview` / `POST /api/stations/cleanup/apply`) —
  wykrywa zdublowane stacje (te same współrzędne pod dwiema nazwami,
  np. ducha po ręcznej zmianie nazwy) i braki marki/adresu, z podglądem
  przed zapisem; nic nie zmienia się bez jawnego zaznaczenia.
- Migracja bazy v11: `stations.ref/street/city/postcode/source`,
  unikalny indeks `(brand, ref)`, nowa tabela `geocode_cache`.
  Współrzędne z pozycji telefonu (`source='gps_phone'`) nie mogą już
  nadpisać współrzędnych z adresu/geokodowania.
- Diagnoza (dane z produkcji), plan i pełna lista przyczyn w
  `docs/PLAN-0.16.0-stations.md`.

## 0.15.0

- **Fix: skaner paragonów padał z „błędem komunikacji z LLM Vision" bez
  prawdziwego powodu.** Diagnoza: konto Google za jedynym providerem
  llmvision miało wyczerpane kredyty (`ServiceValidationError`) — HA
  oddawało gołe HTTP 500, `ha_client.call_service()` zwracał `None`, a
  add-on pokazywał generyczne „nie odpowiedziała". Przy okazji wykryto,
  że fallback modeli w `receipts.analyze()` był **martwy od 0.5.1**:
  pętla `for model in MODELS` na HTTP 500/`None` rzucała natychmiast
  zamiast próbować kolejny model — wykonywała się zawsze tylko raz.
- **Nowy łańcuch trzech providerów** (`fuel_tracker/vision.py`, nowy
  moduł): Gemini bezpośrednio (`gemini_api_key`) → lokalny router
  OpenAI-compatible (`local_llm_base_url`/`local_llm_api_key`, np.
  freellmapi) → istniejące `llmvision` przez HA jako ostatnie ogniwo.
  Ogniwo, które zawiedzie, oddaje głos następnemu; `ReceiptError` dopiero
  gdy wszystkie padną, z powodami wszystkich prób sklejonymi w jeden
  komunikat (prawdziwa treść błędu providera, nie generyk). Oba nowe
  ogniwa opcjonalne — puste pole w opcjach add-onu = ogniwo pomijane,
  zachowanie identyczne z ≤0.14.0.
- **Fix: `gemini-2.5-flash-lite` w liście modeli zwracał HTTP 404** („no
  longer available to new users") na nowych kluczach Gemini — jedyny
  „zapasowy" model add-onu był od jakiegoś czasu martwy. Zastąpiony
  `gemini-3.5-flash-lite` (zweryfikowany na realnym paragonie).
- Nowe opcje: `gemini_api_key`, `gemini_model`, `local_llm_base_url`,
  `local_llm_api_key`, `local_llm_model` (domyślnie `gemini-3.1-flash-lite`).
  Zero zmian we froncie — `static/app.js` już renderował `error` z
  odpowiedzi 502, więc naprawiony komunikat dociera bez modyfikacji UI.
- Diagnoza i plan w `docs/PLAN-0.15.0-vision.md`, w tym pełny zapis
  weryfikacji empirycznej (realne wywołania obu nowych ogniw na
  fixturach paragonów przed wdrożeniem).

## 0.14.0

- **Fix: sensory MQTT `state_class` niezgodny z charakterem wartości psuł
  statystyki długoterminowe (LTS) w HA.** Wszystkie sensory `monetary`
  publikowały `state_class: "total"`, czyli "licznik, który tylko rośnie" —
  dla `budget_left_month` (maleje w miesiącu), `month_forecast_cost` i
  `last_fillup_cost` (skaczą w obie strony) silnik statystyk HA zaliczał
  każdy spadek jako reset licznika i dopisywał całą nową wartość do sumy LTS
  zamiast realnego przyrostu. Te trzy sensory tracą teraz `state_class`
  (dozwolone: `device_class: monetary` nie wymaga `state_class`, walidator HA
  odrzuca tylko niekompatybilny typ, nie jego brak) — bieżący stan i wykresy
  "na teraz" bez zmian, tylko wykresy historyczne przestają kłamać.
- **Fix: `month_fuel_cost` i `ytd_fuel_cost` (liczniki miesięczny/roczny) nie
  miały `last_reset`**, więc ich comiesięczne/coroczne zerowanie też mylnie
  wchodziło do sumy LTS jako "reset". Zostają na `state_class: "total"`, ale
  discovery MQTT dostaje `value_template` + `last_reset_value_template`
  (payload JSON zamiast gołej liczby), a `queries.sensor_values()` liczy
  znaczniki resetu (1. dzień miesiąca / 1 stycznia, ze strefą `TZ`).
- **Wymagane ręczne czyszczenie statystyk po aktualizacji** (Narzędzia
  deweloperskie → Statystyki): dla `budget_left_month`, `month_forecast_cost`,
  `last_fillup_cost` HA zgłosi "encja nie ma już state_class" — skasować ich
  historię. Dla `month_fuel_cost`/`ytd_fuel_cost` też warto skasować dawną
  (błędną) historię LTS, żeby nowy cykl `last_reset` ruszył czysto.
- Dotknięte encje: `sensor.<pojazd>_fuel_budget_left_month`,
  `sensor.<pojazd>_fuel_month_forecast_cost`,
  `sensor.<pojazd>_fuel_last_fillup_cost`,
  `sensor.<pojazd>_fuel_month_fuel_cost`,
  `sensor.<pojazd>_fuel_ytd_fuel_cost`. Wartości stanu (state) tych encji się
  nie zmieniają — zmieniają się wyłącznie ich atrybuty/discovery i to, jak HA
  agreguje ich historię.
- Zero zmian w formacie danych/API poza opisanym wyżej. 268 + 5 nowych testów
  regresyjnych zielonych.

## 0.13.1

- **Fix (krytyczny): alert budżetu paliwowego martwy od 0.11.0.**
  `notifications.py` bramkował alert na `settings["monthly_fuel_budget"]` —
  migracja #9 (0.11.0) przeniosła to pole do `vehicles.monthly_fuel_budget`
  i usunęła klucz z `settings`, więc bramka zawsze czytała pustkę i alert
  nigdy nie odpalał, mimo że był włączony i skonfigurowany. Bramka usunięta
  — `budget_left_month` z sensorów już koduje "budżet nieustawiony" jako
  `None`, więc jest zbędna.
- **Fix: usunięta/przemianowana domyślna kategoria wydatku wracała po
  restarcie add-onu.** Seed domyślnych kategorii uruchamiał się przy
  każdym starcie, nie tylko na świeżej instalacji — usunięcie kategorii
  było nietrwałe, a zmiana nazwy tworzyła duplikat (stara nazwa "wolna"
  → wsiewana z powrotem). Seed teraz działa wyłącznie, gdy tabela kategorii
  jest pusta.
- **Fix: raport miesięczny i podział kosztów gubiły kategorię „Płyny” po
  zmianie jej nazwy.** Klasyfikacja szła po dopasowaniu tekstowym do
  `"Płyny"` zamiast po `tco_group` — funkcja własnych kategorii (0.13.0)
  pozwala tę nazwę zmienić, co po cichu przesuwało kwoty do „Inne wydatki”
  w raporcie miesięcznym, `/api/statistics` (`split`) i eksporcie CSV.
- **Fix: usunięcie pojazdu z historią alertów kończyło się błędem 500.**
  `alert_state` (stan anty-flap powiadomień) ma klucz obcy do pojazdu;
  usuwanie nie czyściło tych wierszy, więc `DELETE /api/vehicles/<id>`
  wywalało się na naruszeniu integralności dla każdego auta, dla którego
  scheduler zdążył choć raz policzyć alert.
- **Fix: drugi identyczny wydatek (ta sama data/kwota/opis) kończył się
  błędem 500** zamiast czytelnego komunikatu — brakowało obsługi
  konfliktu unikalności, którą tankowania miały od dawna.
- **Fix: nieprawidłowe wartości liczbowe w formularzu pojazdu.**
  `POST /api/vehicles` z tekstem w polu pojemności baku/raty/limitu kończył
  się błędem 500; `PUT /api/vehicles/<id>` w ogóle nie sprawdzał typów i po
  cichu zapisywał śmieć do kolumny liczbowej (SQLite na to pozwala) —
  zepsute dane wychodziły dopiero przy kolejnym wyliczeniu TCO/leasingu.
  Oba endpointy walidują teraz pola liczbowe i zwracają 400.
- Zero zmian w formacie danych/API poza kodami błędów opisanymi wyżej
  (400/409 zamiast 500). 268/268 testów zielonych (13 nowych regresyjnych).

## 0.12.1

- **Porządki po 0.12.0** — bez zmian funkcjonalnych:
  - Usunięta martwa reguła CSS `.verify-bad` (jedyni konsumenci zniknęli
    razem z kartą weryfikacji w 0.12.0).
  - Klasa `.verify-ok` przemianowana na `.badge-active` — styluje wyłącznie
    znacznik „aktywny" przy pojeździe, nazwa nie sugeruje już usuniętej
    weryfikacji.
  - Poprawiony komentarz sekcji API w `web.py` (bez „weryfikacji").

## 0.12.0

- **Sanityzacja repo publicznego** — bez zmian funkcjonalnych w dzienniku,
  statystykach i sensorach:
  - Import/eksport CSV opisywany neutralnie (moduł przemianowany na
    `csv_io.py`); nowe wpisy z importu CSV dostają tag źródła `csv`
    (stare wiersze zachowują dotychczasowy tag — nic po nim nie filtruje).
  - **Route eksportu przeniesiony** na `GET /api/export/log.csv`
    (poprzedni route usunięty; plik pobiera się jako
    `fuel_tracker_export.csv`).
  - **Usunięty `GET /api/verify`** wraz z kartą „Weryfikacja migracji"
    w Ustawieniach — jednorazowy raport porównawczy z czasów migracji
    z Drivvo, na stałe przypięty do konkretnej instalacji; import z API
    Drivvo zostaje bez zmian.
  - Domyślne opcje add-onu zgenerycyzowane (nazwa pojazdu, encje HA,
    budżet, region cen, usługa notify) — istniejących instalacji nie
    dotyczy (Supervisor trzyma opcje użytkownika, seed tylko przy
    pierwszym starcie).
  - Testy przepisane na w pełni syntetyczne dane (226/226 zielonych).

## 0.11.1

- **Fix: usunięcie/archiwizacja pojazdu czyści jego urządzenie MQTT** —
  znalezione podczas weryfikacji produkcyjnej 0.11.0: `DELETE
  /api/vehicles/<id>` i `POST /api/vehicles/<id>/archive` usuwały pojazd
  z bazy, ale zostawiały jego discovery retained na brokerze na zawsze —
  encje sensor.* zostawały osierocone w rejestrze HA mimo że pojazd już
  nie istniał. Nowy `MQTTPublisher.unpublish_device()` publikuje puste
  retained payloady do wszystkich topików discovery danego urządzenia;
  `web.py` wywołuje go po udanym delete/archive (przed `changed()`, żeby
  ewentualne odtworzenie nowego aktywnego auta na tym samym gołym
  `fuel_tracker` topiku — gdy usuwane/archiwizowane było auto aktywne —
  poszło już po czyszczeniu, nie przed nim).
- Nie dotyczy to samego `unarchive` (przywrócenie pojazdu po prostu
  republikuje jego discovery przy najbliższym ticku).

## 0.11.0

- **Pełny multi-vehicle** — kilka aut z równoległymi, żywymi sensorami MQTT
  naraz (dotąd: jedno aktywne auto, historia poprzednich zachowana bez
  publikacji). Przełącznik pojazdu w navbarze na każdej stronie (`<select>`
  zasilany z `GET /api/vehicles`, zapamiętywany w `localStorage` i
  odzwierciedlony w URL jako `?vehicle_id=`).
- **Entity_id bez zmian dla dotychczasowego auta** — aktywny pojazd zostaje
  na dzisiejszym stałym prefiksie urządzenia MQTT (`fuel_tracker`,
  `sensor.<pojazd>_fuel_*`) — zero migracji `template.yaml`/utility_meter/
  dashboardu. Każde KOLEJNE dodane auto dostaje własny, odrębny prefiks
  urządzenia (`fuel_tracker_<id>`) z własnym kompletem sensorów.
- **Budżet i encje HA są teraz per pojazd** — miesięczny budżet paliwowy,
  encja odometru, poziomu paliwa i lokalizacji telefonu przeniesione z
  globalnych Ustawień do formularza każdego pojazdu (karta „Pojazdy").
  Migracja automatycznie kopiuje dzisiejsze globalne wartości do
  istniejącego pojazdu przy aktualizacji. `price_region` i progi/włączniki
  alertów zostają globalne (świadome uproszczenie — jedna karta
  Powiadomień, nie N kart).
- **Prefill i statystyki czytają encje PRZEGLĄDANEGO auta** — naprawiony
  realny błąd: dotąd przeglądanie danych auta B i tak czytało GPS/odometr
  auta A (aktywnego). Formularz tankowania, prefill i strona Statystyki
  używają teraz encji HA właściwego, przeglądanego pojazdu.
- **Powiadomienia rozdzielone per pojazd** — stan anty-flap (`alert_state`)
  ma teraz klucz `(alert, vehicle_id)` zamiast samego `alert` — dwa auta
  z tym samym progiem przekroczonym w tym samym momencie dostają osobne,
  niezależne powiadomienia zamiast dzielić jeden stan.
- **Bramka weryfikacji z Drivvo zostaje przypięta do aktywnego auta** —
  `GET /api/verify` świadomie ignoruje `?vehicle_id=`: nowo dodane auto
  nie ma z czym się porównywać względem starych sensorów Drivvo.
- **Eksport/import pełnej kopii JSON zostaje całobazowy** (bez pojęcia
  „przeglądanego auta") — tylko eksport/import CSV (zawsze per auto)
  przechodzi z aktywnego na przeglądane auto.
- Migracje **#8** (`alert_state` → `PRIMARY KEY(alert, vehicle_id)`,
  backfill do aktywnego/pierwszego nie-zarchiwizowanego pojazdu) i **#9**
  (`vehicles` += `odometer_entity`/`fuel_level_entity`/`location_entity`/
  `monthly_fuel_budget`, backfill z dawnych globalnych ustawień, które
  są potem usuwane).
- Popup „samochod" w Lovelace (i cały dashboard HA) **pozostaje bez zmian**
  — obsługuje tylko aktywne auto na dzisiejszych entity_id, zgodnie ze
  świadomą decyzją zakresu tego wydania.
- 21 nowych/rozszerzonych testów w tym nowy `tests/test_multi_vehicle_web.py`
  (przełącznik widoku faktycznie scope'uje dane, 400 przy jawnie złym
  `vehicle_id`, cichy fallback na aktywne przy braku parametru, dowód
  naprawy buga prefill, dowód że `/api/verify` ignoruje parametr) —
  222/222 zielone, w tym pełny regres istniejących testów bez zmian
  zachowania poza mechanicznymi aktualizacjami sygnatur.

## 0.10.0

- **Kopia zapasowa w UI** — nowy moduł `backup.py`. Nocny backup (03:15,
  `VACUUM INTO`, retencja 7) obejmuje teraz też katalog `attachments/`
  (zdjęcia paragonów) jako osobne archiwum `.tar.gz` z własną retencją —
  wcześniej backupowana była tylko baza. Karta „Kopia zapasowa" w
  Ustawieniach: lista nocnych kopii z przyciskiem „Przywróć" i upload
  własnego pliku `.db`. Każde przywrócenie najpierw automatycznie
  zabezpiecza bieżącą bazę (`backups/pre_restore/`, retencja 3, osobna od
  kopii nocnych) i odrzuca pliki niebędące bazą SQLite lub pochodzące
  z nowszej wersji schematu; starszy schemat jest migrowany automatycznie
  po przywróceniu.
- **Pełny eksport/import JSON** — `GET /api/backup/export.json` (wszystkie
  10 tabel + wersja schematu) i `POST /api/backup/import.json` (pełne
  zastąpienie w jednej transakcji, nie merge — wymaga dokładnie tej samej
  wersji schematu co eksport; międzywersyjne przywracanie idzie przez
  plik `.db`, który migruje automatycznie).
- **PWA — dodaj do ekranu głównego** — `GET /manifest.webmanifest`
  (szablon Jinja, `start_url`/`scope` uwzględniają `X-Ingress-Path`),
  ikony w `static/icons/`, tagi `apple-mobile-web-app-*` w `base.html`.
  Instalacja tylko przez natywne „Dodaj do ekranu głównego" w aplikacji
  mobilnej HA (webview niesie autoryzację ingress) — bez service workera.
- Nowe endpointy: `GET /api/backup/list`, `POST /api/backup/restore`,
  `POST /api/backup/restore/upload`, `GET /api/backup/export.json`,
  `POST /api/backup/import.json`, `GET /manifest.webmanifest`.
- 33 nowe testy (`test_backup.py`, `test_backup_api.py`,
  `test_manifest.py`) — nocny backup + retencja obu typów artefaktów,
  walidacja kandydata do przywrócenia, bezpieczny snapshot przed
  przywróceniem, auto-migracja starszego schematu, round-trip JSON,
  atomowość/pełne-zastąpienie importu JSON, path-traversal w nazwie
  pliku, manifest z poprawnym `start_url` per ingress (196/196 zielone).

## 0.9.1

- **Fix: podwójne powiadomienie przy równoległej ewaluacji** — przy starcie
  0.9.0 joby `publish_sensors` i `refresh_prices` odpaliły równocześnie
  i alert „Tanie paliwo" wyszedł dwa razy (oba wątki odczytały stan `ok`
  zanim którykolwiek zapisał `cheap`). `notifications.evaluate` jest teraz
  serializowane blokadą (`threading.Lock`) — dotyczy też `on_data_change`
  z wątków Flaska. Test regresyjny z równoległymi wątkami (163/163 zielone).

## 0.9.0

- **Pojazdy w jednej karcie** — karty „Pojazdy" i „Aktywny pojazd" scalone
  w jedną: tabela wszystkich aut (nazwa, paliwo, bak, stan, leasing)
  z wyróżnionym wierszem aktywnego, a per-wiersz przyciski Edytuj /
  Aktywuj / Archiwizuj / Przywróć / Usuń. „Edytuj" otwiera pod tabelą
  formularz **wypełniony aktualnymi wartościami** dowolnego pojazdu
  (także zarchiwizowanego), łącznie z polami leasingu; „+ Dodaj pojazd"
  otwiera ten sam formularz pusty — nowy pojazd można od razu założyć
  z leasingiem (`POST /api/vehicles` przyjmuje `lease_start/lease_end/
  lease_km_limit/monthly_rate`).
- **Powiadomienia w add-onie** — alerty przestają żyć w automatyzacjach HA;
  add-on sam sprawdza progi (co 15 min i po każdej zmianie danych, na tych
  samych wartościach co sensory MQTT — moduł `notifications.py`) i wysyła
  powiadomienia przez wybraną usługę HA. Karta „Powiadomienia" w
  Ustawieniach: wybór usługi notify (lista z HA przez nowy
  `GET /api/ha-services`), włącznik per alert i edytowalne progi —
  budżet (PLN, domyślnie 100), tanie paliwo w regionie (PLN/L, domyślnie
  0.20), zapas km leasingu (km, domyślnie 1000). Nowe klucze ustawień:
  `notify_service`, `alert_{budget,cheap_fuel,lease}_enabled`,
  `alert_budget_threshold`, `alert_cheap_fuel_delta`,
  `alert_lease_km_threshold`.
- **Anty-spam** — powiadomienie tylko przy wejściu w stan (ok → ostrzeżenie
  → przekroczenie), ponowne dopiero po powrocie do normy, z oknem anty-flap
  24 h per stan; nieudana wysyłka ponawiana przy następnym ticku. Stan
  alertów w nowej tabeli `alert_state` (migracja #7) — przeżywa restarty.
- **Usunięte**: endpoint `POST /api/settings/toggle-automation`, klucze
  `alert_*_automation` (migracja #7 czyści je z bazy) i wiersze przełączania
  automatyzacji w UI. Pakiet `fuel_tracker_package.yaml` w HA staje się
  **zbędny** — po weryfikacji powiadomień usuń go z `configuration.yaml`.
  Opcja `notify_service` jest teraz faktycznie używana (format kropkowy
  `notify.mobile_app_telefon`; stary zapis `notify/x` jest normalizowany).
- 18 nowych testów (`test_notifications.py` + rozszerzenia
  `test_settings_api.py`/`test_vehicles.py`): stany i eskalacje alertów,
  dedup/anty-flap, retry po nieudanej wysyłce, migracja #7, tworzenie
  pojazdu z leasingiem, roundtrip ustawień alertów (162/162 zielone).

## 0.8.0

- **Pojazdy: cykl życia** — nowa karta „Pojazdy" w Ustawieniach: dodawanie,
  archiwizacja/przywracanie, przełączanie aktywnego pojazdu i twarde
  usuwanie (tylko bez historii tankowań/wydatków) — wszystko bez restartu
  add-onu. Aktywny pojazd żyje w `settings.active_vehicle_id` (migracja
  #6 dodaje też `vehicles.archived/lease_start/lease_end/lease_km_limit/
  monthly_rate`); sensory MQTT, pulpit i statystyki zawsze dotyczą
  aktywnego pojazdu. Upgrade z instalacji jednopojazdowych jest
  transparentny — bez skonfigurowanego `active_vehicle_id` add-on
  automatycznie wybiera jedyny istniejący pojazd.
- **Leasing per auto** — start/koniec leasingu, limit km i rata miesięczna
  edytowalne przy każdym pojeździe. Add-on liczy zapas km samodzielnie
  (`sensor.<pojazd>_fuel_lease_km_margin`) tą samą krzywą co dotychczasowy
  szablon `sensor.odo_vs_budget`, plus prognozę wyczerpania limitu
  (`sensor.<pojazd>_fuel_lease_depletion_date`) — przebieg z
  `odometer_entity`, awaryjnie z ostatniego tankowania. Stary
  `sensor.odo_vs_budget`/`odo_budget_entity` zostają tymczasowo w
  odpowiedzi `/api/statistics` do porównania (±1 km) przed ewentualnym
  wycofaniem szablonu — osobna decyzja, nie w tym wydaniu. Opcja
  Supervisora `lease_km_limit` **usunięta** (zastąpiona per-pojazdowym polem).
- Nowe endpointy: `GET/POST /api/vehicles`, `GET/PUT/DELETE
  /api/vehicles/<id>` (rozszerzone na dowolny pojazd, nie tylko aktywny),
  `POST /api/vehicles/<id>/activate|archive|unarchive`.
- 35 nowych testów (`test_vehicles.py`, `test_vehicles_api.py`, rozszerzenia
  `test_stats_extended.py`/`test_prices.py`) — cykl życia pojazdów
  (archiwizacja/usuwanie z historią i bez), fallback aktywnego pojazdu
  (nieskonfigurowany/zarchiwizowany/nieistniejący), matematyka leasingu
  zweryfikowana wprost przeciw formule `sensor.odo_vs_budget`, przełączanie
  aktywnego pojazdu bez restartu (144/144 zielone).

## 0.7.0

- **Ustawienia edytowalne w UI, bez restartu add-onu** — nowa tabela
  `settings` (migracja #5) zastępuje część opcji Supervisora jako źródło
  prawdy: budżet miesięczny, domyślna waluta (nowość, nie istniała jako
  opcja Supervisora), region cen, encje HA (odometr/poziom paliwa/
  lokalizacja). Opcje w Supervisorze zostają jako **wartość startowa** —
  seedowane do bazy jednorazowo przy pierwszym uruchomieniu
  (`INSERT OR IGNORE`), potem baza ma pierwszeństwo; zmiana opcji w
  Supervisorze po pierwszym starcie nie ma już efektu.
- **Dane pojazdu edytowalne w UI** — nazwa, pojemność baku i domyślne
  paliwo to teraz kolumny tabeli `vehicles` (istniały od v1, dotąd
  zapisywane raz i nigdy nieodczytywane) edytowalne na karcie „Pojazd";
  publikacja MQTT i strona Statystyki czytają je świeżo przy każdym ticku.
- **Toggle automatyzacji alertów z poziomu add-onu** — karta
  „Powiadomienia" w Ustawieniach: wklej entity_id automatyzacji z pakietu
  YAML (0.6.0), a add-on pokaże jej stan i pozwoli włączyć/wyłączyć przez
  `automation.turn_on`/`turn_off` (HA API) — bez wchodzenia do Home
  Assistant. Progi i treść powiadomień nadal edytuje się wyłącznie w YAML;
  add-on nie wysyła własnych powiadomień (`notify_service` pozostaje
  nieużywany, jak dotychczas).
- Nowe endpointy: `GET/PUT /api/settings`, `POST
  /api/settings/toggle-automation`, `GET/PUT /api/vehicles/<id>`.
- 20 nowych testów (`test_settings.py`, `test_settings_api.py`) — precedencja
  seed/baza, roundtrip typowanych ustawień, efekt natychmiastowy bez
  restartu, toggle automatyzacji (sukces/brak konfiguracji/błąd HA).

## 0.6.0

- **Integracja z HA** — nowa sekcja README „Integracja z HA" z gotowym
  pakietem `automation:` na trzy alerty: budżet paliwowy (ostrzeżenie
  <100 PLN, przekroczenie <0), tanie paliwo w regionie (cena regionalna
  ≥0,20 PLN/L niższa od ostatniego tankowania przez ponad godzinę) i
  tempo leasingu (zapas km z `odo_budget_entity` <1000 km przez 6 h lub
  limit przekroczony). Zero zmian w kodzie add-onu — sensory MQTT już
  istniały, brakowało tylko gotowej receptury na automatyzacje.
- Wdrożone i zweryfikowane produkcyjnie: pakiet + przebudowana karta
  Lovelace (mozaika `mushroom-template-card` na budżet/tankowanie/zużycie,
  mini-wykresy zapasu leasingu i zysku z wynajmu) w konfiguracji autora —
  wzór opisany w README do skopiowania.

## 0.5.1

- **Parser: modele lite z fallbackiem** — weryfikacja produkcyjna 0.5.0
  wykazała, że darmowa quota `gemini-2.5-flash` to już tylko
  20 zapytań/dzień (wyczerpana testami tego samego dnia). Parser używa
  teraz `gemini-3.1-flash-lite` z fallbackiem na `gemini-2.5-flash-lite`
  (drugi model przy braku odpowiedzi pierwszego) — oba parsują dowód
  wydania FLOTA w 100% zgodnie z wpisem referencyjnym, a limity lite
  są wielokrotnie wyższe.

## 0.5.0

- **Parser paragonów ze zdjęcia (LLM vision)** — przycisk „📷 Zeskanuj
  paragon" w formularzu tankowania: w aplikacji mobilnej HA otwiera od razu
  aparat (`capture="environment"`), działa też upload z galerii. Zdjęcie
  analizuje usługa `llmvision.image_analyzer` (istniejąca integracja,
  provider wykrywany automatycznie przez config entries — zero nowych opcji
  add-onu; jawny model `gemini-2.5-flash`, bo domyślny `gemini-2.0-flash`
  z integracji stracił darmową quotę). Wynik **prefilluje formularz** —
  nigdy auto-zapis, użytkownik weryfikuje pola i klika Zapisz.
- **Dwa formaty paragonów ORLEN**: paragon fiskalny (nazwa paliwa,
  litry × cena/L) oraz „Dowód wydania — karta FLOTA ORLEN" (niefiskalny:
  Kwota, Ilość, **Stan licznika → prefill przebiegu**; cena/L wyliczana
  z kwoty ÷ litrów, typ paliwa z konfiguracji). Zweryfikowane na
  prawdziwym dowodzie wydania: 100% zgodności z ręcznym wpisem
  (data, przebieg, litry, kwota).
- **Rozdział paragonu mieszanego**: pozycje niepaliwowe (AdBlue, płyn do
  spryskiwaczy…) → checkbox „Dodaj też wydatek Płyny" przy zapisie —
  jedno zdjęcie tworzy tankowanie + wydatek, oba świadomie zatwierdzone.
- **Załączniki**: zdjęcia paragonów w `<backup_share>/attachments/`
  (obejmuje je nocny backup share), tabela `attachments` (migracja #4),
  link 📷 przy wpisie na liście tankowań, `GET /api/attachments/<id>`.
  Zdjęcie zostaje nawet gdy analiza padnie (można podpiąć do ręcznego
  wpisu) i gdy wpis zostanie usunięty (dowód zostaje).
- **Fix wyścigu MQTT przy starcie**: pierwsza publikacja stanu wyprzedzała
  połączenie z brokerem, przez co sensory po restarcie wisiały jako
  „unknown" do kolejnego ticku (15 min). Stan jest teraz zapamiętywany
  i publikowany w `on_connect` — sensory mają wartości od razu.
- Nowe endpointy: `POST /api/receipts/parse`, `GET /api/attachments/<id>`;
  `POST/PUT /api/fillups` i `POST /api/expenses` przyjmują `attachment_id`.
- Limit uploadu podniesiony do 16 MB (zdjęcia z aparatu telefonu).

## 0.4.4

- **Fix (właściwy) problemu z cache na telefonie**: diagnoza po 0.4.2/0.4.3
  wykazała, że WebView aplikacji HA Companion trzyma w trwałym cache dyskowym
  **sam HTML** (strony nie miały żadnych nagłówków `Cache-Control`) i serwuje
  go bez kontaktu z serwerem — więc stemplowanie statyk `?v=` nigdy nie
  docierało do telefonu, a force-close aplikacji nie czyści tego cache.
  Teraz serwer wysyła `Cache-Control: no-store` dla stron HTML i API oraz
  `public, max-age=31536000, immutable` dla statyk (bezpieczne dzięki `?v=`).
  **Jednorazowo po tej aktualizacji** na telefonie trzeba wyczyścić pamięć
  podręczną WebView (Ustawienia → Aplikacje towarzyszące → Rozwiązywanie
  problemów), potem problem nie wróci.
- **Badge wersji w pasku nawigacji** (`v0.4.4`) — od razu widać, którą wersję
  UI renderuje dane urządzenie (stary HTML z cache nie ma badge wcale).

## 0.4.3

- Bump wersji bez zmian w kodzie — ponowienie cache-bust z 0.4.2 (nieskuteczne
  na telefonie; patrz 0.4.4).

## 0.4.2

- **Fix: stary `app.js`/`app.css` z cache przeglądarki po aktualizacji**
  (zwłaszcza na telefonie/WebView aplikacji HA) — strona „Statystyki"
  bywała całkowicie pusta (`FT.initStatistics` nieznane starej wersji JS),
  a wykres „Koszty miesięczne" pokazywał się bez serii „Paliwo prywatne"
  z 0.4.1. Statyki (`app.js`, `app.css`, `chart.umd.min.js`, `leaflet.js`,
  `leaflet.css`) są teraz ostemplowane numerem wersji (`?v=0.4.2`),
  co wymusza pobranie świeżych plików po każdym wydaniu.

## 0.4.1

- **Pulpit — wykres „Koszty miesięczne"**: tankowania opłacone prywatnie
  (`paid_by=own`) wydzielone jako osobna seria „Paliwo prywatne" (zielona,
  spójna z odznaką „moje" i pinami na mapie); seria „Paliwo (karta)"
  pokazuje już tylko kartę ORLEN Flota. Bez zmian w sensorach i budżecie
  (budżet nadal liczy całość paliwa).

## 0.4.0

> Numeracja zgodna z roadmapą: 0.3.0 (parser paragonów LLM vision)
> celowo przełożony na później — funkcje 0.4.0 weszły pierwsze.

- **Tankowania za granicą**: wybór waluty w formularzu (EUR, CZK, HUF, CHF…;
  domyślnie PLN — zero dodatkowych kliknięć w kraju); kurs średni NBP
  (tabela A, ostatni sprzed daty tankowania) dociągany automatycznie,
  z możliwością ręcznej korekty; statystyki i sensory zawsze w PLN,
  kwota oryginalna zachowana i widoczna na liście; cache kursów w SQLite
  (migracja #3), awaryjnie ostatni znany kurs
- **Ceny regionalne paliw**: scraper autocentrum.pl (tabela wojewódzka,
  opcja `price_region`) co 6 h do tabeli
  `fuel_prices` (retencja 400 dni); sensory `region_fuel_price`
  i `price_vs_region` (moja ostatnia cena vs region)
- **Strona „Statystyki"**: zasięg na baku, tempo roczne km, moja cena vs
  region (wykres), przebieg miesięczny, podział kosztów (karta ORLEN
  Flota / prywatne / płyny / inne), rekordy (najlepsze/najgorsze spalanie,
  najdłuższy dystans na baku, najtańsze/najdroższe tankowanie), ranking
  stacji, raport miesięczny z eksportem CSV (`/api/report.csv`)
- **Leasing**: zapas km z `sensor.odo_vs_budget` (opcja
  `odo_budget_entity`) + prognoza daty wyczerpania limitu (opcja
  `lease_km_limit`, domyślnie 90 000 km) przy obecnym tempie
- **5 nowych sensorów statystyk**: `estimated_range_km`,
  `month_forecast_cost`, `ytd_fuel_cost`, `projected_annual_km`,
  `best_station` (24 sensory łącznie)
- Nowe endpointy API: `GET /api/rate`, `GET /api/statistics`,
  `GET /api/report.csv`
- Nowe opcje add-onu: `price_region`, `odo_budget_entity`, `lease_km_limit`

## 0.2.1

- Fix: przycisk „Anuluj edycję" na stronie wydatków był widoczny od razu —
  atrybut `hidden` przegrywał z `display` klasy `.btn` (globalny override
  `[hidden] { display: none !important; }`)

## 0.2.0

- **Stacje po GPS**: nowa tabela stacji (backfill z historii tankowań);
  przy otwarciu formularza add-on pobiera pozycję z `location_entity`
  (person/device_tracker z aplikacji mobilnej HA) i dopasowuje najbliższą
  zapisaną stację (promień 300 m); bez dopasowania pyta OSM Overpass
  o stacje w promieniu 500 m i podpowiada nazwę
- **Mapa tankowań**: podstrona „Mapa" (Leaflet, kafelki OSM) z pinami stacji —
  rozmiar wg liczby wizyt, kolor odróżnia tankowania prywatne i zagraniczne,
  popup ze statystykami stacji (wizyty, suma, śr. cena, ostatnia wizyta)
- **Tankowania opłacone prywatnie**: pole „Zapłacone przeze mnie" w formularzu,
  oznaczenie na liście i mapie, nowy sensor `self_paid_fuel_total` —
  docelowo zastąpi ręczny `input_number.suma_moich_wydatkow_na_paliwo`
- **Walidacja przebiegu**: przebieg musi rosnąć w czasie względem sąsiednich
  wpisów (wyłączana checkboxem „Pominięto poprzednie tankowanie")
- **Edycja wydatków** (`PUT /api/expenses/<id>`) + przycisk „Edytuj" na liście
- **Kategorie wydatków**: nowa kategoria „Płyny" (AdBlue, spryskiwacze z karty
  ORLEN Flota); nieużywane kategorie można ukryć w Ustawieniach
- Schemat bazy przygotowany pod tankowania za granicą (waluta, kwoty
  oryginalne, kurs) — funkcja wchodzi w 0.4.0
- Nowa opcja add-onu: `location_entity` (person/device_tracker z aplikacji
  mobilnej HA)

## 0.1.3

- Import wydatków z Drivvo działa z realnym schematem web API: kwoty są
  w zagnieżdżonej liście `tipos_despesa[].valor` (nie `valor_total`),
  opis w `observacao`, id w `id_despesa`
- Dedup wydatków między importem CSV a API Drivvo po (odometr, kwota) —
  daty różnią się o minutę, opisy wielkością liter
- Kategoria „Płyny" mapowana na Eksploatację

## 0.1.2

- Naprawa auto-wyboru pojazdu przy imporcie z Drivvo — API zwraca klucz
  `id_veiculo`, nie `id` (KeyError przy `drivvo_vehicle_id: 0`)
- `POST /api/import/drivvo` przyjmuje też `vehicle_id` w body żądania

## 0.1.1

- MQTT bez konfiguracji: gdy `mqtt_user` jest puste, add-on pobiera dane brokera
  z usługi Supervisora (`services: mqtt:need`) — działa od razu z core-mosquitto
- `POST /api/import/drivvo` przyjmuje `email`/`password` w body żądania —
  jednorazowy import bez zapisywania hasła w opcjach add-onu
- Sensory `monetary` mają `state_class: total` (jedyna kombinacja dopuszczana
  przez walidator HA; wcześniej `total_increasing`/`measurement` logowały ostrzeżenia)

## 0.1.0

Pierwsze wydanie:

- Dziennik tankowań (pełny/częściowy bak, cena/L, stacja, GPS) z edycją i usuwaniem
- Silnik statystyk: spalanie L/100km liczone segmentami między pełnymi bakami, średnia ogólna Σvol/Σdist, koszt/km
- Wydatki w kategoriach (Serwis, Eksploatacja, Parking, Myjnia, …)
- Import historii z pliku CSV (upload w UI lub auto-import z `/share/fuel_tracker/import/`)
- Import wydatków/serwisów z API Drivvo (jednorazowa migracja)
- Raport weryfikacyjny importu (liczba wpisów, suma PLN, suma litrów)
- Eksport CSV
- Sensory MQTT discovery: koszty, spalanie, ostatnie tankowanie, budżet miesięczny
- Web UI po polsku przez ingress (pulpit z wykresami, formularz tankowania z prefill odometru z encji HA)
- Nocny backup bazy do `/share/fuel_tracker/`
