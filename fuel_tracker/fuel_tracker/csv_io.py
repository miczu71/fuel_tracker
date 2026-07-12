"""Import/eksport dziennika w formacie CSV.

Format pliku: sekcje rozdzielone wierszami "## Nazwa", każda sekcja
ma własny nagłówek CSV. Obsługiwane sekcje: Vehicle, Log (tankowania),
CostCategories, Costs (wydatki). Pozostałe (Category, ...) są ignorowane.
"""
from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import dataclass, field

from . import db as dbm

LOG_HEADER = [
    "Data", "Odo (km)", "Fuel (litres)", "Full", "Price (optional)",
    "l/100km (optional)", "latitude (optional)", "longitude (optional)",
    "City (optional)", "Notes (optional)", "Missed", "TankNumber", "FuelType",
    "VolumePrice", "StationID (optional)", "ExcludeDistance", "UniqueId",
    "TankCalc", "Weather", "guid", "lastupdated",
]


@dataclass
class ImportReport:
    fillups_added: int = 0
    fillups_skipped: int = 0
    expenses_added: int = 0
    expenses_skipped: int = 0
    total_cost: float = 0.0      # suma PLN dodanych+pominiętych tankowań z pliku
    total_volume: float = 0.0    # suma litrów z pliku
    fillups_in_file: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "fillups_added": self.fillups_added,
            "fillups_skipped": self.fillups_skipped,
            "expenses_added": self.expenses_added,
            "expenses_skipped": self.expenses_skipped,
            "fillups_in_file": self.fillups_in_file,
            "total_cost": round(self.total_cost, 3),
            "total_volume": round(self.total_volume, 3),
            "errors": self.errors,
        }


def parse_sections(text: str) -> dict[str, list[dict]]:
    """Tnie plik CSV na sekcje; zwraca {nazwa: [wiersze jako dict]}."""
    sections: dict[str, list[dict]] = {}
    current: str | None = None
    header: list[str] | None = None
    for row in csv.reader(io.StringIO(text)):
        if not row or all(c == "" for c in row):
            continue
        if len(row) == 1 and row[0].startswith("## "):
            current = row[0][3:].strip()
            sections[current] = []
            header = None
            continue
        if current is None:
            continue
        if header is None:
            header = row
            continue
        # Wiersze mogą mieć mniej/więcej pól niż nagłówek (np. puste Weather).
        padded = row + [""] * (len(header) - len(row))
        sections[current].append(dict(zip(header, padded)))
    return sections


def _f(value: str | None) -> float:
    try:
        return float(value) if value not in (None, "") else 0.0
    except ValueError:
        return 0.0


def _fillup_from_row(row: dict, default_fuel_type: str) -> dict | None:
    date = (row.get("Data") or "").strip()
    if not date:
        return None
    volume = _f(row.get("Fuel (litres)"))
    total = _f(row.get("Price (optional)"))
    price = _f(row.get("VolumePrice"))
    if not price and volume:
        price = round(total / volume, 3)
    lat = _f(row.get("latitude (optional)")) or None
    lon = _f(row.get("longitude (optional)")) or None
    return {
        "date": date,
        "odometer": int(_f(row.get("Odo (km)"))),
        "volume_l": volume,
        "price_per_l": price,
        "total_cost": total,
        "full_tank": 1 if (row.get("Full") or "0").strip() == "1" else 0,
        "missed_previous": 1 if (row.get("Missed") or "0").strip() == "1" else 0,
        "fuel_type": default_fuel_type,
        "station": (row.get("City (optional)") or "").strip() or None,
        "latitude": lat,
        "longitude": lon,
        "notes": (row.get("Notes (optional)") or "").strip() or None,
        "source_uid": (row.get("UniqueId") or "").strip() or None,
    }


