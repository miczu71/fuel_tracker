# Changelog

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
