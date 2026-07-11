# Fuel Tracker — add-on Home Assistant

Dziennik tankowań i wydatków samochodu w stylu [Fuelio](https://www.fuel.io/),
działający w 100% lokalnie (SQLite w `/data`). Zastępuje chmurowe aplikacje typu
Drivvo/Fuelio: pełny import historii, statystyki spalania liczone jak w Fuelio,
sensory MQTT discovery i mobilny web UI po polsku przez ingress.

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
- **Statystyki jak w Fuelio** — spalanie L/100km liczone segmentami między
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
- **Wydatki w kategoriach** — Serwis, Eksploatacja, Rejestracja, Parking,
  Myjnia, Opłaty za przejazd, Mandaty, Tuning, Ubezpieczenie, Płyny, Inne;
  edycja wpisów, ukrywanie nieużywanych kategorii w Ustawieniach.
- **Import Fuelio CSV** — upload w UI lub auto-import plików `*.csv`
  z `/share/fuel_tracker/import/` przy starcie; idempotentny (re-import nie
  duplikuje wpisów).
- **Import z API Drivvo** — jednorazowa migracja wydatków/serwisów
  (opcjonalnie także tankowań, deduplikowanych z importem CSV).
- **Raport weryfikacyjny** — porównanie sum (liczba tankowań, PLN, litry)
  z żywymi sensorami integracji Drivvo przed przepięciem szablonów.
- **Eksport Fuelio CSV** — pełny round-trip, dane zawsze można zabrać ze sobą.
- **Sensory MQTT discovery** — urządzenie w HA bez żadnej konfiguracji YAML.
- **Prefill formularza** — odometr z integracji myskoda
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

## Encje (MQTT discovery)

Urządzenie: nazwa z opcji `vehicle_name` + „Fuel” (domyślnie **Superb Fuel**),
`identifiers: ["fuel_tracker"]`. Publikacja co 15 min oraz po każdej zmianie danych.

| Encja | Jednostka | Opis |
|---|---|---|
| `sensor.superb_fuel_total_cost` | PLN | Suma wydatków na paliwo (wszystkie tankowania) |
| `sensor.superb_fuel_total_volume` | L | Suma zatankowanych litrów |
| `sensor.superb_fuel_fillup_count` | — | Liczba tankowań |
| `sensor.superb_fuel_avg_consumption` | L/100km | Średnie spalanie (Σvol/Σdist po segmentach) |
| `sensor.superb_fuel_last_consumption` | L/100km | Spalanie ostatniego segmentu pełny→pełny |
| `sensor.superb_fuel_cost_per_km` | PLN/km | Koszt paliwa na kilometr |
| `sensor.superb_fuel_avg_price_per_l` | PLN/L | Średnia cena litra |
| `sensor.superb_fuel_last_fillup_date` | timestamp | Data ostatniego tankowania |
| `sensor.superb_fuel_last_fillup_odometer` | km | Odometr przy ostatnim tankowaniu |
| `sensor.superb_fuel_last_fillup_price` | PLN/L | Cena litra przy ostatnim tankowaniu |
| `sensor.superb_fuel_last_fillup_volume` | L | Litry ostatniego tankowania |
| `sensor.superb_fuel_last_fillup_cost` | PLN | Kwota ostatniego tankowania |
| `sensor.superb_fuel_last_fillup_station` | — | Stacja ostatniego tankowania |
| `sensor.superb_fuel_expenses_total` | PLN | Suma wydatków pozapaliwowych |
| `sensor.superb_fuel_budget_left_month` | PLN | Pozostały budżet paliwowy w bieżącym miesiącu |
| `sensor.superb_fuel_month_fuel_cost` | PLN | Wydatki na paliwo w bieżącym miesiącu |
| `sensor.superb_fuel_self_paid_fuel_total` | PLN | Suma tankowań opłaconych prywatnie („Zapłacone przeze mnie") |
| `sensor.superb_fuel_region_fuel_price` | PLN/L | Cena regionalna paliwa (`price_region`, autocentrum.pl) |
| `sensor.superb_fuel_price_vs_region` | PLN/L | Moja ostatnia cena − cena regionalna (ujemna = taniej) |
| `sensor.superb_fuel_estimated_range` | km | Zasięg na pełnym baku przy średnim spalaniu |
| `sensor.superb_fuel_month_forecast_cost` | PLN | Prognoza kosztu paliwa w bieżącym miesiącu |
| `sensor.superb_fuel_ytd_fuel_cost` | PLN | Wydatki na paliwo od początku roku |
| `sensor.superb_fuel_projected_annual_km` | km | Roczne tempo przebiegu (ekstrapolacja historii) |
| `sensor.superb_fuel_best_station` | — | Stacja z najniższą średnią ceną (min. 2 tankowania) |
| `sensor.superb_fuel_lease_km_margin` | km | Zapas km do limitu leasingu aktywnego pojazdu (ta sama krzywa co dawny `sensor.odo_vs_budget`) |
| `sensor.superb_fuel_lease_depletion_date` | date | Prognoza daty wyczerpania limitu km przy obecnym tempie |

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
klasyczne `entities`/`glance`; `sensor.superb_fuel_budget_left_month` dobrze
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

| Opcja | Domyślnie | Opis |
|---|---|---|
| `vehicle_name` | `Skoda Superb` | Nazwa **pierwszego** pojazdu (kolejne dodaje się w Ustawieniach) — wartość startowa, potem edycja w Ustawieniach |
| `tank_capacity_l` | `66.0` | Pojemność baku pierwszego pojazdu [L] — wartość startowa, potem edycja w Ustawieniach |
| `default_fuel_type` | `PB95` | Domyślny typ paliwa pierwszego pojazdu — wartość startowa, potem edycja w Ustawieniach |
| `monthly_fuel_budget` | `984.0` | Miesięczny budżet paliwowy [PLN] — wartość startowa, potem edycja w Ustawieniach |
| `odometer_entity` | `sensor.skoda_superb_mileage` | Encja odometru do prefill formularza — wartość startowa, potem edycja w Ustawieniach |
| `fuel_level_entity` | `sensor.skoda_superb_fuel_level` | Encja poziomu paliwa (nieużywana) — wartość startowa, potem edycja w Ustawieniach |
| `location_entity` | `device_tracker.op12` | Encja z GPS telefonu do dopasowania stacji w formularzu — wartość startowa, potem edycja w Ustawieniach |
| `price_region` | `dolnośląskie` | Województwo do cen regionalnych (autocentrum.pl) — wartość startowa, potem edycja w Ustawieniach |
| `odo_budget_entity` | `sensor.odo_vs_budget` | Encja HA z zapasem km leasingu — tylko do porównania z nowym wyliczeniem per auto (strona Statystyki) |
| `drivvo_email` / `drivvo_password` | — | Konto Drivvo do jednorazowego importu |
| `drivvo_vehicle_id` | `0` | ID pojazdu w Drivvo (`0` = pierwszy z konta) |
| `notify_service` | `notify.mobile_app_op12` | Usługa powiadomień dla wbudowanych alertów — wartość startowa, potem edycja w Ustawieniach (karta „Powiadomienia") |
| `mqtt_host` / `mqtt_port` / `mqtt_user` / `mqtt_password` | `core-mosquitto` / `1883` | Broker MQTT |
| `log_level` | `info` | `debug` / `info` / `warning` / `error` |
| `backup_share` | `/share/fuel_tracker` | Katalog backupów i auto-importu |
| `timezone` | `Europe/Warsaw` | Strefa czasowa |

Domyślna waluta tankowań (`default_currency`, domyślnie `PLN`) jest ustawieniem
wyłącznie w UI (karta Budżet) — nie ma odpowiednika w opcjach Supervisora.

## REST API (przez ingress)

`GET /api/summary` · CRUD `/api/fillups` · `GET /api/prefill` ·
`GET /api/rate` · CRUD `/api/expenses` · `GET|PUT /api/categories` ·
`GET /api/stations` · `GET /api/stations/nearby` · `GET /api/map-data` ·
`POST /api/receipts/parse` · `GET /api/attachments/<id>` ·
`GET /api/statistics` · `GET /api/report.csv` · `POST /api/import/csv` ·
`POST /api/import/drivvo` · `GET /api/verify` · `GET /api/export/fuelio.csv` ·
`GET|PUT /api/settings` · `GET /api/ha-services` ·
`GET /api/vehicles` · `POST /api/vehicles` (od 0.9.0 także pola leasingu) ·
`GET|PUT|DELETE /api/vehicles/<id>` ·
`POST /api/vehicles/<id>/activate` · `POST /api/vehicles/<id>/archive` ·
`POST /api/vehicles/<id>/unarchive` · `GET /api/health` ·
`GET /api/backup/list` · `POST /api/backup/restore` ·
`POST /api/backup/restore/upload` · `GET /api/backup/export.json` ·
`POST /api/backup/import.json` · `GET /manifest.webmanifest`

## Plan rozwoju

- **0.11.0** — pełny multi-vehicle (kilka aut z równoległymi sensorami
  MQTT, przełącznik widoku na każdej stronie UI).

## Rozwój

```bash
cd fuel_tracker
pip install -r requirements.txt pytest
pytest
```

Testy używają wyłącznie syntetycznych fixtur (bez danych osobowych).
