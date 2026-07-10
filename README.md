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
- **Leasing** — zapas km z `sensor.odo_vs_budget` (opcja
  `odo_budget_entity`) + prognoza daty wyczerpania limitu
  (`lease_km_limit`) przy obecnym tempie przebiegu.
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
  działa od razu. Karta „Powiadomienia" pozwala włączać/wyłączać z Ustawień
  automatyzacje alertów zdefiniowane w HA (patrz „Integracja z HA") bez
  wchodzenia do Home Assistant.

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

> Rzeczywiste `entity_id` zależą od nazwy urządzenia — po pierwszym starcie
> zweryfikuj je w **Narzędzia deweloperskie → Stany**.

## Integracja z HA

Sensory MQTT discovery są od razu gotowe do automatyzacji — wystarczy dodać
poniższy pakiet do `packages/` i wpiąć go w `configuration.yaml`
(`homeassistant.packages.fuel_tracker: !include packages/fuel_tracker.yaml`).
Trzy gotowe alerty; podmień `notify.mobile_app_XXX` na swoją usługę
powiadomień, a progi (100 PLN / 0,20 PLN/L / 1000 km) i skalę budżetu
(`861` = `monthly_fuel_budget` − 123 PLN marginesu, dopasuj do swojej opcji)
dopasuj do swoich liczb — encje mają prefiks zależny od `vehicle_name`
(zweryfikuj rzeczywiste `entity_id` w **Narzędzia deweloperskie → Stany**
zanim wkleisz do automatyzacji).

```yaml
automation:
  - id: "fuel_tracker_alert_budzetu"
    alias: "Fuel Tracker: Alert budżetu paliwowego"
    description: >-
      Powiadamia gdy pozostały budżet paliwowy miesiąca spada poniżej
      100 PLN (ostrzeżenie) lub zostaje przekroczony (poniżej 0).
    trigger:
      - platform: numeric_state
        entity_id: sensor.superb_fuel_budget_left_month
        below: 100
        id: ostrzezenie
      - platform: numeric_state
        entity_id: sensor.superb_fuel_budget_left_month
        below: 0
        id: przekroczony
    action:
      - choose:
          - conditions:
              - condition: trigger
                id: przekroczony
            sequence:
              - service: notify.mobile_app_XXX
                data:
                  title: "⛽ Budżet paliwowy przekroczony"
                  message: >-
                    Budżet miesiąca przekroczony o
                    {{ (states('sensor.superb_fuel_budget_left_month') | float(0) | abs) | round(2) }} PLN.
                    Prognoza na cały miesiąc:
                    {{ states('sensor.superb_fuel_month_forecast_cost') | float(0) | round(0) }} PLN.
        default:
          - service: notify.mobile_app_XXX
            data:
              title: "⛽ Budżet paliwowy na wyczerpaniu"
              message: >-
                Zostało {{ states('sensor.superb_fuel_budget_left_month') | float(0) | round(2) }} PLN
                budżetu paliwowego na ten miesiąc. Prognoza na cały miesiąc:
                {{ states('sensor.superb_fuel_month_forecast_cost') | float(0) | round(0) }} PLN.
    mode: single

  - id: "fuel_tracker_tanie_paliwo"
    alias: "Fuel Tracker: Tanie paliwo w regionie"
    description: >-
      Powiadamia gdy cena regionalna (`price_region`) jest co najmniej
      0,20 PLN/L niższa od ceny z ostatniego tankowania — utrzymująca się
      ponad godzinę.
    trigger:
      - platform: numeric_state
        entity_id: sensor.superb_fuel_price_vs_region
        above: 0.20
        for: "01:00:00"
    action:
      - service: notify.mobile_app_XXX
        data:
          title: "⛽ Tanie paliwo w regionie"
          message: >-
            Cena regionalna {{ states('sensor.superb_fuel_region_fuel_price') }} PLN/L —
            o {{ states('sensor.superb_fuel_price_vs_region') | float(0) | round(2) }} PLN/L
            taniej niż Twoje ostatnie tankowanie
            ({{ states('sensor.superb_fuel_last_fillup_price') }} PLN/L).
    mode: single

  - id: "fuel_tracker_tempo_leasingu"
    alias: "Fuel Tracker: Zapas km leasingu topnieje"
    description: >-
      Powiadamia gdy zapas kilometrów względem krzywej limitu leasingu
      (`odo_budget_entity`) spada poniżej 1000 km na dłużej niż 6 h
      (topnieje) lub poniżej 0 (limit przekroczony).
    trigger:
      - platform: numeric_state
        entity_id: sensor.odo_vs_budget
        below: 1000
        for: "06:00:00"
        id: zapas
      - platform: numeric_state
        entity_id: sensor.odo_vs_budget
        below: 0
        id: przekroczony
    action:
      - choose:
          - conditions:
              - condition: trigger
                id: przekroczony
            sequence:
              - service: notify.mobile_app_XXX
                data:
                  title: "🚗 Limit km leasingu przekroczony"
                  message: >-
                    Przebieg wyprzedza krzywą limitu leasingu o
                    {{ (states('sensor.odo_vs_budget') | float(0) | abs) | round(0) }} km.
                    Tempo roczne: {{ states('sensor.superb_fuel_projected_annual_km') }} km/rok.
        default:
          - service: notify.mobile_app_XXX
            data:
              title: "🚗 Zapas km leasingu topnieje"
              message: >-
                Zapas względem krzywej limitu leasingu:
                {{ states('sensor.odo_vs_budget') | float(0) | round(0) }} km.
                Tempo roczne: {{ states('sensor.superb_fuel_projected_annual_km') }} km/rok.
    mode: single
```

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
> restartu add-onu, jest na stronie **Ustawienia** (karty Pojazd / Budżet /
> Ceny regionalne / Encje HA). Zmiana tych opcji w Supervisorze po
> pierwszym starcie nie ma już żadnego efektu — reszta opcji poniżej
> zostaje techniczna (wymaga restartu).

| Opcja | Domyślnie | Opis |
|---|---|---|
| `vehicle_name` | `Skoda Superb` | Nazwa pojazdu (i prefiks urządzenia MQTT) — wartość startowa, potem edycja w Ustawieniach |
| `tank_capacity_l` | `66.0` | Pojemność baku [L] — wartość startowa, potem edycja w Ustawieniach |
| `default_fuel_type` | `PB95` | Domyślny typ paliwa w formularzu — wartość startowa, potem edycja w Ustawieniach |
| `monthly_fuel_budget` | `984.0` | Miesięczny budżet paliwowy [PLN] — wartość startowa, potem edycja w Ustawieniach |
| `odometer_entity` | `sensor.skoda_superb_mileage` | Encja odometru do prefill formularza — wartość startowa, potem edycja w Ustawieniach |
| `fuel_level_entity` | `sensor.skoda_superb_fuel_level` | Encja poziomu paliwa (nieużywana) — wartość startowa, potem edycja w Ustawieniach |
| `location_entity` | `device_tracker.op12` | Encja z GPS telefonu do dopasowania stacji w formularzu — wartość startowa, potem edycja w Ustawieniach |
| `price_region` | `dolnośląskie` | Województwo do cen regionalnych (autocentrum.pl) — wartość startowa, potem edycja w Ustawieniach |
| `odo_budget_entity` | `sensor.odo_vs_budget` | Encja HA z zapasem km leasingu (strona Statystyki) |
| `lease_km_limit` | `90000` | Limit km leasingu do prognozy wyczerpania (0 = wyłączone) |
| `drivvo_email` / `drivvo_password` | — | Konto Drivvo do jednorazowego importu |
| `drivvo_vehicle_id` | `0` | ID pojazdu w Drivvo (`0` = pierwszy z konta) |
| `notify_service` | `notify/family` | Usługa powiadomień (nieużywana — alerty idą przez pakiet automatyzacji HA, patrz „Integracja z HA") |
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
`GET|PUT /api/settings` · `POST /api/settings/toggle-automation` ·
`GET|PUT /api/vehicles/<id>` · `GET /api/health`

## Plan rozwoju

- **0.8.0** — pojazdy: cykl życia (dodawanie/archiwizacja) + leasing per auto.
- **0.9.0** — backup/restore w UI + PWA.

## Rozwój

```bash
cd fuel_tracker
pip install -r requirements.txt pytest
pytest
```

Testy używają wyłącznie syntetycznych fixtur (bez danych osobowych).
