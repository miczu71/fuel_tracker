# Fuel Tracker — add-on Home Assistant

Dziennik tankowań i wydatków samochodu, działający w 100% lokalnie (SQLite
w `/data`). Zastępuje chmurowe aplikacje do śledzenia tankowań: pełny import
historii (CSV / API Drivvo), spalanie L/100km liczone segmentami między
pełnymi bakami, sensory MQTT discovery i mobilny web UI po polsku przez ingress.

## Instalacja

1. **Ustawienia → Dodatki → Sklep dodatków → ⋮ → Repozytoria** — dodaj
   `https://github.com/miczu71/fuel_tracker`
2. Zainstaluj **Fuel Tracker**, uzupełnij konfigurację (patrz niżej) i uruchom.
3. Otwórz panel boczny **Fuel Tracker** (ingress).

## Funkcje

- **Dziennik tankowań** — data, odometr, litry, cena/L, kwota (2 z 3 pól
  wyliczają trzecie), pełny/częściowy bak, pominięte tankowanie, stacja, GPS,
  notatki; edycja i usuwanie.
- **Parser paragonów ze zdjęcia (LLM vision)** — „📷 Zeskanuj paragon"
  w formularzu: w aplikacji mobilnej HA otwiera aparat, działa też galeria.
  Analiza przez istniejącą integrację `llmvision` (provider wykrywany
  automatycznie, modele `gemini-3.1-flash-lite` + fallback) — wynik prefilluje formularz,
  zapis zawsze ręczny. Rozpoznaje paragon fiskalny **i** „Dowód wydania —
  karta FLOTA ORLEN" (z przebiegiem ze stanu licznika); pozycje
  niepaliwowe z paragonu mieszanego mogą jednym kliknięciem utworzyć
  wydatek „Płyny". Zdjęcie zostaje jako załącznik wpisu (📷 na liście),
  przechowywane w `<backup_share>/attachments/`.
- **Statystyki spalania** — spalanie L/100km liczone segmentami między
  tankowaniami do pełna (partiale wliczane do segmentu), średnia ogólna
  Σlitrów/Σkm (nie średnia średnich), koszt/km, serie miesięczne
  (paliwo z karty / paliwo prywatne / wydatki jako osobne serie wykresu).
- **Stacje po GPS** — przy otwarciu formularza add-on pobiera pozycję telefonu
  (`location_entity`, aplikacja mobilna HA) i dopasowuje najbliższą zapisaną
  stację (300 m); bez dopasowania podpowiada nazwę z OSM Overpass (500 m).
- **Mapa tankowań** — podstrona z pinami stacji (Leaflet + kafelki OSM):
  rozmiar pinu = liczba wizyt, kolor odróżnia tankowania prywatne
  i zagraniczne, popup ze statystykami stacji.
- **Tankowania opłacone prywatnie** — checkbox „Zapłacone przeze mnie",
  oznaczenie na liście i mapie, osobny sensor `self_paid_fuel_total`
  do rozliczenia zysku z wynajmu.
- **Tankowania za granicą** — wybór waluty w formularzu (domyślnie PLN),
  kurs średni NBP (tabela A) dociągany automatycznie z ręczną korektą;
  statystyki i sensory zawsze w PLN, kwota oryginalna widoczna na liście.
- **Ceny regionalne** — scraper autocentrum.pl co 6 h (województwo z opcji
  `price_region`); sensory ceny regionalnej i różnicy do własnej ceny.
- **Strona Statystyki** — zasięg na baku, tempo roczne km, moja cena vs
  region (wykres), przebieg miesięczny, podział kosztów
  (karta ORLEN Flota / prywatne / płyny / inne), rekordy, ranking stacji,
  raport miesięczny z eksportem CSV.
- **Leasing per auto** — start/koniec leasingu, limit km i rata miesięczna
  edytowalne przy każdym pojeździe (strona Ustawienia); add-on sam liczy
  zapas km (ta sama krzywa co dawny `sensor.odo_vs_budget`) i prognozę daty
  wyczerpania limitu przy obecnym tempie przebiegu — bez zależności od
  szablonu HA. `sensor.odo_vs_budget`/`odo_budget_entity` zostają
  tymczasowo do porównania, aż do osobnej decyzji o wycofaniu.
