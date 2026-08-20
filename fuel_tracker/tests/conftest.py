import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fuel_tracker import db as dbm  # noqa: E402
from fuel_tracker import stations as stn  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = dbm.get_conn(str(tmp_path / "test.db"))
    dbm.migrate(c)
    yield c
    c.close()


@pytest.fixture
def vehicle_id(conn):
    return dbm.ensure_vehicle(conn, "Testowy", 66.0, "PB95")


@pytest.fixture(autouse=True)
def _clear_enrich_cache():
    # stations._enrich_cache (0.16.1, Krok 5) żyje w module, na cały proces
    # testowy — bez czyszczenia jeden test podsuwałby drugiemu swój
    # zamockowany wynik Overpass dla tych samych współrzędnych (ST_A/ST_B
    # są reużywane w wielu testach).
    stn._enrich_cache.clear()
    yield
    stn._enrich_cache.clear()
