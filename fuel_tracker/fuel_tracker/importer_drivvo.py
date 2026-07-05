"""Jednorazowy import z prywatnego API Drivvo (migracja z aplikacji Drivvo).

Synchroniczny port custom_components/drivvo/api.py: hasło jako md5 w polu
"senha", token w nagłówku "x-token", nagłówki udające przeglądarkę web.drivvo.com.
Tankowania są deduplikowane po (date, odometer) względem importu Fuelio CSV —
Drivvo trzyma daty jako "YYYY-MM-DD HH:MM:SS", ucinamy do minut jak Fuelio.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
from typing import Any

import requests

from . import db as dbm
from .csv_fuelio import ImportReport

logger = logging.getLogger(__name__)

BASE_URL = "https://api.drivvo.com"

# Mapowanie typów wydatków/serwisów Drivvo → kategorie; reszta → 'Inne'.
_CATEGORY_MAP = {
    "serwis": "Serwis", "service": "Serwis",
    "ubezpieczenie": "Ubezpieczenie", "insurance": "Ubezpieczenie",
    "parking": "Parking",
    "myjnia": "Myjnia", "washing": "Myjnia", "lavagem": "Myjnia",
    "rejestracja": "Rejestracja",
    "mandaty": "Mandaty", "mandat": "Mandaty",
    "eksploatacja": "Eksploatacja",
}


def _headers(token: str | None = None) -> dict[str, str]:
    h = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://web.drivvo.com",
        "Referer": "https://web.drivvo.com/",
        "App-Platform": "HA-Drivvo",
        "App-Version": "1",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
        ),
    }
    if token:
        h["x-token"] = token
    return h


class DrivvoError(Exception):
    pass


class DrivvoClient:
    def __init__(self, email: str, password: str, timeout: int = 30) -> None:
        self._email = email
        self._password_md5 = hashlib.md5(password.encode("utf-8")).hexdigest()
        self._timeout = timeout
        self._token: str | None = None

    def login(self) -> None:
        resp = requests.post(
            f"{BASE_URL}/autenticacao/login",
            json={"email": self._email, "senha": self._password_md5},
            headers=_headers(), timeout=self._timeout,
        )
        if resp.status_code in (401, 403):
            raise DrivvoError("Drivvo odrzuciło dane logowania")
        resp.raise_for_status()
        self._token = resp.json().get("token")
        if not self._token:
            raise DrivvoError("Brak tokenu w odpowiedzi logowania")

    def _get(self, path: str) -> Any:
        resp = requests.get(f"{BASE_URL}{path}", headers=_headers(self._token),
                            timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def vehicles(self) -> list[dict]:
        return self._get("/veiculo/web") or []

    def refuellings(self, vehicle_id: int) -> list[dict]:
        return self._get(f"/veiculo/{vehicle_id}/abastecimento/web") or []

    def services(self, vehicle_id: int) -> list[dict]:
        return self._get(f"/veiculo/{vehicle_id}/servico/web") or []

    def expenses(self, vehicle_id: int) -> list[dict]:
        return self._get(f"/veiculo/{vehicle_id}/despesa/web") or []


def _date_minutes(value: str | None) -> str:
    """'YYYY-MM-DD HH:MM:SS' → 'YYYY-MM-DD HH:MM' (format jak w Fuelio CSV)."""
    return (value or "")[:16]


def _volume(row: dict) -> float:
    volume = row.get("volume") or 0
    if volume:
        return float(volume)
    price, total = row.get("preco") or 0, row.get("valor_total") or 0
    return float(total) / float(price) if price else 0.0


def _map_category(conn: sqlite3.Connection, raw: str | None) -> int:
    name = _CATEGORY_MAP.get((raw or "").strip().lower())
    return dbm.category_id(conn, name)


def import_expenses(conn: sqlite3.Connection, vehicle_id: int,
                    client: DrivvoClient, drivvo_vehicle_id: int) -> ImportReport:
    """Import wydatków i serwisów z Drivvo (tankowania pomijane — źródłem jest CSV)."""
    report = ImportReport()
    rows = [
        *(dict(r, _kind="servico") for r in client.services(drivvo_vehicle_id)),
        *(dict(r, _kind="despesa") for r in client.expenses(drivvo_vehicle_id)),
    ]
    for r in rows:
        date = _date_minutes(r.get("data"))
        cost = float(r.get("valor_total") or 0)
        if not date or not cost:
            continue
        raw_type = r.get("tipo_servico") or r.get("tipo_despesa")
        desc = (r.get("descricao") or "").strip() or (raw_type or "").strip() or None
        cur = conn.execute(
            """INSERT OR IGNORE INTO expenses
               (vehicle_id, date, odometer, category_id, description, cost,
                source, source_uid)
               VALUES (?,?,?,?,?,?,'drivvo_api',?)""",
            (vehicle_id, date, r.get("odometro") or None,
             _map_category(conn, raw_type), desc, cost,
             str(r.get("id") or "") or None),
        )
        if cur.rowcount:
            report.expenses_added += 1
        else:
            report.expenses_skipped += 1
    conn.commit()
    return report


def import_refuellings(conn: sqlite3.Connection, vehicle_id: int,
                       client: DrivvoClient, drivvo_vehicle_id: int,
                       default_fuel_type: str = "PB95") -> ImportReport:
    """Uzupełniający import tankowań (dedup po (date, odometer) z UNIQUE)."""
    report = ImportReport()
    for r in client.refuellings(drivvo_vehicle_id):
        date = _date_minutes(r.get("data"))
        if not date:
            continue
        volume = _volume(r)
        total = float(r.get("valor_total") or 0)
        price = float(r.get("preco") or 0) or (round(total / volume, 3) if volume else 0)
        station = None
        st = r.get("posto_combustivel")
        if isinstance(st, dict):
            station = st.get("nome")
        report.fillups_in_file += 1
        report.total_cost += total
        report.total_volume += volume
        cur = conn.execute(
            """INSERT OR IGNORE INTO fillups
               (vehicle_id, date, odometer, volume_l, price_per_l, total_cost,
                full_tank, missed_previous, fuel_type, station, source, source_uid)
               VALUES (?,?,?,?,?,?,?,0,?,?,'drivvo_api',?)""",
            (vehicle_id, date, r.get("odometro") or 0, volume, price, total,
             1 if r.get("tanque_cheio") else 0, default_fuel_type, station,
             str(r.get("id") or "") or None),
        )
        if cur.rowcount:
            report.fillups_added += 1
        else:
            report.fillups_skipped += 1
    conn.commit()
    return report


def run_import(conn: sqlite3.Connection, vehicle_id: int, email: str,
               password: str, drivvo_vehicle_id: int = 0,
               default_fuel_type: str = "PB95",
               include_refuellings: bool = False) -> dict:
    """Pełny przebieg importu z Drivvo. Zwraca raport jako dict dla UI."""
    client = DrivvoClient(email, password)
    client.login()
    if not drivvo_vehicle_id:
        vehicles = client.vehicles()
        if not vehicles:
            raise DrivvoError("Brak pojazdów na koncie Drivvo")
        drivvo_vehicle_id = vehicles[0]["id"]
        logger.info("Drivvo: auto-wybrano pojazd id=%s", drivvo_vehicle_id)

    report = import_expenses(conn, vehicle_id, client, drivvo_vehicle_id)
    result = {"expenses": report.as_dict(), "drivvo_vehicle_id": drivvo_vehicle_id}
    if include_refuellings:
        result["fillups"] = import_refuellings(
            conn, vehicle_id, client, drivvo_vehicle_id, default_fuel_type
        ).as_dict()
    return result
