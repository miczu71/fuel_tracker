# Changelog

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
