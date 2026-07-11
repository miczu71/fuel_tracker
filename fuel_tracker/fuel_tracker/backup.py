"""Kopia zapasowa (0.10.0): nocny backup .db+attachments, restore, eksport/import JSON.

VACUUM INTO robi spójny snapshot bazy bez zatrzymywania add-onu. Restore
zawsze poprzedza safety_snapshot() bieżącej bazy — nigdy nie tracimy danych
przy nieudanym/pomyłkowym przywróceniu.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import tarfile
from datetime import datetime
from pathlib import Path

from . import __version__
from . import db as dbm

logger = logging.getLogger("fuel_tracker")

_NAME_RE = re.compile(r"fuel_tracker-(\d{4})(\d{2})(\d{2})\.db$")

# Kolejność bez znaczenia przy imporcie (foreign_keys=OFF na czas transakcji),
# ale export_json trzyma ją stałą dla czytelności pliku JSON.
_ALL_TABLES = ["vehicles", "fillups", "expense_categories", "expenses",
              "fuel_prices", "stations", "exchange_rates", "attachments",
              "settings", "alert_state"]


class BackupError(Exception):
    """Błąd walidacji/przywracania kopii zapasowej — komunikat user-facing."""


def current_schema_version() -> int:
    return len(dbm._MIGRATIONS)


def list_backups(share_dir: str) -> list[dict]:
    backups = Path(share_dir) / "backups"
    if not backups.is_dir():
        return []
    rows = []
    for f in backups.glob("fuel_tracker-*.db"):
        if not f.is_file():
            continue
        m = _NAME_RE.match(f.name)
        created_at = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
        rows.append({"filename": f.name, "size_bytes": f.stat().st_size,
                     "created_at": created_at})
    rows.sort(key=lambda r: r["filename"], reverse=True)
    return rows


def validate_candidate(path: str) -> None:
    """Odrzuca plik nie-SQLite lub pochodzący z nowszej wersji add-onu."""
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            version = c.execute("PRAGMA user_version").fetchone()[0]
            # PRAGMA user_version na pliku spoza SQLite zwykle nie rzuca —
            # dopiero realne zapytanie wymusza czytanie nagłówka strony.
            c.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        finally:
            c.close()
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"Plik nie jest poprawną bazą SQLite: {exc}") from exc
    if version > current_schema_version():
        raise BackupError(
            "Kopia pochodzi z nowszej wersji add-onu "
            f"(schemat {version} > {current_schema_version()})")


def safety_snapshot(db_path: str, share_dir: str, keep: int = 3) -> Path:
    """Auto-backup bieżącej bazy PRZED przywróceniem — własny podfolder
    i własna (mała) retencja, żeby nigdy nie wypierać nocnych kopii."""
    pre_dir = Path(share_dir) / "backups" / "pre_restore"
    pre_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    target = pre_dir / f"fuel_tracker-prerestore-{stamp}.db"
    conn = dbm.get_conn(db_path)
    try:
        conn.execute("VACUUM INTO ?", (str(target),))
    finally:
        conn.close()
    old = sorted(pre_dir.glob("fuel_tracker-prerestore-*.db"))[:-keep]
    for f in old:
        f.unlink(missing_ok=True)
    return target


def _replace_and_migrate(candidate_path: str, db_path: str) -> tuple[int, int]:
    before_conn = dbm.get_conn(db_path)
    before = before_conn.execute("PRAGMA user_version").fetchone()[0]
    before_conn.close()

    tmp_path = f"{db_path}.restoring"
    shutil.copy2(candidate_path, tmp_path)
    os.replace(tmp_path, db_path)

    conn = dbm.get_conn(db_path)
    try:
        dbm.migrate(conn)
        after = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    return before, after


def restore_from_path(candidate_path: str, db_path: str, share_dir: str) -> dict:
    snapshot = safety_snapshot(db_path, share_dir)
    validate_candidate(candidate_path)
    before, after = _replace_and_migrate(candidate_path, db_path)
    logger.info("Baza przywrócona z %s (schemat %s -> %s)",
                candidate_path, before, after)
    return {
        "restored_from": candidate_path,
        "safety_snapshot": str(snapshot),
        "schema_version_before": before,
        "schema_version_after": after,
    }


def restore_from_upload(file_storage, db_path: str, share_dir: str) -> dict:
    tmp_path = f"{db_path}.upload"
    try:
        file_storage.save(tmp_path)
        return restore_from_path(tmp_path, db_path, share_dir)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def export_json(conn: sqlite3.Connection) -> dict:
    """Pełna kopia JSON — wszystkie tabele, metadane obejmują wersję
    schematu (żeby import mógł odmówić przywrócenia z niezgodnej wersji)."""
    tables = {}
    for table in _ALL_TABLES:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        tables[table] = [dict(r) for r in rows]
    return {
        "schema_version": current_schema_version(),
        "exported_at": datetime.now().isoformat(),
        "app_version": __version__,
        "tables": tables,
    }


def import_json(conn: sqlite3.Connection, payload: dict) -> dict:
    """Pełne zastąpienie (nie merge): DELETE+INSERT każdej tabeli w jednej
    transakcji. Wymaga dokładnej zgodności schema_version — międzywersyjne
    przywracanie idzie przez plik .db (auto-migrujący), nie przez JSON."""
    version = payload.get("schema_version")
    if version != current_schema_version():
        raise BackupError(
            "Niezgodna wersja schematu kopii JSON "
            f"({version} != {current_schema_version()}) — użyj przywrócenia "
            "z pliku .db (migruje automatycznie)")
    tables = payload.get("tables") or {}
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for table in _ALL_TABLES:
            conn.execute(f"DELETE FROM {table}")
        for table in _ALL_TABLES:
            for row in tables.get(table, []):
                cols = list(row.keys())
                placeholders = ",".join("?" for _ in cols)
                conn.execute(
                    f"INSERT INTO {table} ({','.join(cols)}) "
                    f"VALUES ({placeholders})",
                    [row[c] for c in cols])
        conn.commit()
    except Exception as exc:
        conn.rollback()
        raise BackupError(f"Import JSON nieudany: {exc}") from exc
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    return {"tables_imported": {t: len(tables.get(t, [])) for t in _ALL_TABLES}}


def nightly_backup(db_path: str, share_dir: str, keep: int = 7,
                   keep_attachments: int = 7) -> None:
    backups = Path(share_dir) / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")

    target = backups / f"fuel_tracker-{stamp}.db"
    conn = dbm.get_conn(db_path)
    try:
        conn.execute("VACUUM INTO ?", (str(target),))
        logger.info("Backup bazy: %s", target)
    except Exception:
        logger.exception("Backup bazy nieudany")
    finally:
        conn.close()
    old = sorted(backups.glob("fuel_tracker-*.db"))[:-keep]
    for f in old:
        f.unlink(missing_ok=True)

    attach_dir = Path(share_dir) / "attachments"
    if attach_dir.is_dir():
        tar_target = backups / f"fuel_tracker-attachments-{stamp}.tar.gz"
        try:
            with tarfile.open(tar_target, "w:gz") as tf:
                tf.add(attach_dir, arcname="attachments")
            logger.info("Backup załączników: %s", tar_target)
        except Exception:
            logger.exception("Backup załączników nieudany")
        old_tar = sorted(
            backups.glob("fuel_tracker-attachments-*.tar.gz"))[:-keep_attachments]
        for f in old_tar:
            f.unlink(missing_ok=True)
