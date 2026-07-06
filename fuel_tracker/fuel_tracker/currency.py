"""Kursy walut NBP (tabela A) dla tankowań za granicą.

Kurs tankowania = ostatni kurs średni NBP opublikowany przed datą (lub w dacie)
tankowania — NBP nie publikuje kursów w weekendy i święta, więc pytamy
o przedział [data-10 dni, data] i bierzemy ostatni wpis. Wynik trafia do
cache w SQLite (exchange_rates), żeby edycja wpisu nie biła w API.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta

import requests

log = logging.getLogger(__name__)

NBP_URL = "https://api.nbp.pl/api/exchangerates/rates/a/{code}/{start}/{end}/"
NBP_TIMEOUT_S = 6

# Waluty w formularzu — sąsiedzi + typowe kierunki; NBP tabela A ma wszystkie.
CURRENCIES = ["PLN", "EUR", "CZK", "HUF", "DKK", "SEK", "NOK", "CHF", "GBP", "USD"]


def get_rate(conn: sqlite3.Connection, code: str,
             on_date: str) -> dict | None:
    """Kurs PLN za 1 jednostkę waluty na dzień on_date ('YYYY-MM-DD').

    Zwraca {"rate": float, "effective_date": str} albo None (brak sieci
    i cache) — wtedy formularz wymaga ręcznego kursu.
    """
    code = code.upper()
    if code == "PLN":
        return {"rate": 1.0, "effective_date": on_date}
    cached = conn.execute(
        "SELECT rate, effective_date FROM exchange_rates"
        " WHERE code = ? AND req_date = ?", (code, on_date)).fetchone()
    if cached:
        return {"rate": cached["rate"], "effective_date": cached["effective_date"]}

    fetched = _fetch_nbp(code, on_date)
    if fetched is None:
        # Awaryjnie ostatni znany kurs z cache (lepszy stary niż żaden).
        stale = conn.execute(
            "SELECT rate, effective_date FROM exchange_rates WHERE code = ?"
            " ORDER BY effective_date DESC LIMIT 1", (code,)).fetchone()
        if stale:
            log.warning("NBP niedostępne — używam kursu %s z %s",
                        code, stale["effective_date"])
            return {"rate": stale["rate"],
                    "effective_date": stale["effective_date"]}
        return None

    conn.execute(
        "INSERT OR REPLACE INTO exchange_rates"
        " (code, req_date, rate, effective_date) VALUES (?, ?, ?, ?)",
        (code, on_date, fetched["rate"], fetched["effective_date"]))
    conn.commit()
    return fetched


def _fetch_nbp(code: str, on_date: str) -> dict | None:
    try:
        end = date.fromisoformat(on_date)
    except ValueError:
        return None
    # Tankowanie z przyszłą datą (strefy czasowe) → kurs na dziś.
    end = min(end, date.today())
    start = end - timedelta(days=10)
    url = NBP_URL.format(code=code.lower(), start=start.isoformat(),
                         end=end.isoformat())
    try:
        resp = requests.get(url, params={"format": "json"},
                            timeout=NBP_TIMEOUT_S)
        resp.raise_for_status()
        rates = resp.json().get("rates", [])
    except Exception as exc:  # sieć/404 — obsłużone przez cache u wołającego
        log.warning("NBP: brak kursu %s na %s (%s)", code, on_date, exc)
        return None
    if not rates:
        return None
    last = rates[-1]
    return {"rate": float(last["mid"]), "effective_date": last["effectiveDate"]}
