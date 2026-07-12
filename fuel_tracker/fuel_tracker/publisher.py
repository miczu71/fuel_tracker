"""MQTT discovery publisher — urządzenie „Superb Fuel" z sensorami tankowań.

Wzorzec jak w pv_roi_tracker: paho loop_start(), availability topic z LWT,
discovery retained. Uwaga na entity_id: HA składa je z nazwy urządzenia
i nazwy sensora (oczekiwane sensor.superb_fuel_<slug>) — po pierwszej
publikacji realne id trzeba potwierdzić przez /api/states.
"""
from __future__ import annotations

import json
import logging
from typing import NamedTuple, Optional

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)

_DEVICE_ID = "fuel_tracker"
_AVAIL_TOPIC = "fuel_tracker/availability"
_STATE_PREFIX = "fuel_tracker/sensors"
_DISC_PREFIX = "homeassistant"


class _Sensor(NamedTuple):
    slug: str
    name: str
    unit: Optional[str]
    device_class: Optional[str]
    state_class: Optional[str]
    icon: Optional[str]


# Per-litr i per-km NIE dostają device_class 'monetary' — HA wymaga wtedy
# jednostki walutowej (PLN), a 'PLN/L' blokuje statystyki długoterminowe.
# Sensory monetary muszą mieć state_class 'total' — jedyna kombinacja
# dopuszczana przez walidator HA (inne logują "impossible considering device class").
_SENSORS: list[_Sensor] = [
    _Sensor("total_cost",           "Total Cost",           "PLN",      "monetary", "total",            "mdi:cash-multiple"),
    _Sensor("total_volume",         "Total Volume",         "L",        "volume",   "total_increasing", "mdi:fuel"),
    _Sensor("fillup_count",         "Fillup Count",         None,       None,       "total_increasing", "mdi:counter"),
    _Sensor("avg_consumption",      "Avg Consumption",      "L/100km",  None,       "measurement",      "mdi:gas-station"),
    _Sensor("last_consumption",     "Last Consumption",     "L/100km",  None,       "measurement",      "mdi:gas-station-outline"),
    _Sensor("cost_per_km",          "Cost Per Km",          "PLN/km",   None,       "measurement",      "mdi:map-marker-distance"),
    _Sensor("avg_price_per_l",      "Avg Price Per L",      "PLN/L",    None,       "measurement",      "mdi:tag"),
    _Sensor("last_fillup_date",     "Last Fillup Date",     None,       "timestamp", None,              "mdi:calendar-clock"),
    _Sensor("last_fillup_odometer", "Last Fillup Odometer", "km",       "distance", "measurement",      "mdi:counter"),
    _Sensor("last_fillup_price",    "Last Fillup Price",    "PLN/L",    None,       "measurement",      "mdi:tag-outline"),
    _Sensor("last_fillup_volume",   "Last Fillup Volume",   "L",        "volume",   "measurement",      "mdi:fuel"),
    _Sensor("last_fillup_cost",     "Last Fillup Cost",     "PLN",      "monetary", "total",            "mdi:cash"),
    _Sensor("last_fillup_station",  "Last Fillup Station",  None,       None,       None,               "mdi:gas-station"),
    _Sensor("expenses_total",       "Expenses Total",       "PLN",      "monetary", "total",            "mdi:receipt"),
    _Sensor("budget_left_month",    "Budget Left Month",    "PLN",      "monetary", "total",            "mdi:piggy-bank"),
    _Sensor("month_fuel_cost",      "Month Fuel Cost",      "PLN",      "monetary", "total",            "mdi:calendar-today"),
    _Sensor("self_paid_fuel_total", "Self Paid Fuel Total", "PLN",      "monetary", "total",            "mdi:account-cash"),
    # 0.4.0 — ceny regionalne + statystyki
    _Sensor("region_fuel_price",    "Region Fuel Price",    "PLN/L",    None,       "measurement",      "mdi:gas-station-in-use"),
    _Sensor("price_vs_region",      "Price Vs Region",      "PLN/L",    None,       "measurement",      "mdi:scale-balance"),
    _Sensor("estimated_range_km",   "Estimated Range",      "km",       "distance", "measurement",      "mdi:map-marker-path"),
    _Sensor("month_forecast_cost",  "Month Forecast Cost",  "PLN",      "monetary", "total",            "mdi:chart-timeline-variant"),
    _Sensor("ytd_fuel_cost",        "YTD Fuel Cost",        "PLN",      "monetary", "total",            "mdi:calendar-range"),
    _Sensor("projected_annual_km",  "Projected Annual Km",  "km",       "distance", "measurement",      "mdi:speedometer"),
    _Sensor("best_station",         "Best Station",         None,       None,       None,               "mdi:trophy"),
    # 0.8.0 — leasing per auto (aktywny pojazd)
    _Sensor("lease_km_margin",      "Lease Km Margin",      "km",       "distance", "measurement",      "mdi:car-clock"),
    _Sensor("lease_depletion_date", "Lease Depletion Date", None,       "date",     None,               "mdi:calendar-alert"),
]


def device_id_for_vehicle(vehicle_id: int, active_vehicle_id: int) -> str:
    """0.11.0 decyzja usera: aktywne auto zostaje na dzisiejszym stałym
    device_id ("fuel_tracker") — zero migracji entity_id istniejących
    sensorów. Każde INNE auto dostaje własny, odrębny prefiks."""
    if vehicle_id == active_vehicle_id:
        return _DEVICE_ID
    return f"{_DEVICE_ID}_{vehicle_id}"


