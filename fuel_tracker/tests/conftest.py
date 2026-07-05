import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fuel_tracker import db as dbm  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = dbm.get_conn(str(tmp_path / "test.db"))
    dbm.migrate(c)
    yield c
    c.close()


@pytest.fixture
def vehicle_id(conn):
    return dbm.ensure_vehicle(conn, "Testowy", 66.0, "PB95")
