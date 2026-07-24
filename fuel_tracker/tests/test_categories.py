"""Kategorie wydatków — CRUD (0.13.0). Rozszerza dotychczasowe hide/show
o tworzenie/zmianę nazwy/usuwanie oraz grupowanie pod TCO (tco_group)."""
import pytest

from fuel_tracker import db as dbm


def test_default_categories_backfilled_with_tco_group(conn):
    rows = {r["name"]: r["tco_group"] for r in
            conn.execute("SELECT name, tco_group FROM expense_categories")}
    assert rows["Płyny"] == "fluids"
    assert rows["Serwis"] == "service"
    assert rows["Ubezpieczenie"] == "insurance"
    assert rows["Parking"] == "fees"
    # "Inne" i kategorie bez jawnego mapowania spadają na domyślne "other".
    assert rows["Inne"] == "other"


def test_create_category(conn):
    cid = dbm.create_category(conn, "Opony", tco_group="service")
    row = conn.execute(
        "SELECT name, tco_group, hidden FROM expense_categories WHERE id = ?",
        (cid,)).fetchone()
    assert row["name"] == "Opony"
    assert row["tco_group"] == "service"
    assert row["hidden"] == 0


def test_create_category_defaults_tco_group_to_other(conn):
    cid = dbm.create_category(conn, "Coś nowego")
    row = conn.execute(
        "SELECT tco_group FROM expense_categories WHERE id = ?", (cid,)).fetchone()
    assert row["tco_group"] == "other"


def test_create_category_rejects_duplicate_name(conn):
    dbm.create_category(conn, "Opony")
    with pytest.raises(dbm.CategoryError):
        dbm.create_category(conn, "Opony")


def test_rename_and_regroup_category(conn):
    cid = dbm.create_category(conn, "Opony")
    assert dbm.update_category(conn, cid, name="Opony sezonowe",
                               tco_group="service")
    row = conn.execute(
        "SELECT name, tco_group FROM expense_categories WHERE id = ?",
        (cid,)).fetchone()
    assert row["name"] == "Opony sezonowe"
    assert row["tco_group"] == "service"


def test_delete_category_without_expenses(conn):
    cid = dbm.create_category(conn, "Do usunięcia")
    ok, reason = dbm.delete_category(conn, cid)
    assert ok and reason is None
    assert conn.execute(
        "SELECT 1 FROM expense_categories WHERE id = ?", (cid,)).fetchone() is None


def test_delete_category_with_expenses_refused(conn, vehicle_id):
    cid = dbm.create_category(conn, "Ma wydatki")
    conn.execute(
        "INSERT INTO expenses (vehicle_id, date, category_id, cost) "
        "VALUES (?, '2026-01-01', ?, 10)", (vehicle_id, cid))
    conn.commit()
    ok, reason = dbm.delete_category(conn, cid)
    assert not ok
    assert "wydatk" in reason


def test_delete_category_refuses_last_category(conn):
    conn.execute("DELETE FROM expense_categories")
    conn.commit()
    cid = dbm.create_category(conn, "Jedyna")
    ok, reason = dbm.delete_category(conn, cid)
    assert not ok
    assert "jedyn" in reason.lower()
