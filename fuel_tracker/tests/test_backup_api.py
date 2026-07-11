"""API kopii zapasowej (0.10.0) — Flask test client."""
from pathlib import Path

import pytest

from fuel_tracker import backup, db as dbm
from fuel_tracker.web import create_app


@pytest.fixture
def share_dir(tmp_path):
    return str(tmp_path / "share")


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "web.db")


@pytest.fixture
def client(db_path, share_dir):
    c = dbm.get_conn(db_path)
    dbm.migrate(c)
    dbm.ensure_vehicle(c, "Testowy", 66.0, "PB95")
    c.close()
    app = create_app(db_path=db_path, config={"share_dir": share_dir})
    app.testing = True
    return app.test_client()


def _seed_backup_file(share_dir, db_path, name="fuel_tracker-20260101.db"):
    backups = Path(share_dir) / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    src = dbm.get_conn(db_path)
    target = backups / name
    src.execute("VACUUM INTO ?", (str(target),))
    src.close()
    return target


def test_backup_list_endpoint(client, share_dir, db_path):
    _seed_backup_file(share_dir, db_path)
    r = client.get("/api/backup/list")
    assert r.status_code == 200
    rows = r.get_json()
    assert rows[0]["filename"] == "fuel_tracker-20260101.db"


def test_backup_restore_endpoint_happy_path(client, share_dir, db_path):
    # Kandydat z inną nazwą pojazdu niż to co jest w żywej bazie.
    cand = Path(share_dir) / "backups" / "fuel_tracker-20260201.db"
    cand.parent.mkdir(parents=True, exist_ok=True)
    cc = dbm.get_conn(str(Path(share_dir) / "candidate_src.db"))
    dbm.migrate(cc)
    dbm.ensure_vehicle(cc, "Przywrocone", 50.0, "PB95")
    cc.execute("VACUUM INTO ?", (str(cand),))
    cc.close()

    r = client.post("/api/backup/restore",
                    json={"filename": "fuel_tracker-20260201.db"})
    assert r.status_code == 200

    after = dbm.get_conn(db_path)
    assert dbm.get_vehicle(after, 1)["name"] == "Przywrocone"
    after.close()


def test_backup_restore_rejects_path_traversal(client):
    r = client.post("/api/backup/restore",
                    json={"filename": "../../etc/passwd"})
    assert r.status_code == 400


def test_backup_restore_rejects_unknown_file(client):
    r = client.post("/api/backup/restore",
                    json={"filename": "fuel_tracker-19990101.db"})
    assert r.status_code == 404


def test_backup_restore_upload_happy_path(client, share_dir, db_path):
    Path(share_dir).mkdir(parents=True, exist_ok=True)
    upload_src = Path(share_dir) / "upload_src.db"
    uc = dbm.get_conn(str(upload_src))
    dbm.migrate(uc)
    dbm.ensure_vehicle(uc, "Uploadowane", 45.0, "PB95")
    uc.close()

    r = client.post("/api/backup/restore/upload", data={
        "file": (open(upload_src, "rb"), "upload.db")},
        content_type="multipart/form-data")
    assert r.status_code == 200

    after = dbm.get_conn(db_path)
    assert dbm.get_vehicle(after, 1)["name"] == "Uploadowane"
    after.close()


def test_backup_restore_upload_rejects_garbage_file(client):
    import io
    r = client.post("/api/backup/restore/upload", data={
        "file": (io.BytesIO(b"not a database"), "garbage.db")},
        content_type="multipart/form-data")
    assert r.status_code == 400


def test_backup_export_json_shape(client):
    r = client.get("/api/backup/export.json")
    assert r.status_code == 200
    assert r.mimetype == "application/json"
    assert "attachment" in r.headers["Content-Disposition"]
    payload = r.get_json()
    assert payload["tables"]["vehicles"][0]["name"] == "Testowy"
    assert payload["schema_version"] == backup.current_schema_version()


def test_backup_import_json_happy_path(client):
    export = client.get("/api/backup/export.json").get_json()
    export["tables"]["vehicles"][0]["name"] = "Zaimportowane"
    import io
    import json as jsonlib
    r = client.post("/api/backup/import.json", data={
        "file": (io.BytesIO(jsonlib.dumps(export).encode()), "export.json")},
        content_type="multipart/form-data")
    assert r.status_code == 200
    assert client.get("/api/vehicles/1").get_json()["name"] == "Zaimportowane"


def test_backup_import_json_rejects_version_mismatch(client):
    export = client.get("/api/backup/export.json").get_json()
    export["schema_version"] = 1
    import io
    import json as jsonlib
    r = client.post("/api/backup/import.json", data={
        "file": (io.BytesIO(jsonlib.dumps(export).encode()), "export.json")},
        content_type="multipart/form-data")
    assert r.status_code == 400


def test_backup_restore_triggers_on_data_change(db_path, share_dir):
    c = dbm.get_conn(db_path)
    dbm.migrate(c)
    dbm.ensure_vehicle(c, "Testowy", 66.0, "PB95")
    c.close()
    cand = _seed_backup_file(share_dir, db_path)

    calls = []
    app = create_app(db_path=db_path, config={"share_dir": share_dir},
                     on_data_change=lambda: calls.append(1))
    app.testing = True
    client = app.test_client()

    r = client.post("/api/backup/restore", json={"filename": cand.name})
    assert r.status_code == 200
    assert calls == [1]
