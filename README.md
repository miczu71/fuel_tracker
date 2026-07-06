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
- **Statystyki jak w Fuelio** — spalanie L/100km liczone segmentami między
  tankowaniami do pełna (partiale wliczane do segmentu), średnia ogólna
  Σlitrów/Σkm (nie średnia średnich), koszt/km, serie miesięczne.
- **Stacje po GPS** — przy otwarciu formularza add-on pobiera pozycję telefonu
  (`location_entity`, aplikacja mobilna HA) i dopasowuje najbliższą zapisaną
  stację (300 m); bez dopasowania podpowiada nazwę z OSM Overpass (500 m).
- **Mapa tankowań** — podstrona z pinami stacji (Leaflet + kafelki OSM):
  rozmiar pinu = liczba wizyt, kolor odróżnia tankowania prywatne
  i zagraniczne, popup ze statystykami stacji.
- **Tankowania opłacone prywatnie** — checkbox „Zapłacone przeze mnie",
  oznaczenie na liście i mapie, osobny sensor `self_paid_fuel_total`
  do rozliczenia zysku z wynajmu.
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

> Rzeczywiste `entity_id` zależą od nazwy urządzenia — po pierwszym starcie
> zweryfikuj je w **Narzędzia deweloperskie → Stany**.

## Konfiguracja

| Opcja | Domyślnie | Opis |
|---|---|---|
| `vehicle_name` | `Skoda Superb` | Nazwa pojazdu (i prefiks urządzenia MQTT) |
| `tank_capacity_l` | `66.0` | Pojemność baku [L] |
| `default_fuel_type` | `PB95` | Domyślny typ paliwa w formularzu |
| `monthly_fuel_budget` | `984.0` | Miesięczny budżet paliwowy [PLN] |
| `odometer_entity` | `sensor.skoda_superb_mileage` | Encja odometru do prefill formularza |
| `fuel_level_entity` | `sensor.skoda_superb_fuel_level` | Encja poziomu paliwa (nieużywana) |
| `location_entity` | `device_tracker.op12` | Encja z GPS telefonu do dopasowania stacji w formularzu |
| `drivvo_email` / `drivvo_password` | — | Konto Drivvo do jednorazowego importu |
| `drivvo_vehicle_id` | `0` | ID pojazdu w Drivvo (`0` = pierwszy z konta) |
| `notify_service` | `notify/family` | Usługa powiadomień |
| `mqtt_host` / `mqtt_port` / `mqtt_user` / `mqtt_password` | `core-mosquitto` / `1883` | Broker MQTT |
| `log_level` | `info` | `debug` / `info` / `warning` / `error` |
| `backup_share` | `/share/fuel_tracker` | Katalog backupów i auto-importu |
| `timezone` | `Europe/Warsaw` | Strefa czasowa |

## REST API (przez ingress)

`GET /api/summary` · CRUD `/api/fillups` · `GET /api/prefill` ·
CRUD `/api/expenses` · `GET|PUT /api/categories` · `GET /api/stations` ·
`GET /api/stations/nearby` · `GET /api/map-data` · `POST /api/import/csv` ·
`POST /api/import/drivvo` · `GET /api/verify` · `GET /api/export/fuelio.csv` ·
`GET /api/health`

## Plan rozwoju

- **0.3.0** — parser paragonów ze zdjęcia (aparat/galeria w aplikacji
  mobilnej HA, LLM vision), rozdział paragonu na tankowanie + „Płyny".
- **0.4.0** — tankowania za granicą (waluty, kursy NBP), scraper lokalnych
  cen paliw, rozbudowane statystyki (rankingi stacji, rekordy, leasing).
- **0.5.0** — pakiet YAML dla HA i karta Lovelace.

## Rozwój

```bash
cd fuel_tracker
pip install -r requirements.txt pytest
pytest
```

Testy używają wyłącznie syntetycznych fixtur (bez danych osobowych).
