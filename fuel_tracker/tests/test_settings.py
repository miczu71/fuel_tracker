"""Ustawienia edytowalne w UI (0.7.0): typed KV store + seed z opcji Supervisora."""
from fuel_tracker import db as dbm
from fuel_tracker import settings as st


def test_get_settings_returns_defaults_when_empty(conn):
    result = st.get_settings(conn)
    assert result["default_currency"] == "PLN"
    assert result["price_region"] == ""


def test_ha_entities_and_budget_moved_to_vehicles_not_settings(conn):
    """0.11.0: encje HA i budżet są teraz per pojazd (tabela vehicles),
    nie globalne w settings — dwa auta nie mogą już dzielić jednego odometru."""
    for key in ("odometer_entity", "fuel_level_entity", "location_entity",
               "monthly_fuel_budget"):
        assert key not in st.SETTINGS_TYPES
        assert key not in st.DEFAULTS


def test_seed_from_options_populates_missing_keys(conn):
    st.seed_from_options(conn, {
        "alert_budget_threshold": 150.0,
        "price_region": "pomorskie",
    })
    result = st.get_settings(conn)
    assert result["alert_budget_threshold"] == 150.0
    assert result["price_region"] == "pomorskie"


def test_seed_does_not_overwrite_existing_value(conn):
    st.set_settings(conn, {"price_region": "śląskie"})
    st.seed_from_options(conn, {"price_region": "pomorskie"})
    assert st.get_settings(conn)["price_region"] == "śląskie"


def test_set_settings_updates_value_on_repeated_calls(conn):
    st.set_settings(conn, {"price_region": "śląskie"})
    st.set_settings(conn, {"price_region": "pomorskie"})
    assert st.get_settings(conn)["price_region"] == "pomorskie"


def test_set_settings_ignores_unknown_keys(conn):
    st.set_settings(conn, {"bogus_key": "x"})
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM settings WHERE key = 'bogus_key'").fetchone()
    assert row["n"] == 0


def test_get_vehicle_returns_row_created_at_startup(conn, vehicle_id):
    v = dbm.get_vehicle(conn, vehicle_id)
    assert v["name"] == "Testowy"
    assert v["tank_capacity_l"] == 66.0
    assert v["fuel_type"] == "PB95"


def test_update_vehicle_persists_allowed_fields(conn, vehicle_id):
    ok = dbm.update_vehicle(conn, vehicle_id, {
        "name": "Testowe Kombi", "tank_capacity_l": 50.0,
        "fuel_type": "PB98", "drivvo_vehicle_id": 999,
    })
    assert ok is True
    v = dbm.get_vehicle(conn, vehicle_id)
    assert v["name"] == "Testowe Kombi"
    assert v["tank_capacity_l"] == 50.0
    assert v["fuel_type"] == "PB98"


def test_update_vehicle_no_allowed_fields_returns_false(conn, vehicle_id):
    assert dbm.update_vehicle(conn, vehicle_id, {"drivvo_vehicle_id": 5}) is False
