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


def call_service(domain: str, service: str, data: dict,
                 return_response: bool = False,
                 timeout: int = 90) -> dict | None:
    """Wywołuje usługę HA; z return_response zwraca service_response.

    Zwraca dict odpowiedzi (dla return_response klucz 'service_response')
    albo None przy błędzie HTTP/sieci.
    """
    url = f"{_BASE}/services/{domain}/{service}"
    if return_response:
        url += "?return_response"
    try:
        resp = requests.post(url, headers=_headers(), json=data,
                             timeout=timeout)
        if resp.status_code < 400:
            return resp.json()
        logger.warning("HA services/%s/%s -> HTTP %d: %s", domain, service,
                       resp.status_code, resp.text[:300])
    except requests.RequestException as exc:
        logger.warning("HA services/%s/%s nieudane: %s", domain, service, exc)
    return None


def find_config_entry(domain: str) -> str | None:
    """entry_id pierwszego wpisu konfiguracyjnego danej integracji albo None."""
    try:
        resp = requests.get(f"{_BASE}/config/config_entries/entry",
                            headers=_headers(), params={"domain": domain},
                            timeout=10)
        if resp.status_code == 200:
            entries = resp.json()
            if entries:
                return entries[0].get("entry_id")
            return None
        logger.warning("HA config_entries?domain=%s -> HTTP %d", domain,
                       resp.status_code)
    except requests.RequestException as exc:
        logger.warning("HA config_entries niedostępne: %s", exc)
    return None


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