- **Walidacja przebiegu** — odometr musi rosnąć w czasie względem sąsiednich
  wpisów (chyba że zaznaczono „Pominięto poprzednie tankowanie").
- **Wydatki w kategoriach, w pełni edytowalnych (od 0.13.0)** — domyślne:
  Serwis, Eksploatacja, Rejestracja, Parking, Myjnia, Opłaty za przejazd,
  Mandaty, Tuning, Ubezpieczenie, Płyny, Inne; można dodawać własne,
  zmieniać nazwę, przypisywać grupę TCO (Płyny/Serwis/Ubezpieczenie/
  Opłaty/Inne — decyduje o rozbiciu kosztu posiadania) i ukrywać/usuwać
  w Ustawieniach (usunięcie odmówione dla kategorii z przypisanymi
  wydatkami albo dla jedynej pozostałej).
- **Import CSV** — upload w UI lub auto-import plików `*.csv`
  z `/share/fuel_tracker/import/` przy starcie (format z sekcjami
  `## Vehicle` / `## Log` — patrz eksport); idempotentny (re-import nie
  duplikuje wpisów).
- **Import z API Drivvo** — jednorazowa migracja wydatków/serwisów
  (opcjonalnie także tankowań, deduplikowanych z importem CSV).
- **Eksport CSV** — pełny round-trip, dane zawsze można zabrać ze sobą.
- **Sensory MQTT discovery** — urządzenie w HA bez żadnej konfiguracji YAML.
- **Prefill formularza** — odometr z encji HA pojazdu
  (`odometer_entity`), ostatnia stacja i cena.
- **Nocny backup** — `VACUUM INTO` do `/share/fuel_tracker/` (7 kopii).
- **Ustawienia edytowalne w UI (bez restartu)** — budżet, waluta domyślna,
  region cen, dane pojazdu (nazwa/pojemność baku/domyślne paliwo) i encje HA
  (odometr/poziom paliwa/lokalizacja) zmienia się na stronie Ustawienia i
  działa od razu; każdy formularz pokazuje aktualne wartości.
- **Pojazdy: cykl życia w jednej karcie** — tabela wszystkich aut
  z wyróżnionym aktywnym; per-wiersz Edytuj (formularz wypełniony aktualnymi
  wartościami, także leasing: start/koniec/limit km/rata), Aktywuj,
  Archiwizuj/Przywróć i twarde Usuń (tylko bez historii tankowań/wydatków).
  Sensory MQTT, pulpit i statystyki zawsze dotyczą aktywnego pojazdu;
  historia zarchiwizowanego auta zostaje.
- **Powiadomienia wbudowane (od 0.9.0)** — add-on sam sprawdza progi
  (co 15 min i po każdej zmianie danych) i wysyła alerty przez wybraną
  usługę HA: budżet na wyczerpaniu/przekroczony, tanie paliwo w regionie,
  zapas km leasingu topnieje/przekroczony. Usługa notify, włączniki
  i progi edytowalne w karcie „Powiadomienia"; anty-spam — alert tylko
  przy wejściu w stan, ponownie po powrocie do normy (okno 24 h).
- **Kopia zapasowa w UI (od 0.10.0)** — karta „Kopia zapasowa" w
  Ustawieniach: lista nocnych backupów (`.db`, 7 dni) z przyciskiem
  „Przywróć" (bieżąca baza jest automatycznie zabezpieczona przed
  każdym przywróceniem), upload własnego pliku `.db` do przywrócenia
  (auto-migruje starszy schemat). Osobno: pełny eksport/import **JSON**
  wszystkich danych naraz (wymaga identycznej wersji add-onu — inaczej
  użyj pliku `.db`, który migruje automatycznie). Nocny backup obejmuje
  teraz też zdjęcia paragonów (`attachments/`, osobne archiwum `.tar.gz`).
- **PWA — dodaj do ekranu głównego (od 0.10.0)** — z aplikacji mobilnej
  HA (webview niesie autoryzację ingress) można dodać skrót do add-onu
  na ekran główny telefonu, otwierający od razu formularz tankowania.
  Bez service workera — instalacja tylko przez natywne „Dodaj do ekranu
  głównego", nie przez kryteria instalowalności przeglądarki.
