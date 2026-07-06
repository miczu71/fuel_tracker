# Changelog

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
- Nowa opcja add-onu: `location_entity` (domyślnie `device_tracker.op12`)

## 0.1.3

- Import wydatków z Drivvo działa z realnym schematem web API: kwoty są
  w zagnieżdżonej liście `tipos_despesa[].valor` (nie `valor_total`),
  opis w `observacao`, id w `id_despesa`
- Dedup wydatków między Fuelio CSV a API Drivvo po (odometr, kwota) —
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
- Silnik statystyk w stylu Fuelio: spalanie L/100km liczone segmentami między pełnymi bakami, średnia ogólna Σvol/Σdist, koszt/km
- Wydatki w kategoriach (Serwis, Eksploatacja, Parking, Myjnia, …)
- Import historii z eksportu Fuelio CSV (upload w UI lub auto-import z `/share/fuel_tracker/import/`)
- Import wydatków/serwisów z API Drivvo (jednorazowa migracja)
- Raport weryfikacyjny importu (liczba wpisów, suma PLN, suma litrów)
- Eksport do Fuelio CSV
- Sensory MQTT discovery (urządzenie „Superb Fuel"): koszty, spalanie, ostatnie tankowanie, budżet miesięczny
- Web UI po polsku przez ingress (pulpit z wykresami, formularz tankowania z prefill odometru z myskoda)
- Nocny backup bazy do `/share/fuel_tracker/`
