"""SQLite: połączenie, migracje (PRAGMA user_version) i seed danych."""
from __future__ import annotations

import os
import sqlite3

# Domyślne kategorie wydatków + "Inne" jako kubeł na nieznane.
# "Płyny" (AdBlue, spryskiwacze) — typowe wydatki z kart flotowych.
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
    # v3 — cache kursów NBP (waluty aktywne od 0.4.0); klucz to data
    # tankowania (req_date), bo NBP nie publikuje kursów w dni wolne
    # i effective_date bywa wcześniejsza.
    """
    CREATE TABLE exchange_rates (
        code TEXT NOT NULL,
        req_date TEXT NOT NULL,
        rate REAL NOT NULL,
        effective_date TEXT NOT NULL,
        PRIMARY KEY (code, req_date)
    );
    """,
    # v4 — załączniki (zdjęcia paragonów, 0.5.0). Plik żyje w
    # <backup_share>/attachments/; wiersz może być powiązany z tankowaniem
    # i/lub wydatkiem (paragon mieszany tworzy oba wpisy). Usunięcie wpisu
    # nie usuwa zdjęcia (ON DELETE SET NULL) — paragon zostaje jako dowód.
    """
    CREATE TABLE attachments (
        id INTEGER PRIMARY KEY,
        filename TEXT NOT NULL,
        fillup_id INTEGER REFERENCES fillups(id) ON DELETE SET NULL,
        expense_id INTEGER REFERENCES expenses(id) ON DELETE SET NULL,
        parsed_json TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX idx_attachments_fillup ON attachments(fillup_id);
    CREATE INDEX idx_attachments_expense ON attachments(expense_id);
    """,
    # v5 — ustawienia edytowalne w UI (0.7.0). Klucz/wartość (wartość zawsze
    # TEXT, typowanie po stronie fuel_tracker.settings); seedowane raz z opcji
    # Supervisora przy starcie, potem baza ma pierwszeństwo.
    """
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """,
    # v6 — pojazdy: cykl życia + leasing per auto (0.8.0). Aktywny pojazd
    # (settings.active_vehicle_id) wybiera, którego dotyczą sensory MQTT/
    # pulpit/statystyki; archived=1 wyklucza z listy kandydatów na aktywny
    # bez usuwania historii tankowań/wydatków.
    """
    ALTER TABLE vehicles ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE vehicles ADD COLUMN lease_start TEXT;
    ALTER TABLE vehicles ADD COLUMN lease_end TEXT;
    ALTER TABLE vehicles ADD COLUMN lease_km_limit INTEGER;
    ALTER TABLE vehicles ADD COLUMN monthly_rate REAL;
    """,
    # v7 — powiadomienia w add-onie (0.9.0). alert_state trzyma ostatni stan
    # każdego alertu (dedup + anty-flap 24 h, przeżywa restarty). Klucze
    # alert_*_automation stają się zbędne — alerty nie żyją już
    # w automatyzacjach HA, tylko w fuel_tracker.notifications.
    """
    CREATE TABLE alert_state (
        alert TEXT PRIMARY KEY,
        state TEXT NOT NULL DEFAULT 'ok',
        changed_at TEXT,
        notified_state TEXT,
        notified_at TEXT
    );

    DELETE FROM settings WHERE key IN (
        'alert_budget_automation',
        'alert_cheap_fuel_automation',
        'alert_lease_automation'
    );
    """,
    # v8 — pełny multi-vehicle (0.11.0): alert_state przechodzi z PRIMARY KEY
    # (alert) na (alert, vehicle_id) — dwa auta nie mogą już dzielić jednego
    # stanu anty-flap tego samego alertu. SQLite nie umie zmienić PRIMARY KEY
    # przez ALTER TABLE — create-new/copy/drop/rename. Backfill istniejących
    # wierszy: skonfigurowany active_vehicle_id (jeśli wskazuje na pojazd
    # nie-zarchiwizowany), inaczej pierwszy nie-zarchiwizowany, inaczej
    # dowolny pojazd (nie powinno się zdarzyć w praktyce — same zarchiwizowane
    # auta z istniejącym stanem alertu — ale FK wymaga jakiegoś id).
    """
    CREATE TABLE alert_state_new (
        alert TEXT NOT NULL,
        vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
        state TEXT NOT NULL DEFAULT 'ok',
        changed_at TEXT,
        notified_state TEXT,
        notified_at TEXT,
        PRIMARY KEY (alert, vehicle_id)
    );

    INSERT INTO alert_state_new
        (alert, vehicle_id, state, changed_at, notified_state, notified_at)
    SELECT a.alert,
        COALESCE(
            (SELECT v.id FROM vehicles v
             JOIN settings s ON s.key = 'active_vehicle_id'
             WHERE v.id = CAST(s.value AS INTEGER) AND v.archived = 0),
            (SELECT id FROM vehicles WHERE archived = 0 ORDER BY id LIMIT 1),
            (SELECT id FROM vehicles ORDER BY id LIMIT 1)
        ),
        a.state, a.changed_at, a.notified_state, a.notified_at
    FROM alert_state a;

    DELETE FROM alert_state_new WHERE vehicle_id IS NULL;

    DROP TABLE alert_state;
    ALTER TABLE alert_state_new RENAME TO alert_state;
    """,
    # v9 — pełny multi-vehicle (0.11.0): encje HA i budżet miesięczny stają
    # się polami per pojazd (dotąd globalne w settings, więc dwa auta
    # dzieliłyby jeden odometr/budżet). Backfill: każdy ISTNIEJĄCY pojazd
    # dostaje kopię dzisiejszych globalnych wartości (świeże instalacje nie
    # mają czego backfillować — ensure_vehicle() przyjmuje je jako kwargs
    # z opcji Supervisora). Klucze globalne usuwane, żeby nie zostały martwe
    # obok pól per-pojazdowych; price_region i progi alertów zostają globalne
    # (świadome uproszczenie tego wydania — patrz plan 0.11.0).
    """
    ALTER TABLE vehicles ADD COLUMN odometer_entity TEXT;
    ALTER TABLE vehicles ADD COLUMN fuel_level_entity TEXT;
    ALTER TABLE vehicles ADD COLUMN location_entity TEXT;
    ALTER TABLE vehicles ADD COLUMN monthly_fuel_budget REAL NOT NULL DEFAULT 0;

    UPDATE vehicles SET
        odometer_entity = (SELECT value FROM settings WHERE key = 'odometer_entity'),
        fuel_level_entity = (SELECT value FROM settings WHERE key = 'fuel_level_entity'),
        location_entity = (SELECT value FROM settings WHERE key = 'location_entity'),
        monthly_fuel_budget = COALESCE(
            (SELECT CAST(value AS REAL) FROM settings WHERE key = 'monthly_fuel_budget'),
            0);

    DELETE FROM settings WHERE key IN (
        'odometer_entity', 'fuel_level_entity', 'location_entity',
        'monthly_fuel_budget'
    );
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
                   fuel_type: str, odometer_entity: str | None = None,
                   fuel_level_entity: str | None = None,
                   location_entity: str | None = None,
                   monthly_fuel_budget: float = 0) -> int:
    """Świeża instalacja: jedyny pojazd musi dostać encje HA/budżet startowe
    z opcji Supervisora — migracja #9 backfilluje tylko istniejące pojazdy
    przy upgrade, nie ma tu nic do przepisania z pustej tabeli settings."""
    row = conn.execute("SELECT id FROM vehicles ORDER BY id LIMIT 1").fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO vehicles (name, tank_capacity_l, fuel_type, "
        "odometer_entity, fuel_level_entity, location_entity, "
        "monthly_fuel_budget) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, tank_capacity_l, fuel_type, odometer_entity,
         fuel_level_entity, location_entity, monthly_fuel_budget),
    )
    conn.commit()
    return cur.lastrowid


_VEHICLE_COLUMNS = (
    "id, name, tank_capacity_l, fuel_type, archived, lease_start, "
    "lease_end, lease_km_limit, monthly_rate, odometer_entity, "
    "fuel_level_entity, location_entity, monthly_fuel_budget"
)


def get_vehicle(conn: sqlite3.Connection, vehicle_id: int) -> dict | None:
    row = conn.execute(
        f"SELECT {_VEHICLE_COLUMNS} FROM vehicles WHERE id = ?",
        (vehicle_id,)).fetchone()
    return dict(row) if row else None


def update_vehicle(conn: sqlite3.Connection, vehicle_id: int, fields: dict) -> bool:
    allowed = {"name", "tank_capacity_l", "fuel_type", "lease_start",
               "lease_end", "lease_km_limit", "monthly_rate",
               "odometer_entity", "fuel_level_entity", "location_entity",
               "monthly_fuel_budget"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    cur = conn.execute(
        f"UPDATE vehicles SET {set_clause} WHERE id = ?",
        (*updates.values(), vehicle_id))
    conn.commit()
    return cur.rowcount > 0


def create_vehicle(conn: sqlite3.Connection, name: str, tank_capacity_l: float,
                   fuel_type: str, lease_start: str | None = None,
                   lease_end: str | None = None,
                   lease_km_limit: int | None = None,
                   monthly_rate: float | None = None,
                   odometer_entity: str | None = None,
                   fuel_level_entity: str | None = None,
                   location_entity: str | None = None,
                   monthly_fuel_budget: float = 0) -> int:
    cur = conn.execute(
        "INSERT INTO vehicles (name, tank_capacity_l, fuel_type, lease_start, "
        "lease_end, lease_km_limit, monthly_rate, odometer_entity, "
        "fuel_level_entity, location_entity, monthly_fuel_budget) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, tank_capacity_l, fuel_type, lease_start, lease_end,
         lease_km_limit, monthly_rate, odometer_entity, fuel_level_entity,
         location_entity, monthly_fuel_budget))
    conn.commit()
    return cur.lastrowid


def list_vehicles(conn: sqlite3.Connection,
                  include_archived: bool = False) -> list[dict]:
    q = f"SELECT {_VEHICLE_COLUMNS} FROM vehicles"
    if not include_archived:
        q += " WHERE archived = 0"
    q += " ORDER BY id"
    return [dict(r) for r in conn.execute(q).fetchall()]


def resolve_active_vehicle_id(conn: sqlite3.Connection,
                              configured_id: int) -> int | None:
    """Aktywny pojazd: skonfigurowany w settings, jeśli istnieje i nie jest
    zarchiwizowany; w przeciwnym razie pierwszy nie-zarchiwizowany pojazd."""
    if configured_id:
        row = conn.execute(
            "SELECT id FROM vehicles WHERE id = ? AND archived = 0",
            (configured_id,)).fetchone()
        if row:
            return row["id"]
    row = conn.execute(
        "SELECT id FROM vehicles WHERE archived = 0 ORDER BY id LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def archive_vehicle(conn: sqlite3.Connection, vehicle_id: int) -> bool:
    """Odmawia, gdyby to był ostatni pozostały aktywny pojazd (musi zostać
    co najmniej jeden kandydat na aktywny)."""
    remaining = conn.execute(
        "SELECT COUNT(*) AS n FROM vehicles WHERE archived = 0 AND id != ?",
        (vehicle_id,)).fetchone()["n"]
    if remaining == 0:
        return False
    cur = conn.execute(
        "UPDATE vehicles SET archived = 1 WHERE id = ?", (vehicle_id,))
    conn.commit()
    return cur.rowcount > 0


def unarchive_vehicle(conn: sqlite3.Connection, vehicle_id: int) -> bool:
    cur = conn.execute(
        "UPDATE vehicles SET archived = 0 WHERE id = ?", (vehicle_id,))
    conn.commit()
    return cur.rowcount > 0


def can_delete_vehicle(conn: sqlite3.Connection,
                       vehicle_id: int) -> tuple[bool, str | None]:
    total = conn.execute("SELECT COUNT(*) AS n FROM vehicles").fetchone()["n"]
    if total <= 1:
        return False, "Nie można usunąć jedynego pojazdu"
    has_history = (
        conn.execute("SELECT 1 FROM fillups WHERE vehicle_id = ? LIMIT 1",
                     (vehicle_id,)).fetchone()
        or conn.execute("SELECT 1 FROM expenses WHERE vehicle_id = ? LIMIT 1",
                        (vehicle_id,)).fetchone())
    if has_history:
        return False, ("Pojazd ma historię tankowań/wydatków — "
                       "zarchiwizuj zamiast usuwać")
    return True, None


def delete_vehicle(conn: sqlite3.Connection,
                   vehicle_id: int) -> tuple[bool, str | None]:
    ok, reason = can_delete_vehicle(conn, vehicle_id)
    if not ok:
        return False, reason
    conn.execute("DELETE FROM vehicles WHERE id = ?", (vehicle_id,))
    conn.commit()
    return True, None


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
