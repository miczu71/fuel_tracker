"""Payloady MQTT discovery i render wartości."""
from fuel_tracker import publisher


def test_discovery_payloads_shape():
    payloads = publisher.discovery_payloads("Superb Fuel", "0.1.0")
    assert len(payloads) == len(publisher._SENSORS)
    topic = "homeassistant/sensor/fuel_tracker/total_cost/config"
    assert topic in payloads
    p = payloads[topic]
    assert p["unique_id"] == "fuel_tracker_total_cost"
    assert p["device"]["name"] == "Superb Fuel"
    assert p["device"]["identifiers"] == ["fuel_tracker"]
    assert p["device_class"] == "monetary"
    assert p["state_class"] == "total"
    assert p["unit_of_measurement"] == "PLN"
    assert p["state_topic"] == "fuel_tracker/sensors/total_cost/state"
    assert p["availability_topic"] == "fuel_tracker/availability"


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


def test_render_values():
    out = publisher.render_values({
        "total_cost": 14701.636, "avg_consumption": None,
        "fillup_count": 50, "last_fillup_station": "Wrocław",
    })
    assert out["total_cost"] == "14701.636"
    assert out["avg_consumption"] == "unknown"
    assert out["fillup_count"] == "50"
    assert out["last_fillup_station"] == "Wrocław"
    # Każdy sensor ma payload
    assert set(out) == {s.slug for s in publisher._SENSORS}
