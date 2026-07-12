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


def test_create_vehicle_with_lease_kwargs(conn, vehicle_id):
    new_id = dbm.create_vehicle(
        conn, "Mazda", 45.0, "PB95", lease_start="2025-01-01",
        lease_end="2028-12-31", lease_km_limit=90000, monthly_rate=1850.0)
    v = dbm.get_vehicle(conn, new_id)
    assert v["lease_start"] == "2025-01-01"
    assert v["lease_end"] == "2028-12-31"
    assert v["lease_km_limit"] == 90000
    assert v["monthly_rate"] == 1850.0


# ── 0.11.0: pełny multi-vehicle — encje HA + budżet per pojazd (migracja #9) ──

def test_new_vehicle_has_ha_entity_and_budget_columns_with_defaults(conn, vehicle_id):
    v = dbm.get_vehicle(conn, vehicle_id)
    assert v["odometer_entity"] is None
    assert v["fuel_level_entity"] is None
    assert v["location_entity"] is None
    assert v["monthly_fuel_budget"] == 0


def test_create_vehicle_with_ha_entity_and_budget_kwargs(conn, vehicle_id):
    new_id = dbm.create_vehicle(
        conn, "Mazda", 45.0, "PB95",
        odometer_entity="sensor.mazda_odo",
        fuel_level_entity="sensor.mazda_fuel",
        location_entity="device_tracker.mazda",
        monthly_fuel_budget=500.0)
    v = dbm.get_vehicle(conn, new_id)
    assert v["odometer_entity"] == "sensor.mazda_odo"
    assert v["fuel_level_entity"] == "sensor.mazda_fuel"
    assert v["location_entity"] == "device_tracker.mazda"
    assert v["monthly_fuel_budget"] == 500.0


def test_update_vehicle_accepts_ha_entity_and_budget_fields(conn, vehicle_id):
    dbm.update_vehicle(conn, vehicle_id, {
        "odometer_entity": "sensor.new_odo", "monthly_fuel_budget": 700.0})
    v = dbm.get_vehicle(conn, vehicle_id)
    assert v["odometer_entity"] == "sensor.new_odo"
    assert v["monthly_fuel_budget"] == 700.0


def test_ensure_vehicle_seeds_ha_entities_and_budget_on_fresh_install(tmp_path):
    """Świeża instalacja: ensure_vehicle tworzy jedyny wiersz — musi przyjąć
    startowe wartości z opcji Supervisora, bo migracja #9 (backfill z
    settings) dotyczy tylko upgrade'u istniejącej bazy."""
    from fuel_tracker import db as freshdbm
    c = freshdbm.get_conn(str(tmp_path / "fresh.db"))
    freshdbm.migrate(c)
    vid = freshdbm.ensure_vehicle(
        c, "Testowe Auto", 66.0, "PB95",
        odometer_entity="sensor.testowe_auto_mileage",
        fuel_level_entity="sensor.testowe_auto_fuel_level",
        location_entity="device_tracker.telefon",
        monthly_fuel_budget=800.0)
    v = freshdbm.get_vehicle(c, vid)
    assert v["odometer_entity"] == "sensor.testowe_auto_mileage"
    assert v["fuel_level_entity"] == "sensor.testowe_auto_fuel_level"
    assert v["location_entity"] == "device_tracker.telefon"
    assert v["monthly_fuel_budget"] == 800.0
    c.close()


def _migrate_to(conn, version):
    """Symulacja upgrade'u ze starszego schematu (jak w test_notifications.py)."""
    for script in dbm._MIGRATIONS[:version]:
        conn.executescript(script)
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()


def test_migration_v9_backfills_ha_entities_and_budget_from_global_settings(tmp_path):
    c = dbm.get_conn(str(tmp_path / "m9.db"))
    _migrate_to(c, 8)
    c.execute("INSERT INTO vehicles (name, tank_capacity_l, fuel_type) "
             "VALUES ('AutoA', 66, 'PB95')")
    c.execute("INSERT INTO vehicles (name, tank_capacity_l, fuel_type) "
             "VALUES ('AutoB', 50, 'PB95')")
    c.execute("INSERT INTO settings (key, value) VALUES "
             "('odometer_entity', 'sensor.auto_a_mileage')")
    c.execute("INSERT INTO settings (key, value) VALUES "
             "('fuel_level_entity', 'sensor.auto_a_fuel_level')")
    c.execute("INSERT INTO settings (key, value) VALUES "
             "('location_entity', 'device_tracker.telefon')")
    c.execute("INSERT INTO settings (key, value) VALUES "
             "('monthly_fuel_budget', '800.0')")
    c.commit()
    dbm.migrate(c)
    rows = c.execute(
        "SELECT name, odometer_entity, fuel_level_entity, location_entity, "
        "monthly_fuel_budget FROM vehicles ORDER BY id").fetchall()
    for row in rows:
        assert row["odometer_entity"] == "sensor.auto_a_mileage"
        assert row["fuel_level_entity"] == "sensor.auto_a_fuel_level"
        assert row["location_entity"] == "device_tracker.telefon"
        assert row["monthly_fuel_budget"] == 800.0
    c.close()


def test_migration_v9_removes_backfilled_keys_from_settings(tmp_path):
    c = dbm.get_conn(str(tmp_path / "m9b.db"))
    _migrate_to(c, 8)
    c.execute("INSERT INTO vehicles (name, tank_capacity_l, fuel_type) "
             "VALUES ('AutoA', 66, 'PB95')")
    c.execute("INSERT INTO settings (key, value) VALUES "
             "('odometer_entity', 'sensor.auto_a_mileage')")
    c.execute("INSERT INTO settings (key, value) VALUES "
             "('price_region', 'pomorskie')")
    c.commit()
    dbm.migrate(c)
    remaining = {r["key"] for r in c.execute("SELECT key FROM settings").fetchall()}
    assert "odometer_entity" not in remaining
    assert "fuel_level_entity" not in remaining
    assert "location_entity" not in remaining
    assert "monthly_fuel_budget" not in remaining
    assert "price_region" in remaining  # zostaje globalny
    c.close()
