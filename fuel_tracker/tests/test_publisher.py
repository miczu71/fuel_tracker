"""Payloady MQTT discovery i render wartości."""
from unittest.mock import MagicMock

from fuel_tracker import publisher


def test_discovery_payloads_shape():
    payloads = publisher.discovery_payloads("fuel_tracker", "AutoA Fuel", "0.1.0")
    assert len(payloads) == len(publisher._SENSORS)
    topic = "homeassistant/sensor/fuel_tracker/total_cost/config"
    assert topic in payloads
    p = payloads[topic]
    assert p["unique_id"] == "fuel_tracker_total_cost"
    assert p["device"]["name"] == "AutoA Fuel"
    assert p["device"]["identifiers"] == ["fuel_tracker"]
    assert p["device_class"] == "monetary"
    assert p["state_class"] == "total"
    assert p["unit_of_measurement"] == "PLN"
    assert p["state_topic"] == "fuel_tracker/sensors/total_cost/state"
    assert p["availability_topic"] == "fuel_tracker/availability"


def test_device_id_for_vehicle_active_returns_stable_constant_prefix():
    # 0.11.0 decyzja usera: aktywne auto zostaje na dzisiejszym stałym
    # prefiksie — zero migracji entity_id/template.yaml/utility_meter.
    assert publisher.device_id_for_vehicle(1, active_vehicle_id=1) == "fuel_tracker"
    assert publisher.device_id_for_vehicle(7, active_vehicle_id=7) == "fuel_tracker"


def test_device_id_for_vehicle_non_active_gets_suffixed_prefix():
    assert publisher.device_id_for_vehicle(2, active_vehicle_id=1) == "fuel_tracker_2"


def test_discovery_payloads_active_device_id_topics_byte_identical_to_today():
    """Dowód decyzji 1: dla aktywnego auta topiki/unique_id/identifiers są
    dokładnie takie same jak przed 0.11.0 (żadnej migracji istniejących
    encji sensor.<pojazd>_fuel_*)."""
    device_id = publisher.device_id_for_vehicle(1, active_vehicle_id=1)
    payloads = publisher.discovery_payloads(device_id, "AutoA Fuel", "0.11.0")
    topic = "homeassistant/sensor/fuel_tracker/total_cost/config"
    assert topic in payloads
    p = payloads[topic]
    assert p["unique_id"] == "fuel_tracker_total_cost"
    assert p["device"]["identifiers"] == ["fuel_tracker"]
    assert p["state_topic"] == "fuel_tracker/sensors/total_cost/state"


def test_discovery_payloads_other_vehicle_gets_distinct_topics_and_unique_id():
    device_id = publisher.device_id_for_vehicle(2, active_vehicle_id=1)
    payloads = publisher.discovery_payloads(device_id, "AutoB Fuel", "0.11.0")
    topic = "homeassistant/sensor/fuel_tracker_2/total_cost/config"
    assert topic in payloads
    p = payloads[topic]
    assert p["unique_id"] == "fuel_tracker_2_total_cost"
    assert p["device"]["identifiers"] == ["fuel_tracker_2"]
    assert p["state_topic"] == "fuel_tracker/sensors/fuel_tracker_2/total_cost/state"
    # Nie koliduje z topikami aktywnego auta
    assert "homeassistant/sensor/fuel_tracker/total_cost/config" not in payloads


def test_publish_for_vehicle_routes_values_to_correct_device_topic():
    pub = publisher.MQTTPublisher("localhost", 1883, "", "")
    pub._client = MagicMock()
    pub._on_connect(pub._client, None, None, 0)
    pub._client.reset_mock()

    pub.publish_for_vehicle("fuel_tracker", "AutoA Fuel", {"total_cost": 100.0})
    pub.publish_for_vehicle("fuel_tracker_2", "AutoB Fuel", {"total_cost": 50.0})

    args = {c.args[0]: c.args[1] for c in pub._client.publish.call_args_list}
    assert args["fuel_tracker/sensors/total_cost/state"] == "100.0"
    assert args["fuel_tracker/sensors/fuel_tracker_2/total_cost/state"] == "50.0"