- **Pełny multi-vehicle (od 0.11.0)** — przełącznik pojazdu w navbarze na
  każdej stronie; kilka aut z równoległymi, żywymi sensorami MQTT naraz
  (nie tylko jedno aktywne). Dotychczasowe auto zostaje na dzisiejszym
  entity_id (zero migracji), każde kolejne dostaje własne urządzenie MQTT.
  Budżet i encje HA (odometr/poziom paliwa/lokalizacja) są teraz polem
  każdego pojazdu — formularz „Pojazdy” w Ustawieniach. Prefill i
  Statystyki czytają encje przeglądanego (nie tylko aktywnego) auta.
- **Koszt posiadania — TCO (od 0.13.0)** — karta na stronie Statystyki:
  rozbicie kosztu na paliwo / wydatki per grupa TCO / ratę leasingu
  (pierwsze wykorzystanie pola „rata miesięczna” z karty Pojazdy),
  kafelki koszt razem/km, razem/miesiąc, razem/100 km, wykres kołowy
  rozbicia. Zastępuje dawną kartę „Podział kosztów”.
- **Porównanie pojazdów (od 0.13.0)** — nowa strona: zestawienie
  wszystkich nie-zarchiwizowanych pojazdów naraz (tankowania, średnie
  spalanie, koszt/km TCO, średnia cena/L, przebieg/miesiąc, wydatki),
  niezależnie od aktualnie przeglądanego auta.
- **Bogatsze wykresy statystyk (od 0.13.0)** — skumulowany koszt w
  czasie i trend koszt/km miesiąc po miesiącu, obok istniejących
  wykresów spalania i ceny.
- **Eksport CSV zweryfikowany jako bezpiecznik migracyjny (od 0.13.0)**
  — format sekcyjny kompatybilny z popularnymi dziennikami tankowań;
  nagłówek sekcji `Log` zablokowany testem regresyjnym względem realnej
  próbki eksportu. Sekcja `Vehicle` to okrojony podzbiór pól źródłowego
  formatu, a pole `FuelType` jest zawsze nieokreślone — przy imporcie do
  innej aplikacji wygodniej najpierw ręcznie skonfigurować tam pojazd.

## Encje (MQTT discovery)

Urządzenie: nazwa aktywnego pojazdu + „Fuel” (np. **Moje Auto Fuel**),
`identifiers: ["fuel_tracker"]`. Publikacja co 15 min oraz po każdej zmianie
danych. W tabeli poniżej `<pojazd>` to slug nazwy urządzenia wygenerowany
przez HA — rzeczywiste `entity_id` zweryfikuj w **Narzędzia deweloperskie →
Stany**.

> **Od 0.11.0 — multi-vehicle:** powyższe dotyczy **dotychczasowego/aktywnego**
> pojazdu — jego `entity_id` nie zmieniają się (zero migracji). Każde
> KOLEJNE dodane auto dostaje własne, odrębne urządzenie MQTT
> `identifiers: ["fuel_tracker_<id>"]` z tym samym kompletem sensorów pod
> `sensor.<nazwa_urządzenia>_*` (realny `entity_id` zależy od nazwy nowego
> pojazdu — zweryfikuj w **Narzędzia deweloperskie → Stany** po dodaniu).