def import_into(conn: sqlite3.Connection, vehicle_id: int, text: str,
                default_fuel_type: str = "PB95") -> ImportReport:
    """Idempotentny import pliku CSV do bazy."""
    report = ImportReport()
    sections = parse_sections(text)

    for row in sections.get("Log", []):
        f = _fillup_from_row(row, default_fuel_type)
        if f is None:
            continue
        report.fillups_in_file += 1
        report.total_cost += f["total_cost"]
        report.total_volume += f["volume_l"]
        cur = conn.execute(
            """INSERT OR IGNORE INTO fillups
               (vehicle_id, date, odometer, volume_l, price_per_l, total_cost,
                full_tank, missed_previous, fuel_type, station, latitude,
                longitude, notes, source, source_uid)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'csv',?)""",
            (vehicle_id, f["date"], f["odometer"], f["volume_l"],
             f["price_per_l"], f["total_cost"], f["full_tank"],
             f["missed_previous"], f["fuel_type"], f["station"], f["latitude"],
             f["longitude"], f["notes"], f["source_uid"]),
        )
        if cur.rowcount:
            report.fillups_added += 1
        else:
            report.fillups_skipped += 1

    cat_by_id = {
        (r.get("CostTypeID") or "").strip(): (r.get("Name") or "").strip()
        for r in sections.get("CostCategories", [])
    }
    for row in sections.get("Costs", []):
        if (row.get("isIncome") or "0").strip() == "1":
            continue
        date = (row.get("Date") or "").strip()
        cost = _f(row.get("Cost"))
        if not date or not cost:
            continue
        cat_name = cat_by_id.get((row.get("CostTypeID") or "").strip())
        title = (row.get("CostTitle") or "").strip()
        notes = (row.get("Notes") or "").strip()
        desc = " — ".join(x for x in (title, notes) if x) or None
        odo = int(_f(row.get("Odo"))) or None
        cur = conn.execute(
            """INSERT OR IGNORE INTO expenses
               (vehicle_id, date, odometer, category_id, description, cost,
                source, source_uid)
               VALUES (?,?,?,?,?,?,'csv',?)""",
            (vehicle_id, date, odo, dbm.category_id(conn, cat_name), desc, cost,
             (row.get("UniqueId") or "").strip() or None),
        )
        if cur.rowcount:
            report.expenses_added += 1
        else:
            report.expenses_skipped += 1

    conn.commit()
    return report


def export_csv(conn: sqlite3.Connection, vehicle_id: int) -> str:
    """Eksport CSV (sekcje Vehicle/Log/CostCategories/Costs)."""
    out = io.StringIO()
    w = csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator="\n")

    v = conn.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
    w.writerow(["## Vehicle"])
    w.writerow(["Name", "Make", "Model", "TankCount", "Tank1Capacity"])
    w.writerow([v["name"], v["make"] or "", v["model"] or "", "1",
                v["tank_capacity_l"] or ""])

    w.writerow(["## Log"])
    w.writerow(LOG_HEADER)
    rows = conn.execute(
        "SELECT * FROM fillups WHERE vehicle_id = ? AND draft = 0 "
        "ORDER BY odometer DESC", (vehicle_id,),
    ).fetchall()
    for f in rows:
        w.writerow([
            f["date"], f"{f['odometer']:.1f}", f["volume_l"], f["full_tank"],
            f["total_cost"], "", f["latitude"] or "", f["longitude"] or "",
            f["station"] or "", f["notes"] or "", f["missed_previous"], "1",
            "0", f["price_per_l"], "0", "0.0", f["source_uid"] or f["id"],
            "0.0", "", "", "",
        ])

    w.writerow(["## CostCategories"])
    w.writerow(["CostTypeID", "Name", "priority", "color"])
    cats = conn.execute("SELECT * FROM expense_categories ORDER BY id").fetchall()
    for c in cats:
        w.writerow([c["id"], c["name"], "0", ""])

    w.writerow(["## Costs"])
    w.writerow(["CostTitle", "Date", "Odo", "CostTypeID", "Notes", "Cost",
                "isIncome", "UniqueId"])
    for e in conn.execute(
        "SELECT * FROM expenses WHERE vehicle_id = ? ORDER BY date DESC",
        (vehicle_id,),
    ).fetchall():
        w.writerow([e["description"] or "", e["date"], e["odometer"] or 0,
                    e["category_id"] or "", "", e["cost"], "0",
                    e["source_uid"] or e["id"]])
    return out.getvalue()
