"""SQLite: połączenie, migracje (PRAGMA user_version) i seed danych."""
from __future__ import annotations

import os
import sqlite3

# Kategorie 1:1 z eksportu Fuelio użytkownika + "Inne" jako kubeł na nieznane.
# "Płyny" (AdBlue, spryskiwacze) — domyślna kategoria wydatków z karty ORLEN Flota.
DEFAULT_CATEGORIES = [
    "Serwis", "Eksploatacja", "Rejestracja", "Parking", "Myjnia",
    "Opłaty za przejazd", "Mandaty", "Tuning", "Ubezpieczenie", "Płyny", "Inne",
]

_MIGRATIONS = [
    # v1 — schemat początkowy
    """
    CREATE TABLE vehicles (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        make TEXT, model TEXT, plate TEXT,
        fuel_type TEXT DEFAULT 'PB95',
        tank_capacity_l REAL,
        drivvo_vehicle_id INTEGER,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE fillups (
        id INTEGER PRIMARY KEY,
        vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
        date TEXT NOT NULL,
        odometer INTEGER NOT NULL,
        volume_l REAL NOT NULL,
        price_per_l REAL NOT NULL,
        total_cost REAL NOT NULL,
        full_tank INTEGER NOT NULL DEFAULT 1,
        missed_previous INTEGER NOT NULL DEFAULT 0,
        draft INTEGER NOT NULL DEFAULT 0,
        fuel_type TEXT,
        station TEXT,
        latitude REAL, longitude REAL,
        notes TEXT,
        source TEXT NOT NULL DEFAULT 'manual',
        source_uid TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(vehicle_id, date, odometer)
    );
    CREATE INDEX idx_fillups_vehicle_odo ON fillups(vehicle_id, odometer);

    CREATE TABLE expense_categories (
        id INTEGER PRIMARY KEY,
        name TEXT UNIQUE NOT NULL,
        icon TEXT
    );

    CREATE TABLE expenses (
        id INTEGER PRIMARY KEY,
        vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
        date TEXT NOT NULL,
        odometer INTEGER,
        category_id INTEGER REFERENCES expense_categories(id),
        description TEXT,
        cost REAL NOT NULL,
        source TEXT NOT NULL DEFAULT 'manual',
        source_uid TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(vehicle_id, date, cost, description)
    );

    CREATE TABLE fuel_prices (
        id INTEGER PRIMARY KEY,
        fetched_at TEXT NOT NULL,
        station TEXT NOT NULL,
        fuel_type TEXT NOT NULL,
        price REAL NOT NULL,
        source TEXT NOT NULL,
        UNIQUE(fetched_at, station, fuel_type)
    );
    """,
    # v2 — stacje po GPS, tankowania prywatne (paid_by), kolumny walutowe
    # (waluty aktywne od 0.4.0), ukrywanie kategorii. Backfill stacji
    # z historycznych wpisów (uśrednione współrzędne, jeśli były).
    """
    CREATE TABLE stations (
        id INTEGER PRIMARY KEY,
        name TEXT UNIQUE NOT NULL,
        brand TEXT,
        latitude REAL, longitude REAL,
        country TEXT NOT NULL DEFAULT 'PL',
        created_at TEXT DEFAULT (datetime('now'))
    );

    ALTER TABLE fillups ADD COLUMN paid_by TEXT NOT NULL DEFAULT 'fleet_card';
    ALTER TABLE fillups ADD COLUMN currency TEXT NOT NULL DEFAULT 'PLN';
    ALTER TABLE fillups ADD COLUMN price_per_l_orig REAL;
    ALTER TABLE fillups ADD COLUMN total_cost_orig REAL;
    ALTER TABLE fillups ADD COLUMN exchange_rate REAL;
    CREATE INDEX idx_fillups_vehicle_date ON fillups(vehicle_id, date);

    ALTER TABLE expense_categories ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0;

    INSERT OR IGNORE INTO stations (name, latitude, longitude)
    SELECT station, AVG(latitude), AVG(longitude)
    FROM fillups
    WHERE station IS NOT NULL AND TRIM(station) != ''
    GROUP BY station;
    """,
]


def get_conn(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or os.environ.get("DB_PATH", "/data/fuel_tracker.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, script in enumerate(_MIGRATIONS[version:], start=version + 1):
        conn.executescript(script)
        conn.execute(f"PRAGMA user_version = {i}")
        conn.commit()
    _seed_categories(conn)


def _seed_categories(conn: sqlite3.Connection) -> None:
    for name in DEFAULT_CATEGORIES:
        conn.execute(
            "INSERT OR IGNORE INTO expense_categories (name) VALUES (?)", (name,)
        )
    conn.commit()


def ensure_vehicle(conn: sqlite3.Connection, name: str, tank_capacity_l: float,
                   fuel_type: str) -> int:
    row = conn.execute("SELECT id FROM vehicles ORDER BY id LIMIT 1").fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO vehicles (name, tank_capacity_l, fuel_type) VALUES (?, ?, ?)",
        (name, tank_capacity_l, fuel_type),
    )
    conn.commit()
    return cur.lastrowid


def category_id(conn: sqlite3.Connection, name: str | None) -> int:
    """Id kategorii po nazwie; nieznane nazwy trafiają do 'Inne'."""
    if name:
        row = conn.execute(
            "SELECT id FROM expense_categories WHERE name = ?", (name,)
        ).fetchone()
        if row:
            return row["id"]
    return conn.execute(
        "SELECT id FROM expense_categories WHERE name = 'Inne'"
    ).fetchone()["id"]