def test_publish_wrapper_uses_active_device_id_unchanged():
    """publish() (API 0.10.0 i wcześniej) zostaje cienkim wrapperem —
    zero zmian zachowania dla dzisiejszego jedynego auta."""
    pub = publisher.MQTTPublisher("localhost", 1883, "", "",
                                  device_name="AutoA Fuel")
    pub._client = MagicMock()
    pub._on_connect(pub._client, None, None, 0)
    pub._client.reset_mock()

    pub.publish({"total_cost": 100.0})
    topics = [c.args[0] for c in pub._client.publish.call_args_list]
    assert "fuel_tracker/sensors/total_cost/state" in topics
    assert "fuel_tracker/sensors/fuel_tracker_2/total_cost/state" not in topics


def test_unpublish_device_clears_discovery_topics_with_empty_retained_payload():
    """0.11.1 hotfix: usunięcie/archiwizacja pojazdu musi czyścić jego retained
    discovery, inaczej urządzenie zostaje osierocone w rejestrze HA (znalezione
    podczas weryfikacji produkcyjnej 0.11.0 — sensor.testowe_auto_* przeżyły
    DELETE /api/vehicles/2)."""
    pub = publisher.MQTTPublisher("localhost", 1883, "", "")
    pub._client = MagicMock()
    pub._on_connect(pub._client, None, None, 0)
    pub._client.reset_mock()

    pub.unpublish_device("fuel_tracker_2")

    calls = {c.args[0]: c for c in pub._client.publish.call_args_list}
    topic = "homeassistant/sensor/fuel_tracker_2/total_cost/config"
    assert topic in calls
    assert calls[topic].args[1] == ""
    assert calls[topic].kwargs.get("retain") is True
    # Wszystkie sensory tego urządzenia, nie tylko jeden.
    assert len(calls) == len(publisher._SENSORS)


def test_unpublish_device_forgets_last_values_so_reconnect_does_not_resurrect_it():
    """Bez tego kolejny _on_connect (reconnect MQTT) odtworzyłby usunięte
    urządzenie ze stanu _last_values zapamiętanego sprzed usunięcia."""
    pub = publisher.MQTTPublisher("localhost", 1883, "", "")
    pub._client = MagicMock()
    pub._on_connect(pub._client, None, None, 0)
    pub.publish_for_vehicle("fuel_tracker_2", "AutoB Fuel", {"total_cost": 50.0})

    pub.unpublish_device("fuel_tracker_2")
    pub._client.reset_mock()
    pub._on_connect(pub._client, None, None, 0)

    topics = [c.args[0] for c in pub._client.publish.call_args_list]
    assert not any("fuel_tracker_2" in t for t in topics)


def test_no_monetary_on_per_unit_sensors():
    # PLN/L i PLN/km nie mogą mieć device_class monetary (lekcja z pv_roi).
    for s in publisher._SENSORS:
        if s.unit in ("PLN/L", "PLN/km"):
            assert s.device_class is None, s.slug


def test_monetary_requires_state_class_total():
    # Walidator HA: device_class monetary dopuszcza tylko state_class total.
    for s in publisher._SENSORS:
        if s.device_class == "monetary":
            assert s.state_class == "total", s.slug


def test_publish_before_connect_flushes_in_on_connect():
    """Fix wyścigu 0.5.0: pierwszy tick schedulera wyprzedza connect —
    stan ma czekać i wyjść w on_connect, nie ginąć do kolejnego ticku."""
    pub = publisher.MQTTPublisher("localhost", 1883, "", "")
    pub._client = MagicMock()

    pub.publish({"total_cost": 100.0})  # przed połączeniem
    pub._client.publish.assert_not_called()

    pub._on_connect(pub._client, None, None, 0)
    topics = [c.args[0] for c in pub._client.publish.call_args_list]
    assert "fuel_tracker/sensors/total_cost/state" in topics
    assert "fuel_tracker/availability" in topics

    # Po połączeniu publikacja idzie od razu
    pub._client.reset_mock()
    pub.publish({"total_cost": 200.0})
    args = {c.args[0]: c.args[1] for c in pub._client.publish.call_args_list}
    assert args["fuel_tracker/sensors/total_cost/state"] == "200.0"


def test_render_values():
    out = publisher.render_values({
        "total_cost": 14701.636, "avg_consumption": None,
        "fillup_count": 50, "last_fillup_station": "Stacja A",
    })
    assert out["total_cost"] == "14701.636"
    assert out["avg_consumption"] == "unknown"
    assert out["fillup_count"] == "50"
    assert out["last_fillup_station"] == "Stacja A"
    # Każdy sensor ma payload
    assert set(out) == {s.slug for s in publisher._SENSORS}
