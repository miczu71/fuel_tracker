"""Ustawienia edytowalne w UI (0.7.0): typed KV store + seed z opcji Supervisora."""
from fuel_tracker import db as dbm
from fuel_tracker import settings as st


def test_get_settings_returns_defaults_when_empty(conn):
    result = st.get_settings(conn)
    assert result["monthly_fuel_budget"] == 0.0
    assert result["default_currency"] == "PLN"
    assert result["price_region"] == ""
    assert result["odometer_entity"] == ""


def test_seed_from_options_populates_missing_keys(conn):
    st.seed_from_options(conn, {
        "monthly_fuel_budget": 984.0,
        "price_region": "dolnośląskie",
    })
    result = st.get_settings(conn)
    assert result["monthly_fuel_budget"] == 984.0
    assert result["price_region"] == "dolnośląskie"


def test_seed_does_not_overwrite_existing_value(conn):
    st.set_settings(conn, {"monthly_fuel_budget": 500.0})
    st.seed_from_options(conn, {"monthly_fuel_budget": 984.0})
    assert st.get_settings(conn)["monthly_fuel_budget"] == 500.0


def test_set_settings_updates_value_on_repeated_calls(conn):
    st.set_settings(conn, {"price_region": "śląskie"})
    st.set_settings(conn, {"price_region": "dolnośląskie"})
    assert st.get_settings(conn)["price_region"] == "dolnośląskie"


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
        "name": "Skoda Superb Combi", "tank_capacity_l": 50.0,
        "fuel_type": "PB98", "drivvo_vehicle_id": 999,
    })
    assert ok is True
    v = dbm.get_vehicle(conn, vehicle_id)
    assert v["name"] == "Skoda Superb Combi"
    assert v["tank_capacity_l"] == 50.0
    assert v["fuel_type"] == "PB98"


def test_update_vehicle_no_allowed_fields_returns_false(conn, vehicle_id):
    assert dbm.update_vehicle(conn, vehicle_id, {"drivvo_vehicle_id": 5}) is False
