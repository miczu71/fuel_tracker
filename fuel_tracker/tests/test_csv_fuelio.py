"""Parser i eksporter formatu Fuelio CSV."""
from pathlib import Path

from fuel_tracker import csv_fuelio, queries, stats as st

FIXTURE = (Path(__file__).parent / "fixtures" / "fuelio_sample.csv").read_text()


def test_parse_sections():
    sections = csv_fuelio.parse_sections(FIXTURE)
    assert set(sections) >= {"Vehicle", "Log", "CostCategories", "Costs"}
    assert len(sections["Log"]) == 3
    assert sections["Log"][0]["Data"] == "2025-03-01 12:00"
    assert sections["Log"][0]["City (optional)"] == "Stacja C"


def test_import_fillups_and_costs(conn, vehicle_id):
    report = csv_fuelio.import_into(conn, vehicle_id, FIXTURE)
    assert report.fillups_added == 3
    assert report.fillups_in_file == 3
    assert round(report.total_cost, 2) == 421.6
    assert round(report.total_volume, 2) == 70.0
    # isIncome=1 pomijany
    assert report.expenses_added == 1

    fillups = queries.fetch_fillups(conn, vehicle_id)
    assert len(fillups) == 3
    newest = fillups[0]
    assert newest["odometer"] == 2000
    assert newest["full_tank"] == 1
    assert newest["station"] == "Stacja C"
    assert newest["source"] == "fuelio_csv"
    assert newest["latitude"] == 50.0

    # partial @1500 (10 L) + pełny @2000 (20 L) → 30 L / 1000 km = 3.0
    s = st.compute_stats(fillups)
    assert s.avg_consumption == 3.0

    exp = queries.fetch_expenses(conn, vehicle_id)
    assert len(exp) == 1
    assert exp[0]["category"] == "Eksploatacja"
    assert exp[0]["cost"] == 89.98


def test_import_idempotent(conn, vehicle_id):
    csv_fuelio.import_into(conn, vehicle_id, FIXTURE)
    report2 = csv_fuelio.import_into(conn, vehicle_id, FIXTURE)
    assert report2.fillups_added == 0
    assert report2.fillups_skipped == 3
    assert report2.expenses_added == 0
    assert len(queries.fetch_fillups(conn, vehicle_id)) == 3


def test_price_fallback_from_total(conn, vehicle_id):
    text = FIXTURE.replace('"6.0","0","0.0","103"', '"","0","0.0","103"')
    csv_fuelio.import_into(conn, vehicle_id, text)
    row = conn.execute(
        "SELECT price_per_l FROM fillups WHERE odometer = 2000").fetchone()
    assert row["price_per_l"] == 6.0  # 120.0 / 20.0


def test_export_roundtrip(conn, vehicle_id, tmp_path):
    csv_fuelio.import_into(conn, vehicle_id, FIXTURE)
    exported = csv_fuelio.export_csv(conn, vehicle_id)
    assert '"## Log"' in exported
    # Reimport eksportu do świeżej bazy daje te same sumy.
    from fuel_tracker import db as dbm
    c2 = dbm.get_conn(str(tmp_path / "rt.db"))
    dbm.migrate(c2)
    v2 = dbm.ensure_vehicle(c2, "RT", 66.0, "PB95")
    report = csv_fuelio.import_into(c2, v2, exported)
    assert report.fillups_added == 3
    assert round(report.total_cost, 2) == 421.6
    s = st.compute_stats(queries.fetch_fillups(c2, v2))
    assert s.avg_consumption == 3.0
    c2.close()
