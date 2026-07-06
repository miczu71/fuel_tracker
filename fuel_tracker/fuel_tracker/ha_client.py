"""Dostęp do Home Assistant przez Supervisor API (SUPERVISOR_TOKEN)."""
from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

_BASE = "http://supervisor/core/api"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ.get('SUPERVISOR_TOKEN', '')}",
        "Content-Type": "application/json",
    }


def get_state(entity_id: str) -> dict | None:
    """Stan encji HA albo None (brak encji / brak połączenia)."""
    if not entity_id:
        return None
    try:
        resp = requests.get(f"{_BASE}/states/{entity_id}", headers=_headers(),
                            timeout=10)
        if resp.status_code == 200:
            return resp.json()
        logger.warning("HA states/%s -> HTTP %d", entity_id, resp.status_code)
    except requests.RequestException as exc:
        logger.warning("HA API niedostępne: %s", exc)
    return None


def get_numeric_state(entity_id: str) -> float | None:
    data = get_state(entity_id)
    if not data:
        return None
    try:
        return float(data["state"])
    except (KeyError, TypeError, ValueError):
        return None


def get_location(entity_id: str) -> tuple[float, float] | None:
    """(lat, lon) z atrybutów encji person/device_tracker albo None."""
    data = get_state(entity_id)
    if not data:
        return None
    attrs = data.get("attributes", {})
    try:
        return float(attrs["latitude"]), float(attrs["longitude"])
    except (KeyError, TypeError, ValueError):
        return None


def notify(service: str, title: str, message: str) -> bool:
    """Wysyła powiadomienie, np. service='notify/family'."""
    try:
        resp = requests.post(
            f"{_BASE}/services/{service}", headers=_headers(),
            json={"title": title, "message": message}, timeout=10,
        )
        return resp.status_code < 400
    except requests.RequestException as exc:
        logger.warning("Powiadomienie nieudane: %s", exc)
        return False


def get_mqtt_service() -> dict | None:
    """Dane brokera MQTT z usługi Supervisora (wymaga services: mqtt:need).

    Zwraca dict z kluczami host/port/username/password/ssl albo None.
    """
    try:
        resp = requests.get("http://supervisor/services/mqtt",
                            headers=_headers(), timeout=10)
        if resp.status_code == 200:
            return resp.json().get("data")
        logger.warning("Supervisor services/mqtt -> HTTP %d", resp.status_code)
    except requests.RequestException as exc:
        logger.warning("Supervisor services/mqtt niedostępne: %s", exc)
    return None
