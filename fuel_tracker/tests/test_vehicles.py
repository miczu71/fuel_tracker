"""Cykl życia pojazdów (0.8.0): wiele aut, archiwizacja, aktywny pojazd."""
from fuel_tracker import db as dbm
from fuel_tracker import settings as settingsm


def test_new_vehicle_has_lease_columns_with_defaults(conn, vehicle_id):
    v = dbm.get_vehicle(conn, vehicle_id)
    assert v["archived"] == 0
    assert v["lease_start"] is None
    assert v["lease_end"] is None
    assert v["lease_km_limit"] is None
    assert v["monthly_rate"] is None


def test_active_vehicle_id_setting_defaults_to_zero(conn):
    assert settingsm.get_settings(conn)["active_vehicle_id"] == 0


def test_create_vehicle_adds_new_row(conn, vehicle_id):
    new_id = dbm.create_vehicle(conn, "Mazda", 45.0, "PB95")
    assert new_id != vehicle_id
    v = dbm.get_vehicle(conn, new_id)
    assert v["name"] == "Mazda"
    assert v["tank_capacity_l"] == 45.0


def test_list_vehicles_excludes_archived_by_default(conn, vehicle_id):
    archived_id = dbm.create_vehicle(conn, "Stary", 50.0, "PB95")
    dbm.archive_vehicle(conn, archived_id)
    ids = [v["id"] for v in dbm.list_vehicles(conn)]
    assert vehicle_id in ids
    assert archived_id not in ids


def test_list_vehicles_include_archived_true_returns_all(conn, vehicle_id):
    archived_id = dbm.create_vehicle(conn, "Stary", 50.0, "PB95")
    dbm.archive_vehicle(conn, archived_id)
    ids = [v["id"] for v in dbm.list_vehicles(conn, include_archived=True)]
    assert archived_id in ids


def test_resolve_active_vehicle_id_falls_back_when_unset(conn, vehicle_id):
    assert dbm.resolve_active_vehicle_id(conn, 0) == vehicle_id


def test_resolve_active_vehicle_id_uses_configured_when_valid(conn, vehicle_id):
    other_id = dbm.create_vehicle(conn, "Mazda", 45.0, "PB95")
    assert dbm.resolve_active_vehicle_id(conn, other_id) == other_id


def test_resolve_active_vehicle_id_falls_back_when_configured_archived(conn, vehicle_id):
    other_id = dbm.create_vehicle(conn, "Mazda", 45.0, "PB95")
    dbm.archive_vehicle(conn, other_id)
    assert dbm.resolve_active_vehicle_id(conn, other_id) == vehicle_id


def test_resolve_active_vehicle_id_falls_back_when_configured_missing(conn, vehicle_id):
    assert dbm.resolve_active_vehicle_id(conn, 9999) == vehicle_id


def test_archive_vehicle_refuses_to_archive_last_vehicle(conn, vehicle_id):
    ok = dbm.archive_vehicle(conn, vehicle_id)
    assert ok is False
    assert dbm.get_vehicle(conn, vehicle_id)["archived"] == 0


def test_archive_vehicle_succeeds_when_another_remains(conn, vehicle_id):
    dbm.create_vehicle(conn, "Mazda", 45.0, "PB95")
    ok = dbm.archive_vehicle(conn, vehicle_id)
    assert ok is True
    assert dbm.get_vehicle(conn, vehicle_id)["archived"] == 1


def test_unarchive_vehicle_clears_flag(conn, vehicle_id):
    dbm.create_vehicle(conn, "Mazda", 45.0, "PB95")
    dbm.archive_vehicle(conn, vehicle_id)
    assert dbm.unarchive_vehicle(conn, vehicle_id) is True
    assert dbm.get_vehicle(conn, vehicle_id)["archived"] == 0


def test_can_delete_vehicle_false_when_last_vehicle(conn, vehicle_id):
    ok, reason = dbm.can_delete_vehicle(conn, vehicle_id)
    assert ok is False
    assert reason


def test_can_delete_vehicle_false_when_has_fillup_history(conn, vehicle_id):
    other_id = dbm.create_vehicle(conn, "Mazda", 45.0, "PB95")
    conn.execute(
        "INSERT INTO fillups (vehicle_id, date, odometer, volume_l, "
        "price_per_l, total_cost) VALUES (?, '2026-01-01 10:00', 100, 40, 6.0, 240)",
        (other_id,))
    conn.commit()
    ok, reason = dbm.can_delete_vehicle(conn, other_id)
    assert ok is False
    assert reason


def test_delete_vehicle_removes_row_when_allowed(conn, vehicle_id):
    other_id = dbm.create_vehicle(conn, "Mazda", 45.0, "PB95")
    ok, reason = dbm.delete_vehicle(conn, other_id)
    assert ok is True
    assert reason is None
    assert dbm.get_vehicle(conn, other_id) is None


def test_delete_vehicle_refuses_with_history(conn, vehicle_id):
    other_id = dbm.create_vehicle(conn, "Mazda", 45.0, "PB95")
    conn.execute(
        "INSERT INTO expenses (vehicle_id, date, cost) VALUES (?, '2026-01-01', 50)",
        (other_id,))
    conn.commit()
    ok, reason = dbm.delete_vehicle(conn, other_id)
    assert ok is False
    assert dbm.get_vehicle(conn, other_id) is not None
