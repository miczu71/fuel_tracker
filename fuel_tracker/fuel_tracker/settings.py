"""Ustawienia edytowalne w UI (0.7.0) — typed KV store w tabeli settings.

Opcje Supervisora zostają jako wartość startowa: seed_from_options() zasila
tylko brakujące klucze (INSERT OR IGNORE), więc baza ma pierwszeństwo nad
opcjami po pierwszym uruchomieniu.
"""
from __future__ import annotations

import sqlite3

# Uwaga: przełączniki alertów jako int 0/1, nie bool — get_settings() typuje
# konstruktorem, a bool("0") is True.
SETTINGS_TYPES: dict[str, type] = {
    "monthly_fuel_budget": float,
    "default_currency": str,
    "price_region": str,
    "odometer_entity": str,
    "fuel_level_entity": str,
    "location_entity": str,
    "notify_service": str,
    "alert_budget_enabled": int,
    "alert_cheap_fuel_enabled": int,
    "alert_lease_enabled": int,
    "alert_budget_threshold": float,
    "alert_cheap_fuel_delta": float,
    "alert_lease_km_threshold": int,
    "active_vehicle_id": int,
}

# Progi domyślne odpowiadają dawnym automatyzacjom z fuel_tracker_package.yaml.
DEFAULTS: dict[str, object] = {
    "monthly_fuel_budget": 0.0,
    "default_currency": "PLN",
    "price_region": "",
    "odometer_entity": "",
    "fuel_level_entity": "",
    "location_entity": "",
    "notify_service": "notify.mobile_app_op12",
    "alert_budget_enabled": 1,
    "alert_cheap_fuel_enabled": 1,
    "alert_lease_enabled": 1,
    "alert_budget_threshold": 100.0,
    "alert_cheap_fuel_delta": 0.20,
    "alert_lease_km_threshold": 1000,
    "active_vehicle_id": 0,
}


def normalize_notify_service(service: str) -> str:
    """Ujednolica zapis do formatu kropkowego: 'notify/x' -> 'notify.x'.

    Stare seedy z options.json używały formatu ścieżkowego HA API.
    """
    service = (service or "").strip()
    if "/" in service and "." not in service:
        service = service.replace("/", ".", 1)
    return service


def get_settings(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    stored = {r["key"]: r["value"] for r in rows}
    result = {}
    for key, typ in SETTINGS_TYPES.items():
        raw = stored.get(key)
        result[key] = typ(raw) if raw is not None else DEFAULTS[key]
    result["notify_service"] = normalize_notify_service(result["notify_service"])
    return result


def set_settings(conn: sqlite3.Connection, updates: dict) -> None:
    for key, value in updates.items():
        if key not in SETTINGS_TYPES:
            continue
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)))
    conn.commit()


def seed_from_options(conn: sqlite3.Connection, options: dict) -> None:
    for key, value in options.items():
        if key not in SETTINGS_TYPES or value is None:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, str(value)))
    conn.commit()
