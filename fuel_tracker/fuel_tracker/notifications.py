"""Powiadomienia w add-onie (0.9.0) — zastępują automatyzacje HA z pakietu.

evaluate() dostaje ten sam dict wartości, który idzie do publishera MQTT,
więc alerty i sensory nigdy się nie rozjeżdżają. Dedup: powiadomienie tylko
przy wzroście severity stanu (ok < warning/cheap < exceeded), z oknem
anty-flap 24 h per (alert, stan) — zastępuje dawne `for: 1h/6h` z YAML.
Powrót do ok i de-eskalacja są ciche. Stan w tabeli alert_state (v7).
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Callable

from . import settings as settingsm

logger = logging.getLogger(__name__)

# publish_sensors bywa wołane równolegle (joby schedulera + on_data_change
# z wątków Flaska) — bez blokady dwa evaluate czytają stary stan zanim
# którykolwiek zapisze nowy i alert wychodzi podwójnie (patrz start 0.9.0).
_LOCK = threading.Lock()

_SEVERITY = {"ok": 0, "warning": 1, "cheap": 1, "exceeded": 2}
_ANTI_FLAP = timedelta(hours=24)
_TS = "%Y-%m-%dT%H:%M:%S"


def _fmt(value, digits: int = 0) -> str:
    if value is None:
        return "–"
    return f"{value:.{digits}f}"


def _budget_state(settings: dict, values: dict) -> str | None:
    if not settings.get("alert_budget_enabled"):
        return None
    if not settings.get("monthly_fuel_budget"):
        return None
    left = values.get("budget_left_month")
    if left is None:
        return None
    if left < 0:
        return "exceeded"
    if left < settings.get("alert_budget_threshold", 0):
        return "warning"
    return "ok"


def _cheap_fuel_state(settings: dict, values: dict) -> str | None:
    if not settings.get("alert_cheap_fuel_enabled"):
        return None
    # price_vs_region = cena mojego ostatniego tankowania - cena regionalna;
    # dodatnia oznacza, że w regionie jest taniej.
    diff = values.get("price_vs_region")
    if diff is None:
        return None
    if diff >= settings.get("alert_cheap_fuel_delta", 0):
        return "cheap"
    return "ok"


def _lease_state(settings: dict, values: dict) -> str | None:
    if not settings.get("alert_lease_enabled"):
        return None
    margin = values.get("lease_km_margin")
    if margin is None:
        return None
    if margin < 0:
        return "exceeded"
    if margin < settings.get("alert_lease_km_threshold", 0):
        return "warning"
    return "ok"


def _messages(alert: str, state: str, values: dict) -> tuple[str, str]:
    left = values.get("budget_left_month")
    forecast = _fmt(values.get("month_forecast_cost"))
    annual = _fmt(values.get("projected_annual_km"))
    margin = values.get("lease_km_margin")
    texts = {
        ("budget", "warning"): (
            "⛽ Budżet paliwowy na wyczerpaniu",
            f"Zostało {_fmt(left, 2)} PLN budżetu paliwowego na ten miesiąc. "
            f"Prognoza na cały miesiąc: {forecast} PLN."),
        ("budget", "exceeded"): (
            "⛽ Budżet paliwowy przekroczony",
            f"Budżet miesiąca przekroczony o "
            f"{_fmt(abs(left) if left is not None else None, 2)} PLN. "
            f"Prognoza na cały miesiąc: {forecast} PLN."),
        ("cheap_fuel", "cheap"): (
            "⛽ Tanie paliwo w regionie",
            f"Cena regionalna {_fmt(values.get('region_fuel_price'), 2)} PLN/L "
            f"— o {_fmt(values.get('price_vs_region'), 2)} PLN/L taniej niż "
            f"Twoje ostatnie tankowanie "
            f"({_fmt(values.get('last_fillup_price'), 2)} PLN/L)."),
        ("lease", "warning"): (
            "🚗 Zapas km leasingu topnieje",
            f"Zapas względem krzywej limitu leasingu: {_fmt(margin)} km. "
            f"Tempo roczne: {annual} km/rok."),
        ("lease", "exceeded"): (
            "🚗 Limit km leasingu przekroczony",
            f"Przebieg wyprzedza krzywą limitu leasingu o "
            f"{_fmt(abs(margin) if margin is not None else None)} km. "
            f"Tempo roczne: {annual} km/rok."),
    }
    return texts[(alert, state)]


def _load_state(conn: sqlite3.Connection, alert: str) -> dict:
    row = conn.execute(
        "SELECT state, notified_state, notified_at FROM alert_state "
        "WHERE alert = ?", (alert,)).fetchone()
    if row:
        return dict(row)
    return {"state": "ok", "notified_state": None, "notified_at": None}


def _save_state(conn: sqlite3.Connection, alert: str, state: str,
                now: datetime, notified: bool) -> None:
    conn.execute(
        "INSERT INTO alert_state (alert, state, changed_at) VALUES (?, ?, ?) "
        "ON CONFLICT(alert) DO UPDATE SET state = excluded.state, "
        "changed_at = excluded.changed_at",
        (alert, state, now.strftime(_TS)))
    if notified:
        conn.execute(
            "UPDATE alert_state SET notified_state = ?, notified_at = ? "
            "WHERE alert = ?", (state, now.strftime(_TS), alert))
    conn.commit()


def _recently_notified(prev: dict, state: str, now: datetime) -> bool:
    if prev.get("notified_state") != state or not prev.get("notified_at"):
        return False
    try:
        notified_at = datetime.strptime(prev["notified_at"], _TS)
    except ValueError:
        return False
    return now - notified_at < _ANTI_FLAP


def evaluate(conn: sqlite3.Connection, settings: dict, values: dict,
             notify: Callable[[str, str, str], bool],
             now: datetime | None = None) -> None:
    """Przelicza stany alertów i wysyła powiadomienia przez usługę HA."""
    with _LOCK:
        _evaluate(conn, settings, values, notify, now)


def _evaluate(conn: sqlite3.Connection, settings: dict, values: dict,
              notify: Callable[[str, str, str], bool],
              now: datetime | None = None) -> None:
    now = now or datetime.now()
    service = settingsm.normalize_notify_service(
        settings.get("notify_service", ""))
    checks = {
        "budget": _budget_state,
        "cheap_fuel": _cheap_fuel_state,
        "lease": _lease_state,
    }
    for alert, check in checks.items():
        state = check(settings, values)
        if state is None:  # alert wyłączony albo brak danych wejściowych
            continue
        prev = _load_state(conn, alert)
        if state == prev["state"]:
            continue
        escalated = _SEVERITY[state] > _SEVERITY[prev["state"]]
        should_notify = (escalated and state != "ok"
                         and not _recently_notified(prev, state, now))
        sent = False
        if should_notify and service:
            title, message = _messages(alert, state, values)
            # ha_client.notify oczekuje formatu ścieżkowego notify/x
            sent = notify(service.replace(".", "/", 1), title, message)
            logger.info("Alert %s: %s -> %s, powiadomienie %s",
                        alert, prev["state"], state,
                        "wysłane" if sent else "nieudane")
            if not sent:
                # Nie utrwalaj stanu — następny tick (15 min) ponowi próbę.
                continue
        elif should_notify:
            logger.info("Alert %s: %s -> %s, brak usługi notify — pomijam",
                        alert, prev["state"], state)
        else:
            logger.debug("Alert %s: %s -> %s bez powiadomienia", alert,
                         prev["state"], state)
        _save_state(conn, alert, state, now, notified=sent)