| Encja | Jednostka | Opis |
|---|---|---|
| `sensor.<pojazd>_fuel_total_cost` | PLN | Suma wydatków na paliwo (wszystkie tankowania) |
| `sensor.<pojazd>_fuel_total_volume` | L | Suma zatankowanych litrów |
| `sensor.<pojazd>_fuel_fillup_count` | — | Liczba tankowań |
| `sensor.<pojazd>_fuel_avg_consumption` | L/100km | Średnie spalanie (Σvol/Σdist po segmentach) |
| `sensor.<pojazd>_fuel_last_consumption` | L/100km | Spalanie ostatniego segmentu pełny→pełny |
| `sensor.<pojazd>_fuel_cost_per_km` | PLN/km | Koszt paliwa na kilometr |
| `sensor.<pojazd>_fuel_avg_price_per_l` | PLN/L | Średnia cena litra |
| `sensor.<pojazd>_fuel_last_fillup_date` | timestamp | Data ostatniego tankowania |
| `sensor.<pojazd>_fuel_last_fillup_odometer` | km | Odometr przy ostatnim tankowaniu |
| `sensor.<pojazd>_fuel_last_fillup_price` | PLN/L | Cena litra przy ostatnim tankowaniu |
| `sensor.<pojazd>_fuel_last_fillup_volume` | L | Litry ostatniego tankowania |
| `sensor.<pojazd>_fuel_last_fillup_cost` | PLN | Kwota ostatniego tankowania |
| `sensor.<pojazd>_fuel_last_fillup_station` | — | Stacja ostatniego tankowania |
| `sensor.<pojazd>_fuel_expenses_total` | PLN | Suma wydatków pozapaliwowych |
| `sensor.<pojazd>_fuel_budget_left_month` | PLN | Pozostały budżet paliwowy w bieżącym miesiącu |
| `sensor.<pojazd>_fuel_month_fuel_cost` | PLN | Wydatki na paliwo w bieżącym miesiącu |
| `sensor.<pojazd>_fuel_self_paid_fuel_total` | PLN | Suma tankowań opłaconych prywatnie („Zapłacone przeze mnie") |
| `sensor.<pojazd>_fuel_region_fuel_price` | PLN/L | Cena regionalna paliwa (`price_region`, autocentrum.pl) |
| `sensor.<pojazd>_fuel_price_vs_region` | PLN/L | Moja ostatnia cena − cena regionalna (ujemna = taniej) |
| `sensor.<pojazd>_fuel_estimated_range` | km | Zasięg na pełnym baku przy średnim spalaniu |
| `sensor.<pojazd>_fuel_month_forecast_cost` | PLN | Prognoza kosztu paliwa w bieżącym miesiącu |
| `sensor.<pojazd>_fuel_ytd_fuel_cost` | PLN | Wydatki na paliwo od początku roku |
| `sensor.<pojazd>_fuel_projected_annual_km` | km | Roczne tempo przebiegu (ekstrapolacja historii) |
| `sensor.<pojazd>_fuel_best_station` | — | Stacja z najniższą średnią ceną (min. 2 tankowania) |
| `sensor.<pojazd>_fuel_lease_km_margin` | km | Zapas km do limitu leasingu aktywnego pojazdu (ta sama krzywa co dawny `sensor.odo_vs_budget`) |
| `sensor.<pojazd>_fuel_lease_depletion_date` | date | Prognoza daty wyczerpania limitu km przy obecnym tempie |

> Rzeczywiste `entity_id` zależą od nazwy urządzenia — po pierwszym starcie
> zweryfikuj je w **Narzędzia deweloperskie → Stany**.

## Powiadomienia (wbudowane od 0.9.0)

Alerty (budżet, tanie paliwo w regionie, zapas km leasingu) są liczone
i wysyłane przez sam add-on — nie potrzeba żadnych automatyzacji YAML.
W karcie **Ustawienia → Powiadomienia** wybierasz usługę notify (lista
pobierana z HA), włączasz/wyłączasz poszczególne alerty i ustawiasz progi:

| Alert | Próg (domyślnie) | Kiedy powiadamia |
|---|---|---|
| Budżet paliwowy | 100 PLN | pozostały budżet miesiąca poniżej progu (ostrzeżenie) lub poniżej 0 (przekroczony) |
| Tanie paliwo w regionie | 0.20 PLN/L | cena regionalna niższa od Twojego ostatniego tankowania o co najmniej próg |
| Zapas km leasingu | 1000 km | zapas względem krzywej limitu poniżej progu (topnieje) lub poniżej 0 (przekroczony) |

Progi są sprawdzane co 15 minut i po każdej zmianie danych. Anty-spam:
powiadomienie przychodzi przy **wejściu** w stan (i przy eskalacji
ostrzeżenie → przekroczenie), ponowne dopiero po powrocie do normy,
z oknem anty-flap 24 h; nieudana wysyłka jest ponawiana przy następnym
ticku. Stan alertów przeżywa restart add-onu.

> **Migracja z ≤0.8.x:** pakiet automatyzacji `fuel_tracker_package.yaml`
> (dawna sekcja „Integracja z HA") jest zbędny — po zweryfikowaniu, że
> powiadomienia z add-onu przychodzą, usuń wpis
> `fuel_tracker: !include packages/fuel_tracker_package.yaml`
> z `configuration.yaml` i sam plik pakietu, po czym zrestartuj HA.
> Endpoint `POST /api/settings/toggle-automation` i klucze
> `alert_*_automation` zostały usunięte.

**Karta Lovelace** — encje z tabeli wyżej nadają się na kafelki
`custom:mushroom-template-card` (mosaic budżet/tankowanie/zużycie) albo
klasyczne `entities`/`glance`; `sensor.<pojazd>_fuel_budget_left_month` dobrze
wygląda jako gauge (`custom:modern-circular-gauge` lub `custom:bar-card`)
z progami przy 200 i 460 PLN. Konkretny układ zależy od Twojego dashboardu —
powyższe sensory wystarczą jako dane wejściowe.

## Konfiguracja

> **Od 0.7.0:** `vehicle_name`, `tank_capacity_l`, `default_fuel_type`,
> `monthly_fuel_budget`, `odometer_entity`, `fuel_level_entity`,
> `location_entity` i `price_region` to teraz tylko **wartość startowa**
> (seedowana do bazy przy pierwszym uruchomieniu) — edycja na żywo, bez
> restartu add-onu, jest na stronie **Ustawienia** (karty Pojazdy / Budżet /
> Ceny regionalne / Encje HA). Zmiana tych opcji w Supervisorze po
> pierwszym starcie nie ma już żadnego efektu — reszta opcji poniżej
> zostaje techniczna (wymaga restartu).
>
> **Od 0.8.0:** wiele pojazdów żyje w bazie (karta „Pojazdy"), nie w
> opcjach Supervisora — `vehicle_name`/`tank_capacity_l`/`default_fuel_type`
> zasiewają tylko **pierwszy** pojazd przy pierwszym starcie. Leasing
> (start/koniec/limit km/rata) jest teraz polem każdego pojazdu w UI —
> opcja `lease_km_limit` **usunięta** (zastąpiona per-pojazdowym polem).
> `odo_budget_entity` zostaje jako Supervisor-only ustawienie, używane
> wyłącznie do wyświetlenia starego `sensor.odo_vs_budget` obok nowego
> wyliczenia — do usunięcia po osobnej decyzji o wycofaniu szablonu.
>
> **Od 0.9.0:** `notify_service` to również tylko wartość startowa —
> usługę, włączniki i progi alertów edytuje się w karcie „Powiadomienia".
> Instalacje z ≤0.8.x mogą mieć zaseedowany stary format `notify/family` —
> zapis ukośnikowy jest normalizowany do kropkowego, ale zweryfikuj usługę
> w Ustawieniach po aktualizacji.
>
> **Od 0.11.0:** `monthly_fuel_budget`/`odometer_entity`/`fuel_level_entity`/
> `location_entity` zasiewają wyłącznie **pierwszy** pojazd przy świeżej
> instalacji — edycja przeniesiona z globalnych Ustawień do formularza
> każdego pojazdu (karta „Pojazdy"). Przy aktualizacji z ≤0.10.x te
> wartości są automatycznie kopiowane z globalnych ustawień do
> istniejącego pojazdu (migracja #9).

| Opcja | Domyślnie | Opis |
|---|---|---|
| `vehicle_name` | `Mój samochód` | Nazwa **pierwszego** pojazdu (kolejne dodaje się w Ustawieniach) — wartość startowa, potem edycja w Ustawieniach |
| `tank_capacity_l` | `50.0` | Pojemność baku pierwszego pojazdu [L] — wartość startowa, potem edycja w Ustawieniach |
| `default_fuel_type` | `PB95` | Domyślny typ paliwa pierwszego pojazdu — wartość startowa, potem edycja w Ustawieniach |
| `monthly_fuel_budget` | `0.0` | Miesięczny budżet paliwowy [PLN]; `0` = bez budżetu (sensor `budget_left_month` będzie ujemny do czasu ustawienia) — wartość startowa, potem edycja w Ustawieniach |
| `odometer_entity` | *(puste)* | Encja odometru do prefill formularza — wartość startowa, potem edycja w Ustawieniach |
| `fuel_level_entity` | *(puste)* | Encja poziomu paliwa (nieużywana) — wartość startowa, potem edycja w Ustawieniach |
| `location_entity` | *(puste)* | Encja z GPS telefonu do dopasowania stacji w formularzu — wartość startowa, potem edycja w Ustawieniach |
| `price_region` | `mazowieckie` | Województwo do cen regionalnych (autocentrum.pl) — wartość startowa, potem edycja w Ustawieniach |
| `odo_budget_entity` | *(puste)* | Opcjonalna encja HA z własnym wyliczeniem zapasu km leasingu — tylko do porównania z wyliczeniem add-onu (strona Statystyki) |
| `drivvo_email` / `drivvo_password` | — | Konto Drivvo do jednorazowego importu |
| `drivvo_vehicle_id` | `0` | ID pojazdu w Drivvo (`0` = pierwszy z konta) |
| `notify_service` | *(puste)* | Usługa powiadomień dla wbudowanych alertów; pusta = alerty nie są wysyłane — wartość startowa, potem edycja w Ustawieniach (karta „Powiadomienia") |
| `mqtt_host` / `mqtt_port` / `mqtt_user` / `mqtt_password` | `core-mosquitto` / `1883` | Broker MQTT |
| `log_level` | `info` | `debug` / `info` / `warning` / `error` |
| `backup_share` | `/share/fuel_tracker` | Katalog backupów i auto-importu |
| `timezone` | `Europe/Warsaw` | Strefa czasowa |

Domyślna waluta tankowań (`default_currency`, domyślnie `PLN`) jest ustawieniem
wyłącznie w UI (karta Budżet) — nie ma odpowiednika w opcjach Supervisora.

## REST API (przez ingress)

`GET /api/summary` · CRUD `/api/fillups` · `GET /api/prefill` ·
`GET /api/rate` · CRUD `/api/expenses` · CRUD `/api/categories`
(od 0.13.0 — dawniej tylko `GET|PUT`) ·
`GET /api/stations` · `GET /api/stations/nearby` · `GET /api/map-data` ·
`POST /api/receipts/parse` · `GET /api/attachments/<id>` ·
`GET /api/statistics` (od 0.13.0 z blokiem `tco`) ·
`GET /api/compare` (od 0.13.0) · `GET /api/report.csv` ·
`POST /api/import/csv` ·
`POST /api/import/drivvo` · `GET /api/export/log.csv` ·
`GET|PUT /api/settings` · `GET /api/ha-services` ·
`GET /api/vehicles` · `POST /api/vehicles` (od 0.9.0 także pola leasingu,
od 0.11.0 też budżet i encje HA) ·
`GET|PUT|DELETE /api/vehicles/<id>` ·
`POST /api/vehicles/<id>/activate` · `POST /api/vehicles/<id>/archive` ·
`POST /api/vehicles/<id>/unarchive` · `GET /api/health` ·
`GET /api/backup/list` · `POST /api/backup/restore` ·
`POST /api/backup/restore/upload` · `GET /api/backup/export.json` ·
`POST /api/backup/import.json` · `GET /manifest.webmanifest`

> **Od 0.11.0:** endpointy danych pojazdu (`fillups`, `expenses`, `prefill`,
> `summary`, `map-data`, `statistics`, `report.csv`, `export/log.csv`,
> `import/csv`, `import/drivvo`, `receipts/parse`) przyjmują opcjonalny
> query param `?vehicle_id=<id>`. Odczyty: brak/nieprawidłowy parametr
> cicho spada na aktywne auto. Zapisy: parametr jawnie podany, ale
> nieprawidłowy/zarchiwizowany → `400`; parametr całkowicie pominięty →
> cichy fallback na aktywne (kompatybilność wsteczna).
>
> **Od 0.12.0:** eksport CSV jest pod `GET /api/export/log.csv` (poprzedni
> route eksportu usunięty); endpoint `GET /api/verify` (jednorazowy raport
> porównawczy z czasów migracji z Drivvo) został usunięty.

## Plan rozwoju

Brak zaplanowanych funkcji — kolejne wydania według potrzeb.

## Rozwój

```bash
cd fuel_tracker
pip install -r requirements.txt pytest
pytest
```

Testy używają wyłącznie syntetycznych fixtur (bez danych osobowych).
