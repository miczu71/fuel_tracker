"""Regionalne ceny paliw z autocentrum.pl (tabela wojewódzka).

Źródło wybrane empirycznie: autocentrum.pl publikuje bieżące średnie
wojewódzkie w prostej tabeli HTML; e-petrol.pl serwował dane sprzed
miesięcy. Parsujemy regexem (stała struktura: wiersz = województwo +
ceny 95/98/ON/ON+/LPG z przecinkiem dziesiętnym), bez zależności od
lxml/bs4. Wyniki lądują w fuel_prices; retencja 400 dni.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime

import requests

log = logging.getLogger(__name__)

PRICES_URL = "https://www.autocentrum.pl/paliwa/ceny-paliw/"
PRICES_TIMEOUT_S = 15
SOURCE = "autocentrum"
RETENTION_DAYS = 400

# Kolumny tabeli autocentrum → nazwy typów paliwa w add-onie.
_COLUMNS = ["PB95", "PB98", "ON", "ON+", "LPG"]

_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def parse_prices(html: str, region: str) -> dict[str, float]:
    """Ceny {typ_paliwa: PLN/L} dla województwa `region`; {} gdy brak."""
    for row in _ROW_RE.findall(html):
        cells = [_TAG_RE.sub("", c).strip() for c in _CELL_RE.findall(row)]
        if not cells or cells[0].strip().lower() != region.lower():
            continue
        prices: dict[str, float] = {}
        for fuel, raw in zip(_COLUMNS, cells[1:]):
            try:
                prices[fuel] = float(raw.replace(",", "."))
            except ValueError:  # "-" = brak notowania
                continue
        return prices
    return {}


def fetch_region_prices(region: str) -> dict[str, float]:
    """Pobiera i parsuje ceny; {} przy błędzie sieci/zmianie strony."""
    try:
        resp = requests.get(PRICES_URL, timeout=PRICES_TIMEOUT_S,
                            headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except Exception as exc:
        log.warning("Ceny paliw niedostępne (%s): %s", PRICES_URL, exc)
        return {}
    prices = parse_prices(resp.text, region)
    if not prices:
        log.warning("Ceny paliw: brak wiersza '%s' — zmiana strony?", region)
    return prices


def store_prices(conn: sqlite3.Connection, region: str,
                 prices: dict[str, float], now: datetime | None = None) -> None:
    """Zapis do fuel_prices (dzień = jeden wpis na typ paliwa) + retencja."""
    day = (now or datetime.now()).strftime("%Y-%m-%d")
    for fuel, price in prices.items():
        conn.execute(
            "INSERT OR REPLACE INTO fuel_prices"
            " (fetched_at, station, fuel_type, price, source)"
            " VALUES (?, ?, ?, ?, ?)",
            (day, f"region:{region}", fuel, price, SOURCE))
    conn.execute(
        "DELETE FROM fuel_prices WHERE fetched_at <"
        f" date('now', '-{RETENTION_DAYS} days')")
    conn.commit()


def refresh(conn: sqlite3.Connection, region: str) -> dict[str, float]:
    """Job schedulera: pobierz + zapisz; zwraca pobrane ceny."""
    prices = fetch_region_prices(region)
    if prices:
        store_prices(conn, region, prices)
        log.info("Ceny paliw %s: %s", region, prices)
    return prices


def latest_price(conn: sqlite3.Connection, region: str,
                 fuel_type: str) -> dict | None:
    """Ostatnia zapisana cena regionalna: {price, fetched_at} albo None."""
    row = conn.execute(
        "SELECT price, fetched_at FROM fuel_prices"
        " WHERE station = ? AND fuel_type = ? AND source = ?"
        " ORDER BY fetched_at DESC LIMIT 1",
        (f"region:{region}", fuel_type, SOURCE)).fetchone()
    return dict(row) if row else None


def price_series(conn: sqlite3.Connection, region: str,
                 fuel_type: str) -> list[dict]:
    """Seria dzienna ceny regionalnej do wykresu na stronie Statystyki."""
    rows = conn.execute(
        "SELECT fetched_at AS date, price AS value FROM fuel_prices"
        " WHERE station = ? AND fuel_type = ? AND source = ?"
        " ORDER BY fetched_at",
        (f"region:{region}", fuel_type, SOURCE))
    return [dict(r) for r in rows]
