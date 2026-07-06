"""Scraper cen regionalnych: parser HTML, zapis, retencja, sensory."""
from datetime import datetime

import pytest

from fuel_tracker import db as dbm, prices, queries

HTML = """
<table><thead>
<tr><th></th><th>95</th><th>98</th><th>ON</th><th>ON+</th><th>LPG</th></tr>
</thead><tbody>
<tr><td>dolnośląskie</td><td class="text-center">6,34</td>
<td class="text-center">6,82</td><td class="text-center">6,71</td>
<td class="text-center">7,19</td><td class="text-center">3,08</td></tr>
<tr><td>lubuskie</td><td>6,56</td><td>-</td><td>-</td><td>-</td><td>3,19</td></tr>
</tbody></table>
"""


@pytest.fixture
def conn(tmp_path):
    c = dbm.get_conn(str(tmp_path / "prices.db"))
    dbm.migrate(c)
    yield c
    c.close()


def test_parse_prices_region_row():
    assert prices.parse_prices(HTML, "dolnośląskie") == {
        "PB95": 6.34, "PB98": 6.82, "ON": 6.71, "ON+": 7.19, "LPG": 3.08}


def test_parse_prices_skips_missing_quotes():
    assert prices.parse_prices(HTML, "lubuskie") == {"PB95": 6.56, "LPG": 3.19}


def test_parse_prices_unknown_region():
    assert prices.parse_prices(HTML, "mazowieckie") == {}
    assert prices.parse_prices("<html></html>", "dolnośląskie") == {}


def test_store_and_latest_price(conn):
    prices.store_prices(conn, "dolnośląskie", {"PB95": 6.34, "ON": 6.71},
                        now=datetime(2026, 7, 6))
    latest = prices.latest_price(conn, "dolnośląskie", "PB95")
    assert latest == {"price": 6.34, "fetched_at": "2026-07-06"}
    assert prices.latest_price(conn, "dolnośląskie", "LPG") is None
    # nadpisanie tego samego dnia — bez duplikatu
    prices.store_prices(conn, "dolnośląskie", {"PB95": 6.40},
                        now=datetime(2026, 7, 6))
    assert prices.latest_price(conn, "dolnośląskie", "PB95")["price"] == 6.40


def test_retention_purges_old_rows(conn):
    conn.execute(
        "INSERT INTO fuel_prices (fetched_at, station, fuel_type, price, source)"
        " VALUES ('2020-01-01', 'region:dolnośląskie', 'PB95', 5.0, 'autocentrum')")
    prices.store_prices(conn, "dolnośląskie", {"PB95": 6.34})
    rows = conn.execute("SELECT fetched_at FROM fuel_prices").fetchall()
    assert all(r["fetched_at"] != "2020-01-01" for r in rows)


def test_price_series(conn):
    prices.store_prices(conn, "dolnośląskie", {"PB95": 6.30},
                        now=datetime(2026, 7, 1))
    prices.store_prices(conn, "dolnośląskie", {"PB95": 6.34},
                        now=datetime(2026, 7, 6))
    assert prices.price_series(conn, "dolnośląskie", "PB95") == [
        {"date": "2026-07-01", "value": 6.30},
        {"date": "2026-07-06", "value": 6.34}]


def test_fetch_network_failure_returns_empty(monkeypatch):
    def boom(*a, **kw):
        raise OSError("brak sieci")
    monkeypatch.setattr(prices.requests, "get", boom)
    assert prices.fetch_region_prices("dolnośląskie") == {}


def test_sensor_values_price_vs_region(conn):
    vid = dbm.ensure_vehicle(conn, "Testowy", 66.0, "PB95")
    conn.execute(
        "INSERT INTO fillups (vehicle_id, date, odometer, volume_l,"
        " price_per_l, total_cost) VALUES (?, '2026-07-05 10:00', 1000,"
        " 40, 6.50, 260.0)", (vid,))
    conn.commit()
    prices.store_prices(conn, "dolnośląskie", {"PB95": 6.34},
                        now=datetime(2026, 7, 6))
    v = queries.sensor_values(conn, vid, 0.0, now=datetime(2026, 7, 6),
                              fuel_type="PB95", price_region="dolnośląskie")
    assert v["region_fuel_price"] == 6.34
    assert v["price_vs_region"] == 0.16  # 6.50 − 6.34 (drożej niż region)


def test_sensor_values_no_region_data(conn):
    vid = dbm.ensure_vehicle(conn, "Testowy", 66.0, "PB95")
    v = queries.sensor_values(conn, vid, 0.0, price_region="dolnośląskie")
    assert v["region_fuel_price"] is None
    assert v["price_vs_region"] is None
