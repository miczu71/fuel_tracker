"""Kopia zapasowa (0.10.0): nocny backup .db+attachments, restore, JSON export/import."""
import sqlite3
import tarfile
from pathlib import Path

import pytest

from fuel_tracker import backup, db as dbm


def _make_db(tmp_path, name="test.db"):
    db_path = str(tmp_path / name)
    c = dbm.get_conn(db_path)
    dbm.migrate(c)
    return db_path, c


def test_nightly_backup_creates_db_snapshot_and_attachments_archive(tmp_path):
    db_path, c = _make_db(tmp_path)
    c.close()
    share_dir = str(tmp_path / "share")
    attach_dir = Path(share_dir) / "attachments"
    attach_dir.mkdir(parents=True)
    (attach_dir / "receipt1.jpg").write_bytes(b"fake-image-data")

    backup.nightly_backup(db_path, share_dir)

    backups = Path(share_dir) / "backups"
    db_backups = list(backups.glob("fuel_tracker-*.db"))
    assert len(db_backups) == 1
    tar_backups = list(backups.glob("fuel_tracker-attachments-*.tar.gz"))
    assert len(tar_backups) == 1
    with tarfile.open(tar_backups[0]) as tf:
        names = tf.getnames()
    assert any("receipt1.jpg" in n for n in names)


def test_current_schema_version_matches_migrations_length():
    assert backup.current_schema_version() == len(dbm._MIGRATIONS)


def test_list_backups_returns_sorted_with_size_excludes_pre_restore_subfolder(tmp_path):
    share_dir = str(tmp_path / "share")
    backups = Path(share_dir) / "backups"
    backups.mkdir(parents=True)
    (backups / "fuel_tracker-20260101.db").write_text("a" * 10)
    (backups / "fuel_tracker-20260215.db").write_text("b" * 20)
    pre = backups / "pre_restore"
    pre.mkdir()
    (pre / "fuel_tracker-prerestore-20260301T000000.db").write_text("c" * 30)

    rows = backup.list_backups(share_dir)

    assert [r["filename"] for r in rows] == [
        "fuel_tracker-20260215.db", "fuel_tracker-20260101.db"]
    assert rows[0]["size_bytes"] == 20
    assert rows[0]["created_at"] == "2026-02-15"


def test_nightly_backup_retention_prunes_old_files_for_both_artifact_types(tmp_path):
    db_path, c = _make_db(tmp_path)
    c.close()
    share_dir = str(tmp_path / "share")
    attach_dir = Path(share_dir) / "attachments"
    attach_dir.mkdir(parents=True)
    (attach_dir / "r.jpg").write_bytes(b"x")
    backups = Path(share_dir) / "backups"
    backups.mkdir(parents=True)
    for day in range(1, 11):
        (backups / f"fuel_tracker-202601{day:02d}.db").write_text("old")
        (backups / f"fuel_tracker-attachments-202601{day:02d}.tar.gz").write_text("old")

    backup.nightly_backup(db_path, share_dir, keep=3, keep_attachments=2)

    assert len(list(backups.glob("fuel_tracker-*.db"))) == 3
    assert len(list(backups.glob("fuel_tracker-attachments-*.tar.gz"))) == 2


def test_nightly_backup_without_attachments_dir_skips_archive_gracefully(tmp_path):
    db_path, c = _make_db(tmp_path)
    c.close()
    share_dir = str(tmp_path / "share")

    backup.nightly_backup(db_path, share_dir)

    backups = Path(share_dir) / "backups"
    assert len(list(backups.glob("fuel_tracker-*.db"))) == 1
    assert list(backups.glob("fuel_tracker-attachments-*.tar.gz")) == []


class FakeFileStorage:
    """Minimalny odpowiednik werkzeug.FileStorage (interfejs .save(path))."""

    def __init__(self, source_path):
        self._source_path = source_path

    def save(self, dest_path):
        Path(dest_path).write_bytes(Path(self._source_path).read_bytes())


