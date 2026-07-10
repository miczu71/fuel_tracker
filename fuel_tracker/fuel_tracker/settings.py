"""Ustawienia edytowalne w UI (0.7.0) — typed KV store w tabeli settings.

Opcje Supervisora zostają jako wartość startowa: seed_from_options() zasila
tylko brakujące klucze (INSERT OR IGNORE), więc baza ma pierwszeństwo nad
opcjami po pierwszym uruchomieniu.
"""
from __future__ import annotations

import sqlite3

SETTINGS_TYPES: dict[str, type] = {
    "monthly_fuel_budget": float,
    "default_currency": str,
    "price_region": str,
    "odometer_entity": str,
    "fuel_level_entity": str,
    "location_entity": str,
    "alert_budget_automation": str,
    "alert_cheap_fuel_automation": str,
    "alert_lease_automation": str,
    "active_vehicle_id": int,
}

DEFAULTS: dict[str, object] = {
    "monthly_fuel_budget": 0.0,
    "default_currency": "PLN",
    "price_region": "",
    "odometer_entity": "",
    "fuel_level_entity": "",
    "location_entity": "",
    "alert_budget_automation": "",
    "alert_cheap_fuel_automation": "",
    "alert_lease_automation": "",
    "active_vehicle_id": 0,
}


def get_settings(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    stored = {r["key"]: r["value"] for r in rows}
    result = {}
    for key, typ in SETTINGS_TYPES.items():
        raw = stored.get(key)
        result[key] = typ(raw) if raw is not None else DEFAULTS[key]
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