def _state_topic(device_id: str, slug: str) -> str:
    # Aktywne auto (device_id == _DEVICE_ID) zostaje na dzisiejszym gołym
    # topiku bez segmentu device_id — dowód zero-migracji w testach.
    if device_id == _DEVICE_ID:
        return f"{_STATE_PREFIX}/{slug}/state"
    return f"{_STATE_PREFIX}/{device_id}/{slug}/state"


def _disc_topic(device_id: str, slug: str) -> str:
    return f"{_DISC_PREFIX}/sensor/{device_id}/{slug}/config"


def render_values(values: dict) -> dict[str, str]:
    """Mapa slug → payload; brakujące wartości publikujemy jako 'unknown'."""
    out: dict[str, str] = {}
    for s in _SENSORS:
        v = values.get(s.slug)
        if v is None:
            out[s.slug] = "unknown"
        elif isinstance(v, float):
            out[s.slug] = str(round(v, 4))
        else:
            out[s.slug] = str(v)
    return out


def discovery_payloads(device_id: str, device_name: str,
                       version: str) -> dict[str, dict]:
    """Mapa topic → payload discovery (wydzielone dla testów)."""
    device = {
        "identifiers": [device_id],
        "name": device_name,
        "manufacturer": "Custom",
        "model": "fuel_tracker",
        "sw_version": version,
    }
    payloads: dict[str, dict] = {}
    for s in _SENSORS:
        p: dict = {
            "name": s.name,
            "unique_id": f"{device_id}_{s.slug}",
            "state_topic": _state_topic(device_id, s.slug),
            "availability_topic": _AVAIL_TOPIC,
            "device": device,
        }
        if s.unit:
            p["unit_of_measurement"] = s.unit
        if s.device_class:
            p["device_class"] = s.device_class
        if s.state_class:
            p["state_class"] = s.state_class
        if s.icon:
            p["icon"] = s.icon
        payloads[_disc_topic(device_id, s.slug)] = p
    return payloads


class MQTTPublisher:
    def __init__(self, host: str, port: int, user: str, password: str,
                 device_name: str = "Superb Fuel", version: str = "0.0.0") -> None:
        self._host = host
        self._port = port
        # device_name/_DEVICE_ID zostają jako domyślne urządzenie dla publish()
        # — cienki wrapper zgodności dla jedynego/aktywnego auta (0.10.0 i
        # wcześniej). Multi-vehicle (0.11.0) idzie przez publish_for_vehicle().
        self._device_name = device_name
        self._version = version
        self._connected = False
        # Ostatni znany stan per urządzenie (device_id -> values) — pierwsza
        # publikacja po starcie zwykle wyprzedza connect (scheduler odpala
        # tick natychmiast), więc stan czeka tu i wychodzi w _on_connect. Bez
        # tego nowe sensory wisiały jako "unknown" do kolejnego ticku (15 min).
        self._last_values: dict[str, dict] = {}
        self._device_names: dict[str, str] = {}

        self._client = mqtt.Client(client_id=_DEVICE_ID, clean_session=True)
        if user:
            self._client.username_pw_set(user, password)
        self._client.will_set(_AVAIL_TOPIC, "offline", retain=True)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

    def connect(self) -> None:
        self._client.connect_async(self._host, self._port, keepalive=60)
        self._client.loop_start()

    def disconnect(self) -> None:
        self._client.publish(_AVAIL_TOPIC, "offline", retain=True)
        self._client.loop_stop()
        self._client.disconnect()

    def _on_connect(self, client, userdata, flags, rc) -> None:
        if rc == 0:
            self._connected = True
            logger.info("MQTT połączone z %s:%d", self._host, self._port)
            client.publish(_AVAIL_TOPIC, "online", retain=True)
            for device_id, values in self._last_values.items():
                self._publish_discovery(device_id, self._device_names[device_id])
                self._publish_values(device_id, values)
            if self._last_values:
                logger.info(
                    "MQTT: opublikowano discovery + stan zaległy sprzed "
                    "połączenia (%d urządzeń)", len(self._last_values))
        else:
            logger.error("MQTT connect nieudany (rc=%d)", rc)

    def _on_disconnect(self, client, userdata, rc) -> None:
        self._connected = False
        if rc != 0:
            logger.warning("MQTT rozłączone (rc=%d) — paho wznowi połączenie", rc)

    def publish(self, values: dict) -> None:
        """Wrapper zgodności — publikuje dla jedynego/aktywnego auta na
        dzisiejszym stałym device_id, bez zmiany zachowania sprzed 0.11.0."""
        self.publish_for_vehicle(_DEVICE_ID, self._device_name, values)

    def publish_for_vehicle(self, device_id: str, device_name: str,
                            values: dict) -> None:
        self._device_names[device_id] = device_name
        self._last_values[device_id] = values
        if not self._connected:
            logger.debug("MQTT niepołączone — stan %s zapamiętany do on_connect",
                        device_id)
            return
        self._publish_discovery(device_id, device_name)
        self._publish_values(device_id, values)

    def _publish_discovery(self, device_id: str, device_name: str) -> None:
        for topic, payload in discovery_payloads(
            device_id, device_name, self._version
        ).items():
            self._client.publish(topic, json.dumps(payload), retain=True)

    def _publish_values(self, device_id: str, values: dict) -> None:
        for slug, payload in render_values(values).items():
            self._client.publish(
                _state_topic(device_id, slug), payload, retain=True)
        logger.debug("Opublikowano stan sensorów MQTT (%s)", device_id)