def test_validate_candidate_rejects_non_sqlite_file(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_text("not a database at all")
    with pytest.raises(backup.BackupError):
        backup.validate_candidate(str(bad))


def test_validate_candidate_rejects_future_schema_version(tmp_path):
    future = tmp_path / "future.db"
    c = sqlite3.connect(str(future))
    c.execute("PRAGMA user_version = 999")
    c.execute("CREATE TABLE x (id INTEGER)")
    c.commit()
    c.close()
    with pytest.raises(backup.BackupError):
        backup.validate_candidate(str(future))


def test_validate_candidate_accepts_valid_older_schema(tmp_path):
    ok = tmp_path / "ok.db"
    c = sqlite3.connect(str(ok))
    c.execute("PRAGMA user_version = 1")
    c.execute("CREATE TABLE x (id INTEGER)")
    c.commit()
    c.close()
    backup.validate_candidate(str(ok))  # nie rzuca


def test_safety_snapshot_creates_file_in_own_subfolder_with_own_retention(tmp_path):
    db_path, c = _make_db(tmp_path)
    c.close()
    share_dir = str(tmp_path / "share")
    pre_dir = Path(share_dir) / "backups" / "pre_restore"

    last = None
    for _ in range(4):
        last = backup.safety_snapshot(db_path, share_dir, keep=3)

    assert last.parent == pre_dir
    assert len(list(pre_dir.glob("fuel_tracker-prerestore-*.db"))) == 3
    # Nie wypiera prawdziwych nocnych kopii we wspólnym backups/
    assert list((Path(share_dir) / "backups").glob("fuel_tracker-2*.db")) == []


def test_restore_from_path_creates_pre_restore_safety_snapshot_in_own_folder(tmp_path):
    db_path, c = _make_db(tmp_path)
    dbm.ensure_vehicle(c, "Live", 66.0, "PB95")
    c.close()
    cand_path, cc = _make_db(tmp_path, "candidate.db")
    dbm.ensure_vehicle(cc, "Candidate", 50.0, "PB95")
    cc.close()
    share_dir = str(tmp_path / "share")

    result = backup.restore_from_path(cand_path, db_path, share_dir)

    pre_dir = Path(share_dir) / "backups" / "pre_restore"
    assert len(list(pre_dir.glob("fuel_tracker-prerestore-*.db"))) == 1
    assert Path(result["safety_snapshot"]).parent == pre_dir


def test_restore_from_path_replaces_live_data(tmp_path):
    db_path, c = _make_db(tmp_path)
    dbm.ensure_vehicle(c, "Live", 66.0, "PB95")
    c.close()
    cand_path, cc = _make_db(tmp_path, "candidate.db")
    dbm.ensure_vehicle(cc, "Candidate", 50.0, "PB95")
    cc.close()
    share_dir = str(tmp_path / "share")

    backup.restore_from_path(cand_path, db_path, share_dir)

    after = dbm.get_conn(db_path)
    v = dbm.get_vehicle(after, 1)
    after.close()
    assert v["name"] == "Candidate"


def test_restore_from_path_auto_migrates_older_schema(tmp_path):
    db_path, c = _make_db(tmp_path)
    dbm.ensure_vehicle(c, "Live", 66.0, "PB95")
    c.close()
    share_dir = str(tmp_path / "share")

    # Kandydat na schemacie v1 (przed alert_state z migracji #7) —
    # tylko pierwszy skrypt migracji, bez reszty.
    cand_path = str(tmp_path / "old_schema.db")
    oc = sqlite3.connect(cand_path)
    oc.executescript(dbm._MIGRATIONS[0])
    oc.execute("PRAGMA user_version = 1")
    oc.execute(
        "INSERT INTO vehicles (name, tank_capacity_l, fuel_type) "
        "VALUES ('OldSchema', 55.0, 'PB95')")
    oc.commit()
    oc.close()

    backup.restore_from_path(cand_path, db_path, share_dir)

    after = dbm.get_conn(db_path)
    assert after.execute("PRAGMA user_version").fetchone()[0] == \
        backup.current_schema_version()
    assert after.execute("SELECT COUNT(*) FROM alert_state").fetchone()[0] == 0
    after.close()


_ALL_TABLES = ["vehicles", "fillups", "expense_categories", "expenses",
              "fuel_prices", "stations", "exchange_rates", "attachments",
              "settings", "alert_state"]


def test_export_json_includes_all_ten_tables_and_schema_version(tmp_path):
    db_path, c = _make_db(tmp_path)
    dbm.ensure_vehicle(c, "Live", 66.0, "PB95")

    payload = backup.export_json(c)
    c.close()

    assert payload["schema_version"] == backup.current_schema_version()
    assert set(payload["tables"].keys()) == set(_ALL_TABLES)
    assert payload["tables"]["vehicles"][0]["name"] == "Live"
    assert "exported_at" in payload
    assert "app_version" in payload


def test_import_json_round_trip_preserves_data(tmp_path):
    db_path, c = _make_db(tmp_path)
    dbm.ensure_vehicle(c, "Live", 66.0, "PB95")
    payload = backup.export_json(c)
    c.close()

    db_path2, c2 = _make_db(tmp_path, "second.db")
    dbm.ensure_vehicle(c2, "InneAuto", 40.0, "PB95")

    backup.import_json(c2, payload)

    assert dbm.get_vehicle(c2, 1)["name"] == "Live"
    c2.close()


def test_import_json_rejects_schema_version_mismatch(tmp_path):
    db_path, c = _make_db(tmp_path)
    payload = backup.export_json(c)
    payload["schema_version"] = 1
    with pytest.raises(backup.BackupError):
        backup.import_json(c, payload)
    c.close()


def test_import_json_is_atomic_on_error(tmp_path):
    db_path, c = _make_db(tmp_path)
    dbm.ensure_vehicle(c, "Original", 66.0, "PB95")
    payload = backup.export_json(c)
    # Popsuty payload — brak wymaganych kolumn NOT NULL w fillups.
    payload["tables"]["fillups"] = [{"id": 999, "vehicle_id": 1}]

    with pytest.raises(backup.BackupError):
        backup.import_json(c, payload)

    assert dbm.get_vehicle(c, 1)["name"] == "Original"
    c.close()


def test_import_json_is_full_replace_not_merge(tmp_path):
    db_path, c = _make_db(tmp_path)
    dbm.ensure_vehicle(c, "First", 66.0, "PB95")
    payload = backup.export_json(c)
    dbm.create_vehicle(c, "Second", 50.0, "PB95")
    assert len(dbm.list_vehicles(c, include_archived=True)) == 2

    backup.import_json(c, payload)

    rows = dbm.list_vehicles(c, include_archived=True)
    assert len(rows) == 1
    assert rows[0]["name"] == "First"
    c.close()


def test_restore_from_upload_delegates_to_restore_from_path(tmp_path):
    db_path, c = _make_db(tmp_path)
    dbm.ensure_vehicle(c, "Live", 66.0, "PB95")
    c.close()
    cand_path, cc = _make_db(tmp_path, "candidate.db")
    dbm.ensure_vehicle(cc, "Uploaded", 50.0, "PB95")
    cc.close()
    share_dir = str(tmp_path / "share")

    backup.restore_from_upload(FakeFileStorage(cand_path), db_path, share_dir)

    after = dbm.get_conn(db_path)
    assert dbm.get_vehicle(after, 1)["name"] == "Uploaded"
    after.close()
